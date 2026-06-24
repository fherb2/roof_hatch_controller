# boot.py - Early startup, runs before main.py
#
# Keeps hardware access to an absolute minimum. Only reads the user button
# to decide whether to enter Wi-Fi AP configuration mode, then sets a flag
# for main.py to act on.
#
# AP mode: hold the user button while powering on / resetting the device.

from machine import Pin
import time

# User button pin (GPIO 1 on Seeed Expansion Board, active low, internal pull-up)
_BTN_PIN = 1
_BTN_DEBOUNCE_MS = 50

def _button_pressed():
    """Return True if the user button is held down at boot."""
    btn = Pin(_BTN_PIN, Pin.IN, Pin.PULL_UP)
    # Debounce: sample twice with a short delay
    if btn.value() == 0:
        time.sleep_ms(_BTN_DEBOUNCE_MS)
        return btn.value() == 0
    return False

# Flag read by main.py to select startup mode.
# True  → start in Wi-Fi AP mode for network configuration
# False → normal operation as Wi-Fi client
AP_MODE = _button_pressed()
