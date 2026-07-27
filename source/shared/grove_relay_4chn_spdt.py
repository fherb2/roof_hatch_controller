# grove_relay.py - MicroPython driver for the Grove 4-Channel SPDT Relay
#
# Protocol reference: Seeed Multi_Channel_Relay_Arduino_Library
# The on-board STM32F030F4P6 accepts I2C commands and switches relays.
#
# IMPORTANT - Voltage compatibility:
#   The board is designed for 5V I2C logic. The XIAO ESP32-S3 uses 3.3V.
#   In practice the board usually responds at 3.3V because the STM32 I2C
#   inputs are 5V-tolerant and the pull-ups on the Grove cable go to 3.3V
#   when connected to the expansion board. If relays do not respond,
#   add a 3.3V→5V level shifter on SDA/SCL.
#
# Channel numbering:
#   Internally this driver uses 0-based channel indices (0–3) consistent
#   with hw_config.py. The hardware bitmask maps channel n → bit n.
#
# Relay state:
#   The board provides no read-back of relay state. State is maintained
#   in software (_state bitmask). On construction all relays are opened.

# I2C command bytes (from STM32 firmware)
_CMD_CHANNEL_CTRL    = 0x10  # set all channels; data byte = bitmask
_CMD_SAVE_I2C_ADDR   = 0x11  # persist new I2C address to flash
_CMD_READ_I2C_ADDR   = 0x12  # read current I2C address
_CMD_READ_FW_VER     = 0x13  # read firmware version byte

_NUM_CHANNELS = 4


class GroveRelay:
    """Driver for the Grove 4-Channel SPDT Relay board (I2C).

    All channels use the NO (normally open) contact:
        relay off  → contact open  → servos unpowered  (safe / default state)
        relay on   → contact closed → servos powered

    Usage:
        relay = GroveRelay(i2c, address=0x11)
        relay.on(0)       # power servos of hatch 0
        relay.off(0)      # cut power to servos of hatch 0
        relay.all_off()   # safe state: all relays open
    """

    def __init__(self, i2c, address=0x11):
        """Initialise the driver and force all relays off.

        Args:
            i2c:     initialised machine.I2C bus.
            address: 7-bit I2C address of the relay board (default 0x11).
        """
        self._i2c   = i2c
        self._addr  = address
        self._state = 0x00  # bitmask of currently energised channels
        # Ensure hardware matches software state (all off)
        self._write_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_state(self):
        """Send current _state bitmask to the relay board."""
        self._i2c.writeto(self._addr, bytes([_CMD_CHANNEL_CTRL, self._state]))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on(self, channel):
        """Energise one relay (close NO contact → servos powered).

        Args:
            channel: 0-based channel index (0–3)
        """
        if not 0 <= channel < _NUM_CHANNELS:
            raise ValueError(f"channel must be 0–{_NUM_CHANNELS - 1}")
        self._state |= (1 << channel)
        self._write_state()

    def off(self, channel):
        """De-energise one relay (open NO contact → servos unpowered).

        Args:
            channel: 0-based channel index (0–3)
        """
        if not 0 <= channel < _NUM_CHANNELS:
            raise ValueError(f"channel must be 0–{_NUM_CHANNELS - 1}")
        self._state &= ~(1 << channel)
        self._write_state()

    def all_off(self):
        """De-energise all relays. Safe state: all servos unpowered."""
        self._state = 0x00
        self._write_state()

    def all_on(self):
        """Energise all relays. Use with caution: exceeds PSU rating."""
        self._state = (1 << _NUM_CHANNELS) - 1  # 0x0F for 4 channels
        self._write_state()

    def is_on(self, channel):
        """Return True if the relay for the given channel is energised.

        Note: reflects software state only; hardware state is not read back.

        Args:
            channel: 0-based channel index (0–3)
        """
        if not 0 <= channel < _NUM_CHANNELS:
            raise ValueError(f"channel must be 0–{_NUM_CHANNELS - 1}")
        return bool(self._state & (1 << channel))

    def get_firmware_version(self):
        """Read and return the firmware version byte from the board."""
        self._i2c.writeto(self._addr, bytes([_CMD_READ_FW_VER]))
        return self._i2c.readfrom(self._addr, 1)[0]
