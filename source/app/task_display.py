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

_REFRESH_MS = 500  # display refresh interval in milliseconds


def _find_pos_idx(hatch_us, positions):
    """Return the index of hatch_us in the positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_us"] == hatch_us:
            return i
    return None


def _status_text(idx, state, targets, config):
    """Return a status string (max 9 chars) for hatch idx.

    "BEWEGT"  – target differs from actual (hatch is moving)
    "ZU"      – at first position (index 0, most closed)
    "AUF"     – at last position (most open)
    "P{n}"    – at intermediate position n (1-based)
    "???"     – actual position not found in positions list
    """
    if targets[idx]["hatch_us"] != state["hatches"][idx]["hatch_us"]:
        return "BEWEGT"

    hatch_us  = state["hatches"][idx]["hatch_us"]
    positions = config["hatches"][idx]["positions"]
    pos_idx   = _find_pos_idx(hatch_us, positions)

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
            _refresh(hw.display, config, state, targets)
        except Exception as e:
            # Display errors must not crash the event loop
            print(f"Display error: {e}")
        await uasyncio.sleep_ms(_REFRESH_MS)
