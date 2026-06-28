# hw_config.py - Hardware configuration constants for roof hatch controller
#
# All pin numbers, I2C addresses, channel assignments and timing constants
# are defined here. Adjust values to match actual hardware before deployment.

# -----------------------------------------------------------------------------
# I2C bus pins (XIAO ESP32-S3 on Seeed Expansion Board)
# -----------------------------------------------------------------------------
I2C_SDA_PIN = 5
I2C_SCL_PIN = 6
I2C_FREQ    = 400_000  # 400 kHz fast mode

# -----------------------------------------------------------------------------
# I2C device addresses
# -----------------------------------------------------------------------------
I2C_ADDR_OLED    = 0x3C  # SSD1306 OLED display
I2C_ADDR_PCA9685 = 0x7F  # PWM driver
I2C_ADDR_RELAY   = 0x11  # Grove 4-channel SPDT relay (default address)
I2C_ADDR_RTC     = 0x51  # PCF8563 RTC (on expansion board, reserved for later)

# -----------------------------------------------------------------------------
# User button (on Seeed Expansion Board)
# -----------------------------------------------------------------------------
BTN_USER_PIN = 2  

# Button timing
BTN_DEBOUNCE_MS       = 50   # debounce time in milliseconds
BTN_AP_MODE_HOLD_MS   = 0    # held at boot → AP mode (any duration, checked at startup)
BTN_CONFIG_UNLOCK_MS  = 60_000   # config page unlock window after button press (ms)
BTN_CONFIG_SESSION_MS = 3_600_000  # config session duration after page access (ms = 1 h)
BTN_LONG_PRESS_MS     = 3_000      # hold duration to trigger long-press action (ms)

# -----------------------------------------------------------------------------
# PCA9685 PWM driver
# -----------------------------------------------------------------------------
PCA9685_FREQ_HZ = 50  # standard servo frequency

# Servo pulse width range in microseconds.
# Extended range servos: adjust SERVO_MIN_US / SERVO_MAX_US to match your hardware.
# Standard:  1000–2000 µs
# Extended:  500–2500 µs (conservative default covering most extended-range servos)
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
FAILSAFE_US  = -1    # sentinel: actual or target position unknown (fail-safe state)

# PCA9685 channel assignments
# Each hatch uses two servos: hatch (movement) and brake (locking mechanism)
# Channel layout: hatch 0 = channels 0+1, hatch 1 = channels 2+3, etc.
PCA9685_CH = {
    # (hatch_index): (hatch_servo_channel, brake_servo_channel)
    0: (0, 1),
    1: (2, 3),
    2: (4, 5),
    3: (6, 7),
}

# -----------------------------------------------------------------------------
# Grove 4-channel relay
# Channel assignment: one relay per hatch, switches +8V to both servos of that hatch.
# NO contact used: relay energised → contact closed → servos powered.
# -----------------------------------------------------------------------------
RELAY_CH = {
    # hatch_index: relay_channel
    0: 0,
    1: 1,
    2: 2,
    3: 3,
}

# Fail-safe constants – used without config.json being readable
BRAKE_TRAVEL_MS        = 500   # time for brake servo to reach open position (ms)
BRAKE_OPEN_US_FAILSAFE = 1200  # brake open pulse width used in fail-safe sequence

# -----------------------------------------------------------------------------
# Number of hatches
# -----------------------------------------------------------------------------
NUM_HATCHES = 4
