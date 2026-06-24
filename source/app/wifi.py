# wifi.py - Wi-Fi management: client mode and AP configuration mode
#
# start_client_mode(): connect using credentials from wifi.json.
#   If wifi.json is missing or connection fails, logs the error and
#   returns without Wi-Fi. The rest of the application continues
#   (display and button still work; web interface unavailable).
#
# start_ap_mode(): start as open access point "Dachluke-Setup".
#   Returns once the AP is active. task_webserver handles the
#   configuration page; call save_wifi_config() on form submit.

import network
import ujson
import machine
import uasyncio

WIFI_CONFIG_PATH   = "/wifi.json"
_CONNECT_TIMEOUT_S = 15
_AP_SSID           = "Dachluke-Setup"


class WifiConfigError(Exception):
    """Raised when wifi.json is missing, corrupt or cannot be written."""
    pass


# ---------------------------------------------------------------------------
# wifi.json helpers (also called by task_webserver)
# ---------------------------------------------------------------------------

def load_wifi_config():
    """Load and return SSID + password from wifi.json.

    Returns:
        dict with keys 'ssid' and 'password'.
    Raises:
        WifiConfigError: if the file is missing, corrupt or incomplete.
    """
    try:
        with open(WIFI_CONFIG_PATH, "r") as f:
            data = ujson.load(f)
    except OSError:
        raise WifiConfigError("wifi.json not found")
    except ValueError:
        raise WifiConfigError("wifi.json parse error")

    if "ssid" not in data or "password" not in data:
        raise WifiConfigError("Missing 'ssid' or 'password' in wifi.json")
    return data


def save_wifi_config(ssid, password):
    """Save SSID and password to wifi.json, then reboot.

    Called by task_webserver after the user submits the AP config form.

    Args:
        ssid:     network name
        password: network password
    Raises:
        WifiConfigError: if the file cannot be written.
    """
    try:
        with open(WIFI_CONFIG_PATH, "w") as f:
            ujson.dump({"ssid": ssid, "password": password}, f)
    except OSError as e:
        raise WifiConfigError(f"Cannot write wifi.json: {e}")
    machine.reset()


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _display_line(hw, line, text):
    """Write one line to the display (if available) and to serial."""
    if hw.display is not None:
        hw.display.fill_rect(0, line * 8, 128, 8, 0)
        hw.display.text(text[:16], 0, line * 8)
        hw.display.show()
    print(f"[wifi] {text}")


# ---------------------------------------------------------------------------
# Public API called from main.py
# ---------------------------------------------------------------------------

async def start_client_mode(hw):
    """Connect to Wi-Fi using credentials from wifi.json.

    Polls for connection every 200 ms up to _CONNECT_TIMEOUT_S seconds.
    On success, shows the IP address on display line 4.
    On failure, shows an error message and returns without Wi-Fi.

    Args:
        hw: HardwareBundle (hw.display may be None)
    """
    try:
        cfg = load_wifi_config()
    except WifiConfigError as e:
        _display_line(hw, 4, "Kein wifi.json")
        print(f"[wifi] {e} – reboot with button held to configure")
        return

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(cfg["ssid"], cfg["password"])

    _display_line(hw, 4, "WiFi verbinden")

    import time
    deadline = time.time() + _CONNECT_TIMEOUT_S
    while not sta.isconnected():
        if time.time() > deadline:
            _display_line(hw, 4, "WiFi Timeout")
            print("[wifi] connection timeout – continuing without Wi-Fi")
            return
        await uasyncio.sleep_ms(200)

    ip = sta.ifconfig()[0]
    _display_line(hw, 4, ip)


async def start_ap_mode(hw):
    """Start as open Wi-Fi access point for network configuration.

    AP SSID : Dachluke-Setup  (open, no password)
    AP IP   : 192.168.4.1 (ESP32 default)

    Returns once the AP is active. task_webserver serves the
    configuration form; save_wifi_config() writes the credentials
    and resets the device.

    Args:
        hw: HardwareBundle (hw.display may be None)
    """
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(ssid=_AP_SSID, authmode=network.AUTH_OPEN)

    ip = ap.ifconfig()[0]
    _display_line(hw, 4, f"AP:{ip}"[:16])
    _display_line(hw, 5, _AP_SSID[:16])
