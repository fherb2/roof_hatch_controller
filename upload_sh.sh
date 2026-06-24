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
USB_PORT="/dev/ttyUSB0"

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
    log "  Cleaning target directories on device ..."
    mpremote $connect exec "
import os, sys

def rm_rf(path):
    try:
        entries = os.listdir(path)
    except OSError:
        return  # path does not exist or is a file
    for entry in entries:
        full = path + '/' + entry
        try:
            os.listdir(full)  # is it a directory?
            rm_rf(full)
            os.rmdir(full)
        except OSError:
            os.remove(full)

for d in ['app', 'shared', 'lib', 'webpages']:
    rm_rf('/' + d)
    print('Removed /' + d)
"

    # Upload source tree (boot.py, main.py, app/, shared/, webpages/)
    log "  Uploading source ..."
    mpremote $connect cp "${SOURCE_DIR}/boot.py"      :boot.py
    mpremote $connect cp "${SOURCE_DIR}/main.py"      :main.py
    mpremote $connect cp "${SOURCE_DIR}/config.json"  :config.json
    mpremote $connect cp -r "${SOURCE_DIR}/app"       :app
    mpremote $connect cp -r "${SOURCE_DIR}/shared"    :shared
    mpremote $connect cp -r "${SOURCE_DIR}/webpages"  :webpages

    # Upload lib/ if it contains files
    if [[ -d "$LIB_DIR" ]] && [[ -n "$(ls -A "$LIB_DIR")" ]]; then
        log "  Uploading lib ..."
        mpremote $connect cp -r "${LIB_DIR}" :lib
    else
        log "  lib/ is empty, skipping."
    fi

    log "Upload complete."

    # Soft-reset the device so it starts with the new code
    log "  Resetting device ..."
    mpremote $connect reset
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

install_deps
upload_to_device
