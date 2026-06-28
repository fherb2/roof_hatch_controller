# Erarbeitetes Wissen: MicroPython, ESP32-S3, XIAO-Varianten & MicroPython Studio

## Inhaltsverzeichnis

1. [Einführung und Überblick](#1-einführung-und-überblick)
2. [Hardware: XIAO ESP32-S3 Varianten](#2-hardware-xiao-esp32-s3-varianten)
3. [Installationsreihenfolge und Build-Prozess](#3-installationsreihenfolge-und-build-prozess)
4. [MicroPython – Spezialwissen](#4-micropython--spezialwissen)
5. [MicroPython Studio – Spezialwissen](#5-micropython-studio--spezialwissen)
6. [MicroPython Studio Debugger – Spezialwissen](#6-micropython-studio-debugger--spezialwissen)

---

## 1. Einführung und Überblick

### Kontext und Ziel

Der **XIAO ESP32-S3** verbindet sich über seinen nativen USB-OTG-Port mit dem PC
(VID:PID `303a:4001`, erscheint als `/dev/ttyACM0` oder `/dev/ttyACM1` unter Linux,
je nach vorherigem Portzustand). Die Standard-MicroPython-Firmware für dieses
Board enthält kein natives `dbg`-Modul, das der Hardware-Debugger des VSCode-Plugins
**MicroPython Studio** benötigt. Deshalb muss eine spezielle Debug-Firmware
geflasht werden.

### Zwei Firmware-Varianten im Einsatz

| Firmware | Version | Zweck |
|---|---|---|
| **Standard-MicroPython** | v1.28.0 | Normaler Betrieb, ein REPL-Port (`/dev/ttyACM0`) |
| **Debug-Firmware** | v1.29.0-preview (niwantha33) | Debugger-Betrieb, enthält natives `dbg`-C-Modul, öffnet zwei CDC-Ports |

### Benötigte Komponenten und ihr Zweck

| Komponente | Version | Zweck |
|---|---|---|
| **ESP-IDF** | v5.5.2 | Espressifs Build-Framework: Compiler, Linker, Toolchain, C-Bibliotheken (FreeRTOS, TinyUSB, WiFi-Stack). Einmalig installiert, dauerhaft nutzbar – auch für reine C/C++-ESP32-Projekte ohne MicroPython. |
| **MicroPython Quellcode** | v1.28.0 | Die MicroPython-Firmware. Wird mit dem IDF zu einem `.bin`-Image gebaut und auf das Board geflasht. Für den Debugger wird stattdessen die vorgefertigte Debug-Firmware verwendet. |
| **Custom Board-Config** | – | Kleine Konfigurationsdatei, die compile-time Features aktiviert. Aktiviert `MICROPY_PY_USB=1` für die selbstgebaute Standard-Firmware (für `usb.device`-Unterstützung). |
| **`usb-device-cdc` Paket** | latest | Python-seitiger High-Level-Wrapper für USB-CDC. Wird per MicroPython Studio Package Install auf das Board installiert. Wird von der Debugger-`boot.py` benötigt. |
| **Debug-Firmware** | v1.29.0-preview | Vorgefertigte Firmware mit nativem `dbg`-C-Modul von niwantha33. Muss separat geflasht werden. Ersetzt die selbstgebaute Standard-Firmware während des Debuggens. |
| **MicroPython Studio** | v2.3.0 | VSCode-Plugin für MicroPython-Entwicklung mit Datei-Manager, Shell/REPL und Hardware-Debugger. |

### Das Dual-CDC-Prinzip (Grundlage des Debuggers)

MicroPython Studio implementiert einen Hardware-Debugger über einen **zweiten
USB-CDC-Kanal**: Der erste Kanal ist der normale REPL. Der zweite ist ein
dedizierter Debug-Kanal, über den Trace-Events (Zeilensprünge, Breakpoint-Hits,
Variablenwerte) zwischen Board und VSCode fließen.

**Wichtig:** Dies erfordert die spezielle Debug-Firmware mit eingebautem `dbg`-Modul
– die selbstgebaute Standard-Firmware mit `MICROPY_PY_USB=1` reicht **nicht** aus.
Das `dbg`-Modul ist ein nativer C-Bestandteil der Debug-Firmware und kann nicht
durch Python-Pakete nachgerüstet werden.

---

## 2. Hardware: XIAO ESP32-S3 Varianten

### Übersicht der drei Varianten

Alle drei Boards teilen denselben XIAO-Formfaktor (21 × 17,8 mm), dieselben
11 Standard-Durchsteck-GPIO-Pins und sind damit mit dem XIAO Base Board
kompatibel. Die Unterseite muss beim Einsatz im Base Board abgeklebt werden.

| Merkmal | ESP32-S3 (Standard) | ESP32-S3 Sense | ESP32-S3 Plus |
|---|---|---|---|
| **Flash** | 8 MB | 8 MB | **16 MB** |
| **PSRAM** | keins | 8 MB (Quad) | **8 MB (Quad)** |
| **GPIO (Durchsteck)** | 11 | 11 (+2 via B2B) | 11 |
| **GPIO (gesamt)** | 11 | 13 | **20** (9 zusätzl. Castellierungen) |
| **Kamera** | nein | OV3660 (abnehmbar) | nein |
| **Mikrofon** | nein | ja (PDM) | nein |
| **SD-Card-Slot** | nein | ja (bis 32 GB FAT) | nein |
| **B2B-Connector** | Kamera-Board | Kamera-Board | Wio-SX1262 (**nicht** Kamera-Board!) |
| **Preis (ca.)** | günstigster | mittel | ~8 USD |

### Entscheidungshinweise

- **Kein Kamerabedarf → Plus**: Deutlich mehr Flash (16 MB) und PSRAM (8 MB)
  gegenüber dem Standard, mehr GPIOs, kein unnötiger Kamera-/Mikrofon-Overhead.
- **Kamerabedarf → Sense**: OV3660-Kameraboard ist abnehmbar, SD-Slot nützlich
  für Datenspeicherung.
- **Standard**: Nur sinnvoll bei kleinstem Budget oder wenn Flash/PSRAM nicht
  gebraucht werden.

### Wichtige Inkompatibilität

Der B2B-Connector des **Plus** ist **nicht** kompatibel mit dem
Kamera-Erweiterungsboard des Sense. Wer also später eine Kamera nachrüsten
möchte, muss beim Sense bleiben.

### PSRAM-Hinweis für spätere Firmware-Builds

Der Plus hat **Quad-PSRAM** (nicht Octal). Beim Firmware-Build muss dann die
`sdkconfig.spiram`-Konfiguration eingebunden werden (nicht `sdkconfig.spiram_oct`).
Das erfordert eine eigene Board-Config (z. B. `XIAO_ESP32S3_PLUS_USB`).

---

## 3. Installationsreihenfolge und Build-Prozess

Dieser Abschnitt beschreibt den Bau der **selbstgebauten Standard-Firmware** mit
`MICROPY_PY_USB=1`. Für den Debugger wird stattdessen die vorgefertigte
Debug-Firmware verwendet (siehe Abschnitt 6). Beide Wege schließen sich nicht aus –
man kann die Standard-Firmware für normale Entwicklung und die Debug-Firmware
für Debugging-Sessions nutzen.

### Voraussetzungen (Linux/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y git wget flex bison gperf python3 python3-pip \
    python3-venv cmake ninja-build ccache libffi-dev libssl-dev \
    dfu-util libusb-1.0-0
```

Diese Pakete sind Systemvoraussetzungen für den IDF-Build-Prozess.

---

### Schritt 1: ESP-IDF installieren

**Warum dieser Schritt?**
Das ESP-IDF liefert den Cross-Compiler (xtensa-esp32s3-elf-gcc), die
ESP32-spezifischen C-Bibliotheken und das `idf.py`-Build-Tool. Ohne das IDF
kann MicroPython für den ESP32 nicht gebaut werden. Das IDF bleibt dauerhaft
auf dem Rechner und kann auch für reine C/C++-Projekte genutzt werden.

Die Versionsbindung ist wichtig: MicroPython v1.28.0 erfordert **ESP-IDF v5.5.2**.
Andere IDF-Versionen werden von dieser MicroPython-Version nicht unterstützt
(auch nicht neuere).

```bash
mkdir -p ~/esp
cd ~/esp
git clone --depth 1 --branch v5.5.2 \
    https://github.com/espressif/esp-idf.git esp-idf
cd esp-idf
git submodule update --init --recursive
```

Das `--depth 1` beim Klonen spart erheblich Platz und Zeit (keine vollständige
Git-History nötig).

Dann die Toolchain installieren (einmaliger Vorgang, lädt mehrere GB):

```bash
./install.sh esp32s3
```

Das lädt Compiler, Debugger und alle Tools speziell für den ESP32-S3 herunter.

**IDF-Environment aktivieren** (in jeder neuen Terminal-Session nötig):

```bash
source ~/esp/esp-idf/export.sh
```

Praktischer Alias in `~/.bashrc` (deaktiviert ein eventuell aktives venv vorher):

```bash
alias idf='type deactivate &>/dev/null && deactivate; . ~/esp/esp-idf/export.sh'
```

Danach reicht ein einfaches `idf` im Terminal. Das `type deactivate`-Konstrukt
verhindert einen Fehler, wenn kein venv aktiv ist (`deactivate` existiert dann
als Funktion nicht).

**Verifizieren:**

```bash
idf
idf.py --version
# Erwartet: ESP-IDF v5.5.2
```

---

### Schritt 2: MicroPython klonen

**Warum dieser Schritt?**
Wir brauchen den Quellcode, um eine eigene Board-Konfiguration hinzufügen und
die Firmware mit aktiviertem `MICROPY_PY_USB` bauen zu können.

MicroPython v1.28.0 ist die aktuell empfohlene stabile Version (Stand Juni 2026)
und auf dem Board ursprünglich vorinstalliert gewesen.

```bash
cd ~/esp
git clone --depth 1 --branch v1.28.0 \
    https://github.com/micropython/micropython.git micropython
cd micropython
git submodule update --init --depth 1
```

---

### Schritt 3: MicroPython Cross-Compiler bauen

**Warum dieser Schritt?**
Der `mpy-cross`-Compiler wird benötigt, um `.py`-Dateien in `.mpy`-Bytecode
zu übersetzen (u. a. für frozen modules in der Firmware). Er muss einmal
gebaut werden, bevor der eigentliche Firmware-Build starten kann.

```bash
cd ~/esp/micropython
make -C mpy-cross
```

Läuft in unter einer Minute durch.

---

### Schritt 4: Custom Board-Config anlegen

**Warum dieser Schritt?**
Die Standard-Konfiguration `ESP32_GENERIC_S3` hat `MICROPY_PY_USB` nicht
aktiviert, weil das native USB-Device-Modul nicht für alle Boards relevant ist.
Wir kopieren die Konfiguration und aktivieren den Schalter.

```bash
cd ~/esp/micropython/ports/esp32/boards
cp -r ESP32_GENERIC_S3 XIAO_ESP32S3_USB
echo '#define MICROPY_PY_USB (1)' >> XIAO_ESP32S3_USB/mpconfigboard.h
```

Die resultierende `mpconfigboard.h` sieht dann so aus:

```c
#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Generic ESP32S3 module"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"
#define MICROPY_HW_ENABLE_UART_REPL         (1)
#define MICROPY_HW_I2C0_SCL                 (9)
#define MICROPY_HW_I2C0_SDA                 (8)
#define MICROPY_PY_USB                      (1)
```

**Für einen späteren Plus-Build** würde eine separate Config
`XIAO_ESP32S3_PLUS_USB` angelegt, die zusätzlich die PSRAM-sdkconfig einbindet.

---

### Schritt 5: Firmware bauen

**Warum dieser Schritt?**
Erst jetzt wird die eigentliche Firmware als `.bin`-Image erzeugt, das auf
das Board geflasht werden kann.

```bash
cd ~/esp/micropython/ports/esp32
idf.py -D MICROPY_BOARD=XIAO_ESP32S3_USB \
        -B build-XIAO_ESP32S3_USB \
        build
```

Der erste Build dauert 5–15 Minuten (IDF kompiliert sehr viel C-Code).
Folge-Builds sind deutlich schneller, da nur geänderte Teile neu gebaut werden.

Erfolgreiche Build-Ausgabe endet mit:

```bash
Generated .../build-XIAO_ESP32S3_USB/micropython.bin
micropython.bin binary size 0x19cee0 bytes. Smallest app partition is
0x1f0000 bytes. 0x53120 bytes (17%) free.
Project build complete.
```

---

### Schritt 6: Firmware flashen

**Warum Flash löschen?**
Ein vollständiges Löschen stellt sicher, dass keine Reste alter Firmware oder
alter Dateisysteme die neue Firmware stören.

```bash
# IDF-Environment aktivieren (falls nötig)
idf

cd ~/esp/micropython/ports/esp32

# Flash löschen
idf.py -p /dev/ttyACM0 -B build-XIAO_ESP32S3_USB erase-flash

# Neue Firmware flashen
idf.py -p /dev/ttyACM0 -B build-XIAO_ESP32S3_USB flash
```

---

### Schritt 7: Verifizieren und usb-device Paket installieren

Das `usb-device`-Paket ist die Python-Seite des USB-Device-Stacks. Es liegt in
micropython-lib und wird per `mip` direkt auf das Board installiert:

```bash
# Im normalen Python-venv (OHNE IDF):
pip install mpremote

# Prüfen ob machine.USBDevice verfügbar ist:
python -m mpremote connect /dev/ttyACM0 exec \
    "import machine; print(hasattr(machine, 'USBDevice'))"
# Erwartet: True

# usb-device Paket installieren:
python -m mpremote connect /dev/ttyACM0 mip install usb-device
```

Das Paket installiert sich nach `/lib/usb/device/` auf dem Board:
- `/lib/usb/device/__init__.mpy`
- `/lib/usb/device/core.mpy`

**Wichtig:** Das Paket wird bei einem Neu-Flash der Firmware gelöscht und
muss danach erneut installiert werden.

---

## 4. MicroPython – Spezialwissen

### Zwei Schichten des USB-Device-Stacks

MicroPython trennt USB-Device-Funktionalität in zwei Schichten:

- **`machine.USBDevice`** (C-Ebene): Muss zur Compile-Zeit mit
  `MICROPY_PY_USB=1` in die Firmware eincompiliert werden. Stellt die
  Low-Level-API bereit. Vorhanden in der selbstgebauten Standard-Firmware
  und in der Debug-Firmware.
- **`usb.device` / `usb.device.cdc`** (Python-Ebene): High-Level-Wrapper
  aus micropython-lib. Wird per `mip` oder MicroPython Studio Package Install
  zur Laufzeit installiert. Stellt komfortablere Klassen wie `CDCInterface` bereit.

Beide Schichten müssen vorhanden sein, damit `boot.py` des Debuggers den
zweiten CDC-Port öffnen kann.

**Zusätzlich** benötigt der Debugger das native **`dbg`-Modul** (C-Ebene), das
ausschließlich in der vorgefertigten Debug-Firmware von niwantha33 enthalten ist.
Es stellt Funktionen bereit wie `set_bp`, `step`, `locals`, `call_stack` etc.
und kann nicht durch Python-Pakete nachgerüstet werden.

### IDF-Version und MicroPython-Version sind fest gekoppelt

Jede MicroPython-Version erfordert eine spezifische IDF-Version. Diese
Bindung ist strikt – auch neuere IDF-Versionen funktionieren nicht.

| MicroPython | ESP-IDF |
|---|---|
| v1.28.0 | v5.5.2 |
| v1.27.0 | v5.5.1 |
| v1.26.x | v5.4.2 |

Beim Wechsel auf eine neue MicroPython-Version muss ggf. auch das IDF
gewechselt werden (neuer `git checkout` + `install.sh`).

### Python-Version prüfen im REPL

```python
import sys
print(sys.version)
# Beispiel: 3.4.0; MicroPython v1.28.0 on 2026-04-06
```

### mpremote vs. IDF-Environment

`mpremote` ist das normale Werkzeug für die Kommunikation mit dem Board
(Dateien hochladen, REPL, Skripte ausführen). Es läuft im normalen Python-venv
und braucht das IDF-Environment **nicht**:

```bash
pip install mpremote
python -m mpremote connect /dev/ttyACM0 repl
```

Das IDF-Environment (`idf`) wird nur für Firmware-Builds benötigt.

### mpremote und Dual-CDC

Bei aktiver Dual-CDC-Konfiguration (Debug-Firmware mit laufendem `trace_pump`)
macht `mpremote cp` intern einen Soft-Reset um Raw-REPL zu betreten. Das kann
den zweiten CDC-Port destabilisieren und zu `OSError: [Errno 5] Input/output error`
führen. In diesem Fall Datei-Uploads über MicroPython Studio oder direkt im REPL
durchführen.

---

## 5. MicroPython Studio (VSCode-Plugin) – Spezialwissen

### Plugin-Identifikation

- Extension-ID: `niwantha33.micropython-studio`
- Version: 2.3.0 (Stand Juni 2026)
- Keine offizielle Dokumentation vorhanden
- Quellcode liegt als `micropython_studio_source.txt` im Projekt
  (analysiert zur Erarbeitung dieses Wissens)

### Konfigurationsdatei `device.cfg`

Das Plugin legt beim Anlegen eines Projekts eine `device.cfg` im Projektstamm an:

```ini
[device]
port = /dev/ttyACM0
mcu = esp32s3
deviceId = 303a:4001
device_firmware = Micropython

[filePath]
deviceCodeDir = "/home/user/git/projekt/source"
virtualEnv = "/home/user/.micropython-studio/.venv"
```

Die `deviceId` `303a:4001` ist der Espressif-VID:PID für den nativen
USB-OTG-Port des ESP32-S3. `device_firmware` muss exakt `Micropython` lauten
(nicht `MicroPython`).

### Verbindung herstellen

Der **"Refresh Device Files"**-Button ist in v2.3.0 nicht in der Statusleiste
sichtbar. Zuverlässiger Weg:

**Ctrl+Shift+P → "MicroPython Studio: MicroPython: Refresh Device Files"**

Der Tree-Refresh-Button im Device Files Panel (Pfeil-Symbol) ist etwas anderes –
er aktualisiert nur die Dateiansicht des bereits bekannten Ports.

### Port-Erkennung

Das Plugin verwendet intern `mpremote connect list` für die Port-Erkennung.
Symlinks (z.B. `/dev/ttyXIAO` via udev) werden dabei **nicht** erkannt.
Das Plugin gleicht Geräte über `deviceId` (VID:PID) ab und aktualisiert
`device.cfg` automatisch wenn sich die Port-Nummer ändert.

### Lock-Dateien

Das Plugin verwendet Lock-Dateien in `/tmp/` um Port-Konflikte zu vermeiden:

```
/tmp/mps_lock__dev_ttyACM0.lock
/tmp/mps_lock__dev_ttyACM1.lock
```

Nach Abstürzen oder hartem USB-Disconnect bleiben diese stehen und blockieren
alle weiteren Verbindungsversuche. Lösung:

```bash
rm /tmp/mps_lock__dev_ttyACM*.lock
```

### Shell / Device Terminal

Der Shell-Button öffnet ein interaktives REPL-Terminal. Die Shell hält den Port
über einen Daemon-Prozess (Lock-Datei). Solange die Shell offen ist, können
Package Install und andere Upload-Operationen den Port nicht nutzen.
**Shell schließen bevor andere Aktionen gestartet werden.**

### Upload

Dateien hochladen: Im lokalen Explorer Rechtsklick auf Datei →
**"Upload to Device"**. Drag & Drop in das Device Files Panel ist nicht
implementiert.

### Run / Ausführen

Run-Target in der Statusleiste muss auf **MCU** stehen. Bei
`"Device aborted raw paste"` im Shell-Terminal kann alternativ direkt ausgeführt
werden:

```python
exec(open('main.py').read())
```

### WebREPL

Der WebREPL-Button wirft `openWebReplTerminal is not defined` – bekannter Bug
in v2.3.0. Für USB/Serial-Arbeit nicht relevant.

### boot.py

Das Plugin benötigt `boot.py` **nicht** für die normale Funktion. Es
überschreibt `boot.py` nur beim "Upload debugger files"-Schritt mit seiner
eigenen Debugger-Version. Die ursprünglich leere/auskommentierte `boot.py`
kann für normale Entwicklung beibehalten werden.

---

## 6. MicroPython Studio Debugger – Spezialwissen

### Voraussetzungen

Der Debugger benötigt zwingend:
1. **Debug-Firmware** mit nativem `dbg`-C-Modul (nicht die selbstgebaute Standard-Firmware)
2. **`usb-device-cdc`** Python-Paket auf dem Board
3. **`trace_pump`** läuft auf dem Board (manuell gestartet nach jedem Reset)

### Debug-Firmware beziehen und flashen

Repository: `https://github.com/niwantha33/micropython_live_dbg_firmware`

Ordner `ESP32S3/` enthält drei Dateien. **Alle drei müssen geflasht werden**,
nur `firmware.bin` reicht nicht (führt zu `Invalid image block, can't boot`):

```bash
# Downloads
cd /tmp
curl -L "https://raw.githubusercontent.com/niwantha33/micropython_live_dbg_firmware/main/ESP32S3/bootloader.bin" -o bootloader.bin
curl -L "https://raw.githubusercontent.com/niwantha33/micropython_live_dbg_firmware/main/ESP32S3/partition-table.bin" -o partition-table.bin
curl -L "https://raw.githubusercontent.com/niwantha33/micropython_live_dbg_firmware/main/ESP32S3/firmware.bin" -o firmware_debug.bin

# Flash löschen
python -m esptool --chip esp32s3 -p /dev/ttyACM0 erase_flash

# Alle drei Teile mit korrekten Adressen flashen
python -m esptool --chip esp32s3 -p /dev/ttyACM0 write_flash \
    0x0    bootloader.bin \
    0x8000 partition-table.bin \
    0x10000 firmware_debug.bin
```

Alternativ über MicroPython Studio: **Flash → ESP32/ESP8266 → Flash Debug Firmware → ESP32-S3** (lädt und flasht automatisch, wenn verfügbar).

### Port-Verhalten der Debug-Firmware

Die Debug-Firmware (v1.29.0-preview) hat ein besonderes Startverhalten:

- **Beim ersten Boot**: Nur ein Port sichtbar (REPL), zunächst im Raw-REPL-Modus
- **ACM0 startet im Raw-REPL**: Erst nach **Ctrl+C dann Ctrl+B** erscheint `>>>`
- **Nach boot.py mit `usb-device-cdc`**: Zwei Ports sichtbar

Port-Zuordnung nach vollständigem Setup:
| Port | Funktion |
|---|---|
| ACM0 (oder ACM1 je nach Bootreihenfolge) | REPL |
| ACM1 (oder ACM2) | Debug-CDC |

**Die Port-Nummern können sich nach jedem USB-Reconnect oder Soft-Reset ändern.**
Nach einem Soft-Reset (Ctrl+D) nummeriert Linux die Ports neu. `device.cfg` muss
ggf. manuell angepasst werden.

### Debugger-Workflow (Schritt für Schritt)

**Einmalige Einrichtung nach dem Flashen der Debug-Firmware:**

1. Shell in VSCode schließen (falls offen)
2. **Start Debug → "Upload debugger files"** – lädt `boot.py`, `dbgref.py`, `trace_pump.py` auf das Board (überschreibt vorhandenes `boot.py`!)
3. **Package Install: `usb-device-cdc`** installieren
   - Ctrl+Shift+P → Package Install → `usb-device-cdc`
   - Shell muss dabei geschlossen sein
4. Shell öffnen, **Ctrl+D** (Soft Reset)
5. Warten bis `[boot] second CDC registered as dbgref.cdc` erscheint
6. Im Shell-Terminal:
   ```python
   import trace_pump; trace_pump.start()
   # Ausgabe: trace_pump: started
   ```
7. Prüfen welcher Port der Debug-CDC ist:
   ```bash
   ls /dev/ttyACM*
   # Erwartet: zwei Ports, z.B. ACM0 und ACM1
   ```

**Debugger verbinden (nach jedem Reset wiederholen ab Schritt 4):**

8. **Start Debug → "Connect only"**
9. Debug-CDC-Port eingeben (vollständiger Pfad, z.B. `/dev/ttyACM1`)
10. Debugger-Panel öffnet sich mit Buttons: Continue, Step Over, Step In, Step Out, Locals, Globals, Call Stack

**Breakpoints setzen:**

- Im Editor auf Zeilennummer klicken (roter Punkt)
- **Nur innerhalb von `def`-Funktionen** möglich – nicht auf Modul-Ebene
- Im Debugger-Panel **Continue** klicken um Ausführung zu starten

### Was die Plugin-`boot.py` tut

```python
# Vereinfacht
import time, sys

def _enable_dual_cdc():
    import usb.device
    from usb.device.cdc import CDCInterface
    dbg_cdc = CDCInterface()
    dbg_cdc.init(timeout=0)
    usb.device.get().init(dbg_cdc, builtin_driver=True)
    import dbgref
    dbgref.cdc = dbg_cdc

print("[boot] dual-CDC enabling in 3s — Ctrl-C to skip")
time.sleep(3)  # Sicherheitsfenster
_enable_dual_cdc()
```

Es gibt ein 3-Sekunden-Fenster zum Abbrechen (Ctrl-C), damit das Board
nicht ohne REPL landet. Bei Fehler (`ImportError: no module named 'usb'`) läuft
das Board normal weiter – `usb-device-cdc` muss dann noch installiert werden.

### Verfügbare `dbg`-Funktionen

```python
import dbg
print(dir(dbg))
# ['locals', 'step', 'step_in', 'step_out', 'call_stack', 'clear_bp',
#  'frame_info', 'func_first_ip', 'is_paused', 'line_to_ip', 'list_bp',
#  'lost_count', 'mute', 'paused_info', 'read_trace', 'reset_lost',
#  'resume', 'set_bp', 'set_pump_fun', 'source_line', 'target_info',
#  'trace_func', 'trace_off', 'trace_on', 'unmute']
```

### Bekannte Probleme

| Problem | Ursache | Lösung |
|---|---|---|
| `ImportError: no module named 'dbg'` | Standard-Firmware statt Debug-Firmware | Debug-Firmware flashen |
| `ImportError: no module named 'usb'` | `usb-device-cdc` nicht installiert | Package Install: `usb-device-cdc` |
| `ImportError: no module named 'main'` | `main.py` nicht auf Gerät | `main.py` hochladen |
| `[Errno 5] Input/output error` | Port nach Soft-Reset neu nummeriert | Port-Nummer prüfen, `device.cfg` anpassen, Lock löschen |
| Lock-Konflikt beim Upload | Shell-Daemon hält Port | Shell-Terminal schließen |
| Debugger-Panel zeigt `ERR open ttyACM2: No such file or directory` | Unvollständiger Pfad eingegeben | Vollständigen Pfad angeben: `/dev/ttyACM2` |
| `mpremote cp` schlägt mit Errno 5 fehl | Dual-CDC-Instabilität beim Soft-Reset | Upload über Studio oder direkt im REPL |

### Offene Punkte (noch zu klären)

- Genauer stabiler Ablauf für wiederholte Debug-Sessions ohne vollständigen Neustart
- Verhalten bei asyncio-Tasks im Debugger (Task-Map-Funktion vorhanden aber noch nicht getestet)
- Ob `main.py` mit `ImportError` beim Breakpoint-Setzen trotzdem pausiert werden kann
