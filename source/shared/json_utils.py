"""
json_utils.py – JSON utility functions for MicroPython.

MicroPython's ujson.dump() does not support an indent parameter and
always produces single-line output.  This module provides pretty_json()
as a drop-in replacement for cases where human-readable output is needed
(e.g. config files that may be edited on a desktop).
"""

import ujson


def pretty_json(obj, _indent=0):
    """Return a human-readable, indented JSON string for obj.

    Handles dicts, lists, strings, booleans, None and numbers.
    String values are serialised via ujson.dumps() to ensure correct
    escaping of special characters.  Produces output compatible with
    standard JSON parsers.

    Args:
        obj:     The object to serialise (dict, list, str, int, float,
                 bool or None).
        _indent: Internal recursion depth; callers should not set this.
    Returns:
        str: Formatted JSON string without a trailing newline.
    """
    sp  = '    ' * _indent
    sp1 = '    ' * (_indent + 1)

    if isinstance(obj, dict):
        if not obj:
            return '{}'
        items = [
            f'{sp1}{ujson.dumps(k)}: {pretty_json(v, _indent + 1)}'
            for k, v in obj.items()
        ]
        return '{\n' + ',\n'.join(items) + '\n' + sp + '}'

    if isinstance(obj, list):
        if not obj:
            return '[]'
        items = [f'{sp1}{pretty_json(v, _indent + 1)}' for v in obj]
        return '[\n' + ',\n'.join(items) + '\n' + sp + ']'

    if isinstance(obj, bool):
        return 'true' if obj else 'false'

    if obj is None:
        return 'null'

    if isinstance(obj, str):
        return ujson.dumps(obj)

    return str(obj)
