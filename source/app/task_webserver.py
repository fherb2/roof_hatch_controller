# task_webserver.py - Async task: microdot web server
#
# Routes:
#   GET  /                       → index.html  (main control page)
#   GET  /config                 → config.html (requires active session)
#   GET  /ap                     → ap_setup.html
#   GET  /static/<path>          → static files (CSS, JS)
#   GET  /sse                    → SSE live status stream
#   GET  /api/config             → current config as JSON
#   POST /api/hatch/<i>/pos      → set hatch target by position index
#   POST /api/hatch/<i>/test     → test move hatch servo to exact degrees
#                                   (full sequence; also used for the hatch
#                                   servo's mounting angle) (session required)
#   POST /api/hatch/<i>/brake_test → move only the brake servo directly to
#                                   exact degrees (mounting angle) (session required)
#   POST /api/tester/hatch       → move servo tester hatch output (session required)
#   POST /api/tester/brake       → move servo tester brake output (session required)
#   POST /api/config             → validate + persist config immediately, no restart
#   POST /api/wifi                → save Wi-Fi credentials, then restart
#   GET  /api/wifi/scan           → scan available SSIDs, return JSON list

import gc
import uasyncio
import ujson
import uos
import network
from microdot import Microdot, Response, send_file

from app import session
from app.hw_config import NUM_HATCHES, FAILSAFE_DEG
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


def _find_pos_idx(hatch_deg, positions):
    """Return 0-based index of hatch_deg in positions list, or None."""
    for i, pos in enumerate(positions):
        if pos["hatch_deg"] == hatch_deg:
            return i
    return None


def _build_status():
    """Build the SSE status payload pushed to all clients every 500 ms.

    The 'failsafe' field per hatch is True when the actual position is
    FAILSAFE_DEG (-1), meaning the hatch is in an unverified closed state
    after a fail-safe sequence.  In that case pos_idx is null and no
    position button is highlighted as active in the web UI.

    Carries no servo tester fields - the tester has no automatic "moving"
    concept, config.html reads its values directly from GET /api/config.
    """
    if not _config or "hatches" not in _config:
        return {"hatches": [], "wifi": {"connected": False, "ip": ""},
                "session": {"active": False, "unlock": False}}
    hatches = []
    for i in range(NUM_HATCHES):
        s         = _state["hatches"][i]
        t         = _targets["hatches"][i]
        positions = _config["hatches"][i]["positions"]
        hatches.append({
            "name":          _config["hatches"][i]["name"],
            "short_name":    _config["hatches"][i]["short_name"],
            "current_deg":   s["hatch_deg"],
            "target_deg":    t["hatch_deg"],
            "moving":        t["hatch_deg"] != s["hatch_deg"],
            "failsafe":      s["hatch_deg"] == FAILSAFE_DEG,
            "pos_idx":       _find_pos_idx(s["hatch_deg"], positions),
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
    """Serve a static asset (CSS/JS fallback files) from /webpages/static/."""
    return send_file(f'/webpages/static/{path}')


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route('/')
async def index(request):
    """Serve the main control page, or redirect to /ap if config.json
    failed to load (nothing meaningful to control yet)."""
    if not _config or "hatches" not in _config:
        return Response(status_code=302, headers={'Location': '/ap'})
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
    """Serve the Wi-Fi setup page (used both in AP mode and as a manual
    fallback if reachable in client mode)."""
    return send_file('/webpages/ap_setup.html')


# ---------------------------------------------------------------------------
# SSE: live status stream
# ---------------------------------------------------------------------------

class _SseStream:
    """Async iterator that emits one SSE status event every 500 ms.

    Implemented as a class-based async iterator instead of an async
    generator function to avoid a MicroPython/microdot incompatibility:
    async generators that contain both yield and await in the same
    function body cause microdot to receive a non-bytes object on the
    second iteration and raise TypeError in its write path.
    """

    def __aiter__(self):
        return self

    async def __anext__(self):
        await uasyncio.sleep_ms(500)
        try:
            data = ujson.dumps(_build_status())
            return f'data: {data}\n\n'.encode()
        except Exception as e:
            print(f"SSE: status build failed: {e}")
            return b': keepalive\n\n'


@app.route('/sse')
async def sse(request):
    """Server-Sent Events stream: pushes status JSON every 500 ms."""
    return Response(
        body=_SseStream(),
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
    """Set hatch target by named position index and persist the new target.

    Looks up the degree value for the requested position in the hatch's
    positions list, writes it to the RAM target dict (which triggers the
    control loop to start moving) and persists the new target to
    state.json so that it survives a reboot.
    """
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

    new_deg = positions[pos_idx]["hatch_deg"]
    _targets["hatches"][idx]["hatch_deg"] = new_deg
    _state["hatches"][idx]["target_deg"] = new_deg
    save_state(_state)
    return _json({'ok': True})


@app.route('/api/hatch/<idx>/test', methods=['POST'])
async def test_position(request, idx):
    """Move hatch servo to an exact angle in degrees for visual calibration.

    Requires an active config session. Sets the RAM target (triggering
    task_control to run the full brake-open → move → brake-close
    sequence) and persists the target to state.json. The value must lie
    within this hatch's own [hard_stop_min_deg, hard_stop_max_deg] from
    config. Also used, unchanged, for the hatch servo's mounting angle -
    the mounting angle is just another degree value the client already
    knows, not looked up from the positions list.
    """
    if not session.is_session_active():
        return _json({'error': 'not authorized'}, 403)

    idx = int(idx)
    if not 0 <= idx < NUM_HATCHES:
        return _json({'error': 'invalid hatch index'}, 400)

    body = request.json
    if body is None or 'hatch_deg' not in body:
        return _json({'error': 'missing hatch_deg'}, 400)

    hatch_deg = int(body['hatch_deg'])
    hs        = _config["hatches"][idx]["hatch_servo"]
    if not hs["hard_stop_min_deg"] <= hatch_deg <= hs["hard_stop_max_deg"]:
        return _json({'error': 'hatch_deg outside hardstop range'}, 400)

    _targets["hatches"][idx]["hatch_deg"] = hatch_deg
    _state["hatches"][idx]["target_deg"] = hatch_deg
    save_state(_state)
    return _json({'ok': True})


@app.route('/api/hatch/<idx>/brake_test', methods=['POST'])
async def brake_test(request, idx):
    """Move only the brake servo of one hatch directly (mounting angle).

    Requires an active config session. Sets targets["brake_only"][idx],
    which task_control picks up and clears again once the move (success
    or failure) has completed - see task_control._move_brake_only(). The
    hatch servo of the same hatch is powered but held steady, not moved.
    No state is persisted here directly; _move_brake_only() does that
    itself once the move actually completes.
    """
    if not session.is_session_active():
        return _json({'error': 'not authorized'}, 403)

    idx = int(idx)
    if not 0 <= idx < NUM_HATCHES:
        return _json({'error': 'invalid hatch index'}, 400)

    body = request.json
    if body is None or 'brake_deg' not in body:
        return _json({'error': 'missing brake_deg'}, 400)

    brake_deg = int(body['brake_deg'])
    bs        = _config["hatches"][idx]["brake_servo"]
    if not bs["hard_stop_min_deg"] <= brake_deg <= bs["hard_stop_max_deg"]:
        return _json({'error': 'brake_deg outside hardstop range'}, 400)

    _targets["brake_only"][idx] = brake_deg
    return _json({'ok': True})


# ---------------------------------------------------------------------------
# API: servo tester
# ---------------------------------------------------------------------------

@app.route('/api/tester/<which>', methods=['POST'])
async def tester_move(request, which):
    """Move one of the two servo tester outputs (no relay, permanently
    powered - see hw_config.TESTER_CH).

    Requires an active config session. <which> must be "hatch" or
    "brake". Sets targets["tester"][f"{which}_deg"], which task_control
    picks up and clears again once the move has completed - see
    task_control._move_tester_servo().
    """
    if not session.is_session_active():
        return _json({'error': 'not authorized'}, 403)

    if which not in ("hatch", "brake"):
        return _json({'error': 'invalid tester output'}, 400)

    body = request.json
    if body is None or 'angle_deg' not in body:
        return _json({'error': 'missing angle_deg'}, 400)

    angle_deg = int(body['angle_deg'])
    servo_cfg = _config["global"]["servo_tester"][f"{which}_servo"]
    if not servo_cfg["hard_stop_min_deg"] <= angle_deg <= servo_cfg["hard_stop_max_deg"]:
        return _json({'error': 'angle_deg outside hardstop range'}, 400)

    _targets["tester"][f"{which}_deg"] = angle_deg
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
    """Validate, persist and apply a new configuration immediately.

    Requires active session. Full validation runs before writing. Writes
    the new config to flash and updates the in-RAM _config in place (so
    task_control and task_display pick up the change on their very next
    pass) - no restart is needed or performed. Called for every single
    field accepted on the config page, and for every position added or
    removed, each time with the complete current document.
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

    # Write to a temporary file first, then rename: os.rename() is atomic
    # on the ESP32 filesystem, so an interrupted write (memory error,
    # reset, power loss) can never leave a truncated /config.json behind -
    # which would make the device fail to boot on the next start.
    try:
        with open('/config.json.tmp', 'w') as f:
            f.write(ujson.dumps(new_cfg))
            f.write('\n')
        try:
            uos.remove('/config.json')
        except OSError:
            pass  # first write, or already gone - nothing to replace
        uos.rename('/config.json.tmp', '/config.json')
    except OSError as e:
        return _json({'error': f'write failed: {e}'}, 500)
    except MemoryError:
        return _json({'error': 'out of memory while writing config'}, 500)

    # Update in place so task_control and task_display see the change
    # immediately without needing a reference swap. Each hatch_servo/
    # brake_servo/positions/servo_tester value is replaced wholesale
    # since the client always sends the complete current document.
    _config["global"]["servo_tester"].update(new_cfg["global"]["servo_tester"])
    for i, h in enumerate(new_cfg["hatches"]):
        _config["hatches"][i].update(h)

    del new_cfg  # drop the reference to the just-parsed copy before
                 # collecting, so gc can actually reclaim it
    gc.collect()

    return _json({'ok': True})


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
