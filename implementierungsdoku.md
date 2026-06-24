# Implementierungsdokumentation – Dachlukensteuerung

Dieses Dokument beschreibt die gesamte Hard- und Software der Dachlukensteuerung so vollständig, dass in einem neuen Chat (zusammen mit dem Quellcode) sofort mit Test, Debugging oder Erweiterungen weitergearbeitet werden kann.

---

## 1. Projektziel

Steuerung von bis zu 4 Dachluken über Web-Interface (Handy, Tablet, Desktop) sowie optional über ein lokales Bedienfeld mit Tastern und LEDs. Pro Dachluke ein kräftiger Servo für die Bewegung und ein zweiter Servo für eine mechanische Feststellbremse. Betrieben auf einem Seeed XIAO ESP32-S3 mit MicroPython.

---

## 2. Hardware

### 2.1 Komponenten

| Komponente | Funktion |
|---|---|
| Seeed XIAO ESP32-S3 | Mikrocontroller, WLAN |
| Seeed Expansion Board Base for XIAO | I²C-Hub, OLED (SSD1306 128×64), Micro-SD-Slot, RTC (PCF8563), User-Button |
| Grove 4-Channel SPDT Relay | Servo-Spannungsversorgung schalten |
| Grove 16-Channel PWM Driver (PCA9685) | PWM-Signale für alle Servos |
| MCP23017 (Breakout) | I²C-GPIO-Expander für lokales Bedienfeld – **zurückgestellt** |

### 2.2 I²C-Bus (alle Geräte auf einem Bus)

| Gerät | Adresse |
|---|---|
| SSD1306 OLED | 0x3C |
| PCA9685 PWM-Driver | 0x40 |
| Grove 4-Ch. Relay | 0x11 |
| RTC PCF8563 | 0x51 |
| MCP23017 (zurückgestellt) | 0x20 |

**GPIO-Pins des Expansion Boards (XIAO ESP32-S3):**

| Signal | GPIO |
|---|---|
| I²C SDA | 4 |
| I²C SCL | 5 |
| User-Button | 1 (aktiv low, Pull-up) |

### 2.3 Elektrische Verdrahtung

- **Pro Dachluke ein Relais** (NO-Kontakt): schaltet die gemeinsame +8V-Ader beider Servos dieser Luke.
- GND durchgehend gemeinsam für alle Servos.
- Jeder Servo hat eine eigene PWM-Leitung vom PCA9685.
- Netzteil: 3A / 8V – ausreichend für **eine Luke gleichzeitig** (softwareseitig garantiert).

### 2.4 PCA9685-Kanalzuordnung

| Kanal | Funktion |
|---|---|
| 0 | Luke 1 – Dachluke-Servo |
| 1 | Luke 1 – Brems-Servo |
| 2 | Luke 2 – Dachluke-Servo |
| 3 | Luke 2 – Brems-Servo |
| 4 | Luke 3 – Dachluke-Servo |
| 5 | Luke 3 – Brems-Servo |
| 6 | Luke 4 – Dachluke-Servo |
| 7 | Luke 4 – Brems-Servo |

Konfiguriert in `hw_config.py` als Dict `PCA9685_CH = {0: (0,1), 1: (2,3), ...}`.

### 2.5 Relay-Zuordnung

| Relay-Kanal | Funktion |
|---|---|
| 0 | +8V für Servos Luke 1 |
| 1 | +8V für Servos Luke 2 |
| 2 | +8V für Servos Luke 3 |
| 3 | +8V für Servos Luke 4 |

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

---

## 4. Plattform

**MicroPython** auf XIAO ESP32-S3.

Begründung: `uasyncio` für kooperatives Multitasking, `microdot` als async-Webserver, eingebautes `ujson`, stabile I²C-Bibliotheken.

Externe Bibliotheken (via `mip`, verwaltet durch `requirements.txt`):
- `ssd1306` – OLED-Treiber
- `microdot` (via `github:miguelgrinberg/microdot`) – Webserver

Eigene Treiber im Projekt:
- `shared/pca9685.py` – PCA9685-Treiber (selbst geschrieben, da verfügbare PyPI-Pakete veraltet/deprecated)
- `shared/grove_relay_4chn_spdt.py` – Grove Relay-Treiber (kein MicroPython-Paket verfügbar)

---

## 5. Projektstruktur

### 5.1 Verzeichnis auf dem Entwicklungsrechner

```
roof_hatch_controller/
├── konzept.md                   # ursprüngliches Designkonzept (veraltet)
├── impl_doc.md                  # dieses Dokument
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
    │   └── grove_relay_4chn_spdt.py  # Grove Relay-Treiber
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

### 6.2 `upload.sh`

Konfigurationsblock oben im Script:
- `UPLOAD_MODE`: `"usb"` oder `"wifi"`
- `USB_PORT`: z.B. `/dev/ttyUSB0`
- `WIFI_IP`: IP des Geräts für WebREPL

Ablauf:
1. `requirements.txt` durchgehen → fehlende Pakete per `micropython -m mip install --target lib/` nachladen
2. Zielverzeichnisse auf dem Gerät löschen (`app/`, `shared/`, `lib/`, `webpages/`)
3. `source/`-Inhalte hochladen: `boot.py`, `main.py`, `config.json`, `app/`, `shared/`, `webpages/`
4. `lib/` hochladen (falls nicht leer)
5. Gerät resetten

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

```json
{
    "global": {
        "brake_open_us": 1200,
        "brake_close_us": 800,
        "delay_after_power_on_ms": 300,
        "delay_after_brake_open_ms": 400,
        "delay_after_move_ms": 300,
        "delay_after_brake_close_ms": 500
    },
    "hatches": [
        {
            "name": "Luke 1",
            "short_name": "Luke1",
            "hatch_min_us": 600,
            "hatch_max_us": 2400,
            "step_us": 10,
            "step_ms": 10,
            "positions": [
                {"hatch_us": 600},
                {"hatch_us": 1500},
                {"hatch_us": 2400}
            ]
        }
        // ... Luke 2, 3, 4 identisch
    ]
}
```

**Felder global:**
- `brake_open_us` / `brake_close_us`: Bremsen-Servo-Positionen (µs), für alle Luken gleich
- `delay_after_power_on_ms`: Wartezeit nach Relais-Ein bevor Bremse öffnet
- `delay_after_brake_open_ms`: Wartezeit nach Bremse-Öffnen bevor Luke fährt
- `delay_after_move_ms`: Wartezeit nach Erreichen der Zielposition bevor Bremse schließt
- `delay_after_brake_close_ms`: Wartezeit nach Bremse-Schließen bevor Relais-Aus

**Felder pro Luke:**
- `name`: Anzeigename (Web-Interface)
- `short_name`: max. 6 Zeichen für OLED-Display
- `hatch_min_us` / `hatch_max_us`: Hardstop-Grenzen – Software fährt nie außerhalb
- `step_us` / `step_ms`: Schrittgröße und Intervall für die Bewegungsrampe
- `positions`: Liste von Rastpositionen `[{"hatch_us": N}, ...]`, min. 2

### 7.2 `/wifi.json` – WLAN-Zugangsdaten

```json
{"ssid": "MeinNetzwerk", "password": "MeinPasswort"}
```

Wird über den AP-Modus geschrieben. Liegt im internen Flash, nicht auf SD. Nicht im Git-Repo.

### 7.3 `/state.json` – Letzter bekannter Servo-Zustand

```json
{
    "hatches": [
        {"hatch_us": 600, "brake_us": 800},
        {"hatch_us": 600, "brake_us": 800},
        {"hatch_us": 600, "brake_us": 800},
        {"hatch_us": 600, "brake_us": 800}
    ]
}
```

Beim allerersten Start nicht vorhanden → `state.py` erzeugt Standardzustand aus `config.json` (alle Luken auf `hatch_min_us`, Bremse auf `brake_close_us`) und speichert ihn.

**Wichtig:** Vor jedem Relais-Ein werden diese Werte an PCA9685 ausgegeben, damit der Servo aus der bekannten Position hält und kein Ruck entsteht.

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

Stage 3:  WLAN
          → boot.AP_MODE=True: start_ap_mode()
          → boot.AP_MODE=False: start_client_mode()

Stage 4:  targets-Dict initialisieren (RAM-only, aus state)
          → targets = [dict(h) for h in state["hatches"]]

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
NUM_HATCHES  = 4
BRAKE_TRAVEL_MS        = 500   # Wartezeit für Bremsen-Servo im Fail-Safe
BRAKE_OPEN_US_FAILSAFE = 1200  # Fallback-Wert ohne config.json
PCA9685_CH = {0:(0,1), 1:(2,3), 2:(4,5), 3:(6,7)}
RELAY_CH   = {0:0, 1:1, 2:2, 3:3}
```

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
- Alle Required-Keys vorhanden
- Servo-µs-Werte innerhalb [SERVO_MIN_US, SERVO_MAX_US]
- `hatch_min_us < hatch_max_us`
- Positionen innerhalb Hardstop-Grenzen
- Exakt NUM_HATCHES Einträge

**Erstes Startup:** `load_state()` wirft `StateError` → `create_default_state(config)` + `save_state()`.

### 9.7 `app/failsafe.py`

Synchrone Funktion (kein asyncio) – sicher vor dem Event-Loop aufrufbar.

```python
fail_safe_close_all(hw, brake_open_us=None)
```

Ablauf für jede Luke sequenziell:
1. Bremsen-Servo auf `brake_open_us` setzen
2. Relais ein
3. `time.sleep_ms(BRAKE_TRAVEL_MS)` warten (Bremsen-Servo fährt)
4. Relais aus → Dachluke schließt durch Schwerkraft, Bremse schnappt ein
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

Herzstück der Anwendung. Vergleicht `targets[i]["hatch_us"]` mit `state["hatches"][i]["hatch_us"]` für alle 4 Luken sequenziell.

**Bewegungssequenz (`_move_hatch`):**
1. PCA9685: Ist-Positionen beider Servos ausgeben (aus `state`)
2. Relais ein; Watchdog-Timestamp setzen
3. `await sleep_ms(delay_after_power_on_ms)`
4. Brems-Servo → `brake_open_us`
5. `await sleep_ms(delay_after_brake_open_ms)`
6. Dachluke-Servo schrittweise zur Zielposition (`step_us` pro Schritt, `await sleep_ms(step_ms)`)
7. `await sleep_ms(delay_after_move_ms)`
8. Brems-Servo → `brake_close_us`
9. `await sleep_ms(delay_after_brake_close_ms)`
10. `finally:` Relais aus (immer, auch bei Fehler)
11. `state` aktualisieren + `save_state()`

**Hardstop-Enforcement:** Zielwert wird vor der Sequenz auf `[hatch_min_us, hatch_max_us]` geclampt.

**Software-Watchdog** (innerhalb `_move_hatch`): `time.ticks_diff` gegen `SERVO_WATCHDOG_MS = 30_000` (30s). Bei Überschreitung: Relais-Aus im `finally`, `targets[i]` auf aktuellen `state` zurücksetzen (kein Retry-Loop).

**`targets`-Dict:** RAM-only, beim Start aus `state["hatches"]` initialisiert:
```python
targets = [dict(h) for h in state["hatches"]]
```
Setzen von `targets[i]["hatch_us"] = neue_position_us` durch `task_button` oder `task_webserver` löst die Bewegung aus. Wenn Soll == Ist → nichts passiert.

### 9.12 `app/task_button.py`

Pollt GPIO 1 alle 50ms. Erkennt fallende Flanke (1→0) mit `BTN_DEBOUNCE_MS` Entprellung. Bei bestätigtem Tastendruck: `session.unlock_config()`.

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

Status-Werte: `"ZU"` (pos_idx=0), `"AUF"` (letzter Index), `"P2"…"P5"` (Zwischenpositionen), `"BEWEGT"` (target≠actual), `"???"` (Position nicht in Liste).

### 9.14 `app/task_webserver.py`

microdot-Webserver auf Port 80. Modulvariablen `_hw`, `_config`, `_state`, `_targets` werden einmalig beim Task-Start gesetzt (route handlers können keine zusätzlichen Parameter empfangen).

**Routen:**

| Methode | Pfad | Funktion |
|---|---|---|
| GET | `/` | `index.html` |
| GET | `/config` | `config.html` (Session aktivieren) |
| GET | `/ap` | `ap_setup.html` |
| GET | `/static/<path>` | statische Dateien aus `/webpages/static/` |
| GET | `/sse` | SSE-Stream, alle 500ms |
| GET | `/api/config` | aktuelle `config.json` als JSON |
| POST | `/api/hatch/<i>/pos` | Sollwert setzen `{position_idx: N}` |
| POST | `/api/hatch/<i>/test` | Direktanfahren `{hatch_us: N}` (Session nötig) |
| POST | `/api/config` | Neue Config validieren + speichern + Reset |
| POST | `/api/wifi` | WLAN-Daten speichern + Reset |
| GET | `/api/wifi/scan` | WLAN-Scan → JSON `{networks: [{ssid, rssi}]}` |

**Config-Session-Schutz:**
- `/config`: erreichbar wenn `is_session_active()` ODER `is_unlock_active()` → aktiviert Session
- `/api/hatch/<i>/test`: nur wenn `is_session_active()`
- `POST /api/config`: nur wenn `is_session_active()`

**SSE-Payload:**
```json
{
  "hatches": [
    {"name": "Luke 1", "short_name": "Luke1", "current_us": 600,
     "target_us": 600, "moving": false, "pos_idx": 0, "num_positions": 3}
  ],
  "wifi": {"connected": true, "ip": "192.168.1.42"},
  "session": {"active": false, "unlock": false}
}
```

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
- Bootstrap Accordion: Globale Einstellungen + 4 Luken-Sektionen

**Feldzustände:**
- `draft !== val` → gelb (`field-edited`) – getippt, nicht übernommen
- `val !== orig` → blau (`field-accepted`) – übernommen, nicht gespeichert
- sonst → weiß

**Interaktion pro Feld:** ↺ (Zurücksetzen auf `orig`), Eingabefeld, ✓ (Übernehmen → Validierung → `val = parsed`)

**Anfahren-Button (▶):** nur aktiv wenn `draft === val` (d.h. Wert übernommen) → sendet `POST /api/hatch/<i>/test`. Führt die vollständige Bewegungssequenz aus (Bremse auf/zu via `task_control`).

**Speichern:** Sendet alle `val`-Werte als JSON an `POST /api/config` → Validierung server-seitig → Schreiben → `machine.reset()`. Fetch-Fehler = Gerät hat neu gestartet → Redirect nach `/` nach 5s.

**Positionen:** Dynamisch hinzufügen/entfernen (min. 2). Neuer Eintrag wird mit dem letzten `val` vorbelegt.

### 10.3 `ap_setup.html` – WLAN-Konfiguration

- Scan startet automatisch beim Seitenaufruf (`GET /api/wifi/scan`)
- Ergebnisse als anklickbare Liste mit RSSI-Balken-Anzeige
- Klick → füllt SSID-Feld
- Passwort-Feld mit Anzeigen/Verbergen-Toggle
- Submit → `POST /api/wifi` → `machine.reset()`
- Nach Verbindungsabbruch (Gerät hat AP deaktiviert): Hinweis, dass Gerät neu startet und IP auf OLED angezeigt wird

### 10.4 Bibliotheken (lokal, kein CDN)

- Bootstrap 5.3.3 (`bootstrap.min.css`, `bootstrap.bundle.min.js`)
- Alpine.js 3.14.1 (`alpine.min.js`)

Abgelegt unter `source/webpages/static/`. Alpine.js wird **ohne** `defer` am Ende von `<body>` geladen, damit die `function appName()` bereits definiert ist wenn Alpine initialisiert.

---

## 11. Async-Architektur

### 11.1 Tasks (alle in `uasyncio.gather()`)

| Task | Intervall | Aufgabe |
|---|---|---|
| `task_watchdog` | 200ms | Hardware-WDT füttern |
| `task_display` | 500ms | OLED aktualisieren |
| `task_control` | 50ms (Idle) / sofort (Bewegung) | Soll/Ist-Vergleich, Bewegungssequenz |
| `task_button` | 50ms | Button pollen, Session-Unlock starten |
| `task_webserver` | event-driven | HTTP-Requests, SSE-Stream |

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
4. Relais aus – Dachluke schließt durch Schwerkraft, Bremse schnappt durch Federkraft ein
5. 200ms Pause → nächste Luke

Nach Fail-Safe: normaler Startablauf wird fortgesetzt.

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

## 14. Lokales Bedienfeld – zurückgestellt

Geplant mit MCP23017 (I²C 0x20): 4 Lukentasten + N Positionstasten + N LEDs.

**Zurückgestellt bis:** Anzahl der tatsächlichen Bremsen-Rastpositionen durch Hardware-Test bekannt.

**Konzept:** Luke-Taste drücken → LEDs zeigen aktuelle Position; Positions-Taste drücken → Sollwert setzen (wie Web-Interface). MCP23017-Interrupt-Ausgang vorhanden für effizienten Betrieb.

Implementierung: neues Modul `app/task_panel.py`, Import in `main.py`, in `gather()` hinzufügen.

---

## 15. Zukünftige Erweiterungen

### 15.1 Regensensor (separates Hardware-Projekt)

- Elektrodynamischer wetterfester Exciter auf Metallplatte (schräg montiert)
- Umgekehrt als Sensor betrieben: Aufprall → Spannungspuls (niederohmig, kein Ladungsverstärker)
- TLV3691-Komparator (75 nA) als Always-On-Wake-up
- MCU (z.B. XIAO ESP32-S3) im Deep Sleep, wacht per Komparator auf
- LoRa-Funk (RFM95, Punkt-zu-Punkt) zum Hauptgerät
- Intensitätsstufen (0–3) aus Pulsrate und Amplitude
- Batteriebetrieb möglich (Jahre Laufzeit)

Anbindung im Hauptsystem: zusätzlicher Task oder Webhook-Endpunkt → setzt Sollwerte über `targets`.

### 15.2 RTC-Zeitplanung

PCF8563 auf Expansion Board bereits vorhanden (I²C 0x51). Automatisches Öffnen/Schließen zu konfigurierten Zeiten → neuer Task + Erweiterung der Konfigurationswebseite.

### 15.3 Event/Trigger-System

Für alle Erweiterungen ist eine saubere Abstraktionsschicht geplant:
- Trigger-Quellen: Web-UI, lokale Taster, Regensensor, RTC
- Aktion: `targets[i]["hatch_us"] = neue_position_us`
- Neue Quellen ohne Umbau der Kernlogik ergänzbar

---

## 16. Offene Punkte / Nächste Schritte

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
