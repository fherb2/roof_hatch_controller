# task_display.py - Async task: OLED display updates
#
# Refreshes the display every _REFRESH_MS milliseconds.
#
# Display layout (128x64, 8 lines of 16 chars each):
#   Line 0–3: hatch status  "<short_name> <status>"
#   Line 4:   Wi-Fi status / IP address
#   Line 5:   config session hint (if active or unlock window open)
#   Line 6–7: reserved for future use

import uasyncio
import network
from app import session
from app.hw_config import FAILSAFE_DEG

# QR code support – uQR must be installed in /lib
try:
    from uQR import QRCode as _QRCode
    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False

_qr_cache_url    = None  # URL for which the matrix was generated
_qr_cache_matrix = None  # cached matrix to avoid regenerating on every refresh

_REFRESH_MS = 500  # display refresh interval in milliseconds


def _find_pos_idx(hatch_deg, positions):
    """Return the index of hatch_deg in the positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_deg"] == hatch_deg:
            return i
    return None


def _status_text(idx, state, targets, config):
    """Return a status string (max 9 chars) for hatch idx.

    "BEWEGT"  – target differs from actual (moving or fail-safe recovery
                in progress)
    "?ZU?"    – actual and target are both FAILSAFE_DEG: hatch fell closed
                by gravity after a fail-safe sequence, position unverified
                and unlocked
    "ZU"      – at first position (index 0, most closed)
    "AUF"     – at last position (most open)
    "P{n}"    – at intermediate position n (1-based, P2 … P(n-1))
    "???"     – actual position not found in positions list (should not
                occur in normal operation after fail-safe handling)
    """
    if targets[idx]["hatch_deg"] != state["hatches"][idx]["hatch_deg"]:
        return "BEWEGT"

    hatch_deg = state["hatches"][idx]["hatch_deg"]
    if hatch_deg == FAILSAFE_DEG:
        return "?ZU?"

    positions = config["hatches"][idx]["positions"]
    pos_idx   = _find_pos_idx(hatch_deg, positions)

    if pos_idx is None:
        return "???"
    if pos_idx == 0:
        return "ZU"
    if pos_idx == len(positions) - 1:
        return "AUF"
    return f"P{pos_idx + 1}"  # 1-based, P2 … P(n-1) for intermediate


def _wifi_line():
    """Return a 16-char Wi-Fi status string."""
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0][:16]          # IP address

    ap = network.WLAN(network.AP_IF)
    if ap.active():
        return ("AP:" + ap.ifconfig()[0])[:16] # AP mode IP

    return "Kein WLAN"


def _render_qr(display):
    """Render the current Wi-Fi IP as a QR code on the display.

    White background, black QR modules. Matrix is cached until the URL
    changes to avoid regenerating on every 500 ms refresh cycle.
    """
    global _qr_cache_url, _qr_cache_matrix

    if not _QR_AVAILABLE:
        display.fill(0)
        display.text("uQR not found", 0, 28)
        display.show()
        return

    # Determine current URL
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        url = "http://" + sta.ifconfig()[0]
    else:
        ap = network.WLAN(network.AP_IF)
        url = "http://" + (ap.ifconfig()[0] if ap.active() else "0.0.0.0")

    # Regenerate matrix only when URL changes
    if url != _qr_cache_url:
        qr = _QRCode()
        qr.add_data(url)
        _qr_cache_matrix = qr.get_matrix()
        _qr_cache_url    = url

    matrix = _qr_cache_matrix
    size   = len(matrix)
    QUIET  = 4  # quiet zone in pixels (required for scanners)

    offset_x = (128 - size - 2 * QUIET) // 2
    offset_y = (64  - size - 2 * QUIET) // 2

    display.fill(1)  # white background (quiet zone + white modules)
    for my in range(size):
        for mx in range(size):
            if matrix[my][mx]:  # True = black module
                x = offset_x + QUIET + mx
                y = offset_y + QUIET + my
                if 0 <= x < 128 and 0 <= y < 64:
                    display.pixel(x, y, 0)
    display.show()


def _refresh(display, config, state, targets):
    """Redraw the entire display."""
    display.fill(0)

    # Lines 0–3: one line per hatch
    for i in range(len(targets)):
        short  = config["hatches"][i]["short_name"][:6]
        status = _status_text(i, state, targets, config)
        # Format: "SSSSSS STATUS   " – exactly 16 chars
        line   = f"{short:<6} {status:<9}"
        display.text(line[:16], 0, i * 8)

    # Line 4: Wi-Fi status
    display.text(_wifi_line()[:16], 0, 32)

    # Line 5: config session / unlock hint
    if session.is_session_active():
        display.text("CFG AKTIV      ", 0, 40)
    elif session.is_unlock_active():
        display.text("CFG BEREIT     ", 0, 40)

    display.show()


async def task_display(hw, config, state, targets):
    """Periodically refresh the OLED display.

    Args:
        hw:      HardwareBundle (hw.display must be initialised)
        config:  config dict from load_config()
        state:   state dict (read-only in this task)
        targets: RAM-only setpoint dict (read-only in this task)
    """
    while True:
        try:
            if session.is_qr_active():
                _render_qr(hw.display)
            else:
                _refresh(hw.display, config, state, targets)
        except Exception as e:
            # Display errors must not crash the event loop
            print(f"Display error: {e}")
        await uasyncio.sleep_ms(_REFRESH_MS)
