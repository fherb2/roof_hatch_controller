# Gesamtkonzept – Dachlukensteuerung

## Kontext und Projektziel

Ziel ist die Entwicklung einer Steuerung für bis zu **4 Dachluken**, die jeweils mit zwei Modellbauservos ausgestattet sind: einem kräftigen Servo zum Öffnen/Schließen der Luke und einem kleineren Servo für eine mechanische Feststellbremse. Die Steuerung erfolgt über ein Web-Interface (Handy, Tablet, Desktop) sowie über ein lokales Bedienfeld mit Tastern und LEDs direkt am Gerät. Später soll ein Regensensor (Funk) und eine RTC-basierte Zeitplanung ergänzt werden.

Das System wird in MicroPython auf einem Seeed XIAO ESP32-S3 entwickelt. Alle nachfolgenden Konzeptentscheidungen sind auf diese Plattform abgestimmt.

---

## 1. Hardware-Übersicht

### Hauptkomponenten

| Komponente | Funktion |
|---|---|
| Seeed XIAO ESP32-S3 | Mikrocontroller, WLAN, uasyncio |
| Seeed Expansion Board Base for XIAO | I²C-Hub, OLED-Display (SSD1306 128×64), Micro-SD-Slot, RTC (PCF8563), User-Button |
| Grove 4-Channel SPDT Relay | Servo-Spannungsversorgung schalten, ein Relais pro Luke |
| Grove 16-Channel PWM Driver (PCA9685) | PWM-Signale für alle Servos |
| MCP23017 (Breakout-Board) | I²C-GPIO-Expander für lokales Bedienfeld (Taster + LEDs) |

### I²C-Busteilnehmer (alle auf einem Bus)

| Gerät | I²C-Adresse |
|---|---|
| OLED SSD1306 | 0x3C |
| PCA9685 PWM-Driver | 0x40 |
| Grove 4-Ch. Relay | 0x11 (oder 0x12, je nach Konfiguration) |
| RTC PCF8563 (auf Expansion Board) | 0x51 |
| MCP23017 GPIO-Expander | 0x20 (Adresspins A0–A2 auf GND) |

Keine Adresskollisionen vorhanden.

### Elektrische Verdrahtung Servos

- **Pro Dachluke ein Relais**: schaltet die gemeinsame +8V-Versorgungsader beider Servos (Luke-Servo + Brems-Servo) dieser Luke.
- **GND** ist für alle Servos durchgehend gemeinsam (über Klemmleiste).
- Jeder Servo hat eine **eigene PWM-Leitung** vom PCA9685.
- Die 8V-Servo-Versorgung kommt von einem **3A-Netzteil** – ausreichend für genau eine Luke gleichzeitig → softwareseitig sicherstellen.

### PCA9685-Kanalzuordnung

| PCA9685-Kanal | Funktion |
|---|---|
| 0 | Luke 1 – Dachluke-Servo |
| 1 | Luke 1 – Brems-Servo |
| 2 | Luke 2 – Dachluke-Servo |
| 3 | Luke 2 – Brems-Servo |
| 4 | Luke 3 – Dachluke-Servo |
| 5 | Luke 3 – Brems-Servo |
| 6 | Luke 4 – Dachluke-Servo |
| 7 | Luke 4 – Brems-Servo |

### Relay-Zuordnung

| Relay-Kanal | Funktion |
|---|---|
| 0 | +8V für beide Servos Luke 1 |
| 1 | +8V für beide Servos Luke 2 |
| 2 | +8V für beide Servos Luke 3 |
| 3 | +8V für beide Servos Luke 4 |

---

## 2. Mechanik und Servos – wichtige Hinweise

### Feststellbremse

Die Bremse besteht aus ineinandergreifenden Zähnen (Rastsystem), die das Haltemoment der Luke übernehmen. Wichtige Eigenschaften:
- Das Getriebe der Luke ist **nicht selbsthemmend** → ohne Bremse und ohne Strom würde der Servo gegen die Last anlaufen und überhitzen.
- Die Bremse hat **wenige Grad Spiel**, rastet federbelastet ein. Der Brems-Servo zieht sie gegen die Feder auf.
- Es gibt **wenige diskrete Rastpositionen** (neben ganz auf/zu einige Zwischenstellungen, maximal 6 Positionen pro Luke).
- Diese Positionen definieren die wählbaren Lukenpositionen.

### Servo-Thermik

Modellbauservos (hier: ~4,5 Nm / 2–3 A Stallstrom) sind für **intermittierenden Betrieb** ausgelegt, nicht für Dauerhalten gegen Last. Dauerlast > 20–30 % des Stallstroms über mehrere Sekunden kann zur Überhitzung führen (kein Thermoschutz eingebaut).

**Deshalb**: Servos werden **nur während des Bewegungsvorgangs bestromt** (Relais ein), danach stromlos (Relais aus). Die mechanische Bremse übernimmt das Haltemoment. Die Phase, in der der Servo gegen die Last hält (zwischen Relais-Ein und Bremse-Öffnung bzw. Bremse-Schließen und Relais-Aus), muss softwareseitig auf **maximal 2–3 Sekunden** begrenzt sein.

**Watchdog**: Falls die Sequenz hängt und ein Servo länger als 5 Sekunden unter Last steht → Relais notfallmäßig abschalten, Fehler melden.

---

## 3. Plattform

**MicroPython** auf dem XIAO ESP32-S3.

### Begründung
- `uasyncio`: Kooperatives Multitasking für Webserver, Display, Steuerloop und Button-Monitor ohne echtes Threading.
- `microdot`: Async-fähiger Webserver, geeignet für REST-API + Server-Sent Events (SSE).
- `ujson`: Eingebaut, für Konfiguration und Zustandsdateien.
- Stabile Bibliotheken für PCA9685, SSD1306, MCP23017 verfügbar (via `mip` oder manuell).
- Gut geeignet für Zustandsmaschinen und Ablauflogik.

### Bibliotheken (Drittanbieter, vor Implementierung konkret auswählen und prüfen)
- `microdot`: [github.com/miguelgrinberg/microdot](https://github.com/miguelgrinberg/microdot)
- `writer.py` (optionale erweiterte Fonts): [github.com/peterhinch/micropython-font-to-py](https://github.com/peterhinch/micropython-font-to-py)
- PCA9685-Treiber: mehrere inoffizielle Implementierungen auf GitHub/micropython-lib → bei Implementierung auswählen
- MCP23017-Treiber: ebenso → bei Implementierung auswählen

---

## 4. Datenhaltung (interner Flash)

Keine SD-Karte. Alle Daten im internen Flash des ESP32-S3.

| Datei | Inhalt |
|---|---|
| `wifi.json` | SSID, Passwort für WLAN-Client-Betrieb |
| `config.json` | Servoparameter aller 4 Luken (siehe Abschnitt 5) |
| `state.json` | Letzter bekannter Ist-Winkel aller Servos (Positionspersistenz über Stromausfall) |

### Warum Positionspersistenz wichtig ist
Beim Neustart nach Stromausfall muss der PCA9685 zuerst den letzten bekannten Winkel ausgeben, **bevor** das Relais zuschaltet – sonst gibt es einen unkontrollierten Ruck, da der Servo aus einer unbekannten Position in die gespeicherte springt.

---

## 5. Konfigurationsparameter (config.json)

**Pro Dachluke:**
- Winkel für jede der bis zu 6 Rastpositionen (Luke + Bremse jeweils)
- Bewegungsgeschwindigkeit: Schrittgröße (Grad pro Schritt) + Schrittintervall (ms)
- Zeitversatz: Wartezeit nach Bremse-Öffnen vor Lukenbewegung (ms)
- Zeitversatz: Wartezeit nach Erreichen der Zielposition vor Bremse-Schließen (ms)

**Konfiguration erfolgt über die Konfigurations-Webseite** (siehe Abschnitt 9), nicht durch manuelle Dateibearbeitung.

---

## 6. Bewegungsablauf einer Dachluke

1. PCA9685 gibt letzten bekannten Ist-Winkel beider Servos aus (`state.json`).
2. Relais zuschalten → Servos erhalten Spannung, halten Position aktiv.
3. Brems-Servo fährt auf Bremse-Auf-Winkel (gegen Federkraft). Maximal 3 s, sonst Watchdog.
4. Warten (konfigurierter Zeitversatz Bremse→Bewegung).
5. Dachluke-Servo fährt schrittweise zur Zielposition (Schrittgröße + Intervall aus config.json).
6. Warten (konfigurierter Zeitversatz Bewegung→Bremse).
7. Brems-Servo fährt auf Bremse-Zu-Winkel → Bremse schnappt federbelastet ein.
8. Kurz warten (Einschnapp-Sicherheitszeit).
9. Relais abschalten → Servos stromlos.
10. Ist-Winkel in `state.json` aktualisieren.

---

## 7. Steuerloop-Prinzip

- Jede Dachluke hat intern einen **Sollwert** (Zielposition, Index 0–5) und einen **Istwert** (aktuelle Position).
- Ein zentraler async-Task durchläuft kontinuierlich alle 4 Luken.
- Gibt es bei einer Luke einen Unterschied zwischen Soll- und Istwert → Bewegungsablauf (Abschnitt 6) vollständig ausführen.
- Erst danach zur nächsten Luke weitergehen.
- **Immer nur eine Luke gleichzeitig aktiv** → Netzteil-Anforderung automatisch erfüllt.
- Keine explizite Warteschlange: Sollwerte können jederzeit gesetzt werden, der Loop arbeitet sie beim nächsten Durchlauf ab. Bei 4 Luken ist die Loop schnell genug, dass keine spürbare Verzögerung entsteht.

---

## 8. Button-Konzept (User-Button auf Expansion Board)

### AP-Modus (WLAN-Konfiguration)
- **Trigger:** User-Button ist beim Programmstart bereits **gedrückt gehalten**.
- Gerät startet als WLAN-Access-Point (Captive Portal oder feste IP).
- Konfigurationsseite zur Eingabe von SSID/Passwort.
- Nach Übernahme: Speichern in `wifi.json` → Neustart als WLAN-Client.

### Config-Freigabe (Servo-Justage im Betrieb)
- **Trigger:** User-Button wird im normalen Betrieb gedrückt (beliebig lang/kurz).
- **60-Sekunden-Fenster** startet: Auf der normalen Bedienwebseite erscheint ein zusätzlicher **„Konfiguration"-Button**.
- Wird dieser Button innerhalb von 60 s angewählt → **1-Stunden-Session** startet:
  - Konfigurationsbutton bleibt auf der Bedienwebseite sichtbar.
  - Konfigurationswebseite ist über diesen Button **oder per Direktaufruf der URL** erreichbar.
- Nach Ablauf der 1 Stunde:
  - Konfigurationsbutton verschwindet beim nächsten Seitenaufruf.
  - Direktaufrufe der Konfigurations-URL werden blockiert.
- Wird der Konfigurationsbutton innerhalb der 60 s nicht angewählt → keine Wirkung.

---

## 9. Web-Interface

### Technologie
- **Statisches HTML + CSS + JS** im internen Flash, wird einmalig vom Browser geladen und dort gerendert.
- **REST-API** auf dem ESP für Steuerbefehle (Luken-Sollwert setzen, Config-Parameter speichern).
- **Server-Sent Events (SSE)** für Live-Statusupdates (Lukenpositionen, laufende Bewegung).
- **Bootstrap 5** (CSS-Framework): Responsive Layout, fertige UI-Komponenten, kein eigenes CSS nötig.
- **Alpine.js**: Reaktive UI-Elemente (Status automatisch aktualisieren, Config-Button ein-/ausblenden) direkt im HTML, kein Build-Schritt.
- Alle Bibliotheksdateien (minifiziert) im internen Flash (~65 KB gesamt, einmalig gecacht).

### Seiten
- **Bedienwebseite** (`index.html`): Steuerung der 4 Luken, Statusanzeige, ggf. Config-Button (nach Button-Druck sichtbar).
- **Konfigurationswebseite** (`config.html`): Servoparameter einstellen, Positionen direkt anfahren zur visuellen Justage vor Ort. Detaillierter Aufbau wird separat festgelegt.

### Sicherung der Konfigurationsseite
Die Konfigurationsseite lässt sich nur innerhalb einer aktiven 1h-Session aufrufen (via Button auf Bedienseite oder Direktaufruf). Außerhalb der Session wird sie blockiert. Die Session wird nur durch physischen Tastendruck am Gerät gestartet.

---

## 10. Lokales Bedienfeld (Taster + LEDs)

### Konzept
Direktbedienung am Gerät unabhängig vom Web-Interface:
- **4 Lukentasten** (Luke 1–4 auswählen)
- **6 Positionstasten** (Zielposition 1–6 wählen)
- **6 LEDs** (eine vor jeder Positionstaste)

**Bedienung:**
1. Lukentaste drücken → LEDs zeigen die aktuelle Istposition dieser Luke an.
2. Positionstaste drücken → Luke fährt zur gewählten Position.

### Hardware
**MCP23017** (I²C-GPIO-Expander, Adresse 0x20):
- 16 GPIO, frei als Ein-/Ausgang konfigurierbar
- 10 Eingänge (Taster, interne Pull-ups nutzbar)
- 6 Ausgänge (LEDs mit Vorwiderständen)
- Interrupt-Ausgang verfügbar (→ ESP32 kann auf Tastendruck reagieren ohne Polling)
- Breakout-Board mit Pinheadern, Verdrahtung der Taster und LEDs direkt am Board auf Lochrasterplatine

---

## 11. Display (OLED SSD1306, 128×64 Pixel)

- Eingebauter 8×8-Font: 16 Zeichen × 8 Zeilen → ausreichend für alle Statusinformationen.
- **Inhalt:**
  - Status aller 4 Luken (aktuelle Position / „BEWEGT")
  - WLAN-Status (verbunden / AP-Modus / IP-Adresse)
  - Aktiver Vorgang (z. B. „Luke 2 öffnet…")
  - Hinweis wenn Config-Session aktiv

---

## 12. Async-Architektur (uasyncio-Tasks)

| Task | Funktion |
|---|---|
| `task_webserver` | microdot-Webserver: REST-API + SSE |
| `task_display` | OLED-Anzeige periodisch aktualisieren |
| `task_control_loop` | Soll/Ist-Vergleich, Bewegungsablauf sequenziell |
| `task_button_monitor` | User-Button: Entprellung, Kurzdruck/Startdruck, Session-Timer |
| `task_panel_monitor` | MCP23017: Taster-Events, LED-Aktualisierung |

---

## 13. Fail-Safe / Robustheit

- **ESP32-Watchdog** aktivieren: Hänger im Ablauf → Neustart statt unkontrolliertem Verhalten.
- **Servo-Watchdog**: Servo länger als 5 s unter Last → Relais notfallmäßig abschalten, Fehler auf Display und Webseite.
- **Stromausfall**: Letzter Ist-Zustand in `state.json` → beim Neustart zuerst Ist-Winkel ausgeben, dann Relais zuschalten (kein Ruck).
- **WLAN-Verlust**: Luken verbleiben im aktuellen Zustand, kein selbstständiges Handeln. Lokales Bedienfeld bleibt weiter funktionsfähig.
- **Nur eine Luke gleichzeitig aktiv** (durch Steuerloop garantiert, Netzteil-Schutz).

---

## 14. Event/Trigger-System (Abstraktion für Erweiterungen)

Von Anfang an als saubere Abstraktionsschicht vorsehen:

- **Trigger-Quellen:** Web-UI, lokale Taster (MCP23017), Regensensor (Funk), RTC-Zeitplan
- **Aktionen:** Luke auf Position X fahren
- Neue Trigger-Quellen sollen ohne Umbau der Kernlogik (Steuerloop, Bewegungsablauf) ergänzbar sein.

---

## 15. Geplante Erweiterungen (noch nicht implementieren)

### RTC-Zeitplan
- PCF8563 auf dem Expansion Board bereits vorhanden.
- Automatisches Öffnen/Schließen zu konfigurierten Zeiten.
- Integration über das Event/Trigger-System.

### Regensensor (separates Hardware-Projekt)
Eigenständiges Low-Power-Funksensor-Modul. Konzept:

**Sensorprinzip:**
- Elektrodynamischer **wetterfester Exciter** (z. B. Dayton Audio, hermetisch abgedichtet) auf einer schräg montierten Metallplatte (Wasser läuft ab).
- Exciter wird **umgekehrt als Sensor** betrieben: Regentropfen-Aufprall → Plattenvibration → Exciter erzeugt Spannungspuls (niederohmig, 4–8 Ω Spulenimpedanz, kein Ladungsverstärker nötig).
- Schräge Metallplatte direkt im Regen, keine Glasscheibe als dämpfendes Zwischenmedium.
- Intensitätsstufen aus Pulsrate und Amplitude (z. B. über Zeitfenster von 10 s).

**Elektronik:**
- **TLV3691-Komparator** (75 nA Ruhestrom) als Always-On-Wake-up-Stufe.
- MCU im Deep Sleep (~15 µA), wacht per Komparator-Ausgang auf.
- Nach Aufwachen: ADC-Messung für Intensitätsklassifikation, LoRa-Paket senden, zurück in Deep Sleep.
- Aktiv-Zyklus ~200–500 ms → Batteriebetrieb über Monate bis Jahre realistisch.

**Funk:**
- **LoRa** (z. B. RFM95-Modul) für Punkt-zu-Punkt-Verbindung zum ESP32.
- Empfänger: zweites RFM95 direkt am ESP32 (SPI).
- Keine fremde Infrastruktur nötig, kein WLAN erforderlich (WLAN-Ausfall des Hauptgeräts schadet nicht).
- Kein Kabel zum Dach → keine Blitzschlaggefahr.

**Schnittstelle zum Hauptsystem:**
- Regensensor liefert Intensitätsstufe (z. B. 0 = kein Regen, 1 = Niesel, 2 = Regen, 3 = Starkregen).
- Hauptsystem reagiert über Event/Trigger-System (z. B. ab Stufe 2 alle Luken schließen, bei Stufe 1 nur auf Spalt).

### Lokale Taster (hardwareseitig bereits eingeplant, Software folgt)
MCP23017 und Bedienfeld-Konzept sind im Konzept enthalten (Abschnitt 10). Implementierung erfolgt nach Web-Interface.

---

## 16. Offene Punkte / noch nicht festgelegt

- Detaillierter Aufbau und Layout der Konfigurationswebseite.
- Konkrete Auswahl der MicroPython-Drittanbieter-Bibliotheken (PCA9685, MCP23017) → vor Implementierung prüfen.
- Regensensor-Hardware: Prototyp und Test steht noch aus.
- Typ des Regensensors (Exciter auf Metallplatte) muss in der Praxis verifiziert werden.
- Verhalten bei Niesel: Luken nur auf Spalt oder vollständig schließen? (Konfigurationsparameter)