# Implementierungsdokumentation – Dachlukensteuerung

Dieses Dokument beschreibt die gesamte Hard- und Software der Dachlukensteuerung so vollständig, dass in einem neuen Chat (zusammen mit dem Quellcode) sofort mit Test, Debugging oder Erweiterungen weitergearbeitet werden kann. Stand: nach der Pro-Kanal-Konfigurationsumstellung (Servotester, Montagewinkel, zeitbasierte S-Kurven-Rampe, Mehrsprachigkeit). Alle vorherigen Konzept-Dokumente (`konzept_grad_umstellung.md`, `konzept_config_redesign.md`) sind in dieses Dokument eingearbeitet und wurden entfernt – dieses Dokument ist die alleinige Referenz.

---

## 1. Projektziel

Steuerung von bis zu 4 Dachluken über Web-Interface (Handy, Tablet, Desktop). Pro Dachluke ein kräftiger Servo für die Bewegung (Hebelservo) und ein zweiter Servo für eine mechanische Feststellbremse (Halteservo). Betrieben auf einem Seeed XIAO ESP32-S3 mit MicroPython. Zusätzlich ein Servotester-Ausgangspaar für Wartung und Kalibrierung von Servos außerhalb der eingebauten Luken.

---

## 2. Hardware

### 2.1 Komponenten

| Komponente                            | Funktion                                                                    |
| -------------------------------------- | --------------------------------------------------------------------------- |
| Seeed XIAO ESP32-S3                   | Mikrocontroller, WLAN                                                       |
| Seeed Expansion Board Base for XIAO   | I²C-Hub, OLED (SSD1306 128×64), Micro-SD-Slot, RTC (PCF8563), User-Button |
| Grove 4-Channel SPDT Relay            | Servo-Spannungsversorgung schalten (pro Luke)                               |
| Grove 16-Channel PWM Driver (PCA9685) | PWM-Signale für alle Servos (8 Luken-Kanäle + 2 Servotester-Kanäle)         |
| MCP23017 (Breakout)                   | I²C-GPIO-Expander für lokales Bedienfeld – **zurückgestellt**            |

### 2.2 I²C-Bus (alle Geräte auf einem Bus)

| Gerät                     | Adresse      | Bemerkungen                                                                                                                                                                                        |
| -------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSD1306 OLED               | 0x3C         |                                                                                                                                                                                                    |
| PCA9685 PWM-Driver         | 0x7F         | Alle Adress-Pads auf High; erscheint deshalb **nie** in `i2c.scan()`-Ergebnissen (außerhalb des Scan-Bereichs 0x08–0x77), ist aber direkt ansprechbar. Siehe Abschnitt 15. |
| Grove 4-Ch. Relay          | 0x11         | Das Board kann in der I2C-Adresse über Software umkonfiguriert werden (s. https://wiki.seeedstudio.com/Grove-4-Channel_SPDT_Relay/). |
| RTC PCF8563                | 0x51         | Auf Expansion Board vorhanden, aktuell ungenutzt (siehe 17.2)                                                                                                                                     |
| MCP23017 (zurückgestellt) | 0x20         |                                                                                                                                                                                                    |

**GPIO-Pins des Expansion Boards (XIAO ESP32-S3), Stand `hw_config.py`:**

| Signal      | GPIO                   |
| ----------- | ---------------------- |
| I²C SDA    | 5                      |
| I²C SCL    | 6                      |
| User-Button | 2 (aktiv low, Pull-up) |

### 2.2b I²C-Bustaktrate

`I2C_FREQ = 1_000_000` (1MHz) – erhöht gegenüber dem ursprünglichen, konservativen Default von 400kHz, nach erfolgreichem Stabilitätstest aller vier Busteilnehmer bei dieser Frequenz (inklusive mehrfach wiederholter Relais-Schaltvorgänge, dem wahrscheinlichsten Ausfallkandidat, da das Grove-Relay-Board seine I²C-Slave-Funktion in Software auf einem STM32F030 implementiert statt in Silizium).

Die I²C-Spezifikation (NXP/UM10204) definiert benannte Modi mit festen Obergrenzen (keine Pflicht-Zwischenstufen, aber die praktisch relevanten Sprungpunkte für elektrische Anforderungen und Bauteilspezifikation):

| Modus | Max. Taktrate |
|---|---|
| Standard Mode | 100 kHz |
| Fast Mode | 400 kHz |
| Fast Mode Plus (Fm+) | 1 MHz |
| High Speed Mode (Hs-Mode) | 3,4 MHz |
| Ultra Fast Mode (UFm) | 5 MHz |

`1_000_000` liegt exakt auf der oberen Fm+-Grenze – ohne Sicherheitsmarge nach oben, aber innerhalb der spezifizierten Grenzen aller beteiligten Chips (ESP32-S3, PCA9685 und SSD1306 unterstützen laut Datenblatt Fm+ bzw. mehr). High Speed Mode (3,4MHz) wäre technisch keine Option, da er einen speziellen Busmaster-Protokollteil (Master-Code + Umschaltung) erfordert, den weder der ESP32 noch die angeschlossenen Geräte hier unterstützen.

**Kein Umschalten der Taktrate je nach Busteilnehmer:** Die Taktrate wird vom Master (ESP32) für die gesamte gemeinsame Clock-Leitung erzeugt, nicht pro Gerät – anders als die PCA9685-PWM-Frequenz (siehe 9.11) ist hier kein Kandidat für eine Laufzeit-Umschaltung bekannt, der das rechtfertigen würde.

### 2.3 Elektrische Verdrahtung

- **Pro Dachluke ein Relais** (NO-Kontakt): schaltet die gemeinsame +8V-Ader beider Servos dieser Luke (Hebel- und Halteservo werden immer gemeinsam bestromt).
- **Servotester-Ausgänge sind dauerbestromt, ohne eigenes Relais** – gedacht für Servos, die aus der eingebauten Luke ausgebaut und einzeln auf der Werkbank getestet werden. Da nie ein Testerkanal und ein Lukenkanal gleichzeitig unter Last stehen (der Tester wird nur an ausgebauten, unbelasteten Servos benutzt), ist das unkritisch.
- GND durchgehend gemeinsam für alle Servos.
- Jeder Servo hat eine eigene PWM-Leitung vom PCA9685.
- Netzteil: 3A / 8V – ausreichend für **eine Luke gleichzeitig** (softwareseitig garantiert, siehe Abschnitt 11).

### 2.4 PCA9685-Kanalzuordnung

| Kanal | Funktion                          |
| ----- | ---------------------------------- |
| 0     | Luke 1 – Hebelservo                |
| 1     | Luke 1 – Halteservo                |
| 2     | Luke 2 – Hebelservo                |
| 3     | Luke 2 – Halteservo                |
| 4     | Luke 3 – Hebelservo                |
| 5     | Luke 3 – Halteservo                |
| 6     | Luke 4 – Hebelservo                |
| 7     | Luke 4 – Halteservo                |
| 8     | Servotester – Hebelservo-Ausgang   |
| 9     | Servotester – Halteservo-Ausgang   |

Konfiguriert in `hw_config.py`: `PCA9685_CH = {0: (0,1), 1: (2,3), 2: (4,5), 3: (6,7)}` (Luken-Kanäle) und `TESTER_CH = (8, 9)` (Servotester, eigenes Konstantenpaar statt Dict-Eintrag, da nur einmal vorhanden).

### 2.5 Relay-Zuordnung

| Relay-Kanal | Funktion               |
| ----------- | ----------------------- |
| 0           | +8V für Servos Luke 1  |
| 1           | +8V für Servos Luke 2  |
| 2           | +8V für Servos Luke 3  |
| 3           | +8V für Servos Luke 4  |

Konfiguriert als `RELAY_CH = {0: 0, 1: 1, 2: 2, 3: 3}` in `hw_config.py`. Der Servotester hat keinen Relay-Eintrag (siehe 2.3).

---

## 3. Mechanik und Servos

### 3.1 Feststellbremse (Halteservo)

- Rastsystem aus ineinandergreifenden Zähnen – übernimmt Haltemoment wenn Servos stromlos.
- Getriebe der Dachluke **nicht selbsthemmend** → ohne Bremse und Strom schließt die Luke durch Schwerkraft.
- Bremse hat zwei reguläre Betriebspositionen (`open_deg`/`close_deg`), **pro Luke individuell konfigurierbar** (Bremsmechanik kann pro Luke leicht unterschiedlich sitzen), plus einen eigenen Montagewinkel für Wartungszwecke (siehe 3.7).
- Bremse schnappt federbelastet ein; Servo zieht gegen die Feder auf.
- Halteservo kann nicht durch zu weites Fahren kaputt gehen (Nockenprinzip).
- Der Halteservo **rampt** genau wie der Hebelservo (eigene, konfigurierbare Geschwindigkeit `speed_deg_per_s`) – ursprünglich sprang er in einem Schritt direkt zwischen den beiden Positionen; das wurde geändert, weil sich damit zwei feste "hoffentlich reicht die Zeit"-Wartezeiten durch echtes Warten auf das tatsächliche Rampenende ersetzen ließen (siehe 9.11).

### 3.2 Servo-Thermik

Modellbauservos (hier ca. 4,5 Nm / 2–3 A Stallstrom) sind für intermittierenden Betrieb ausgelegt. Dauerlast führt zur Überhitzung. Deshalb: Servos werden **nur während der Bewegungssequenz bestromt** (Relais ein), danach stromlos. Die mechanische Bremse übernimmt das Haltemoment. Der Servotester ist die einzige Ausnahme (dauerbestromt, siehe 2.3) – dort sind aber nie belastete Servos angeschlossen.

### 3.3 Erweiterter PWM-Stellbereich

Jeder Servo (Hebel- und Halteservo, pro Luke, sowie beide Servotester-Ausgänge) hat einen eigenen, individuell konfigurierbaren Stellbereich statt fester Werte:

- `range_deg`: Gesamtstellbereich, Umschaltwert **180°, 270° oder 360°**
- `direction`: Drehrichtung, `"pos"` oder `"neg"` (Default `"neg"`) – legt fest, ob 0° der minimalen oder maximalen Impulszeit entspricht (siehe 9.2b)
- `pulse_min_us`/`pulse_max_us`: Impulszeit-Grenzen in µs, konfigurierbar im Bereich **500–4000µs** (deckt Standard- ebenso wie Long-Throw-Servos ab)

Absolute Hardware-Grenzen dazu in `hw_config.py`: `SERVO_MIN_US = 500`, `SERVO_MAX_US = 4000` – jeder konfigurierte `pulse_min_us`/`pulse_max_us`-Wert muss innerhalb dieses Fensters liegen, unabhängig vom gewählten `range_deg`.

### 3.4 Servo-Einschaltverhalten (Power-On-Jerk)

Digitale Hochmoment-Servos führen beim Einschalten eine interne Initialisierungsroutine durch und fahren dabei kurz mit Maximalgeschwindigkeit in Richtung Mittelstellung – unabhängig vom anliegenden PWM-Signal. Die Bewegungssequenz in `task_control.py` enthält weiterhin einen Workaround dafür: `step_preset_servos` gibt schon vor dem Relais-Ein die Ist-Position aus (Hebelservo auf 0µs = kein Signal, damit der Controller während seines Bootvorgangs kein Signal sieht), `step_preset_delay` gibt dem Controller Zeit zu booten, bevor das erste gültige Signal kommt. Details und Hintergrund: `xiao_esp32s3_erfahrungen.md` Abschnitt 12.

**Historische Anmerkung – Wiggle entfernt:** Zusätzlich zum Preset-Delay gab es früher ein "Wiggle" (Hebelservo alternierte kurz ±10µs um die Zielposition, 5 Zyklen), das denselben Einschalt-Ruck zusätzlich abfangen sollte. Seit die Bewegungsrampe eine S-Kurve fährt (siehe 3.5/9.11) und jede Bewegung ohnehin bei Geschwindigkeit 0 beginnt, trat der Effekt, den Wiggle verhindern sollte, nicht mehr auf – der Mechanismus wurde ersatzlos entfernt.

### 3.5 Bewegungsprofil: S-Kurve statt linearer Rampe

Beide Servos einer Luke (und die beiden Servotester-Ausgänge) fahren nicht linear, sondern nach einem **Smoothstep-Profil** (`3f² − 2f³`, `f` = Bewegungsfortschritt 0…1): Position und ihre erste Ableitung (Geschwindigkeit) sind bei Bewegungsbeginn und -ende exakt Null und stetig – kein Ruck (Sprung in der Beschleunigung) an keinem der beiden Enden. Details zur Implementierung siehe 9.11.

Wichtige Konsequenz für `speed_deg_per_s`: Bei einem Smoothstep-Profil ist die **Spitzengeschwindigkeit** (erreicht in der Bewegungsmitte) das 1,5-fache der Durchschnittsgeschwindigkeit. Die konfigurierte `speed_deg_per_s` ist bewusst als **Spitzengeschwindigkeit** definiert (nicht als Durchschnitt) – die Bewegungsdauer wird deshalb intern mit Faktor 1,5 gegenüber einer rein linearen, gleich schnellen Rampe verlängert, damit das konfigurierte Geschwindigkeitslimit (siehe 3.6) an keiner Stelle der Bewegung überschritten wird.

### 3.6 Geschwindigkeitsgrenzen

Für alle Servos (Hebel- und Halteservo aller 4 Luken, beide Servotester-Ausgänge) gilt derselbe zulässige Bereich für `speed_deg_per_s`: **30–180°/s** (`SPEED_MIN_DEG_PER_S`/`SPEED_MAX_DEG_PER_S` in `hw_config.py`).

### 3.7 Montagewinkel

Zusätzlich zu den regulären Rastpositionen (Hebelservo) bzw. Auf/Zu-Stellungen (Halteservo) hat jeder der beiden Servos pro Luke einen eigenen, separat konfigurierbaren **Montagewinkel** (`mount_angle_deg`). Zweck: eine für die mechanische Montage günstige Winkelstellung anfahren können, unabhängig von den betrieblich sinnvollen Rastpositionen.

**Wichtiger Warnhinweis (gehört zwingend in die Anwenderdokumentation):** Der Montagewinkel des Hebelservos kann eine Position sein, die **nicht ins Zahnschema der Bremse passt** und daher unter Last nicht gehalten werden kann. Der Montagewinkel darf deshalb **nur** angefahren werden, wenn die Last (das Fenster/die Luke) vorher physisch vom Hebel getrennt wurde. Softwareseitig wird der Montagewinkel wie eine ganz normale Zielposition behandelt (inkl. vollständiger Bremse-auf/Bewegung/Bremse-zu-Sequenz) – die Software kann und wird diese Warnung nicht selbst durchsetzen.

---

## 4. Plattform

**MicroPython** auf XIAO ESP32-S3.

Begründung: `uasyncio` für kooperatives Multitasking, `microdot` als async-Webserver, eingebautes `ujson`, stabile I²C-Bibliotheken.

Externe Bibliotheken (via `mip`, verwaltet durch `requirements.txt`):

- `ssd1306` – OLED-Treiber
- `microdot` (via `github:miguelgrinberg/microdot`) – Webserver

Zusätzlich manuell installierte Bibliotheken (nicht per `mip` verfügbar, siehe 6.2/6.2b):

- `uQR` – QR-Code-Erzeugung
- `writer.py` (Peter Hinch, MIT) + `font_hatch_status.py` – größere Displayschrift

Eigene Treiber im Projekt:

- `shared/pca9685.py` – PCA9685-Treiber (selbst geschrieben, deutlich kleiner als das komplette Paket von PyPI)
- `shared/grove_relay_4chn_spdt.py` – Grove Relay-Treiber (kein MicroPython-Paket verfügbar)
- `shared/servo_units.py` – Grad↔µs-Umrechnung (siehe 9.2b)

---

## 5. Projektstruktur

### 5.1 Verzeichnis auf dem Entwicklungsrechner

```
roof_hatch_controller/
├── designkonzept.md             # ursprüngliches Designkonzept (veraltet, historisch)
├── implementierungsdoku.md      # dieses Dokument
├── requirements.txt             # externe MicroPython-Pakete
├── upload.sh                    # Deploy-Script
├── pyrightconfig.json           # VSCode/Pyright: extraPaths: ["source"]
├── lib/                         # externe Bibliotheken (via mip oder manuell installiert)
│   ├── microdot.py
│   ├── ssd1306.py
│   ├── uQR.py                   # manuell installiert, siehe 6.2
│   ├── writer.py                # manuell installiert, siehe 6.2b (Peter Hinch, MIT)
│   └── font_hatch_status.py     # manuell erzeugt, siehe 6.2b (14px-Font für task_display.py)
└── source/                      # wird 1:1 auf das Gerät kopiert
    ├── main.py                  # Einstiegspunkt, enthält auch die frühe Button-Abfrage für AP_MODE
    ├── config.json              # Gerätekonfiguration (Startwerte anpassen!)
    ├── config.json.template     # konservative Vorlage für config_migration.py
    ├── test_qr.py                # historisches, eigenständiges QR-Testskript (siehe 15)
    ├── app/
    │   ├── __init__.py
    │   ├── hw_config.py         # Hardware-Konstanten
    │   ├── hw_init.py           # Hardware-Initialisierung
    │   ├── state.py             # config.json / state.json Verwaltung
    │   ├── config_migration.py  # config.json Erstellung/Migration beim Boot
    │   ├── failsafe.py          # Fail-Safe-Sequenz
    │   ├── fatal_error.py       # zentraler Handler für fatale Boot-Fehler
    │   ├── wifi.py              # WLAN-Verwaltung
    │   ├── session.py           # Config-Session-/QR-Zustand
    │   ├── task_watchdog.py     # Hardware-WDT-Feeder-Task
    │   ├── task_control.py      # Steuerloop-Task
    │   ├── task_button.py       # Button-Monitor-Task
    │   ├── task_display.py      # OLED-Update-Task
    │   └── task_webserver.py    # microdot-Webserver-Task
    ├── shared/
    │   ├── __init__.py
    │   ├── pca9685.py           # PCA9685-Treiber
    │   ├── grove_relay_4chn_spdt.py  # Grove Relay-Treiber
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

**Hinweis zu `boot.py`:** Ein früher geplantes separates `boot.py` für die frühe AP-Modus-Erkennung wurde nicht umgesetzt – die Logik (`_button_pressed()`, gesetzt in `AP_MODE`) sitzt direkt am Kopf von `main.py`, vor allen anderen Imports (siehe 8/9.1).

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

**Wichtig:** `lib/` liegt auf **Projekt-Ebene** (`roof_hatch_controller/lib/`), **nicht** unter `source/lib/` – `upload.sh` verwendet `LIB_DIR="./lib"` relativ zum Ausführungsverzeichnis des Scripts (Projektwurzel). Dateien, die versehentlich unter `source/lib/` abgelegt werden, werden von `upload.sh` nicht gefunden und folglich nicht hochgeladen.

### 6.2b `writer.py` + `font_hatch_status.py` manuell installieren

Für die größere Schriftart in `task_display.py` (siehe 9.13) werden zwei weitere, nicht per `mip` installierbare Dateien benötigt, ebenfalls nach `lib/` (Projektwurzel):

1. **`writer.py`**: unverändert aus `https://github.com/peterhinch/micropython-font-to-py` (Ordner `writer/writer.py`, MIT-Lizenz) übernehmen → `lib/writer.py`
2. **`font_hatch_status.py`**: mit dem PC-Tool `font_to_py.py` aus demselben Repository selbst erzeugt:
   ```bash
   python3 font_to_py.py DejaVuSansMono.ttf 14 font_hatch_status.py -f -s 32 -l 126
   ```
   Ergebnis: monospace, 14px Zeichenhöhe, 8px Zeichenbreite, Zeichensatz 32–126 (ASCII), ca. 8,3 KB. Datei nach `lib/font_hatch_status.py` legen.
3. `upload.sh` kopiert `lib/` danach automatisch aufs Gerät (wie bei `uQR.py`)

Bei Bedarf einer anderen Schriftart/-größe: `font_to_py.py` mit anderem Höhenwert bzw. anderer `.ttf`-Datei erneut ausführen. Zeilenraster in `task_display.py` (`_LINE_HEIGHT_PX = 16`) ggf. anpassen, falls die neue Zeichenhöhe nicht mehr zur bisherigen Rasterung passt.

### 6.3 `upload.sh`

Konfigurationsblock oben im Script:

- `UPLOAD_MODE`: `"usb"` oder `"wifi"`
- `USB_PORT`: z.B. `/dev/ttyACM0`
- `WIFI_IP`: IP des Geräts für WebREPL

Ablauf:

1. `requirements.txt` durchgehen → fehlende Pakete per `micropython -m mip install --target lib/` nachladen
2. Gerät zurücksetzen, um eine saubere REPL-Verbindung zu bekommen (`mpremote reset`, mit Retry bei Bedarf)
3. Alles in einer einzigen mpremote-Session: Zielverzeichnisse löschen, `config.json.template`, `config.json` (falls vorhanden), `app/`, `shared/`, `webpages/`, `lib/` hochladen, dann `main.py` als letztes
4. Finaler `mpremote reset` → echte `main.py` startet

**`mpremote reset` ist ein echter Hardware-Reset** (DTR/RTS-Leitung, wie der Reset-Taster) – lädt beim nächsten Boot alle Python-Module frisch von Flash. **Wichtige Einschränkung:** Der PCA9685 selbst hat eine eigene Spannungsversorgung und bleibt bei einem reinen ESP32-Reset durchgehend bestromt – sein `PRE_SCALE`-Register (PWM-Frequenz) und interne Zustände überleben also einen `mpremote reset` unverändert. Nach Änderungen an PWM-/Timing-nahem Code empfiehlt sich deshalb zusätzlich ein **kompletter Stromzyklus** (USB ab/an), der auch die PCA9685-Versorgung mit zurücksetzt.

**`config.json.template`** wird immer hochgeladen; `config.json` nur wenn im `source/`-Verzeichnis vorhanden.

**WLAN-Upload** (WebREPL): Einmalig per USB aktivieren:

```python
import webrepl_setup
```

Danach per `UPLOAD_MODE="wifi"` möglich.

### 6.4 `pyrightconfig.json` (Repo-Root)

```json
{
    "extraPaths": ["source"]
}
```

Damit löst VSCode/Pyright Imports korrekt auf (ohne `source.`-Präfix).

### 6.5 Statische Bibliotheken einmalig herunterladen

```bash
cd source/webpages/static
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css -o bootstrap.min.css
curl -L https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js -o bootstrap.bundle.min.js
curl -L https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js -o alpine.min.js
```

---

## 7. Konfigurationsdateien

### 7.1 `/config.json` – Gerätekonfiguration

Muss vor dem ersten Start auf das Gerät kopiert werden (per `upload.sh` automatisch aus `source/config.json`). Alle Positionen, Hard-Stops, Geschwindigkeiten und Montagewinkel sind in **Grad** bzw. **Grad/s** angegeben, nicht in µs. Die Umrechnung nach µs für die PCA9685-Ansteuerung übernimmt `shared/servo_units.py` (`deg_to_us()`), ausschließlich innerhalb von `task_control.py`, unmittelbar vor der Hardware-Ansteuerung.

**Zentrale Architekturentscheidung:** Jeder Servo-Parameter (Stellbereich, Richtung, Geschwindigkeit, Impulszeiten, Hard-Stops) ist **pro Kanal** konfigurierbar – es gibt (anders als in einer früheren Zwischenversion) keine geteilten/globalen Servo-Parameter für alle 4 Luken mehr. Einzige Ausnahme: der Servotester (`global.servo_tester`) hat einen eigenen, in sich global gültigen Parametersatz, da er nur ein einziges zusätzliches Kanalpaar ist.

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
            "name": "Luke 1",
            "short_name": "Luke 1",
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

**Feldreferenz je Servo-Block** (`hatch_servo`/`brake_servo`, sowohl pro Luke als auch in `global.servo_tester`):

| Feld | Bedeutung | Zulässiger Bereich |
|---|---|---|
| `range_deg` | Gesamtstellbereich des Servos | {180, 270, 360} |
| `direction` | Drehrichtung: `"neg"` (0° → `pulse_max_us`) oder `"pos"` (0° → `pulse_min_us`) | {"pos", "neg"}, Default `"neg"` |
| `speed_deg_per_s` | Spitzengeschwindigkeit (siehe 3.5) | 30–180 |
| `pulse_min_us`/`pulse_max_us` | Impulszeit-Grenzen in µs | 500–4000, `min < max` |
| `hard_stop_min_deg`/`hard_stop_max_deg` | Software-Endanschläge in Grad – nie überschritten | 0…`range_deg`, `min < max` |

**Zusätzliche Felder nur bei `hatch_servo` (pro Luke):**

- `mount_angle_deg`: Montagewinkel (siehe 3.7), muss innerhalb `[hard_stop_min_deg, hard_stop_max_deg]` liegen

**Zusätzliche Felder nur bei `brake_servo` (pro Luke):**

- `mount_angle_deg`: Montagewinkel Halteservo
- `open_deg`/`close_deg`: reguläre Betriebspositionen der Bremse
- alle drei müssen innerhalb `[hard_stop_min_deg, hard_stop_max_deg]` dieses Blocks liegen

**Zusätzliche Felder nur in `global.servo_tester.hatch_servo`/`brake_servo`:**

- `test_angle_a_deg`/`test_angle_b_deg`: die beiden frei wählbaren Testwinkel, zwischen denen der jeweilige Testerkanal hin- und hergefahren werden kann; müssen innerhalb der eigenen Hard-Stops liegen

**`pwm_freq_hz`** (500Hz…400Hz-Bereich, siehe `PWM_FREQ_MIN_HZ`/`PWM_FREQ_MAX_HZ`) liegt **pro Luke** (nicht pro Servo!) sowie einmal für `global.servo_tester` – Begründung siehe 9.11 (PCA9685-Frequenz-Architektur).

**`positions`** (nur Hebelservo, pro Luke): Liste `[{"hatch_deg": N}, ...]`, min. 2 Einträge. `positions[0]` ist immer der Zu-Endanschlag ("ZU"), `positions[-1]` immer der Auf-Endanschlag ("AUF") – diese beiden bleiben bei Einfügen/Entfernen von Zwischenpositionen stets an erster/letzter Stelle der Liste (siehe 10.2).

**`config.json.template`** im `source/`-Verzeichnis dient als konservative Vorlage. `config_migration.py` nutzt sie beim Boot um fehlende oder umbenannte Felder automatisch zu ergänzen (siehe 9.16).

**Entfallen** (aus früheren Config-Iterationen, nicht mehr Teil des Schemas): globale `hatch_servo`/`brake_servo`-Blöcke (jetzt pro Luke), `SERVO_DIRECTION_REVERSED` als feste Code-Konstante (jetzt `direction`-Feld pro Servo), `HATCH_STEP_US`/feste Schrittgröße (jetzt zeitbasierte S-Kurven-Rampe, siehe 9.11), `delay_after_brake_change_cmd_ms`/`delay_after_move_ms` (durch echtes Warten auf Rampenende ersetzt).

### 7.2 `/wifi.json` – WLAN-Zugangsdaten

```json
{"ssid": "MeinNetzwerk", "password": "MeinPasswort"}
```

Wird über den AP-Modus geschrieben. Liegt im internen Flash, nicht auf SD. Nicht im Git-Repo.

### 7.3 `/state.json` – Letzter bekannter Servo-Zustand

```json
{
    "hatches": [
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0}
    ],
    "servo_tester": {"hatch_deg": 0, "brake_deg": 0}
}
```

Wie `config.json` durchgehend in Grad. Umrechnung nach µs passiert nur lokal in `task_control.py` unmittelbar vor der PCA9685-Ansteuerung.

- `hatches[i].hatch_deg`: Letzter bekannter Ist-Zustand des Hebelservos in Grad, oder `FAILSAFE_DEG` (-1) wenn die Position unbekannt ist (Fail-Safe-Zustand oder Stromausfall mitten in einer Fahrt).
- `hatches[i].brake_deg`: Letzter bekannter Ist-Zustand des Halteservos in Grad. Immer ein echter Winkel, nie `FAILSAFE_DEG`.
- `hatches[i].target_deg`: Zuletzt befohlener Soll-Zustand in Grad, oder `FAILSAFE_DEG` (-1) im passiven Fail-Safe-Ruhezustand. Persistiert damit ein Soll/Ist-Mismatch nach Stromausfall erkennbar bleibt und beim nächsten Boot automatisch abgearbeitet wird.
- `servo_tester.hatch_deg`/`brake_deg`: letzte bekannte Ist-Winkel der beiden Testerkanäle, immer echte Winkel, kein Soll-Wert (der Tester bewegt sich nie automatisch) – dient nur dazu, dass eine Testfahrt nach einem Reboot vom richtigen Startpunkt aus rampt statt von einem veralteten Wert.

Beim allerersten Start nicht vorhanden → `state.py` erzeugt Standardzustand aus `config.json`: alle Luken auf `hatch_servo.hard_stop_min_deg` (Hebel), `brake_servo.close_deg` (Bremse, jeweils der eigenen Luke), Soll = Ist. Servotester startet an `hard_stop_min_deg` beider eigener Servo-Blöcke. Ein `state.json` ohne `servo_tester`-Schlüssel oder fehlenden Pflichtfeldern (auch alte, inzwischen überholte Schemata) wird als ungültig erkannt und ebenfalls neu erzeugt.

**Wichtig:** Vor jedem Relais-Ein werden die Ist-Positionen an PCA9685 ausgegeben. Bei `FAILSAFE_DEG` wird `positions[0]` als Fallback für den Hebelservo verwendet, damit kein Ruck entsteht.

---

## 8. Startsequenz (`main.py`)

```
Stage 0:  WDT-Reset-Erkennung
          → machine.reset_cause() == WDT_RESET?
          → ja: init_hw_failsafe() + fail_safe_close_all() vor allem anderen

Stage 1:  Display früh initialisieren (init_display_early)
          → ab jetzt Statusmeldungen auf OLED

Stage 1b: AP-Modus – falls der Button beim Einschalten gehalten wurde
          (Prüfung ganz am Kopf von main.py, vor allen weiteren Imports,
          siehe 9.1) → start_ap_mode() + minimaler task_webserver-Aufruf
          mit leeren, aber korrekt geformten Platzhaltern für config/
          state/targets, dann return (Gerät startet nach WLAN-Speichern
          neu, dieser Codepfad wird nie regulär verlassen)

Stage 2:  Restliche Hardware initialisieren (init_hardware)
          → PCA9685, Relay, Button
          → Fehler → fatal_error()

Stage 2b: config_migration.migrate_config() → stellt sicher, dass
          config.json existiert und vollständig ist
          config.json laden (load_config) → Fehler → fatal_error()
          state.json laden (load_state)
          → Fehler → create_default_state() + save_state()
          Bei WDT-Reset: alle Luken in state auf FAILSAFE_DEG (-1) setzen
          + save_state() (physische Fail-Safe-Fahrt war bereits Stage 0;
          der Servotester hat kein Fail-Safe-Konzept und bleibt unberührt)

Stage 3:  WLAN
          → start_client_mode()

Stage 4:  targets-Dict aus persistiertem Soll aufbauen (siehe 9.11 für
          die vollständige Struktur mit hatches/brake_only/tester)
          uasyncio.gather() – alle Tasks starten:
          task_watchdog, task_display, task_control,
          task_button, task_webserver
```

---

## 9. Modulbeschreibungen

### 9.1 Kopf von `main.py` (früher geplant als `boot.py`)

Läuft ganz am Anfang von `main.py`, **vor** allen anderen Imports (nur `gc`, `network`, `uasyncio`, `machine`, `Pin`, `time` und `app.hw_init` sind zu diesem Zeitpunkt schon importiert):

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

Liest den User-Button einmalig mit einfacher Entprellung (50ms). `AP_MODE` steuert danach in `main()` Stage 1b, ob der WLAN-Setup-Modus statt des Normalbetriebs gestartet wird.

**AP-Modus:** Button beim **Einschalten gedrückt halten**.

### 9.2 `app/hw_config.py`

Alle Hardware-Konstanten. Keine Imports von Hardware-Bibliotheken – überall importierbar.

Wichtige Konstanten:

```python
I2C_SDA_PIN = 5
I2C_SCL_PIN = 6
I2C_FREQ    = 1_000_000  # siehe 2.2b für die Herleitung dieses Werts

I2C_ADDR_OLED    = 0x3C
I2C_ADDR_PCA9685 = 0x7F
I2C_ADDR_RELAY   = 0x11
I2C_ADDR_RTC     = 0x51

BTN_USER_PIN = 2
BTN_DEBOUNCE_MS       = 50
BTN_CONFIG_UNLOCK_MS  = 60_000     # 60s Freigabefenster nach Button
BTN_CONFIG_SESSION_MS = 3_600_000  # 1h Session
BTN_LONG_PRESS_MS     = 3_000      # Schwelle kurzer/langer Druck

PCA9685_FREQ_HZ = 50   # Boot-Default, siehe 9.11 für die Laufzeit-Umschaltung

SERVO_MIN_US = 500     # absolute Hardwaregrenze für pulse_min_us/pulse_max_us
SERVO_MAX_US = 4000    # jedes Servos, unabhängig vom konfigurierten range_deg

RANGE_DEG_OPTIONS   = (180, 270, 360)
SERVO_RANGE_DEG_MAX = 360   # obere Sanity-Grenze für Grad-Werte in state.json,
                             # unabhängig vom jeweils konfigurierten range_deg

SPEED_MIN_DEG_PER_S = 30
SPEED_MAX_DEG_PER_S = 180

PWM_FREQ_MIN_HZ = 50
PWM_FREQ_MAX_HZ = 400

FAILSAFE_DEG = -1      # Sentinel: Position unbekannt (Fail-Safe-Zustand)

MIN_STEP_MS = 8         # praktische Mindest-Update-Rate der Bewegungsrampe
                         # (siehe 9.11); tatsächlich verwendetes Intervall:
                         # max(MIN_STEP_MS, PWM-Periode der konfigurierten
                         # pwm_freq_hz) – die 8ms-Grenze greift erst ab
                         # pwm_freq_hz >= 125Hz

NUM_HATCHES  = 4

PCA9685_CH = {0:(0,1), 1:(2,3), 2:(4,5), 3:(6,7)}   # pro Luke: (Hebel, Halte)
TESTER_CH  = (8, 9)                                  # Servotester: (Hebel, Halte)
RELAY_CH   = {0:0, 1:1, 2:2, 3:3}

BRAKE_TRAVEL_MS        = 500   # Wartezeit für Bremsen-Servo im Fail-Safe
BRAKE_OPEN_US_FAILSAFE = 1200  # Fallback-Wert ohne config.json (roher µs-Wert,
                                # unabhängig von der Grad-Konfiguration –
                                # failsafe.py braucht kein lesbares config.json)
```

### 9.2b `shared/servo_units.py`

Reine Umrechnungsmathematik, kein Hardware-Import, daher überall importierbar:

```python
deg_to_us(deg, pulse_min_us, pulse_max_us, range_deg, direction)  # -> int µs
us_to_deg(us, pulse_min_us, pulse_max_us, range_deg, direction)   # -> float Grad
```

`direction` ist seit der Pro-Kanal-Umstellung ein **Parameter**, keine feste Code-Konstante mehr (früher `SERVO_DIRECTION_REVERSED` in `hw_config.py`, einheitlich für alle Servos): `"neg"` bedeutet, steigende Impulszeit entspricht sinkendem Winkel (0° → `pulse_max_us`); `"pos"` das Gegenteil (0° → `pulse_min_us`).

```python
us_per_deg = (pulse_max_us - pulse_min_us) / range_deg
# direction == "neg":
us = pulse_max_us - deg * us_per_deg
# direction == "pos":
us = pulse_min_us + deg * us_per_deg
```

Wird ausschließlich in `task_control.py` verwendet, um die in Grad gehaltene Konfiguration/den Zustand unmittelbar vor der PCA9685-Ansteuerung nach µs umzurechnen. `us_to_deg()` ist als symmetrisches Gegenstück vorhanden, wird aktuell aber nirgends aufgerufen.

### 9.3 `shared/pca9685.py`

Selbst geschriebener Treiber direkt nach Datenblatt. Kein externer Import nötig.

**API:**

```python
pwm = PCA9685(i2c, address=0x40)
pwm.freq(50)                      # PWM-Frequenz setzen (chip-weit, alle 16 Kanäle)
pwm.set_pwm(ch, on, off)          # rohe 12-Bit-Werte
pwm.set_us(ch, us, freq_hz=50)    # Pulsbreite in µs
pwm.set_all_off()                 # alle Kanäle aus (sicherer Zustand)
```

**Kritischer Implementierungspunkt – `freq_hz`-Parameter von `set_us()`:** Der Treiber kann die tatsächlich im Chip programmierte Frequenz **nicht auslesen** – `set_us()` rechnet Mikrosekunden anhand des übergebenen `freq_hz`-Arguments (Default 50) in Tick-Werte um. Wird `freq_hz` nicht explizit übergeben oder passt nicht zum zuletzt per `freq()` gesetzten Wert, berechnet `set_us()` stillschweigend eine falsche Pulsbreite – ohne jede Fehlermeldung. **Jeder Aufrufer, der mit einer von 50Hz abweichenden Frequenz arbeitet, muss bei jedem einzelnen `set_us()`-Aufruf dasselbe `freq_hz` übergeben, das zuvor per `freq()` gesetzt wurde.** `task_control.py` hält sich strikt daran (siehe 9.11); `failsafe.py` erzwingt vor jeder Ansteuerung explizit 50Hz, weil zum Zeitpunkt des Fail-Safe-Laufs unbekannt ist, mit welcher Frequenz der Chip zuletzt lief (siehe 9.7).

**Weitere Implementierungsdetails:**

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
relay.all_on()       # Vorsicht: überschreitet PSU-Auslegung
relay.is_on(channel) # Software-State abfragen
relay.get_firmware_version()  # liest FW-Byte vom Board
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

Alle drei werfen `HardwareInitError` wenn ein I²C-Gerät nicht antwortet (`_check_i2c_device`, das Adressen über 0x77 direkt anschreibt statt `i2c.scan()` zu nutzen, siehe 15).

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

**Validierung config.json** (siehe auch 7.1 für die Feldreferenz):

- Struktur: `global.servo_tester` (mit `pwm_freq_hz`, `hatch_servo`, `brake_servo`) sowie exakt `NUM_HATCHES` Einträge in `hatches`, jeder mit `name`, `short_name`, `pwm_freq_hz`, `hatch_servo`, `brake_servo`, `positions`
- Jeder Servo-Block (`_validate_servo_block`): `range_deg` ∈ {180,270,360}, `direction` ∈ {"pos","neg"}, `speed_deg_per_s` ∈ [30,180], `pulse_min_us < pulse_max_us` beide innerhalb [`SERVO_MIN_US`, `SERVO_MAX_US`], `hard_stop_min_deg < hard_stop_max_deg` beide innerhalb [0, `range_deg`]
- Zusätzliche Winkel (`mount_angle_deg`, `open_deg`/`close_deg`, `test_angle_a/b_deg`) je nach Blocktyp, immer geprüft gegen die **eigenen** Hard-Stops desselben Blocks (`_check_within_hard_stops`)
- `positions`: mindestens 2 Einträge, jeder `hatch_deg` innerhalb der Hard-Stops des zugehörigen `hatch_servo`
- `pwm_freq_hz` (pro Luke und für den Tester) ∈ [`PWM_FREQ_MIN_HZ`, `PWM_FREQ_MAX_HZ`]

**Wichtiger Nebeneffekt des Sofort-Persistenz-Modells (siehe 10.2):** Da jedes einzelne Feld für sich sofort das **komplette** Dokument validiert (kein Sammel-Speichern mehr, das mehrere Änderungen gemeinsam prüfen könnte), entsteht beim **Verkleinern** eines `range_deg`/Hard-Stops eine Reihenfolge-Abhängigkeit: Ein abhängiger Wert (Position, Montagewinkel, Testwinkel, `open_deg`/`close_deg`), der noch außerhalb des neuen, kleineren Bereichs liegt, muss zuerst angepasst werden, bevor der Bereich selbst verkleinert werden kann – sonst schlägt die Validierung mit einer Meldung zum abhängigen Feld fehl, nicht zwingend zum gerade bearbeiteten Feld. Das ist kein Bug, sondern eine bewusst in Kauf genommene Konsequenz des Sofort-Speichern-Designs.

`hatch_deg` und `target_deg` in `state.json` dürfen `FAILSAFE_DEG` (-1) enthalten; in diesem Fall wird der Range-Check für das jeweilige Feld übersprungen. `brake_deg` sowie die beiden `servo_tester`-Winkel sind immer echte Winkel und werden immer geprüft (Sanity-Grenze [0, `SERVO_RANGE_DEG_MAX`], unabhängig von `config.json`).

**Erstes Startup / veraltetes Format:** `load_state()` wirft `StateError` → `create_default_state(config)` + `save_state()`.

### 9.7 `app/failsafe.py`

Synchrone Funktion (kein asyncio) – sicher vor dem Event-Loop aufrufbar.

```python
fail_safe_close_all(hw, brake_open_us=None)
```

Ablauf:

1. **`hw.pwm.freq(50)`** – erzwingt eine bekannte Frequenz. Läuft in Stage 0, bevor `config.json` lesbar ist; der PCA9685 könnte zu diesem Zeitpunkt noch auf einer beliebigen zuvor konfigurierten `pwm_freq_hz` stehen (der Chip bleibt über einen ESP32-Reset hinweg bestromt und behält sein `PRE_SCALE`-Register). Ohne diesen erzwungenen Reset würde `set_us()` (siehe 9.3) mit falscher Tick-Umrechnung eine falsche Bremsposition anfahren – ausgerechnet im sicherheitskritischsten Codepfad.
2. Für jede Luke sequenziell: Bremsen-Servo auf `brake_open_us` setzen (mit explizit `freq_hz=50`)
3. Relais ein
4. `time.sleep_ms(BRAKE_TRAVEL_MS)` warten (Bremsen-Servo fährt)
5. Relais aus → Dachluke schließt durch Schwerkraft, Bremse schnappt dabei nicht ein, da Servo im "offen" Zustand zurückgelassen wird (Federkraft reicht nicht aus, um den Servo über die Nockenscheibe zu verstellen → Luke bleibt unverriegelt)
6. 200ms Pause vor nächster Luke (PSU-Schonung)

Wenn `brake_open_us=None` → Fallback auf `BRAKE_OPEN_US_FAILSAFE` aus `hw_config.py` (funktioniert ohne `config.json`, kein Anwendungsfall übergibt aktuell einen anderen Wert).

### 9.8 `app/wifi.py`

```python
load_wifi_config()               # liest /wifi.json → WifiConfigError
save_wifi_config(ssid, password) # schreibt /wifi.json + machine.reset()
start_client_mode(hw)            # async, verbindet WLAN, 15s Timeout
start_ap_mode(hw)                # async, startet AP "Dachluke-Setup" (offen)
```

Bei fehlendem `wifi.json` in `start_client_mode`: Fehlermeldung auf Display, weiter ohne WLAN. Neustart mit gehaltenem Button für AP-Modus nötig.

AP-SSID: `"Dachluke-Setup"`, kein Passwort, IP: 192.168.4.1 (ESP32-Standard).

Status-Zeilen während des Verbindungsaufbaus werden über `_display_line(hw, line, text)` (`line * 8` px) direkt geschrieben – unabhängig vom 16px-Zeilenraster, das `task_display.py` im Normalbetrieb verwendet (siehe 9.13). Da `task_display` erst nach abgeschlossenem Boot startet und beim ersten regulären Refresh das komplette Display neu zeichnet, entsteht daraus kein Konflikt; die Boot-Meldungen werden einfach überschrieben.

### 9.9 `app/session.py`

Geteilter Zustand zwischen `task_button` und `task_webserver`/`task_display`. Nur RAM, keine Persistenz.

```python
unlock_config()       # startet 60s-Freigabefenster (langer Tastendruck)
activate_session()    # startet 1h-Session (Config-Seite aufgerufen)
is_unlock_active()    # True während 60s-Fenster
is_session_active()   # True während 1h-Session
show_qr()             # startet 15s-QR-Anzeige (kurzer Tastendruck)
is_qr_active()        # True während der 15s
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

**Historische Anmerkung – Herzschlag-Diagnose entfernt:** Während der Fehlersuche zu einer Servo-Geschwindigkeitsschwankung wurde `task_watchdog` vorübergehend um einen alle ~5s geloggten Herzschlag (`gc.mem_free()`) erweitert, um zu unterscheiden, ob der Event-Loop wirklich hängt oder nur die Netzwerk-/Socket-Ebene betroffen ist. Nach erfolgreicher Fehlersuche wieder auf die einfache Fassung zurückgebaut.

---

### 9.11 `app/task_control.py`

Herzstück der Anwendung. Läuft als Endlosschleife, vergleicht bei jedem Durchlauf sequenziell: alle 4 Luken (`targets["hatches"][i]["hatch_deg"]` vs. `state["hatches"][i]["hatch_deg"]`), dann alle 4 ausstehenden Halteservo-Direktfahrten (Montagewinkel), dann die 2 ausstehenden Servotester-Fahrten. `config`, `state` und `targets` sind durchgehend in Grad; die Umrechnung nach µs (`shared/servo_units.py`, `deg_to_us()`) passiert ausschließlich lokal am Anfang der jeweiligen Bewegungsfunktion. **Alle I²C-Schreibzugriffe auf den PCA9685 laufen ausschließlich in diesem einen Task** – auch für Montagewinkel-Halteservo und Servotester (keine direkte Hardware-Ansteuerung aus `task_webserver.py`).

#### PWM-Frequenz-Architektur

Der PCA9685 hat **ein einziges, chip-weites `PRE_SCALE`-Register** – keine Kanal-individuelle Frequenz ist in Hardware möglich. Deshalb:

- **Eine Frequenz pro Luke** (`hatches[i].pwm_freq_hz`), gemeinsam für Hebel- und Halteservo dieser Luke, da beide während der gesamten Bewegungssequenz gleichzeitig bestromt sind und ohnehin nie unterschiedliche Frequenzen gleichzeitig haben könnten.
- **Eine weitere, gemeinsame Frequenz für beide Servotester-Ausgänge** (`global.servo_tester.pwm_freq_hz`), unabhängig von den 4 Luken.
- `_ensure_pwm_freq(hw, freq_hz)` reprogrammiert den Chip unmittelbar vor jeder Bewegung auf die passende Frequenz – aber nur, wenn sie vom zuletzt gesetzten Wert abweicht (`_current_pwm_freq`-Modulcache), um unnötige Neuprogrammierungen zu vermeiden.
- **Bewusst keine Absicherung** dagegen, dass eine laufende Lukenbewegung die Servotester-Frequenz durcheinanderbringt (oder umgekehrt): Der Servotester ist nur für ausgebaute, unbelastete Servos gedacht – falsches Verhalten während einer zufällig gleichzeitigen Lukenbewegung ist unkritisch.

**Kritische Reihenfolge:** `_ensure_pwm_freq()` muss vor **jedem** `set_us()`-Aufruf der jeweiligen Bewegung ausgeführt werden – sowohl weil `set_us()`s Tick-Berechnung von der aktuell gültigen Frequenz abhängt (siehe 9.3), als auch weil `pwm.freq()` selbst den Chip kurz zurücksetzt, was zuvor geschriebene Ausgänge stören würde, wenn es *nach* ihnen aufgerufen würde.

#### Zeitbasierte S-Kurven-Rampe (`_ramp`)

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
        eased = frac * frac * (3 - 2 * frac)   # Smoothstep: 3f² - 2f³
        current_us = round(start_us + distance * eased)
        hw.pwm.set_us(channel, current_us, freq_hz)
        await uasyncio.sleep_ms(tick_ms)
```

Zwei wichtige Entwurfsentscheidungen stecken hier, beide aus konkreten Debugging-Sitzungen mit dem echten Servo hervorgegangen:

**1. Zeitbasiert statt schrittzahlbasiert.** Die kommandierte Position bei jedem Tick wird aus der **tatsächlich vergangenen Zeit** berechnet, nicht aus einem mitlaufenden Schrittzähler. `task_display` (OLED-Refresh, siehe 9.13) und `task_webserver` teilen sich denselben I²C-Bus und Event-Loop mit diesem Task; ein Tick, der durch einen anderen Task verzögert wird, holt beim nächsten Tick automatisch die verpasste Distanz auf, statt dass die Verzögerung zu einer bleibenden Lücke in der Bewegung führt. Ursache und Symptom waren real messbar: Ein per Debug-Log aufgezeichnetes, schrittzahlbasiertes Rampenprofil zeigte eine Zeitstempel-Lücke von 80–100ms etwa alle 330–500ms, exakt im Rhythmus des (damals bei jedem Zyklus unbedingten) OLED-Redraws – siehe 9.13 für die zugehörige Behebung auf der Display-Seite. Nach beiden Fixes (zeitbasierte Rampe **und** bedingter Redraw) war das Zeitraster durchgehend gleichmäßig (19–31ms zwischen den Ticks bei `pwm_freq_hz=50`).

**2. Smoothstep-Profil statt linearer Interpolation.** `eased = 3f² − 2f³` sorgt für stetige, bei `f=0` und `f=1` exakt null Geschwindigkeit – kein Ruck an Bewegungsanfang oder -ende. Da die Spitzengeschwindigkeit eines Smoothstep-Profils (in der Bewegungsmitte) das 1,5-fache der Durchschnittsgeschwindigkeit beträgt, verlängert `_ramp_duration_ms()` die naive konstante-Geschwindigkeit-Dauer um denselben Faktor 1,5 – dadurch bleibt die konfigurierte `speed_deg_per_s` exakt die tatsächliche Spitzengeschwindigkeit, nicht eine (höhere) Durchschnittsgeschwindigkeit, und das konfigurierte Geschwindigkeitslimit (30–180°/s) wird an keiner Stelle der Bewegung überschritten.

`_tick_interval_ms()` begrenzt die Update-Rate zusätzlich auf mindestens `MIN_STEP_MS` (8ms) **und** mindestens eine volle PWM-Periode der aktuell konfigurierten `pwm_freq_hz` – schnelleres Schreiben als der Chip überhaupt einen vollen PWM-Zyklus ausgibt, würde manche Zwischenschritte nie als vollständigen Puls ausgeben, andere doppelt so lange stehen lassen: ein unregelmäßiges Muster, das sich wie wiederholtes Beschleunigen/Abbremsen anfühlt, obwohl die Software eine gleichmäßige Rampe beabsichtigt. Bei 50Hz (Standard) ergibt das 20ms Update-Intervall; die 8ms-Untergrenze greift erst ab `pwm_freq_hz` ≥ 125Hz.

#### Bewegungssequenz `_move_hatch` (volle Lukenfahrt)

| Schritt | Was passiert |
|---|---|
| `step_mark_failsafe` | `hatch_deg` auf `FAILSAFE_DEG` setzen und persistieren → bei Stromausfall bleibt der Sentinel in `state.json`; nächster Boot erkennt die unterbrochene Fahrt |
| `step_preset_servos` | Halteservo auf seine **eigene aktuelle Ist-Position** (nicht `open_deg` – vermeidet einen Ruck beim Relais-Ein, da er erst rampt, sobald der Hebelservo die Haltekraft übernommen hat); Hebelservo auf **0µs** (kein gültiges Signal), damit der Servo-Controller während seines Bootvorgangs kein Signal sieht |
| `step_set_pwm_freq` | `_ensure_pwm_freq()` auf `pwm_freq_hz` dieser Luke – **vor** den obigen Preset-Schreibzugriffen (siehe oben) |
| `step_relay_on` | Relais ein → beide Servos dieser Luke bestromt |
| (Wartezeit `_RELAY_ON_TO_TORQUE_MS` = 200ms) | Zeit bis der Hebelservo die Haltekraft übernommen hat, bevor die Bremse gelöst werden darf. Kein Wiggle-Schritt mehr nötig (siehe 3.4) – die S-Kurven-Rampe beginnt ohnehin bei Geschwindigkeit 0 |
| `step_ramp_brake_open` | Halteservo rampt von seiner Ist-Position zu `open_deg` (eigene `speed_deg_per_s`) – keine Pause danach |
| `step_ramp_hatch` | Hebelservo rampt zur Zielposition (eigene `speed_deg_per_s`), geclampt auf `[hard_stop_min_deg, hard_stop_max_deg]` |
| (Wartezeit `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS` = 200ms) | Zeit zum Ausregeln am Ziel, bevor die Bremse den Schließbefehl bekommt |
| `step_ramp_brake_close` | Halteservo rampt von `open_deg` zu `close_deg` |
| `step_relay_off` | Relais aus, unmittelbar nach Ende der Bremse-zu-Rampe – immer im `finally`-Block, unabhängig vom Ausgang |
| `step_update_state` | `state` aktualisieren (`hatch_deg`, `brake_deg`, `target_deg`) + `save_state()` |

Da der Halteservo jetzt ebenfalls rampt (statt in einem Schritt zu springen), ist genau bekannt, wann er physisch fertig ist – dadurch entfallen zwei frühere, rein geschätzte feste Wartezeiten (`_BRAKE_OPEN_TO_HATCH_MOVE_MS`, `_BRAKE_CLOSE_TO_RELAY_OFF_MS`) ersatzlos.

**Modul-Konstanten** (direkt oben in `task_control.py`, nicht in `config.json`, nicht web-konfigurierbar):

| Konstante | Wert | Bedeutung |
|---|---|---|
| `_PRESET_DELAY_MS` | 250 | Wartezeit nach Relay-Ein vor erstem gültigem PWM-Signal; 0 = deaktiviert |
| `_RELAY_ON_TO_TORQUE_MS` | 200 | Relais-Ein bis Hebelservo Haltekraft übernommen hat |
| `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS` | 200 | Zielposition erreicht bis Bremse schließt |
| `_MAX_MOVE_RETRIES` | 3 | siehe Fehlerbehandlung unten |

**Hardstop-Enforcement:** Zielwert wird vor der Sequenz auf `[hard_stop_min_deg, hard_stop_max_deg]` **dieser Luke** geclampt und zurück in `targets` geschrieben.

#### `_move_brake_only` (Montagewinkel Halteservo)

Vereinfachte Variante ohne die volle Choreografie – bewegt **nur** den Halteservo direkt:

```
step_preset_servos:  Hebelservo auf seine aktuelle Ist-Position (halten, nicht bewegen),
                     Halteservo auf seine aktuelle Ist-Position
step_set_pwm_freq
step_relay_on
step_preset_delay    (gemeinsam für beide Kanäle, da am selben Relais)
step_ramp_brake:     Halteservo rampt zur Zielposition (eigene Geschwindigkeit)
step_relay_off:      sofort nach Rampenende
step_update_state:   nur brake_deg aktualisieren, hatch_deg/target_deg unverändert
```

Der Hebelservo wird also weiterhin bestromt (gemeinsames Relais), aber nur zum Halten der aktuellen Position genutzt, nicht bewegt – **kein** eigener Wiggle-artiger Mechanismus dafür, da dort ohnehin nichts fahren soll.

#### `_move_tester_servo` (Servotester)

Noch einfacher – **kein Relais** (Testerkanäle sind dauerbestromt, siehe 2.3):

```python
async def _move_tester_servo(hw, config, state, which, target_deg):
    # which: "hatch" oder "brake"
    st = config["global"]["servo_tester"]
    servo_cfg = st[f"{which}_servo"]
    channel   = TESTER_CH[0 if which == "hatch" else 1]
    ...
    _ensure_pwm_freq(hw, st["pwm_freq_hz"])
    await _ramp(hw, channel, current_us, target_us, duration_ms, st["pwm_freq_hz"])
    state["servo_tester"][f"{which}_deg"] = target_deg
    save_state(state)
```

#### `targets`-Struktur

```python
targets = {
    "hatches":    [{"hatch_deg": N}, ...],   # 4 Einträge, löst _move_hatch aus
    "brake_only": [None, None, None, None],  # pro Luke; ein gesetzter Wert löst
                                              # _move_brake_only aus und wird danach
                                              # (Erfolg oder Fehler) wieder auf None gesetzt
    "tester": {"hatch_deg": None, "brake_deg": None},  # analog für den Servotester
}
```

Beim Start aus dem persistierten Soll aufgebaut (`main.py` Stage 4):

```python
targets = {
    "hatches":    [{"hatch_deg": h["target_deg"]} for h in state["hatches"]],
    "brake_only": [None for _ in state["hatches"]],
    "tester":     {"hatch_deg": None, "brake_deg": None},
}
```

`task_webserver.py` setzt Werte in `targets`, `task_control` liest sie und führt die passende Bewegungsfunktion aus – nie umgekehrt, kein direkter Hardwarezugriff aus dem Webserver-Task heraus.

#### Fehlerbehandlung und Retry-Begrenzung

Exceptions aus den drei Bewegungsfunktionen werden im Hauptloop gefangen und auf der seriellen Konsole geloggt; der Loop fährt mit der nächsten Luke/dem nächsten Kanal fort. Das Relais ist durch die jeweiligen `finally`-Blöcke immer sicher aus.

**Wichtig:** Ein fehlgeschlagener `_move_hatch`-Aufruf hinterlässt `state["hatch_deg"] == FAILSAFE_DEG`, während `targets` den ursprünglichen Zielwert behält – die Mismatch-Bedingung bleibt damit dauerhaft erfüllt, und ohne Gegenmaßnahme würde `_move_hatch` bei jedem Schleifendurchlauf erneut versucht (inklusive erneutem `save_state()`-Flash-Schreibzugriff bei jedem Versuch – das kann bei sehr häufigen Fehlversuchen die Netzwerk-Latenz beeinträchtigen und den Flash unnötig abnutzen). `_fail_count` (pro Luke, Modul-Liste `[0,0,0,0]`) zählt aufeinanderfolgende Fehlschläge; nach `_MAX_MOVE_RETRIES` (3) wird `targets["hatches"][i]["hatch_deg"]` auf den aktuellen (unbekannten) Ist-Wert zurückgesetzt – die Mismatch-Bedingung verschwindet, der Loop gibt auf. Ein neuer Nutzerbefehl kann die Fahrt jederzeit erneut anstoßen.

Unerwartete, nicht gefangene Exceptions (außerhalb der drei Bewegungsfunktionen) reißen den Event-Loop mit → WDT-Reset → Fail-Safe.

---

### 9.12 `app/task_button.py`

Pollt den User-Button alle 50ms. Erkennt fallende Flanke (1→0) mit `BTN_DEBOUNCE_MS` Entprellung. Unterscheidet kurzen und langen Tastendruck anhand der Haltezeit:

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

Prüft alle `_REFRESH_MS` (500ms), ob sich der Displayinhalt geändert hat, und fasst den I²C-Bus **nur an, wenn tatsächlich etwas neu zu zeichnen ist**.

**Warum das mehr als nur Effizienz ist:** `task_display` teilt sich den I²C-Bus und den Event-Loop mit `task_control` (Servo-Ansteuerung). Ein voller Redraw (Framebuffer füllen + `Writer`-Glyphen rendern + der abschließende I²C-Transfer in `show()`) läuft ohne `await` dazwischen und blockiert den Event-Loop für seine gesamte Dauer. Gemessene Auswirkung, bevor dieser Mechanismus eingeführt wurde: Ein unbedingter Redraw bei **jedem** Zyklus – auch wenn sich am Bildschirm inhaltlich gar nichts änderte – hat periodisch die geplante Aufwach-Zeit der Servo-Rampe in `task_control` verzögert, was sich als reale, physische Geschwindigkeitsschwankung am Servo bemerkbar machte (am deutlichsten beim Servotester, da sich die 4 Lukenstatus-Zeilen während dessen Nutzung gar nicht ändern). Derselbe unbedingte Redraw verursachte außerdem ein sichtbares Flackern auf dem QR-Screen (Moiré-Bänderung beim Filmen mit einer Handykamera aus Scan-Distanz), weil derselbe QR-Code wiederholt gelöscht und neu aufgebaut wurde.

**Mechanismus:** `_last_drawn` hält einen Fingerabdruck dessen, was aktuell tatsächlich auf dem Display zu sehen ist (inklusive welcher der beiden Bildschirme – Status oder QR – gerade aktiv ist). Sowohl `_refresh()` als auch `_render_qr()` überspringen ihre I²C-Arbeit vollständig, wenn der Fingerabdruck, den sie zeichnen würden, bereits mit `_last_drawn` übereinstimmt. Da der Fingerabdruck den Bildschirm-Modus einschließt, erzwingt ein Wechsel zwischen Status- und QR-Anzeige immer einen Redraw, selbst wenn die jeweils zugrundeliegenden Daten zufällig unverändert wären – der Redraw wird rein dadurch ausgelöst, dass der tatsächlich sichtbare Inhalt vom aktuell gezeichneten abweicht.

**Partieller Redraw während einer echten Lukenfahrt:** Der obige Fingerabdruck-Vergleich hilft nicht, solange eine Luke tatsächlich fährt – dann ändert sich der rotierende Spinner (siehe unten) bei **jedem** Zyklus, der volle Redraw bliebe also weiterhin nötig und könnte spürbar mit der Servo-Rampe um I²C-Bus-Zeit konkurrieren. Für genau diesen Fall gibt es einen zweiten, feineren Mechanismus: `_last_lines` cacht die zuletzt gezeichneten 4 Zeilenstrings. Ändert sich bei einer Zeile **nur** das 4-Zeichen-Statusfeld (Spalten 96–127, die letzten 4 der 16 Zeichen) – der häufigste Fall während einer Bewegung –, wird **nicht** die ganze Anzeige neu aufgebaut, sondern nur dieses kleine 32×16px-Rechteck:

- `_show_region(display, x0, x1, page0, page1)` setzt das SSD1306-Adressfenster gezielt auf dieses Rechteck (`SET_COL_ADDR` 0x21 / `SET_PAGE_ADDR` 0x22 – der Standard-`ssd1306`-Treiber nutzt diese Befehle nie, sein `show()` adressiert immer den vollen 128×64-Bereich) und überträgt nur die zugehörigen Framebuffer-Bytes (64 statt 1024 Byte).
- `_redraw_status_region()` löscht vorher nur diese Zelle (`fill_rect`, verhindert Geisterbilder zwischen unterschiedlich geformten Spinner-Frames) und zeichnet den neuen Text hinein.
- Ändert sich dagegen auch der Kurzname-Teil einer Zeile (z. B. Config-Bearbeitung während gleichzeitig eine andere Luke fährt), fällt der Code auf den vollen Redraw zurück – dieser Fall ist selten und die Einfachheit hier wichtiger als weitere Optimierung.

Dieser Mechanismus geht am `ssd1306`-Treiber vorbei direkt auf dessen `write_cmd()`/`write_data()`/`buffer`/`width`-Attribute – Standardverhalten des verbreiteten `micropython-lib`-Treibers (page-majores `MONO_VLSB`-Pufferlayout), aber nicht Teil seiner offiziell stabilen API. Bei einer anderen Treiberversion müsste `_show_region()` neu geprüft werden.

**Layout Normalbetrieb** (128×64 px, 4 Zeilen à 16px, füllen die volle Höhe):

```
Zeile 0 (y=0):  "<short_name> <status>"   Luke 1
Zeile 1 (y=16): "<short_name> <status>"   Luke 2
Zeile 2 (y=32): "<short_name> <status>"   Luke 3
Zeile 3 (y=48): "<short_name> <status>"   Luke 4
```

Zeilenformat: `short_name` linksbündig auf 11 Zeichen, 1 Leerzeichen, dann exakt 4 Zeichen Status (immer feste Breite) – 16 Zeichen gesamt bei 8px/Zeichen = exakt 128px.

**Größere Schrift via `Writer`-Klasse:** Statt des eingebauten 8×8-Fonts von `framebuf`/`ssd1306.py` (der keine Skalierung unterstützt – nur ganzzahlige Pixel-Verdopplung wäre möglich, was als zu grob verworfen wurde) wird `lib/writer.py` (Peter Hinch, `micropython-font-to-py`, MIT-Lizenz) zusammen mit `lib/font_hatch_status.py` (14px, monospace, 8px Zeichenbreite, siehe 6.2b) verwendet. `Writer`-Instanz wird einmalig in `task_display()` erzeugt (nicht bei jedem Refresh neu). `wri.set_clip(row_clip=True, col_clip=True, wrap=False)` verhindert automatisches Zeilenumbrechen.

**Statuswerte, feste 4-Zeichen-Breite:**

| Wert | Bedeutung |
|---|---|
| `" \| "`/`" / "`/`" - "`/`" \\ "` | Soll≠Ist (Bewegung läuft, auch während Fail-Safe-Wiederherstellung) – rotierender Strich, ein Frame pro tatsächlichem Redraw (`_spin_frame`, nur solange sich die Zeile ohnehin ändert) |
| `"?ZU?"` | Ist == Soll == `FAILSAFE_DEG`: schwerkraftbedingt zugefallen, unverriegelt |
| `"ZU  "` | Position 0 in `positions` |
| `"AUF "` | letzte Position in `positions` |
| `"P2  "`…`"P5  "` | Zwischenpositionen (1-basiert) |
| `"??? "` | Ist-Position nicht in `positions` gefunden (sollte im Normalbetrieb nicht auftreten) |

**Historische Anmerkung – Spinner statt Klartext:** Der Bewegungsstatus wurde ursprünglich als Klartext `"BEWEGT"` (6 Zeichen) dargestellt. Da damit insgesamt mehr als 4 Zeichen für den Status nötig waren, wurde er auf die jetzige feste 4-Zeichen-Rotation umgestellt, nachdem entschieden wurde, dass **alle** Statuswerte (nicht nur die Positionslabels) einheitlich genau 4 Zeichen belegen sollen – Kurzname bekommt dadurch die übrigen 11 der 16 Zeichen einer Zeile.

**Invertierte Darstellung bei aktiver Config-Session:** Ist `session.is_session_active()` oder `session.is_unlock_active()` wahr, wird das gesamte Display invertiert (`display.invert(True)`) – einzige Anzeige am Gerät selbst dafür, dass die Konfigurationsseite gerade erreichbar ist (der eigentliche Config-Button erscheint nur auf der Weboberfläche). `config_reachable` ist Teil des Redraw-Fingerabdrucks, damit eine reine Session-Zustandsänderung (ohne Änderung der Statustexte selbst) trotzdem einen Redraw auslöst.

**QR-Modus** (`session.is_qr_active()` = True, ausgelöst durch kurzen Tastendruck):

- Oberer Bereich (0–47px, `qr_area_height = 64 - _IP_LINE_PX`): weißer Hintergrund, schwarzer QR-Code zentriert
- Unterer Bereich (48–63px, 16px): IP-Adresse als reiner Text (ohne `http://`-Präfix), im normalen eingebauten 8px-Font, horizontal zentriert – seit die IP-/WLAN-Zeile aus dem Normalbetrieb entfernt wurde, ist dies die einzige Stelle, an der die IP am Gerät sichtbar ist
- URL für die QR-Codierung: `http://<aktuelle-IP>` (STA oder AP); die separate Textzeile zeigt nur die nackte IP (`_current_ip()`)
- QR-Matrix wird einmalig via `uQR` generiert und gecacht solange sich die URL nicht ändert (Generierung dauert ca. 0,5s); die *Anzeige* wird zusätzlich über den in diesem Abschnitt beschriebenen Fingerabdruck-Mechanismus nur bei tatsächlicher Änderung neu gezeichnet
- Beim Eintritt in den QR-Modus wird `display.invert(False)` erzwungen, damit eine zuvor aktive Session-Invertierung den QR-Code nicht negativ darstellt
- Scale 1px/Modul + 4px Quiet Zone, auf die oberen 48px begrenzt – bei typischen Heim-IPs weiterhin ausreichend Platz
- Mit iOS Kamera-App direkt scannbar
- Wenn `uQR` nicht in `/lib` liegt: Fallback-Text `"uQR not found"` auf Display

### 9.14 `app/task_webserver.py`

microdot-Webserver auf Port 80. Modulvariablen `_hw`, `_config`, `_state`, `_targets` werden einmalig beim Task-Start gesetzt (Route-Handler können in microdot keine zusätzlichen Parameter empfangen).

**Routen:**

| Methode | Pfad | Funktion |
| ------- | --------------------- | ---------------------------------------------- |
| GET     | `/`                    | `index.html` (Redirect nach `/ap`, falls config.json nicht geladen werden konnte) |
| GET     | `/config`              | `config.html` (aktiviert Session, wenn Freigabefenster offen) |
| GET     | `/ap`                  | `ap_setup.html` |
| GET     | `/static/<path>`       | statische Dateien aus `/webpages/static/` |
| GET     | `/sse`                 | SSE-Stream, alle 500ms |
| GET     | `/api/config`          | aktuelle `config.json` als JSON |
| POST    | `/api/hatch/<i>/pos`   | Sollwert setzen `{position_idx: N}` |
| POST    | `/api/hatch/<i>/test`  | Hebelservo direkt anfahren `{hatch_deg: N}` – volle Sequenz; auch für den Hebelservo-Montagewinkel genutzt (Session nötig) |
| POST    | `/api/hatch/<i>/brake_test` | nur Halteservo direkt anfahren `{brake_deg: N}` – Montagewinkel, setzt `targets["brake_only"][i]` (Session nötig) |
| POST    | `/api/tester/<which>`  | Servotester-Ausgang anfahren, `which` ∈ {"hatch","brake"}, `{angle_deg: N}` (Session nötig) |
| POST    | `/api/config`           | Neue Config validieren, sofort persistieren + im RAM anwenden – **kein Reset**; wird bei jeder einzelnen Feld-Übernahme sowie bei jedem Positions-Einfügen/-Entfernen erneut mit dem kompletten aktuellen Dokument aufgerufen (Session nötig) |
| POST    | `/api/wifi`             | WLAN-Daten speichern + Reset |
| GET     | `/api/wifi/scan`        | WLAN-Scan → JSON `{networks: [{ssid, rssi}]}` |

**`POST /api/config` – atomares Schreiben:**

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

`os.rename()` ist auf dem ESP32-Dateisystem atomar – ein unterbrochenes Schreiben (Speicherfehler, Reset, Stromausfall) kann dadurch nie eine abgeschnittene `/config.json` zurücklassen (die das Gerät beim nächsten Boot am Start hindern würde). `ujson.dumps()` statt der früher verwendeten selbstgeschriebenen `pretty_json()`-Formatierung (siehe 9.16b) – reduziert die geschriebene Datenmenge (ca. 2,5 statt 5,5 KB) und vermeidet den großen, aus vielen kleinen Teilstrings zusammengesetzten Zwischenstring, der auf dem kleinen MicroPython-Heap zu Fragmentierung neigt. `del new_cfg` + `gc.collect()` direkt danach gibt den Speicher der soeben geschriebenen Kopie aktiv frei, statt auf die automatische Garbage Collection zu warten.

Bei Erfolg wird `_config` **in place** aktualisiert (`_config["global"]["servo_tester"].update(...)`, pro Luke `_config["hatches"][i].update(h)`), damit `task_control` und `task_display` die Änderung beim nächsten Durchlauf sehen, ohne dass eine Referenz ausgetauscht werden müsste.

**SSE-Implementierung:** Der `/sse`-Stream verwendet eine klassenbasierte `_SseStream`-Klasse statt eines async-Generators, da MicroPython bei async-Generatoren mit gleichzeitigem `yield` und `await` einen `TypeError` im microdot-Write-Pfad wirft (zweite Iteration liefert kein Bytes-Objekt). Die Klasse implementiert das `__aiter__`/`__anext__`-Protokoll explizit.

**Config-Session-Schutz:**

- `/config`: erreichbar wenn `is_session_active()` ODER `is_unlock_active()` → aktiviert Session
- `/api/hatch/<i>/test`, `/api/hatch/<i>/brake_test`, `/api/tester/<which>`, `POST /api/config`: nur wenn `is_session_active()`

**SSE-Payload** (`_build_status()`):

```json
{
  "hatches": [
    {"name": "Luke 1", "short_name": "Luke 1", "current_deg": 0,
     "target_deg": 0, "moving": false, "failsafe": false,
     "pos_idx": 0, "num_positions": 3}
  ],
  "wifi": {"connected": true, "ip": "192.168.8.70"},
  "session": {"active": false, "unlock": false}
}
```

Enthält **keine** Servotester-Felder – der Tester hat kein automatisches "moving"-Konzept, `config.html` liest seine Werte direkt aus `GET /api/config`.

### 9.15 `app/fatal_error.py`

Zentraler Handler für fatale Boot-Fehler. Ersetzt das frühere Muster von `return` aus `main()`, das in eine unkontrollierte WDT-Reboot-Schleife führte.

```python
async def fatal_error(message, hw=None, display=None)
```

Ablauf:
1. Fail-Safe-Fahrt versuchen wenn `hw` vorhanden (try/except – Fehler werden geloggt, aber ignoriert, damit die folgenden Schritte immer laufen)
2. Permanente Fehlermeldung auf OLED wenn `display` vorhanden (Kopfzeile + Nachricht auf 7 Zeilen à 16 Zeichen umgebrochen)
3. WLAN-AP „Dachluke-Setup" starten, damit die Fehlerseite erreichbar ist
4. Minimalen Webserver auf Port 80 starten und **nie zurückkehren**

Kein WDT wird gestartet → Power-Cycle ist der einzige Ausweg. Aufgerufen aus `main()` anstelle jedes `return` nach einem fatalen Fehler.

### 9.16 `app/config_migration.py`

Wird in `main()` Stage 2b **vor** `load_config()` aufgerufen. Stellt sicher, dass `config.json` existiert und alle erforderlichen Felder enthält.

```python
def migrate_config()   # raises ConfigMigrationError wenn Template fehlt
```

Logik:
- Template (`/config.json.template`) fehlt → `ConfigMigrationError` (fatal)
- `config.json` fehlt → Template verbatim kopieren
- `config.json` vorhanden und valide (`validate_config()` wirft keinen Fehler) → nichts tun
- `config.json` vorhanden aber ungültig oder Felder fehlen/umbenannt → Merge: bekannte Werte übernehmen, fehlende aus Template ergänzen, unbekannte Felder verwerfen (so werden z. B. Felder aus einer früheren Schema-Version wie die einstige flache `hatch_min_us`/`step_us`-Struktur automatisch entfernt)

Merge ist rekursiv für verschachtelte Dicts (verarbeitet die aktuellen Blöcke `global.servo_tester`, `hatch_servo`, `brake_servo` bereits generisch, keine Sonderbehandlung nötig). `hatches`-Liste wird immer als 4 Einträge verarbeitet. `positions`-Liste wird aus `config.json` übernommen, wenn nicht leer (kalibrierte Werte erhalten), sonst aus dem Template.

**In der Praxis:** Da `POST /api/config` (siehe 9.14) bei jeder Feldübernahme sofort und vollständig validiert schreibt, ist `config.json` im Normalbetrieb praktisch immer bereits valide – der Merge-Pfad greift nur unmittelbar nach einer Schema-Änderung im Code (neues Feld im Template, altes `config.json` noch vom vorherigen Schema) oder nach Datenkorruption.

### 9.16b `test_qr.py`

Historisches, eigenständiges Testskript aus der frühen Entwicklungsphase, um die QR-Code-Darstellung vor der eigentlichen Implementierung in `task_display.py` überhaupt erst auszuprobieren. Wird von keinem anderen Modul importiert oder aufgerufen. **Bekannter Fehler:** die Schwarz/Weiß-Zuordnung beim Zeichnen ist invertiert (schreibt `pixel=1` für ein QR-Modul, das schwarz sein soll, obwohl auf dem SSD1306 der Wert `1` "weiß" bedeutet) – die produktive Fassung in `task_display._render_qr()` macht es korrekt. Nur manuell per REPL ausführbar (`exec(open('test_qr.py').read())`), nicht Teil des regulären Boot-/Testablaufs.

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
- Von der Pro-Kanal-Umstellung **unberührt** – verwendet nur `moving`/`failsafe`/`pos_idx`/`num_positions`/`name`/`short_name` aus der SSE-Payload, keine Feldnamen, die sich geändert haben

### 10.2 `config.html` – Konfigurationswebseite

- Alpine.js `configApp()` Komponente, lädt Config via `GET /api/config` beim Seitenstart
- **Kein gemeinsamer "alle 4 Luken"-Bereich mehr** – jede Luke ist ein eigenständiges Accordion-Item, Titel = `name`
- **5. Accordion-Item „Servotester"**, mit Warnhinweis-Text (nur für ausgebaute, unbelastete Servos; PWM-Frequenz unabhängig von den 4 Kanälen)
- **Download-Button** (Navbar): baut aus dem aktuell geladenen/gespeicherten `buildConfig()`-Dokument einen `Blob`, triggert Download via `<a download="config.json">` – rein client-seitig, kein eigener Server-Endpunkt
- **Sprachumschalter** (Navbar, DE/EN-Dropdown), Default aus `navigator.language`

**Kanal-Karte (pro Luke), Aufbau von oben nach unten:**

1. Name, Kurzname (Textfelder, volle Breite, kein 2-Spalten-Layout)
2. PWM-Frequenz (ein Feld, gilt für Hebel- **und** Halteservo dieses Kanals gemeinsam, siehe 9.11)
3. **Zwei Spalten**, durch dünne vertikale Linie getrennt: links "Hebelservo, Kanal N", rechts "Halteservo, Kanal N"
   - Zeile 1: Stellbereich (Auswahl 180°/270°/360°), Drehrichtung (Auswahl pos/neg), Geschwindigkeit (°/s)
   - Zeile 2: Impulszeit bei 0°, Impulszeit bei aktuellem `range_deg`° (Label passt sich dynamisch an den gewählten Stellbereich an – nur die Beschriftung, die Werte selbst bleiben beim Bereichswechsel unverändert)
   - Zeile 3: Hard-Stop min/max (°)
   - Nur Halteservo-Spalte, Zeile 4: Bremse Auf/Zu (°) – die beiden regulären Betriebspositionen
   - Zeile (Hebel: 4, Halte: 5): Montagewinkel + ✓ + **sichtbarer Abstand** + ▶-Anfahren-Button (verhindert Fehlklicks beim Ändern des Werts). Hebelservo-▶ löst die volle Sequenz aus (`/api/hatch/<i>/test`), Halteservo-▶ bewegt nur diesen Servo (`/api/hatch/<i>/brake_test`)
4. Rastpositionen (nur Hebelservo-Spalte; Halteservo-Spalte bleibt dort leer, um das 2-Spalten-Raster beizubehalten)

**Servotester-Karte:** gleicher 2-Spalten-Aufbau, Zeile 1–3 identisch zur Kanal-Karte, eine gemeinsame PWM-Frequenz-Zeile über beiden Spalten, danach pro Spalte zwei Testwinkel-Zeilen (A und B, je mit ✓ + Abstand + ▶) statt Positionsliste/Montagewinkel – damit lässt sich jeder Testerkanal beliebig zwischen zwei frei wählbaren Winkeln hin- und herfahren.

**Feldzustände** (zwei statt drei, da Übernahme sofort persistiert):

- `draft !== committed` → gelb (`field-edited`) – getippt, noch nicht übernommen
- sonst → weiß (übernommen = bereits dauerhaft gespeichert)

**Interaktion pro Feld:** ↺ (Zurücksetzen auf `committed`), Eingabefeld, ✓ (Übernehmen → lokale Validierung → komplettes aktuelles Dokument per `POST /api/config` senden → bei Erfolg `committed = draft`, bei Fehler Rollback des `committed`-Werts + Fehlermeldung, `draft` bleibt zur Korrektur stehen).

**`persistConfig()` – Fehlerklassifizierung:** `buildConfig()` läuft bewusst außerhalb des Netzwerk-`try`-Blocks (ein JS-Fehler dort ist ein Client-Bug, kein Netzwerkfehler). Die Antwort wird immer zuerst als Text gelesen und dann als JSON geparst, unabhängig vom HTTP-Status – ein `400` mit gültigem `{"error": "..."}`-Body (normale Validierungsablehnung, "Bedienfehler") wird als reine Fehlermeldung angezeigt; nur eine Antwort, die sich gar nicht als JSON parsen lässt, wird als "Server error NNN: ..." dargestellt (echter Absturz/kaputte Antwort).

**Anfahren-Button (▶):** nur aktiv wenn `draft === committed` (Wert bereits übernommen und persistiert) → sendet direkt die passende Test-Route, **ohne** vorherigen Apply-Schritt.

**Positionen:** `positions[0]` (ZU) und `positions[length-1]` (AUF) sind feste Randwerte und werden nie verschoben. Eine neue Zwischenposition wird immer unmittelbar **vor AUF** eingefügt, Default-Wert = Mittelwert aus AUF und der bisher letzten Zwischenposition (bzw. aus AUF und ZU, falls noch keine Zwischenposition existiert). Entfernen löscht die jeweils letzte Zwischenposition (Index `length-2`), nie ZU oder AUF selbst. Beides sofort persistiert (kein zusätzlicher ✓-Klick).

**Mehrsprachigkeit (i18n):** Ein `I18N`-Objekt mit den Sprachcodes `de`/`en`, je ein flaches Dictionary aller Textschlüssel. `t(key)` liest daraus. Nur Web-UI-Texte (Labels, Buttons, Legenden, Hinweistexte) sind übersetzt; Fehlermeldungen vom Gerät (`validate_config()`) sowie alle Boot-/Konsolenausgaben bleiben bewusst Englisch, unabhängig von der UI-Sprache (Diagnosezwecke, nicht für Endnutzer gedacht).

### 10.3 `ap_setup.html` – WLAN-Konfiguration

- Scan startet automatisch beim Seitenaufruf (`GET /api/wifi/scan`)
- Ergebnisse als anklickbare Liste mit RSSI-Balken-Anzeige
- Klick → füllt SSID-Feld
- Passwort-Feld mit Anzeigen/Verbergen-Toggle
- Submit → `POST /api/wifi` → `machine.reset()`
- Nach Verbindungsabbruch (Gerät hat AP deaktiviert): Hinweis, dass Gerät neu startet und IP auf OLED angezeigt wird
- Von der Pro-Kanal-Umstellung **unberührt**

### 10.4 Bibliotheken (CDN mit lokalem Fallback)

- Bootstrap 5.3.3 (`bootstrap.min.css`, `bootstrap.bundle.min.js`)
- Alpine.js 3.14.1 (`alpine.min.js`)

Alle drei HTML-Seiten laden die Bibliotheken **primär vom CDN** (jsdelivr.net). Bei nicht erreichbarem CDN greift ein `onerror`-Handler auf die lokalen Kopien in `source/webpages/static/` zurück. Der Browser cached CDN-Dateien nach dem ersten Laden, danach entfällt der Netzwerkzugriff komplett.

Alpine.js wird **ohne** `defer` am Ende von `<body>` geladen, damit die jeweilige `function appName()` bereits definiert ist, wenn Alpine initialisiert.

---

## 11. Async-Architektur

### 11.1 Tasks (alle in `uasyncio.gather()`)

| Task             | Intervall                       | Aufgabe                               |
| ---------------- | -------------------------------- | -------------------------------------- |
| `task_watchdog`  | 200ms                            | Hardware-WDT füttern                  |
| `task_display`   | 500ms (Prüfung; Redraw nur bei Änderung) | OLED aktualisieren        |
| `task_control`   | 10ms (nach Bewegung) / 50ms (Idle); während einer Bewegung: siehe `_ramp()`-Update-Intervall | Soll/Ist-Vergleich, Bewegungssequenzen |
| `task_button`    | 50ms                              | Button pollen, Session-Unlock/QR starten |
| `task_webserver` | event-driven                     | HTTP-Requests, SSE-Stream             |

### 11.2 Coding-Regel

**Jede `while`-Schleife im Projekt muss mindestens ein `await` enthalten.**

Hintergrund: kooperatives Multitasking ohne Prioritäten. Blockierender Code ohne `await` verhindert, dass `task_watchdog` läuft → WDT-Reset.

### 11.3 I²C-Bus-Konkurrenz zwischen Display und Servo-Ansteuerung

`task_display` und `task_control` teilen sich denselben physischen I²C-Bus **und** denselben kooperativen Event-Loop. Ein blockierender I²C-Transfer in `task_display` (OLED-Redraw) verzögert die nächste geplante Fortsetzung von `task_control`s Bewegungsrampe um genau diese Zeit. Bei einer schrittzahlbasierten Rampe (frühere Implementierung) summierte sich das zu einer **regelmäßigen, physisch spürbaren** Geschwindigkeitsschwankung am Servo. Zwei voneinander unabhängige Gegenmaßnahmen wurden eingebaut, die sich gegenseitig ergänzen (siehe 9.11 und 9.13 für die jeweiligen Details):

1. `task_display` zeichnet nur noch, wenn sich der Inhalt tatsächlich geändert hat (drastisch weniger I²C-Traffic im Normalbetrieb, insbesondere beim Servotester)
2. `task_control`s Rampe ist zeitbasiert statt schrittzahlbasiert (verzögerte Ticks werden aufgeholt statt verloren zu gehen)

### 11.4 Hardware-Watchdog

- Timeout: 4000ms
- Feed-Intervall: 200ms
- Initialisiert innerhalb `task_watchdog` (nicht in `main.py`)
- WDT ist erst aktiv, wenn `task_watchdog` gestartet wurde (d.h. nach erfolgreichem Abschluss aller Startphasen)

---

## 12. Fail-Safe

**Auslöser:**

- WDT-Reset (Programm hat nicht mehr reagiert)
- Unbehandelter Fehler im Startablauf (`fatal_error()`, siehe 9.15)

**Sequenz** (`fail_safe_close_all`, siehe 9.7):

1. PCA9685-Frequenz explizit auf 50Hz erzwingen (unabhängig vom vorherigen Zustand)
2. Für jede Luke: Bremsen-Servo-PWM auf `brake_open_us` setzen
3. Relais ein
4. `time.sleep_ms(BRAKE_TRAVEL_MS)` – Bremse öffnet
5. Relais aus – Dachluke schließt durch Schwerkraft; Brems-Servo bleibt stromlos in Stellung „auf" (Federkraft reicht nicht aus, den Servo über die Nockenscheibe zu verstellen → Luke bleibt unverriegelt)
6. 200ms Pause → nächste Luke

**Warum ruckt der Hebelservo im Fail-Safe nicht?** `fail_safe_close_all` setzt ausschließlich den Brems-Kanal auf einen gültigen PWM-Wert. Der **Hebel-Kanal bleibt auf 0µs** – kein gültiger Puls. Der Servo-Controller erkennt das fehlende Signal und aktiviert seinen Motor nicht. Dieses Verhalten wird in der normalen Bewegungssequenz bewusst nachgebildet (siehe 9.11, `step_preset_servos`).

Nach Fail-Safe (WDT-Reset): normaler Startablauf wird fortgesetzt. In Stage 2b werden alle Luken in `state.json` auf `FAILSAFE_DEG` (-1) gesetzt – Soll und Ist stimmen damit mit der physischen Realität überein. `task_control` bleibt danach für diese Luken passiv (Soll == Ist == `FAILSAFE_DEG`). Erst ein neuer Nutzerbefehl (echte Grad-Position) löst die vollständige Bewegungssequenz inkl. Verriegelung aus. Der Servotester hat kein eigenes Fail-Safe-Konzept (bewegt sich nie automatisch) und bleibt von alldem unberührt.

---

## 13. Config-Session (Sicherung der Konfigurationsseite)

**Ziel:** Konfigurationsseite nur nach physischem, **langem** Tastendruck am Gerät erreichbar.

**Ablauf:**

1. User-Button im Normalbetrieb **lang** drücken (≥ `BTN_LONG_PRESS_MS` = 3s) → `session.unlock_config()` → 60s-Fenster
2. Innerhalb 60s: Config-Button erscheint auf Bedienwebseite
3. Config-Button klicken → `/config` aufrufen → `session.activate_session()` → 1h-Session
4. Während 1h: Config-Seite über Button oder Direktaufruf erreichbar
5. Nach 1h: Button verschwindet beim nächsten Seitenaufruf, Direktaufruf geblockt

**Sicherheitsebene:**

- Physischer Zugang zum Gerät nötig (langer Tastendruck)
- Zeitbegrenztes Fenster
- Alle bewegungsauslösenden und schreibenden Config-Routen (siehe 9.14) nur mit aktiver Session

**Sichtbarkeit am Gerät:** Invertierte Displaydarstellung während Freigabefenster oder aktiver Session (siehe 9.13) – einzige Anzeige direkt am Gerät, ansonsten ist der Status nur auf der Webseite selbst sichtbar.

---

## 14. QR-Code-Anzeige

**Zweck:** Einfacher Zugang zur Web-UI ohne IP-Adresse tippen zu müssen.

**Auslösung:** Kurzer Tastendruck (< `BTN_LONG_PRESS_MS` = 3s) auf den User-Button.

**Ablauf:**
1. `session.show_qr()` setzt `_qr_until = time.time() + 15`
2. `task_display` erkennt `is_qr_active()` = True und ruft `_render_qr()` auf
3. QR-Matrix wird einmalig via `uQR` generiert (URL = `http://<IP>`) und gecacht; die Anzeige selbst wird zusätzlich nur bei tatsächlicher Änderung neu gezeichnet (siehe 9.13)
4. OLED zeigt weißen Hintergrund mit schwarzem QR-Code (zentriert, 4px Quiet Zone), begrenzt auf die oberen 48px
5. In den unteren 16px erscheint zusätzlich die reine IP-Adresse (ohne `http://`) als Text im normalen 8px-Font, horizontal zentriert
6. Nach 15s kehrt `task_display` automatisch zum Normalmodus zurück

**Scannbarkeit:** Mit iOS Kamera-App direkt scannbar. Scale 1 (1px/Modul) ist im auf 48px reduzierten QR-Bereich bei typischen Heim-IPs weiterhin ausreichend.

**Bibliothek:** `uQR` (JASchilz/uQR) – erzeugt eine 2D-Bool-Matrix. True = schwarzes Modul, False = weißes Modul. Muss manuell als `/lib/uQR.py` auf das Gerät kopiert werden (nicht per mip installierbar).

---

## 15. Bekannte Fallstricke & Diagnosewissen

### I²C-Adresse 0x7F nicht per scan() findbar

`i2c.scan()` deckt per I²C-Spezifikation nur `0x08–0x77` ab. Der Seeed Grove PCA9685 hat defaultmäßig Adresse `0x7F` (alle Adress-Pads auf High). Er erscheint daher **nie** in Scan-Ergebnissen, ist aber direkt ansprechbar. Diagnose: `i2c.writeto(0x7F, b'')` + `i2c.readfrom(0x7F, 1)` – MODE1-Register `0x11` bedeutet Gerät antwortet korrekt. `hw_init._check_i2c_device()` berücksichtigt das bereits automatisch (Adressen über 0x77 werden direkt angeschrieben statt gescannt).

### Bus-Kurzschluss-Pattern

Wenn `i2c.scan()` alle Adressen von 8 bis 119 zurückgibt → SDA oder SCL falsch (z.B. auf GND gezogen). Kein echter Scan-Befund, sondern Artefakt. Korrekte Pins für XIAO ESP32-S3 auf Expansion Board: SDA=5, SCL=6.

### config.json Markdown-Backticks

Beim Kopieren von JSON aus Chat-Konversationen können Markdown-Backticks (` ``` `) am Ende der Datei landen. MicroPython's `ujson` wirft dann `JSON parse error`. Datei im Editor prüfen und Backticks entfernen.

### mpremote und Port-Wechsel

mpremote toggelt beim Verbinden DTR/RTS → Hardware-Reset des ESP32 → Port wechselt ggf. von `/dev/ttyACM0` auf `/dev/ttyACM1`. Wenn Skripte dann mit dem alten Port weitermachen, schlagen sie fehl. Lösung: Gerät kurz vom USB trennen und neu anstecken.

### `mpremote reset` schlägt mit `TransportError: could not enter raw repl` fehl

Tritt typischerweise am Ende von `upload.sh` auf, wenn das Gerät nach dem Upload nicht sauber in den REPL-Modus wechseln kann – meist, weil ein Import-Fehler in einer der frisch hochgeladenen Dateien das Gerät in eine Boot-Schleife schickt statt in Ruhe auf den REPL zu warten. Die zuvor hochgeladenen Dateien sind davon in der Regel nicht betroffen (der Fehler tritt erst beim finalen Start auf). Diagnose: `mpremote connect /dev/ttyACM0` (offen lassen, nichts tippen) zeigt den Python-Traceback des Importfehlers.

### PCA9685 überlebt einen reinen ESP32-Reset

Der PCA9685 hat eine eigene Spannungsversorgung und bleibt bei `mpremote reset` oder einem Software-Reset des ESP32 durchgehend bestromt – sein `PRE_SCALE`-Register (PWM-Frequenz) und alle sonstigen internen Zustände überleben unverändert. Nach Änderungen an PWM-/Timing-nahem Code (insbesondere an `pwm_freq_hz`-Handling) kann sich das als scheinbar zufälliges "geht nach Upload nicht, geht nach Stromzyklus" äußern. Abhilfe: nach solchen Änderungen einen kompletten Stromzyklus (USB ab/an) statt nur `mpremote reset` durchführen. Siehe auch 6.3.

### `Ctrl+C` beweist nicht, wo ein Hänger sitzt – nur, dass der Loop *zu diesem Zeitpunkt* lief

Ein `KeyboardInterrupt` per `Ctrl+C` in einer offenen `mpremote`-Verbindung zeigt einen Traceback der Stelle, an der der Event-Loop *im Moment des Interrupts* gerade war. Landet er wiederholt in `asyncio/core.py`s `wait_io_event` (der normalen Leerlauf-Wartestelle zwischen Tasks), heißt das: der Loop **läuft normal weiter** und ist nicht hängengeblieben – ein zufällig getroffener Zeitpunkt in einer aktiv wartenden Coroutine ist kein Hänger-Beweis, auch wenn der Rest des Systems (z. B. die Weboberfläche) gerade unerreichbar wirkt. In so einem Fall liegt die Ursache eher außerhalb des reinen Python-Event-Loops (z. B. WLAN-/Socket-Ebene) als in einer Endlosschleife im eigenen Code. Ein permanent mitlaufender, periodisch geloggter Zustandswert (z. B. `gc.mem_free()`, siehe historische Anmerkung in 9.10) kann das zuverlässiger unterscheiden als ein einzeln getimter `Ctrl+C`-Versuch: Läuft der periodische Log bis kurz vor einen Watchdog-Reset unverändert weiter, ist der Loop selbst nie hängengeblieben.

### Sofort-Persistenz + Bereichsverkleinerung = Reihenfolge-Falle

Siehe 9.6: Wird `range_deg` oder ein Hard-Stop verkleinert, während ein abhängiger Wert (Position, Montagewinkel, `open_deg`/`close_deg`, Testwinkel) noch außerhalb des neuen, kleineren Bereichs liegt, lehnt die Validierung die Änderung ab – mit einer Fehlermeldung zum abhängigen Feld, nicht zwingend zum gerade bearbeiteten. Immer zuerst die abhängigen Werte in den neuen Bereich bringen, dann den Bereich selbst verkleinern.

### `main.py` unterbrechen für REPL-Zugang

`main.py` startet sofort und blockiert den REPL über `uasyncio.run(main())` – solange das Programm läuft, erscheint **kein** `>>>`-Prompt, egal wie oft Enter gedrückt wird (das ist normales Verhalten, kein Fehler). Ein `Ctrl+C` in der offenen `mpremote`-Verbindung unterbricht den laufenden Loop sauber und öffnet den Prompt. Alternativ, um dauerhaft ohne `main.py`-Start zu booten:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main.py', '/main_bak.py')"
```
Zurückbenennen:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main_bak.py', '/main.py')"
```

### Lock-Dateien blockieren mpremote

MicroPython Studio hinterlässt Lock-Dateien in `/tmp/`. Bei Abstürzen oder hartem Disconnect bleiben sie stehen:
```bash
rm /tmp/mps_lock__dev_ttyACM*.lock
```

---

## 16. Vor-Ort-Bedienfeld (Status: noch nicht realisiert)

Direktbedienung am Gerät unabhängig vom Web-Interface, aus dem ursprünglichen Designkonzept übernommen. **Bisher nicht umgesetzt** – weder Hardware verbaut noch Software geschrieben.

**Bedienkonzept:**

- **4 Lukentasten** (Luke 1–4 auswählen)
- **6 Positionstasten** (Zielposition 1–6 wählen)
- **6 LEDs** (eine vor jeder Positionstaste)

Bedienung: Lukentaste drücken → LEDs zeigen die aktuelle Istposition dieser Luke an. Positionstaste drücken → Luke fährt zur gewählten Position (setzt `targets["hatches"][i]["hatch_deg"]` genauso wie `task_webserver` – keine Änderung an `task_control.py` nötig).

**Geplante Hardware – MCP23017 (I²C-GPIO-Expander, Adresse 0x20):**

- 16 GPIO gesamt, frei als Ein-/Ausgang konfigurierbar
- 10 Eingänge (Taster, interne Pull-ups nutzbar)
- 6 Ausgänge (LEDs mit Vorwiderständen)
- Interrupt-Ausgang verfügbar → ESP32 kann auf Tastendruck reagieren ohne Polling
- Vorgesehen als Breakout-Board mit Pinheadern, Verdrahtung der Taster und LEDs direkt am Board auf einer Lochrasterplatine

**Zurückgestellt bis:** Bedarf nach Hardware-Erfahrung mit der Weboberfläche neu bewertet.

**Implementierung, wenn umgesetzt:** neues Modul `app/task_panel.py`, Import in `main.py`, in `gather()` hinzufügen.

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

Anbindung im Hauptsystem: zusätzlicher Task oder Webhook-Endpunkt → setzt Sollwerte über `targets["hatches"]`.

### 17.2 RTC-Zeitplanung

PCF8563 auf Expansion Board bereits vorhanden (I²C 0x51). Automatisches Öffnen/Schließen zu konfigurierten Zeiten → neuer Task + Erweiterung der Konfigurationswebseite.

### 17.3 Event/Trigger-System

Für alle Erweiterungen ist eine saubere Abstraktionsschicht geplant:

- Trigger-Quellen: Web-UI, lokale Taster, Regensensor, RTC
- Aktion: `targets["hatches"][i]["hatch_deg"] = neue_position_deg`
- Neue Quellen ohne Umbau der Kernlogik ergänzbar – `task_control.py` reagiert bereits jetzt ausschließlich auf Änderungen in `targets`, unabhängig davon, wer sie setzt

### 17.4 Feinere Rampen-Updates bei hoher PWM-Frequenz

`MIN_STEP_MS` (aktuell 8ms) ist die Untergrenze für das Update-Intervall der Bewegungsrampe, erreicht bei `pwm_freq_hz` ≥ 125Hz (siehe 9.11). Bei Bedarf nach noch feineren Updates (z. B. für besonders hochauflösende oder schnelle Servos) könnte dieser Wert weiter gesenkt werden – Kehrseite: höhere I²C-Bus-Last pro Sekunde, was das in Abschnitt 11.3 beschriebene Konkurrenzproblem mit `task_display`/`task_webserver` wieder verschärfen könnte. Nur mit konkretem Bedarf ändern.

### 17.5 Mehrfeld-Übernahme (Sammel-Speichern) für die Konfigurationsseite

Aktuell validiert jede einzelne Feld-Übernahme sofort das komplette Dokument (siehe 9.6, "Reihenfolge-Falle"). Eine optionale Batch-Übernahme (mehrere Entwürfe sammeln, dann gemeinsam senden) würde dieses UX-Problem lösen, wurde aber bewusst nicht umgesetzt, um das einfachere Sofort-Persistenz-Modell zu erhalten. Bei wiederkehrendem Bedarf hier ansetzen.

---

## 18. Status / Nächste Schritte

**Erledigt:**

- Vollständige Hardware-Inbetriebnahme (I²C-Bus, PCA9685, Relay, Display, Button)
- Grad-basierte Konfiguration mit Servo-Kalibrierung (Stellbereich, Richtung, Impulszeiten, Hard-Stops) pro Kanal
- Sofort-Persistenz der Konfiguration (kein Sammel-Speichern, kein Reboot für Config-Änderungen)
- Zeitbasierte S-Kurven-Bewegungsrampe für Hebel- **und** Halteservo
- Servotester-Funktion (2 zusätzliche, dauerbestromte PWM-Kanäle)
- Montagewinkel pro Servo für Wartungszwecke
- Pro-Kanal-PWM-Frequenz (mit dokumentierten Hardware-Grenzen, siehe 9.11)
- Mehrsprachige Konfigurationsoberfläche (DE/EN)
- Config-Download-Button
- QR-Code-Anzeige mit IP-Text, invertierte Session-Anzeige
- Fail-Safe-Sequenz inkl. WDT-Reset-Erkennung
- Behobene Bugs aus der Hardware-Erprobung: PWM-Frequenz-Reihenfolge (`_ensure_pwm_freq` vor `set_us()`), I²C-Bus-Konkurrenz Display/Servo (bedingter Redraw + zeitbasierte Rampe), atomares Config-Schreiben, Speicherfragmentierung beim Config-Schreiben, diverse Docstring-/Kommentar-Inkonsistenzen aus den verschiedenen Umbaustufen

**Offen:**

1. **Weitere Hardware-Erprobung unter realer Last** (Langzeittest, insbesondere der PWM-Frequenz-Konkurrenz zwischen Servotester und Lukenbetrieb, siehe 9.11)
2. **Lokales Bedienfeld:** Nach Bedarfsklärung (siehe 16)
3. **Unit-Tests** (auf Eis): Sinnvoll für `state.py`-Validierung und `pca9685.py`-Berechnung – nach weiterer Stabilisierung der API
4. **Regensensor:** Eigenständiges Prototyping-Projekt (siehe 17.1)
5. **Anwenderdokumentation** für Endnutzer/Nachnutzer – geplant als eigenständiges Dokument, aufbauend auf diesem hier
