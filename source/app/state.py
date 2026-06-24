# state.py - Configuration and runtime state management
#
# Handles loading, validation and persistence of:
#   config.json – servo parameters, positions, timing (must always exist)
#   state.json  – last known servo positions (created on first run if missing)
#
# On first run, state.json does not exist. Recommended pattern in main.py:
#
#   config = load_config()          # raises ConfigError if missing/invalid
#   try:
#       state = load_state()
#   except StateError:
#       state = create_default_state(config)
#       save_state(state)

import ujson
from app.hw_config import NUM_HATCHES, SERVO_MIN_US, SERVO_MAX_US

CONFIG_PATH = "/config.json"
STATE_PATH  = "/state.json"

# Required keys checked during validation
_GLOBAL_REQUIRED = [
    "brake_open_us", "brake_close_us",
    "delay_after_power_on_ms", "delay_after_brake_open_ms",
    "delay_after_move_ms",     "delay_after_brake_close_ms",
]
_HATCH_REQUIRED = [
    "name", "short_name",
    "hatch_min_us", "hatch_max_us",
    "step_us", "step_ms",
    "positions",
]
_STATE_HATCH_REQUIRED = ["hatch_us", "brake_us"]


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
    """Load and parse a JSON file.

    Raises error_class if the file is missing or contains invalid JSON.
    """
    try:
        with open(path, "r") as f:
            return ujson.load(f)
    except OSError:
        raise error_class(f"File not found: {path}")
    except ValueError:
        raise error_class(f"JSON parse error in: {path}")


def _require_keys(d, keys, context, error_class):
    """Verify all required keys are present in dict d."""
    for k in keys:
        if k not in d:
            raise error_class(f"Missing key '{k}' in {context}")


def _check_us_range(value, lo, hi, label, error_class):
    """Verify a microsecond value is within [lo, hi]."""
    if not lo <= value <= hi:
        raise error_class(
            f"{label} = {value} µs out of range [{lo}, {hi}]"
        )
    
def validate_config(data):
    """Validate a config dict without loading from file.

    Same rules as load_config(). Raises ConfigError on any problem.
    Call this before saving a new config from the web interface.

    Args:
        data: dict to validate (as received from POST /api/config)
    Raises:
        ConfigError: if validation fails.
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
    for key in ["delay_after_power_on_ms", "delay_after_brake_open_ms",
                "delay_after_move_ms",     "delay_after_brake_close_ms"]:
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
    """Load and validate config.json.

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
    """Load and validate state.json.

    Returns:
        dict with key 'hatches', each entry containing
        'hatch_us' (int) and 'brake_us' (int).
    Raises:
        StateError: if the file is missing, corrupt or fails validation.

    On first run, state.json does not yet exist. Catch StateError and call
    create_default_state(config) to generate and persist a safe initial state.
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
        _check_us_range(h["hatch_us"], SERVO_MIN_US, SERVO_MAX_US,
                        f"{ctx}.hatch_us", StateError)
        _check_us_range(h["brake_us"], SERVO_MIN_US, SERVO_MAX_US,
                        f"{ctx}.brake_us", StateError)

    return data


def save_state(state):
    """Persist state dict to state.json.

    Args:
        state: dict as returned by load_state() or create_default_state().
    Raises:
        StateError: if the file cannot be written.
    """
    try:
        with open(STATE_PATH, "w") as f:
            ujson.dump(state, f)
    except OSError as e:
        raise StateError(f"Failed to write state.json: {e}")


def create_default_state(config):
    """Create a safe initial state from config values.

    All hatches are placed at hatch_min_us (fully closed) with the brake
    at brake_close_us. Call save_state() afterwards to persist.

    Args:
        config: dict as returned by load_config().
    Returns:
        dict suitable for save_state() and use as runtime state.
    """
    brake_us = config["global"]["brake_close_us"]
    return {
        "hatches": [
            {
                "hatch_us": h["hatch_min_us"],
                "brake_us": brake_us,
            }
            for h in config["hatches"]
        ]
    }
