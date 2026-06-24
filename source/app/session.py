# session.py - Shared config session state
#
# Written by task_button (unlock_config) and task_webserver (activate_session).
# Read by task_webserver to gate access to the configuration page.

import time

_UNLOCK_DURATION_S  = 60    # seconds after button press
_SESSION_DURATION_S = 3600  # seconds after config page first access (1 hour)

_unlock_until  = 0  # end of 60s unlock window (time.time() epoch seconds)
_session_until = 0  # end of 1h session


def unlock_config():
    """Start the 60s unlock window. Called by task_button on button press."""
    global _unlock_until
    _unlock_until = time.time() + _UNLOCK_DURATION_S


def activate_session():
    """Start 1h config session. Called by task_webserver on config page access
    within the unlock window."""
    global _session_until
    _session_until = time.time() + _SESSION_DURATION_S


def is_unlock_active():
    """Return True if within the 60s window after button press."""
    return time.time() < _unlock_until


def is_session_active():
    """Return True if within the active 1h config session."""
    return time.time() < _session_until