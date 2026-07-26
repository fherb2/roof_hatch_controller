"""
task_control.py - Async task: hatch control loop.

Continuously compares the target angle (set by the web interface or the
button task) with the last known actual angle for each hatch, both held
in degrees. When a mismatch is found, the full movement sequence is
executed for that hatch before checking the next one. All four hatches
are processed sequentially in index order within a single loop pass, then
any pending brake-only (mounting angle) moves, then any pending servo
tester moves - all within the same loop pass, all sequentially. Only one
PCA9685 channel pair is ever driven at a time.

Degrees are converted to microseconds only where the hardware is
actually driven (deg_to_us(), see shared/servo_units.py); state, targets
and config all speak degrees.

PWM frequency: the PCA9685 has a single, chip-wide PRE_SCALE register -
no per-channel frequency is possible in hardware. config.json's per-hatch
pwm_freq_hz (shared by that hatch's own hatch_servo and brake_servo, since
both are powered simultaneously) and global.servo_tester.pwm_freq_hz
(shared by both tester outputs) are therefore applied by reprogramming
the chip immediately before each move, via _ensure_pwm_freq() - which
skips the reprogram if the requested frequency is already active. No
attempt is made to protect the servo tester's frequency from being
overwritten by a concurrent hatch move: the tester is meant only for
servos removed from the hatches, so incorrect behaviour while a hatch
happens to move at the same time is accepted as harmless.

Movement sequence executed by _move_hatch:
  step_mark_failsafe:     Set hatch_deg to FAILSAFE_DEG and persist so
                          that a power loss mid-move leaves a detectable
                          sentinel in state.json rather than a stale
                          position.
  step_preset_servos:     Output start positions to PCA9685 before
                          relay-on. Hatch servo: last known actual
                          position, or positions[0] if actual was
                          FAILSAFE_DEG (hatch assumed near closed by
                          gravity). Brake servo: its own last known
                          actual position (NOT open_deg) so it does not
                          jerk when the relay closes - it only starts
                          ramping towards open_deg once the hatch servo
                          has taken up the holding load (see below).
  step_set_pwm_freq:      Reprogram the PCA9685 to this hatch's own
                          pwm_freq_hz if not already active.
  step_relay_on:          Relay on -> both servos of this hatch powered.
  step_wiggle:            Alternate hatch servo +/- _WIGGLE_US around the
                          preset position for _WIGGLE_CYCLES full +/-
                          cycles at ~50 Hz (2 x 20 ms per cycle).
                          Breaks power-on centre-seeking on some digital
                          servos. Disabled when _WIGGLE_CYCLES = 0. Only
                          the hatch servo is wiggled - the brake servo
                          is holding a fixed position at this point.
  (wait _RELAY_ON_TO_TORQUE_MS): time for the hatch servo to take up the
                          holding load, counted from relay-on, before the
                          brake is allowed to release.
  step_ramp_brake_open:   Brake servo ramps from its actual position to
                          open_deg (its own speed_deg_per_s/step size).
                          No pause afterwards - the hatch move starts the
                          instant this ramp finishes.
  step_ramp_hatch:        Hatch servo ramps to the target angle (its own
                          speed_deg_per_s/step size), clamped to
                          [hard_stop_min_deg, hard_stop_max_deg].
  (wait _HATCH_SETTLE_TO_BRAKE_CLOSE_MS): time for the hatch to settle
                          at its final position before the brake starts
                          closing.
  step_ramp_brake_close:  Brake servo ramps from open_deg to close_deg.
  step_relay_off:         Relay off, immediately once the brake-close
                          ramp finishes. Always runs in the finally
                          block regardless of outcome, so an exception
                          anywhere above can never leave the servos
                          powered indefinitely.
  step_update_state:      Update hatch_deg, brake_deg and target_deg in
                          state and persist to flash.

Because the brake servo now ramps (rather than jumping in one step) we
know exactly when it has physically finished moving, which is what let
us replace the two previous fixed "just wait and hope" delays
(_BRAKE_OPEN_TO_HATCH_MOVE_MS, _BRAKE_CLOSE_TO_RELAY_OFF_MS) with the
ramp's own natural completion.

_move_brake_only() and _move_tester_servo() are simplified variants used
for the per-servo "mounting angle" maintenance moves and the servo
tester - see their own docstrings below.

The hardware WDT (task_watchdog) acts as the last safety net for cases
where the event loop itself stops responding.
"""

import uasyncio
from app.hw_config import PCA9685_CH, RELAY_CH, TESTER_CH, FAILSAFE_DEG, MIN_STEP_MS
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
_RELAY_ON_TO_TORQUE_MS          = 200  # relay-on -> hatch servo has torque
_HATCH_SETTLE_TO_BRAKE_CLOSE_MS = 200  # target reached -> brake closes

_current_pwm_freq = None  # module cache of the PCA9685's last-programmed
                           # frequency, to skip redundant reprogramming


def _ensure_pwm_freq(hw, freq_hz):
    """Reprogram the PCA9685's chip-wide frequency if it isn't already
    set to freq_hz. See the module docstring for why this is per-move
    rather than per-channel."""
    global _current_pwm_freq
    if freq_hz != _current_pwm_freq:
        hw.pwm.freq(freq_hz)
        _current_pwm_freq = freq_hz


def _compute_step(servo_cfg):
    """Derive (step_us, step_ms) for a movement ramp from a servo's
    configured speed.

    Used for the hatch servo, the brake servo and the two servo tester
    outputs alike - any dict with range_deg/pulse_min_us/pulse_max_us/
    speed_deg_per_s works. Rather than fixing the step size and deriving
    whatever (possibly far too small) interval that implies, this fixes
    a practical minimum step interval (MIN_STEP_MS) that reliably
    exceeds real-world uasyncio scheduling and I2C write overhead -
    alongside the other concurrent tasks (webserver, watchdog, button
    poll) - and derives the step size in microseconds needed to hit the
    configured angular speed at that interval.

    Args:
        servo_cfg: a hatch_servo or brake_servo config dict.
    Returns:
        (step_us, step_ms) tuple, both at least 1.
    """
    us_per_deg = ((servo_cfg["pulse_max_us"] - servo_cfg["pulse_min_us"])
                  / servo_cfg["range_deg"])
    step_ms = MIN_STEP_MS
    step_us = max(1, round(us_per_deg * servo_cfg["speed_deg_per_s"]
                            * step_ms / 1000))
    return step_us, step_ms


async def _ramp(hw, channel, current_us, target_us, step_us, step_ms):
    """Ramp a single PCA9685 channel from current_us to target_us.

    Shared by the hatch servo, the brake servo, and the servo tester -
    previously the brake servo jumped straight to its target in one
    step; it now ramps identically to the hatch servo.

    Returns:
        int the final pulse width written (== target_us).
    """
    while current_us != target_us:
        if current_us < target_us:
            current_us = min(current_us + step_us, target_us)
        else:
            current_us = max(current_us - step_us, target_us)
        hw.pwm.set_us(channel, current_us)
        await uasyncio.sleep_ms(step_ms)
    return current_us


async def _move_hatch(idx, hw, config, state, targets):
    """Execute the full movement sequence for one hatch.

    Drives the PCA9685 and relay through the named steps described in
    the module docstring. All position/target values are in degrees;
    conversion to microseconds happens locally via deg_to_us().

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
        targets: full targets dict (see task_control() docstring) -
                 targets["hatches"][idx]["hatch_deg"] may be clamped in
                 place before the move.
    """
    h  = config["hatches"][idx]
    hs = h["hatch_servo"]
    bs = h["brake_servo"]
    s  = state["hatches"][idx]
    hatch_ch, brake_ch = PCA9685_CH[idx]
    relay_ch            = RELAY_CH[idx]

    # Clamp target to this hatch's own hardstop range (targets may be
    # set from web/button before config is fully validated for this hatch)
    target_deg = max(hs["hard_stop_min_deg"],
                      min(hs["hard_stop_max_deg"], targets["hatches"][idx]["hatch_deg"]))
    targets["hatches"][idx]["hatch_deg"] = target_deg  # write back clamped value

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
    preset_us       = deg_to_us(preset_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                 hs["range_deg"], hs["direction"])
    target_us       = deg_to_us(target_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                 hs["range_deg"], hs["direction"])
    brake_preset_us = deg_to_us(s["brake_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_open_us   = deg_to_us(bs["open_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_close_us  = deg_to_us(bs["close_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    hatch_step_us, hatch_step_ms = _compute_step(hs)
    brake_step_us, brake_step_ms = _compute_step(bs)

    # -- step_preset_servos: brake to its own actual position (not -------
    #    open_deg - avoids a jerk on relay-on); hatch to 0 (no signal)
    #    so the servo controller does not see a valid pulse during its
    #    power-on boot sequence
    hw.pwm.set_us(hatch_ch, 0)
    hw.pwm.set_us(brake_ch, brake_preset_us)

    # -- step_set_pwm_freq -------------------------------------------------
    _ensure_pwm_freq(hw, h["pwm_freq_hz"])

    # -- step_relay_on: both servos of this hatch now powered -------------
    hw.relay.on(relay_ch)

    try:
        # -- step_preset_delay: wait for servo controller to boot ---------
        #    before presenting a valid PWM signal
        if _PRESET_DELAY_MS > 0:
            await uasyncio.sleep_ms(_PRESET_DELAY_MS)

        # -- step_wiggle: set first pulse and immediately alternate -------
        #    +/- _WIGGLE_US to break any residual centre-seeking
        #    (hatch servo only - brake servo is holding still)
        _period_ms = 20  # 50 Hz base period
        hw.pwm.set_us(hatch_ch, preset_us)
        for _ in range(_WIGGLE_CYCLES):
            hw.pwm.set_us(hatch_ch, preset_us + _WIGGLE_US)
            await uasyncio.sleep_ms(_period_ms)
            hw.pwm.set_us(hatch_ch, preset_us - _WIGGLE_US)
            await uasyncio.sleep_ms(_period_ms)
        if _WIGGLE_CYCLES > 0:
            hw.pwm.set_us(hatch_ch, preset_us)

        # -- wait for the hatch servo to take up the holding torque -------
        #    before the brake is allowed to release
        await uasyncio.sleep_ms(_RELAY_ON_TO_TORQUE_MS)

        # -- step_ramp_brake_open: brake ramps to open, no pause after ----
        await _ramp(hw, brake_ch, brake_preset_us, brake_open_us,
                    brake_step_us, brake_step_ms)

        # -- step_ramp_hatch: hatch ramps to target position --------------
        await _ramp(hw, hatch_ch, preset_us, target_us,
                    hatch_step_us, hatch_step_ms)

        # -- wait for the hatch to settle at its final position before ----
        #    commanding the brake to close
        await uasyncio.sleep_ms(_HATCH_SETTLE_TO_BRAKE_CLOSE_MS)

        # -- step_ramp_brake_close: brake ramps to close -------------------
        await _ramp(hw, brake_ch, brake_open_us, brake_close_us,
                    brake_step_us, brake_step_ms)

        # -- step_update_state: confirm reached position and persist ------
        s["hatch_deg"]  = target_deg
        s["brake_deg"]  = bs["close_deg"]
        s["target_deg"] = target_deg
        save_state(state)

    finally:
        # -- step_relay_off: always cut servo power, immediately ----------
        #    once the try block above finishes or raises
        hw.relay.off(relay_ch)


async def _move_brake_only(idx, hw, config, state, target_deg):
    """Move only the brake servo of one hatch directly, without the full
    _move_hatch choreography. Used for the brake servo's "mounting
    angle" maintenance move.

    The hatch servo of this same hatch is powered simultaneously (shared
    relay) but held at its own current actual position rather than being
    moved - see the module and _move_hatch docstrings for why both
    servos of a hatch are always powered together. Neither hatch_deg nor
    target_deg in state are touched; only brake_deg is updated.

    Args:
        idx:        hatch index (0-3).
        hw:         HardwareBundle.
        config:     config dict from load_config().
        state:      state dict - state["hatches"][idx]["brake_deg"] is
                    mutated and persisted on success.
        target_deg: requested brake angle in degrees, clamped here to
                    this hatch's own brake_servo hard stops.
    """
    h  = config["hatches"][idx]
    hs = h["hatch_servo"]
    bs = h["brake_servo"]
    s  = state["hatches"][idx]
    hatch_ch, brake_ch = PCA9685_CH[idx]
    relay_ch            = RELAY_CH[idx]

    target_deg = max(bs["hard_stop_min_deg"], min(bs["hard_stop_max_deg"], target_deg))

    hatch_hold_deg = (h["positions"][0]["hatch_deg"]
                      if s["hatch_deg"] == FAILSAFE_DEG else s["hatch_deg"])
    hatch_hold_us   = deg_to_us(hatch_hold_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                 hs["range_deg"], hs["direction"])
    brake_preset_us = deg_to_us(s["brake_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_target_us = deg_to_us(target_deg, bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_step_us, brake_step_ms = _compute_step(bs)

    hw.pwm.set_us(hatch_ch, 0)
    hw.pwm.set_us(brake_ch, brake_preset_us)
    _ensure_pwm_freq(hw, h["pwm_freq_hz"])
    hw.relay.on(relay_ch)

    try:
        if _PRESET_DELAY_MS > 0:
            await uasyncio.sleep_ms(_PRESET_DELAY_MS)

        # Hold the hatch servo steady at its current position - no
        # wiggle, since it is not the servo being moved here.
        hw.pwm.set_us(hatch_ch, hatch_hold_us)

        await _ramp(hw, brake_ch, brake_preset_us, brake_target_us,
                    brake_step_us, brake_step_ms)

        s["brake_deg"] = target_deg
        save_state(state)

    finally:
        hw.relay.off(relay_ch)


async def _move_tester_servo(hw, config, state, which, target_deg):
    """Move one of the two servo tester outputs directly. No relay - the
    tester circuit is permanently powered by design (only ever used with
    servos removed from the hatches, see hw_config.TESTER_CH).

    Args:
        hw:         HardwareBundle.
        config:     config dict from load_config().
        state:      state dict - state["servo_tester"][f"{which}_deg"] is
                    mutated and persisted on success.
        which:      "hatch" or "brake" - selects the tester output and
                    its config/state sub-key.
        target_deg: requested angle in degrees, clamped here to this
                    tester servo's own hard stops.
    """
    st       = config["global"]["servo_tester"]
    servo_cfg = st[f"{which}_servo"]
    channel   = TESTER_CH[0 if which == "hatch" else 1]

    target_deg = max(servo_cfg["hard_stop_min_deg"],
                      min(servo_cfg["hard_stop_max_deg"], target_deg))

    current_deg = state["servo_tester"][f"{which}_deg"]
    current_us  = deg_to_us(current_deg, servo_cfg["pulse_min_us"], servo_cfg["pulse_max_us"],
                             servo_cfg["range_deg"], servo_cfg["direction"])
    target_us   = deg_to_us(target_deg, servo_cfg["pulse_min_us"], servo_cfg["pulse_max_us"],
                             servo_cfg["range_deg"], servo_cfg["direction"])
    step_us, step_ms = _compute_step(servo_cfg)

    _ensure_pwm_freq(hw, st["pwm_freq_hz"])
    await _ramp(hw, channel, current_us, target_us, step_us, step_ms)

    state["servo_tester"][f"{which}_deg"] = target_deg
    save_state(state)


async def task_control(hw, config, state, targets):
    """Async control loop: move hatches, brake-only and tester servos to
    their target positions.

    Runs forever as part of uasyncio.gather(). On every pass it checks,
    in order: all 4 hatches (full sequence on mismatch), all 4 pending
    brake-only moves (mounting angle), then the 2 pending servo tester
    moves. Everything runs strictly sequentially - only one PCA9685
    channel pair is ever driven at a time, and all I2C writes to the
    PCA9685 happen exclusively in this task (see module docstring).

    Exceptions from individual moves are caught here, logged to the
    serial console and skipped, so that a transient error on one move
    does not prevent the others from being serviced. By the time the
    exception reaches this level the relay (if any) is already off (see
    the finally blocks in _move_hatch/_move_brake_only).

    Truly unexpected errors that propagate past this handler will bring
    down the event loop, allowing the hardware WDT to trigger a reset
    and run the fail-safe sequence - this is intentional design, not an
    oversight.

    Args:
        hw:      HardwareBundle.
        config:  config dict from load_config().
        state:   state dict (mutated and persisted after each successful
                 move).
        targets: RAM-only setpoint dict:
                 {
                     "hatches":    [{"hatch_deg": N}, ...],  # per hatch
                     "brake_only": [None, ...],              # per hatch;
                         a non-None value triggers _move_brake_only and
                         is reset to None afterwards (success or failure)
                     "tester": {"hatch_deg": None, "brake_deg": None},
                         # a non-None value triggers _move_tester_servo
                         # and is reset to None afterwards
                 }
    """
    num_hatches = len(targets["hatches"])

    while True:
        moved = False

        for i in range(num_hatches):
            if targets["hatches"][i]["hatch_deg"] != state["hatches"][i]["hatch_deg"]:
                try:
                    await _move_hatch(i, hw, config, state, targets)
                except Exception as e:
                    print(f"task_control: move hatch {i} failed: {e}")
                    # relay is already off via _move_hatch finally block;
                    # leave state unchanged and continue with next hatch
                moved = True

        for i in range(len(targets["brake_only"])):
            if targets["brake_only"][i] is not None:
                try:
                    await _move_brake_only(i, hw, config, state, targets["brake_only"][i])
                except Exception as e:
                    print(f"task_control: brake-only move hatch {i} failed: {e}")
                targets["brake_only"][i] = None
                moved = True

        for which in ("hatch", "brake"):
            if targets["tester"][f"{which}_deg"] is not None:
                try:
                    await _move_tester_servo(hw, config, state, which,
                                              targets["tester"][f"{which}_deg"])
                except Exception as e:
                    print(f"task_control: tester {which} move failed: {e}")
                targets["tester"][f"{which}_deg"] = None
                moved = True

        # Shorter sleep after a move pass to stay responsive to rapid
        # target changes; longer sleep when idle to reduce CPU load
        await uasyncio.sleep_ms(10 if moved else 50)
