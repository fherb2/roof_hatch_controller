# Roof Hatch Controller

Web-controlled, servo-driven roof hatch (skylight) controller for up to 4 hatches, built on a Seeed XIAO ESP32-S3 running MicroPython.

Each hatch is opened and closed by a **lever servo**, and held in place by a separate **latch servo** against a spring-loaded, non-self-locking gear train. A responsive, mobile-friendly web interface lets you drive hatches to configurable rest positions, while a full-featured configuration page handles per-channel servo calibration, maintenance angles, and a standalone servo tester for bench work.

This repository documents a finished, working home-automation project rather than a general-purpose library. Reusing the code as-is on different hardware is unlikely to make sense without adaptation — see the [Documentation](#documentation) section below for everything you'd need to adapt it.

---

## Table of Contents

- [Features](#features)
- [Hardware](#hardware)
- [How It Works](#how-it-works)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Safety Design](#safety-design)
- [Status & Roadmap](#status--roadmap)
- [Documentation](#documentation)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Features

**Control**
- Web UI for up to 4 hatches, live status via Server-Sent Events (no polling, no page reloads)
- Per-hatch, per-servo-configurable detent/rest positions (2–6 positions, closed and open endpoints always fixed)
- Smooth, jerk-free **S-curve (smoothstep) motion profile** for both the lever and latch servo — zero velocity at the start and end of every move
- Time-based movement ramp that self-corrects for scheduling jitter, instead of a naive fixed-step ramp (see the implementation docs for the story behind this)

**Configuration**
- Fully per-channel servo calibration: mechanical range (180°/270°/360°), rotation direction, peak speed, pulse-width limits, hard stops — no shared/global servo parameters
- **Instant persistence**: every accepted field is validated and written to flash immediately, atomically (no "Save" button, no reboot required, power-loss safe)
- **Mounting angle** per servo for convenient positioning during physical installation/maintenance
- **Servo tester**: two extra, permanently powered PWM outputs for bench-testing servos before they're installed, independent of the 4 hatch channels, including their own PWM frequency
- Per-channel PWM frequency (50–400Hz), correctly handled despite the PCA9685's single chip-wide frequency register
- One-click configuration download (client-side, as a JSON file)
- Bilingual configuration UI (German/English), easy to extend to further languages

**Reliability & Safety**
- Hardware watchdog with an automatic fail-safe sequence: on a hang, all hatches release their brakes and close under gravity, ending in a known, unlocked state
- Power-loss detection: an interrupted move is recognised and can be resumed after reboot
- Servos are only ever powered during an active move, never continuously — protects the hardware from thermal overload, protected further by a relay per hatch
- Only one hatch is ever powered at a time, guaranteeing the power supply is never overloaded
- Extensive input validation on every configuration change, with clear, field-specific error messages

**Access & Diagnostics**
- Wi-Fi setup via a temporary access point (no hard-coded credentials, no serial console needed for first boot)
- QR code shown on the OLED display (short button press) for quick phone access to the web UI, without typing an IP address
- Config page access gated behind a physical, long button press plus a time-limited session — no config exposure over the network by default
- Compact OLED status display (4 lines, one per hatch) using a larger custom-generated font, with an animated "in motion" indicator
- Careful attention to I²C bus sharing between the display and the servo driver, so updating the screen never introduces visible timing artefacts in the servo motion

---

## Hardware

| Component | Role |
|---|---|
| Seeed XIAO ESP32-S3 | Microcontroller, Wi-Fi |
| Seeed Expansion Board Base for XIAO | I²C hub, OLED (SSD1306 128×64), user button |
| Grove 16-Channel PWM Driver (PCA9685) | Drives all servo PWM signals (8 hatch channels + 2 servo tester channels) |
| Grove 4-Channel SPDT Relay | Switches servo power per hatch |
| 8 hobby servos (4× lever, 4× latch) | Hatch movement and mechanical locking |

Full pin-outs, I²C addresses, wiring notes and mechanical background are in the [implementation documentation](#documentation).

---

## How It Works

1. Each hatch's lever servo and latch servo share one relay — powered together, unpowered together, protecting the servos from continuous load.
2. Moving a hatch runs a short, fully automated sequence: the latch releases, the lever servo ramps smoothly to the target position, the latch re-engages, then power is cut.
3. All of this is driven by a single async control task that compares a target ("what should the hatch be at") against the last known actual position, for every hatch, every pass — the same mechanism a local button panel, a rain sensor, or a scheduler could hook into later without touching the core logic.
4. A hardware watchdog and a dedicated fail-safe routine guarantee that if anything ever goes wrong, hatches end up closed and unlocked rather than stuck in an unknown, powered state.

See the implementation docs for the full sequence diagrams, timing constants, and the reasoning behind each design decision.

---

## Getting Started

This project is tailored to specific hardware and is documented here primarily for reference, not as a drop-in library. If you have the same or similar hardware:

1. Flash MicroPython onto the XIAO ESP32-S3.
2. Review and adjust `source/app/hw_config.py` (pins, I²C addresses, channel mapping) for your wiring.
3. Fetch the manually-installed third-party libraries (`uQR`, `writer.py`, a generated status font) as described in the [documentation](#documentation) — they are not fetched automatically by the deploy script.
4. Run `./upload.sh` to deploy everything to the device (see the deploy script's own configuration block for the serial port / Wi-Fi upload settings).
5. On first boot, hold the user button to enter Wi-Fi setup (access point mode), connect, and enter your Wi-Fi credentials via the served setup page.
6. Open the device's IP (or scan the QR code shown after a short button press) and use the configuration page to calibrate each hatch's servo positions.

None of the above is a generic "just works" install path — see the documentation for the full, exact sequence, dependencies, and known pitfalls.

---

## Project Structure

```
roof_hatch_controller/
├── docs/                  # this documentation, German (primary) + English
├── requirements.txt       # MicroPython packages fetched via mip
├── upload.sh              # deploy script
├── lib/                   # third-party libraries (mip + manually installed)
└── source/                # deployed 1:1 to the device
    ├── main.py
    ├── config.json(.template)
    ├── app/                # tasks, hardware init, config/state management
    ├── shared/             # PCA9685 driver, relay driver, unit conversion
    └── webpages/           # control page, config page, Wi-Fi setup page
```

Full file-by-file breakdown in the [implementation documentation](#documentation).

---

## Configuration

All servo parameters — mechanical range, rotation direction, peak speed, pulse-width limits, hard stops, detent positions, mounting angle — are configurable per channel from the web-based configuration page, in **degrees** and **degrees/second**, not raw microseconds. Every accepted value is validated and written to flash immediately.

The full `config.json` schema, every field's valid range, and the validation rules are documented in detail in the implementation docs (section 7).

---

## Safety Design

- **Thermal protection:** servos are powered only for the duration of a move.
- **Power-supply protection:** only one hatch is ever active at a time, by design of the control loop.
- **Fail-safe:** a hardware watchdog resets the device if the event loop ever hangs; the reset handler releases every hatch's latch and lets it close under gravity, leaving a known, unlocked state — no assumptions are made about the hatch's prior position.
- **Power-loss recovery:** the last commanded target and the last confirmed actual position are both persisted; an interrupted move is detected and can be resumed automatically after reboot.
- **Config access control:** the configuration page (and every state-changing configuration/movement endpoint) requires a physical, long button press at the device plus a time-limited session — not reachable over the network without physical access first.

---

## Status & Roadmap

Fully implemented and running on real hardware. See section 18 of the implementation documentation for the detailed "done" list and the remaining open items (further long-term hardware testing, an optional local button/LED panel, a planned rain sensor add-on, RTC-based scheduling, and unit tests).

---

## Documentation

Full implementation documentation — hardware wiring, every configuration field and its valid range, the complete async task architecture, the movement sequence and its timing constants, the web API, and a section of known pitfalls and debugging techniques discovered while bringing this up on real hardware:

- 🇩🇪 [`docs/implementierungsdoku.md`](docs/implementierungsdoku.md) — German, primary/original document
- 🇬🇧 [`docs/implementation.md`](docs/implementation.md) — English translation

If you're adapting this project for your own hardware, start there — this README only covers the overview.

---

## License

Released under the [MIT License](LICENSE).

This project's own code (the MicroPython application, both driver modules, and the web UI) is MIT-licensed. It depends on a small number of third-party components, all under permissive licenses compatible with MIT — see below.

---

## Acknowledgements

Third-party components used by this project:

| Component | License | Purpose |
|---|---|---|
| [microdot](https://github.com/miguelgrinberg/microdot) (Miguel Grinberg) | MIT | Async web server |
| [ssd1306](https://github.com/micropython/micropython-lib) (MicroPython) | MIT | OLED display driver |
| [uQR](https://github.com/JASchilz/uQR) (Joseph Schilz, forked from [python-qrcode](https://github.com/lincolnloop/python-qrcode)) | BSD-3-Clause | QR code generation |
| [micropython-font-to-py / writer.py](https://github.com/peterhinch/micropython-font-to-py) (Peter Hinch) | MIT | Larger OLED font rendering |
| [Bootstrap](https://getbootstrap.com/) | MIT | Web UI styling |
| [Alpine.js](https://alpinejs.dev/) | MIT | Reactive web UI behaviour |

Thanks to all of the above authors for making their work freely reusable.
