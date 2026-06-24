# task_webserver.py - Async task: microdot web server
#
# Routes:
#   GET  /                    → index.html  (main control page)
#   GET  /config              → config.html (requires active session)
#   GET  /ap                  → ap_setup.html
#   GET  /static/<path>       → static files (CSS, JS)
#   GET  /sse                 → SSE live status stream
#   GET  /api/config          → current config as JSON
#   POST /api/hatch/<i>/pos   → set hatch target by position index
#   POST /api/hatch/<i>/test  → test move to exact µs (session required)
#   POST /api/config          → validate + save new config, then restart
#   POST /api/wifi            → save Wi-Fi credentials, then restart
#   GET  /api/wifi/scan       → scan available SSIDs, return JSON list

import uasyncio
import ujson
import machine
import network
from microdot import Microdot, Response, send_file

from app import session
from app.hw_config import NUM_HATCHES
from app.state import validate_config, ConfigError, save_state
from app.wifi import save_wifi_config, WifiConfigError

app = Microdot()

# Module-level references set once by task_webserver() before app starts.
# Route handlers cannot take extra parameters in microdot, so we use
# module globals to share application state.
_hw      = None
_config  = None
_state   = None
_targets = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _json(data, status=200):
    """Return a JSON response."""
    return Response(
        body=ujson.dumps(data),
        status_code=status,
        headers={'Content-Type': 'application/json'},
    )


def _find_pos_idx(hatch_us, positions):
    """Return 0-based index of hatch_us in positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_us"] == hatch_us:
            return i
    return None


def _build_status():
    """Build the SSE status payload sent to all clients every 500 ms."""
    hatches = []
    for i in range(NUM_HATCHES):
        s         = _state["hatches"][i]
        t         = _targets[i]
        positions = _config["hatches"][i]["positions"]
        hatches.append({
            "name":          _config["hatches"][i]["name"],
            "short_name":    _config["hatches"][i]["short_name"],
            "current_us":    s["hatch_us"],
            "target_us":     t["hatch_us"],
            "moving":        t["hatch_us"] != s["hatch_us"],
            "pos_idx":       _find_pos_idx(s["hatch_us"], positions),
            "num_positions": len(positions),
        })

    sta = network.WLAN(network.STA_IF)
    return {
        "hatches": hatches,
        "wifi": {
            "connected": sta.isconnected(),
            "ip":        sta.ifconfig()[0] if sta.isconnected() else "",
        },
        "session": {
            "active": session.is_session_active(),
            "unlock": session.is_unlock_active(),
        },
    }


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

@app.route('/static/<path:path>')
async def static_files(request, path):
    return send_file(f'/webpages/static/{path}')


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route('/')
async def index(request):
    return send_file('/webpages/index.html')


@app.route('/config')
async def config_page(request):
    """Serve config.html. Activates the 1h session if unlock window is open."""
    if not session.is_session_active() and not session.is_unlock_active():
        return _json({'error': 'not authorized'}, 403)
    session.activate_session()
    return send_file('/webpages/config.html')


@app.route('/ap')
async def ap_page(request):
    return send_file('/webpages/ap_setup.html')


# ---------------------------------------------------------------------------
# SSE: live status stream
# ---------------------------------------------------------------------------

@app.route('/sse')
async def sse(request):
    """Server-Sent Events stream: pushes status JSON every 500 ms."""
    async def stream():
        while True:
            data = ujson.dumps(_build_status())
            yield f'data: {data}\n\n'.encode()
            await uasyncio.sleep_ms(500)

    return Response(
        body=stream(),
        headers={
            'Content-Type':      'text/event-stream',
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ---------------------------------------------------------------------------
# API: hatch control
# ---------------------------------------------------------------------------

@app.route('/api/hatch/<idx>/pos', methods=['POST'])
async def set_position(request, idx):
    """Set hatch target by position index (0-based)."""
    idx = int(idx)
    if not 0 <= idx < NUM_HATCHES:
        return _json({'error': 'invalid hatch index'}, 400)

    body = request.json
    if body is None or 'position_idx' not in body:
        return _json({'error': 'missing position_idx'}, 400)

    pos_idx   = int(body['position_idx'])
    positions = _config["hatches"][idx]["positions"]
    if not 0 <= pos_idx < len(positions):
        return _json({'error': 'invalid position_idx'}, 400)

    _targets[idx]["hatch_us"] = positions[pos_idx]["hatch_us"]
    return _json({'ok': True})


@app.route('/api/hatch/<idx>/test', methods=['POST'])
async def test_position(request, idx):
    """Move hatch to exact µs value for visual calibration.

    Requires an active config session. The move goes through the full
    sequence (brake open → hatch move → brake close) via task_control.
    """
    if not session.is_session_active():
        return _json({'error': 'not authorized'}, 403)

    idx = int(idx)
    if not 0 <= idx < NUM_HATCHES:
        return _json({'error': 'invalid hatch index'}, 400)

    body = request.json
    if body is None or 'hatch_us' not in body:
        return _json({'error': 'missing hatch_us'}, 400)

    hatch_us = int(body['hatch_us'])
    h        = _config["hatches"][idx]
    if not h["hatch_min_us"] <= hatch_us <= h["hatch_max_us"]:
        return _json({'error': 'hatch_us outside hardstop range'}, 400)

    _targets[idx]["hatch_us"] = hatch_us
    return _json({'ok': True})


# ---------------------------------------------------------------------------
# API: configuration
# ---------------------------------------------------------------------------

@app.route('/api/config')
async def get_config(request):
    """Return current configuration as JSON."""
    return _json(_config)


@app.route('/api/config', methods=['POST'])
async def post_config(request):
    """Validate and save new configuration, then restart.

    Requires active session. Full validation runs before writing.
    On success, machine.reset() is called and the response is never sent.
    """
    if not session.is_session_active():
        return _json({'error': 'not authorized'}, 403)

    new_cfg = request.json
    if new_cfg is None:
        return _json({'error': 'invalid JSON body'}, 400)

    try:
        validate_config(new_cfg)
    except ConfigError as e:
        return _json({'error': str(e)}, 400)

    try:
        with open('/config.json', 'w') as f:
            ujson.dump(new_cfg, f)
    except OSError as e:
        return _json({'error': f'write failed: {e}'}, 500)

    machine.reset()


# ---------------------------------------------------------------------------
# API: Wi-Fi
# ---------------------------------------------------------------------------

@app.route('/api/wifi', methods=['POST'])
async def post_wifi(request):
    """Save Wi-Fi credentials and restart. Used from ap_setup.html."""
    body = request.json
    if body is None or 'ssid' not in body or 'password' not in body:
        return _json({'error': 'missing ssid or password'}, 400)

    try:
        save_wifi_config(body['ssid'], body['password'])
    except WifiConfigError as e:
        return _json({'error': str(e)}, 500)


@app.route('/api/wifi/scan')
async def wifi_scan(request):
    """Scan for available Wi-Fi networks. Returns list sorted by signal strength.

    The scan() call blocks for ~1-2 seconds. This is within the WDT timeout.
    """
    sta = network.WLAN(network.STA_IF)
    was_active = sta.active()
    sta.active(True)
    await uasyncio.sleep_ms(100)  # yield before blocking call

    try:
        nets = sta.scan()
    except Exception:
        nets = []

    if not was_active:
        sta.active(False)

    # Deduplicate and sort by signal strength (rssi, higher = stronger)
    results = []
    seen    = set()
    for n in sorted(nets, key=lambda x: -x[3]):
        ssid = n[0].decode('utf-8', 'ignore') if isinstance(n[0], bytes) else n[0]
        if ssid and ssid not in seen:
            seen.add(ssid)
            results.append({'ssid': ssid, 'rssi': n[3]})

    return _json({'networks': results})


# ---------------------------------------------------------------------------
# Task entry point
# ---------------------------------------------------------------------------

async def task_webserver(hw, config, state, targets):
    """Initialise module globals and start the microdot server on port 80."""
    global _hw, _config, _state, _targets
    _hw      = hw
    _config  = config
    _state   = state
    _targets = targets

    await app.start_server(port=80, debug=False)
