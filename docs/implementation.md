# Implementation Documentation – Roof Hatch Controller

This document describes the complete hardware and software of the roof hatch controller in enough detail that work (testing, debugging, or extensions) can resume immediately in a new chat, together with the source code. Status: after the per-channel configuration redesign (servo tester, mounting angles, time-based S-curve ramp, internationalisation). All previous concept documents (`konzept_grad_umstellung.md`, `konzept_config_redesign.md`) have been incorporated into this document and removed – this document is the sole reference. This is the English translation of `implementierungsdoku.md`; the German version remains the primary/original document.

---

## 1. Project Goal

Control of up to 4 roof hatches via a web interface (phone, tablet, desktop). Each hatch has a powerful servo for movement (lever servo) and a second servo for a mechanical locking brake (latch servo). Runs on a Seeed XIAO ESP32-S3 with MicroPython. Additionally, a servo tester output pair for maintenance and calibration of servos outside the installed hatches.

---

## 2. Hardware

### 2.1 Components

| Component                              | Function                                                                     |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| Seeed XIAO ESP32-S3                    | Microcontroller, Wi-Fi                                                        |
| Seeed Expansion Board Base for XIAO    | I²C hub, OLED (SSD1306 128×64), micro-SD slot, RTC (PCF8563), user button   |
| Grove 4-Channel SPDT Relay             | Switches servo power supply (per hatch)                                       |
| Grove 16-Channel PWM Driver (PCA9685)  | PWM signals for all servos (8 hatch channels + 2 servo tester channels)       |
| MCP23017 (breakout)                    | I²C GPIO expander for local control panel – **deferred**                    |

### 2.2 I²C Bus (all devices on one bus)

| Device                     | Address      | Notes                                                                                                                                                                                              |
| --------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSD1306 OLED                | 0x3C         |                                                                                                                                                                                                    |
| PCA9685 PWM driver          | 0x7F         | All address pads pulled high; therefore **never** appears in `i2c.scan()` results (outside the 0x08–0x77 scan range), but is directly addressable. See section 15. |
| Grove 4-ch. relay           | 0x11         | The board's I²C address can be reconfigured via software (see https://wiki.seeedstudio.com/Grove-4-Channel_SPDT_Relay/). |
| RTC PCF8563                 | 0x51         | Present on the expansion board, currently unused (see 17.2)                                                                                                                                       |
| MCP23017 (deferred)         | 0x20         |                                                                                                                                                                                                    |

**GPIO pins on the expansion board (XIAO ESP32-S3), as of `hw_config.py`:**

| Signal       | GPIO                    |
| ------------ | ----------------------- |
| I²C SDA     | 5                       |
| I²C SCL     | 6                       |
| User button  | 2 (active low, pull-up) |

### 2.2b I²C Bus Clock Rate

`I2C_FREQ = 1_000_000` (1MHz) – raised from the originally conservative default of 400kHz, after a successful stability test of all four bus participants at this rate (including repeated relay switching, the most likely failure candidate, since the Grove relay board implements its I²C slave function in software on an STM32F030 rather than in silicon).

The I²C specification (NXP/UM10204) defines named modes with fixed upper limits (not mandatory intermediate steps, but the practically relevant thresholds for electrical requirements and component specification):

| Mode | Max. clock rate |
|---|---|
| Standard Mode | 100 kHz |
| Fast Mode | 400 kHz |
| Fast Mode Plus (Fm+) | 1 MHz |
| High Speed Mode (Hs-Mode) | 3.4 MHz |
| Ultra Fast Mode (UFm) | 5 MHz |

`1_000_000` sits exactly at the upper Fm+ limit – no safety margin above it, but within the specified limits of all chips involved (ESP32-S3, PCA9685 and SSD1306 all support Fm+ or higher per their datasheets). High Speed Mode (3.4MHz) is not an option here, since it requires a special bus-master protocol element (master code + switchover) that neither the ESP32 nor the attached devices support.

**No per-device clock switching:** The clock rate is generated by the master (ESP32) for the entire shared clock line, not per device – unlike the PCA9685 PWM frequency (see 9.11), there is no known candidate here that would justify a runtime switch.

### 2.3 Electrical Wiring

- **One relay per hatch** (NO contact): switches the shared +8V line for both servos of that hatch (lever and latch servo are always powered together).
- **Servo tester outputs are permanently powered, with no relay of their own** – intended for servos removed from an installed hatch and tested individually on the bench. Since a tester channel and a hatch channel are never under load at the same time (the tester is only ever used with servos removed from the hatches), this is uncritical.
- GND is common to all servos throughout.
- Each servo has its own PWM line from the PCA9685.
- Power supply: 3A / 8V – sufficient for **one hatch at a time** (guaranteed in software, see section 11).

### 2.4 PCA9685 Channel Assignment

| Channel | Function                          |
| ------- | ----------------------------------- |
| 0       | Hatch 1 – lever servo               |
| 1       | Hatch 1 – latch servo               |
| 2       | Hatch 2 – lever servo               |
| 3       | Hatch 2 – latch servo               |
| 4       | Hatch 3 – lever servo               |
| 5       | Hatch 3 – latch servo               |
| 6       | Hatch 4 – lever servo               |
| 7       | Hatch 4 – latch servo               |
| 8       | Servo tester – lever servo output   |
| 9       | Servo tester – latch servo output   |

Configured in `hw_config.py`: `PCA9685_CH = {0: (0,1), 1: (2,3), 2: (4,5), 3: (6,7)}` (hatch channels) and `TESTER_CH = (8, 9)` (servo tester, its own constant pair rather than a dict entry, since it only exists once).

### 2.5 Relay Assignment

| Relay channel | Function                |
| -------------- | ------------------------- |
| 0              | +8V for hatch 1's servos  |
| 1              | +8V for hatch 2's servos  |
| 2              | +8V for hatch 3's servos  |
| 3              | +8V for hatch 4's servos  |

Configured as `RELAY_CH = {0: 0, 1: 1, 2: 2, 3: 3}` in `hw_config.py`. The servo tester has no relay entry (see 2.3).

---

## 3. Mechanics and Servos

### 3.1 Locking Brake (Latch Servo)

- A ratchet system of interlocking teeth takes over the holding torque when the servos are unpowered.
- The hatch gear train is **not self-locking** → without the brake and power, the hatch would close under its own weight.
- The brake has two regular operating positions (`open_deg`/`close_deg`), **individually configurable per hatch** (the brake mechanism may sit slightly differently on each hatch), plus its own mounting angle for maintenance purposes (see 3.7).
- The brake engages under spring load; the servo pulls it open against the spring.
- The latch servo cannot be damaged by over-travel (cam principle).
- The latch servo now **ramps** just like the lever servo (its own configurable `speed_deg_per_s`) – it originally jumped directly between the two positions in a single step; this was changed because it let two fixed "hopefully long enough" wait times be replaced by actually waiting for the ramp to finish (see 9.11).

### 3.2 Servo Thermals

Hobby servos (here roughly 4.5 Nm / 2–3 A stall current) are designed for intermittent duty. Continuous load leads to overheating. Therefore: servos are **only powered during the movement sequence** (relay on), unpowered afterwards. The mechanical brake takes over the holding torque. The servo tester is the only exception (permanently powered, see 2.3) – but no loaded servos are ever connected there.

### 3.3 Extended PWM Range

Every servo (lever and latch, per hatch, plus both servo tester outputs) has its own individually configurable range instead of fixed values:

- `range_deg`: total range, toggle value **180°, 270°, or 360°**
- `direction`: rotation direction, `"pos"` or `"neg"` (default `"neg"`) – determines whether 0° corresponds to the minimum or maximum pulse width (see 9.2b)
- `pulse_min_us`/`pulse_max_us`: pulse-width limits in µs, configurable within **500–4000µs** (covers standard as well as long-throw servos)

Absolute hardware limits for this in `hw_config.py`: `SERVO_MIN_US = 500`, `SERVO_MAX_US = 4000` – every configured `pulse_min_us`/`pulse_max_us` value must lie within this window, regardless of the chosen `range_deg`.

### 3.4 Servo Power-On Behaviour (Power-On Jerk)

High-torque digital servos run an internal initialisation routine on power-up and briefly move at maximum speed towards centre position – regardless of the PWM signal present. The movement sequence in `task_control.py` still contains a workaround for this: `step_preset_servos` outputs the actual position before relay-on (lever servo at 0µs = no signal, so the controller doesn't see a signal during its own boot process), `step_preset_delay` gives the controller time to boot before the first valid signal is applied. Details and background: `xiao_esp32s3_erfahrungen.md` section 12 (German only).

**Historical note – wiggle removed:** In addition to the preset delay, there used to be a "wiggle" (lever servo alternated briefly by ±10µs around the target position, 5 cycles) intended to additionally absorb the same power-on jerk. Since the movement ramp now follows an S-curve (see 3.5/9.11) and every move already starts at zero speed, the effect wiggle was meant to prevent no longer occurred – the mechanism was removed without replacement.

### 3.5 Motion Profile: S-Curve Instead of Linear Ramp

Both servos of a hatch (and both servo tester outputs) do not move linearly but follow a **smoothstep profile** (`3f² − 2f³`, `f` = motion progress 0…1): position and its first derivative (velocity) are exactly zero and continuous at the start and end of the motion – no jerk (discontinuity in acceleration) at either end. See section 9.11 for implementation details.

Important consequence for `speed_deg_per_s`: with a smoothstep profile, the **peak velocity** (reached at the midpoint of the motion) is 1.5 times the average velocity. The configured `speed_deg_per_s` is deliberately defined as the **peak velocity** (not the average) – the motion duration is therefore internally extended by a factor of 1.5 compared to a linear ramp of the same peak speed, so that the configured speed limit (see 3.6) is never exceeded at any point in the motion.

### 3.6 Speed Limits

The same allowed range for `speed_deg_per_s` applies to every servo (lever and latch servo of all 4 hatches, both servo tester outputs): **30–180°/s** (`SPEED_MIN_DEG_PER_S`/`SPEED_MAX_DEG_PER_S` in `hw_config.py`).

### 3.7 Mounting Angle

In addition to the regular detent positions (lever servo) or open/close positions (latch servo), each of the two servos per hatch has its own, separately configurable **mounting angle** (`mount_angle_deg`). Purpose: being able to move to an angle convenient for physical mounting, independent of the operationally meaningful detent positions.

**Important warning (must be included in the end-user documentation):** The lever servo's mounting angle may be a position that **does not fit the brake's tooth pattern** and therefore cannot be held under load. The mounting angle must therefore **only** be driven to after the load (the window/hatch) has been physically disconnected from the lever beforehand. In software, the mounting angle is treated like any regular target position (including the full brake-open/move/brake-close sequence) – the software cannot and does not enforce this warning itself.

---

## 4. Platform

**MicroPython** on the XIAO ESP32-S3.

Rationale: `uasyncio` for cooperative multitasking, `microdot` as an async web server, built-in `ujson`, stable I²C libraries.

External libraries (via `mip`, managed through `requirements.txt`):

- `ssd1306` – OLED driver
- `microdot` (via `github:miguelgrinberg/microdot`) – web server

Additionally manually installed libraries (not available via `mip`, see 6.2/6.2b):

- `uQR` – QR code generation
- `writer.py` (Peter Hinch, MIT) + `font_hatch_status.py` – larger display font

Custom drivers in the project:

- `shared/pca9685.py` – PCA9685 driver (hand-written, considerably smaller than the full PyPI package)
- `shared/grove_relay_4chn_spdt.py` – Grove relay driver (no MicroPython package available)
- `shared/servo_units.py` – degree↔µs conversion (see 9.2b)

---

## 5. Project Structure

### 5.1 Directory on the Development Machine

```
roof_hatch_controller/
├── designkonzept.md             # original design concept (German, superseded, historical)
├── docs/
│   ├── implementierungsdoku.md  # this document (German, primary)
│   └── implementation.md        # this document (English translation)
├── requirements.txt              # external MicroPython packages
├── upload.sh                     # deploy script
├── pyrightconfig.json             # VSCode/Pyright: extraPaths: ["source"]
├── lib/                          # external libraries (via mip or manually installed)
│   ├── microdot.py
│   ├── ssd1306.py
│   ├── uQR.py                    # manually installed, see 6.2
│   ├── writer.py                 # manually installed, see 6.2b (Peter Hinch, MIT)
│   └── font_hatch_status.py      # manually generated, see 6.2b (14px font for task_display.py)
└── source/                       # copied 1:1 to the device
    ├── main.py                   # entry point, also contains the early button check for AP_MODE
    ├── config.json               # device configuration (adjust default values!)
    ├── config.json.template      # conservative template for config_migration.py
    ├── test_qr.py                 # historical, standalone QR test script (see 15)
    ├── app/
    │   ├── __init__.py
    │   ├── hw_config.py          # hardware constants
    │   ├── hw_init.py            # hardware initialisation
    │   ├── state.py              # config.json / state.json management
    │   ├── config_migration.py   # config.json creation/migration at boot
    │   ├── failsafe.py           # fail-safe sequence
    │   ├── fatal_error.py        # central handler for fatal boot errors
    │   ├── wifi.py               # Wi-Fi management
    │   ├── session.py            # config session/QR state
    │   ├── task_watchdog.py      # hardware WDT feeder task
    │   ├── task_control.py       # control loop task
    │   ├── task_button.py        # button monitor task
    │   ├── task_display.py       # OLED update task
    │   └── task_webserver.py     # microdot web server task
    ├── shared/
    │   ├── __init__.py
    │   ├── pca9685.py            # PCA9685 driver
    │   ├── grove_relay_4chn_spdt.py  # Grove relay driver
    │   └── servo_units.py        # degree <-> µs conversion (deg_to_us/us_to_deg)
    └── webpages/
        ├── index.html            # control web page
        ├── config.html           # configuration web page
        ├── ap_setup.html         # Wi-Fi configuration (AP mode)
        └── static/
            ├── bootstrap.min.css
            ├── bootstrap.bundle.min.js
            └── alpine.min.js
```

**Note on `boot.py`:** An earlier planned separate `boot.py` for early AP-mode detection was never implemented – the logic (`_button_pressed()`, setting `AP_MODE`) sits directly at the top of `main.py`, before all other imports (see 8/9.1).

### 5.2 On the Device (Flash Filesystem)

Same structure, but without the `source/` prefix. Root `/` corresponds to `source/`.

Additionally on flash (not in the repo, created at runtime):

- `/wifi.json` – Wi-Fi credentials (via AP mode or manually)
- `/state.json` – last known servo state (created automatically on first boot)

---

## 6. Build & Deployment

### 6.1 `requirements.txt`

```
ssd1306
github:miguelgrinberg/microdot
```

`upload.sh` checks each entry to see whether the file/directory already exists in `lib/` and installs it if needed.

### 6.2 Installing `uQR` Manually

`uQR` is not in the micropython.org package index. Download it manually, once:

1. `https://github.com/JASchilz/uQR` → `uQR.py` → Raw button → save as `lib/uQR.py`
2. `upload.sh` then copies `lib/` to the device automatically

**Important:** `lib/` lives at the **project root** (`roof_hatch_controller/lib/`), **not** under `source/lib/` – `upload.sh` uses `LIB_DIR="./lib"` relative to the script's execution directory (project root). Files accidentally placed under `source/lib/` will not be found by `upload.sh` and therefore not uploaded.

### 6.2b Installing `writer.py` + `font_hatch_status.py` Manually

For the larger font in `task_display.py` (see 9.13), two further files not installable via `mip` are needed, also under `lib/` (project root):

1. **`writer.py`**: taken unchanged from `https://github.com/peterhinch/micropython-font-to-py` (folder `writer/writer.py`, MIT license) → `lib/writer.py`
2. **`font_hatch_status.py`**: generated with the PC tool `font_to_py.py` from the same repository:
   ```bash
   python3 font_to_py.py DejaVuSansMono.ttf 14 font_hatch_status.py -f -s 32 -l 126
   ```
   Result: monospace, 14px character height, 8px character width, character set 32–126 (ASCII), roughly 8.3 KB. Place the file at `lib/font_hatch_status.py`.
3. `upload.sh` then copies `lib/` to the device automatically (like `uQR.py`)

If a different font/size is needed: re-run `font_to_py.py` with a different height value or a different `.ttf` file. Adjust the line grid in `task_display.py` (`_LINE_HEIGHT_PX = 16`) if the new character height no longer matches the current grid.

### 6.3 `upload.sh`

Configuration block at the top of the script:

- `UPLOAD_MODE`: `"usb"` or `"wifi"`
- `USB_PORT`: e.g. `/dev/ttyACM0`
- `WIFI_IP`: device IP for WebREPL

Sequence:

1. Walk `requirements.txt` → fetch missing packages via `micropython -m mip install --target lib/`
2. Reset the device to get a clean REPL connection (`mpremote reset`, with retry if needed)
3. Everything in a single mpremote session: delete target directories, upload `config.json.template`, `config.json` (if present), `app/`, `shared/`, `webpages/`, `lib/`, then `main.py` last
4. Final `mpremote reset` → the real `main.py` starts

**`mpremote reset` is a genuine hardware reset** (DTR/RTS line, like the reset button) – on the next boot it loads all Python modules fresh from flash. **Important limitation:** the PCA9685 itself has its own power supply and remains continuously powered across a plain ESP32 reset – its `PRE_SCALE` register (PWM frequency) and internal state survive an `mpremote reset` unchanged. After changes to PWM-/timing-related code, an additional **full power cycle** (USB unplug/replug) is therefore recommended, which also resets the PCA9685's supply.

**`config.json.template`** is always uploaded; `config.json` only if present in the `source/` directory.

**Wi-Fi upload** (WebREPL): enable once, via USB:

```python
import webrepl_setup
```

After that, possible via `UPLOAD_MODE="wifi"`.

### 6.4 `pyrightconfig.json` (Repo Root)

```json
{
    "extraPaths": ["source"]
}
```

Lets VSCode/Pyright resolve imports correctly (without a `source.` prefix).

### 6.5 Downloading Static Libraries Once

```bash
cd source/webpages/static
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css -o bootstrap.min.css
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js -o bootstrap.bundle.min.js
curl -L https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js -o alpine.min.js
```

---

## 7. Configuration Files

### 7.1 `/config.json` – Device Configuration

Must be copied to the device before first boot (done automatically by `upload.sh` from `source/config.json`). All positions, hard stops, speeds and mounting angles are given in **degrees** or **degrees/s**, not µs. The conversion to µs for PCA9685 output is handled by `shared/servo_units.py` (`deg_to_us()`), exclusively inside `task_control.py`, immediately before driving the hardware.

**Central architectural decision:** every servo parameter (range, direction, speed, pulse widths, hard stops) is configurable **per channel** – unlike an earlier intermediate version, there are no shared/global servo parameters for all 4 hatches anymore. The only exception is the servo tester (`global.servo_tester`), which has its own, internally global parameter set, since it is only a single additional channel pair.

```json
{
    "global": {
        "servo_tester": {
            "pwm_freq_hz": 50,
            "hatch_servo": {
                "range_deg": 270, "direction": "neg", "speed_deg_per_s": 30,
                "pulse_min_us": 500, "pulse_max_us": 2500,
                "hard_stop_min_deg": 0, "hard_stop_max_deg": 270,
                "test_angle_a_deg": 0, "test_angle_b_deg": 90
            },
            "brake_servo": {
                "range_deg": 180, "direction": "neg", "speed_deg_per_s": 30,
                "pulse_min_us": 500, "pulse_max_us": 2500,
                "hard_stop_min_deg": 0, "hard_stop_max_deg": 180,
                "test_angle_a_deg": 10, "test_angle_b_deg": 170
            }
        }
    },
    "hatches": [
        {
            "name": "Hatch 1",
            "short_name": "Hatch 1",
            "pwm_freq_hz": 50,
            "hatch_servo": {
                "range_deg": 270, "direction": "neg", "speed_deg_per_s": 30,
                "pulse_min_us": 500, "pulse_max_us": 2500,
                "hard_stop_min_deg": 120, "hard_stop_max_deg": 150,
                "mount_angle_deg": 120
            },
            "brake_servo": {
                "range_deg": 180, "direction": "neg", "speed_deg_per_s": 30,
                "pulse_min_us": 500, "pulse_max_us": 2500,
                "hard_stop_min_deg": 0, "hard_stop_max_deg": 180,
                "mount_angle_deg": 90, "open_deg": 85, "close_deg": 95
            },
            "positions": [
                {"hatch_deg": 120}, {"hatch_deg": 135}, {"hatch_deg": 150}
            ]
        }
    ]
}
```

Note: the internal field names remain `hatch_servo`/`brake_servo` in the JSON schema (matching the code), even though the web UI and this document use the terms "lever servo"/"latch servo" for them (see 9.2b and 10.2 for the naming rationale).

**Field reference per servo block** (`hatch_servo`/`brake_servo`, both per hatch and in `global.servo_tester`):

| Field | Meaning | Allowed range |
|---|---|---|
| `range_deg` | Total servo travel | {180, 270, 360} |
| `direction` | Rotation direction: `"neg"` (0° → `pulse_max_us`) or `"pos"` (0° → `pulse_min_us`) | {"pos", "neg"}, default `"neg"` |
| `speed_deg_per_s` | Peak speed (see 3.5) | 30–180 |
| `pulse_min_us`/`pulse_max_us` | Pulse-width limits in µs | 500–4000, `min < max` |
| `hard_stop_min_deg`/`hard_stop_max_deg` | Software end stops in degrees – never exceeded | 0…`range_deg`, `min < max` |

**Additional fields only in `hatch_servo` (per hatch):**

- `mount_angle_deg`: mounting angle (see 3.7), must lie within `[hard_stop_min_deg, hard_stop_max_deg]`

**Additional fields only in `brake_servo` (per hatch):**

- `mount_angle_deg`: latch servo mounting angle
- `open_deg`/`close_deg`: regular operating positions of the brake
- all three must lie within `[hard_stop_min_deg, hard_stop_max_deg]` of this block

**Additional fields only in `global.servo_tester.hatch_servo`/`brake_servo`:**

- `test_angle_a_deg`/`test_angle_b_deg`: the two freely chosen test angles between which the respective tester channel can be moved back and forth; must lie within its own hard stops

**`pwm_freq_hz`** (range roughly 50–400Hz, see `PWM_FREQ_MIN_HZ`/`PWM_FREQ_MAX_HZ`) lives **per hatch** (not per servo!), plus once for `global.servo_tester` – rationale in 9.11 (PCA9685 frequency architecture).

**`positions`** (lever servo only, per hatch): list `[{"hatch_deg": N}, ...]`, at least 2 entries. `positions[0]` is always the closed end stop ("ZU"/closed), `positions[-1]` is always the open end stop ("AUF"/open) – these two always stay at the first/last position of the list when intermediate positions are inserted/removed (see 10.2).

**`config.json.template`** in the `source/` directory serves as a conservative template. `config_migration.py` uses it at boot to fill in missing or renamed fields automatically (see 9.16).

**Removed** (from earlier config iterations, no longer part of the schema): global `hatch_servo`/`brake_servo` blocks (now per hatch), `SERVO_DIRECTION_REVERSED` as a fixed code constant (now a `direction` field per servo), `HATCH_STEP_US`/fixed step size (now a time-based S-curve ramp, see 9.11), `delay_after_brake_change_cmd_ms`/`delay_after_move_ms` (replaced by actually waiting for the ramp to finish).

### 7.2 `/wifi.json` – Wi-Fi Credentials

```json
{"ssid": "MyNetwork", "password": "MyPassword"}
```

Written via AP mode. Lives on internal flash, not on SD. Not in the Git repo.

### 7.3 `/state.json` – Last Known Servo State

```json
{
    "hatches": [
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0}
    ],
    "servo_tester": {"hatch_deg": 0, "brake_deg": 0}
}
```

Consistently in degrees, like `config.json`. Conversion to µs happens only locally in `task_control.py`, immediately before driving the PCA9685.

- `hatches[i].hatch_deg`: last confirmed actual angle of the lever servo in degrees, or `FAILSAFE_DEG` (-1) if the position is unknown (fail-safe state or power loss mid-move).
- `hatches[i].brake_deg`: last confirmed actual angle of the latch servo in degrees. Always a real angle, never `FAILSAFE_DEG`.
- `hatches[i].target_deg`: last commanded target angle in degrees, or `FAILSAFE_DEG` (-1) in the passive fail-safe rest state. Persisting this makes a target/actual mismatch after a power loss detectable and lets it be automatically resumed on the next boot.
- `servo_tester.hatch_deg`/`brake_deg`: last known actual angles of the two tester outputs, always real angles, no target value (the tester never moves on its own) – kept only so a test move ramps from the correct starting angle after a reboot instead of a stale value.

Not present on the very first boot → `state.py` builds a default state from `config.json`: all hatches at `hatch_servo.hard_stop_min_deg` (lever), `brake_servo.close_deg` (brake, each hatch's own), target = actual. The servo tester starts at `hard_stop_min_deg` of both of its own servo blocks. A `state.json` missing the `servo_tester` key or any required field (including old, now-superseded schemas) is treated as invalid and also recreated.

**Important:** actual positions are output to the PCA9685 before every relay-on. At `FAILSAFE_DEG`, `positions[0]` is used as the lever-servo fallback, so no jerk occurs.

---

## 8. Startup Sequence (`main.py`)

```
Stage 0:  WDT reset detection
          -> machine.reset_cause() == WDT_RESET?
          -> yes: init_hw_failsafe() + fail_safe_close_all() before anything else

Stage 1:  Initialise the display early (init_display_early)
          -> status messages on the OLED from here on

Stage 1b: AP mode - if the button was held down at power-on (checked at
          the very top of main.py, before all further imports, see 9.1)
          -> start_ap_mode() + a minimal task_webserver() call with
          empty but correctly shaped placeholders for config/state/
          targets, then return (the device restarts after Wi-Fi
          credentials are saved; this code path is never regularly left)

Stage 2:  Initialise remaining hardware (init_hardware)
          -> PCA9685, relay, button
          -> error -> fatal_error()

Stage 2b: config_migration.migrate_config() -> ensures config.json
          exists and is complete
          Load config.json (load_config) -> error -> fatal_error()
          Load state.json (load_state)
          -> error -> create_default_state() + save_state()
          On WDT reset: mark all hatches FAILSAFE_DEG (-1) in state
          + save_state() (the physical fail-safe move already ran in
          Stage 0; the servo tester has no fail-safe concept and is
          left untouched)

Stage 3:  Wi-Fi
          -> start_client_mode()

Stage 4:  Build the targets dict from the persisted target (see 9.11
          for the full structure with hatches/brake_only/tester)
          uasyncio.gather() - start all tasks:
          task_watchdog, task_display, task_control,
          task_button, task_webserver
```

---

## 9. Module Descriptions

### 9.1 Top of `main.py` (Originally Planned as `boot.py`)

Runs right at the start of `main.py`, **before** all other imports (only `gc`, `network`, `uasyncio`, `machine`, `Pin`, `time` and `app.hw_init` are imported at this point):

```python
def _button_pressed():
    from app.hw_config import BTN_USER_PIN
    btn = Pin(BTN_USER_PIN, Pin.IN, Pin.PULL_UP)
    if btn.value() == 0:
        time.sleep_ms(50)
        return btn.value() == 0
    return False

AP_MODE = _button_pressed()
```

Reads the user button once with simple debouncing (50ms). `AP_MODE` then controls, in `main()` Stage 1b, whether the Wi-Fi setup mode is started instead of normal operation.

**AP mode:** hold the button down **while powering on**.

### 9.2 `app/hw_config.py`

All hardware constants. No imports of hardware libraries – importable from anywhere.

Important constants:

```python
I2C_SDA_PIN = 5
I2C_SCL_PIN = 6
I2C_FREQ    = 1_000_000  # see 2.2b for the rationale

I2C_ADDR_OLED    = 0x3C
I2C_ADDR_PCA9685 = 0x7F
I2C_ADDR_RELAY   = 0x11
I2C_ADDR_RTC     = 0x51

BTN_USER_PIN = 2
BTN_DEBOUNCE_MS       = 50
BTN_CONFIG_UNLOCK_MS  = 60_000     # 60s unlock window after button press
BTN_CONFIG_SESSION_MS = 3_600_000  # 1h session
BTN_LONG_PRESS_MS     = 3_000      # short-vs-long press threshold

PCA9685_FREQ_HZ = 50   # boot default, see 9.11 for the runtime switch

SERVO_MIN_US = 500     # absolute hardware limit for pulse_min_us/pulse_max_us
SERVO_MAX_US = 4000    # of every servo, regardless of the configured range_deg

RANGE_DEG_OPTIONS   = (180, 270, 360)
SERVO_RANGE_DEG_MAX = 360   # upper sanity bound for degree values in state.json,
                             # independent of whichever range_deg is configured

SPEED_MIN_DEG_PER_S = 30
SPEED_MAX_DEG_PER_S = 180

PWM_FREQ_MIN_HZ = 50
PWM_FREQ_MAX_HZ = 400

FAILSAFE_DEG = -1      # sentinel: position unknown (fail-safe state)

MIN_STEP_MS = 8         # practical minimum update rate of the movement ramp
                         # (see 9.11); actual interval used:
                         # max(MIN_STEP_MS, PWM period of the configured
                         # pwm_freq_hz) - the 8ms floor is only reached
                         # at pwm_freq_hz >= 125Hz

NUM_HATCHES  = 4

PCA9685_CH = {0:(0,1), 1:(2,3), 2:(4,5), 3:(6,7)}   # per hatch: (lever, latch)
TESTER_CH  = (8, 9)                                  # servo tester: (lever, latch)
RELAY_CH   = {0:0, 1:1, 2:2, 3:3}

BRAKE_TRAVEL_MS        = 500   # wait time for the brake servo in fail-safe
BRAKE_OPEN_US_FAILSAFE = 1200  # fallback value with no config.json (raw µs
                                # value, independent of the degree config -
                                # failsafe.py doesn't need a readable config.json)
```

### 9.2b `shared/servo_units.py`

Pure conversion math, no hardware import, importable everywhere:

```python
deg_to_us(deg, pulse_min_us, pulse_max_us, range_deg, direction)  # -> int µs
us_to_deg(us, pulse_min_us, pulse_max_us, range_deg, direction)   # -> float degrees
```

`direction` has been a **parameter** since the per-channel redesign, not a fixed code constant anymore (it used to be `SERVO_DIRECTION_REVERSED` in `hw_config.py`, uniform for all servos): `"neg"` means increasing pulse width corresponds to decreasing angle (0° → `pulse_max_us`); `"pos"` is the opposite (0° → `pulse_min_us`).

```python
us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
# direction == "neg":
us = pulse_max_us - deg * us_per_deg
# direction == "pos":
us = pulse_min_us + deg * us_per_deg
```

Used exclusively in `task_control.py`, to convert the configuration/state held in degrees to µs immediately before driving the PCA9685. `us_to_deg()` exists as the symmetric counterpart, but is currently never called anywhere.

### 9.3 `shared/pca9685.py`

Hand-written driver, built directly from the datasheet. No external import needed.

**API:**

```python
pwm = PCA9685(i2c, address=0x40)
pwm.freq(50)                      # set PWM frequency (chip-wide, all 16 channels)
pwm.set_pwm(ch, on, off)          # raw 12-bit values
pwm.set_us(ch, us, freq_hz=50)    # pulse width in µs
pwm.set_all_off()                 # all channels off (safe state)
```

**Critical implementation point – `set_us()`'s `freq_hz` parameter:** the driver **cannot read back** the frequency actually programmed into the chip – `set_us()` converts microseconds to tick values based on the passed `freq_hz` argument (default 50). If `freq_hz` isn't passed explicitly, or doesn't match the value last set via `freq()`, `set_us()` silently computes a wrong pulse width – with no error of any kind. **Every caller working with a frequency other than 50Hz must pass the same `freq_hz` on every single `set_us()` call that was previously set via `freq()`.** `task_control.py` adheres to this strictly (see 9.11); `failsafe.py` forces 50Hz explicitly before every drive, because at the time the fail-safe sequence runs, it's unknown which frequency the chip was last running at (see 9.7).

**Further implementation details:**

- The ALLCALL bit is **not** set in `_reset()` (setting it would make the PCA9685 also listen on the broadcast address 0x00 → interference with other I²C devices)
- Prescaler formula correct per datasheet: `round(osc_clk / (4096 * hz)) - 1`
- Sleep time after oscillator wake-up: 1 ms (datasheet: min. 500 µs)

### 9.4 `shared/grove_relay_4chn_spdt.py`

Driver for the Grove 4-Channel SPDT Relay (STM32-based, I²C).

**I²C protocol:**

- Only relevant command: `CMD_CHANNEL_CTRL = 0x10` + state byte (bitmask: bit 0–3 = channel 1–4)
- No hardware read-back possible → software state held in RAM
- Board designed for 5V logic; experience shows it also works at 3.3V; if not, use a level shifter

**API (0-based channel indices):**

```python
relay = GroveRelay(i2c, address=0x11)
relay.on(channel)    # energise relay (close NO contact -> servos powered)
relay.off(channel)   # de-energise relay (open NO contact -> servos unpowered)
relay.all_off()      # safe state on init
relay.all_on()       # caution: exceeds PSU rating
relay.is_on(channel) # query software state
relay.get_firmware_version()  # read FW byte from the board
```

### 9.5 `app/hw_init.py`

Three initialisation functions:

```python
init_hw_failsafe()      # I2C + PCA9685 + relay only (no display/button)
                        # -> for fail-safe after a WDT reset

init_display_early()    # I2C + display only
                        # -> returns (i2c, display)

init_hardware(i2c=None, display=None)  # everything: PCA9685, relay, display, button
                                       # reuses i2c/display if provided
```

All three raise `HardwareInitError` if an I²C device fails to respond (`_check_i2c_device`, which writes directly to addresses above 0x77 instead of using `i2c.scan()`, see 15).

`HardwareBundle` is the return type, with fields: `i2c`, `pwm`, `relay`, `display`, `btn`.

### 9.6 `app/state.py`

Manages `config.json` and `state.json`.

**API:**

```python
load_config()              # loads + validates config.json -> ConfigError if missing/invalid
validate_config(data)      # validates a dict without a file (for POST /api/config)
load_state()                # loads + validates state.json -> StateError if missing/invalid
save_state(state)           # writes state.json -> StateError on write failure
create_default_state(cfg)   # builds a safe initial state from config
```

**Validating config.json** (see also 7.1 for the field reference):

- Structure: `global.servo_tester` (with `pwm_freq_hz`, `hatch_servo`, `brake_servo`) plus exactly `NUM_HATCHES` entries in `hatches`, each with `name`, `short_name`, `pwm_freq_hz`, `hatch_servo`, `brake_servo`, `positions`
- Each servo block (`_validate_servo_block`): `range_deg` ∈ {180,270,360}, `direction` ∈ {"pos","neg"}, `speed_deg_per_s` ∈ [30,180], `pulse_min_us < pulse_max_us` both within [`SERVO_MIN_US`, `SERVO_MAX_US`], `hard_stop_min_deg < hard_stop_max_deg` both within [0, `range_deg`]
- Additional angles (`mount_angle_deg`, `open_deg`/`close_deg`, `test_angle_a/b_deg`) depending on block type, always checked against that **same** block's own hard stops (`_check_within_hard_stops`)
- `positions`: at least 2 entries, each `hatch_deg` within the associated `hatch_servo`'s hard stops
- `pwm_freq_hz` (per hatch and for the tester) ∈ [`PWM_FREQ_MIN_HZ`, `PWM_FREQ_MAX_HZ`]

**Important side effect of the instant-persistence model (see 10.2):** since every single field, on its own, immediately validates the **entire** document (there is no batch-save that could validate several changes together anymore), **shrinking** a `range_deg`/hard stop creates an ordering dependency: a dependent value (a position, mounting angle, test angle, `open_deg`/`close_deg`) that still lies outside the new, smaller range must be adjusted first, before the range itself can be shrunk – otherwise validation fails with a message about the dependent field, not necessarily the field currently being edited. This is not a bug, but a deliberately accepted consequence of the instant-save design.

`hatch_deg` and `target_deg` in `state.json` may contain `FAILSAFE_DEG` (-1); in that case the range check for that field is skipped. `brake_deg` as well as both `servo_tester` angles are always real angles and are always checked (sanity bound [0, `SERVO_RANGE_DEG_MAX`], independent of `config.json`).

**First startup / outdated format:** `load_state()` raises `StateError` → `create_default_state(config)` + `save_state()`.

### 9.7 `app/failsafe.py`

Synchronous function (no asyncio) – safe to call before the event loop starts.

```python
fail_safe_close_all(hw, brake_open_us=None)
```

Sequence:

1. **`hw.pwm.freq(50)`** – forces a known frequency. Runs in Stage 0, before `config.json` is readable; the PCA9685 might still be at any previously configured `pwm_freq_hz` at this point (the chip stays powered across an ESP32 reset and keeps its `PRE_SCALE` register). Without this forced reset, `set_us()` (see 9.3) would compute a wrong tick conversion and drive the brake to a wrong position – in precisely the most safety-critical code path.
2. For each hatch, sequentially: set the brake servo to `brake_open_us` (with `freq_hz=50` explicitly)
3. Relay on
4. Wait `time.sleep_ms(BRAKE_TRAVEL_MS)` (brake servo moves)
5. Relay off → the hatch closes under gravity; the brake servo is left unpowered in the "open" state (spring force isn't enough to move the servo past the cam → the hatch stays unlocked)
6. 200ms pause before the next hatch (to protect the PSU)

If `brake_open_us=None` → falls back to `BRAKE_OPEN_US_FAILSAFE` from `hw_config.py` (works without `config.json`; no current call site passes a different value).

### 9.8 `app/wifi.py`

```python
load_wifi_config()               # reads /wifi.json -> WifiConfigError
save_wifi_config(ssid, password)  # writes /wifi.json + machine.reset()
start_client_mode(hw)             # async, connects to Wi-Fi, 15s timeout
start_ap_mode(hw)                 # async, starts AP "Dachluke-Setup" (open)
```

If `wifi.json` is missing in `start_client_mode`: error message on the display, continues without Wi-Fi. Requires a reboot with the button held for AP mode.

AP SSID: `"Dachluke-Setup"`, no password, IP: 192.168.4.1 (ESP32 default).

Status lines during connection setup are written directly via `_display_line(hw, line, text)` (`line * 8` px) – independent of the 16px line grid `task_display.py` uses in normal operation (see 9.13). Since `task_display` only starts after boot completes and redraws the entire display on its first regular refresh, this causes no conflict; the boot messages are simply overwritten.

### 9.9 `app/session.py`

Shared state between `task_button` and `task_webserver`/`task_display`. RAM only, no persistence.

```python
unlock_config()       # starts the 60s unlock window (long button press)
activate_session()    # starts the 1h session (config page accessed)
is_unlock_active()    # True during the 60s window
is_session_active()   # True during the 1h session
show_qr()              # starts the 15s QR display (short button press)
is_qr_active()         # True during those 15s
```

Time base: `time.time()` (seconds since epoch).

### 9.10 `app/task_watchdog.py`

```python
async def task_watchdog():
    wdt = WDT(timeout=4000)  # 4-second hardware WDT
    while True:
        wdt.feed()
        await uasyncio.sleep_ms(200)  # feed every 200ms
```

**Why 200ms:** cooperative multitasking with no priorities – frequent feeding ensures this task doesn't get starved by other ready tasks. WDT timeout (4s) >> feed interval (200ms) → large margin.

**Coding rule:** every `while` loop in the entire project **must** contain at least one `await`, so `task_watchdog` always remains reachable.

**WDT reset detection in `main.py`:**

```python
if machine.reset_cause() == machine.WDT_RESET:
    hw_fs = init_hw_failsafe()
    fail_safe_close_all(hw_fs)
```

**Historical note – heartbeat diagnostic removed:** while debugging a servo speed wobble, `task_watchdog` was temporarily extended with a heartbeat logged roughly every 5s (`gc.mem_free()`), to tell whether the event loop was truly hung or only the network/socket layer was affected. Reverted to the simple version after the debugging was complete.

---

### 9.11 `app/task_control.py`

The heart of the application. Runs as an endless loop, sequentially comparing on every pass: all 4 hatches (`targets["hatches"][i]["hatch_deg"]` vs. `state["hatches"][i]["hatch_deg"]`), then all 4 pending latch-servo-only moves (mounting angle), then the 2 pending servo tester moves. `config`, `state` and `targets` are consistently in degrees; conversion to µs (`shared/servo_units.py`, `deg_to_us()`) happens only locally at the start of each movement function. **All I²C write access to the PCA9685 happens exclusively in this one task** – including for the latch-servo mounting angle and the servo tester (no direct hardware access from `task_webserver.py`).

#### PWM Frequency Architecture

The PCA9685 has **a single, chip-wide `PRE_SCALE` register** – no per-channel frequency is possible in hardware. Therefore:

- **One frequency per hatch** (`hatches[i].pwm_freq_hz`), shared by the lever and latch servo of that hatch, since both are powered simultaneously throughout the entire movement sequence and could never have different frequencies active at the same time anyway.
- **One further, shared frequency for both servo tester outputs** (`global.servo_tester.pwm_freq_hz`), independent of the 4 hatches.
- `_ensure_pwm_freq(hw, freq_hz)` reprograms the chip immediately before every move to the appropriate frequency – but only if it differs from the last one set (`_current_pwm_freq` module cache), to avoid unnecessary reprogramming.
- **Deliberately no safeguard** against a running hatch move disturbing the servo tester's frequency (or vice versa): the tester is only meant for servos removed from the hatches, so incorrect behaviour during an accidental simultaneous hatch move is considered harmless.

**Critical ordering:** `_ensure_pwm_freq()` must run before **every** `set_us()` call of the respective move – both because `set_us()`'s tick computation depends on the currently valid frequency (see 9.3), and because `pwm.freq()` itself briefly resets the chip, which would disturb outputs already written if called *after* them.

#### Time-Based S-Curve Ramp (`_ramp`)

```python
def _ramp_duration_ms(distance_deg, speed_deg_per_s):
    return 1.5 * abs(distance_deg) / speed_deg_per_s * 1000

def _tick_interval_ms(freq_hz):
    return max(MIN_STEP_MS, round(1000 / freq_hz))

async def _ramp(hw, channel, start_us, target_us, duration_ms, freq_hz):
    tick_ms    = _tick_interval_ms(freq_hz)
    start_tick = time.ticks_ms()
    distance   = target_us - start_us
    while True:
        elapsed = time.ticks_diff(time.ticks_ms(), start_tick)
        if elapsed >= duration_ms:
            hw.pwm.set_us(channel, target_us, freq_hz)
            return target_us
        frac  = elapsed / duration_ms
        eased = frac * frac * (3 - 2 * frac)   # smoothstep: 3f^2 - 2f^3
        current_us = round(start_us + distance * eased)
        hw.pwm.set_us(channel, current_us, freq_hz)
        await uasyncio.sleep_ms(tick_ms)
```

Two important design decisions are embedded here, both arising from concrete debugging sessions with the real servo:

**1. Time-based, not step-count-based.** The commanded position at each tick is computed from the **actually elapsed time**, not from a running step counter. `task_display` (OLED refresh, see 9.13) and `task_webserver` share the same I²C bus and event loop with this task; a tick delayed by another task automatically catches up the missed distance on the next tick, instead of the delay becoming a permanent gap in the motion. Cause and symptom were both measured directly: a step-count-based ramp profile recorded via debug log showed a timestamp gap of 80–100ms roughly every 330–500ms, exactly in the rhythm of the (at the time unconditional, every-cycle) OLED redraw – see 9.13 for the corresponding fix on the display side. After both fixes (time-based ramp **and** conditional redraw), the timing grid was consistently even (19–31ms between ticks at `pwm_freq_hz=50`).

**2. Smoothstep profile instead of linear interpolation.** `eased = 3f² − 2f³` gives continuous velocity that is exactly zero at `f=0` and `f=1` – no jerk at the start or end of a move. Since a smoothstep profile's peak velocity (at the motion's midpoint) is 1.5 times the average velocity, `_ramp_duration_ms()` extends the naive constant-velocity duration by the same factor of 1.5 – this keeps the configured `speed_deg_per_s` exactly the actual peak velocity, not a (higher) average velocity, and the configured speed limit (30–180°/s) is never exceeded anywhere in the motion.

`_tick_interval_ms()` additionally floors the update rate at both `MIN_STEP_MS` (8ms) **and** at least one full PWM period of the currently configured `pwm_freq_hz` – writing faster than the chip actually outputs a full PWM cycle would mean some intermediate steps are never output as a complete pulse at all, while others linger twice as long: an irregular pattern that feels like repeated acceleration/deceleration, even though the software intends a smooth ramp. At 50Hz (default) this gives a 20ms update interval; the 8ms floor is only reached at `pwm_freq_hz` ≥ 125Hz.

#### Movement Sequence `_move_hatch` (Full Hatch Move)

| Step | What happens |
|---|---|
| `step_mark_failsafe` | Set `hatch_deg` to `FAILSAFE_DEG` and persist → on a power loss, the sentinel remains in `state.json`; the next boot detects the interrupted move |
| `step_preset_servos` | Latch servo to its **own current actual position** (not `open_deg` - avoids a jerk on relay-on, since it only starts ramping once the lever servo has taken up the holding torque); lever servo to **0µs** (no valid signal), so the servo controller doesn't see a signal during its own boot process |
| `step_set_pwm_freq` | `_ensure_pwm_freq()` to this hatch's `pwm_freq_hz` – **before** the preset writes above (see above) |
| `step_relay_on` | Relay on → both servos of this hatch powered |
| (wait `_RELAY_ON_TO_TORQUE_MS` = 200ms) | Time until the lever servo has taken up the holding torque, before the brake is allowed to release. No wiggle step needed anymore (see 3.4) - the S-curve ramp already starts at zero speed |
| `step_ramp_brake_open` | Latch servo ramps from its actual position to `open_deg` (its own `speed_deg_per_s`) - no pause afterwards |
| `step_ramp_hatch` | Lever servo ramps to the target position (its own `speed_deg_per_s`), clamped to `[hard_stop_min_deg, hard_stop_max_deg]` |
| (wait `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS` = 200ms) | Time to settle at the target before the brake gets the close command |
| `step_ramp_brake_close` | Latch servo ramps from `open_deg` to `close_deg` |
| `step_relay_off` | Relay off, immediately once the brake-close ramp finishes - always in the `finally` block, regardless of outcome |
| `step_update_state` | Update state (`hatch_deg`, `brake_deg`, `target_deg`) + `save_state()` |

Since the latch servo now also ramps (instead of jumping in one step), it is precisely known when it has physically finished - this lets two previous, purely estimated fixed wait times (`_BRAKE_OPEN_TO_HATCH_MOVE_MS`, `_BRAKE_CLOSE_TO_RELAY_OFF_MS`) be removed without replacement.

**Module constants** (defined directly at the top of `task_control.py`, not in `config.json`, not web-configurable):

| Constant | Value | Meaning |
|---|---|---|
| `_PRESET_DELAY_MS` | 250 | Wait after relay-on before the first valid PWM signal; 0 = disabled |
| `_RELAY_ON_TO_TORQUE_MS` | 200 | Relay-on until the lever servo has taken up torque |
| `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS` | 200 | Target reached until the brake closes |
| `_MAX_MOVE_RETRIES` | 3 | See error handling below |

**Hard-stop enforcement:** the target value is clamped to `[hard_stop_min_deg, hard_stop_max_deg]` of **this hatch** before the sequence and written back into `targets`.

#### `_move_brake_only` (Latch Servo Mounting Angle)

A simplified variant without the full choreography – moves **only** the latch servo directly:

```
step_preset_servos:  lever servo to its current actual position (hold, don't move),
                     latch servo to its current actual position
step_set_pwm_freq
step_relay_on
step_preset_delay    (shared for both channels, since they're on the same relay)
step_ramp_brake:     latch servo ramps to the target position (its own speed)
step_relay_off:      immediately after the ramp finishes
step_update_state:   only brake_deg updated, hatch_deg/target_deg unchanged
```

The lever servo therefore stays powered (shared relay), but is only used to hold its current position, not moved - **no** wiggle-like mechanism for it, since nothing is meant to move there anyway.

#### `_move_tester_servo` (Servo Tester)

Even simpler - **no relay** (tester channels are permanently powered, see 2.3):

```python
async def _move_tester_servo(hw, config, state, which, target_deg):
    # which: "hatch" or "brake"
    st = config["global"]["servo_tester"]
    servo_cfg = st[f"{which}_servo"]
    channel   = TESTER_CH[0 if which == "hatch" else 1]
    ...
    _ensure_pwm_freq(hw, st["pwm_freq_hz"])
    await _ramp(hw, channel, current_us, target_us, duration_ms, st["pwm_freq_hz"])
    state["servo_tester"][f"{which}_deg"] = target_deg
    save_state(state)
```

#### `targets` Structure

```python
targets = {
    "hatches":    [{"hatch_deg": N}, ...],   # 4 entries, triggers _move_hatch
    "brake_only": [None, None, None, None],  # per hatch; a set value triggers
                                              # _move_brake_only and is reset to
                                              # None afterwards (success or failure)
    "tester": {"hatch_deg": None, "brake_deg": None},  # analogous for the servo tester
}
```

Built from the persisted target on startup (`main.py` Stage 4):

```python
targets = {
    "hatches":    [{"hatch_deg": h["target_deg"]} for h in state["hatches"]],
    "brake_only": [None for _ in state["hatches"]],
    "tester":     {"hatch_deg": None, "brake_deg": None},
}
```

`task_webserver.py` sets values in `targets`, `task_control` reads them and runs the appropriate movement function - never the other way around, no direct hardware access from the web server task.

#### Error Handling and Retry Limiting

Exceptions from the three movement functions are caught in the main loop and logged to the serial console; the loop continues with the next hatch/channel. The relay is always safely off due to the respective `finally` blocks.

**Important:** a failed `_move_hatch` call leaves `state["hatch_deg"] == FAILSAFE_DEG` while `targets` still holds the original target - the mismatch condition therefore stays permanently true, and without a countermeasure `_move_hatch` would be retried on every loop pass (including another `save_state()` flash write on every attempt - which, with sufficiently frequent failures, could affect network latency and wear the flash unnecessarily). `_fail_count` (per hatch, module list `[0,0,0,0]`) counts consecutive failures; after `_MAX_MOVE_RETRIES` (3), `targets["hatches"][i]["hatch_deg"]` is reset to the current (unknown) actual value - the mismatch condition disappears, the loop gives up. A new user command can retrigger the move at any time.

Unexpected, uncaught exceptions (outside the three movement functions) bring down the event loop → WDT reset → fail-safe.

### 9.12 `app/task_button.py`

Polls the user button every 50ms. Detects a falling edge (1→0) with `BTN_DEBOUNCE_MS` debouncing. Distinguishes a short from a long press by hold duration:

- **Short press** (< `BTN_LONG_PRESS_MS` = 3s): `session.show_qr()` → OLED shows the QR code for 15s
- **Long press** (≥ 3s): `session.unlock_config()` → 60s unlock window for the config page

```python
# Pseudocode of the logic:
if falling_edge detected and debounced:
    press_start = time.ticks_ms()
    long_press = False
    while btn still pressed:
        if elapsed >= BTN_LONG_PRESS_MS:
            long_press = True
            session.unlock_config()
            wait for release
            break
        await sleep_ms(_POLL_MS)
    if not long_press:
        session.show_qr()
```

### 9.13 `app/task_display.py`

Checks every `_REFRESH_MS` (500ms) whether the display content needs to change, and only touches the I²C bus **when there is actually something new to draw**.

**Why this is more than just efficiency:** `task_display` shares the I²C bus and the event loop with `task_control` (servo control). A full redraw (framebuffer fill + `Writer` glyph rendering + the final I²C transfer in `show()`) runs with no `await` in between and blocks the event loop for its entire duration. Measured impact before this mechanism was introduced: an unconditional redraw on **every** cycle - even when nothing on screen actually changed - periodically delayed the scheduled wake-up of the servo ramp in `task_control`, which manifested as a real, physical speed wobble at the servo (most visible with the servo tester, since the 4 hatch status lines never change while it's in use). The same unconditional redraw also caused a visible flicker on the QR screen (moiré banding when filming it with a phone camera at scanning distance), from repeatedly blanking and rebuilding the same QR code.

**Mechanism:** `_last_drawn` holds a fingerprint of whatever is currently actually shown on the display (including which of the two screens - status or QR - is active). Both `_refresh()` and `_render_qr()` skip their I²C work entirely when the fingerprint they would draw already matches `_last_drawn`. Because the fingerprint includes the screen mode, switching between the status and QR view always forces a redraw, even if the underlying data happened to be unchanged - the redraw is triggered purely because what's actually visible on the panel differs from what's currently drawn.

**Partial redraw during an actual hatch move:** the fingerprint comparison above doesn't help while a hatch is actually moving - then the rotating spinner (see below) changes on **every** cycle, so a full redraw would still be needed and could noticeably compete with the servo ramp for I²C bus time. For exactly this case there is a second, finer-grained mechanism: `_last_lines` caches the 4 line strings last drawn. If, for one row, **only** the 4-character status field changes (columns 96-127, the last 4 of 16 characters) - the most common case during a move - the **entire** display is **not** redrawn; instead only this small 32x16px rectangle is:

- `_show_region(display, x0, x1, page0, page1)` sets the SSD1306 address window specifically to this rectangle (`SET_COL_ADDR` 0x21 / `SET_PAGE_ADDR` 0x22 - the standard `ssd1306` driver never uses these commands, its `show()` always addresses the full 128x64 area) and transfers only the corresponding framebuffer bytes (64 instead of 1024 bytes).
- `_redraw_status_region()` first clears only this cell (`fill_rect`, to prevent ghosting between differently shaped spinner frames) and draws the new text into it.
- If the short-name part of a row also changes (e.g. editing config while a different hatch happens to be moving), the code falls back to a full redraw - this case is rare, and simplicity matters more here than further optimisation.

This mechanism bypasses the `ssd1306` driver and talks directly to its `write_cmd()`/`write_data()`/`buffer`/`width` attributes - standard behaviour of the common `micropython-lib` driver (page-major `MONO_VLSB` buffer layout), but not part of its officially stable API. With a different driver version, `_show_region()` would need re-checking.

**Normal layout** (128x64, 4 lines at 16px each, filling the entire height):

```
Line 0 (y=0):  "<short_name> <status>"   Hatch 1
Line 1 (y=16): "<short_name> <status>"   Hatch 2
Line 2 (y=32): "<short_name> <status>"   Hatch 3
Line 3 (y=48): "<short_name> <status>"   Hatch 4
```

Line format: `short_name` left-aligned in 11 characters, 1 space, then exactly 4 characters of status (always fixed width) - 16 characters total at 8px/character = exactly 128px.

**Larger font via the `Writer` class:** instead of the built-in 8x8 font of `framebuf`/`ssd1306.py` (which supports no scaling - only integer pixel doubling would be possible, rejected as too coarse), `lib/writer.py` (Peter Hinch, `micropython-font-to-py`, MIT license) is used together with `lib/font_hatch_status.py` (14px, monospace, 8px character width, see 6.2b). The `Writer` instance is created once in `task_display()` (not on every refresh). `wri.set_clip(row_clip=True, col_clip=True, wrap=False)` prevents automatic line wrapping.

**Status values, fixed 4-character width:**

| Value | Meaning |
|---|---|
| `" \| "`/`" / "`/`" - "`/`" \\ "` | target != actual (move in progress, also during fail-safe recovery) - rotating stroke, one frame per actual redraw (`_spin_frame`, only while the line is redrawn anyway) |
| `"?ZU?"` | actual == target == `FAILSAFE_DEG`: fell shut under gravity after a fail-safe sequence, unlocked |
| `"ZU  "` | position 0 in `positions` (closed) |
| `"AUF "` | last position in `positions` (open) |
| `"P2  "`...`"P5  "` | intermediate positions (1-based) |
| `"??? "` | actual position not found in `positions` (should not occur in normal operation) |

**Historical note - spinner instead of plain text:** the movement status used to be shown as the plain-text word `"BEWEGT"` ("moving", 6 characters). Since that needed more than 4 characters for the status overall, it was changed to the current fixed 4-character rotation, once it was decided that **all** status values (not just the position labels) should uniformly occupy exactly 4 characters - the short name gets the remaining 11 of a line's 16 characters as a result.

**Inverted display while the config session is active:** if `session.is_session_active()` or `session.is_unlock_active()` is true, the entire display is inverted (`display.invert(True)`) - the only on-device indication that the configuration page is currently reachable (the actual config button only appears on the web page). `config_reachable` is part of the redraw fingerprint, so a pure session-state change (with the status text otherwise unchanged) still forces a redraw.

**QR mode** (`session.is_qr_active()` = True, triggered by a short button press):

- Top area (0-47px, `qr_area_height = 64 - _IP_LINE_PX`): white background, black QR code centred
- Bottom area (48-63px, 16px): IP address as plain text (without the `http://` prefix), in the normal built-in 8px font, horizontally centred - since the IP/Wi-Fi line was removed from normal operation, this is the only place the IP is visible on the device
- URL for the QR encoding: `http://<current-IP>` (STA or AP); the separate text line shows only the bare IP (`_current_ip()`)
- The QR matrix is generated once via `uQR` and cached as long as the URL doesn't change (generation takes about 0.5s); the *display* is additionally only redrawn on an actual change, via the fingerprint mechanism described above
- On entering QR mode, `display.invert(False)` is forced, so a previously active session inversion doesn't render the QR code negatively
- Scale 1px/module + 4px quiet zone, confined to the top 48px - still enough room for typical home IPs
- Directly scannable with the iOS camera app
- If `uQR` isn't in `/lib`: fallback text `"uQR not found"` on the display

---

### 9.14 `app/task_webserver.py`

microdot web server on port 80. Module variables `_hw`, `_config`, `_state`, `_targets` are set once when the task starts (route handlers cannot receive extra parameters in microdot).

**Routes:**

| Method | Path | Function |
| ------- | --------------------- | ---------------------------------------------- |
| GET     | `/`                    | `index.html` (redirects to `/ap` if config.json failed to load) |
| GET     | `/config`              | `config.html` (activates the session if the unlock window is open) |
| GET     | `/ap`                  | `ap_setup.html` |
| GET     | `/static/<path>`       | static files from `/webpages/static/` |
| GET     | `/sse`                 | SSE stream, every 500ms |
| GET     | `/api/config`          | current `config.json` as JSON |
| POST    | `/api/hatch/<i>/pos`   | set target by position index `{position_idx: N}` |
| POST    | `/api/hatch/<i>/test`  | drive the lever servo directly `{hatch_deg: N}` - full sequence; also used for the lever servo's mounting angle (session required) |
| POST    | `/api/hatch/<i>/brake_test` | drive only the latch servo directly `{brake_deg: N}` - mounting angle, sets `targets["brake_only"][i]` (session required) |
| POST    | `/api/tester/<which>`  | drive a servo tester output, `which` in {"hatch","brake"}, `{angle_deg: N}` (session required) |
| POST    | `/api/config`           | validate the new config, persist immediately + apply in RAM - **no reset**; called again with the complete current document on every single field acceptance and every position insert/remove (session required) |
| POST    | `/api/wifi`             | save Wi-Fi credentials + reset |
| GET     | `/api/wifi/scan`        | Wi-Fi scan → JSON `{networks: [{ssid, rssi}]}` |

**`POST /api/config` - atomic write:**

```python
with open('/config.json.tmp', 'w') as f:
    f.write(ujson.dumps(new_cfg))
    f.write('\n')
try:
    uos.remove('/config.json')
except OSError:
    pass
uos.rename('/config.json.tmp', '/config.json')
```

`os.rename()` is atomic on the ESP32 filesystem - an interrupted write (memory error, reset, power loss) can therefore never leave a truncated `/config.json` behind (which would prevent the device from booting on the next start). `ujson.dumps()` instead of the previously used, hand-written `pretty_json()` formatting (see 9.16b) - reduces the amount of data written (roughly 2.5 instead of 5.5 KB) and avoids the large intermediate string built from many small sub-strings, which tends to fragment the small MicroPython heap. `del new_cfg` + `gc.collect()` immediately afterwards actively frees the memory of the just-written copy, instead of waiting for automatic garbage collection.

On success, `_config` is updated **in place** (`_config["global"]["servo_tester"].update(...)`, per hatch `_config["hatches"][i].update(h)`), so `task_control` and `task_display` see the change on their very next pass, without needing a reference swap.

**SSE implementation:** the `/sse` stream uses a class-based `_SseStream` class instead of an async generator, since MicroPython raises a `TypeError` in microdot's write path for async generators that combine `yield` and `await` in the same function body (the second iteration returns a non-bytes object). The class implements the `__aiter__`/`__anext__` protocol explicitly.

**Config session protection:**

- `/config`: reachable if `is_session_active()` OR `is_unlock_active()` → activates the session
- `/api/hatch/<i>/test`, `/api/hatch/<i>/brake_test`, `/api/tester/<which>`, `POST /api/config`: only if `is_session_active()`

**SSE payload** (`_build_status()`):

```json
{
  "hatches": [
    {"name": "Hatch 1", "short_name": "Hatch 1", "current_deg": 0,
     "target_deg": 0, "moving": false, "failsafe": false,
     "pos_idx": 0, "num_positions": 3}
  ],
  "wifi": {"connected": true, "ip": "192.168.8.70"},
  "session": {"active": false, "unlock": false}
}
```

Contains **no** servo tester fields - the tester has no automatic "moving" concept, `config.html` reads its values directly from `GET /api/config`.

### 9.15 `app/fatal_error.py`

Central handler for fatal boot errors. Replaces the earlier pattern of returning early from `main()`, which led to an uncontrolled WDT reboot loop.

```python
async def fatal_error(message, hw=None, display=None)
```

Sequence:
1. Attempt a fail-safe move if `hw` is available (try/except - errors are logged but ignored, so the following steps always run)
2. Permanent error message on the OLED if `display` is available (header line + message wrapped across 7 lines of 16 characters)
3. Start the "Dachluke-Setup" Wi-Fi AP, so the error page is reachable
4. Start a minimal web server on port 80 and **never return**

No WDT is started → a power cycle is the only way out. Called from `main()` in place of every `return` after a fatal error.

### 9.16 `app/config_migration.py`

Called in `main()` Stage 2b **before** `load_config()`. Ensures `config.json` exists and contains all required fields.

```python
def migrate_config()   # raises ConfigMigrationError if the template is missing
```

Logic:
- Template (`/config.json.template`) missing → `ConfigMigrationError` (fatal)
- `config.json` missing → copy the template verbatim
- `config.json` present and valid (`validate_config()` raises no error) → do nothing
- `config.json` present but invalid or with missing/renamed fields → merge: keep known values, fill in missing ones from the template, drop unknown fields (this is how fields from an earlier schema version, such as the once-flat `hatch_min_us`/`step_us` structure, get automatically removed)

The merge is recursive for nested dicts (already handles the current `global.servo_tester`, `hatch_servo`, `brake_servo` blocks generically, no special-casing needed). The `hatches` list is always processed as 4 entries. The `positions` list is taken from `config.json` if non-empty (preserves calibrated values), otherwise from the template.

**In practice:** since `POST /api/config` (see 9.14) writes a fully and immediately validated document on every single field acceptance, `config.json` is, in normal operation, practically always already valid - the merge path only kicks in right after a schema change in the code (new field in the template, old `config.json` still on the previous schema) or after data corruption.

### 9.16b `test_qr.py`

A historical, standalone test script from the early development phase, used to try out the QR code rendering before it was actually implemented in `task_display.py`. Not imported or called by any other module. **Known bug:** the black/white assignment when drawing is inverted (writes `pixel=1` for a QR module that should be black, even though on the SSD1306 a value of `1` means "white") - the production implementation in `task_display._render_qr()` does it correctly. Only runnable manually via the REPL (`exec(open('test_qr.py').read())`), not part of the regular boot/test flow.

---

## 10. Web Interface

### 10.1 `index.html` - Control Page

- Alpine.js `app()` component on `<body>`
- SSE connection to `/sse` with auto-reconnect after 3s
- One Bootstrap card per hatch with position buttons
- Button labels: `ZU`/closed (index 0), `AUF`/open (last), `P2`...`P5` (intermediate positions)
- Active position: `btn-primary`, others: `btn-outline-secondary`
- Moving: badge pulses (CSS animation)
- Config button: appears/disappears reactively, no page reload
- **Unaffected** by the per-channel redesign - only uses `moving`/`failsafe`/`pos_idx`/`num_positions`/`name`/`short_name` from the SSE payload, no field names that changed

### 10.2 `config.html` - Configuration Page

- Alpine.js `configApp()` component, loads config via `GET /api/config` on page load
- **No shared "all 4 hatches" area anymore** - each hatch is its own independent accordion item, titled by `name`
- **5th accordion item "Servo Tester"**, with a warning note (for servos removed from the hatches only; PWM frequency independent of the 4 channels)
- **Download button** (navbar): builds a `Blob` from the currently loaded/saved `buildConfig()` document, triggers a download via `<a download="config.json">` - purely client-side, no dedicated server endpoint
- **Language switcher** (navbar, DE/EN dropdown), defaults from `navigator.language`

**Channel card (per hatch), layout top to bottom:**

1. Name, short name (text fields, full width, no two-column layout)
2. PWM frequency (one field, shared by the lever **and** latch servo of this channel, see 9.11)
3. **Two columns**, separated by a thin vertical line: left "Lever servo, channel N", right "Latch servo, channel N"
   - Row 1: range (selector 180°/270°/360°), direction (selector pos/neg), speed (°/s)
   - Row 2: pulse width at 0°, pulse width at the current `range_deg`° (the label adapts dynamically to the chosen range - only the label, the values themselves stay unchanged across a range switch)
   - Row 3: hard stop min/max (°)
   - Latch servo column only, row 4: brake open/close (°) - the two regular operating positions
   - Row (lever: 4, latch: 5): mounting angle + accept + **visible gap** + "go" button (prevents mis-clicks while editing the angle value). Lever servo "go" triggers the full sequence (`/api/hatch/<i>/test`), latch servo "go" moves only that servo (`/api/hatch/<i>/brake_test`)
4. Detent positions (lever servo column only; the latch servo column stays empty there, to keep the two-column grid)

**Servo tester card:** the same two-column layout, rows 1-3 identical to the channel card, one shared PWM frequency row across both columns, then per column two test-angle rows (A and B, each with accept + gap + "go") instead of the position list/mounting angle - letting each tester channel be moved back and forth between two freely chosen angles.

**Field states** (two instead of three, since acceptance persists immediately):

- `draft !== committed` → yellow (`field-edited`) - typed, not yet accepted
- otherwise → white (accepted = already permanently saved)

**Interaction per field:** reset (undo to `committed`), input field, accept (validates locally → sends the complete current document via `POST /api/config` → on success `committed = draft`; on failure, rolls back `committed`, shows an error, `draft` stays for correction).

**`persistConfig()` - error classification:** `buildConfig()` deliberately runs outside the network `try` block (a JS error there is a client bug, not a network error). The response is always read as text first and then parsed as JSON, regardless of HTTP status - a `400` with a valid `{"error": "..."}` body (a normal validation rejection, an "operator error") is shown as a plain error message; only a response that can't be parsed as JSON at all is shown as "Server error NNN: ..." (a genuine crash/broken response).

**"Go" button:** only active when `draft === committed` (the value has already been accepted and persisted) → sends the appropriate test route directly, **without** a prior apply step.

**Positions:** `positions[0]` (closed) and `positions[length-1]` (open) are fixed endpoints and are never moved. A new intermediate position is always inserted right **before** the open endpoint, defaulting to the average of the open endpoint and the previously last intermediate position (or of the open and closed endpoints, if no intermediate position exists yet). Removing deletes the last intermediate position (index `length-2`), never the closed or open endpoint itself. Both are persisted immediately (no extra accept click needed).

**Internationalisation (i18n):** an `I18N` object with language codes `de`/`en`, each a flat dictionary of all text keys. `t(key)` reads from it. Only the web UI text (labels, buttons, legends, hint text) is translated; error messages from the device (`validate_config()`) as well as all boot/console output stay deliberately in English, regardless of the UI language (for diagnostic purposes, not meant for end users).

### 10.3 `ap_setup.html` - Wi-Fi Configuration

- Scan starts automatically on page load (`GET /api/wifi/scan`)
- Results shown as a clickable list with an RSSI bar indicator
- Click → fills in the SSID field
- Password field with a show/hide toggle
- Submit → `POST /api/wifi` → `machine.reset()`
- After the connection drops (the device has deactivated its AP): a note that the device is restarting and the IP will briefly appear on the display
- **Unaffected** by the per-channel redesign

### 10.4 Libraries (CDN with Local Fallback)

- Bootstrap 5.3.3 (`bootstrap.min.css`, `bootstrap.bundle.min.js`)
- Alpine.js 3.14.1 (`alpine.min.js`)

All three HTML pages load the libraries **primarily from the CDN** (jsdelivr.net). If the CDN is unreachable, an `onerror` handler falls back to the local copies in `source/webpages/static/`. The browser caches CDN files after the first load, after which no further network access occurs.

Alpine.js is loaded **without** `defer` at the end of `<body>`, so the respective `function appName()` is already defined by the time Alpine initialises.

---

## 11. Async Architecture

### 11.1 Tasks (all in `uasyncio.gather()`)

| Task             | Interval                       | Job                                    |
| ---------------- | -------------------------------- | -------------------------------------- |
| `task_watchdog`  | 200ms                            | Feed the hardware WDT                  |
| `task_display`   | 500ms (check; redraw only on change) | Update the OLED                    |
| `task_control`   | 10ms (after a move) / 50ms (idle); during a move: see `_ramp()`'s update interval | Target/actual comparison, movement sequences |
| `task_button`    | 50ms                              | Poll the button, start session unlock/QR |
| `task_webserver` | event-driven                     | HTTP requests, SSE stream              |

### 11.2 Coding Rule

**Every `while` loop in the project must contain at least one `await`.**

Background: cooperative multitasking with no priorities. Blocking code without an `await` prevents `task_watchdog` from running → WDT reset.

### 11.3 I²C Bus Contention Between the Display and Servo Control

`task_display` and `task_control` share the same physical I²C bus **and** the same cooperative event loop. A blocking I²C transfer in `task_display` (OLED redraw) delays `task_control`'s next scheduled continuation of its movement ramp by exactly that amount of time. With a step-count-based ramp (the earlier implementation), this added up to a **regular, physically noticeable** speed wobble at the servo. Two independent countermeasures were put in place, each complementing the other (see 9.11 and 9.13 for the respective details):

1. `task_display` only redraws when the content has actually changed (drastically less I²C traffic in normal operation, especially with the servo tester)
2. `task_control`'s ramp is time-based rather than step-count-based (a delayed tick is caught up rather than lost)

### 11.4 Hardware Watchdog

- Timeout: 4000ms
- Feed interval: 200ms
- Initialised inside `task_watchdog` (not in `main.py`)
- The WDT only becomes active once `task_watchdog` has started (i.e. after all startup stages have completed successfully)

---

## 12. Fail-Safe

**Triggers:**

- WDT reset (the program stopped responding)
- Unhandled error during startup (`fatal_error()`, see 9.15)

**Sequence** (`fail_safe_close_all`, see 9.7):

1. Explicitly force the PCA9685 frequency to 50Hz (regardless of the prior state)
2. For each hatch: set the brake servo PWM to `brake_open_us`
3. Relay on
4. `time.sleep_ms(BRAKE_TRAVEL_MS)` - the brake opens
5. Relay off - the hatch closes under gravity; the brake servo stays unpowered in the "open" position (spring force isn't enough to move the servo past the cam → the hatch stays unlocked)
6. 200ms pause → next hatch

**Why doesn't the lever servo jerk during fail-safe?** `fail_safe_close_all` only sets the brake channel to a valid PWM value. The **lever channel stays at 0µs** - no valid pulse. The servo controller detects the missing signal and doesn't engage its motor. This behaviour is deliberately mirrored in the normal movement sequence (see 9.11, `step_preset_servos`).

After a fail-safe (WDT reset): the normal startup sequence continues. In Stage 2b, all hatches are set to `FAILSAFE_DEG` (-1) in `state.json` - target and actual now match physical reality. `task_control` then stays passive for these hatches (target == actual == `FAILSAFE_DEG`). Only a new user command (a real degree position) triggers the full movement sequence including locking again. The servo tester has no fail-safe concept of its own (it never moves automatically) and is unaffected by all of this.

---

## 13. Config Session (Securing the Configuration Page)

**Goal:** the configuration page is only reachable after a physical, **long** button press on the device.

**Sequence:**

1. Press the user button **long** during normal operation (>= `BTN_LONG_PRESS_MS` = 3s) → `session.unlock_config()` → 60s window
2. Within 60s: the config button appears on the control web page
3. Clicking the config button → calls `/config` → `session.activate_session()` → 1h session
4. During the 1h: the config page is reachable via the button or a direct URL
5. After 1h: the button disappears on the next page load, direct calls are blocked

**Security level:**

- Physical access to the device required (long button press)
- Time-limited window
- All movement-triggering and writing config routes (see 9.14) require an active session

**Visibility on the device:** inverted display while the unlock window or session is active (see 9.13) - the only indication directly on the device; otherwise the status is only visible on the web page itself.

---

## 14. QR Code Display

**Purpose:** easy access to the web UI without having to type the IP address.

**Trigger:** a short button press (< `BTN_LONG_PRESS_MS` = 3s).

**Sequence:**
1. `session.show_qr()` sets `_qr_until = time.time() + 15`
2. `task_display` detects `is_qr_active()` = True and calls `_render_qr()`
3. The QR matrix is generated once via `uQR` (URL = `http://<IP>`) and cached; the *display* is additionally only redrawn on an actual change (see 9.13)
4. The OLED shows a white background with a black QR code (centred, 4px quiet zone), confined to the top 48px
5. The bare IP address (without `http://`) additionally appears in the bottom 16px as text in the normal 8px font, horizontally centred
6. After 15s, `task_display` automatically returns to normal mode

**Scannability:** directly scannable with the iOS camera app. Scale 1 (1px/module) remains sufficient for typical home IPs within the 48px-reduced QR area.

**Library:** `uQR` (JASchilz/uQR) - produces a 2D boolean matrix. True = black module, False = white module. Must be manually copied as `/lib/uQR.py` onto the device (not installable via mip).

---

## 15. Known Pitfalls & Diagnostic Knowledge

### I²C Address 0x7F Not Findable via scan()

`i2c.scan()` only covers `0x08-0x77` per the I²C specification. The Seeed Grove PCA9685 defaults to address `0x7F` (all address pads pulled high). It therefore **never** appears in scan results, but is directly addressable. Diagnosis: `i2c.writeto(0x7F, b'')` + `i2c.readfrom(0x7F, 1)` - a MODE1 register value of `0x11` means the device is responding correctly. `hw_init._check_i2c_device()` already handles this automatically (addresses above 0x77 are written to directly instead of scanned).

### Bus Short-Circuit Pattern

If `i2c.scan()` returns every address from 8 to 119 → SDA or SCL is wired wrong (e.g. pulled to GND). Not a genuine scan result, but an artefact. Correct pins for the XIAO ESP32-S3 on the expansion board: SDA=5, SCL=6.

### config.json Markdown Backticks

When copying JSON out of chat conversations, Markdown backticks (` ``` `) can end up at the end of the file. MicroPython's `ujson` then raises a `JSON parse error`. Check the file in an editor and remove the backticks.

### mpremote and Port Changes

mpremote toggles DTR/RTS on connect → hardware reset of the ESP32 → the port may change from `/dev/ttyACM0` to `/dev/ttyACM1`. If scripts then continue using the old port, they fail. Fix: briefly unplug and replug the device from USB.

### `mpremote reset` Fails with `TransportError: could not enter raw repl`

Typically occurs at the end of `upload.sh`, when the device can't cleanly switch to REPL mode after the upload - usually because an import error in one of the freshly uploaded files sends the device into a boot loop instead of quietly waiting at the REPL. The files uploaded before that point are usually unaffected (the error only occurs at the final start). Diagnosis: `mpremote connect /dev/ttyACM0` (leave it open, don't type anything) shows the Python traceback of the import error.

### The PCA9685 Survives a Plain ESP32 Reset

The PCA9685 has its own power supply and stays continuously powered across an `mpremote reset` or a software reset of the ESP32 - its `PRE_SCALE` register (PWM frequency) and all other internal state survive unchanged. After changes to PWM-/timing-related code (particularly to `pwm_freq_hz` handling), this can show up as a seemingly random "doesn't work after upload, works after a power cycle". Workaround: after such changes, do a full power cycle (USB unplug/replug) instead of just `mpremote reset`. See also 6.3.

### `Ctrl+C` Doesn't Prove Where a Hang Is - Only That the Loop *Was* Running at That Moment

A `KeyboardInterrupt` via `Ctrl+C` in an open `mpremote` connection shows a traceback of wherever the event loop happened to be *at the moment of the interrupt*. If it repeatedly lands in `asyncio/core.py`'s `wait_io_event` (the normal idle wait point between tasks), that means: the loop **is running normally** and is not hung - a randomly hit point in an actively waiting coroutine is not proof of a hang, even if the rest of the system (e.g. the web UI) seems unreachable at that moment. In that case the cause more likely lies outside the plain Python event loop (e.g. the Wi-Fi/socket layer) rather than in an infinite loop in the code itself. A permanently running, periodically logged state value (e.g. `gc.mem_free()`, see the historical note in 9.10) can distinguish this more reliably than a single timed `Ctrl+C` attempt: if the periodic log keeps running right up until a watchdog reset, the loop itself never hung.

### Instant Persistence + Range Shrinking = an Ordering Trap

See 9.6: if `range_deg` or a hard stop is shrunk while a dependent value (a position, mounting angle, `open_deg`/`close_deg`, test angle) still lies outside the new, smaller range, validation rejects the change - with an error message about the dependent field, not necessarily the one currently being edited. Always bring the dependent values into the new range first, then shrink the range itself.

### Interrupting `main.py` for REPL Access

`main.py` starts immediately and blocks the REPL via `uasyncio.run(main())` - as long as the program is running, **no** `>>>` prompt appears, no matter how often Enter is pressed (this is normal behaviour, not a bug). A `Ctrl+C` in the open `mpremote` connection cleanly interrupts the running loop and opens the prompt. Alternatively, to boot permanently without starting `main.py`:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main.py', '/main_bak.py')"
```
Renaming it back:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main_bak.py', '/main.py')"
```

### Lock Files Blocking mpremote

MicroPython Studio leaves lock files in `/tmp/`. After crashes or a hard disconnect, they remain:
```bash
rm /tmp/mps_lock__dev_ttyACM*.lock
```

---

## 16. On-Site Control Panel (Status: Not Yet Implemented)

Direct control at the device, independent of the web interface, carried over from the original design concept. **Not implemented so far** - neither the hardware installed nor the software written.

**Control concept:**

- **4 hatch buttons** (select hatch 1-4)
- **6 position buttons** (select target position 1-6)
- **6 LEDs** (one next to each position button)

Operation: press a hatch button → the LEDs show that hatch's current actual position. Press a position button → the hatch moves to the selected position (sets `targets["hatches"][i]["hatch_deg"]` the same way `task_webserver` does - no change to `task_control.py` needed).

**Planned hardware - MCP23017 (I²C GPIO expander, address 0x20):**

- 16 GPIO total, freely configurable as input/output
- 10 inputs (buttons, internal pull-ups usable)
- 6 outputs (LEDs with series resistors)
- Interrupt output available → the ESP32 can react to a button press without polling
- Planned as a breakout board with pin headers, buttons and LEDs wired directly at the board on a piece of perfboard

**Deferred until:** the need is reassessed based on experience with the web interface.

**Implementation, if built:** a new module `app/task_panel.py`, imported in `main.py`, added to `gather()`.

---

## 17. Future Extensions

### 17.1 Rain Sensor (Separate Hardware Project)

- A weatherproof electrodynamic exciter (mounted on an angled metal plate)
- Operated in reverse as a sensor: raindrop impact → plate vibration → the exciter generates a voltage pulse (low impedance, no charge amplifier needed)
- TLV3691 comparator (75 nA) as an always-on wake-up stage
- The MCU sleeps deeply, woken by the comparator output
- After waking: ADC measurement for intensity classification, send a LoRa packet, back to deep sleep
- Active cycle ~200-500ms → battery operation for months to years is realistic

**Radio:**

- LoRa (e.g. an RFM95 module) for a point-to-point link to the ESP32
- Receiver: a second RFM95 directly on the ESP32 (SPI)
- No third-party infrastructure needed, no Wi-Fi required (a Wi-Fi outage of the main device doesn't matter)
- No cable to the roof → no lightning-strike risk

**Interface to the main system:** the rain sensor delivers an intensity level (e.g. 0 = no rain, 1 = drizzle, 2 = rain, 3 = heavy rain). The main system reacts via the event/trigger system (e.g. close all hatches from level 2, only crack them open at level 1).

Connection to the main system: an additional task or webhook endpoint → sets targets via `targets["hatches"]`.

### 17.2 RTC Scheduling

The PCF8563 is already present on the expansion board (I²C 0x51). Automatic open/close at configured times → a new task + an extension of the configuration web page.

### 17.3 Event/Trigger System

A clean abstraction layer is planned for all extensions:

- Trigger sources: web UI, local buttons, rain sensor, RTC
- Action: `targets["hatches"][i]["hatch_deg"] = new_position_deg`
- New sources can be added without reworking the core logic - `task_control.py` already reacts exclusively to changes in `targets`, regardless of who sets them

### 17.4 Finer Ramp Updates at High PWM Frequency

`MIN_STEP_MS` (currently 8ms) is the floor for the movement ramp's update interval, reached at `pwm_freq_hz` ≥ 125Hz (see 9.11). If even finer updates are ever needed (e.g. for particularly high-resolution or fast servos), this value could be lowered further - the trade-off is higher I²C bus load per second, which could re-aggravate the contention issue with `task_display`/`task_webserver` described in section 11.3. Only change this with a concrete need.

### 17.5 Batch Field Acceptance for the Configuration Page

Currently, every single field acceptance immediately validates the entire document (see 9.6, "ordering trap"). An optional batch acceptance (collect several drafts, then send them together) would solve this UX issue, but was deliberately not implemented, to keep the simpler instant-persistence model. Revisit if the need recurs.

---

## 18. Status / Next Steps

**Done:**

- Full hardware bring-up (I²C bus, PCA9685, relay, display, button)
- Degree-based configuration with per-channel servo calibration (range, direction, pulse widths, hard stops)
- Instant configuration persistence (no batch save, no reboot for config changes)
- Time-based S-curve movement ramp for both the lever and latch servo
- Servo tester functionality (2 additional, permanently powered PWM channels)
- Mounting angle per servo for maintenance purposes
- Per-channel PWM frequency (with documented hardware limits, see 9.11)
- Multilingual configuration UI (DE/EN)
- Config download button
- QR code display with IP text, inverted session indicator
- Fail-safe sequence including WDT reset detection
- Bugs fixed during hardware testing: PWM frequency ordering (`_ensure_pwm_freq` before `set_us()`), I²C bus contention between the display and servo (conditional redraw + time-based ramp), atomic config writes, memory fragmentation during config writes, various docstring/comment inconsistencies from the various redesign stages

**Open:**

1. **Further hardware testing under real load** (long-term test, particularly the PWM frequency contention between the servo tester and hatch operation, see 9.11)
2. **On-site control panel:** pending a needs assessment (see 16)
3. **Unit tests** (on hold): worthwhile for `state.py` validation and `pca9685.py`'s calculations - after the API stabilises further
4. **Rain sensor:** a standalone prototyping project (see 17.1)
5. **End-user documentation** for end users/re-users - planned as a separate document, building on this one
