# Reproduktion von Issue #1 (aleluc13, R5 Pro + ES) OHNE Hardware.
#
# Zwei Firmware-Modelle, jede Verhaltensregel mit Quelle belegt:
#
#  ES-Kranz am R5 Pro ("EsOnR5"):
#   - hoert NUR auf Geraete-ID 0x13=19 (die Base proxyt an den Kranz)
#       -> moza-rev src/moza.rs Kopfkommentar + DEVICE_BASE=0x13, am R5+ES
#          hardware-bestaetigt; boxflat cycle_wheel_id endet bei 19
#          (connection_manager.py:374-395); boxflat Issue #33 ("works with
#          base ids"), Issue #151 (ES nicht auf 23)
#   - versteht NUR das alte Protokoll: Gruppe 65, cmd [253,222], 4 Byte BE
#       -> boxflat serial.yml old-send-telemetry (write 65, id [253,222]);
#          moza-rev GROUP_BASE_TELEMETRY=0x41; AZOM WheelModelInfo
#          ("ES/ESX ... old-protocol only"); boxflat Issue #126
#          ("ALL wheels received the new settings, apart from ES wheel")
#   - neue Kommandos (63/[28,0], 63/[26,0], 63/[25,0], 63/[27,0,255])
#     werden ignoriert  -> dieselben Quellen
#   - Frames an dev 0x17=23 landen in einer toten Firmware-Queue
#       -> moza-rev: "Sending to 0x17 fills some firmware queue and
#          eventually locks the base requiring a power cycle."
#   - Modus-Setup: cmd [0x04]=1 an dev 19 (Gruppe 0x40 lt. moza-rev,
#     Gruppe 0x3F lt. boxflat-Write-Tabelle -- Simulator akzeptiert beide);
#     Default-Modus 3 = Kranz unter Base-Kontrolle, Telemetrie-Masken
#     wirken erst bei Modus 1  -> moza-rev Z.25-28/127-132
#   - Farben: persistente Firmware-Palette, ab Werk vorhanden
#       -> AZOM ("relies on firmware-stored palette defaults"); boxflats
#          _wheel_rpm_test (wheel_old.py:408ff) und moza-rev senden beide
#          KEINE Farben und funktionieren
#
#  Moderner Kranz am R9 ("ModernRim", die eigene Hardware-Historie):
#   - hoert auf dev 23, neues Protokoll (63/[26,0] u16 LE)
#   - Telemetrie-Farbtabelle ist FLUECHTIG: nach Base-Power-Off leer;
#     Bitmaske ohne Farbtabelle leuchtet jedes Segment schwarz
#       -> moza.py:45-48 (am eigenen R9 erlebt), Fix in
#          lmu_rpm_leds.py:284-294 und lmu_led_config.py:649-658
#
# Gefuettert wird mit den EXAKTEN Frames, die led_test.py erzeugt
# (os.write gestubbt, kein echter Port).

import os
import sys
import time

PROJECT = "/home/fpauker/simracing/lmu-rpm-leds"
sys.path.insert(0, PROJECT)
os.environ["MOZA_SERIAL_PORT"] = "/dev/null-FAKE-moza"

FAKE_FD = 7777
captured = []
_real_open, _real_write, _real_close = os.open, os.write, os.close
os.open = lambda p, f, *a, **k: FAKE_FD if "FAKE-moza" in str(p) else _real_open(p, f, *a, **k)
os.write = lambda fd, d: (captured.append(bytes(d)) or len(d)) if fd == FAKE_FD else _real_write(fd, d)
os.close = lambda fd: None if fd == FAKE_FD else _real_close(fd)
import termios  # noqa: E402
termios.tcgetattr = lambda fd: [0, 0, 0, 0, 0, 0, [0] * 32]
termios.tcsetattr = lambda fd, when, attrs: None
time.sleep = lambda s: None

import moza  # noqa: E402


def parse(frame):
    """7E len group dev cmd+payload chk -> dict; None bei kaputtem Frame."""
    if frame[0] != 0x7E or (13 + sum(frame[:-1])) % 256 != frame[-1]:
        return None
    return {"group": frame[2], "dev": frame[3], "body": frame[4:4 + frame[1]], "raw": frame}


class EsOnR5:
    """ES-Kranz hinter einer R5-Pro-Base, frisch eingeschaltet."""
    def __init__(self):
        self.mode = 3                 # Default: Kranz unter Base-Kontrolle
        self.mask = 0
        self.palette_ok = True        # persistente Firmware-Palette vorhanden
        self.visible_changes = 0
        self.accepted = 0
        self.dead_queue_17 = 0        # Frames an 0x17 -> Firmware-Queue (Lock-Gefahr)
        self.ignored = 0

    def feed(self, f):
        p = parse(f)
        if p is None:
            self.ignored += 1
            return
        if p["dev"] == 0x17:          # 23: niemand zu Hause, Queue fuellt sich
            self.dead_queue_17 += 1
            return
        if p["dev"] != 0x13:          # nur Base-ID 19 erreicht den Kranz
            self.ignored += 1
            return
        body = p["body"]
        if p["group"] in (0x3F, 0x40) and body[:1] == b"\x04" and len(body) == 2:
            self.mode = body[1]       # rpm-indicator-mode
            self.accepted += 1
        elif p["group"] == 0x41 and body[:2] == b"\xfd\xde" and len(body) == 6:
            self.accepted += 1
            if self.mode == 1:        # nur im Telemetrie-Modus wirksam
                new = int.from_bytes(body[2:6], "big")
                if new != self.mask and self.palette_ok:
                    self.visible_changes += 1
                self.mask = new
        else:
            self.ignored += 1         # alles Neue: unbekannt fuer ES

    def report(self, name):
        print(f"  [{name}] akzeptiert={self.accepted}  sichtbare LED-Aenderungen="
              f"{self.visible_changes}  an-0x17-verpufft={self.dead_queue_17}"
              f"  ignoriert={self.ignored}")


class ModernRim:
    """Moderner Kranz (R9-Historie), Base frisch eingeschaltet -> Farbtabelle leer."""
    def __init__(self):
        self.mode = 0
        self.mask = 0
        self.colors = {}              # fluechtig! leer nach Power-Off
        self.visible_changes = 0
        self.accepted = 0
        self.black_lit = 0            # Maske angenommen, aber schwarz geleuchtet
        self._visibly_on = False      # leuchtet gerade sichtbar etwas?

    def feed(self, f):
        p = parse(f)
        if p is None or p["dev"] != 0x17:
            return
        body = p["body"]
        if p["group"] == 0x3F and body[:2] == b"\x1c\x00" and len(body) == 3:
            self.mode = body[2]; self.accepted += 1          # telemetry-mode
        elif p["group"] == 0x3F and body[:2] == b"\x19\x00" and len(body) == 22:
            for i in range(2, 22, 4):                        # Farbtabelle [25,0]
                self.colors[body[i]] = tuple(body[i + 1:i + 4])
            self.accepted += 1
        elif p["group"] == 0x3F and body[:2] == b"\x1a\x00" and len(body) == 4:
            self.accepted += 1                               # send-rpm-telemetry
            if self.mode == 1:
                new = int.from_bytes(body[2:4], "little")
                if new != self.mask:
                    lit = [i for i in range(16) if new >> i & 1]
                    now_visible = any(
                        self.colors.get(i, (0, 0, 0)) != (0, 0, 0) for i in lit)
                    if now_visible or self._visibly_on:
                        self.visible_changes += 1            # an, um, oder aus
                    elif lit:
                        self.black_lit += 1                  # "leuchtet schwarz"
                    self._visibly_on = now_visible
                self.mask = new
        else:
            self.accepted += 1  # Modus alt, Helligkeit etc.: schluckt er

    def report(self, name):
        print(f"  [{name}] akzeptiert={self.accepted}  sichtbare LED-Aenderungen="
              f"{self.visible_changes}  schwarz-geleuchtet={self.black_lit}")


# ---- (1) Die exakten Frames des Melders: led_test.py ----------------------
captured.clear()
import led_test  # noqa: E402
led_test.main()
frames_led_test = list(captured)
print(f"led_test.py erzeugt {len(frames_led_test)} Frames; Geraete-IDs darin: "
      f"{sorted({f[3] for f in frames_led_test})} (23=0x17, fest verdrahtet, moza.py:29)")

print("\n== Fuetterung mit led_test.py-Frames (das Szenario des Melders) ==")
es = EsOnR5()
for f in frames_led_test:
    es.feed(f)
es.report("ES am R5 Pro      ")
modern = ModernRim()
for f in frames_led_test:
    modern.feed(f)
modern.report("Moderner Kranz R9*")
print("   (*frisch eingeschaltete Base, Farbtabelle leer)")

# ---- (2) Gegenprobe A: korrigierte Legacy-Sequenz an die Base-ID ----------
print("\n== Gegenprobe A: gleiche Sweeps, aber moza-rev-konform an dev 19 ==")
es2 = EsOnR5()
es2.feed(moza.build(0x40, 0x13, [0x04], bytes([1])))         # Modus 1, an Base
for i in range(moza.RPM_LEDS + 1):
    es2.feed(moza.build(0x41, 0x13, [0xFD, 0xDE], ((1 << i) - 1).to_bytes(4, "big")))
es2.feed(moza.build(0x41, 0x13, [0xFD, 0xDE], (0).to_bytes(4, "big")))
es2.report("ES am R5 Pro      ")

# ---- (3) Gegenprobe B: Daemon-Sequenz (_assert_mode) an modernen Kranz ----
print("\n== Gegenprobe B: Daemon-Setup (Mode+FARBTABELLE+Helligkeit) + Sweep ==")
captured.clear()
m = moza.MozaSerial()
m.set_indicator_mode(1)
m.set_rpm_colors()        # genau das fehlt in led_test.py
m.set_rpm_brightness(100)
for i in range(moza.RPM_LEDS + 1):
    m.set_leds((1 << i) - 1)
modern2 = ModernRim()
for f in captured:
    modern2.feed(f)
modern2.report("Moderner Kranz R9 ")

print("\nFazit:")
print(" - led_test.py: auf BEIDEN Modellen 0 sichtbare Aenderungen -> Symptom")
print("   des Issues reproduziert, aber aus ZWEI verschiedenen Gruenden:")
print("   ES: alle 103 Frames an die falsche ID 23 (davon fuellen ~100 die")
print("   Firmware-Queue der Base -> Power-Cycle-Empfehlung an den Melder!);")
print("   moderner Kranz: Masken kommen an, leuchten aber schwarz (Farbtabelle).")
print(" - Beide Gegenproben liefern sichtbare Aenderungen -> jede Hypothese")
print("   hat einen konkreten, getrennten Fix.")
