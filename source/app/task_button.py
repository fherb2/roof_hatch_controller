# task_button.py - Async task: user button monitor
#
# Polls the user button and starts the config unlock window on press.
# The unlock window allows the web interface to show the config page
# button for 60 seconds (see app/session.py for session logic).
#
# Button is active low (internal pull-up on Seeed Expansion Board).
# Detects falling edge (1→0) with software debounce.

import uasyncio
from app.hw_config import BTN_DEBOUNCE_MS
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
                session.unlock_config()

        last_state = current_state
        await uasyncio.sleep_ms(_POLL_MS)
