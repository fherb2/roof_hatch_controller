"""
task_control.py - Async task: hatch control loop.

Continuously compares the target angle (set by the web interface or the
button task) with the last known actual angle for each hatch, both held
in degrees. When a mismatch is found, the full movement sequence is
executed for that hatch before checking the next one. All four hatches
are processed sequentially in index order within a single loop pass.

Degrees are converted to microseconds only where the hardware is
actually driven (deg_to_us(), see shared/servo_units.py); state, targets
and config all speak degrees.

Movement sequence executed by _move_hatch:
  step_mark_failsafe:     Set hatch_deg to FAILSAFE_DEG and persist so
                          that a power loss mid-move leaves a detectable
                          sentinel in state.json rather than a stale
                          position.
  step_preset_servos:     Output start positions to PCA9685 before
                          relay-on. Hatch servo: last known actual
                          position, or positions[0] if actual was
                          FAILSAFE_DEG (hatch assumed near closed by
                          gravity). Brake servo: always this hatch's own
                          brake_open_deg so it immediately begins
                          travelling when the relay closes.
  step_relay_on:          Relay on -> servos powered; brake immediately
                          starts moving towards the open position.
  step_wiggle:            Alternate hatch servo +/- _WIGGLE_US around the
                          preset position for _WIGGLE_CYCLES full +/-
                          cycles at ~50 Hz (2 x 20 ms per cycle).
                          Breaks power-on centre-seeking on some digital
                          servos. Disabled when _WIGGLE_CYCLES = 0.
  (wait _RELAY_ON_TO_BRAKE_OPEN_MS):    time for the brake servo to
                          complete its physical travel to the open
                          position, counted from relay-on.
  (wait _BRAKE_OPEN_TO_HATCH_MOVE_MS):  additional safety margin after
                          the brake is open before the hatch servo
                          starts moving.
  step_move_hatch:        Hatch servo -> target angle, stepped by the
                          fixed HATCH_STEP_US every step_ms (step_ms is
                          derived from hatch_servo.speed_deg_per_s, see
                          _compute_step_ms()), clamped to
                          [hard_stop_min_deg, hard_stop_max_deg].
  (wait _HATCH_SETTLE_TO_BRAKE_CLOSE_MS): time for the hatch to settle
                          at its final position before the brake closes.
  step_close_brake:       Brake servo -> this hatch's own
                          brake_close_deg.
  (wait _BRAKE_CLOSE_TO_RELAY_OFF_MS):   time for the brake servo to
                          complete its travel to closed before cutting
                          power.
  step_relay_off:         Relay off -> servos unpowered. Always runs in
                          the finally block regardless of outcome.
  step_update_state:      Update hatch_deg, brake_deg and target_deg in
                          state and persist to flash.

The relay is switched off unconditionally in a finally block so that an
unexpected exception can never leave the servos powered indefinitely.
The hardware WDT (task_watchdog) acts as the last safety net for cases
where the event loop itself stops responding.
"""

import uasyncio
from app.hw_config import PCA9685_CH, RELAY_CH, FAILSAFE_DEG, HATCH_STEP_US
from app.state import save_state
from shared.servo_units import deg_to_us

# Wiggle parameters: after relay-on, the hatch servo alternates between
# preset_us +/- _WIGGLE_US for _WIGGLE_CYCLES full oscillations at ~50 Hz
# (each cycle is one +/- pair = 2 x 20 ms). Intended to interrupt the
# power-on centre-seeking routine of some digital servos.
# Set _WIGGLE_CYCLES = 0 to disable entirely.
_WIGGLE_US       = 10   # amplitude in µs
_WIGGLE_CYCLES   = 5    # full +/- cycles; 0 = disabled
_PRESET_DELAY_MS = 250  # wait after relay-on before first PWM signal;
                         # gives servo controller time to boot without
                         # seeing a valid signal -> may prevent power-on
                         # centre-seeking jerk; 0 = disabled

# Fixed sequence timing, identical for all four hatches, not exposed via
# config.json or the web interface.
_RELAY_ON_TO_BRAKE_OPEN_MS      = 200  # relay-on -> brake fully open
_BRAKE_OPEN_TO_HATCH_MOVE_MS    = 400  # extra margin before hatch moves
_HATCH_SETTLE_TO_BRAKE_CLOSE_MS = 200  # target reached -> brake closes
_BRAKE_CLOSE_TO_RELAY_OFF_MS    = 500  # brake-close command -> relay off


def _compute_step_ms(hatch_servo_cfg):
    """Derive the ramp step interval (ms) from the configured speed.

    The hatch servo always steps by the fixed HATCH_STEP_US per step;
    the interval between steps is computed from the desired angular
    speed (hatch_servo.speed_deg_per_s) and this servo's µs-per-degree
    ratio, so that changing the speed in config.json takes effect
    immediately without needing a step size change.

    Args:
        hatch_servo_cfg: config["global"]["hatch_servo"] dict.
    Returns:
        int step interval in milliseconds, at least 1.
    """
    us_per_deg = ((hatch_servo_cfg["pulse_max_us"] - hatch_servo_cfg["pulse_min_us"])
                  / hatch_servo_cfg["range_deg"])
    deg_per_step = HATCH_STEP_US / us_per_deg
    return max(1, round(1000 * deg_per_step / hatch_servo_cfg["speed_deg_per_s"]))


async def _move_hatch(idx, hw, config, state, targets):
    """Execute the full movement sequence for one hatch.

    Drives the PCA9685 and relay through the named steps described in
    the module docstring. Timing constants above and the position/speed
    values from config are used; the last known servo positions come
    from state. All position/target values are in degrees; conversion
    to microseconds happens locally via deg_to_us().

    The target angle is clamped to [hard_stop_min_deg, hard_stop_max_deg]
    before the move starts and written back into targets.

    In step_mark_failsafe, hatch_deg is set to FAILSAFE_DEG and persisted
    so that a power loss mid-move leaves a recoverable sentinel in
    state.json instead of a stale position.

    save_state() is called in step_mark_failsafe (FAILSAFE_DEG) and
    again in step_update_state on success. On any exception the relay is
    switched off in step_relay_off (finally block) and the exception
    propagates to task_control, which logs it and continues.

    Args:
        idx:     hatch index (0-3).
        hw:      HardwareBundle.
        config:  config dict from load_config().
        state:   state dict from load_state() - mutated and persisted on
                 success.
        targets: RAM-only target dict - target value may be clamped in
                 place before the move.
    """
    hs = config["global"]["hatch_servo"]
    bs = config["global"]["brake_servo"]
    h  = config["hatches"][idx]
    s  = state["hatches"][idx]
    hatch_ch, brake_ch = PCA9685_CH[idx]
    relay_ch            = RELAY_CH[idx]

    # Clamp target to hardstop range (targets may be set from web/button
    # before config is fully validated for this hatch)
    target_deg = max(hs["hard_stop_min_deg"],
                      min(hs["hard_stop_max_deg"], targets[idx]["hatch_deg"]))
    targets[idx]["hatch_deg"] = target_deg  # write back clamped value

    # Capture actual position before overwriting it with FAILSAFE_DEG
    actual_deg = s["hatch_deg"]

    # -- Step 0: mark actual as unknown and persist -----------------------
    # Any power loss after this point leaves FAILSAFE_DEG in state.json,
    # signalling on next boot that the physical position is unknown.
    s["hatch_deg"] = FAILSAFE_DEG
    save_state(state)

    # -- Convert this move's degree values to microseconds ----------------
    preset_deg = (h["positions"][0]["hatch_deg"]
                  if actual_deg == FAILSAFE_DEG else actual_deg)
    preset_us      = deg_to_us(preset_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                hs["range_deg"])
    target_us      = deg_to_us(target_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                hs["range_deg"])
    brake_open_us  = deg_to_us(h["brake_open_deg"], bs["pulse_min_us"],
                                bs["pulse_max_us"], bs["range_deg"])
    brake_close_us = deg_to_us(h["brake_close_deg"], bs["pulse_min_us"],
                                bs["pulse_max_us"], bs["range_deg"])
    step_ms        = _compute_step_ms(hs)

    # -- step_preset_servos: brake to brake_open_us; hatch to 0 (no ------
    #    signal) so the servo controller does not see a valid pulse
    #    during its power-on boot sequence
    hw.pwm.set_us(hatch_ch, 0)
    hw.pwm.set_us(brake_ch, brake_open_us)

    # -- step_relay_on: servos powered; brake immediately starts ----------
    #    moving towards the open position
    hw.relay.on(relay_ch)

    try:
        # -- step_preset_delay: wait for servo controller to boot ---------
        #    before presenting a valid PWM signal
        if _PRESET_DELAY_MS > 0:
            await uasyncio.sleep_ms(_PRESET_DELAY_MS)

        # -- step_wiggle: set first pulse and immediately alternate -------
        #    +/- _WIGGLE_US to break any residual centre-seeking
        _period_ms = 20  # 50 Hz base period
        hw.pwm.set_us(hatch_ch, preset_us)
        for _ in range(_WIGGLE_CYCLES):
            hw.pwm.set_us(hatch_ch, preset_us + _WIGGLE_US)
            await uasyncio.sleep_ms(_period_ms)
            hw.pwm.set_us(hatch_ch, preset_us - _WIGGLE_US)
            await uasyncio.sleep_ms(_period_ms)
        if _WIGGLE_CYCLES > 0:
            hw.pwm.set_us(hatch_ch, preset_us)

        # -- wait for brake to complete its physical travel to open, ------
        #    then an extra safety margin before the hatch starts moving
        await uasyncio.sleep_ms(_RELAY_ON_TO_BRAKE_OPEN_MS)
        await uasyncio.sleep_ms(_BRAKE_OPEN_TO_HATCH_MOVE_MS)

        # -- step_move_hatch: ramp hatch servo to target position ---------
        #    fixed HATCH_STEP_US per step; step_ms derived from the
        #    configured speed (see _compute_step_ms)
        current_us = preset_us
        while current_us != target_us:
            if current_us < target_us:
                current_us = min(current_us + HATCH_STEP_US, target_us)
            else:
                current_us = max(current_us - HATCH_STEP_US, target_us)
            hw.pwm.set_us(hatch_ch, current_us)
            await uasyncio.sleep_ms(step_ms)

        # -- wait for the hatch to settle at its final position before ----
        #    commanding the brake to close
        await uasyncio.sleep_ms(_HATCH_SETTLE_TO_BRAKE_CLOSE_MS)

        # -- step_close_brake: command brake closed, wait for travel ------
        hw.pwm.set_us(brake_ch, brake_close_us)
        await uasyncio.sleep_ms(_BRAKE_CLOSE_TO_RELAY_OFF_MS)

        # -- step_update_state: confirm reached position and persist ------
        s["hatch_deg"]  = target_deg
        s["brake_deg"]  = h["brake_close_deg"]
        s["target_deg"] = target_deg
        save_state(state)

    finally:
        # -- step_relay_off: always cut servo power -----------------------
        hw.relay.off(relay_ch)


async def task_control(hw, config, state, targets):
    """Async control loop: move hatches to their target positions.

    Runs forever as part of uasyncio.gather(). On every pass it checks
    all hatches in order and starts a full movement sequence for each
    one whose target differs from its last known actual position.

    Exceptions from individual moves are caught here, logged to the
    serial console and skipped, so that a transient error on one hatch
    does not prevent the others from being serviced. By the time the
    exception reaches this level the relay is already off (see the
    finally block in _move_hatch).

    Truly unexpected errors that propagate past this handler will bring
    down the event loop, allowing the hardware WDT to trigger a reset
    and run the fail-safe sequence - this is intentional design, not an
    oversight.

    Args:
        hw:      HardwareBundle.
        config:  config dict from load_config().
        state:   state dict (mutated and persisted after each successful
                 move).
        targets: RAM-only setpoint dict (mutated by web and button
                 tasks).
    """
    num_hatches = len(targets)

    while True:
        moved = False
        for i in range(num_hatches):
            if targets[i]["hatch_deg"] != state["hatches"][i]["hatch_deg"]:
                try:
                    await _move_hatch(i, hw, config, state, targets)
                except Exception as e:
                    print(f"task_control: move hatch {i} failed: {e}")
                    # relay is already off via _move_hatch finally block;
                    # leave state unchanged and continue with next hatch
                moved = True

        # Shorter sleep after a move pass to stay responsive to rapid
        # target changes; longer sleep when idle to reduce CPU load
        await uasyncio.sleep_ms(10 if moved else 50)
