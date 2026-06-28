# failsafe.py - Fail-safe sequence for roof hatch controller
#
# Opens brakes and cuts servo power for all hatches in sequence,
# allowing them to close under their own weight. The brake servo is
# left powered-off in the open position; the brake spring alone cannot
# force the cam mechanism past the unpowered servo, so the hatch
# remains unlocked after closing.
#
# Runs synchronously with time.sleep_ms() – safe to call before
# the uasyncio event loop starts and without config.json being readable.

import time
from app.hw_config import (
    NUM_HATCHES, PCA9685_CH, RELAY_CH,
    BRAKE_TRAVEL_MS, BRAKE_OPEN_US_FAILSAFE,
)


def fail_safe_close_all(hw, brake_open_us=None):
    """Open brakes and cut servo power for all hatches in sequence.

    For each hatch:
      1. Set brake servo PWM to open position (before relay-on)
      2. Relay on  →  brake servo receives power, moves to open
      3. Wait BRAKE_TRAVEL_MS for brake servo to reach open position
      4. Relay off →  hatch closes under gravity; brake servo is left
                      powered-off in the open position (spring cannot
                      force the cam past the unpowered servo →
                      hatch unlocked after closing)

    Only one hatch is powered at a time to stay within PSU limits.

    Args:
        hw:            HardwareBundle (pwm + relay must be initialised;
                       display and btn may be None)
        brake_open_us: brake open pulse width in µs. If None, uses
                       BRAKE_OPEN_US_FAILSAFE from hw_config as fallback
                       when config.json is not available.
    """
    if brake_open_us is None:
        brake_open_us = BRAKE_OPEN_US_FAILSAFE

    for i in range(NUM_HATCHES):
        _, brake_ch = PCA9685_CH[i]
        relay_ch    = RELAY_CH[i]

        # Output brake-open position before powering the servo so it
        # moves immediately in the right direction when relay closes.
        hw.pwm.set_us(brake_ch, brake_open_us)

        # Power on – brake servo can now move
        hw.relay.on(relay_ch)

        # Wait for brake servo to reach open position
        time.sleep_ms(BRAKE_TRAVEL_MS)

        # Cut power – hatch closes under gravity; brake servo is left
        # powered-off in the open position (spring cannot force cam
        # past unpowered servo → hatch unlocked after closing)
        hw.relay.off(relay_ch)

        # Brief pause between hatches to avoid PSU overload
        time.sleep_ms(200)
