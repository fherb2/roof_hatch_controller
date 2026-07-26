# servo_units.py - Degree <-> microsecond conversion for servo pulse widths
#
# Pure math, no hardware imports - usable everywhere (app code, tests).
#
# A servo is described by four calibration values:
#   pulse_min_us, pulse_max_us  - pulse width range in microseconds
#   range_deg                    - total mechanical travel in degrees (180/270/360)
#   direction                    - "pos" or "neg" (see below)
#
# direction is now a per-servo config value (config.json field, default
# "neg"), not a fixed global constant: "neg" means increasing pulse width
# corresponds to decreasing degrees (0° maps to pulse_max_us); "pos" means
# the opposite (0° maps to pulse_min_us).


def deg_to_us(deg, pulse_min_us, pulse_max_us, range_deg, direction):
    """Convert an angle in degrees to a servo pulse width in microseconds.

    Args:
        deg:          angle in degrees, expected within [0, range_deg].
        pulse_min_us: pulse width at the low end of the servo's travel.
        pulse_max_us: pulse width at the high end of the servo's travel.
        range_deg:    total mechanical travel in degrees (180/270/360).
        direction:    "pos" (0° -> pulse_min_us) or "neg" (0° -> pulse_max_us).
    Returns:
        int pulse width in microseconds.
    """
    us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
    if direction == "neg":
        return round(pulse_max_us - deg * us_per_deg)
    return round(pulse_min_us + deg * us_per_deg)


def us_to_deg(us, pulse_min_us, pulse_max_us, range_deg, direction):
    """Convert a servo pulse width in microseconds to an angle in degrees.

    Inverse of deg_to_us(). Returns a float; callers that need whole
    degrees (e.g. before persisting to state.json) must round explicitly.

    Args:
        us:           pulse width in microseconds.
        pulse_min_us: pulse width at the low end of the servo's travel.
        pulse_max_us: pulse width at the high end of the servo's travel.
        range_deg:    total mechanical travel in degrees (180/270/360).
        direction:    "pos" (0° -> pulse_min_us) or "neg" (0° -> pulse_max_us).
    Returns:
        float angle in degrees.
    """
    us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
    if direction == "neg":
        return (pulse_max_us - us) / us_per_deg
    return (us - pulse_min_us) / us_per_deg
