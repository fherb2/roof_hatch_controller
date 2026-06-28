#!/usr/bin/env bash
# upload.sh - Deploy roof_hatch_controller to MicroPython device or install deps locally
#
# Usage:
#   ./upload.sh          - Install missing deps to ./lib, then upload to device
#
# Upload mode is selected by UPLOAD_MODE below.

set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Upload mode: "usb" or "wifi"
UPLOAD_MODE="usb"

# USB: serial port of the connected MicroPython device
USB_PORT="/dev/ttyACM0"

# WiFi: IP address of the MicroPython device (WebREPL must be enabled)
WIFI_IP="192.168.1.100"
WIFI_PASS="micropython"

# Paths
SOURCE_DIR="./source"
LIB_DIR="./lib"
REQUIREMENTS="./requirements.txt"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

log() { echo "[upload.sh] $*"; }

# Build mpremote connection string from UPLOAD_MODE
mpremote_connect() {
    case "$UPLOAD_MODE" in
        usb)  echo "connect $USB_PORT" ;;
        wifi) echo "connect ws:${WIFI_IP},${WIFI_PASS}" ;;
        *)    echo "Unknown UPLOAD_MODE: $UPLOAD_MODE" >&2; exit 1 ;;
    esac
}

# -----------------------------------------------------------------------------
# Step 1: Install missing dependencies from requirements.txt into ./lib
# -----------------------------------------------------------------------------

install_deps() {
    if [[ ! -f "$REQUIREMENTS" ]]; then
        log "No requirements.txt found, skipping dependency install."
        return
    fi

    log "Checking dependencies from $REQUIREMENTS ..."

    while IFS= read -r package || [[ -n "$package" ]]; do
        # Skip empty lines and comments
        [[ -z "$package" || "$package" == \#* ]] && continue

        # Derive expected filename: last path segment, replace - with _, add .py
        # Works for simple package names; complex packages may need manual handling
        filename=$(echo "$package" | awk -F'/' '{print $NF}' | tr '-' '_')
        target_py="${LIB_DIR}/${filename}.py"
        target_dir="${LIB_DIR}/${filename}"

        if [[ -f "$target_py" || -d "$target_dir" ]]; then
            log "  OK (already present): $package"
        else
            log "  Installing: $package"
            micropython -m mip install --target "$LIB_DIR" "$package"
        fi
    done < "$REQUIREMENTS"
}

# -----------------------------------------------------------------------------
# Step 2: Upload to device
# -----------------------------------------------------------------------------

upload_to_device() {
    local connect
    connect=$(mpremote_connect)

    log "Uploading to device ($UPLOAD_MODE) ..."

    # Remove existing app directories on device to avoid stale files
    # Upload in separate mpremote sessions.
    # during the full upload. Generated in /tmp, never committed to the repo.
    local stop_wdt
    stop_wdt=$(mktemp /tmp/stop_wdt_main_XXXXXX.py)
    cat > "$stop_wdt" << 'EOF'
# Temporary main.py uploaded before the real deployment.
# Runs no watchdog so mpremote can upload without WDT resets.
import time
while True:
    time.sleep_ms(500)
EOF

    # Before attempting any upload, send a reset and wait for a clean REPL.
    # This breaks out of any crash-repl, WDT loop or stale session.
    log "  Resetting device to get a clean REPL ..."
    mpremote $connect reset 2>/dev/null || true
    sleep 3

    log "  Uploading WDT-stop main.py (retrying until device is reachable) ..."
    for attempt in $(seq 1 20); do
        if mpremote $connect cp "$stop_wdt" :main.py 2>/dev/null; then
            break
        fi
        if [[ $attempt -eq 20 ]]; then
            log "  ERROR: device unreachable after 20 attempts."
            log "  Power-cycle the device and run upload.sh again."
            rm -f "$stop_wdt"
            exit 1
        fi
        # Send another reset to clear any crash-repl state, then retry
        mpremote $connect reset 2>/dev/null || true
        log "  Not reachable yet, retrying in 2s ($attempt/20) ..."
        sleep 2
    done
    mpremote $connect reset
    rm -f "$stop_wdt"
    sleep 2

    # Clean and upload everything in a single mpremote session so there
    # is only one soft-reset and no reconnection race conditions.
    log "  Cleaning and uploading (single session) ..."

    local cleaning
    cleaning=$(cat << 'PYEOF'
import os
def rm_rf(p):
    try:
        entries = os.listdir(p)
    except OSError:
        return
    for e in entries:
        f = p + '/' + e
        try:
            os.listdir(f)
            rm_rf(f)
            os.rmdir(f)
        except OSError:
            os.remove(f)
for d in ['app', 'shared', 'lib', 'webpages']:
    rm_rf('/' + d)
    print('Removed /' + d)
PYEOF
)

    # Build the mpremote command as an array so conditional entries
    # (config.json, lib/) expand correctly without quoting problems.
    local cmd=(mpremote $connect exec "$cleaning")
    cmd+=(+ cp "${SOURCE_DIR}/config.json.template" :config.json.template)
    [[ -f "${SOURCE_DIR}/config.json" ]] && \
        cmd+=(+ cp "${SOURCE_DIR}/config.json" :config.json)
    cmd+=(+ cp -r "${SOURCE_DIR}/app"      :)
    cmd+=(+ cp -r "${SOURCE_DIR}/shared"   :)
    cmd+=(+ cp -r "${SOURCE_DIR}/webpages" :)
    if [[ -d "$LIB_DIR" ]] && [[ -n "$(ls -A "$LIB_DIR")" ]]; then
        cmd+=(+ cp -r "${LIB_DIR}" :)
    fi
    cmd+=(+ cp "${SOURCE_DIR}/main.py" :main.py)

    "${cmd[@]}"

    # Final reset to start the real main.py
    log "  Resetting device to start application ..."
    mpremote $connect reset

    log "Upload complete."

}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

install_deps
upload_to_device
