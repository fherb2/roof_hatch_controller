"""
main.py – Application entry point for the roof hatch controller.

Startup sequence:
  Stage 0:  WDT-reset detection – if the previous run was killed by the
            hardware watchdog, run fail_safe_close_all() before anything
            else to bring all hatches to a safe physical state.
  Stage 1:  OLED display early init – all subsequent stages can report
            status on the display.
  Stage 1b: AP mode – if the user button was held on power-up, start
            the Wi-Fi setup access point and serve the config page.
            Device resets after the user saves new credentials.
  Stage 2:  Remaining hardware init (PCA9685, relay, button).
  Stage 2b: Load config.json and state.json.  On WDT reset, mark all
            hatches as FAILSAFE_DEG in state after the physical
            fail-safe sequence has already run in Stage 0.
  Stage 3:  Wi-Fi client mode.
  Stage 4:  Build the targets dict from persisted Soll values and start
            all async tasks via uasyncio.gather().
"""

import gc
gc.collect()
import network
network.WLAN(network.STA_IF).active(True)  # reserve WiFi memory early
gc.collect()
import uasyncio
import machine
from machine import Pin
import time
from app.hw_init import init_display_early, init_hardware, HardwareInitError, HardwareBundle

def _button_pressed():
    from app.hw_config import BTN_USER_PIN
    btn = Pin(BTN_USER_PIN, Pin.IN, Pin.PULL_UP)
    if btn.value() == 0:
        time.sleep_ms(50)
        return btn.value() == 0
    return False

AP_MODE = _button_pressed()
from app.fatal_error import fatal_error
from app.task_watchdog import task_watchdog
from app.wifi import start_ap_mode, start_client_mode
from app.failsafe import fail_safe_close_all
from app.task_display import task_display
from app.task_control import task_control
from app.task_button import task_button
from app.task_webserver import task_webserver

# Number of status lines used during boot (reserved at top of display)
_BOOT_LINES = 7


def _boot_status(display, line, text, invert=False):
    """Write a status line to the OLED during the boot sequence.

    Args:
        display: SSD1306 display object
        line:    line index 0–7 (each line is 8 px tall)
        text:    message string (truncated to 16 characters)
        invert:  if True, draw white-on-black for error highlighting
    """
    y = line * 8
    bg, fg = (1, 0) if invert else (0, 1)
    display.fill_rect(0, y, 128, 8, bg)
    display.text(text[:16], 0, y, fg)
    display.show()


async def main():
    """Boot sequence and async task launcher for the hatch controller.

    Runs all startup stages in order.  Returns early on unrecoverable
    hardware or config errors; in that case the async tasks never start
    and the hardware WDT is never initialised.
    """
    display = None

    # Capture reset cause once; referenced in Stage 0 and Stage 2b.
    wdt_reset = machine.reset_cause() == machine.WDT_RESET

    # ------------------------------------------------------------------
    # Stage 0: Fail-safe after WDT reset
    # Run before anything else – config.json may not be readable.
    # ------------------------------------------------------------------
    if wdt_reset:
        try:
            from app.hw_init import init_hw_failsafe
            hw_fs = init_hw_failsafe()
            fail_safe_close_all(hw_fs)
        except Exception as e:
            print(f"FAIL-SAFE error after WDT reset: {e}")

    # ------------------------------------------------------------------
    # Stage 1: Display early init
    # Display must come up first so all later stages can report status.
    # If the display itself is missing, we can only log to serial (USB).
    # ------------------------------------------------------------------
    try:
        i2c, display = init_display_early()
        _boot_status(display, 0, "Booting...")
    except HardwareInitError as e:
        await fatal_error(f"Display init failed: {e}")

    # ------------------------------------------------------------------
    # Stage 2: Remaining hardware init
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Stage 1b: AP mode – no hardware needed, device reboots after config
    # ------------------------------------------------------------------
    if AP_MODE:
        _boot_status(display, 1, "AP mode...")
        hw = HardwareBundle(i2c, None, None, display, None)
        await start_ap_mode(hw)
        await task_webserver(hw, {}, {}, [])
        return  # unreachable, device resets after wifi save

    _boot_status(display, 1, "Init hardware...")
    try:
        hw = init_hardware(i2c=i2c, display=display)
    except HardwareInitError as e:
        await fatal_error(f"Hardware init failed: {e}", display=display)

    _boot_status(display, 2, "Hardware OK")

    # ------------------------------------------------------------------
    # Stage 2b: Load config and state
    # ------------------------------------------------------------------
    from app.hw_config import FAILSAFE_DEG
    from app.state import (load_config, load_state, save_state,
                           create_default_state, ConfigError, StateError)
    from app.config_migration import migrate_config, ConfigMigrationError
    _boot_status(display, 3, "Loading config")
    try:
        migrate_config()
    except ConfigMigrationError as e:
        await fatal_error(f"Config template missing: {e}", hw=hw, display=display)
    try:
        config = load_config()
    except ConfigError as e:
        await fatal_error(f"Config invalid: {e}", hw=hw, display=display)

    try:
        state = load_state()
    except StateError:
        state = create_default_state(config)
        save_state(state)

    if wdt_reset:
        # Physical fail-safe has already run in Stage 0. Now mark all
        # hatches as FAILSAFE_DEG so state.json matches the physical
        # reality: positions are unknown, hatches are unlocked. The
        # servo tester has no fail-safe concept (never moves on its
        # own) and is left untouched.
        for h in state["hatches"]:
            h["hatch_deg"]  = FAILSAFE_DEG
            h["target_deg"] = FAILSAFE_DEG
        save_state(state)

    # ------------------------------------------------------------------
    # Stage 3: Wi-Fi
    # ------------------------------------------------------------------
    _boot_status(display, 5, "WiFi connecting")
    await start_client_mode(hw)

    # ------------------------------------------------------------------
    # Stage 4: Launch async tasks
    # ------------------------------------------------------------------
    targets = {
        "hatches":    [{"hatch_deg": h["target_deg"]} for h in state["hatches"]],
        "brake_only": [None for _ in state["hatches"]],
        "tester":     {"hatch_deg": None, "brake_deg": None},
    }
    _boot_status(display, 6, "Starting...")
    await uasyncio.gather(
        task_watchdog(),
        task_display(hw, config, state, targets),
        task_control(hw, config, state, targets),
        task_button(hw, config, state, targets),
        task_webserver(hw, config, state, targets),
    )


uasyncio.run(main())
