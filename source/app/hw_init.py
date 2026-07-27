# hw_init.py - Hardware initialisation for roof hatch controller
#
# Call init_hardware() once at startup. Returns a HardwareBundle with all
# initialised hardware objects. Raises HardwareInitError on failure.

from machine import I2C, Pin
import ssd1306
from shared.pca9685 import PCA9685
from app.hw_config import (
    I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ,
    I2C_ADDR_OLED, I2C_ADDR_PCA9685, I2C_ADDR_RELAY,
    PCA9685_FREQ_HZ, BTN_USER_PIN,
)
from shared.grove_relay_4chn_spdt import GroveRelay


class HardwareInitError(Exception):
    """Raised when a hardware component cannot be initialised."""
    pass


class HardwareBundle:
    """Container for all initialised hardware objects.

    Attributes:
        i2c     (I2C):       shared I2C bus
        pwm     (PCA9685):   16-channel PWM driver
        relay   (GroveRelay): 4-channel relay board
        display (SSD1306_I2C): OLED display 128x64
        btn     (Pin):       user button (active low, internal pull-up)
    """
    def __init__(self, i2c, pwm, relay, display, btn):
        self.i2c     = i2c
        self.pwm     = pwm
        self.relay   = relay
        self.display = display
        self.btn     = btn


def _check_i2c_device(i2c, address, name):
    """Verify that a device answers at the given I2C address, raising
    HardwareInitError with a descriptive message if not.

    Addresses above 0x77 (the standard 7-bit I2C scan range) are outside
    what i2c.scan() reports, so those are probed directly with a
    zero-byte write instead of relying on the scan result.

    Args:
        i2c:     initialised I2C bus.
        address: 7-bit I2C address to check.
        name:    human-readable device name, used in the error message.
    Raises:
        HardwareInitError: if no device acknowledges at address.
    """
    if address > 0x77:
        # Addresses 0x78-0x7F are outside the range scanned by i2c.scan()
        # (reserved per I2C spec). Probe directly with a write attempt.
        try:
            i2c.writeto(address, b'')
        except OSError:
            raise HardwareInitError(
                f"{name} not found on I2C bus (expected 0x{address:02X})"
            )
    else:
        devices = i2c.scan()
        if address not in devices:
            raise HardwareInitError(
                f"{name} not found on I2C bus (expected 0x{address:02X}, "
                f"found: {[hex(d) for d in devices]})"
            )

def init_hw_failsafe():
    """Minimal hardware init for fail-safe sequence.

    Initialises only I2C bus, PCA9685 and relay – no display, no button.
    Safe to call immediately after WDT reset before normal startup.

    Returns:
        HardwareBundle with display=None and btn=None.
    Raises:
        HardwareInitError: if PCA9685 or relay cannot be reached.
    """
    i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=I2C_FREQ)
    _check_i2c_device(i2c, I2C_ADDR_PCA9685, "PCA9685 PWM driver")
    _check_i2c_device(i2c, I2C_ADDR_RELAY,   "Grove 4-ch relay")
    pwm = PCA9685(i2c, address=I2C_ADDR_PCA9685)
    pwm.freq(PCA9685_FREQ_HZ)
    pwm.set_all_off()
    relay = GroveRelay(i2c, address=I2C_ADDR_RELAY)
    relay.all_off()
    return HardwareBundle(i2c, pwm, relay, None, None)

def init_display_early():
    """Initialise only the I2C bus and OLED display.

    Call this first so the display is available for status and error
    messages during the rest of the startup sequence.

    Returns:
        tuple (I2C, SSD1306_I2C)
    Raises:
        HardwareInitError: if the display is not found on the I2C bus.
    """
    i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=I2C_FREQ)
    _check_i2c_device(i2c, I2C_ADDR_OLED, "SSD1306 OLED display")
    display = ssd1306.SSD1306_I2C(128, 64, i2c, addr=I2C_ADDR_OLED)
    display.fill(0)
    display.show()
    return i2c, display

def init_hardware(i2c=None, display=None):
    """Initialise all hardware components and return a HardwareBundle.

    If i2c and display are provided (from init_display_early()), they are
    reused instead of being initialised again.

    Initialisation order:
    1. I2C bus (skipped if i2c provided)
    2. I2C device presence check for remaining devices
    3. PCA9685 PWM driver (all channels off after init)
    4. Grove relay (all relays open after init)
    5. SSD1306 OLED display (skipped if display provided)
    6. User button pin

    Raises:
        HardwareInitError: if any I2C device is missing or init fails.
    """
    # -- I2C bus ----------------------------------------------------------
    if i2c is None:
        i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=I2C_FREQ)

    # -- Verify remaining I2C devices are present -------------------------
    _check_i2c_device(i2c, I2C_ADDR_PCA9685, "PCA9685 PWM driver")
    _check_i2c_device(i2c, I2C_ADDR_RELAY,   "Grove 4-ch relay")
    if display is None:
        _check_i2c_device(i2c, I2C_ADDR_OLED, "SSD1306 OLED display")

    # -- PCA9685 PWM driver -----------------------------------------------
    pwm = PCA9685(i2c, address=I2C_ADDR_PCA9685)
    pwm.freq(PCA9685_FREQ_HZ)
    pwm.set_all_off()  # safe state: all servo signals off

    # -- Grove 4-channel relay --------------------------------------------
    relay = GroveRelay(i2c, address=I2C_ADDR_RELAY)
    relay.all_off()  # safe state: all relays open, servos unpowered

    # -- SSD1306 OLED display (128x64) ------------------------------------
    if display is None:
        display = ssd1306.SSD1306_I2C(128, 64, i2c, addr=I2C_ADDR_OLED)
        display.fill(0)
        display.show()

    # -- User button (active low, internal pull-up) -----------------------
    btn = Pin(BTN_USER_PIN, Pin.IN, Pin.PULL_UP)

    return HardwareBundle(i2c, pwm, relay, display, btn)
