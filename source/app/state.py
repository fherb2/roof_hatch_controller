"""
state.py – Configuration and runtime state management.

Handles loading, validation and persistence of:
  config.json – servo timing and position parameters (must always exist).
  state.json  – last known actual and target servo positions (created on
                first run if missing).

state.json schema (one entry per hatch):

    {
        "hatches": [
            {"hatch_us": 600, "brake_us": 800, "target_us": 600},
            ...
        ]
    }

  hatch_us  – last confirmed actual hatch-servo position in µs (Ist),
              or FAILSAFE_US (-1) when the position is unknown (e.g.
              power lost during a move, or after a fail-safe sequence).
  brake_us  – last confirmed actual brake-servo position in µs (Ist).
              Always a real µs value, never FAILSAFE_US.
  target_us – last commanded target position in µs (Soll), or
              FAILSAFE_US (-1) when the system is in passive fail-safe
              rest (Soll == Ist == FAILSAFE_US → task_control idles).
              Persisting the target makes an interrupted move
              recognisable after a reboot: task_control sees
              target_us ≠ hatch_us and resumes the move on next boot.

A state.json that is missing, corrupt or lacks target_us causes
load_state() to raise StateError.  main.py Stage 2b catches this and
calls create_default_state() + save_state() to generate a valid file –
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
from app.hw_config import NUM_HATCHES, SERVO_MIN_US, SERVO_MAX_US, FAILSAFE_US

CONFIG_PATH = "/config.json"
STATE_PATH  = "/state.json"

# Required keys checked during validation
_GLOBAL_REQUIRED = [
    "brake_open_us", "brake_close_us",
    "delay_after_brake_change_cmd_ms",
    "delay_after_move_ms",
]
_HATCH_REQUIRED = [
    "name", "short_name",
    "hatch_min_us", "hatch_max_us",
    "step_us", "step_ms",
    "positions",
]
_STATE_HATCH_REQUIRED = ["hatch_us", "brake_us", "target_us"]


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


def _check_us_range(value, lo, hi, label, error_class):
    """Verify that a microsecond value lies within [lo, hi].

    Raises error_class with a descriptive message if the value is outside
    the allowed servo pulse range.
    """
    if not lo <= value <= hi:
        raise error_class(
            f"{label} = {value} µs out of range [{lo}, {hi}]"
        )


def validate_config(data):
    """Validate a config dict without loading from file.

    Checks structure, required keys and all µs values for both the global
    settings and every hatch entry.  Raises ConfigError on the first
    problem found.  Called by load_config() and also directly by the web
    interface before saving a new configuration received via POST.

    Args:
        data: dict to validate (as received from POST /api/config).
    Raises:
        ConfigError: if any required key is missing, any µs value is out
                     of range, or the number of hatches does not match
                     NUM_HATCHES.
    """
    if "global" not in data:
        raise ConfigError("Missing top-level key 'global'")
    if "hatches" not in data:
        raise ConfigError("Missing top-level key 'hatches'")

    g = data["global"]
    _require_keys(g, _GLOBAL_REQUIRED, "global", ConfigError)
    _check_us_range(g["brake_open_us"],  SERVO_MIN_US, SERVO_MAX_US,
                    "global.brake_open_us",  ConfigError)
    _check_us_range(g["brake_close_us"], SERVO_MIN_US, SERVO_MAX_US,
                    "global.brake_close_us", ConfigError)
    for key in ["delay_after_brake_change_cmd_ms", "delay_after_move_ms"]:
        if g[key] < 0:
            raise ConfigError(f"global.{key} must be >= 0")

    hatches = data["hatches"]
    if len(hatches) != NUM_HATCHES:
        raise ConfigError(
            f"Expected {NUM_HATCHES} hatches, found {len(hatches)}"
        )
    for i, h in enumerate(hatches):
        ctx = f"hatches[{i}]"
        _require_keys(h, _HATCH_REQUIRED, ctx, ConfigError)
        _check_us_range(h["hatch_min_us"], SERVO_MIN_US, SERVO_MAX_US,
                        f"{ctx}.hatch_min_us", ConfigError)
        _check_us_range(h["hatch_max_us"], SERVO_MIN_US, SERVO_MAX_US,
                        f"{ctx}.hatch_max_us", ConfigError)
        if h["hatch_min_us"] >= h["hatch_max_us"]:
            raise ConfigError(f"{ctx}: hatch_min_us must be < hatch_max_us")
        if h["step_us"] <= 0:
            raise ConfigError(f"{ctx}.step_us must be > 0")
        if h["step_ms"] <= 0:
            raise ConfigError(f"{ctx}.step_ms must be > 0")
        if not h["positions"]:
            raise ConfigError(f"{ctx}: positions list is empty")
        for j, pos in enumerate(h["positions"]):
            if "hatch_us" not in pos:
                raise ConfigError(f"{ctx}.positions[{j}]: missing 'hatch_us'")
            _check_us_range(pos["hatch_us"],
                            h["hatch_min_us"], h["hatch_max_us"],
                            f"{ctx}.positions[{j}].hatch_us", ConfigError)


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

    Validates presence of all required fields (hatch_us, brake_us,
    target_us) and checks that every real µs value lies within the
    absolute hardware range [SERVO_MIN_US, SERVO_MAX_US].  hatch_us and
    target_us may be FAILSAFE_US (-1); in that case the range check is
    skipped for that field.  brake_us is always a real µs value and is
    always range-checked.

    A missing target_us field (old state.json format without Soll
    persistence) is caught as a missing-key StateError.  Callers should
    handle this by recreating the state via create_default_state().

    Returns:
        dict with key 'hatches', each entry containing 'hatch_us' (int),
        'brake_us' (int) and 'target_us' (int).
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
        if h["hatch_us"] != FAILSAFE_US:
            _check_us_range(h["hatch_us"],  SERVO_MIN_US, SERVO_MAX_US,
                            f"{ctx}.hatch_us",  StateError)
        _check_us_range(h["brake_us"],  SERVO_MIN_US, SERVO_MAX_US,
                        f"{ctx}.brake_us",  StateError)
        if h["target_us"] != FAILSAFE_US:
            _check_us_range(h["target_us"], SERVO_MIN_US, SERVO_MAX_US,
                            f"{ctx}.target_us", StateError)

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
    detects a missing, corrupt or outdated file.  All hatches start at
    hatch_min_us (fully closed) with the brake at brake_close_us.  The
    target is set equal to the initial actual position so that
    task_control does not immediately schedule a move on boot.

    Args:
        config: dict as returned by load_config().
    Returns:
        dict suitable for save_state() and direct use as runtime state.
    """
    brake_us = config["global"]["brake_close_us"]
    return {
        "hatches": [
            {
                "hatch_us":  h["hatch_min_us"],
                "brake_us":  brake_us,
                "target_us": h["hatch_min_us"],
            }
            for h in config["hatches"]
        ]
    }
