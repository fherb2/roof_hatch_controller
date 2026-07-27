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
# While a hatch is actually moving, its status field DOES change every
# cycle (the rotating spinner) - the fingerprint-skip above can't help
# there. To still avoid a full 1024-byte screen transfer for a 4-character
# animation, _refresh() additionally does a PARTIAL, windowed redraw
# whenever only the trailing status field of one row changed (see
# _redraw_status_region()/_show_region()): the SSD1306 controller lets
# the column/page address window be restricted to an arbitrary rectangle
# via SET_COL_ADDR (0x21) / SET_PAGE_ADDR (0x22) - the standard ssd1306
# driver's show() just never uses this, it always addresses the full
# 128x64 area. For a 4-character, 16px-tall field that means transferring
# 64 bytes instead of 1024, at the cost of a handful of extra 2-byte
# command writes. This bypasses ssd1306.SSD1306_I2C.show() and talks to
# its write_cmd()/write_data() primitives directly, and assumes the
# standard page-major (MONO_VLSB) buffer layout
# (display.buffer[page * display.width + x]) that the common
# micropython-lib ssd1306 driver uses - if a different driver version is
# ever used, _show_region() would need re-checking.
#
# _last_drawn holds a fingerprint of whatever is currently actually shown
# on the display (including which of the two screens - status or QR - is
# active); _last_lines additionally caches the exact 4 status lines last
# drawn, so a per-row comparison can tell a spinner-only change apart from
# a change that needs a full redraw (short_name edited, session state
# toggled, or switching in from the QR screen).
#
# Normal display layout (128x64, 4 hatch status lines using a larger
# 14px-high monospace font via the Writer class - see lib/writer.py and
# lib/font_hatch_status.py):
#   Line 0-3: "<short_name:11> <status:4>", 16px pitch each (2 pages of
#             8px each per line), filling the full 64px height. The
#             status field always occupies columns 96-127 (the last 4 of
#             16 characters at 8px/character) - this fixed position is
#             what _redraw_status_region() relies on.
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
_last_lines = None  # the 4 status line strings behind the last "status"
                     # fingerprint, for the per-row partial-redraw check;
                     # meaningless whenever _last_drawn isn't a "status" one

_REFRESH_MS     = 500  # display refresh interval in milliseconds
_LINE_HEIGHT_PX = 16   # px per hatch status line; 4 lines exactly fill 64px
_IP_LINE_PX     = 16   # px reserved at the bottom of the QR screen for the IP text
_STATUS_COL_PX  = 12 * 8  # column where the 4-char status field starts
                           # (11 short_name chars + 1 space, 8px/char)

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


def _show_region(display, x0, x1, page0, page1):
    """Write only a rectangular sub-region of the framebuffer to the
    SSD1306 panel, instead of display.show()'s full 128x64 transfer.

    Uses the controller's column/page address window commands directly
    (0x21 SET_COL_ADDR, 0x22 SET_PAGE_ADDR) via the driver's own
    write_cmd()/write_data() primitives, then extracts just the affected
    bytes from the driver's framebuffer (page-major MONO_VLSB layout:
    one byte per column covers 8 vertical pixels, buffer index =
    page * display.width + x). See module docstring for why this exists
    and what it depends on.

    Args:
        display: SSD1306_I2C instance (must expose .write_cmd(),
                 .write_data(), .buffer, .width).
        x0, x1:  inclusive column range (0-127).
        page0, page1: inclusive page range (0-7 for a 64px-tall panel).
    """
    display.write_cmd(0x21)
    display.write_cmd(x0)
    display.write_cmd(x1)
    display.write_cmd(0x22)
    display.write_cmd(page0)
    display.write_cmd(page1)

    width = x1 - x0 + 1
    data  = bytearray(width * (page1 - page0 + 1))
    idx   = 0
    for page in range(page0, page1 + 1):
        start = page * display.width + x0
        data[idx:idx + width] = display.buffer[start:start + width]
        idx += width
    display.write_data(data)


def _redraw_status_region(wri, row, status_text):
    """Redraw only the 4-character status field of one hatch row.

    Clears just that 32x16px cell (to avoid ghosting between differently
    shaped glyphs, e.g. spinner frames), draws the new text into the
    framebuffer at that position, then transfers only that rectangle via
    _show_region() rather than the whole screen.

    Args:
        wri:         Writer instance bound to the display.
        row:         hatch index (0-3), also the display row.
        status_text: the 4-character status string to draw.
    """
    display = wri.device
    x0, x1  = _STATUS_COL_PX, 127
    page0   = row * (_LINE_HEIGHT_PX // 8)
    page1   = page0 + (_LINE_HEIGHT_PX // 8) - 1

    display.fill_rect(x0, row * _LINE_HEIGHT_PX, x1 - x0 + 1, _LINE_HEIGHT_PX, 0)
    Writer.set_textpos(display, row * _LINE_HEIGHT_PX, x0)
    wri.printstring(status_text)
    _show_region(display, x0, x1, page0, page1)


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
    """Redraw the status lines - but only what actually changed.

    Three cases, cheapest first:
      1. Nothing at all changed (fingerprint match) - do nothing.
      2. We were already showing the status view at the same inversion
         state, and only the trailing 4-character status field of one or
         more rows changed (the common case while a hatch is moving -
         the spinner, or a ZU/AUF/P.. swap at the end of a move) - redraw
         just those small regions via _redraw_status_region().
      3. Anything else changed (short_name edited, session state
         toggled, or the previous screen was the QR code) - full redraw,
         same as before.

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
    global _spin_frame, _last_drawn, _last_lines

    config_reachable = session.is_session_active() or session.is_unlock_active()

    lines = []
    for i in range(len(targets["hatches"])):
        short  = config["hatches"][i]["short_name"][:11]
        status = _status_text(i, state, targets, config, _spin_frame)
        lines.append(f"{short:<11} {status}")

    fingerprint = ("status", config_reachable, tuple(lines))
    if fingerprint == _last_drawn:
        return

    display = wri.device
    # Was the previous draw the status view at the same inversion state?
    # If not (previous was QR, or this is the very first draw, or the
    # session state just changed), a full redraw is required regardless
    # of how the individual lines compare.
    can_do_partial = (
        _last_drawn is not None
        and _last_drawn[0] == "status"
        and _last_drawn[1] == config_reachable
        and _last_lines is not None
    )

    if not can_do_partial:
        display.invert(config_reachable)
        display.fill(0)
        for i, line in enumerate(lines):
            Writer.set_textpos(display, i * _LINE_HEIGHT_PX, 0)
            wri.printstring(line)
        display.show()
    else:
        need_full_redraw = False
        rows_to_patch = []
        for i, line in enumerate(lines):
            if line == _last_lines[i]:
                continue
            if line[:_STATUS_COL_PX // 8] == _last_lines[i][:_STATUS_COL_PX // 8]:
                rows_to_patch.append(i)
            else:
                need_full_redraw = True
                break

        if need_full_redraw:
            display.fill(0)
            for i, line in enumerate(lines):
                Writer.set_textpos(display, i * _LINE_HEIGHT_PX, 0)
                wri.printstring(line)
            display.show()
        else:
            for i in rows_to_patch:
                _redraw_status_region(wri, i, lines[i][_STATUS_COL_PX // 8:])

    _last_drawn = fingerprint
    _last_lines = lines
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
