# pca9685.py - MicroPython driver for the PCA9685 16-channel PWM controller
#
# Datasheet: https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf
# I2C interface, 12-bit resolution per channel, up to 1526 Hz PWM frequency.

import time

# Register addresses (PCA9685 datasheet section 7.3)
_MODE1      = 0x00
_MODE2      = 0x01
_LED0_ON_L  = 0x06  # base register; each channel occupies 4 bytes
_ALL_LED_ON_L  = 0xFA
_ALL_LED_OFF_L = 0xFC
_PRE_SCALE  = 0xFE

# MODE1 bits
_SLEEP   = 0x10
_ALLCALL = 0x01
_AI      = 0x20  # auto-increment

# MODE2 bits
_OUTDRV  = 0x04  # totem-pole output (required for most servo/relay drivers)

_PCA9685_OSC_CLOCK = 25_000_000  # internal oscillator frequency in Hz


class PCA9685:
    """Driver for the PCA9685 16-channel 12-bit PWM controller.

    Usage:
        from machine import I2C, Pin
        i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
        pwm = PCA9685(i2c)
        pwm.freq(50)
        pwm.set_us(channel=0, us=1500)  # centre position
    """

    def __init__(self, i2c, address=0x40):
        """Initialise the driver and reset the chip to a known state.

        Args:
            i2c:     initialised machine.I2C bus.
            address: 7-bit I2C address of the PCA9685 (default 0x40).
        """
        self._i2c  = i2c
        self._addr = address
        self._reset()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, reg, val):
        self._i2c.writeto_mem(self._addr, reg, bytes([val]))

    def _read(self, reg):
        return self._i2c.readfrom_mem(self._addr, reg, 1)[0]

    def _reset(self):
        """Initialise the chip to a known state."""
        # Enable auto-increment but not ALLCALL; keep oscillator running
        self._write(_MODE1, _AI)
        self._write(_MODE2, _OUTDRV)
        time.sleep_ms(1)  # oscillator stabilisation (datasheet: min 500 µs)
        # Clear sleep bit to activate oscillator
        mode1 = self._read(_MODE1) & ~_SLEEP
        self._write(_MODE1, mode1)
        time.sleep_ms(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def freq(self, hz):
        """Set PWM frequency in Hz for all channels (24–1526 Hz).

        Puts the chip to sleep briefly to allow PRE_SCALE write (required
        by the datasheet).
        """
        # PRE_SCALE = round(osc_clock / (4096 * hz)) - 1  (datasheet eq. 1)
        prescale = round(_PCA9685_OSC_CLOCK / (4096 * hz)) - 1
        prescale = max(3, min(255, prescale))  # datasheet limits: 3–255

        mode1 = self._read(_MODE1)
        self._write(_MODE1, (mode1 & 0x7F) | _SLEEP)  # enter sleep
        self._write(_PRE_SCALE, prescale)
        self._write(_MODE1, mode1)                     # restore, exit sleep
        time.sleep_ms(1)
        self._write(_MODE1, mode1 | 0x80)              # restart

    def set_pwm(self, channel, on, off):
        """Set raw 12-bit ON and OFF tick counts for one channel.

        Args:
            channel: 0–15
            on:  tick count (0–4095) at which the pulse goes high
            off: tick count (0–4095) at which the pulse goes low
        """
        if not 0 <= channel <= 15:
            raise ValueError("channel must be 0–15")
        base = _LED0_ON_L + channel * 4
        self._i2c.writeto_mem(self._addr, base, bytes([
            on  & 0xFF, (on  >> 8) & 0x0F,
            off & 0xFF, (off >> 8) & 0x0F,
        ]))

    def set_us(self, channel, us, freq_hz=50):
        """Set servo pulse width in microseconds.

        Converts microseconds to 12-bit tick counts based on PWM frequency.

        IMPORTANT: this driver has no way to read back the frequency
        actually programmed into the chip via freq() - freq_hz here is
        used purely for the tick-count math and silently defaults to
        50 Hz if omitted. Passing a freq_hz that does not match the last
        freq() call produces a wrong pulse width with no error of any
        kind. Callers driving a chip at a non-default frequency must
        pass the same freq_hz on every single set_us() call.

        Args:
            channel: 0–15
            us:      pulse width in microseconds (e.g. 500–2500)
            freq_hz: PWM frequency in Hz (must match value set via freq())
        """
        period_us = 1_000_000 / freq_hz          # period in µs (e.g. 20 000)
        off = round(us / period_us * 4096)        # scale to 12-bit range
        off = max(0, min(4095, off))
        self.set_pwm(channel, 0, off)

    def set_all_off(self):
        """Force all channels off immediately (full off bit = bit 4 of OFF_H)."""
        self._i2c.writeto_mem(self._addr, _ALL_LED_ON_L,  bytes([0x00, 0x00]))
        self._i2c.writeto_mem(self._addr, _ALL_LED_OFF_L, bytes([0x00, 0x10]))
