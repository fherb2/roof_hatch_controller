# Designkonzept – Dachlukensteuerung

## 1. Hardware-Übersicht

### Hauptkomponenten
| Komponente | Funktion |
|---|---|
| Seeed XIAO ESP32-S3 | Mikrocontroller, WLAN, uasyncio |
| Seeed Expansion Board | I²C-Hub, OLED-Display, SD-Slot, User-Button |
| Grove 4-Channel SPDT Relay | Spannungsversorgung der Servos schalten |
| Grove 16-Channel PWM Driver (PCA9685) | PWM-Signale für alle Servos |

### I²C-Busteilnehmer (alle auf einem Bus)
| Gerät | Adresse |
|---|---|
| OLED SSD1306 | 0x3C |
| PCA9685 PWM-Driver | 0x40 |
| Grove 4-Ch. Relay | 0x11 (oder 0x12, je nach Konfiguration) |
| RTC PCF8563 (auf Expansion Board) | 0x51 |

### Elektrische Verdrahtung
- Pro Dachluke **ein Relais**: schaltet die gemeinsame +8V-Versorgungsader beider Servos (Luke + Bremse) einer Dachluke.
- GND ist für alle Servos durchgehend gemeinsam (über Klemmleiste).
- Jeder Servo hat eine **eigene PWM-Leitung** vom PCA9685.
- Die 8V-Servo-Versorgung vom 3A-Netzteil ist ausreichend für **eine Luke gleichzeitig** → softwareseitig sicherstellen.

### PCA9685-Kanalzuordnung
| PCA9685-Kanal | Funktion |
|---|---|
| 0 | Luke 1 – Dachluke-Servo |
| 1 | Luke 1 – Bremsen-Servo |
| 2 | Luke 2 – Dachluke-Servo |
| 3 | Luke 2 – Bremsen-Servo |
| 4 | Luke 3 – Dachluke-Servo |
| 5 | Luke 3 – Bremsen-Servo |
| 6 | Luke 4 – Dachluke-Servo |
| 7 | Luke 4 – Bremsen-Servo |

### Relay-Zuordnung
| Relay-Kanal | Funktion |
|---|---|
| 0 | +8V für Servos Luke 1 |
| 1 | +8V für Servos Luke 2 |
| 2 | +8V für Servos Luke 3 |
| 3 | +8V für Servos Luke 4 |

---

## 2. Plattform

**MicroPython** auf dem XIAO ESP32-S3.

**Begründung:**
- `uasyncio` ermöglicht kooperatives Multitasking (Webserver, Display, Steuerloop, Button-Monitor) ohne echtes Threading.
- `microdot` als async-fähiger Webserver.
- Eingebaute JSON-Unterstützung (`ujson`) für Konfiguration und Zustandsspeicherung.
- Stabile Bibliotheken für PCA9685, SSD1306.
- Python-Code gut geeignet für die Ablauflogik (Zustandsmaschine, Sequenzierung).

---

## 3. Datenhaltung (interner Flash)

Alle Daten liegen im internen Flash des ESP32-S3. Keine SD-Karte notwendig.

| Datei | Inhalt |
|---|---|
| `wifi.json` | SSID, Passwort für WLAN-Client |
| `config.json` | Servoparameter aller 4 Luken (s. Abschnitt 4) |
| `state.json` | Letzter bekannter Ist-Winkel aller Servos (Positionspersistenz über Stromausfall hinweg) |

---

## 4. Konfigurationsparameter (config.json)

Pro Dachluke:
- Winkel Luke-Auf, Luke-Zu (in Grad oder PWM-Ticks)
- Winkel Bremse-Auf, Bremse-Zu
- Bewegungsgeschwindigkeit: Schrittgröße (Grad/Tick pro Schritt) + Schrittintervall (ms)
- Zeitversatz: Wartezeit nach Bremse-Auf bevor Luke fährt (ms)
- Zeitversatz: Wartezeit nach Erreichen der Zielposition bevor Bremse schließt (ms)

---

## 5. Bewegungsablauf einer Dachluke

1. PCA9685 gibt letzten bekannten Ist-Winkel beider Servos aus (aus `state.json`).
2. Relais zuschalten → Servos erhalten Spannung, halten Position.
3. Bremsen-Servo fährt auf Bremse-Auf-Winkel (gegen Federkraft).
4. Warten (konfigurierter Zeitversatz).
5. Dachluke-Servo fährt schrittweise zur Zielposition.
6. Warten (konfigurierter Zeitversatz).
7. Bremsen-Servo fährt auf Bremse-Zu-Winkel → Bremse schnappt ein (Feder).
8. Kurz warten (Einschnappzeit sicherstellen).
9. Relais abschalten → Servos stromlos.
10. Ist-Winkel in `state.json` aktualisieren.

---

## 6. Steuerloop-Prinzip

- Jede Dachluke hat intern einen **Sollwert** und einen **Istwert** (Position in Grad/Ticks).
- Ein zentraler async-Task durchläuft kontinuierlich alle 4 Luken.
- Gibt es bei einer Luke einen Unterschied zwischen Soll- und Istwert, führt der Loop den Bewegungsablauf (s. Abschnitt 5) vollständig für diese Luke durch.
- Erst danach wird zur nächsten Luke weitergegangen.
- Dadurch ist immer nur **eine Luke gleichzeitig** aktiv → Netzteil-Anforderung ist automatisch erfüllt.
- Keine explizite Warteschlange notwendig: Soll-Werte können jederzeit gesetzt werden; der Loop arbeitet sie beim nächsten Durchlauf ab.

---

## 7. Button-Konzept

### AP-Modus (WLAN-Konfiguration)
- **Bedingung:** User-Button ist beim Programmstart bereits **gedrückt gehalten**.
- Gerät startet als WLAN-Access-Point.
- Konfigurationsseite zum Eingeben von SSID/Passwort (Captive Portal oder feste IP).
- Nach Übernahme der Daten → Speichern in `wifi.json` → Neustart als WLAN-Client.

### Config-Freigabe (Servo-Justage)
- **Bedingung:** User-Button wird **im normalen Betrieb** (beliebig lang/kurz) gedrückt.
- 60-Sekunden-Fenster startet: Auf der normalen Bedienwebseite erscheint ein zusätzlicher **"Konfiguration"-Button**.
- Wird dieser Button innerhalb von 60 s angewählt, startet eine **1-Stunden-Session**:
  - Der Konfigurationsbutton bleibt auf der Bedienwebseite sichtbar.
  - Die Konfigurationswebseite ist über diesen Button **oder per Direktaufruf der URL** erreichbar.
- Nach Ablauf der 1 Stunde:
  - Der Konfigurationsbutton verschwindet beim nächsten Seitenaufruf der Bedienwebseite.
  - Direktaufrufe der Konfigurations-URL werden blockiert.
- Wird der Konfigurationsbutton innerhalb der 60 s **nicht** angewählt, passiert nichts weiter.

---

## 8. Async-Architektur (uasyncio-Tasks)

| Task | Funktion |
|---|---|
| `task_webserver` | microdot-Webserver: Bedien- und Konfigurations-API |
| `task_display` | OLED-Anzeige periodisch aktualisieren |
| `task_control_loop` | Soll/Ist-Vergleich, Bewegungsablauf sequenziell |
| `task_button_monitor` | Button-Entprellung, Kurz-/Startdruck-Erkennung, Session-Timer |

---

## 9. Display-Inhalt

- Status aller 4 Luken (Position / Bewegung läuft)
- WLAN-Status (verbunden / AP-Modus / IP-Adresse)
- Aktiver Vorgang (z. B. „Luke 2 öffnet…")
- Hinweis, wenn Config-Session aktiv ist

---

## 10. Webseite – Aufbau (noch nicht detailliert festgelegt)

- Responsive (Handy, Tablet, Desktop).
- Normale Bedienwebseite: Steuerung der 4 Luken, Status-Anzeige, ggf. Config-Button (s. Abschnitt 7).
- Konfigurationswebseite: Servoparameter justieren, Positionen direkt anfahren zur visuellen Justage. Detaillierter Aufbau wird separat festgelegt.
- Technologie (Framework, ob SSE/Polling etc.) noch offen.

---

## 11. Vorkehrungen für spätere Erweiterungen

- **Event/Trigger-System** als Abstraktionsschicht vorsehen:
  - Trigger-Quellen: Web-UI, Taster (Hardware), Regensensor, RTC-Zeitplan
  - Aktionen: Luke öffnen, Luke schließen
  - Neue Quellen sollen ohne Umbau der Kernlogik ergänzbar sein.
- **RTC PCF8563** ist auf dem Expansion Board bereits vorhanden → Zeitplan-Automatik später nachrüstbar.
- **Regensensor**: Grove-Port freihalten; Typ noch offen; Integration über Trigger-System.

---

## 12. Fail-Safe / Robustheit

- **ESP32-Watchdog** aktivieren: Hänger im Ablauf führt zu Neustart, nicht zu unkontrolliertem Verhalten.
- **Stromausfall:** Letzter Ist-Zustand in `state.json` → beim Neustart werden zuerst die Ist-Winkel ausgegeben, bevor Relais zuschalten (kein Ruck).
- **WLAN-Verlust im Betrieb:** Luken verbleiben im aktuellen Zustand, kein selbstständiges Handeln.
- **Nur eine Luke gleichzeitig aktiv** (softwareseitig durch Steuerloop garantiert).
