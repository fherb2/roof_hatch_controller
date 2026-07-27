"""
config_migration.py – Ensure config.json is present and up to date.

Called once during boot (main.py Stage 2b) before load_config(), so that
load_config() is guaranteed to find a valid, fully-populated file.

Migration rules:
  Template missing              → raise ConfigMigrationError (fatal).
  config.json missing           → copy template verbatim.
  config.json present and valid → no action (load_config() will succeed).
  config.json present but invalid or with missing/renamed fields → merge:
      For each key in the template, the existing value is kept if present;
      otherwise the template value is used. Keys in the existing config
      that are absent from the template are silently dropped – this is
      how old or renamed parameters (e.g. the flat hatch_min_us/step_us
      fields from an earlier config.json schema, now replaced by the
      nested hatch_servo/brake_servo blocks) are removed without manual
      intervention.
      The merge is applied recursively for nested dicts.
      The hatches list is always processed as exactly four entries; each
      hatch dict is merged individually against its template counterpart.
      The positions list inside each hatch is kept verbatim from the
      existing config if non-empty (user-calibrated values); the template
      positions are used otherwise.
"""

import ujson
from app.state import validate_config, ConfigError

CONFIG_PATH   = "/config.json"
TEMPLATE_PATH = "/config.json.template"


class ConfigMigrationError(Exception):
    """Raised when the config template is missing or unreadable."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    """Load and parse a JSON file.

    Returns the parsed object, or None on any read or parse error so
    callers can handle missing/corrupt files without exception handling.
    """
    try:
        with open(path, "r") as f:
            return ujson.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    """Persist data as JSON to path.

    Raises ConfigMigrationError if the file cannot be written (e.g. flash
    full or path not writable).
    """
    try:
        with open(path, "w") as f:
            ujson.dump(data, f)
    except OSError as e:
        raise ConfigMigrationError(f"Cannot write {path}: {e}")


def _merge_dicts(existing, template):
    """Recursively merge existing values into the template structure.

    For each key in template:
      - Both values are dicts  → recurse into _merge_dicts.
      - Both values are lists  → delegate to _merge_lists for
                                 context-aware handling.
      - Key present in existing with a scalar → use existing value.
      - Key absent in existing               → use template value.
    Keys present in existing but absent in template are silently dropped,
    which removes old or renamed config parameters automatically.
    """
    result = {}
    for key, tval in template.items():
        if key not in existing:
            result[key] = tval
        elif isinstance(tval, dict) and isinstance(existing[key], dict):
            result[key] = _merge_dicts(existing[key], tval)
        elif isinstance(tval, list) and isinstance(existing[key], list):
            result[key] = _merge_lists(key, existing[key], tval)
        else:
            result[key] = existing[key]
    return result


def _merge_lists(key, existing_list, template_list):
    """Merge a list field using context-specific rules.

    hatches:   Always produces exactly four entries.  Each hatch dict is
               merged against its counterpart in the template (index 0 is
               used as a fallback for any missing template entry).
    positions: Kept verbatim from existing if non-empty (user-calibrated
               servo angles); template positions are used otherwise.
    Others:    Existing list is returned unchanged.
    """
    if key == "hatches":
        fallback = template_list[0] if template_list else {}
        return [
            _merge_dicts(
                existing_list[i] if i < len(existing_list) else {},
                template_list[i] if i < len(template_list) else fallback,
            )
            for i in range(4)
        ]

    if key == "positions":
        return existing_list if existing_list else template_list

    return existing_list


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def migrate_config():
    """Ensure config.json exists and contains all required fields.

    Reads the template, inspects the current config.json (if any) and
    writes an updated or copied file as needed.  After this function
    returns without raising, config.json is guaranteed to contain every
    field defined in the template.

    Raises:
        ConfigMigrationError: if the template file is missing or
                              unreadable.  This is a fatal condition –
                              the device cannot operate without it.
    """
    template = _read_json(TEMPLATE_PATH)
    if template is None:
        raise ConfigMigrationError(
            f"Config template not found or unreadable: {TEMPLATE_PATH}"
        )

    existing = _read_json(CONFIG_PATH)
    if existing is None:
        # First deployment or corrupt file – install template verbatim
        print("config_migration: config.json missing, installing template")
        _write_json(CONFIG_PATH, template)
        return

    try:
        validate_config(existing)
        return  # already valid and complete, nothing to do
    except ConfigError:
        pass

    # Existing config is outdated or has missing/renamed fields – merge
    print("config_migration: config.json outdated or invalid, merging with template")
    _write_json(CONFIG_PATH, _merge_dicts(existing, template))
