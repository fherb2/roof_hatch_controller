"""
state.py - Configuration and runtime state management.

Handles loading, validation and persistence of:
  config.json - per-channel servo calibration, hard stops, speed, mounting
                angle and positions/brake angles (must always exist), all
                in degrees. Also holds the servo tester configuration.
  state.json  - last known actual and target servo positions (created on
                first run if missing), also in degrees.

config.json schema:

    {
        "global": {
            "servo_tester": {
                "pwm_freq_hz": 50,
                "hatch_servo": {
                    "range_deg": 270, "direction": "neg", "speed_deg_per_s": 30,
                    "pulse_min_us": 500, "pulse_max_us": 2500,
                    "hard_stop_min_deg": 0, "hard_stop_max_deg": 270,
                    "test_angle_a_deg": 0, "test_angle_b_deg": 90
                },
                "brake_servo": {
                    "range_deg": 180, "direction": "neg", "speed_deg_per_s": 30,
                    "pulse_min_us": 500, "pulse_max_us": 2500,
                    "hard_stop_min_deg": 0, "hard_stop_max_deg": 180,
                    "test_angle_a_deg": 10, "test_angle_b_deg": 170
                }
            }
        },
        "hatches": [
            {
                "name": "Luke 1",
                "short_name": "Luke1",
                "pwm_freq_hz": 50,
                "hatch_servo": {
                    "range_deg": 270, "direction": "neg", "speed_deg_per_s": 30,
                    "pulse_min_us": 500, "pulse_max_us": 2500,
                    "hard_stop_min_deg": 0, "hard_stop_max_deg": 270,
                    "mount_angle_deg": 0
                },
                "brake_servo": {
                    "range_deg": 180, "direction": "neg", "speed_deg_per_s": 30,
                    "pulse_min_us": 500, "pulse_max_us": 2500,
                    "hard_stop_min_deg": 0, "hard_stop_max_deg": 180,
                    "mount_angle_deg": 90, "open_deg": 10, "close_deg": 170
                },
                "positions": [{"hatch_deg": 0}, {"hatch_deg": 90}, {"hatch_deg": 260}]
            },
            ...
        ]
    }

Every hatch_servo/brake_servo parameter is per-channel (no shared/global
servo parameters anymore) - only global.servo_tester's own hatch_servo/
brake_servo blocks are shared, since the tester uses one fixed extra
channel pair (see hw_config.TESTER_CH) for bench-testing servos before
assembly, independent of any single hatch.

pwm_freq_hz is per hatch (shared by that hatch's own hatch_servo AND
brake_servo, since both are powered simultaneously by the same relay and
the PCA9685 has a single, chip-wide frequency register - see
task_control._ensure_pwm_freq()). global.servo_tester has its own single
shared pwm_freq_hz for both tester outputs, active whenever no hatch
channel is currently driving (see task_control module docstring).

mount_angle_deg (hatch_servo and brake_servo, per hatch) is a maintenance
angle independent of positions/open_deg/close_deg, used to bring a servo
to a convenient angle for physical assembly. It is NOT guaranteed to be
a holdable position under load - see task_control._move_hatch() and
_move_brake_only() docstrings.

positions[0] is always the closed end-stop ("ZU"), positions[-1] is
always the open end-stop ("AUF"); at least these two entries must be
present. Only hatch_servo has positions - brake_servo instead has a
fixed pair (open_deg/close_deg).

state.json schema:

    {
        "hatches": [
            {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0},
            ...
        ],
        "servo_tester": {"hatch_deg": 0, "brake_deg": 0}
    }

  hatch_deg  - last confirmed actual hatch-servo angle in degrees (Ist),
               or FAILSAFE_DEG (-1) when the position is unknown (e.g.
               power lost during a move, or after a fail-safe sequence).
  brake_deg  - last confirmed actual brake-servo angle in degrees (Ist).
               Always a real angle, never FAILSAFE_DEG.
  target_deg - last commanded target angle in degrees (Soll), or
               FAILSAFE_DEG (-1) when the system is in passive fail-safe
               rest (Soll == Ist == FAILSAFE_DEG -> task_control idles).
               Persisting the target makes an interrupted move
               recognisable after a reboot: task_control sees
               target_deg != hatch_deg and resumes the move on next boot.
  servo_tester.hatch_deg/brake_deg - last known actual angle of the two
               tester outputs, always real angles (no FAILSAFE_DEG, no
               target - the tester never moves automatically), kept only
               so a bench-test ramp resumes from the right angle after
               a reboot.

A state.json that is missing, corrupt or lacks any required field causes
load_state() to raise StateError. main.py Stage 2b catches this and
calls create_default_state() + save_state() to generate a valid file -
no explicit migration code is needed.

Recommended pattern in main.py:

    config = load_config()          # raises ConfigError if missing/invalid
    try:
        state = load_state()
    except StateError:
        state = create_default_state(config)
        save_state(state)
"""

import ujson
from app.hw_config import (
    NUM_HATCHES, SERVO_MIN_US, SERVO_MAX_US, SERVO_RANGE_DEG_MAX,
    RANGE_DEG_OPTIONS, SPEED_MIN_DEG_PER_S, SPEED_MAX_DEG_PER_S,
    PWM_FREQ_MIN_HZ, PWM_FREQ_MAX_HZ, FAILSAFE_DEG,
)

CONFIG_PATH = "/config.json"
STATE_PATH  = "/state.json"

_VALID_DIRECTIONS = ("pos", "neg")

# Required keys checked during validation
_SERVO_COMMON_REQUIRED = [
    "range_deg", "direction", "speed_deg_per_s",
    "pulse_min_us", "pulse_max_us",
    "hard_stop_min_deg", "hard_stop_max_deg",
]
_HATCH_SERVO_EXTRA   = ["mount_angle_deg"]
_BRAKE_SERVO_EXTRA   = ["mount_angle_deg", "open_deg", "close_deg"]
_TESTER_SERVO_EXTRA  = ["test_angle_a_deg", "test_angle_b_deg"]

_GLOBAL_REQUIRED      = ["servo_tester"]
_TESTER_TOP_REQUIRED  = ["pwm_freq_hz", "hatch_servo", "brake_servo"]
_HATCH_TOP_REQUIRED   = [
    "name", "short_name", "pwm_freq_hz", "hatch_servo", "brake_servo",
    "positions",
]

_STATE_HATCH_REQUIRED  = ["hatch_deg", "brake_deg", "target_deg"]
_STATE_TESTER_REQUIRED = ["hatch_deg", "brake_deg"]


class ConfigError(Exception):
    """Raised when config.json is missing, corrupt or fails validation."""
    pass


class StateError(Exception):
    """Raised when state.json is missing, corrupt or fails validation."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path, error_class):
    """Load and parse a JSON file, raising error_class on any failure.

    Distinguishes between a missing file (OSError) and invalid JSON
    (ValueError) so both result in the same typed exception for the
    caller to handle uniformly.
    """
    try:
        with open(path, "r") as f:
            return ujson.load(f)
    except OSError:
        raise error_class(f"File not found: {path}")
    except ValueError:
        raise error_class(f"JSON parse error in: {path}")


def _require_keys(d, keys, context, error_class):
    """Verify that all required keys are present in dict d.

    Raises error_class with a descriptive message on the first missing key.
    """
    for k in keys:
        if k not in d:
            raise error_class(f"Missing key '{k}' in {context}")


def _check_range(value, lo, hi, label, error_class):
    """Verify that a numeric value lies within [lo, hi].

    Raises error_class with a descriptive message if the value is outside
    the allowed range.
    """
    if not lo <= value <= hi:
        raise error_class(f"{label} = {value} out of range [{lo}, {hi}]")


def _validate_servo_block(block, context, extra_keys, error_class):
    """Validate one hatch_servo/brake_servo config block.

    Checks the fields common to every servo (range_deg, direction, speed,
    pulse width, hard stops) plus presence of the caller-supplied extra
    fields (e.g. mount_angle_deg, open_deg/close_deg, test_angle_*_deg) -
    but does NOT range-check those extra fields against the hard stops;
    callers do that themselves once both this block and its extra values
    are known, since the valid range depends on hard_stop_min/max_deg
    which live in this same block.
    """
    _require_keys(block, _SERVO_COMMON_REQUIRED + extra_keys, context, error_class)

    if block["range_deg"] not in RANGE_DEG_OPTIONS:
        raise error_class(
            f"{context}.range_deg = {block['range_deg']} must be one of "
            f"{RANGE_DEG_OPTIONS}"
        )
    if block["direction"] not in _VALID_DIRECTIONS:
        raise error_class(
            f"{context}.direction = {block['direction']!r} must be one of "
            f"{_VALID_DIRECTIONS}"
        )
    _check_range(block["speed_deg_per_s"], SPEED_MIN_DEG_PER_S, SPEED_MAX_DEG_PER_S,
                 f"{context}.speed_deg_per_s", error_class)
    _check_range(block["pulse_min_us"], SERVO_MIN_US, SERVO_MAX_US,
                 f"{context}.pulse_min_us", error_class)
    _check_range(block["pulse_max_us"], SERVO_MIN_US, SERVO_MAX_US,
                 f"{context}.pulse_max_us", error_class)
    if block["pulse_min_us"] >= block["pulse_max_us"]:
        raise error_class(f"{context}: pulse_min_us must be < pulse_max_us")
    if block["hard_stop_min_deg"] >= block["hard_stop_max_deg"]:
        raise error_class(
            f"{context}: hard_stop_min_deg must be < hard_stop_max_deg"
        )
    _check_range(block["hard_stop_min_deg"], 0, block["range_deg"],
                 f"{context}.hard_stop_min_deg", error_class)
    _check_range(block["hard_stop_max_deg"], 0, block["range_deg"],
                 f"{context}.hard_stop_max_deg", error_class)


def _check_within_hard_stops(block, field, context, error_class):
    """Range-check block[field] against block's own hard stops.

    Shared by mount_angle_deg, open_deg/close_deg and test_angle_*_deg -
    all of these are angles that must lie within the same servo's own
    [hard_stop_min_deg, hard_stop_max_deg].
    """
    _check_range(block[field], block["hard_stop_min_deg"], block["hard_stop_max_deg"],
                 f"{context}.{field}", error_class)


def validate_config(data):
    """Validate a config dict without loading from file.

    Checks structure, required keys and all values for the servo tester
    and every hatch entry. Raises ConfigError on the first problem found.
    Called by load_config() and also directly by the web interface before
    persisting a config change received via POST.

    Args:
        data: dict to validate (as received from POST /api/config).
    Raises:
        ConfigError: if any required key is missing, any value is out of
                     range, or the number of hatches does not match
                     NUM_HATCHES.
    """
    if "global" not in data:
        raise ConfigError("Missing top-level key 'global'")
    if "hatches" not in data:
        raise ConfigError("Missing top-level key 'hatches'")

    g = data["global"]
    _require_keys(g, _GLOBAL_REQUIRED, "global", ConfigError)

    st = g["servo_tester"]
    _require_keys(st, _TESTER_TOP_REQUIRED, "global.servo_tester", ConfigError)
    _check_range(st["pwm_freq_hz"], PWM_FREQ_MIN_HZ, PWM_FREQ_MAX_HZ,
                 "global.servo_tester.pwm_freq_hz", ConfigError)
    for servo_key in ("hatch_servo", "brake_servo"):
        ctx   = f"global.servo_tester.{servo_key}"
        block = st[servo_key]
        _validate_servo_block(block, ctx, _TESTER_SERVO_EXTRA, ConfigError)
        _check_within_hard_stops(block, "test_angle_a_deg", ctx, ConfigError)
        _check_within_hard_stops(block, "test_angle_b_deg", ctx, ConfigError)

    hatches = data["hatches"]
    if len(hatches) != NUM_HATCHES:
        raise ConfigError(
            f"Expected {NUM_HATCHES} hatches, found {len(hatches)}"
        )
    for i, h in enumerate(hatches):
        ctx = f"hatches[{i}]"
        _require_keys(h, _HATCH_TOP_REQUIRED, ctx, ConfigError)
        _check_range(h["pwm_freq_hz"], PWM_FREQ_MIN_HZ, PWM_FREQ_MAX_HZ,
                     f"{ctx}.pwm_freq_hz", ConfigError)

        hs = h["hatch_servo"]
        hctx = f"{ctx}.hatch_servo"
        _validate_servo_block(hs, hctx, _HATCH_SERVO_EXTRA, ConfigError)
        _check_within_hard_stops(hs, "mount_angle_deg", hctx, ConfigError)

        bs = h["brake_servo"]
        bctx = f"{ctx}.brake_servo"
        _validate_servo_block(bs, bctx, _BRAKE_SERVO_EXTRA, ConfigError)
        _check_within_hard_stops(bs, "mount_angle_deg", bctx, ConfigError)
        _check_within_hard_stops(bs, "open_deg", bctx, ConfigError)
        _check_within_hard_stops(bs, "close_deg", bctx, ConfigError)

        positions = h["positions"]
        if len(positions) < 2:
            raise ConfigError(
                f"{ctx}: positions must contain at least 2 entries "
                f"(ZU and AUF)"
            )
        for j, pos in enumerate(positions):
            if "hatch_deg" not in pos:
                raise ConfigError(f"{ctx}.positions[{j}]: missing 'hatch_deg'")
            _check_range(pos["hatch_deg"],
                         hs["hard_stop_min_deg"], hs["hard_stop_max_deg"],
                         f"{ctx}.positions[{j}].hatch_deg", ConfigError)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config():
    """Load config.json, validate it and return the config dict.

    Combines file I/O and full structural validation in one step so
    callers receive a single, typed exception on any problem.

    Returns:
        dict with keys 'global' (dict) and 'hatches' (list of dicts).
    Raises:
        ConfigError: if the file is missing, corrupt or fails validation.
    """
    data = _load_json(CONFIG_PATH, ConfigError)

    if "global" not in data:
        raise ConfigError("Missing top-level key 'global' in config.json")
    if "hatches" not in data:
        raise ConfigError("Missing top-level key 'hatches' in config.json")

    validate_config(data)
    return data


def load_state():
    """Load state.json, validate it and return the state dict.

    Validates presence of all required fields for every hatch and for the
    servo tester, and checks that every real degree value lies within the
    absolute sanity bound [0, SERVO_RANGE_DEG_MAX]. This bound is
    independent of the currently configured range_deg (180/270/360) so
    that load_state() does not need config.json to already be loaded.
    hatch_deg and target_deg may be FAILSAFE_DEG (-1); in that case the
    range check is skipped for that field. brake_deg and the servo_tester
    angles are always real and always range-checked.

    A missing required field (old state.json format from before this
    schema) is caught as a missing-key StateError. Callers should handle
    this by recreating the state via create_default_state().

    Returns:
        dict with keys 'hatches' (list of dicts, each with 'hatch_deg',
        'brake_deg', 'target_deg') and 'servo_tester' (dict with
        'hatch_deg', 'brake_deg').
    Raises:
        StateError: if the file is missing, corrupt, has the wrong number
                    of hatch entries or fails any field validation.
    """
    data = _load_json(STATE_PATH, StateError)

    if "hatches" not in data:
        raise StateError("Missing top-level key 'hatches' in state.json")
    if "servo_tester" not in data:
        raise StateError("Missing top-level key 'servo_tester' in state.json")

    hatches = data["hatches"]
    if len(hatches) != NUM_HATCHES:
        raise StateError(
            f"Expected {NUM_HATCHES} hatches in state.json, "
            f"found {len(hatches)}"
        )

    for i, h in enumerate(hatches):
        ctx = f"state.hatches[{i}]"
        _require_keys(h, _STATE_HATCH_REQUIRED, ctx, StateError)
        if h["hatch_deg"] != FAILSAFE_DEG:
            _check_range(h["hatch_deg"], 0, SERVO_RANGE_DEG_MAX,
                         f"{ctx}.hatch_deg", StateError)
        _check_range(h["brake_deg"], 0, SERVO_RANGE_DEG_MAX,
                     f"{ctx}.brake_deg", StateError)
        if h["target_deg"] != FAILSAFE_DEG:
            _check_range(h["target_deg"], 0, SERVO_RANGE_DEG_MAX,
                         f"{ctx}.target_deg", StateError)

    tester = data["servo_tester"]
    _require_keys(tester, _STATE_TESTER_REQUIRED, "state.servo_tester", StateError)
    _check_range(tester["hatch_deg"], 0, SERVO_RANGE_DEG_MAX,
                 "state.servo_tester.hatch_deg", StateError)
    _check_range(tester["brake_deg"], 0, SERVO_RANGE_DEG_MAX,
                 "state.servo_tester.brake_deg", StateError)

    return data


def save_state(state):
    """Persist the state dict to state.json.

    Called after every target change (by the web interface) and after
    every completed move (by task_control) so that both the Soll and the
    Ist position are always up to date on flash.

    Args:
        state: dict as returned by load_state() or create_default_state().
    Raises:
        StateError: if the file cannot be written (e.g. flash full).
    """
    try:
        with open(STATE_PATH, "w") as f:
            ujson.dump(state, f)
    except OSError as e:
        raise StateError(f"Failed to write state.json: {e}")


def create_default_state(config):
    """Build a safe initial state dict from config values.

    Used on first boot (no state.json yet) and whenever load_state()
    detects a missing, corrupt or outdated file. All hatches start at
    their hatch_servo's hard_stop_min_deg (fully closed) with the brake
    at that hatch's own brake_servo.close_deg. The target is set equal to
    the initial actual position so that task_control does not immediately
    schedule a move on boot. The servo tester starts at both its servos'
    own hard_stop_min_deg (safe, arbitrary starting point - the tester has
    no "closed" concept of its own).

    Args:
        config: dict as returned by load_config().
    Returns:
        dict suitable for save_state() and direct use as runtime state.
    """
    hatches_state = []
    for h in config["hatches"]:
        closed_deg = h["hatch_servo"]["hard_stop_min_deg"]
        hatches_state.append({
            "hatch_deg":  closed_deg,
            "brake_deg":  h["brake_servo"]["close_deg"],
            "target_deg": closed_deg,
        })

    st = config["global"]["servo_tester"]
    return {
        "hatches": hatches_state,
        "servo_tester": {
            "hatch_deg": st["hatch_servo"]["hard_stop_min_deg"],
            "brake_deg": st["brake_servo"]["hard_stop_min_deg"],
        },
    }
