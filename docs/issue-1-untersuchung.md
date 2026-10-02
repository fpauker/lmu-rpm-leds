# Issue #1 „LED not working" — Untersuchungsstand

Stand: 2026-10-02 · Issue: https://github.com/fpauker/lmu-rpm-leds/issues/1
Arbeitsdokument (deutsch). **Antworten auf GitHub auf Englisch, Posten nur nach
Freigabe des Besitzers.**

## Die Meldung

`aleluc13`, 2026-09-27: Moza **R5 Pro** Base + **ES**-Lenkrad, boxflat
1.36.2-flatpak, Firmware 1.2.10.13, Code = `main`. `python3 led_test.py` öffnet
den Port, läuft beide Phasen fehlerfrei durch — **keine LED ändert sich**.

## Bewiesen (eigene Messung)

**1. `led_test.py` sendet keine Farbtabelle und keine Helligkeit.**
Frame-Mitschnitt gegen gestubbten Port: 103 Frames, nur Modus-Kommandos und
nackte Bitmasken; die App (`_claim_wheel`) und der Daemon (`_assert_mode`)
senden dagegen byteidentisch je 5 Frames inkl. Farbtabelle `[25,0]` und
Helligkeit `[27,0,255]`. `led_map.py` hat dasselbe Loch.

**2. Ohne Farbtabelle ist der Testlauf unsichtbar — live am R9 vorgeführt
(2026-10-02, Besitzer als Zeuge):** identischer Sweep dreimal; mit absichtlich
geschwärzter Tabelle (alle LEDs 0,0,0) blieb der Kranz stumm, mit richtiger
Tabelle lief das Lauflicht. Die Base quittiert Masken auch ohne Farben — sie
leuchtet sie schwarz. Dasselbe war am 2026-08-16 der Grund für „alles
schwarz" nach dem Aus-/Einschalten der Base (Commit 5718edc); gefixt wurde
damals nur Daemon + App, **nie die Diagnosewerkzeuge**.

## Belegt (fremde Quellen, noch nicht an ES-Hardware verifiziert)

**3. ES am R5/R5 Pro ist ein Alt-Protokoll-Rad und hängt an der Base-Kennung.**
Die 10 RGB-LEDs sitzen zwar im Kranz, werden bei der R3/R5-Generation aber
über **Gerätekennung 0x13 (19, Base)** angesteuert — die Base reicht intern
weiter. Die neuen Kommandos (`telemetry-mode` 28/0, `send-rpm-telemetry` 26/0,
Farbtabelle 25/0) ignoriert das ES komplett. Quellen:
- AZOM `Devices/WheelModelInfo.cs`: „ES/ESX … old-protocol only".
- moza-rev `src/moza.rs`: Legacy-Erkennung über `_r5_`/`_r3_` im
  by-id-Pfad — „usb-Gudsen_MOZA_**R5_P**ro_Base" matcht; Antworten kommen
  von der Base-ID zurück.
- boxflat 1.36.2 hat **keine feste Wheel-ID**: `cycle_wheel_id`
  (connection_manager.py) rotiert {23 → 21 → 19 → …} alle 3 s, bis ein
  Probe-Read antwortet; Parser-Kommentar „Some ES wheels report on main/base
  IDs for some reason". Boxflats ES-Erkennung ist faktisch „antwortet nicht
  auf 23".

Entscheidend dabei: `led_test.py` sendet die **richtigen Legacy-Frames längst**
(Phase 2, Gruppe 65, id 253/222 — deckungsgleich mit boxflats wheel_old-Test).
Der Alleintäter ist die **Gerätekennung**: alles geht hart an `DEV_WHEEL = 23`
(moza.py), das ES am R5 hört auf 19. Beide Phasen verpuffen. Maintainer-Zitate:
boxflat #126 „ALL wheels received the new settings, apart from ES wheel",
#33 „it seems like it somehow works with base ids". Und: Im alten Protokoll
sind die Farben **persistent im Kranz gespeichert** (`old-rpm-color1..10`) —
boxflats eigener funktionierender Legacy-Test sendet gar keine Farbtabelle.
Die Farbtabellen-Lücke ist also ein reiner Neu-Protokoll-Bug (R9-Klasse).

**4. Warnung:** moza-rev wörtlich: *„Sending to 0x17 fills some firmware queue
and eventually locks the base requiring a power cycle."* led_test.py hat beim
Melder ~90 Frames an 0x17 geschickt → ihm **Base aus-/einschalten** empfehlen,
bevor er weitertestet.

## Ursachen-Rangliste für den Melder (Urteil vom 2026-10-02)

| # | Ursache | P | Status |
|---|---|---|---|
| 1 | **Falsche Gerätekennung: alles an 23, ES am R5 hört auf 19.** Beide Phasen verpuffen. | ~75 % | bewiesen, dass wir nur an 23 senden; belegt (moza-rev hardware-bestätigt, boxflat #33/#151), dass ES@R5 auf 19 hört |
| 2 | ES versteht das neue Protokoll nicht → Phase 1 prinzipiell wirkungslos | ~95 % wahr, erklärt aber allein nur Phase 1 | belegt (boxflat #126/#130, AZOM) |
| 3 | Fehlende Farbtabelle in led_test.py | ~10 % als Ursache HIER (Legacy-Farben sind persistent) | als Lücke bewiesen; trifft moderne Kränze (R9-Klasse) voll — dort live vorgeführt |
| 4 | Modus-Feinheiten (moza-rev: Gruppe 0x40 an Base) / boxflat parallel / Defekt | klein | Vermutung; boxflats Testknopf klärt es |

## Nächste Schritte

- [ ] Rückfragen an den Melder (englisch, nach Freigabe), in dieser Reihenfolge:
      1. **Base aus-/einschalten** (Queue-Warnung!), dann: boxflat öffnen —
         siehst du „Wheel" oder „Wheel (old)"? RPM-Testknopf drücken: leuchten
         die LEDs? (trennt Hardware-Defekt von Software UND verrät die
         Protokollfamilie; ausgegrauter Stick-Mode = Kennung ≠ 23)
      2. Mini-Probe: Read `rpm-value1` (`7E 03 40 X 18 01 CS`) nacheinander an
         X = 23/21/19 — welche Kennung antwortet (Antwortgruppe 63–66 zählt)?
      3. Lief boxflat parallel, und haben die LEDs unter Pit House je geleuchtet?
- [x] Fix (umgesetzt 2026-10-02, Weg „moza-rev" statt Read-Probe):
      **Profil `auto`/`modern`/`legacy`** in `moza.py` (`resolve_profile()`:
      `_R3_`/`_R5_` im by-id-Namen → Legacy an Kennung 19, alte Befehlsfamilie
      inkl. persistenter Farben `[21,0,n]`; sonst modern an 23). Wahlschalter
      in der App („Wheelbase-Generation"), Config-Schlüssel `profile`
      (migriert alten `legacy`-Schalter), Daemon `--profile`, Diagnose-Tools
      `--profile=…`. led_test/led_map setzen im modernen Profil jetzt die
      Farbtabelle; `led_test.py --profile=legacy` ist der Ein-Kommando-Test
      für den Melder. Im Legacy-Profil wird **nur** `rpm-indicator-mode`
      gesendet — kein Frame, der einen R5 wedgen kann.
      Eine Read-Probe {23→21→19} bleibt verworfen, solange der Namens-Weg
      nicht an realer Hardware scheitert.
- [ ] Hardware-Bestätigung des Legacy-Pfads durch den Melder steht aus.
- [ ] Nach der Bestätigung: v1.2.0 (siehe PROJECT.md, Offen Nr. 3).

## Reproduktion

**Ohne Hardware:** [`docs/repro_issue1.py`](repro_issue1.py) — stubbt
`os.write`, lässt das echte `led_test.main()` laufen und füttert die Frames in
zwei quellenbelegte Firmware-Modelle. Ergebnis (reproduzierbar, Exit 0):

```
led_test.py (v1.1.0, synthetisiert — die Version des Melders):
              ES am R5 Pro   akzeptiert=0    sichtbar=0   (alle 103 an falsche ID)
              moderner Kranz akzeptiert=103  sichtbar=0   (42 Masken schwarz geleuchtet)
Gegenproben:  legacy @ 19 → 11 sichtbar;  Mode+Farben+Helligkeit → 10 sichtbar
Fix-Nachweis (heutiges led_test.py):
              --profile=modern → 46 sichtbar, 0 schwarz
              --profile=legacy → 46 sichtbar an Kennung 19, 0 an 0x17 verpufft
```

**Am eigenen R9 (live, 2026-10-02):** identischer Sweep dreimal — mit
geschwärzter Tabelle (`w.set_rpm_colors([(0,0,0)]*10)`) unsichtbar, mit
richtiger Tabelle sichtbar. Für den ES-Fall selbst gibt es ohne ES-Hardware
keine Live-Reproduktion, nur das Modell oben.
