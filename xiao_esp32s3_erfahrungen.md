# XIAO ESP32-S3 + Seeed Expansion Board – Erfahrungen und Erkenntnisse

Gesammelte Erkenntnisse aus der praktischen Inbetriebnahme. Gültig für:
- **Seeed XIAO ESP32-S3** (nicht Sense-Variante)
- **Seeed Expansion Board Base for XIAO**
- **MicroPython v1.28.0** (Standard) / **v1.29.0-preview** (Debug-Firmware)

---

## 1. Pin-Belegung

### I²C

Die Dokumentation und das Pinout-Bild sind irreführend. Die **tatsächlich funktionierenden** I²C-Pins:

| Signal | GPIO |
|---|---|
| SDA | **5** |
| SCL | **6** |

```python
from machine import I2C, Pin
i2c = I2C(0, sda=Pin(5), scl=Pin(6), freq=400000)
```

**Nicht** GPIO 4/5 wie im Pinout-Bild angegeben. GPIO 4 als SCL führt dazu, dass der Scan scheinbar alle Adressen 8–119 zurückgibt (Bus-Kurzschluss-Pattern). Das ist ein zuverlässiges Diagnosemerkmal für falsche Pins.

### User Button (Expansion Board)

| Signal | GPIO |
|---|---|
| User Button | **2** (aktiv low, Pull-up nötig) |

**Nicht** GPIO 1 wie ursprünglich angenommen. Testen mit:
```python
from machine import Pin
p = Pin(2, Pin.IN, Pin.PULL_UP)
print(p.value())  # 0 = gedrückt, 1 = nicht gedrückt
```

### User LED (XIAO ESP32-S3)

| Signal | GPIO |
|---|---|
| User LED (orange) | **21** (aktiv low) |

```python
from machine import Pin
led = Pin(21, Pin.OUT)
led.value(0)  # an
led.value(1)  # aus
```

### Buzzer (Expansion Board)

| Signal | GPIO |
|---|---|
| Buzzer | **3** (A3) |

Der Buzzer reagiert beim I²C-Scan mit falschen Pins – nützliches Diagnosemerkmal.

### I²C-Geräte auf dem Expansion Board

| Gerät | I²C-Adresse |
|---|---|
| SSD1306 OLED (128×64) | 0x3C |
| RTC PCF8563 | 0x51 |
| Unbekanntes Gerät | 0x6B (noch nicht identifiziert) |

### Alle XIAO ESP32-S3 Pins die auf das Expansion Board gehen

GPIOs: 1, 2, 3, 4, 5, 6, 7, 8, 9, 43, 44

### Reset-Buttons

- **XIAO-Reset-Button**: Resettet den ESP32-S3 direkt (Hardware-RST-Pin)
- **Board-Reset-Button**: Wirkt **nicht** auf den ESP32-S3 – war für den SAMD21 ausgelegt, geht über die Pogo-Pins

---

## 2. Pogo-Pins (Federkontakte unter dem XIAO)

Das Expansion Board hat 4 Federkontakte die Pads auf der Unterseite des XIAO abtasten. Diese waren für den **SAMD21** gedacht (SWD-Debug: SWDCLK, SWDIO, RST).

Beim **ESP32-S3** sind diese Pads anders belegt (JTAG-Pins). **Empfehlung: Federkontakte isolieren** um unbeabsichtigte Verbindungen zu vermeiden. Die SWD-Pins des ESP32-S3 (GPIO38-42) sind für unser Projekt nicht nötig.

Der Board-Reset-Button funktioniert deshalb nicht – er geht über einen dieser Federkontakte.

---

## 3. Blinkende Power-LED

Die schnell blinkende LED auf dem Expansion Board ist **kein Fehler**. Sie bedeutet: kein LiPo-Akku angeschlossen. Vollkommen normal beim USB-Betrieb ohne Akku. Kann ignoriert werden.

---

## 4. MicroPython – Speicherverwaltung

### WiFi muss als erstes initialisiert werden

Der ESP32-S3 WiFi-Stack benötigt einen **großen zusammenhängenden RAM-Block**. Wenn andere Module zuerst geladen werden, fragmentiert der Heap und WiFi schlägt mit `OSError: WiFi Out of Memory` fehl – obwohl `gc.mem_free()` noch 180+ KB anzeigt.

**Lösung:** WiFi **als allererstes** nach `gc.collect()` initialisieren, noch vor allen anderen Imports:

```python
import gc
gc.collect()
import network
network.WLAN(network.STA_IF).active(True)  # Speicherblock früh reservieren
gc.collect()
# jetzt erst weitere Imports
import uasyncio
# ...
```

### Verfügbarer RAM

Nach dem Start von MicroPython v1.28.0 auf dem XIAO ESP32-S3:
- **~188 KB** frei (nach `gc.collect()`)
- MicroPython-Overhead: ca. 150–200 KB

### gc.collect() beim Start

Immer `gc.collect()` als allererstes in `main.py` aufrufen um fragmentierten Heap aus dem Boot-Prozess aufzuräumen. Ohne das kann `main.py` mit "Out of Memory" abstürzen obwohl theoretisch genug RAM frei wäre.

### Diagnose bei Out of Memory

```python
import gc
gc.collect()
print(gc.mem_free())   # freier RAM
print(gc.mem_alloc())  # belegter RAM
```

---

## 5. I²C-Diagnose

### Bus-Kurzschluss-Pattern

Wenn `i2c.scan()` alle Adressen von 8 bis 119 zurückgibt, sind **SDA oder SCL falsch** (z.B. auf GND gezogen). Das ist kein echter Scan-Befund sondern ein Artefakt.

### Korrekte Pin-Suche

Alle Pin-Kombinationen systematisch testen:

```python
from machine import I2C, Pin

for sda in [4, 5, 6, 7]:
    for scl in [4, 5, 6, 7]:
        if sda == scl:
            continue
        try:
            i2c = I2C(0, sda=Pin(sda), scl=Pin(scl), freq=100000)
            result = i2c.scan()
            if len(result) < 50:  # nicht der Kurzschluss-Pattern
                print(f"SDA={sda} SCL={scl}: {result}")
        except:
            pass
```

### I²C-Frequenz bei der Suche

Bei der Pin-Suche `freq=100000` (100 kHz) verwenden – robuster als 400 kHz.

---

## 6. MicroPython REPL – Tipps

### main.py unterbrechen

`main.py` startet automatisch und ist oft zu schnell für manuelles Ctrl-C. Lösung: `main.py` umbenennen damit sie nicht automatisch startet:

```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main.py', '/main_bak.py')"
```

Zurückbenennen:
```bash
mpremote connect /dev/ttyACM0 exec "import os; os.rename('/main_bak.py', '/main.py')"
```

### Letzte Exception lesen

`sys.last_exception` existiert in MicroPython v1.28 nicht. Stattdessen `main.py` manuell importieren:

```python
import main
```

### Verzeichnis rekursiv löschen

```python
import os

def rm_rf(path):
    for e in os.listdir(path):
        f = path + '/' + e
        try:
            os.listdir(f)
            rm_rf(f)
            os.rmdir(f)
        except:
            os.remove(f)

rm_rf('/verzeichnis')
os.rmdir('/verzeichnis')
```

---

## 7. mpremote – Upload

### Verzeichnisse kopieren

Bei `cp -r` kein Ziel angeben außer `:` (Gerätewurzel):

```bash
mpremote connect /dev/ttyUSB0 cp -r source/app :
```

**Nicht** `:/app` – das führt zu Fehlern.

### Alle Uploads in einer Session

Verbindungsaufbau kostet Zeit. Alle Befehle mit `+` verketten:

```bash
mpremote connect /dev/ttyACM0 \
    cp source/boot.py :boot.py + \
    cp source/main.py :main.py + \
    cp -r source/app : + \
    cp -r source/shared :
```

### Source-Verzeichnis als Basis

`cp -r source/app :` kopiert `source/app/` als `/source/app/` auf das Gerät – falsch! Immer zuerst in das Source-Verzeichnis wechseln:

```bash
cd source
mpremote connect /dev/ttyACM0 cp -r app :
cd ..
```

### Einzelne Datei schnell ersetzen

```bash
mpremote connect /dev/ttyACM0 cp source/app/hw_config.py :app/hw_config.py
```

Kein kompletter Upload nötig für einzelne Änderungen.

### mpremote und Dual-CDC

Bei Dual-CDC-Firmware (zwei USB-Ports) macht `mpremote cp` intern einen Soft-Reset um Raw-REPL zu betreten. Das kann den zweiten CDC-Port destabilisieren und zu `OSError: [Errno 5] Input/output error` führen. In diesem Fall Datei-Uploads über das MicroPython Studio Plugin oder direkt im REPL durchführen.

---

## 8. Kompatibilität Expansion Board / ESP32-S3

Das Expansion Board wurde primär für **SAMD21, RP2040 und nRF52840** entwickelt. Der ESP32-S3 ist **nicht offiziell gelistet**, funktioniert aber weitgehend – mit folgenden Einschränkungen:

- I²C-Pins weichen vom erwarteten Pinout ab (s. Abschnitt 1)
- User Button auf anderem GPIO als dokumentiert
- Board-Reset-Button funktioniert nicht (Pogo-Pin-Problem)
- Blinkende LED ist normal (kein Akku)

Alle I²C-Peripherie auf dem Board (OLED, RTC) funktioniert einwandfrei mit den korrekten Pins.

---

## 9. MicroPython Studio (VSCode Plugin) – Inbetriebnahme

### Plugin-Version

**niwantha33.micropython-studio v2.3.0**. Faktisch keine offizielle Dokumentation vorhanden.

### Refresh Device Files

Der **"Refresh Device Files"** Button in der Statusleiste ist in v2.3.0 nicht sichtbar. Stattdessen:

**Ctrl+Shift+P → "MicroPython Studio: MicroPython: Refresh Device Files"**

Das ist der einzige Weg der zuverlässig funktioniert. Der Tree-Refresh-Button im Device Files Panel (Pfeil-Symbol) ist etwas anderes – er aktualisiert nur die Anzeige des bereits bekannten Ports, führt aber keine neue Port-Erkennung durch.

### Port-Erkennung

Das Plugin verwendet intern `mpremote connect list` für die Port-Erkennung. **Symlinks** (z.B. `/dev/ttyXIAO` via udev) werden dabei nicht erkannt – nur echte Gerätepfade wie `/dev/ttyACM0`.

Port-Zuordnung beim XIAO ESP32-S3 mit Standard-Firmware:
- `/dev/ttyACM0` = REPL-Port (der einzige)

Nach USB-Reconnect kann sich die Nummer ändern. Das Plugin erkennt das Gerät über die `deviceId` (VID:PID = `303a:4001`) und aktualisiert `device.cfg` automatisch.

### device.cfg

Liegt im Projektstamm. Relevante Felder:

```ini
[device]
port = /dev/ttyACM0
deviceId = 303a:4001
device_firmware = Micropython

[filePath]
deviceCodeDir = "/home/user/git/projekt/source"
virtualEnv = "/home/user/.micropython-studio/.venv"
```

`device_firmware` muss exakt `Micropython` lauten (nicht `MicroPython`) – Groß-/Kleinschreibung beachten.

### Lock-Dateien

Das Plugin verwendet Lock-Dateien in `/tmp/` um Port-Konflikte zu vermeiden:

```
/tmp/mps_lock__dev_ttyACM0.lock
```

Nach Abstürzen oder hartem USB-Disconnect bleiben diese Dateien stehen und blockieren alle weiteren Verbindungsversuche. Lösung:

```bash
rm /tmp/mps_lock__dev_ttyACM*.lock
```

### Shell / Device Terminal

Der **Shell**-Button öffnet ein interaktives REPL-Terminal über den seriellen Port. Nach dem Öffnen erscheint `>>> Press Enter if prompt is not visible` – Enter drücken falls kein Prompt erscheint.

**Wichtig:** Die Shell hält den Port über einen Daemon-Prozess (Lock-Datei). Solange die Shell offen ist, können andere Plugin-Funktionen (Package Install, Upload) den Port nicht nutzen → Shell schließen bevor man andere Aktionen startet.

### Upload

Dateien hochladen: Im lokalen Explorer (nicht Device Files Panel) Rechtsklick auf Datei → **"Upload to Device"**. Drag & Drop in das Device Files Panel ist nicht implementiert.

### Run / Debugging

**Run-Target** in der Statusleiste muss auf **MCU** stehen.

Beim Run-Button kann `"Device aborted raw paste"` auftreten – besonders nach einem Reset. Das Plugin hat eine Auto-Retry-Logik, aber bei Dual-CDC-Firmware ist das häufiger. Alternativ im Shell-Terminal direkt ausführen:

```python
exec(open('main.py').read())
```

### WebREPL

Der **WebREPL**-Button in der Statusleiste wirft `openWebReplTerminal is not defined` – bekannter Bug in v2.3.0. Für USB/Serial-Arbeit nicht relevant, einfach ignorieren.

---

## 10. MicroPython Studio Debugger – Inbetriebnahme

### Voraussetzungen

Der Debugger benötigt eine **spezielle Debug-Firmware** mit eingebautem nativem `dbg`-Modul. Die Standard-MicroPython-Firmware reicht nicht.

### Debug-Firmware für ESP32-S3

Download von: `https://github.com/niwantha33/micropython_live_dbg_firmware`

Drei Dateien nötig (ESP32S3-Ordner):
- `bootloader.bin`
- `partition-table.bin`
- `firmware.bin`

Flash-Befehl (alle drei Teile mit korrekten Adressen):

```bash
# Flash löschen
python -m esptool --chip esp32s3 -p /dev/ttyACM0 erase_flash

# Flashen
python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash \
    0x0 bootloader.bin \
    0x8000 partition-table.bin \
    0x10000 firmware.bin
```

**Nicht** nur `firmware.bin` alleine flashen – das führt zu `Invalid image block, can't boot`.

### Port-Verhalten der Debug-Firmware

Die Debug-Firmware (v1.29.0-preview) öffnet nach vollständigem Boot **zwei CDC-Ports**:
- **ACM0** = REPL-Port (nach Ctrl+B aus Raw-REPL)
- **ACM1** = Debug-CDC-Port

**Besonderheit:** ACM0 startet im Raw-REPL-Modus. Erst nach **Ctrl+C dann Ctrl+B** erscheint der normale `>>>` Prompt. Das Plugin (Shell-Button) kommt damit nicht automatisch zurecht – manuell Ctrl+C + Ctrl+B nötig.

### Debugger-Workflow (Schritt für Schritt)

1. **Shell schließen** (falls offen)
2. **Start Debug → "Upload debugger files"** – lädt `boot.py`, `dbgref.py`, `trace_pump.py` auf das Gerät
3. Wenn gefragt: Debug-CDC-Port eingeben (z.B. `/dev/ttyACM1`)
4. **Package Install**: `usb-device-cdc` installieren (nötig für zweiten CDC-Port)
   - Ctrl+Shift+P → Package Install → `usb-device-cdc`
   - Shell muss dabei geschlossen sein (Lock-Konflikt)
5. **Shell öffnen**, Ctrl+D (Soft Reset) – `boot.py` aktiviert zweiten CDC-Port
6. Warten bis `[boot] second CDC registered as dbgref.cdc` erscheint
7. Im Shell-Terminal eingeben:
   ```python
   import trace_pump; trace_pump.start()
   ```
8. **Start Debug → "Connect only"** → Debug-CDC-Port eingeben (`/dev/ttyACM1` oder je nach aktueller Nummerierung)
9. Breakpoints in `.py`-Dateien setzen (nur innerhalb von `def`-Funktionen, nicht auf Modul-Ebene)
10. Im Debugger-Panel **Continue** klicken

### Bekannte Probleme Debug-Firmware

- Nach Soft-Reset nummeriert Linux die ACM-Ports neu → Port-Nummern in `device.cfg` und beim Debugger-Connect müssen ggf. angepasst werden
- `mpremote cp` (Datei-Upload) schlägt mit Errno 5 fehl wenn die Debug-Firmware läuft, weil der interne Soft-Reset den zweiten CDC-Port destabilisiert
- Lock-Dateien bleiben nach Abstürzen stehen → `rm /tmp/mps_lock__dev_ttyACM*.lock`
- Der `dbg`-Fehler `ImportError: no module named 'main'` erscheint wenn `main.py` noch nicht auf dem Gerät liegt

### Prüfen ob dbg-Modul vorhanden

```python
import dbg
print(dir(dbg))
# Erwartet: ['locals', 'step', 'call_stack', 'clear_bp', 'set_bp', ...]
```

---

## 11. udev-Regel für festen Gerätenamen

**Hinweis:** Nützlich für manuelle Terminal-Zugriffe, aber **nicht** für MicroPython Studio geeignet, da das Plugin Symlinks nicht über `mpremote connect list` erkennt und den Port dann nicht in `device.cfg` aktualisieren kann.

Für manuelle Nutzung:

```bash
sudo nano /etc/udev/rules.d/99-xiao-esp32s3.rules
```

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="4001", SYMLINK+="ttyXIAO"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Rückgängig machen: Datei löschen, dann `sudo udevadm control --reload-rules && sudo udevadm trigger`.

---

## 12. Servo-Einschaltverhalten (digitale Hochmoment-Servos)

### Problem: Power-On-Zentrierfahrt

Digitale Hochmoment-Servos (getestet: Miuzei 45 kg/cm) führen beim Einschalten eine Initialisierungsroutine durch: Der interne Controller fährt mit Maximalgeschwindigkeit kurz in Richtung Mittelstellung, bevor er das externe PWM-Signal annimmt. Das tritt auch dann auf, wenn das PWM-Signal bereits vor dem Einschalten anliegt – der Controller ignoriert es während des Bootvorgangs.

Der baugleiche 25 kg/cm-Servo desselben Herstellers zeigt dieses Verhalten nicht (vermutlich analoger Controller oder kürzere Boot-Routine).

### Ursache

Digitale Servos verwenden intern einen Mikrocontroller. Dieser bootet beim Einschalten, führt eine eigene Initialisierung durch und liest das externe PWM erst danach aus. Je nach Firmware-Implementierung fährt der Servo dabei kurz die Mittelstellung an.

### Workaround in `task_control.py`

Die Bewegungssequenz nutzt drei Modul-Parameter um das Rucken zu minimieren:

1. **`_PRESET_DELAY_MS`**: Luken-Kanal bleibt beim Relay-Ein auf 0µs (kein gültiges Signal). Der Controller bootet ohne gültiges PWM. Nach `_PRESET_DELAY_MS` ms wird das erste gültige Signal gesetzt – der Controller hat dann seine Initialisierung abgeschlossen und nimmt die Position direkt an.
2. **`_WIGGLE_CYCLES` / `_WIGGLE_US`**: Direkt nach dem ersten gültigen Signal alterniert der Servo ±`_WIGGLE_US` um die Zielposition. Das bricht eine noch laufende Zentrierfahrt ab.

Ergebnis: Das Rucken ist auf ein sehr kurzes, drehmomentschwaches Zucken reduziert. Unter Betriebslast voraussichtlich vernachlässigbar.

### Verhalten im Fail-Safe

Im Fail-Safe-Ablauf ruckt der Servo **nicht**, weil der Luken-Kanal dort grundsätzlich auf 0µs bleibt. Kein gültiges PWM → kein Motor-Aktivierung beim Booten.

### Servos mit dokumentiertem Soft-Start

**Savox „Plus"-Serie** bewirbt „Soft Start" als Feature – der Servo fährt beim Einschalten sanft in die vom PWM vorgegebene Position statt zur Mitte. Bei Servo-Neubeschaffung für diese Anwendung sollte „Soft Start" als Auswahlkriterium gelten.
