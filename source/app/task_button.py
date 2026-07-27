# task_button.py - Async task: user button monitor
#
# Polls the user button and distinguishes short from long presses:
#   Short press (< BTN_LONG_PRESS_MS): shows the Wi-Fi QR code on the
#     OLED for session._QR_DURATION_S seconds (see app/session.py).
#   Long press (>= BTN_LONG_PRESS_MS): starts the config unlock window,
#     which lets the web interface show the config page button for
#     BTN_CONFIG_UNLOCK_MS (see app/session.py for session logic).
#
# Button is active low (internal pull-up on Seeed Expansion Board).
# Detects falling edge (1→0) with software debounce.

import uasyncio
import time
from app.hw_config import BTN_DEBOUNCE_MS, BTN_LONG_PRESS_MS
from app import session

_POLL_MS = 50  # button polling interval in milliseconds


async def task_button(hw, config, state, targets):
    """Poll user button and manage config unlock window.

    Args:
        hw:      HardwareBundle (hw.btn is the user button Pin)
        config:  unused, present for consistent task signature
        state:   unused, present for consistent task signature
        targets: unused, present for consistent task signature
    """
    btn        = hw.btn
    last_state = btn.value()  # 1 = released (pull-up), 0 = pressed

    while True:
        current_state = btn.value()

        # Detect falling edge: released → pressed
        if last_state == 1 and current_state == 0:
            # Debounce: wait and confirm button is still pressed
            await uasyncio.sleep_ms(BTN_DEBOUNCE_MS)
            if btn.value() == 0:
                press_start = time.ticks_ms()
                long_press  = False

                # Wait for release or long-press threshold
                while btn.value() == 0:
                    if time.ticks_diff(time.ticks_ms(), press_start) >= BTN_LONG_PRESS_MS:
                        # Long press: unlock config page
                        long_press = True
                        session.unlock_config()
                        # Wait for button release before continuing
                        while btn.value() == 0:
                            await uasyncio.sleep_ms(_POLL_MS)
                        break
                    await uasyncio.sleep_ms(_POLL_MS)

                if not long_press:
                    # Short press: show QR code on display
                    session.show_qr()

        last_state = btn.value()  # re-read after potential wait periods
        await uasyncio.sleep_ms(_POLL_MS)
