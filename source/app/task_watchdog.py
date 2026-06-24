# task_watchdog.py - Hardware watchdog feeder task
#
# Feeds the ESP32 hardware WDT every _FEED_MS milliseconds.
# If the uasyncio event loop stops running (EMV, crash, or any while
# loop without await), the WDT triggers a hardware reset.
#
# Coding rule enforced by this design: every while loop in the project
# must contain at least one await so this task stays reachable.

from machine import WDT
import uasyncio

_WDT_TIMEOUT_MS = 4000  # hardware reset if not fed within this time
_FEED_MS        = 200   # feed interval – well within timeout


async def task_watchdog():
    """Initialise the hardware WDT and feed it continuously."""
    wdt = WDT(timeout=_WDT_TIMEOUT_MS)
    while True:
        wdt.feed()
        await uasyncio.sleep_ms(_FEED_MS)