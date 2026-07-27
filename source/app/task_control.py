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

IMPORTANT: shared/pca9685.py's set_us() converts microseconds to a 12-bit
tick count using a freq_hz argument (default 50) - it has no way to read
back the frequency actually programmed into the chip via freq(). Every
set_us() call in this module MUST therefore be given the same freq_hz
that was just passed to _ensure_pwm_freq() for that same move, or the
tick-count math silently assumes 50 Hz regardless of the real PRE_SCALE
value, producing a wrong pulse width (and therefore a wrong angle)
whenever a channel's configured pwm_freq_hz differs from 50.

RAMPING IS TIME-BASED, NOT STEP-COUNT-BASED (see _ramp()): the commanded
position at each tick is computed from actually elapsed wall-clock time,
not from a running step counter. This matters because task_display (OLED
refresh, every 333 ms) and task_webserver share the same I2C bus and
event loop with this task; a step-count-based ramp that just adds a
fixed increment every "step_ms via sleep_ms()" has no way to compensate
when a tick is delayed by another task - the delayed step is simply
skipped, which is perceived as the servo periodically slowing down and
speeding back up while driving to its target, even though the configured
speed and hardware are both fine. Time-based ramping self-corrects: a
late tick just computes a larger jump to stay on the correct time/
position curve, so the resulting *average* speed matches the configured
speed_deg_per_s regardless of individual tick jitter.

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
                          pwm_freq_hz if not already active. Happens
                          BEFORE any set_us() call for this move, since
                          set_us()'s tick-count math depends on it and
                          freq() itself briefly resets the chip.
  step_relay_on:          Relay on -> both servos of this hatch powered.
  (wait _RELAY_ON_TO_TORQUE_MS): time for the hatch servo to take up the
                          holding load, counted from relay-on, before the
                          brake is allowed to release. No wiggle step
                          needed anymore - the smoothstep ramp profile
                          (see _ramp()) starts every move at zero speed.
  step_ramp_brake_open:   Brake servo ramps from its actual position to
                          open_deg (its own speed_deg_per_s). No pause
                          afterwards - the hatch move starts the instant
                          this ramp finishes.
  step_ramp_hatch:        Hatch servo ramps to the target angle (its own
                          speed_deg_per_s), clamped to
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

_move_brake_only() and _move_tester_servo() are simplified variants used
for the per-servo "mounting angle" maintenance moves and the servo
tester - see their own docstrings below.

The hardware WDT (task_watchdog) acts as the last safety net for cases
where the event loop itself stops responding.
"""

import time
import uasyncio
from app.hw_config import PCA9685_CH, RELAY_CH, TESTER_CH, FAILSAFE_DEG, MIN_STEP_MS
from app.state import save_state
from shared.servo_units import deg_to_us

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

_MAX_MOVE_RETRIES = 3     # consecutive failures before a hatch's target is
                           # cleared, to stop an endless retry loop (see
                           # task_control() below)
_fail_count = [0, 0, 0, 0]


def _ensure_pwm_freq(hw, freq_hz):
    """Reprogram the PCA9685's chip-wide frequency if it isn't already
    set to freq_hz. See the module docstring for why this is per-move
    rather than per-channel."""
    global _current_pwm_freq
    if freq_hz != _current_pwm_freq:
        hw.pwm.freq(freq_hz)
        _current_pwm_freq = freq_hz


def _ramp_duration_ms(distance_deg, speed_deg_per_s):
    """Total wall-clock duration for a ramp covering distance_deg at the
    configured angular speed. Used by the time-based _ramp() below.

    _ramp() follows a smoothstep (S-curve) profile rather than constant
    velocity, so that the motion starts and ends at exactly zero speed
    at both ends - no jerk (discontinuous acceleration) at start or
    stop, which is kinder to the servo gearing than the abrupt full-speed
    start/stop of a linear ramp. A smoothstep profile's peak velocity
    (reached at the midpoint) is 1.5x its average velocity for the same
    distance and duration; multiplying the naive constant-velocity
    duration by 1.5 here compensates for that geometry, so the
    configured speed_deg_per_s ends up as the actual peak speed - never
    exceeded - instead of just the average.

    Args:
        distance_deg:    signed or unsigned angular distance to cover;
                          only the magnitude matters.
        speed_deg_per_s: configured speed, must be > 0 (validated by
                          state.validate_config()) - this is the PEAK
                          speed reached at the midpoint of the move, not
                          the average.
    Returns:
        float duration in milliseconds, >= 0.
    """
    return 1.5 * abs(distance_deg) / speed_deg_per_s * 1000


def _tick_interval_ms(freq_hz):
    """Interval between PWM update writes during a ramp: never faster
    than the PCA9685's own refresh period (1000/freq_hz ms) - writing
    faster than the chip refreshes wastes I2C bandwidth for no visible
    benefit - and never faster than MIN_STEP_MS, which keeps the I2C bus
    load bounded at high configured frequencies."""
    return max(MIN_STEP_MS, round(1000 / freq_hz))


async def _ramp(hw, channel, start_us, target_us, duration_ms, freq_hz):
    """Ramp a single PCA9685 channel from start_us to target_us over
    duration_ms of wall-clock time.

    Time-based rather than step-count-based - see the module docstring's
    "RAMPING IS TIME-BASED" section for why: a tick delayed by scheduling
    jitter (task_display/task_webserver sharing the I2C bus and event
    loop) is caught up on the very next tick instead of silently
    producing a slower average speed for that stretch of the ramp.

    Args:
        hw:          HardwareBundle.
        channel:     PCA9685 channel to drive.
        start_us:    pulse width to start from.
        target_us:   pulse width to end at.
        duration_ms: total time the ramp should take, as computed by
                     _ramp_duration_ms(). A duration <= 0 (or start
                     already equal to target) jumps straight to target.
        freq_hz:     the frequency currently programmed into the chip
                     for this move (see the module docstring's IMPORTANT
                     note) - set_us() cannot read it back itself.
    Returns:
        int the final pulse width written (== target_us).
    """
    if duration_ms <= 0 or start_us == target_us:
        hw.pwm.set_us(channel, target_us, freq_hz)
        return target_us

    tick_ms    = _tick_interval_ms(freq_hz)
    start_tick = time.ticks_ms()
    distance   = target_us - start_us

    while True:
        elapsed = time.ticks_diff(time.ticks_ms(), start_tick)
        if elapsed >= duration_ms:
            hw.pwm.set_us(channel, target_us, freq_hz)
            return target_us
        # Smoothstep easing (3f^2 - 2f^3): both the position curve and its
        # first derivative (velocity) are continuous and zero at f=0/f=1,
        # so the motion starts and ends with zero speed - no jerk at
        # either end. Peak velocity at f=0.5 is compensated for in
        # _ramp_duration_ms() (1.5x factor) so it matches speed_deg_per_s.
        frac  = elapsed / duration_ms
        eased = frac * frac * (3 - 2 * frac)
        current_us = round(start_us + distance * eased)
        hw.pwm.set_us(channel, current_us, freq_hz)
        await uasyncio.sleep_ms(tick_ms)


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
    freq_hz              = h["pwm_freq_hz"]

    # Clamp target to this hatch's own hardstop range (targets may be
    # set from web/button before config is fully validated for this hatch)
    target_deg = max(hs["hard_stop_min_deg"],
                      min(hs["hard_stop_max_deg"], targets["hatches"][idx]["hatch_deg"]))
    targets["hatches"][idx]["hatch_deg"] = target_deg  # write back clamped value

    # Capture actual position before overwriting it with FAILSAFE_DEG
    actual_deg = s["hatch_deg"]
    actual_brake_deg = s["brake_deg"]

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
    brake_preset_us = deg_to_us(actual_brake_deg, bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_open_us   = deg_to_us(bs["open_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_close_us  = deg_to_us(bs["close_deg"], bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])

    hatch_duration_ms       = _ramp_duration_ms(target_deg - preset_deg, hs["speed_deg_per_s"])
    brake_open_duration_ms  = _ramp_duration_ms(bs["open_deg"] - actual_brake_deg, bs["speed_deg_per_s"])
    brake_close_duration_ms = _ramp_duration_ms(bs["close_deg"] - bs["open_deg"], bs["speed_deg_per_s"])

    # -- step_preset_servos: brake to its own actual position (not -------
    #    open_deg - avoids a jerk on relay-on); hatch to 0 (no signal)
    #    so the servo controller does not see a valid pulse during its
    #    power-on boot sequence
    # -- step_set_pwm_freq: MUST happen before any set_us() for this move,
    #    because set_us() computes tick counts for freq_hz and freq()
    #    restarts the chip (which would disturb outputs written earlier)
    _ensure_pwm_freq(hw, freq_hz)

    hw.pwm.set_us(hatch_ch, 0, freq_hz)
    hw.pwm.set_us(brake_ch, brake_preset_us, freq_hz)

    # -- step_relay_on: both servos of this hatch now powered -------------
    hw.relay.on(relay_ch)

    try:
        # -- step_preset_delay: wait for servo controller to boot ---------
        #    before presenting a valid PWM signal
        if _PRESET_DELAY_MS > 0:
            await uasyncio.sleep_ms(_PRESET_DELAY_MS)

        # -- wait for the hatch servo to take up the holding torque -------
        #    before the brake is allowed to release. The preset pulse
        #    from step_preset_servos is already applied at this point;
        #    no wiggle needed - _ramp()'s smoothstep profile starts every
        #    move at zero speed, so there is no abrupt jump to catch up
        #    from in the first place.
        await uasyncio.sleep_ms(_RELAY_ON_TO_TORQUE_MS)

        # -- step_ramp_brake_open: brake ramps to open, no pause after ----
        await _ramp(hw, brake_ch, brake_preset_us, brake_open_us,
                    brake_open_duration_ms, freq_hz)

        # -- step_ramp_hatch: hatch ramps to target position --------------
        await _ramp(hw, hatch_ch, preset_us, target_us,
                    hatch_duration_ms, freq_hz)

        # -- wait for the hatch to settle at its final position before ----
        #    commanding the brake to close
        await uasyncio.sleep_ms(_HATCH_SETTLE_TO_BRAKE_CLOSE_MS)

        # -- step_ramp_brake_close: brake ramps to close -------------------
        await _ramp(hw, brake_ch, brake_open_us, brake_close_us,
                    brake_close_duration_ms, freq_hz)

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
    freq_hz              = h["pwm_freq_hz"]

    target_deg = max(bs["hard_stop_min_deg"], min(bs["hard_stop_max_deg"], target_deg))
    actual_brake_deg = s["brake_deg"]

    hatch_hold_deg = (h["positions"][0]["hatch_deg"]
                      if s["hatch_deg"] == FAILSAFE_DEG else s["hatch_deg"])
    hatch_hold_us   = deg_to_us(hatch_hold_deg, hs["pulse_min_us"], hs["pulse_max_us"],
                                 hs["range_deg"], hs["direction"])
    brake_preset_us = deg_to_us(actual_brake_deg, bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_target_us = deg_to_us(target_deg, bs["pulse_min_us"], bs["pulse_max_us"],
                                 bs["range_deg"], bs["direction"])
    brake_duration_ms = _ramp_duration_ms(target_deg - actual_brake_deg, bs["speed_deg_per_s"])

    _ensure_pwm_freq(hw, freq_hz)
    hw.pwm.set_us(hatch_ch, 0, freq_hz)
    hw.pwm.set_us(brake_ch, brake_preset_us, freq_hz)
    hw.relay.on(relay_ch)

    try:
        if _PRESET_DELAY_MS > 0:
            await uasyncio.sleep_ms(_PRESET_DELAY_MS)

        # Hold the hatch servo steady at its current position - it is
        # not the servo being moved here, only kept powered so the
        # shared relay can stay on for the brake move below.
        hw.pwm.set_us(hatch_ch, hatch_hold_us, freq_hz)

        await _ramp(hw, brake_ch, brake_preset_us, brake_target_us,
                    brake_duration_ms, freq_hz)

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
    st        = config["global"]["servo_tester"]
    servo_cfg = st[f"{which}_servo"]
    channel   = TESTER_CH[0 if which == "hatch" else 1]
    freq_hz   = st["pwm_freq_hz"]

    target_deg = max(servo_cfg["hard_stop_min_deg"],
                      min(servo_cfg["hard_stop_max_deg"], target_deg))

    current_deg = state["servo_tester"][f"{which}_deg"]
    current_us  = deg_to_us(current_deg, servo_cfg["pulse_min_us"], servo_cfg["pulse_max_us"],
                             servo_cfg["range_deg"], servo_cfg["direction"])
    target_us   = deg_to_us(target_deg, servo_cfg["pulse_min_us"], servo_cfg["pulse_max_us"],
                             servo_cfg["range_deg"], servo_cfg["direction"])
    duration_ms = _ramp_duration_ms(target_deg - current_deg, servo_cfg["speed_deg_per_s"])

    _ensure_pwm_freq(hw, freq_hz)
    await _ramp(hw, channel, current_us, target_us, duration_ms, freq_hz)

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

    A hatch whose move keeps failing has its target cleared after
    _MAX_MOVE_RETRIES consecutive failures (see _fail_count) rather than
    being retried forever - an endless retry loop would otherwise write
    state.json to flash on every single pass (step_mark_failsafe runs
    again each retry), starving the network stack and wearing the flash.

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
                    _fail_count[i] = 0
                except Exception as e:
                    _fail_count[i] += 1
                    print(f"task_control: move hatch {i} failed "
                          f"({_fail_count[i]}/{_MAX_MOVE_RETRIES}): {e}")
                    if _fail_count[i] >= _MAX_MOVE_RETRIES:
                        targets["hatches"][i]["hatch_deg"] = state["hatches"][i]["hatch_deg"]
                        _fail_count[i] = 0
                        print(f"task_control: hatch {i} giving up, target cleared")
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
