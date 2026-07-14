# task_display.py - Async task: OLED display updates
#
# Refreshes the display every _REFRESH_MS milliseconds.
#
# Normal display layout (128x64, 4 hatch status lines using a larger
# 14px-high monospace font via the Writer class - see lib/writer.py and
# lib/font_hatch_status.py):
#   Line 0-3: hatch status "<short_name> <status>", 16px pitch each,
#             filling the full 64px height. No Wi-Fi/IP line and no
#             config-session hint are shown here anymore - the Wi-Fi
#             status is only relevant when scanning the QR code (see
#             below), and the config session state is now shown
#             immediately and exclusively on the web page itself.
#
# QR-code layout (shown for 15s after a short button press):
#   Top 48px:    QR code (scale 1px/module, 4px quiet zone)
#   Bottom 16px: IP address as plain text, standard 8px built-in font

import uasyncio
import network
from app import session
from app.hw_config import FAILSAFE_DEG
from writer import Writer
import font_hatch_status

# QR code support - uQR must be installed in /lib
try:
    from uQR import QRCode as _QRCode
    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False

_qr_cache_url    = None  # URL for which the matrix was generated
_qr_cache_matrix = None  # cached matrix to avoid regenerating on every refresh

_REFRESH_MS     = 500  # display refresh interval in milliseconds
_LINE_HEIGHT_PX = 16   # px per hatch status line; 4 lines exactly fill 64px
_IP_LINE_PX     = 16   # px reserved at the bottom of the QR screen for the IP text


def _find_pos_idx(hatch_deg, positions):
    """Return the index of hatch_deg in the positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_deg"] == hatch_deg:
            return i
    return None


def _status_text(idx, state, targets, config):
    """Return a status string (max 9 chars) for hatch idx.

    "BEWEGT"  - target differs from actual (moving or fail-safe recovery
                in progress)
    "?ZU?"    - actual and target are both FAILSAFE_DEG: hatch fell closed
                by gravity after a fail-safe sequence, position unverified
                and unlocked
    "ZU"      - at first position (index 0, most closed)
    "AUF"     - at last position (most open)
    "P{n}"    - at intermediate position n (1-based, P2 ... P(n-1))
    "???"     - actual position not found in positions list (should not
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
    return f"P{pos_idx + 1}"  # 1-based, P2 ... P(n-1) for intermediate


def _current_ip():
    """Return the current IP address as a plain string, or None if no
    Wi-Fi connection is active (neither client mode nor AP mode)."""
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return sta.ifconfig()[0]

    ap = network.WLAN(network.AP_IF)
    if ap.active():
        return ap.ifconfig()[0]

    return None


def _render_qr(display):
    """Render the current Wi-Fi IP as a QR code, with the IP address as
    plain text underneath.

    White background, black QR modules. The QR code is confined to the
    top 48px so the bottom _IP_LINE_PX px remain free for the IP address
    text. Matrix is cached until the URL changes to avoid regenerating
    on every 500 ms refresh cycle.
    """
    global _qr_cache_url, _qr_cache_matrix

    if not _QR_AVAILABLE:
        display.fill(0)
        display.text("uQR not found", 0, 28)
        display.show()
        return

    ip       = _current_ip()
    url      = "http://" + (ip if ip else "0.0.0.0")
    ip_text  = ip if ip else "Kein WLAN"
    qr_area_height = 64 - _IP_LINE_PX

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
    offset_y = (qr_area_height - size - 2 * QUIET) // 2

    display.fill(1)  # white background (quiet zone + white modules)
    for my in range(size):
        for mx in range(size):
            if matrix[my][mx]:  # True = black module
                x = offset_x + QUIET + mx
                y = offset_y + QUIET + my
                if 0 <= x < 128 and 0 <= y < qr_area_height:
                    display.pixel(x, y, 0)

    # IP address line beneath the QR code, standard built-in 8px font,
    # black text on the white background, horizontally centred
    text_x = max(0, (128 - len(ip_text) * 8) // 2)
    text_y = qr_area_height + (_IP_LINE_PX - 8) // 2
    display.text(ip_text, text_x, text_y, 0)

    display.show()


def _refresh(wri, config, state, targets):
    """Redraw the entire display: one larger-font status line per hatch.

    Args:
        wri: Writer instance bound to the display and font_hatch_status,
             created once in task_display() and reused on every refresh.
    """
    wri.device.fill(0)

    for i in range(len(targets)):
        short  = config["hatches"][i]["short_name"][:6]
        status = _status_text(i, state, targets, config)
        line   = f"{short:<6} {status}"
        Writer.set_textpos(wri.device, i * _LINE_HEIGHT_PX, 0)
        wri.printstring(line)

    wri.device.show()


async def task_display(hw, config, state, targets):
    """Periodically refresh the OLED display.

    Args:
        hw:      HardwareBundle (hw.display must be initialised)
        config:  config dict from load_config()
        state:   state dict (read-only in this task)
        targets: RAM-only setpoint dict (read-only in this task)
    """
    wri = Writer(hw.display, font_hatch_status, verbose=False)
    wri.set_clip(row_clip=True, col_clip=True, wrap=False)

    while True:
        try:
            if session.is_qr_active():
                _render_qr(hw.display)
            else:
                _refresh(wri, config, state, targets)
        except Exception as e:
            # Display errors must not crash the event loop
            print(f"Display error: {e}")
        await uasyncio.sleep_ms(_REFRESH_MS)
