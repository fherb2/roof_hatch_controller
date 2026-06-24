# main.py - Application entry point for roof hatch controller
#
# Startup sequence:
#   1. Init OLED display early so all subsequent steps can show status
#   2. Init remaining hardware (PCA9685, relay, button)
#   3. Start Wi-Fi: AP config mode or client mode depending on boot.AP_MODE
#   4. Launch async tasks

import uasyncio
import machine
import boot
from app.failsafe import fail_safe_close_all
from app.task_watchdog import task_watchdog
from app.hw_init import init_display_early, init_hardware, HardwareInitError
from app.wifi import start_ap_mode, start_client_mode
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
    display = None

    # ------------------------------------------------------------------
    # Stage 0: Fail-safe after WDT reset
    # Run before anything else – config.json may not be readable.
    # ------------------------------------------------------------------
    if machine.reset_cause() == machine.WDT_RESET:
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
        print(f"FATAL: display init failed: {e}")
        return

    # ------------------------------------------------------------------
    # Stage 2: Remaining hardware init
    # ------------------------------------------------------------------
    _boot_status(display, 1, "Init hardware...")
    try:
        hw = init_hardware(i2c=i2c, display=display)
    except HardwareInitError as e:
        _boot_status(display, 2, "HW ERROR:", invert=True)
        _boot_status(display, 3, str(e)[:16], invert=True)
        print(f"FATAL: hardware init failed: {e}")
        return

    _boot_status(display, 2, "Hardware OK")

    # ------------------------------------------------------------------
    # Stage 2b: Load config and state
    # ------------------------------------------------------------------
    from app.state import (load_config, load_state, save_state,
                           create_default_state, ConfigError, StateError)
    _boot_status(display, 3, "Loading config")
    try:
        config = load_config()
    except ConfigError as e:
        _boot_status(display, 4, "CFG ERROR:", invert=True)
        _boot_status(display, 5, str(e)[:16], invert=True)
        print(f"FATAL: config error: {e}")
        return

    try:
        state = load_state()
    except StateError:
        state = create_default_state(config)
        save_state(state)

    # ------------------------------------------------------------------
    # Stage 3: Wi-Fi
    # ------------------------------------------------------------------
    if boot.AP_MODE:
        _boot_status(display, 5, "AP mode")
        await start_ap_mode(hw)
    else:
        _boot_status(display, 5, "WiFi connecting")
        await start_client_mode(hw)

    # ------------------------------------------------------------------
    # Stage 4: Launch async tasks
    # All tasks run indefinitely inside their own async loops.
    # ------------------------------------------------------------------
    _boot_status(display, 6, "Starting...")
    await uasyncio.gather(
        task_watchdog(),
        task_display(hw, config, state, targets),
        task_control(hw, config, state, targets),
        task_button(hw, config, state, targets),
        task_webserver(hw, config, state, targets),
    )


uasyncio.run(main())
