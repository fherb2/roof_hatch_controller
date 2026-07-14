# Implementierungsdokumentation – Dachlukensteuerung

Dieses Dokument beschreibt die gesamte Hard- und Software der Dachlukensteuerung so vollständig, dass in einem neuen Chat (zusammen mit dem Quellcode) sofort mit Test, Debugging oder Erweiterungen weitergearbeitet werden kann.

---

## 1. Projektziel

Steuerung von bis zu 4 Dachluken über Web-Interface (Handy, Tablet, Desktop) sowie optional über ein lokales Bedienfeld mit Tastern und LEDs. Pro Dachluke ein kräftiger Servo für die Bewegung und ein zweiter Servo für eine mechanische Feststellbremse. Betrieben auf einem Seeed XIAO ESP32-S3 mit MicroPython.

---

## 2. Hardware

### 2.1 Komponenten


| Komponente                            | Funktion                                                                    |
| ------------------------------------- | --------------------------------------------------------------------------- |
| Seeed XIAO ESP32-S3                   | Mikrocontroller, WLAN                                                       |
| Seeed Expansion Board Base for XIAO   | I²C-Hub, OLED (SSD1306 128×64), Micro-SD-Slot, RTC (PCF8563), User-Button |
| Grove 4-Channel SPDT Relay            | Servo-Spannungsversorgung schalten                                          |
| Grove 16-Channel PWM Driver (PCA9685) | PWM-Signale für alle Servos                                                |
| MCP23017 (Breakout)                   | I²C-GPIO-Expander für lokales Bedienfeld –**zurückgestellt**            |

### 2.2 I²C-Bus (alle Geräte auf einem Bus)


| Gerät                     | Adresse      | Bemerkungen                                                                                                                                                                                        |
| -------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSD1306 OLED               | 0x3C         |                                                                                                                                                                                                    |
| PCA9685 PWM-Driver         | 0x7F         | Das Board kann in der I2C-Adresse durch HW-Brücken umkonfiguriert werden.                                                                                                                         |
| Grove 4-Ch. Relay          | 0x11 or 0x12 | Das Board kann in der I2C-Adresse über Softwarer umkonfiguriert werden. Für Arduinos Treiber-Bibliothek gibts dafür einen Befehl (s. https://wiki.seeedstudio.com/Grove-4-Channel_SPDT_Relay/). |
| RTC PCF8563                | 0x51         |                                                                                                                                                                                                    |
| MCP23017 (zurückgestellt) | 0x20         |                                                                                                                                                                                                    |

**GPIO-Pins des Expansion Boards (XIAO ESP32-S3):**


| Signal      | GPIO                   |
| ----------- | ---------------------- |
| I²C SDA    | 4                      |
| I²C SCL    | 5                      |
| User-Button | 1 (aktiv low, Pull-up) |

### 2.3 Elektrische Verdrahtung

- **Pro Dachluke ein Relais** (NO-Kontakt): schaltet die gemeinsame +8V-Ader beider Servos dieser Luke.
- GND durchgehend gemeinsam für alle Servos.
- Jeder Servo hat eine eigene PWM-Leitung vom PCA9685.
- Netzteil: 3A / 8V – ausreichend für **eine Luke gleichzeitig** (softwareseitig garantiert).

### 2.4 PCA9685-Kanalzuordnung


| Kanal | Funktion                 |
| ----- | ------------------------ |
| 0     | Luke 1 – Dachluke-Servo |
| 1     | Luke 1 – Brems-Servo    |
| 2     | Luke 2 – Dachluke-Servo |
| 3     | Luke 2 – Brems-Servo    |
| 4     | Luke 3 – Dachluke-Servo |
| 5     | Luke 3 – Brems-Servo    |
| 6     | Luke 4 – Dachluke-Servo |
| 7     | Luke 4 – Brems-Servo    |

Konfiguriert in `hw_config.py` als Dict `PCA9685_CH = {0: (0,1), 1: (2,3), ...}`.

### 2.5 Relay-Zuordnung


| Relay-Kanal | Funktion               |
| ----------- | ---------------------- |
| 0           | +8V für Servos Luke 1 |
| 1           | +8V für Servos Luke 2 |
| 2           | +8V für Servos Luke 3 |
| 3           | +8V für Servos Luke 4 |

Konfiguriert als `RELAY_CH = {0: 0, 1: 1, 2: 2, 3: 3}` in `hw_config.py`.

---

## 3. Mechanik und Servos

### 3.1 Feststellbremse

- Rastsystem aus ineinandergreifenden Zähnen – übernimmt Haltemoment wenn Servos stromlos.
- Getriebe der Dachluke **nicht selbsthemmend** → ohne Bremse und Strom schließt die Luke durch Schwerkraft.
- Bremse hat genau **2 Positionen**: `brake_open_us` und `brake_close_us` (global konfiguriert).
- Bremse schnappt federbelastet ein; Servo zieht gegen die Feder auf.
- Brems-Servo kann nicht durch zu weites Fahren kaputt gehen (Nockenprinzip).

### 3.2 Servo-Thermik

Modellbauservos (hier ca. 4,5 Nm / 2–3 A Stallstrom) sind für intermittierenden Betrieb ausgelegt. Dauerlast führt zur Überhitzung. Deshalb: Servos werden **nur während der Bewegungssequenz bestromt** (Relais ein), danach stromlos. Die mechanische Bremse übernimmt das Haltemoment.

### 3.3 Erweiterter PWM-Stellbereich

Die verwendeten Servos haben einen erweiterten Stellbereich. Konfiguriert in `hw_config.py`:

- `SERVO_MIN_US = 500`
- `SERVO_MAX_US = 2500`

### 3.4 Servo-Einschaltverhalten (Power-On-Jerk)

Digitale Hochmoment-Servos führen beim Einschalten eine interne Initialisierungsroutine durch und fahren dabei kurz mit Maximalgeschwindigkeit in Richtung Mittelstellung – unabhängig vom anliegenden PWM-Signal. Die Bewegungssequenz in `task_control.py` enthält einen Workaround (`step_preset_servos`, `step_preset_delay`, `step_wiggle`). Details und Hintergrund: `xiao_esp32s3_erfahrungen.md` Abschnitt 12.

---

## 4. Plattform

**MicroPython** auf XIAO ESP32-S3.

Begründung: `uasyncio` für kooperatives Multitasking, `microdot` als async-Webserver, eingebautes `ujson`, stabile I²C-Bibliotheken.

Externe Bibliotheken (via `mip`, verwaltet durch `requirements.txt`):

- `ssd1306` – OLED-Treiber
- `microdot` (via `github:miguelgrinberg/microdot`) – Webserver

Eigene Treiber im Projekt:

- `shared/pca9685.py` – PCA9685-Treiber (selbst geschrieben, deutlich kleiner als das komplette Paket von PyPi)
- `shared/grove_relay_4chn_spdt.py` – Grove Relay-Treiber (kein MicroPython-Paket verfügbar)

---

## 5. Projektstruktur

### 5.1 Verzeichnis auf dem Entwicklungsrechner

```
roof_hatch_controller/
├── designkonzept.md             # ursprüngliches Designkonzept (veraltet)
├── implementierungsdoku.md      # dieses Dokument
├── requirements.txt             # externe MicroPython-Pakete
├── upload.sh                    # Deploy-Script
├── pyrightconfig.json           # VSCode/Pyright: extraPaths: ["source"]
├── lib/                         # externe Bibliotheken (via mip installiert)
│   ├── microdot.py
│   └── ssd1306.py
└── source/                      # wird 1:1 auf das Gerät kopiert
    ├── boot.py
    ├── main.py
    ├── config.json              # Gerätekonfiguration (Startwerte anpassen!)
    ├── app/
    │   ├── __init__.py
    │   ├── hw_config.py         # Hardware-Konstanten
    │   ├── hw_init.py           # Hardware-Initialisierung
    │   ├── state.py             # config.json / state.json Verwaltung
    │   ├── failsafe.py          # Fail-Safe-Sequenz
    │   ├── wifi.py              # WLAN-Verwaltung
    │   ├── session.py           # Config-Session-Zustand
    │   ├── task_watchdog.py     # Hardware-WDT-Feeder-Task
    │   ├── task_control.py      # Steuerloop-Task
    │   ├── task_button.py       # Button-Monitor-Task
    │   ├── task_display.py      # OLED-Update-Task
    │   └── task_webserver.py    # microdot-Webserver-Task
    ├── shared/
    │   ├── __init__.py
    │   ├── pca9685.py           # PCA9685-Treiber
    │   ├── grove_relay_4chn_spdt.py  # Grove Relay-Treiber
    │   ├── json_utils.py        # pretty_json() für lesbare config.json-Ausgabe
    │   └── servo_units.py       # Grad <-> µs Umrechnung (deg_to_us/us_to_deg)
    └── webpages/
        ├── index.html           # Bedienwebseite
        ├── config.html          # Konfigurationswebseite
        ├── ap_setup.html        # WLAN-Konfiguration (AP-Modus)
        └── static/
            ├── bootstrap.min.css
            ├── bootstrap.bundle.min.js
            └── alpine.min.js
```

### 5.2 Auf dem Gerät (Flash-Dateisystem)

Gleiche Struktur, aber ohne `source/`-Präfix. Wurzel `/` entspricht `source/`.

Zusätzlich im Flash (nicht im Repo, werden zur Laufzeit erzeugt):

- `/wifi.json` – WLAN-Zugangsdaten (via AP-Modus oder manuell)
- `/state.json` – letzter bekannter Servo-Zustand (beim ersten Start automatisch erzeugt)

---

## 6. Build & Deployment

### 6.1 `requirements.txt`

```
ssd1306
github:miguelgrinberg/microdot
```

`upload.sh` prüft für jeden Eintrag ob die Datei/das Verzeichnis in `lib/` existiert und installiert sie bei Bedarf nach.

### 6.2 `uQR` manuell installieren

`uQR` ist nicht im micropython.org-Paketindex. Einmalig manuell herunterladen:

1. `https://github.com/JASchilz/uQR` → `uQR.py` → Raw-Button → als `lib/uQR.py` speichern
2. `upload.sh` kopiert `lib/` danach automatisch aufs Gerät

### 6.3 `upload.sh`

Konfigurationsblock oben im Script:

- `UPLOAD_MODE`: `"usb"` oder `"wifi"`
- `USB_PORT`: z.B. `/dev/ttyUSB0`
- `WIFI_IP`: IP des Geräts für WebREPL

Ablauf:

1. `requirements.txt` durchgehen → fehlende Pakete per `micropython -m mip install --target lib/` nachladen
2. Temporäres `stop_wdt_main.py` in `/tmp` erzeugen und als `main.py` auf das Gerät laden, dann Reset. Damit läuft kein WDT mehr → alle folgenden Schritte sind ohne Zeitdruck. Retry-Schleife mit initialem Hard-Reset bricht auch einen WDT-Loop oder Crash-REPL auf.
3. Alles in einer einzigen mpremote-Session (nur ein Soft-Reset): Zielverzeichnisse löschen, `config.json.template`, `config.json` (falls vorhanden), `app/`, `shared/`, `webpages/`, `lib/` hochladen, dann `main.py` als letztes.
4. Finaler Reset → echte `main.py` startet.

**Warum `main.py` zuletzt?** Das `stop_wdt_main.py` bleibt während aller Uploads aktiv. Erst nach dem letzten Upload wird die echte `main.py` eingespielt und mit dem finalen Reset gestartet.

**`config.json.template`** wird immer hochgeladen; `config.json` nur wenn im `source/`-Verzeichnis vorhanden.

**WLAN-Upload** (WebREPL): Einmalig per USB aktivieren:

```python
import webrepl_setup
```

Danach per `UPLOAD_MODE="wifi"` möglich.

### 6.3 `pyrightconfig.json` (Repo-Root)

```json
{
    "extraPaths": ["source"]
}
```

Damit löst VSCode/Pyright Imports korrekt auf (ohne `source.`-Präfix).

### 6.4 Statische Bibliotheken einmalig herunterladen

```bash
cd source/webpages/static
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css -o bootstrap.min.css
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js -o bootstrap.bundle.min.js
curl -L https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js -o alpine.min.js
```

---

## 7. Konfigurationsdateien

### 7.1 `/config.json` – Gerätekonfiguration

Muss vor dem ersten Start auf das Gerät kopiert werden (per `upload.sh` automatisch aus `source/config.json`). Startwerte sind Platzhalter – müssen über die Konfigurationswebseite auf die tatsächliche Hardware angepasst werden.

Alle Positionen, Hard-Stops und Geschwindigkeiten sind in **Grad** bzw. **Grad/s** angegeben, nicht in µs. Die Umrechnung nach µs für die PCA9685-Ansteuerung übernimmt `shared/servo_units.py` (`deg_to_us()`/`us_to_deg()`), ausschließlich innerhalb von `task_control.py`. Die Drehrichtung ist dabei softwareseitig fest umgekehrt (`SERVO_DIRECTION_REVERSED = True` in `hw_config.py`, gilt einheitlich für Luken- und Bremsservo, ist kein Konfigurationswert).

```json
{
    "global": {
        "hatch_servo": {
            "range_deg": 270,
            "pulse_min_us": 500,
            "pulse_max_us": 2500,
            "hard_stop_min_deg": 0,
            "hard_stop_max_deg": 270,
            "speed_deg_per_s": 30
        },
        "brake_servo": {
            "range_deg": 180,
            "pulse_min_us": 500,
            "pulse_max_us": 2500
        }
    },
    "hatches": [
        {
            "name": "Luke 1",
            "short_name": "Luke1",
            "brake_open_deg": 10,
            "brake_close_deg": 170,
            "positions": [{"hatch_deg": 0}, {"hatch_deg": 90}, {"hatch_deg": 260}]
        }
    ]
}
```

**`global.hatch_servo`** – gilt einheitlich für alle 4 Lukenservos:

- `range_deg`: Gesamtstellbereich des Servos, Umschaltwert 180 oder 270
- `pulse_min_us` / `pulse_max_us`: Impulszeit-Grenzen in µs (üblicherweise 500/2500)
- `hard_stop_min_deg` / `hard_stop_max_deg`: Hardstop-Grenzen in Grad – Software fährt nie außerhalb, gilt für alle 4 Luken gleich
- `speed_deg_per_s`: Bewegungsgeschwindigkeit in Grad/s, einheitlich für alle 4 Luken. Wird in `task_control.py` zusammen mit der festen Schrittgröße `HATCH_STEP_US` (5 µs, Code-Konstante) in ein Schrittintervall umgerechnet (`_compute_step_ms()`)

**`global.brake_servo`** – gilt einheitlich für alle 4 Bremsservos:

- `range_deg`: Gesamtstellbereich, Umschaltwert 180 oder 270
- `pulse_min_us` / `pulse_max_us`: Impulszeit-Grenzen in µs

**`config.json.template`** im `source/`-Verzeichnis dient als konservative Vorlage. `config_migration.py` nutzt sie beim Boot um fehlende oder umbenannte Felder automatisch zu ergänzen (Merge-Mechanismus unverändert, verarbeitet die neuen verschachtelten Dicts bereits generisch).

**Felder pro Luke:**

- `name`: Anzeigename (Web-Interface)
- `short_name`: max. 6 Zeichen für OLED-Display
- `brake_open_deg` / `brake_close_deg`: Bremsen-Servo-Winkel in Grad (Auf- und Zu-Stellung), individuell pro Luke kalibrierbar (anders als beim Lukenservo, da die Bremsmechanik pro Luke leicht unterschiedlich sitzen kann)
- `positions`: Liste von Rastpositionen `[{"hatch_deg": N}, ...]`, min. 2. `positions[0]` ist immer der Zu-Endanschlag ("ZU"), `positions[-1]` immer der Auf-Endanschlag ("AUF") – diese beiden bleiben bei Einfügen/Entfernen von Zwischenpositionen stets an erster/letzter Stelle der Liste

**Entfallen** (durch die Grad-Umstellung ersetzt bzw. nicht mehr web-konfigurierbar, siehe 9.11): `global.brake_open_us`/`brake_close_us`, `global.delay_after_brake_change_cmd_ms`/`delay_after_move_ms`, pro Luke `hatch_min_us`/`hatch_max_us`/`step_us`/`step_ms`.

### 7.2 `/wifi.json` – WLAN-Zugangsdaten

```json
{"ssid": "MeinNetzwerk", "password": "MeinPasswort"}
```

Wird über den AP-Modus geschrieben. Liegt im internen Flash, nicht auf SD. Nicht im Git-Repo.

### 7.3 `/state.json` – Letzter bekannter Servo-Zustand

````json
{
    "hatches": [
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0},
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0},
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0},
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0}
    ]
}
````

Wie `config.json` durchgehend in Grad (nicht mehr µs). Umrechnung nach µs passiert nur lokal in `task_control.py` unmittelbar vor der PCA9685-Ansteuerung.

- `hatch_deg`: Letzter bekannter Ist-Zustand des Luken-Servos in Grad, oder `FAILSAFE_DEG` (-1) wenn die Position unbekannt ist (Fail-Safe-Zustand oder Stromausfall mitten in einer Fahrt).
- `brake_deg`: Letzter bekannter Ist-Zustand des Brems-Servos in Grad. Immer ein echter Winkel, nie `FAILSAFE_DEG`.
- `target_deg`: Zuletzt befohlener Soll-Zustand in Grad, oder `FAILSAFE_DEG` (-1) im passiven Fail-Safe-Ruhezustand. Persistiert damit ein Soll/Ist-Mismatch nach Stromausfall erkennbar bleibt und beim nächsten Boot automatisch abgearbeitet wird.

Beim allerersten Start nicht vorhanden → `state.py` erzeugt Standardzustand aus `config.json` (alle Luken auf `hard_stop_min_deg`, Bremse auf die jeweilige `brake_close_deg` der Luke, Soll = Ist) und speichert ihn. Ein `state.json` ohne `target_deg` (altes Format, auch das frühere µs-basierte Schema) wird ebenfalls als ungültig erkannt und neu erzeugt.

**Wichtig:** Vor jedem Relais-Ein werden die Ist-Positionen an PCA9685 ausgegeben. Bei `FAILSAFE_DEG` werden Fallback-Werte verwendet (`positions[0]` für Luken-Servo, `brake_open_deg` der jeweiligen Luke für Brems-Servo), damit kein Ruck entsteht.

---

## 8. Startsequenz (`main.py`)

```
Stage 0:  WDT-Reset-Erkennung
          → machine.reset_cause() == WDT_RESET?
          → ja: init_hw_failsafe() + fail_safe_close_all() vor allem anderen

Stage 1:  Display früh initialisieren (init_display_early)
          → ab jetzt Statusmeldungen auf OLED

Stage 2:  Restliche Hardware initialisieren (init_hardware)
          → PCA9685, Relay, Button
          → Fehler → Anzeige auf OLED + return

Stage 2b: config.json laden (load_config)
          → Fehler → Anzeige auf OLED + return
          state.json laden (load_state)
          → Fehler → create_default_state() + save_state()
          Bei WDT-Reset: alle Luken in state auf FAILSAFE_DEG (-1) setzen
          + save_state() (physische Fail-Safe-Fahrt war bereits Stage 0)

Stage 3:  WLAN
          → boot.AP_MODE=True: start_ap_mode()
          → boot.AP_MODE=False: start_client_mode()

Stage 4:  targets-Dict aus persistiertem Soll aufbauen
          → targets = [{"hatch_deg": h["target_deg"]} for h in state["hatches"]]

Stage 5:  uasyncio.gather() – alle Tasks starten:
          task_watchdog, task_display, task_control,
          task_button, task_webserver
```

---

## 9. Modulbeschreibungen

### 9.1 `boot.py`

Läuft als erstes, noch vor `main.py`. Minimaler Code:

- Liest User-Button (GPIO 1) mit Entprellung
- Setzt `AP_MODE = True/False` als Modulvariable
- `main.py` importiert: `import boot; if boot.AP_MODE: ...`

AP-Modus: Button beim **Einschalten gedrückt halten**.

### 9.2 `app/hw_config.py`

Alle Hardware-Konstanten. Keine Imports von Hardware-Bibliotheken – überall importierbar.

Wichtige Konstanten:

```python
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5
I2C_FREQ    = 400_000
BTN_USER_PIN = 1
BTN_DEBOUNCE_MS = 50
BTN_CONFIG_UNLOCK_MS  = 60_000    # 60s Freigabefenster nach Button
BTN_CONFIG_SESSION_MS = 3_600_000 # 1h Session
PCA9685_FREQ_HZ = 50
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
SERVO_RANGE_DEG_MAX = 270  # obere Sanity-Grenze für Grad-Werte in state.json,
                            # unabhängig vom konfigurierten range_deg (180/270)
SERVO_DIRECTION_REVERSED = True  # fest, gilt einheitlich für Luken- UND Bremsservo
HATCH_STEP_US = 5    # feste Schrittgröße der Bewegungsrampe (µs); die
                      # konfigurierte Geschwindigkeit (speed_deg_per_s)
                      # bestimmt nur das Schrittintervall, nicht die Schrittgröße
FAILSAFE_DEG = -1    # Sentinel: Position unbekannt (Fail-Safe-Zustand)
NUM_HATCHES  = 4
BRAKE_TRAVEL_MS        = 500   # Wartezeit für Bremsen-Servo im Fail-Safe
BRAKE_OPEN_US_FAILSAFE = 1200  # Fallback-Wert ohne config.json (roher µs-Wert,
                                # unabhängig von der Grad-Konfiguration –
                                # failsafe.py braucht kein lesbares config.json)
PCA9685_CH = {0:(0,1), 1:(2,3), 2:(4,5), 3:(6,7)}
RELAY_CH   = {0:0, 1:1, 2:2, 3:3}
```

### 9.2b `shared/servo_units.py`

Reine Umrechnungsmathematik, kein Hardware-Import, daher überall importierbar:

```python
deg_to_us(deg, pulse_min_us, pulse_max_us, range_deg)  # -> int µs
us_to_deg(us, pulse_min_us, pulse_max_us, range_deg)   # -> float Grad
```

Berücksichtigt intern `SERVO_DIRECTION_REVERSED` aus `hw_config.py`. Wird ausschließlich in `task_control.py` verwendet, um die in Grad gehaltene Konfiguration/den Zustand unmittelbar vor der PCA9685-Ansteuerung nach µs umzurechnen.

### 9.3 `shared/pca9685.py`

Selbst geschriebener Treiber direkt nach Datenblatt. Kein externer Import nötig.

**API:**

```python
pwm = PCA9685(i2c, address=0x40)
pwm.freq(50)              # PWM-Frequenz setzen (50 Hz für Servos)
pwm.set_pwm(ch, on, off)  # rohe 12-Bit-Werte
pwm.set_us(ch, us)        # Pulsbreite in µs (Standard: freq_hz=50)
pwm.set_all_off()         # alle Kanäle aus (sicherer Zustand)
```

**Wichtige Implementierungsdetails:**

- ALLCALL-Bit in `_reset()` **nicht** gesetzt (würde PCA9685 auf Broadcast-Adresse 0x00 hören lassen → Interferenz mit anderen I²C-Geräten)
- Prescaler-Formel korrekt nach Datenblatt: `round(osc_clk / (4096 * hz)) - 1`
- Sleep-Zeit nach Oszillator-Aufwachen: 1 ms (Datenblatt: min. 500 µs)

### 9.4 `shared/grove_relay_4chn_spdt.py`

Treiber für Grove 4-Channel SPDT Relay (STM32-basiert, I²C).

**I²C-Protokoll:**

- Einziger relevanter Befehl: `CMD_CHANNEL_CTRL = 0x10` + State-Byte (Bitmask: Bit 0–3 = Kanal 1–4)
- Kein Hardware-Read-Back möglich → Software-State im RAM
- Board für 5V-Logik ausgelegt; funktioniert erfahrungsgemäß auch mit 3.3V, falls nicht: Level-Shifter

**API (0-basierte Kanalindizes):**

```python
relay = GroveRelay(i2c, address=0x11)
relay.on(channel)    # Relais schließen → Servos bestromt
relay.off(channel)   # Relais öffnen → Servos stromlos
relay.all_off()      # sicherer Zustand beim Init
relay.is_on(channel) # Software-State abfragen
```

### 9.5 `app/hw_init.py`

Drei Initialisierungsfunktionen:

```python
init_hw_failsafe()      # nur I²C + PCA9685 + Relay (kein Display/Button)
                        # → für Fail-Safe nach WDT-Reset

init_display_early()    # nur I²C + Display
                        # → gibt (i2c, display) zurück

init_hardware(i2c=None, display=None)  # alles: PCA9685, Relay, Display, Button
                                       # wiederverwendet i2c/display wenn übergeben
```

Alle drei werfen `HardwareInitError` wenn I²C-Gerät nicht antwortet.
`HardwareBundle` ist der Rückgabetyp mit Feldern: `i2c`, `pwm`, `relay`, `display`, `btn`.

### 9.6 `app/state.py`

Verwaltet `config.json` und `state.json`.

**API:**

```python
load_config()              # lädt + validiert config.json → ConfigError wenn fehlt/ungültig
validate_config(data)      # validiert ein Dict ohne Datei (für POST /api/config)
load_state()               # lädt + validiert state.json → StateError wenn fehlt/ungültig
save_state(state)          # schreibt state.json → StateError wenn Schreibfehler
create_default_state(cfg)  # erzeugt sicheren Anfangszustand aus config
```

**Validierung config.json:**

- Alle Required-Keys vorhanden, inklusive der verschachtelten `global.hatch_servo`/`global.brake_servo`-Blöcke
- `range_deg` beider Servotypen ∈ {180, 270}
- `pulse_min_us < pulse_max_us`, beide innerhalb [SERVO_MIN_US, SERVO_MAX_US] (absolute Hardwaregrenze, unabhängig vom konfigurierten `range_deg`)
- `hard_stop_min_deg < hard_stop_max_deg`, beide innerhalb [0, hatch_servo.range_deg]
- `speed_deg_per_s > 0`
- Pro Luke: `brake_open_deg`/`brake_close_deg` innerhalb [0, brake_servo.range_deg]; mindestens 2 Positionen; jede `positions[].hatch_deg` innerhalb [hard_stop_min_deg, hard_stop_max_deg]
- Exakt NUM_HATCHES Einträge

`hatch_deg` und `target_deg` in `state.json` dürfen `FAILSAFE_DEG` (-1) enthalten; in diesem Fall wird der Range-Check für das jeweilige Feld übersprungen. `brake_deg` ist immer ein echter Winkel und wird immer geprüft (Sanity-Grenze [0, SERVO_RANGE_DEG_MAX], unabhängig von `config.json`, siehe 9.2).

**Erstes Startup / veraltetes Format:** `load_state()` wirft `StateError` → `create_default_state(config)` + `save_state()`. Ein `state.json` ohne `target_deg` (auch das alte µs-basierte Format) wird ebenfalls als ungültig erkannt und löst denselben Pfad aus.

### 9.7 `app/failsafe.py`

Synchrone Funktion (kein asyncio) – sicher vor dem Event-Loop aufrufbar.

```python
fail_safe_close_all(hw, brake_open_us=None)
```

Ablauf für jede Luke sequenziell:

1. Bremsen-Servo auf `brake_open_us` setzen
2. Relais ein
3. `time.sleep_ms(BRAKE_TRAVEL_MS)` warten (Bremsen-Servo fährt)
4. Relais aus → Dachluke schließt durch Schwerkraft, Bremse schnappt dabei nicht ein, da Servo im "offen" Zustand zurück gelassen.
5. 200 ms Pause vor nächster Luke

Wenn `brake_open_us=None` → Fallback auf `BRAKE_OPEN_US_FAILSAFE` aus `hw_config.py` (funktioniert ohne `config.json`).

### 9.8 `app/wifi.py`

```python
load_wifi_config()              # liest /wifi.json → WifiConfigError
save_wifi_config(ssid, password)# schreibt /wifi.json + machine.reset()
start_client_mode(hw)           # async, verbindet WLAN, 15s Timeout
start_ap_mode(hw)               # async, startet AP "Dachluke-Setup" (offen)
```

Bei fehlendem `wifi.json` in `start_client_mode`: Fehlermeldung auf Display, weiter ohne WLAN (lokales Bedienfeld bleibt funktionsfähig). Neustart mit gehaltenem Button für AP-Modus nötig.

AP-SSID: `"Dachluke-Setup"`, kein Passwort, IP: 192.168.4.1 (ESP32-Standard).

### 9.9 `app/session.py`

Geteilter Zustand zwischen `task_button` und `task_webserver`. Nur RAM, keine Persistenz.

```python
unlock_config()       # startet 60s-Freigabefenster (Button gedrückt)
activate_session()    # startet 1h-Session (Config-Seite aufgerufen)
is_unlock_active()    # True während 60s-Fenster
is_session_active()   # True während 1h-Session
```

Zeitbasis: `time.time()` (Sekunden seit Epoch).

### 9.10 `app/task_watchdog.py`

```python
async def task_watchdog():
    wdt = WDT(timeout=4000)  # 4 Sekunden Hardware-WDT
    while True:
        wdt.feed()
        await uasyncio.sleep_ms(200)  # alle 200ms füttern
```

**Warum 200ms:** Kooperatives Multitasking ohne Prioritäten – häufiges Füttern stellt sicher, dass der Task nicht durch andere bereite Tasks verdrängt wird. WDT-Timeout (4s) >> Feed-Intervall (200ms) → großer Puffer.

**Coding-Regel:** Jede `while`-Schleife im gesamten Projekt **muss** mindestens ein `await` enthalten, damit `task_watchdog` stets erreichbar bleibt.

**WDT-Reset-Erkennung in `main.py`:**

```python
if machine.reset_cause() == machine.WDT_RESET:
    hw_fs = init_hw_failsafe()
    fail_safe_close_all(hw_fs)
```

### 9.11 `app/task_control.py`

Herzstück der Anwendung. Vergleicht `targets[i]["hatch_deg"]` mit `state["hatches"][i]["hatch_deg"]` für alle 4 Luken sequenziell. `config`, `state` und `targets` sind durchgehend in Grad; die Umrechnung nach µs (`shared/servo_units.py`, `deg_to_us()`) passiert ausschließlich lokal zu Beginn von `_move_hatch`, unmittelbar vor der Ansteuerung.

**Bewegungssequenz (`_move_hatch`):**

| Schritt | Was passiert |
|---|---|
| `step_mark_failsafe` | `hatch_deg` auf `FAILSAFE_DEG` setzen und persistieren → bei Stromausfall bleibt der Sentinel in `state.json`; nächster Boot setzt die Fahrt fort |
| `step_preset_servos` | Brems-Servo auf `brake_open_deg` **dieser Luke** (in µs umgerechnet); Luken-Servo auf **0µs** (kein gültiges PWM-Signal), damit der Servo-Controller beim Einschalten keine Mittelstellungsfahrt macht |
| `step_relay_on` | Relais ein → Servos bestromt; Bremse beginnt sofort zu öffnen |
| `step_preset_delay` | `_PRESET_DELAY_MS` warten damit der Servo-Controller bootet bevor das erste gültige PWM-Signal gesetzt wird; 0 = deaktiviert |
| `step_wiggle` | Luken-Servo für `_WIGGLE_CYCLES` Zyklen um ±`_WIGGLE_US` alternieren – bricht ein verbliebenes Zucken ab; 0 = deaktiviert |
| (Wartezeit `_RELAY_ON_TO_BRAKE_OPEN_MS`) | Zeit ab Relais-Ein bis Bremse vollständig offen |
| (Wartezeit `_BRAKE_OPEN_TO_HATCH_MOVE_MS`) | zusätzlicher Sicherheitspuffer bevor die Lukenbewegung beginnt |
| `step_move_hatch` | Luken-Servo schrittweise zur Zielposition (feste Schrittgröße `HATCH_STEP_US` = 5µs, Intervall `step_ms` aus `speed_deg_per_s` berechnet – siehe `_compute_step_ms()`); Startpunkt bei `FAILSAFE_DEG`-Ist: `positions[0]` |
| (Wartezeit `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS`) | Zeit ab Zielposition erreicht bis Bremse den Schließbefehl bekommt |
| `step_close_brake` | Brems-Servo → `brake_close_deg` **dieser Luke** |
| (Wartezeit `_BRAKE_CLOSE_TO_RELAY_OFF_MS`) | Zeit ab Bremse-Schließbefehl bis Relais aus |
| `step_relay_off` | Relais aus (immer, im `finally`-Block) |
| `step_update_state` | `state` aktualisieren (`hatch_deg`, `brake_deg`, `target_deg`) + `save_state()` |

**Modul-Konstanten** (direkt oben in `task_control.py` anpassbar, nicht in `config.json`, nicht web-konfigurierbar):

| Konstante | Wert | Bedeutung |
|---|---|---|
| `_WIGGLE_US` | 10 | Amplitude des Servo-Wackelns in µs |
| `_WIGGLE_CYCLES` | 5 | Anzahl ±-Zyklen; 0 = deaktiviert |
| `_PRESET_DELAY_MS` | 250 | Wartezeit nach Relay-Ein vor erstem PWM; 0 = deaktiviert |
| `_RELAY_ON_TO_BRAKE_OPEN_MS` | 200 | Relais-Ein bis Bremse vollständig offen |
| `_BRAKE_OPEN_TO_HATCH_MOVE_MS` | 400 | zusätzlicher Puffer bis Bewegungsbeginn |
| `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS` | 200 | Zielposition erreicht bis Bremse schließt |
| `_BRAKE_CLOSE_TO_RELAY_OFF_MS` | 500 | Bremse-Schließbefehl bis Relais aus |

`HATCH_STEP_US` (fest 5µs) liegt in `hw_config.py`, da es dort mit den übrigen Hardware-Konstanten importierbar ist (siehe 9.2).

**Geschwindigkeit → Schrittintervall** (`_compute_step_ms()`): berechnet aus `hatch_servo.speed_deg_per_s` und dem µs-pro-Grad-Verhältnis des Lukenservos das Schrittintervall in ms, bei fester Schrittgröße `HATCH_STEP_US`. Wird bei jedem `_move_hatch`-Aufruf neu berechnet, sodass eine Geschwindigkeitsänderung sofort wirkt.

**Hardstop-Enforcement:** Zielwert wird vor der Sequenz auf `[hard_stop_min_deg, hard_stop_max_deg]` (global, einheitlich für alle 4 Luken) geclampt und zurück in `targets` geschrieben.

**Fehlerbehandlung:** Exceptions aus `_move_hatch` werden in `task_control` gefangen, auf der seriellen Konsole geloggt, der Loop fährt mit der nächsten Luke fort. Das Relais ist durch den `finally`-Block immer sicher aus. Unerwartete, nicht gefangene Exceptions reißen den Event-Loop mit → WDT-Reset → Fail-Safe.

**`targets`-Dict:** Beim Start aus dem persistierten Soll aufgebaut:

````python
targets = [{"hatch_deg": h["target_deg"]} for h in state["hatches"]]
````

Setzen von `targets[i]["hatch_deg"]` durch `task_webserver` (mit sofortiger `save_state`-Persistenz) löst die Bewegung aus. Wenn Soll == Ist → nichts passiert.

### 9.12 `app/task_button.py`

Pollt GPIO 2 alle 50ms. Erkennt fallende Flanke (1→0) mit `BTN_DEBOUNCE_MS` Entprellung. Unterscheidet kurzen und langen Tastendruck anhand der Haltezeit:

- **Kurzer Druck** (< `BTN_LONG_PRESS_MS` = 3s): `session.show_qr()` → OLED zeigt QR-Code für 15s
- **Langer Druck** (≥ 3s): `session.unlock_config()` → 60s-Freigabefenster für Config-Seite

```python
# Pseudocode der Logik:
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

Aktualisiert OLED alle 500ms. Layout (128×64 px, 8 Zeilen à 16 Zeichen):

```
Zeile 0: "<short_name> <status>"   Luke 1
Zeile 1: "<short_name> <status>"   Luke 2
Zeile 2: "<short_name> <status>"   Luke 3
Zeile 3: "<short_name> <status>"   Luke 4
Zeile 4: IP-Adresse / "Kein WLAN"
Zeile 5: "CFG AKTIV" / "CFG BEREIT" (wenn Session/Unlock aktiv)
Zeile 6–7: reserviert
```

Status-Werte: `"BEWEGT"` (target≠actual, auch während Fail-Safe-Wiederherstellung), `"?ZU?"` (Ist == Soll == `FAILSAFE_DEG`: schwerkraftbedingt zugefallen, unverriegelt), `"ZU"` (pos_idx=0), `"AUF"` (letzter Index), `"P2"…"P5"` (Zwischenpositionen), `"???"` (Position nicht in Positionsliste – sollte im Normalbetrieb nicht mehr auftreten).

**QR-Modus** (`session.is_qr_active()` = True, ausgelöst durch kurzen Tastendruck):

- Ganzes Display weiß, schwarzer QR-Code zentriert
- URL: `http://<aktuelle-IP>` (STA oder AP)
- QR-Matrix wird einmalig via `uQR` generiert und gecacht solange sich die URL nicht ändert (Generierung dauert ca. 0,5 s)
- Scale 1px/Modul + 4px Quiet Zone → passt für typische Home-IPs auf 128×64
- Mit iOS Kamera-App direkt scannbar
- Wenn `uQR` nicht in `/lib` liegt: Fallback-Text `"uQR not found"` auf Display

### 9.14 `app/task_webserver.py`

microdot-Webserver auf Port 80. Modulvariablen `_hw`, `_config`, `_state`, `_targets` werden einmalig beim Task-Start gesetzt (route handlers können keine zusätzlichen Parameter empfangen).

**Routen:**


| Methode | Pfad                  | Funktion                                       |
| ------- | --------------------- | ---------------------------------------------- |
| GET     | `/`                   | `index.html`                                   |
| GET     | `/config`             | `config.html` (Session aktivieren)             |
| GET     | `/ap`                 | `ap_setup.html`                                |
| GET     | `/static/<path>`      | statische Dateien aus`/webpages/static/`       |
| GET     | `/sse`                | SSE-Stream, alle 500ms                         |
| GET     | `/api/config`         | aktuelle`config.json` als JSON                 |
| POST    | `/api/hatch/<i>/pos`  | Sollwert setzen`{position_idx: N}`             |
| POST    | `/api/hatch/<i>/test` | Direktanfahren`{hatch_deg: N}` (Session nötig) |
| POST    | `/api/config`         | Neue Config validieren, sofort persistieren + im RAM anwenden – **kein Reset mehr**; wird bei jeder einzelnen Feld-Übernahme sowie bei jedem Positions-Einfügen/-Entfernen erneut mit dem kompletten aktuellen Dokument aufgerufen (Session nötig) |
| POST    | `/api/wifi`           | WLAN-Daten speichern + Reset                   |
| GET     | `/api/wifi/scan`      | WLAN-Scan → JSON`{networks: [{ssid, rssi}]}`  |

`POST /api/config/apply` **entfällt ersatzlos** – seine Funktion (In-RAM-Update ohne Reset) ist jetzt fester Bestandteil von `POST /api/config`.

**SSE-Implementierung:** Der `/sse`-Stream verwendet eine klassenbasierte `_SseStream`-Klasse statt eines async-Generators, da MicroPython bei async-Generatoren mit gleichzeitigem `yield` und `await` einen `TypeError` im microdot-Write-Pfad wirft (zweite Iteration liefert kein Bytes-Objekt). Die Klasse implementiert das `__aiter__`/`__anext__`-Protokoll explizit.

**Config-Session-Schutz:**

- `/config`: erreichbar wenn `is_session_active()` ODER `is_unlock_active()` → aktiviert Session
- `/api/hatch/<i>/test`: nur wenn `is_session_active()`
- `POST /api/config`: nur wenn `is_session_active()`

**SSE-Payload:**

```json
{
  "hatches": [
    {"name": "Luke 1", "short_name": "Luke1", "current_deg": 0,
     "target_deg": 0, "moving": false, "failsafe": false,
     "pos_idx": 0, "num_positions": 3}
  ],
```

### 9.15 `app/fatal_error.py`

Zentraler Handler für fatale Boot-Fehler. Ersetzt das frühere Muster von `return` aus `main()`, das in eine unkontrollierte WDT-Reboot-Schleife führte.

```python
async def fatal_error(message, hw=None, display=None)
```

Ablauf:
1. Fail-Safe-Fahrt versuchen wenn `hw` vorhanden (try/except – Fehler werden ignoriert)
2. Permanente Fehlermeldung auf OLED wenn `display` vorhanden
3. WLAN-AP „Dachluke-Setup" starten damit die Fehlerseite erreichbar ist
4. Minimalen Webserver auf Port 80 starten und **nie zurückkehren**

Kein WDT wird gestartet → Power-Cycle ist der einzige Ausweg. Aufgerufen aus `main()` anstelle jedes `return` nach einem fatalen Fehler.

### 9.16 `app/config_migration.py`

Wird in `main()` Stage 2b **vor** `load_config()` aufgerufen. Stellt sicher dass `config.json` existiert und alle erforderlichen Felder enthält.

```python
def migrate_config()   # raises ConfigMigrationError wenn Template fehlt
```

Logik:
- Template (`/config.json.template`) fehlt → `ConfigMigrationError` (fatal)
- `config.json` fehlt → Template verbatim kopieren
- `config.json` vorhanden und valide → nichts tun
- `config.json` vorhanden aber ungültig oder Felder fehlen/umbenannt → Merge: bekannte Werte übernehmen, fehlende aus Template ergänzen, unbekannte Felder verwerfen

Merge ist rekursiv für verschachtelte Dicts. `hatches`-Liste wird immer als 4 Einträge verarbeitet. `positions`-Liste wird aus `config.json` übernommen wenn nicht leer (kalibrierte Werte erhalten).

---

## 10. Web-Interface

### 10.1 `index.html` – Bedienwebseite

- Alpine.js `app()` Komponente auf `<body>`
- SSE-Verbindung auf `/sse` mit Auto-Reconnect nach 3s
- Pro Luke eine Bootstrap Card mit Positionsbuttons
- Button-Labels: `ZU` (Index 0), `AUF` (letzter), `P2`…`P5` (Zwischenpositionen)
- Aktive Position: `btn-primary`, andere: `btn-outline-secondary`
- BEWEGT: Badge pulsiert (CSS-Animation)
- Config-Button: erscheint/verschwindet reaktiv ohne Seitenreload

### 10.2 `config.html` – Konfigurationswebseite

- Alpine.js `configApp()` Komponente
- Lädt Config via `GET /api/config` beim Seitenstart
- Bootstrap Accordion: Globale Einstellungen (Lukenservo + Bremsservo) + 4 Luken-Sektionen
- **Kein Sammel-Speichern-Button mehr** (weder oben noch unten) – jedes Feld persistiert einzeln und sofort bei ✓

**Feldzustände** (nur noch zwei statt drei, da Übernahme sofort persistiert):

- `draft !== committed` → gelb (`field-edited`) – getippt, noch nicht übernommen
- sonst → weiß (übernommen = bereits dauerhaft gespeichert)

**Interaktion pro Feld:** ↺ (Zurücksetzen auf `committed`), Eingabefeld, ✓ (Übernehmen → lokale Validierung → komplettes aktuelles Dokument per `POST /api/config` senden → bei Erfolg `committed = draft`, bei Fehler Rollback + Fehlermeldung).

**Anfahren-Button (▶):** nur aktiv wenn `draft === committed` (d.h. Wert bereits übernommen und persistiert) → sendet direkt `POST /api/hatch/<i>/test` mit `{hatch_deg}`, **ohne** vorherigen Apply-Schritt (entfällt, da der Wert durch ✓ schon aktiv ist). Führt die vollständige Bewegungssequenz aus (Bremse auf/zu via `task_control`).

**Persistenz (`persistConfig()`):** Baut das komplette aktuelle Konfigurationsdokument aus allen `committed`-Werten (`buildConfig()`) und sendet es per `POST /api/config`. Kein Reset, keine Weiterleitung – die Antwort kommt synchron zurück, bei Fehler wird der zuvor geänderte Wert lokal zurückgerollt und eine Fehlermeldung angezeigt.

**Positionen:** `positions[0]` (ZU) und `positions[length-1]` (AUF) sind feste Randwerte und werden nie verschoben. Eine neue Zwischenposition wird immer unmittelbar **vor AUF** eingefügt, Default-Wert = Mittelwert aus AUF und der bisher letzten Zwischenposition (bzw. aus AUF und ZU, falls noch keine Zwischenposition existiert). Entfernen löscht die jeweils letzte Zwischenposition (Index `length-2`), nie ZU oder AUF selbst. Beides sofort persistiert (kein zusätzlicher ✓-Klick).

### 10.3 `ap_setup.html` – WLAN-Konfiguration

- Scan startet automatisch beim Seitenaufruf (`GET /api/wifi/scan`)
- Ergebnisse als anklickbare Liste mit RSSI-Balken-Anzeige
- Klick → füllt SSID-Feld
- Passwort-Feld mit Anzeigen/Verbergen-Toggle
- Submit → `POST /api/wifi` → `machine.reset()`
- Nach Verbindungsabbruch (Gerät hat AP deaktiviert): Hinweis, dass Gerät neu startet und IP auf OLED angezeigt wird

### 10.4 Bibliotheken (CDN mit lokalem Fallback)

- Bootstrap 5.3.3 (`bootstrap.min.css`, `bootstrap.bundle.min.js`)
- Alpine.js 3.14.1 (`alpine.min.js`)

Alle drei HTML-Seiten laden die Bibliotheken **primär vom CDN** (jsdelivr.net). Bei nicht erreichbarem CDN greift ein `onerror`-Handler auf die lokalen Kopien in `source/webpages/static/` zurück. Der Browser cached CDN-Dateien nach dem ersten Laden, danach entfällt der Netzwerkzugriff komplett.

Alpine.js wird **ohne** `defer` am Ende von `<body>` geladen, damit die `function appName()` bereits definiert ist wenn Alpine initialisiert.

---

## 11. Async-Architektur

### 11.1 Tasks (alle in `uasyncio.gather()`)


| Task             | Intervall                       | Aufgabe                               |
| ---------------- | ------------------------------- | ------------------------------------- |
| `task_watchdog`  | 200ms                           | Hardware-WDT füttern                 |
| `task_display`   | 500ms                           | OLED aktualisieren                    |
| `task_control`   | 50ms (Idle) / sofort (Bewegung) | Soll/Ist-Vergleich, Bewegungssequenz  |
| `task_button`    | 50ms                            | Button pollen, Session-Unlock starten |
| `task_webserver` | event-driven                    | HTTP-Requests, SSE-Stream             |

### 11.2 Coding-Regel

**Jede `while`-Schleife im Projekt muss mindestens ein `await` enthalten.**

Hintergrund: kooperatives Multitasking ohne Prioritäten. Blockierender Code ohne `await` verhindert, dass `task_watchdog` läuft → WDT-Reset.

### 11.3 Hardware-Watchdog

- Timeout: 4000ms
- Feed-Intervall: 200ms
- Initialisiert innerhalb `task_watchdog` (nicht in `main.py`)
- WDT ist erst aktiv wenn `task_watchdog` gestartet wurde (d.h. nach erfolgreichem Abschluss aller Startphasen)

---

## 12. Fail-Safe

**Auslöser:**

- WDT-Reset (Programm hat nicht mehr reagiert)
- Unbehandelter Fehler im Startablauf (return aus `main()`)

**Sequenz** (`fail_safe_close_all`):

1. Für jede Luke: Bremsen-Servo-PWM auf `brake_open_us` setzen
2. Relais ein
3. `time.sleep_ms(BRAKE_TRAVEL_MS)` – Bremse öffnet
4. Relais aus – Dachluke schließt durch Schwerkraft; Brems-Servo bleibt stromlos in Stellung „auf" (Federkraft reicht nicht aus, den Servo über die Nockenscheibe zu verstellen → Luke bleibt unverriegelt)
5. 200ms Pause → nächste Luke

**Warum ruckt der Luken-Servo im Fail-Safe nicht?**
`fail_safe_close_all` setzt ausschließlich den Brems-Kanal auf einen gültigen PWM-Wert. Der **Luken-Kanal bleibt auf 0µs** – kein gültiger Puls. Der Servo-Controller erkennt das fehlende Signal und aktiviert seinen Motor nicht. Dieses Verhalten wird in der normalen Bewegungssequenz bewusst nachgebildet: `step_preset_servos` setzt den Luken-Kanal auf 0µs vor dem Relay-Ein, `step_preset_delay` gibt dem Controller-Bootvorgang Zeit bevor das erste gültige Signal kommt.

Nach Fail-Safe (WDT-Reset): normaler Startablauf wird fortgesetzt. In Stage 2b werden alle Luken in `state.json` auf `FAILSAFE_DEG` (-1) gesetzt – Soll und Ist stimmen damit mit der physischen Realität überein. `task_control` bleibt danach passiv (Soll == Ist == `FAILSAFE_DEG`). Erst ein neuer Nutzerbefehl (echte Grad-Position) löst die vollständige Bewegungssequenz inkl. Verriegelung aus.

---

## 13. Config-Session (Sicherung der Konfigurationsseite)

**Ziel:** Konfigurationsseite nur nach physischem Tastendruck am Gerät erreichbar.

**Ablauf:**

1. User-Button im Normalbetrieb drücken → `session.unlock_config()` → 60s-Fenster
2. Innerhalb 60s: Config-Button erscheint auf Bedienwebseite
3. Config-Button klicken → `/config` aufrufen → `session.activate_session()` → 1h-Session
4. Während 1h: Config-Seite über Button oder Direktaufruf erreichbar
5. Nach 1h: Button verschwindet beim nächsten Seitenaufruf, Direktaufruf geblockt

**Sicherheitsebene:**

- Physischer Zugang zum Gerät nötig (Button drücken)
- Zeitbegrenztes Fenster
- `/api/hatch/<i>/test` und `POST /api/config` nur mit aktiver Session

---

## 14. QR-Code-Anzeige

**Zweck:** Einfacher Zugang zur Web-UI ohne IP-Adresse tippen zu müssen.

**Auslösung:** Kurzer Tastendruck (< 3s) auf den User-Button.

**Ablauf:**
1. `session.show_qr()` setzt `_qr_until = time.time() + 15`
2. `task_display` erkennt `is_qr_active()` = True und ruft `_render_qr()` auf
3. QR-Matrix wird einmalig via `uQR` generiert (URL = `http://<IP>`) und gecacht
4. OLED zeigt weißen Hintergrund mit schwarzem QR-Code (zentriert, 4px Quiet Zone)
5. Nach 15s kehrt `task_display` automatisch zum Normalmodus zurück

**Scannbarkeit:** Mit iOS Kamera-App direkt scannbar. Scale 1 (1px/Modul) ist bei 128×64 ausreichend – Scale 2 würde für längere URLs nicht mehr passen.

**Bibliothek:** `uQR` (JASchilz/uQR) – erzeugt eine 2D-Bool-Matrix. True = schwarzes Modul, False = weißes Modul. Muss manuell als `/lib/uQR.py` auf das Gerät kopiert werden (nicht per mip installierbar).

---

## 15. Bekannte Fallstricke & Diagnosewissen

### I²C-Adresse 0x7F nicht per scan() findbar

`i2c.scan()` deckt per I²C-Spezifikation nur `0x08–0x77` ab. Der Seeed Grove PCA9685 hat defaultmäßig Adresse `0x7F` (alle Adress-Pads auf High). Er erscheint daher **nie** in Scan-Ergebnissen, ist aber direkt ansprechbar. Diagnose: `i2c.writeto(0x7F, b'')` + `i2c.readfrom(0x7F, 1)` – MODE1-Register `0x11` bedeutet Gerät antwortet korrekt.

### Bus-Kurzschluss-Pattern

Wenn `i2c.scan()` alle Adressen von 8 bis 119 zurückgibt → SDA oder SCL falsch (z.B. auf GND gezogen). Kein echter Scan-Befund, sondern Artefakt. Korrekte Pins für XIAO ESP32-S3 auf Expansion Board: SDA=5, SCL=6.

### config.json Markdown-Backticks

Beim Kopieren von JSON aus Chat-Konversationen können Markdown-Backticks (` ``` `) am Ende der Datei landen. MicroPython's `ujson` wirft dann `JSON parse error`. Datei im Editor prüfen und Backticks entfernen.

### mpremote und Port-Wechsel

mpremote toggelt beim Verbinden DTR/RTS → Hardware-Reset des ESP32 → Port wechselt von `/dev/ttyACM0` auf `/dev/ttyACM1`. Wenn Skripte dann mit dem alten Port weitermachen, schlagen sie fehl. Lösung: Gerät kurz vom USB trennen und neu anstecken.

### Lock-Dateien blockieren mpremote

MicroPython Studio hinterlässt Lock-Dateien in `/tmp/`. Bei Abstürzen oder hartem Disconnect bleiben sie stehen:
```bash
rm /tmp/mps_lock__dev_ttyACM*.lock
```

### main.py unterbrechen für REPL-Zugang

`main.py` startet sofort und blockiert den REPL. Umbenennen um freien REPL zu bekommen:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main.py', '/main_bak.py')"
```
Zurückbenennen:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main_bak.py', '/main.py')"
```

---

## 16. Lokales Bedienfeld – zurückgestellt

Geplant mit MCP23017 (I²C 0x20): 4 Lukentasten + N Positionstasten + N LEDs.

**Zurückgestellt bis:** Anzahl der tatsächlichen Bremsen-Rastpositionen durch Hardware-Test bekannt.

**Konzept:** Luke-Taste drücken → LEDs zeigen aktuelle Position; Positions-Taste drücken → Sollwert setzen (wie Web-Interface). MCP23017-Interrupt-Ausgang vorhanden für effizienten Betrieb.

Implementierung: neues Modul `app/task_panel.py`, Import in `main.py`, in `gather()` hinzufügen.

---

## 17. Zukünftige Erweiterungen

### 17.1 Regensensor (separates Hardware-Projekt)

- Elektrodynamischer wetterfester Exciter auf Metallplatte (schräg montiert)
- Umgekehrt als Sensor betrieben: Aufprall → Spannungspuls (niederohmig, kein Ladungsverstärker)
- TLV3691-Komparator (75 nA) als Always-On-Wake-up
- MCU (z.B. XIAO ESP32-S3) im Deep Sleep, wacht per Komparator auf
- LoRa-Funk (RFM95, Punkt-zu-Punkt) zum Hauptgerät
- Intensitätsstufen (0–3) aus Pulsrate und Amplitude
- Batteriebetrieb möglich (Jahre Laufzeit)

Anbindung im Hauptsystem: zusätzlicher Task oder Webhook-Endpunkt → setzt Sollwerte über `targets`.

### 17.2 RTC-Zeitplanung

PCF8563 auf Expansion Board bereits vorhanden (I²C 0x51). Automatisches Öffnen/Schließen zu konfigurierten Zeiten → neuer Task + Erweiterung der Konfigurationswebseite.

### 17.3 Event/Trigger-System

Für alle Erweiterungen ist eine saubere Abstraktionsschicht geplant:

- Trigger-Quellen: Web-UI, lokale Taster, Regensensor, RTC
- Aktion: `targets[i]["hatch_deg"] = neue_position_deg`
- Neue Quellen ohne Umbau der Kernlogik ergänzbar

---

## 18. Offene Punkte / Nächste Schritte

1. **Hardware-Test** (XIAO ESP32-S3 steht kurz bevor):

   - MicroPython flashen
   - `upload.sh` testen
   - I²C-Scan: alle Geräte auf dem Bus prüfen
   - `hw_init.py` testen: alle Komponenten initialisieren
   - Webserver: Erreichbarkeit, SSE, Buttons
   - `task_control.py`: erste echte Lukenbewegung
   - Konfigurationswebseite: Positionen justieren
2. **Lokales Bedienfeld:** Nach Hardware-Test und Bestimmung der Rastpositionen.
3. **Unit-Tests** (auf Eis): Sinnvoll für `state.py`-Validierung und `pca9685.py`-Berechnung – nach Stabilisierung der API.
4. **Regensensor:** Eigenständiges Prototyping-Projekt.
