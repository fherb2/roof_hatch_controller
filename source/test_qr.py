# test_qr.py - Render current Wi-Fi IP as QR code on the OLED display.
#
# Run once from the REPL or via mpremote to test QR code readability.
# Does not modify any application files.
#
# Usage in REPL:
#   exec(open('test_qr.py').read())

from machine import I2C, Pin
import network
import ssd1306
from uQR import QRCode

# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
i2c     = I2C(0, sda=Pin(5), scl=Pin(6), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c, addr=0x3C)

# ---------------------------------------------------------------------------
# Get IP address
# ---------------------------------------------------------------------------
sta = network.WLAN(network.STA_IF)
ap  = network.WLAN(network.AP_IF)

if sta.isconnected():
    ip = sta.ifconfig()[0]
elif ap.active():
    ip = ap.ifconfig()[0]
else:
    ip = "0.0.0.0"

url = "http://" + ip
print(f"QR target: {url}")

# ---------------------------------------------------------------------------
# Generate QR matrix
# ---------------------------------------------------------------------------
qr = QRCode()
qr.add_data(url)
matrix = qr.get_matrix()

size = len(matrix)  # matrix is square
print(f"QR version: {qr.version}, matrix size: {size}x{size}")

# ---------------------------------------------------------------------------
# Render on OLED
# Quiet zone: 4 px border (required for scanners)
# Scale: 1 px per module
# Total: (size + 8) x (size + 8) pixels
# ---------------------------------------------------------------------------
QUIET = 4
total = size + 2 * QUIET

# Centre on 128x64 display
offset_x = (128 - total) // 2
offset_y = (64  - total) // 2

if offset_x < 0 or offset_y < 0:
    print(f"WARNING: QR code ({total}x{total}px) does not fit on display!")

display.fill(1)  # white background

# Draw white quiet zone + white modules (invert: OLED 1=white, 0=black)
# QR: True=black module, False=white module
for row in range(total):
    for col in range(total):
        mx = col - QUIET
        my = row - QUIET
        if 0 <= mx < size and 0 <= my < size:
            pixel = 1 if matrix[my][mx] else 0  # black module on white background
        else:
            pixel = 0  # quiet zone = white (display is already white)

        x = offset_x + col
        y = offset_y + row
        if 0 <= x < 128 and 0 <= y < 64:
            display.pixel(x, y, pixel)

display.show()
print(f"Done. QR code for '{url}' shown on OLED ({total}x{total}px, offset {offset_x},{offset_y}).")
