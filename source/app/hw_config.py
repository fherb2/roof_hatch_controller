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
# Boot-time default frequency, set once in hw_init.py before any config is
# loaded. From then on, task_control reprograms the chip's single PRE_SCALE
# register to each hatch's/the tester's own pwm_freq_hz immediately before
# driving it - see task_control._ensure_pwm_freq(). The PCA9685 has exactly
# one frequency register for all 16 channels; per-channel frequency is not
# possible in hardware.
PCA9685_FREQ_HZ = 50

# Servo pulse width range in microseconds. Absolute hardware envelope -
# config.json's per-servo pulse_min_us/pulse_max_us must lie within this,
# regardless of servo type. Covers standard (1000-2000µs), extended-range
# (500-2500µs) and some long-throw servos (up to 4000µs).
SERVO_MIN_US = 500
SERVO_MAX_US = 4000

# Allowed values for config.json's per-servo range_deg (mechanical travel).
RANGE_DEG_OPTIONS   = (180, 270, 360)
SERVO_RANGE_DEG_MAX = 360  # widest allowed range_deg; used as an absolute
                            # sanity bound when validating degree values in
                            # state.json independent of config.json

# Allowed values for config.json's per-servo speed_deg_per_s.
SPEED_MIN_DEG_PER_S = 30
SPEED_MAX_DEG_PER_S = 180

# Allowed values for config.json's per-channel/tester pwm_freq_hz.
PWM_FREQ_MIN_HZ = 50
PWM_FREQ_MAX_HZ = 400

FAILSAFE_DEG = -1    # sentinel: actual or target position unknown (fail-safe state)

MIN_STEP_MS = 15  # practical minimum ramp step interval in ms, for both the
                    # hatch and the brake servo ramp; must reliably exceed
                    # the real-world uasyncio scheduling + I2C write overhead
                    # (multiple concurrent tasks), otherwise the actual speed
                    # falls far short of the configured speed_deg_per_s. The
                    # step size in µs is derived from this floor at move time
                    # (see task_control._compute_step()), not fixed.

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

# Servo tester: next free channel pair after the 4 hatches, permanently
# powered (no relay), used to test servos on the bench before assembly.
TESTER_CH = (8, 9)  # (hatch_servo_channel, brake_servo_channel)

# -----------------------------------------------------------------------------
# Grove 4-channel relay
# Channel assignment: one relay per hatch, switches +8V to both servos of that hatch.
# NO contact used: relay energised → contact closed → servos powered.
# The servo tester (TESTER_CH) has no relay - it is permanently powered by
# design, since it is only ever used with servos removed from the hatches.
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
