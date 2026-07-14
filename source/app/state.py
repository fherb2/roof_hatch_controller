"""
state.py - Configuration and runtime state management.

Handles loading, validation and persistence of:
  config.json - servo calibration, hard stops, speed and per-hatch
                positions/brake angles (must always exist), all in degrees.
  state.json  - last known actual and target servo positions (created on
                first run if missing), also in degrees.

config.json schema:

    {
        "global": {
            "hatch_servo": {
                "range_deg": 270,
                "pulse_min_us": 500,
                "pulse_max_us": 2500,
                "hard_stop_min_deg": 0,
                "hard_stop_max_deg": 270,
                "speed_deg_per_s": 30
            },
            "brake_servo": {
                "range_deg": 180,
                "pulse_min_us": 500,
                "pulse_max_us": 2500
            }
        },
        "hatches": [
            {
                "name": "Luke 1",
                "short_name": "Luke1",
                "brake_open_deg": 10,
                "brake_close_deg": 170,
                "positions": [{"hatch_deg": 0}, {"hatch_deg": 90}, {"hatch_deg": 260}]
            },
            ...
        ]
    }

hatch_servo / brake_servo parameters apply identically to all NUM_HATCHES
hatches - only brake_open_deg, brake_close_deg and positions are per-hatch.
positions[0] is always the closed end-stop ("ZU"), positions[-1] is always
the open end-stop ("AUF"); at least these two entries must be present.

state.json schema (one entry per hatch):

    {
        "hatches": [
            {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0},
            ...
        ]
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

A state.json that is missing, corrupt or lacks target_deg causes
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
    FAILSAFE_DEG,
)

CONFIG_PATH = "/config.json"
STATE_PATH  = "/state.json"

_VALID_RANGE_DEG = (180, 270)

# Required keys checked during validation
_GLOBAL_REQUIRED             = ["hatch_servo", "brake_servo"]
_HATCH_SERVO_REQUIRED        = [
    "range_deg", "pulse_min_us", "pulse_max_us",
    "hard_stop_min_deg", "hard_stop_max_deg", "speed_deg_per_s",
]
_BRAKE_SERVO_REQUIRED        = ["range_deg", "pulse_min_us", "pulse_max_us"]
_HATCH_REQUIRED              = [
    "name", "short_name", "brake_open_deg", "brake_close_deg", "positions",
]
_STATE_HATCH_REQUIRED = ["hatch_deg", "brake_deg", "target_deg"]


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


def _validate_servo_block(block, context, error_class):
    """Validate the shared structure of a hatch_servo/brake_servo block.

    Checks range_deg is one of the two allowed values and that
    pulse_min_us < pulse_max_us, both within the absolute hardware
    envelope [SERVO_MIN_US, SERVO_MAX_US].
    """
    if block["range_deg"] not in _VALID_RANGE_DEG:
        raise error_class(
            f"{context}.range_deg = {block['range_deg']} must be one of "
            f"{_VALID_RANGE_DEG}"
        )
    _check_range(block["pulse_min_us"], SERVO_MIN_US, SERVO_MAX_US,
                 f"{context}.pulse_min_us", error_class)
    _check_range(block["pulse_max_us"], SERVO_MIN_US, SERVO_MAX_US,
                 f"{context}.pulse_max_us", error_class)
    if block["pulse_min_us"] >= block["pulse_max_us"]:
        raise error_class(f"{context}: pulse_min_us must be < pulse_max_us")


def validate_config(data):
    """Validate a config dict without loading from file.

    Checks structure, required keys and all values for the global servo
    blocks and every hatch entry. Raises ConfigError on the first problem
    found. Called by load_config() and also directly by the web interface
    before persisting a config change received via POST.

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

    hs = g["hatch_servo"]
    _require_keys(hs, _HATCH_SERVO_REQUIRED, "global.hatch_servo", ConfigError)
    _validate_servo_block(hs, "global.hatch_servo", ConfigError)
    if hs["hard_stop_min_deg"] >= hs["hard_stop_max_deg"]:
        raise ConfigError(
            "global.hatch_servo: hard_stop_min_deg must be < hard_stop_max_deg"
        )
    _check_range(hs["hard_stop_min_deg"], 0, hs["range_deg"],
                 "global.hatch_servo.hard_stop_min_deg", ConfigError)
    _check_range(hs["hard_stop_max_deg"], 0, hs["range_deg"],
                 "global.hatch_servo.hard_stop_max_deg", ConfigError)
    if hs["speed_deg_per_s"] <= 0:
        raise ConfigError("global.hatch_servo.speed_deg_per_s must be > 0")

    bs = g["brake_servo"]
    _require_keys(bs, _BRAKE_SERVO_REQUIRED, "global.brake_servo", ConfigError)
    _validate_servo_block(bs, "global.brake_servo", ConfigError)

    hatches = data["hatches"]
    if len(hatches) != NUM_HATCHES:
        raise ConfigError(
            f"Expected {NUM_HATCHES} hatches, found {len(hatches)}"
        )
    for i, h in enumerate(hatches):
        ctx = f"hatches[{i}]"
        _require_keys(h, _HATCH_REQUIRED, ctx, ConfigError)
        _check_range(h["brake_open_deg"], 0, bs["range_deg"],
                     f"{ctx}.brake_open_deg", ConfigError)
        _check_range(h["brake_close_deg"], 0, bs["range_deg"],
                     f"{ctx}.brake_close_deg", ConfigError)
        if len(h["positions"]) < 2:
            raise ConfigError(
                f"{ctx}: positions must contain at least 2 entries "
                f"(ZU and AUF)"
            )
        for j, pos in enumerate(h["positions"]):
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

    Validates presence of all required fields (hatch_deg, brake_deg,
    target_deg) and checks that every real degree value lies within the
    absolute sanity bound [0, SERVO_RANGE_DEG_MAX]. This bound is
    independent of the currently configured range_deg (180 or 270) so
    that load_state() does not need config.json to already be loaded.
    hatch_deg and target_deg may be FAILSAFE_DEG (-1); in that case the
    range check is skipped for that field. brake_deg is always a real
    angle and is always range-checked.

    A missing target_deg field (old state.json format without Soll
    persistence, or the pre-degree µs-based format) is caught as a
    missing-key StateError. Callers should handle this by recreating the
    state via create_default_state().

    Returns:
        dict with key 'hatches', each entry containing 'hatch_deg' (int),
        'brake_deg' (int) and 'target_deg' (int).
    Raises:
        StateError: if the file is missing, corrupt, has the wrong number
                    of hatch entries or fails any field validation.
    """
    data = _load_json(STATE_PATH, StateError)

    if "hatches" not in data:
        raise StateError("Missing top-level key 'hatches' in state.json")

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
    hard_stop_min_deg (fully closed) with the brake at that hatch's own
    brake_close_deg. The target is set equal to the initial actual
    position so that task_control does not immediately schedule a move
    on boot.

    Args:
        config: dict as returned by load_config().
    Returns:
        dict suitable for save_state() and direct use as runtime state.
    """
    closed_deg = config["global"]["hatch_servo"]["hard_stop_min_deg"]
    return {
        "hatches": [
            {
                "hatch_deg":  closed_deg,
                "brake_deg":  h["brake_close_deg"],
                "target_deg": closed_deg,
            }
            for h in config["hatches"]
        ]
    }
