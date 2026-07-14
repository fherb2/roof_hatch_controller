# servo_units.py - Degree <-> microsecond conversion for servo pulse widths
#
# Pure math, no hardware imports - usable everywhere (app code, tests).
#
# A servo is described by three calibration values:
#   pulse_min_us, pulse_max_us  - pulse width range in microseconds
#   range_deg                    - total mechanical travel in degrees (180 or 270)
#
# SERVO_DIRECTION_REVERSED is a single, fixed, global switch (not a per-servo
# config value): both the hatch (lever) servo and the brake servo are wired
# so that increasing pulse width corresponds to decreasing degrees. If a
# servo is ever rewired the other way round, flip this one constant - it
# applies uniformly to every servo in the system.

SERVO_DIRECTION_REVERSED = True


def deg_to_us(deg, pulse_min_us, pulse_max_us, range_deg):
    """Convert an angle in degrees to a servo pulse width in microseconds.

    Args:
        deg:          angle in degrees, expected within [0, range_deg].
        pulse_min_us: pulse width at the low end of the servo's travel.
        pulse_max_us: pulse width at the high end of the servo's travel.
        range_deg:    total mechanical travel in degrees (180 or 270).
    Returns:
        int pulse width in microseconds.
    """
    us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
    if SERVO_DIRECTION_REVERSED:
        return round(pulse_max_us - deg * us_per_deg)
    return round(pulse_min_us + deg * us_per_deg)


def us_to_deg(us, pulse_min_us, pulse_max_us, range_deg):
    """Convert a servo pulse width in microseconds to an angle in degrees.

    Inverse of deg_to_us(). Returns a float; callers that need whole
    degrees (e.g. before persisting to state.json) must round explicitly.

    Args:
        us:           pulse width in microseconds.
        pulse_min_us: pulse width at the low end of the servo's travel.
        pulse_max_us: pulse width at the high end of the servo's travel.
        range_deg:    total mechanical travel in degrees (180 or 270).
    Returns:
        float angle in degrees.
    """
    us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
    if SERVO_DIRECTION_REVERSED:
        return (pulse_max_us - us) / us_per_deg
    return (us - pulse_min_us) / us_per_deg
