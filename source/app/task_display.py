# task_display.py - Async task: OLED display updates
#
# Checks every _REFRESH_MS milliseconds whether the display content needs
# to change, and only touches the I2C bus (fill/draw/show) when it
# actually does. This matters for more than just efficiency: task_display
# shares the I2C bus and the single-threaded event loop with task_control,
# which drives the servos. A full redraw (framebuffer fill + Writer glyph
# rendering + the final I2C transfer in show()) runs with no await in
# between, blocking the event loop for its whole duration. Measured
# impact: a redraw every cycle - which used to happen unconditionally,
# even while nothing on screen was actually changing - periodically
# starved task_control's servo ramp of its scheduled wake-up, producing a
# real, physical speed wobble at the servo (most visible with the servo
# tester, since the 4 hatch status lines never change while it is used).
# The same unconditional redraw also caused a visible flicker on the QR
# screen (moire banding when filming it with a phone camera at scanning
# distance), from repeatedly blanking and rebuilding the same QR code.
#
# _last_drawn holds a fingerprint of whatever is currently actually shown
# on the display (including which of the two screens - status or QR - is
# active), and both _refresh() and _render_qr() skip their I2C work
# entirely when the fingerprint they would draw matches it. Because the
# fingerprint includes the screen mode, switching between status and QR
# always forces a redraw even if the two screens coincidentally examine
# unchanged data - it forces a redraw purely because content actually
# visible on the panel is different from what's on screen right now.
#
# Normal display layout (128x64, 4 hatch status lines using a larger
# 14px-high monospace font via the Writer class - see lib/writer.py and
# lib/font_hatch_status.py):
#   Line 0-3: hatch status "<short_name> <status>", 16px pitch each,
#             filling the full 64px height.
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

_last_drawn = None  # fingerprint of whatever is currently physically shown
                     # on the display - see module docstring

_REFRESH_MS     = 500  # display refresh interval in milliseconds
_LINE_HEIGHT_PX = 16   # px per hatch status line; 4 lines exactly fill 64px
_IP_LINE_PX     = 16   # px reserved at the bottom of the QR screen for the IP text

_SPIN_FRAMES = ["|", "/", "-", "\\"]  # rotating-stroke animation while moving


def _find_pos_idx(hatch_deg, positions):
    """Return the index of hatch_deg in the positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_deg"] == hatch_deg:
            return i
    return None


def _status_text(idx, state, targets, config, spin_frame=0):
    """Return a fixed 4-character status string for hatch idx.

    " |  "/" /  "/" -  "/" \\  " (spin_frame 0-3) - target differs from
                actual (moving or fail-safe recovery in progress);
                rotates one frame per redraw for a simple "in progress"
                animation instead of a long static word
    "?ZU?"    - actual and target are both FAILSAFE_DEG: hatch fell closed
                by gravity after a fail-safe sequence, position unverified
                and unlocked
    "ZU  "    - at first position (index 0, most closed)
    "AUF "    - at last position (most open)
    "P{n}  "  - at intermediate position n (1-based, P2 ... P(n-1))
    "??? "    - actual position not found in positions list (should not
                occur in normal operation after fail-safe handling)
    """
    if targets["hatches"][idx]["hatch_deg"] != state["hatches"][idx]["hatch_deg"]:
        return " " + _SPIN_FRAMES[spin_frame % len(_SPIN_FRAMES)] + "  "

    hatch_deg = state["hatches"][idx]["hatch_deg"]
    if hatch_deg == FAILSAFE_DEG:
        return "?ZU?"

    positions = config["hatches"][idx]["positions"]
    pos_idx   = _find_pos_idx(hatch_deg, positions)

    if pos_idx is None:
        return "??? "
    if pos_idx == 0:
        return "ZU  "
    if pos_idx == len(positions) - 1:
        return "AUF "
    label = f"P{pos_idx + 1}"
    return label + " " * (4 - len(label))  # 1-based, P2 ... P(n-1) for intermediate


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
    plain text underneath - but only if the display isn't already
    showing exactly that (see module docstring for why).

    White background, black QR modules. The QR code is confined to the
    top 48px so the bottom _IP_LINE_PX px remain free for the IP address
    text. Matrix is cached until the URL changes to avoid regenerating
    on every redraw.
    """
    global _qr_cache_url, _qr_cache_matrix, _last_drawn

    if not _QR_AVAILABLE:
        fingerprint = ("qr_unavailable",)
        if fingerprint == _last_drawn:
            return
        display.fill(0)
        display.text("uQR not found", 0, 28)
        display.show()
        _last_drawn = fingerprint
        return

    ip      = _current_ip()
    url     = "http://" + (ip if ip else "0.0.0.0")
    ip_text = ip if ip else "Kein WLAN"

    fingerprint = ("qr", url)
    if fingerprint == _last_drawn:
        return

    # QR mode always draws its own black-on-white pixels directly and
    # must not be affected by the config-session inversion used in the
    # status view.
    display.invert(False)

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
    _last_drawn = fingerprint


def _refresh(wri, config, state, targets):
    """Redraw the status lines - but only if the display isn't already
    showing exactly that (see module docstring for why).

    Inverted (negative) display indicates that the config session is
    reachable right now - either the 60s unlock window is open or the
    1h session is already active. This is the only on-device indicator
    of that state. config_reachable is part of the fingerprint precisely
    so that a session-state change alone (with the status text otherwise
    unchanged) still forces a redraw.

    Args:
        wri: Writer instance bound to the display and font_hatch_status,
             created once in task_display() and reused on every refresh.
    """
    global _spin_frame, _last_drawn

    config_reachable = session.is_session_active() or session.is_unlock_active()

    lines = []
    for i in range(len(targets["hatches"])):
        short  = config["hatches"][i]["short_name"][:11]
        status = _status_text(i, state, targets, config, _spin_frame)
        lines.append(f"{short:<11} {status}")

    fingerprint = ("status", config_reachable, tuple(lines))
    if fingerprint == _last_drawn:
        return

    wri.device.invert(config_reachable)
    wri.device.fill(0)
    for i, line in enumerate(lines):
        Writer.set_textpos(wri.device, i * _LINE_HEIGHT_PX, 0)
        wri.printstring(line)
    wri.device.show()

    _last_drawn = fingerprint
    # Only advance the spinner once a redraw actually happened - it is
    # part of "lines" above, so a moving hatch always produces a fresh
    # fingerprint and therefore a redraw every cycle, same as before.
    _spin_frame = (_spin_frame + 1) % len(_SPIN_FRAMES)


_spin_frame = 0  # module-level counter, advances by one every redraw
                 # while at least one hatch is moving


async def task_display(hw, config, state, targets):
    """Periodically check whether the OLED needs to be redrawn.

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
