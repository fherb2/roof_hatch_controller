# Konzept: Config-Redesign 2 – Pro-Kanal-Konfiguration, Halteservo-Rampe, Montagewinkel, Servotester, i18n

Referenz-Quellcode-Stand zu Beginn dieser Änderung: siehe vorheriges Konzept (`konzept_grad_umstellung.md`) + alle seither im Chat besprochenen Einzeländerungen (Sofort-Persistenz-Fix, größere Displayschrift, Spinner, Invertierung bei Config-Session).

## 1. Übersicht der Änderungen

1. Alle Servo-Parameter (Stellbereich, Richtung, Geschwindigkeit, Impulszeiten, Hard-Stops, PWM-Frequenz) werden von "global, für alle 4 Kanäle" zurück auf "pro Kanal" umgestellt – hebt die Global-Entscheidung aus dem letzten Umbau auf.
2. Umbenennung in der UI: `Lukenservo` → **`Hebelservo`**, `Bremsenservo` → **`Halteservo`**. Betrifft **nur sichtbare Texte** in `config.html`; interne Bezeichner (`hatch_servo`/`brake_servo` in `config.json`, `hatch_deg`/`brake_deg` in `state.json`, alle Python-Module) bleiben unverändert, um den Umfang der Änderung klein zu halten.
3. Halteservo bekommt eine eigene Geschwindigkeit und rampt jetzt genau wie der Hebelservo, statt in einem Schritt zu springen. Dadurch werden zwei feste Wartezeiten durch echtes "Rampe fertig" ersetzt.
4. Neue Stellbereichs-Option `0…360°` (zusätzlich zu 180/270); die zunächst angedachte Option `-90…+90°` entfällt wieder (Start bleibt immer 0°).
5. Neues Feld **Montagewinkel** pro Servo (Hebel und Halte), zum Anfahren einer Wartungsposition außerhalb der normalen Rastpositionen.
6. Neue Sektion **Servotester** (5. Ausklappfeld) für die beiden nächsten freien PWM-Kanäle, dauerbestromt, ohne Relais.
7. Download-Button für die aktuelle Konfiguration (rein client-seitig).
8. Komplett neues Seitenlayout: kein gemeinsamer "alle 4 Luken"-Bereich mehr, jeder Kanal einzeln auf-/zuklappbar, 2-spaltig (Hebelservo | Halteservo).
9. Mehrsprachigkeit der Web-UI-Texte (i18n), zunächst Deutsch/Englisch. Fehlermeldungen und Boot-Anzeigen bleiben Englisch.

## 2. PWM-Frequenz

Der PCA9685 hat **ein einziges `PRE_SCALE`-Register für den gesamten Chip** – keine Kanal-individuelle Frequenz möglich (Hardware-Limit, siehe `pca9685.py`). Lösung, wie besprochen:

- **Eine Frequenz pro Hatch-Kanal** (`hatches[i].pwm_freq_hz`), gemeinsam für Hebel- und Halteservo dieses Kanals (die beiden sind während der gesamten Bewegungssequenz gleichzeitig bestromt, können also ohnehin nie unterschiedliche Frequenzen gleichzeitig haben).
- **Eine weitere, gemeinsame Frequenz für beide Servotester-Ausgänge** (`global.servo_tester.pwm_freq_hz`).
- `task_control` programmiert die Frequenz unmittelbar vor jedem Relais-Ein auf den Wert des jeweiligen Kanals um (`hw.pwm.freq(...)`, siehe `pca9685.py`). Nach Relais-Aus wird die Frequenz nicht zurückgesetzt – das passiert erst, wenn als nächstes tatsächlich der Servotester (oder ein anderer Kanal) etwas ausgibt. Ein Modul-Cache (`_current_pwm_freq`) verhindert unnötiges Neuprogrammieren, wenn sich die Frequenz nicht ändert.
- **Bewusst keine Absicherung** dagegen, dass ein laufender Kanalbetrieb die Servotester-Frequenz durcheinanderbringt: Der Servotester ist nur für ausgebaute, unbelastete Servos gedacht – falsches Verhalten während dieser Zeit ist unkritisch (siehe 9.1, Doku-Hinweis).

## 3. Neues `config.json`-Schema

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
            "short_name": "Luke1",
            "pwm_freq_hz": 50,
            "hatch_servo": {
                "range_deg": 270,
                "direction": "neg",
                "speed_deg_per_s": 30,
                "pulse_min_us": 500,
                "pulse_max_us": 2500,
                "hard_stop_min_deg": 0,
                "hard_stop_max_deg": 270,
                "mount_angle_deg": 0
            },
            "brake_servo": {
                "range_deg": 180,
                "direction": "neg",
                "speed_deg_per_s": 30,
                "pulse_min_us": 500,
                "pulse_max_us": 2500,
                "hard_stop_min_deg": 0,
                "hard_stop_max_deg": 180,
                "mount_angle_deg": 90,
                "open_deg": 10,
                "close_deg": 170
            },
            "positions": [{"hatch_deg": 0}, {"hatch_deg": 90}, {"hatch_deg": 260}]
        }
    ]
}
```

**Wertebereiche (Validierung in `state.py`):**

| Feld | Bereich |
|---|---|
| `range_deg` | {180, 270, 360} |
| `direction` | {"pos", "neg"}, Default `"neg"` |
| `speed_deg_per_s` | [30, 180] |
| `pulse_min_us`/`pulse_max_us` | [500, 4000], `min < max` |
| `pwm_freq_hz` | [50, 400] |
| `hard_stop_min_deg`/`hard_stop_max_deg` | [0, range_deg], `min < max` |
| `mount_angle_deg` | [hard_stop_min_deg, hard_stop_max_deg] – wie eine reguläre Zielposition behandelt |
| `open_deg`/`close_deg` (nur `brake_servo`) | [hard_stop_min_deg, hard_stop_max_deg] |
| `test_angle_a_deg`/`test_angle_b_deg` (nur Tester) | [hard_stop_min_deg, hard_stop_max_deg] |
| `positions[].hatch_deg` (nur `hatch_servo`, min. 2 Einträge) | [hard_stop_min_deg, hard_stop_max_deg] |

`positions[0]` = ZU, `positions[-1]` = AUF, unverändert wie bisher (Einfügen/Entfernen von Zwischenpositionen wie zuvor konzipiert).

**Umrechnungsformel unverändert** (`shared/servo_units.py`, `deg_to_us`/`us_to_deg`) – da Start immer 0° bleibt, ist keine Erweiterung auf Winkel-Paare nötig, nur `range_deg` bekommt einen dritten zulässigen Wert (360).

## 4. Neues `state.json`-Schema

```json
{
    "hatches": [
        {"hatch_deg": 0, "brake_deg": 170, "target_deg": 0}
    ],
    "servo_tester": {"hatch_deg": 0, "brake_deg": 0}
}
```

- `hatches[i].hatch_deg`/`brake_deg`/`target_deg` – Feldnamen und Semantik unverändert (`FAILSAFE_DEG`-Sentinel weiterhin nur bei `hatch_deg`/`target_deg`, nie bei `brake_deg`).
- `servo_tester.hatch_deg`/`brake_deg`: letzte bekannte Ist-Winkel der beiden Testerkanäle, damit Rampen nach einem Neustart wieder vom richtigen Punkt starten. Kein Soll-Wert nötig, da der Tester nie "automatisch" fährt.

## 5. Neue Bewegungssequenz (`app/task_control.py`)

Da der Halteservo jetzt auch rampt, wissen wir, wann er fertig ist – zwei feste Wartezeiten entfallen komplett:

```
step_mark_failsafe        (unverändert, nur hatch_deg betroffen)
step_preset_servos:        Hebelservo auf 0µs (kein Signal), dann nach Preset-Delay+Wiggle
                            auf aktuelle Ist-Position; Halteservo direkt auf seine aktuelle
                            Ist-Position (brake_deg) - NICHT auf open_deg, damit kein Ruck beim
                            Relais-Ein entsteht
step_set_pwm_freq:         PCA9685-Frequenz auf hatches[i].pwm_freq_hz setzen (nur falls
                            abweichend vom zuletzt gesetzten Wert)
step_relay_on
step_preset_delay          (_PRESET_DELAY_MS, unverändert)
step_wiggle                (unverändert, nur Hebelservo)
  → warte _RELAY_ON_TO_TORQUE_MS = 200ms   (Hebelservo hat Haltekraft übernommen)
step_ramp_brake_open:      Halteservo rampt von Ist-Position zu open_deg (eigene
                            Geschwindigkeit/Schrittgröße, siehe unten) - keine Pause danach
step_ramp_hatch:           Hebelservo rampt zur Zielposition (eigene Geschwindigkeit/
                            Schrittgröße) - startet unmittelbar nach Ende der Bremsen-Rampe
  → warte _HATCH_SETTLE_TO_BRAKE_CLOSE_MS = 200ms  (Ausregeln am Ziel)
step_ramp_brake_close:     Halteservo rampt von open_deg zu close_deg
step_relay_off:            unmittelbar nach Ende dieser Rampe (finally-Block, keine weitere
                            feste Wartezeit mehr nötig)
step_update_state:         hatch_deg, brake_deg, target_deg aktualisieren + save_state()
```

**Entfallen:** `_BRAKE_OPEN_TO_HATCH_MOVE_MS`, `_BRAKE_CLOSE_TO_RELAY_OFF_MS` (die Rampenzeit selbst übernimmt jetzt diese Funktion). **Neu:** `_RELAY_ON_TO_TORQUE_MS = 200`. **Bleibt:** `_HATCH_SETTLE_TO_BRAKE_CLOSE_MS = 200` (unverändert), `_PRESET_DELAY_MS`, `_WIGGLE_US`, `_WIGGLE_CYCLES` (alle unverändert, nur Hebelservo betreffend).

**Gemeinsame Rampen-Hilfsfunktion** (statt Code-Duplikation für Hebel- und Halteservo):

```python
def _compute_step(servo_cfg):
    """Wie bisher _compute_step, jetzt generisch für jeden Servo-Typ
    (hatch_servo oder brake_servo config dict) statt nur für den Hebelservo."""
    ...  # unverändert übernommen aus dem letzten Umbau, nur Parameter umbenannt

async def _ramp(hw, channel, current_us, target_us, step_us, step_ms):
    """Rampt einen einzelnen PCA9685-Kanal von current_us nach target_us.
    Wird für Hebel- UND Halteservo verwendet (vorher: nur Hebelservo,
    Halteservo sprang direkt)."""
    while current_us != target_us:
        if current_us < target_us:
            current_us = min(current_us + step_us, target_us)
        else:
            current_us = max(current_us - step_us, target_us)
        hw.pwm.set_us(channel, current_us)
        await uasyncio.sleep_ms(step_ms)
    return current_us
```

## 6. Neue Bewegungsfunktionen für Montagewinkel & Servotester

**Montagewinkel Hebelservo:** Kein neuer Code nötig – Anfahren-Button ruft den bestehenden `/api/hatch/<i>/test`-Endpunkt mit dem aktuell übernommenen `mount_angle_deg`-Wert auf (Client kennt den Wert bereits aus dem Formular). Läuft durch die volle `_move_hatch`-Sequenz aus Abschnitt 5, genau wie eine reguläre Position – der einzige Unterschied ist, dass der Wert nicht aus der `positions`-Liste stammt und nicht im Auswahlmenü der Bedienoberfläche auftaucht.

**Montagewinkel Halteservo – neue Funktion `_move_brake_only`:** Bewegt nur den Halteservo direkt, ohne Choreografie:

```
step_preset_servos:  Hebelservo auf seine aktuelle Ist-Position (halten, nicht bewegen),
                     Halteservo auf seine aktuelle Ist-Position
step_set_pwm_freq
step_relay_on
step_preset_delay + step_wiggle (nur Hebelservo, unverändert - Hebelservo bleibt aber
                     stehen, Wiggle dient nur der Ruck-Vermeidung beim Einschalten)
step_ramp_brake:     Halteservo rampt zur Zielposition (eigene Geschwindigkeit)
step_relay_off:      sofort nach Rampenende
step_update_state:   nur brake_deg aktualisieren, target_deg/hatch_deg unverändert
```

**Servotester – zwei neue Funktionen, kein Relais:**

```python
async def _move_tester_servo(hw, servo_cfg, current_deg, target_deg) -> new_deg:
    """Rampt einen der beiden Testerkanäle (fest bestromt, kein Relais).
    Setzt vorher die Tester-PWM-Frequenz (global.servo_tester.pwm_freq_hz),
    falls abweichend vom zuletzt gesetzten Wert."""
```

Wird für Kanal 8 (Hebelservo-Ausgang des Testers) und Kanal 9 (Halteservo-Ausgang) verwendet.

## 7. Erweiterte `targets`-Struktur

```python
targets = {
    "hatches":    [{"hatch_deg": N}, ...],        # unverändert, löst volle Sequenz aus
    "brake_only": [None, None, None, None],        # pro Kanal: gesetzter Wert löst
                                                    # _move_brake_only aus, wird danach
                                                    # wieder auf None zurückgesetzt
    "tester": {"hatch_deg": None, "brake_deg": None},  # gesetzter Wert löst
                                                        # _move_tester_servo aus
}
```

**Wichtiger Architekturgrund:** Alle I²C-Schreibzugriffe auf den PCA9685 bleiben ausschließlich in `task_control` gebündelt (ein einziger Task greift auf den I²C-Bus zu), auch für Montagewinkel-Halteservo und Servotester. Die Web-Handler setzen nur Werte in `targets`, genau wie bisher bei den regulären Positionen – kein direkter Hardwarezugriff aus `task_webserver.py` heraus. `task_control`'s Hauptschleife bekommt dafür zwei zusätzliche Prüfungen pro Durchlauf (auf `brake_only[i]` und `tester`).

## 8. Web-API – neue/geänderte Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/api/hatch/<i>/brake_test` | `{brake_deg}` → setzt `targets.brake_only[i]`, löst `_move_brake_only` aus |
| POST | `/api/tester/hatch` | `{angle_deg}` → setzt `targets.tester.hatch_deg` |
| POST | `/api/tester/brake` | `{angle_deg}` → setzt `targets.tester.brake_deg` |

`/api/hatch/<i>/test` (bestehend) bleibt unverändert und wird auch für den Hebelservo-Montagewinkel wiederverwendet. `POST /api/config` bleibt strukturell unverändert (validiert + persistiert sofort + aktualisiert `_config` in RAM), nur das Dokument, das validiert wird, hat das neue Schema aus Abschnitt 3.

## 9. Web-Seite `config.html` – neues Layout

### 9.1 Aufbau

- **Download-Button** ganz oben (Navbar oder direkt darunter): baut aus dem aktuell geladenen/gespeicherten `buildConfig()`-Dokument einen `Blob`, triggert Download via `<a download="config.json">` + `URL.createObjectURL` – **kein neuer Server-Endpunkt**.
- Kein gemeinsamer "alle 4 Luken"-Bereich mehr. Jeder der 4 Kanäle ist ein eigenständiges Accordion-Item, Titel = `name` (wie bisher schon für die Kanal-Accordions, nur ohne die vorgeschaltete globale Sektion).
- **5. Accordion-Item: "Servotester"**, mit Hinweistext: *"Nur für ausgebaute, unbelastete Servos. PWM-Frequenz ist unabhängig von den 4 Kanälen und wird nur aktiv, solange kein Kanal fährt."*

### 9.2 Kanal-Karte (pro Luke), Reihenfolge von oben nach unten

1. **Name** (Textfeld, linksbündig, keine 2. Spalte)
2. **Kurzname** (Textfeld, max. 11 Zeichen, linksbündig, keine 2. Spalte)
3. **PWM-Frequenz** (ein Feld, volle Breite, Beschriftung z. B. "PWM-Frequenz (Hebel- und Halteservo)")
4. **Zwei Spalten**, durch dünne vertikale Linie getrennt (`border-left` per CSS), Spaltenüberschrift dick/größer als Feld-Labels, linksbündig:
   - Links: **"Hebelservo, Kanal N"**
   - Rechts: **"Halteservo, Kanal N"**

   Innerhalb jeder Spalte, bis zu 3 Felder pro Zeile:
   - **Zeile 1:** Stellbereich (180°/270°/360°, Auswahl), Drehrichtung (pos/neg, Default neg), Geschwindigkeit (°/s, 30–180)
   - **Zeile 2:** Impulszeit bei 0° (500–4000µs), Impulszeit bei `range_deg`° (Label passt sich dynamisch an: "Impulszeit 180°"/"270°"/"360°" – nur das Label, die Werte selbst bleiben beim Wechsel unverändert)
   - **Zeile 3 (Hard-Stops):** Hard-Stop min (°), Hard-Stop max (°)
   - **Zeile 4 (Montagewinkel):** Winkel-Eingabefeld + ✓ + **spürbarer Abstand** + ▶-Anfahren-Button (verhindert Fehlklicks). Links (Hebelservo): löst volle Sequenz aus (`/api/hatch/<i>/test`). Rechts (Halteservo): löst `_move_brake_only` aus (`/api/hatch/<i>/brake_test`), bewegt nur diesen Servo.
5. **Positionszeilen** (wie bisher: ZU/P2…/AUF, Einfügen/Entfernen vor AUF) – **nur in der linken Spalte** (Hebelservo). Rechte Spalte bleibt in diesen Zeilen leer, um das 2-Spalten-Raster beizubehalten.

### 9.3 Servotester-Karte

Gleicher 2-Spalten-Aufbau (Hebelservo | Halteservo), Zeilen 1–2 identisch zur Kanal-Karte (Stellbereich/Richtung/Geschwindigkeit, Impulszeiten – **keine** eigene PWM-Frequenz pro Spalte, stattdessen eine gemeinsame Frequenz-Zeile über beiden Spalten wie bei den Kanälen). Danach, **pro Spalte**, zwei Testwinkel-Zeilen statt Positionsliste:

- **Zeile 3:** Testwinkel A + ✓ + Abstand + ▶ (`/api/tester/hatch` bzw. `/api/tester/brake`)
- **Zeile 4:** Testwinkel B + ✓ + Abstand + ▶ (gleicher Endpunkt, anderer Wert)

Damit lässt sich jeder der beiden Testerkanäle beliebig zwischen zwei frei wählbaren Winkeln hin- und herfahren.

### 9.4 Feldzustandsmodell, Sofort-Persistenz

Unverändert aus dem letzten Umbau: `{committed, draft}`, ✓ persistiert sofort per `POST /api/config` mit dem kompletten aktuellen Dokument, ↺ verwirft den Entwurf. Kein Sammel-Speichern-Button (weiterhin nicht vorhanden).

## 10. Mehrsprachigkeit (i18n)

Nur Web-UI-Texte (Labels, Buttons, Legenden, Spaltenüberschriften, Hinweistexte). Fehlermeldungen vom Gerät (`validate_config()`) und die Konsolen-/Boot-Ausgaben bleiben Englisch, unabhängig von der UI-Sprache.

**Struktur:** Ein `I18N`-Objekt in `config.html`, ein Eintrag pro Sprachcode (`de`, `en`), darin ein flaches Dictionary aller Textschlüssel:

```js
const I18N = {
  de: {
    hatch_servo: 'Hebelservo', brake_servo: 'Halteservo',
    range_deg: 'Stellbereich', direction: 'Drehrichtung',
    speed: 'Geschwindigkeit (°/s)', pulse_at: (deg) => `Impulszeit ${deg}°`,
    hard_stop_min: 'Hard-Stop min (°)', hard_stop_max: 'Hard-Stop max (°)',
    mount_angle: 'Montagewinkel (°)', download_config: 'Konfiguration herunterladen',
    servo_tester: 'Servotester', ...
  },
  en: {
    hatch_servo: 'Lever servo', brake_servo: 'Latch servo',
    range_deg: 'Range', direction: 'Direction',
    speed: 'Speed (°/s)', pulse_at: (deg) => `Pulse width at ${deg}°`,
    ...
  },
};
```

Zugriff im Template über eine kleine Helper-Funktion `t(key, ...args)` in `configApp()`, die auf `I18N[this.lang][key]` zeigt. Sprachwahl per kleinem Dropdown in der Navbar, Default aus `navigator.language`, keine Persistierung nötig (reiner UI-Zustand, muss nicht auf dem Gerät gespeichert werden).

**Aufwand:** mechanisch, aber durchgängig – jedes Label/jeder Button-Text im gesamten Formular muss durch `t(...)`-Aufrufe ersetzt werden. Da wir das Formular ohnehin komplett neu schreiben, ist das der günstigste Zeitpunkt dafür.

## 11. Betroffene Dateien – Gesamtübersicht

| Datei | Änderungsumfang |
|---|---|
| `app/hw_config.py` | `_RELAY_ON_TO_TORQUE_MS` statt zweier alter Konstanten; `PWM_FREQ_MIN/MAX_HZ` (50/400); `PULSE_MIN/MAX_US` erweitert auf 500/4000 |
| `app/state.py` | `validate_config`/`load_state`/`create_default_state` komplett neu (neues Schema aus Abschnitt 3+4) |
| `source/config.json` + `.template` | neues Schema, inkl. `global.servo_tester` |
| `app/task_control.py` | `_move_hatch` neu (Abschnitt 5), `_move_brake_only` neu, `_move_tester_servo` neu, gemeinsame `_ramp()`/`_compute_step()`, PWM-Frequenz-Umschaltung, erweiterte `targets`-Struktur |
| `app/task_webserver.py` | neue Routen (Abschnitt 8), `_build_status` liefert zusätzlich Tester-Zustand für eventuelle spätere Anzeige (optional, siehe 12) |
| `source/main.py` | `targets`-Aufbau erweitert um `brake_only`/`tester` – **keine** Feldnamen-Umbenennung (Entscheidung 12.1) |
| `app/task_display.py` | keine Änderung nötig – Feldnamen (`hatch_deg`/`brake_deg`) bleiben wie bisher |
| `webpages/config.html` | komplett neu (Abschnitt 9+10) |
| `webpages/index.html` | keine Änderung erwartet (nutzt nur `pos_idx`/`moving`/`failsafe`, keine Feldnamen-Referenz) |
| `implementierungsdoku.md` | nach Umsetzung aktualisieren, inkl. neuem Doku-Hinweis zu Servotester-PWM-Frequenz (Abschnitt 2) und Montagewinkel-Warnhinweis (Last vorher trennen) |

## 12. Bestätigte Entscheidungen (final, keine offenen Punkte mehr)

1. **Umbenennung nur in der UI** – interne Feldnamen (`hatch_servo`/`brake_servo`, `hatch_deg`/`brake_deg`) bleiben in `state.json`, `config.json`, allen Python-Modulen und der Web-API unverändert. Nur sichtbare Texte in `config.html` heißen "Hebelservo"/"Halteservo" (bzw. "Lever servo"/"Latch servo" auf Englisch).
2. **SSE-Payload** bekommt keine Servotester-Felder (der Tester braucht keinen Live-Status wie "moving") – `config.html` liest die Tester-Werte direkt aus `GET /api/config`.
3. **`_move_brake_only` und `_move_tester_servo`** nutzen dieselben `_PRESET_DELAY_MS`/Wiggle-Mechanismen wie der Hebelservo. Bei `_move_brake_only` wird das Hebelservo-Signal gehalten (nicht gewiggelt), nur der Preset-Delay (Servo-Controller-Boot) gilt für beide Kanäle gemeinsam, da beide am selben Relais hängen.

## 13. Nächste Schritte

Nach deiner Bestätigung der Punkte in Abschnitt 12 gehe ich in dieser Reihenfolge vor:
1. `app/hw_config.py`, `app/state.py`, `source/config.json` + `.template`
2. `app/task_control.py`
3. `app/task_webserver.py`, `source/main.py`, `app/task_display.py`
4. `webpages/config.html` (inkl. i18n)
5. `implementierungsdoku.md` aktualisieren
