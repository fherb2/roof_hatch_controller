# task_control.py - Async task: hatch control loop
#
# Continuously compares target and actual positions for each hatch.
# If a difference is found, executes the full movement sequence for
# that hatch before proceeding to the next one.
#
# Movement sequence per hatch:
#   1. Output last known servo positions to PCA9685
#   2. Relay on  →  servos powered
#   3. Wait delay_after_power_on_ms
#   4. Brake servo → brake_open_us
#   5. Wait delay_after_brake_open_ms
#   6. Hatch servo → target position (stepped, respects hardstops)
#   7. Wait delay_after_move_ms
#   8. Brake servo → brake_close_us
#   9. Wait delay_after_brake_close_ms
#  10. Relay off  →  servos unpowered
#  11. Update state, persist to flash

import uasyncio
import time
from app.hw_config import PCA9685_CH, RELAY_CH
from app.state import save_state


class _WatchdogError(Exception):
    """Raised when a relay has been on longer than SERVO_WATCHDOG_MS."""
    pass


def _check_watchdog(relay_on_ticks, hatch_idx):
    """Raise _WatchdogError if relay has been on too long.

    Args:
        relay_on_ticks: value of time.ticks_ms() when relay was switched on
        hatch_idx:      hatch index for error message
    """
    elapsed = time.ticks_diff(time.ticks_ms(), relay_on_ticks)
    if elapsed > SERVO_WATCHDOG_MS:
        raise _WatchdogError(
            f"hatch {hatch_idx}: relay on for {elapsed} ms "
            f"(limit {SERVO_WATCHDOG_MS} ms)"
        )


async def _move_hatch(idx, hw, config, state, targets):
    """Execute the full movement sequence for one hatch.

    Clamps the target position to the configured hardstop range before
    moving. On watchdog timeout, cuts the relay immediately and resets
    the target to the last known actual position to prevent retry loops.

    Args:
        idx:     hatch index (0–3)
        hw:      HardwareBundle
        config:  config dict from load_config()
        state:   state dict from load_state() – mutated and persisted on success
        targets: RAM-only target dict – mutated on watchdog error
    """
    g = config["global"]
    h = config["hatches"][idx]
    s = state["hatches"][idx]
    hatch_ch, brake_ch = PCA9685_CH[idx]
    relay_ch            = RELAY_CH[idx]

    # Clamp target to hardstop range (targets may be set from web/button
    # before config is fully validated for this hatch)
    target_us = max(h["hatch_min_us"],
                    min(h["hatch_max_us"], targets[idx]["hatch_us"]))
    targets[idx]["hatch_us"] = target_us  # write back clamped value

    # -- Step 1: output last known positions before powering servos -------
    hw.pwm.set_us(hatch_ch, s["hatch_us"])
    hw.pwm.set_us(brake_ch, s["brake_us"])

    # -- Step 2: relay on, start watchdog ---------------------------------
    relay_on_ticks = time.ticks_ms()
    hw.relay.on(relay_ch)

    try:
        # -- Step 3: wait after power-on ----------------------------------
        await uasyncio.sleep_ms(g["delay_after_power_on_ms"])
        _check_watchdog(relay_on_ticks, idx)

        # -- Step 4+5: open brake, wait -----------------------------------
        hw.pwm.set_us(brake_ch, g["brake_open_us"])
        await uasyncio.sleep_ms(g["delay_after_brake_open_ms"])
        _check_watchdog(relay_on_ticks, idx)

        # -- Step 6: move hatch servo step by step ------------------------
        current_us = s["hatch_us"]
        step       = h["step_us"]
        while current_us != target_us:
            if current_us < target_us:
                current_us = min(current_us + step, target_us)
            else:
                current_us = max(current_us - step, target_us)
            hw.pwm.set_us(hatch_ch, current_us)
            await uasyncio.sleep_ms(h["step_ms"])
            _check_watchdog(relay_on_ticks, idx)

        # -- Step 7: wait after move --------------------------------------
        await uasyncio.sleep_ms(g["delay_after_move_ms"])
        _check_watchdog(relay_on_ticks, idx)

        # -- Step 8+9: close brake, wait ----------------------------------
        hw.pwm.set_us(brake_ch, g["brake_close_us"])
        await uasyncio.sleep_ms(g["delay_after_brake_close_ms"])
        # No watchdog check here: relay goes off in finally immediately after

        # -- Step 11: update and persist state ----------------------------
        s["hatch_us"] = target_us
        s["brake_us"] = g["brake_close_us"]
        save_state(state)

    except _WatchdogError as e:
        # Position unknown after timeout – reset target to last known
        # actual position so the loop does not retry endlessly.
        print(f"WATCHDOG ERROR: {e}")
        targets[idx]["hatch_us"] = s["hatch_us"]

    finally:
        # Always cut servo power, regardless of success or error
        hw.relay.off(relay_ch)


async def task_control(hw, config, state, targets):
    """Async control loop: iterate all hatches, move where needed.

    Runs forever. Yields between loop passes and between every step
    during movement so other tasks remain responsive.

    Args:
        hw:      HardwareBundle
        config:  config dict from load_config()
        state:   state dict (mutated and persisted after each move)
        targets: RAM-only setpoint dict (mutated by web/button tasks)
    """
    num_hatches = len(targets)

    while True:
        moved = False
        for i in range(num_hatches):
            if targets[i]["hatch_us"] != state["hatches"][i]["hatch_us"]:
                await _move_hatch(i, hw, config, state, targets)
                moved = True

        # Yield briefly; shorter when idle to stay responsive to new targets
        await uasyncio.sleep_ms(50 if not moved else 10)
