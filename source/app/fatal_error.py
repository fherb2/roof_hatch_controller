"""
fatal_error.py – Centralised fatal error handler for unrecoverable boot failures.

Replaces the previous pattern of returning early from main(), which caused an
uncontrolled WDT reboot loop.  Instead this handler leaves the hardware in a
safe state, shows the error permanently on the display and serves a minimal
error page on port 80 forever.  A power-cycle is the only way out.

Sequence:
  1. Attempt a fail-safe sequence (open brakes, cut servo power) if the
     hardware bundle is available.  Failure is logged and silently ignored
     so that the remaining steps always run.
  2. Write a permanent error screen to the OLED display, if available.
     The display is updated once and never touched again.
  3. Start a minimal Wi-Fi access point (SSID: Dachluke-Setup, open) so
     the error page is reachable without a station connection.
  4. Serve a single-page HTML error report on port 80 and loop forever.
"""

import uasyncio
import network
from microdot import Microdot, Response

_AP_SSID = "Dachluke-Setup"

# Double braces {{ }} in the CSS escape the format() call; {msg} is replaced.
_ERROR_HTML = """\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Systemfehler</title>
<style>
  body{{font-family:sans-serif;padding:2em;background:#fff}}
  h2{{color:#c00}}
  pre{{background:#f4f4f4;padding:1em;border-radius:4px;
       white-space:pre-wrap;word-break:break-word}}
</style>
</head>
<body>
<h2>&#9888; Systemfehler</h2>
<pre>{msg}</pre>
<p>Bitte Fehler beheben und Ger&#228;t neu starten (Power-Cycle).</p>
</body>
</html>
"""


def _show_on_display(display, message):
    """Write a permanent error screen to the OLED.

    Renders a fixed header on line 0 and splits the error message across
    the remaining seven lines (16 characters per line, 112 characters
    total).  Called once; the display is not updated again after this.
    """
    try:
        display.fill(0)
        display.text("!! FEHLER !!", 0, 0, 1)
        for i in range(7):
            chunk = message[i * 16 : (i + 1) * 16]
            if not chunk:
                break
            display.text(chunk, 0, (i + 1) * 8, 1)
        display.show()
    except Exception as e:
        print(f"fatal_error: display update failed: {e}")


def _start_ap():
    """Activate the Wi-Fi access point so the error page is reachable.

    Uses the same SSID as normal AP mode ('Dachluke-Setup') so the user
    does not need to look up a different network name.  The station
    interface is deactivated to avoid interference.  Failure is logged
    but does not prevent the webserver from starting.
    """
    try:
        network.WLAN(network.STA_IF).active(False)
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        ap.config(ssid=_AP_SSID, authmode=network.AUTH_OPEN)
        print(f"fatal_error: AP '{_AP_SSID}' active, IP {ap.ifconfig()[0]}")
    except Exception as e:
        print(f"fatal_error: AP start failed: {e}")


async def fatal_error(message, hw=None, display=None):
    """Handle an unrecoverable boot error.

    Runs the safety sequence described in the module docstring.  Never
    returns; the event loop stays alive (feeding no WDT) serving the
    error page until the device is power-cycled.

    Args:
        message: human-readable error description (shown on display and
                 in the web page).
        hw:      HardwareBundle if hardware was already initialised;
                 None skips the fail-safe sequence.
        display: SSD1306 display object if the display was initialised;
                 None skips the display update.
    """
    print(f"FATAL ERROR: {message}")

    # 1. Fail-safe: open brakes and cut servo power
    if hw is not None:
        try:
            from app.failsafe import fail_safe_close_all
            fail_safe_close_all(hw)
        except Exception as e:
            print(f"fatal_error: fail-safe sequence failed: {e}")

    # 2. Permanent error screen on OLED
    if display is not None:
        _show_on_display(display, message)

    # 3. AP so the error page is reachable
    _start_ap()

    # 4. Minimal webserver – loops forever
    error_html = _ERROR_HTML.format(msg=message)
    error_app  = Microdot()

    @error_app.route('/')
    async def error_page(request):
        """Serve the static error page."""
        return Response(
            body=error_html,
            headers={'Content-Type': 'text/html; charset=utf-8'},
        )

    @error_app.route('/<path:path>')
    async def catch_all(request, path):
        """Redirect all other paths to the error page."""
        return Response(status_code=302, headers={'Location': '/'})

    await error_app.start_server(port=80, debug=False)
