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

Unser Code sendet alles hart an `DEV_WHEEL = 23` (moza.py) — beim Melder kommt
also **gar nichts** am LED-Controller an. Das erklärt sein Symptom unabhängig
von Punkt 1/2.

**4. Warnung:** moza-rev wörtlich: *„Sending to 0x17 fills some firmware queue
and eventually locks the base requiring a power cycle."* led_test.py hat beim
Melder ~90 Frames an 0x17 geschickt → ihm **Base aus-/einschalten** empfehlen,
bevor er weitertestet.

## Ursachen-Rangliste für den Melder

1. **Falsche Kennung + falsche Protokollfamilie** (ES → Legacy-Kommandos an
   Base-ID 19). Primärursache; erklärt „keine LED ändert sich" vollständig.
2. **Fehlende Farbtabelle in led_test.py** — beim Melder nachrangig (seine
   Frames kommen ohnehin nicht an), auf modernen Kränzen (R9-Klasse) aber
   derselbe Totalausfall des Tests; live bewiesen, siehe oben.

## Nächste Schritte

- [ ] Urteils-Lauf der Untersuchung einarbeiten, falls er die Rangliste ändert
      (lief bei Redaktionsschluss noch).
- [ ] Rückfragen an den Melder (englisch, nach Freigabe): (a) Base einmal
      aus-/einschalten (Queue-Warnung!), (b) leuchten die LEDs in boxflats
      eigenem RPM-Test?, (c) `ls /dev/serial/by-id/` — zur Bestätigung des
      `_r5_`-Musters.
- [ ] Fix-Skizze: `led_test.py`/`led_map.py` setzen Farbtabelle + Helligkeit
      (wie `_claim_wheel`); zusätzlich Legacy-/Kennungs-Pfad für R3/R5-Basen —
      Erkennung über by-id-Pfad wie moza-rev (`_r5_`/`_r3_` → Legacy an 19)
      oder Probe-Zyklus wie boxflat (23→21→19). Betrifft dann auch den Daemon
      (`moza.py` Kennung/Kommandowahl), nicht nur die Testskripte.
- [ ] Nach dem Fix: v1.2.0 (siehe PROJECT.md, Offen Nr. 3).

## Reproduktion für später

Am eigenen R9 (neues Protokoll): Farbtabelle schwärzen → Sweep unsichtbar →
Tabelle setzen → sichtbar. Dreiphasen-Skript steht in der Sitzungshistorie vom
2026-10-02; Kern: `w.set_rpm_colors([(0,0,0)]*10)` vor dem Sweep. Für den
ES-Fall gibt es ohne ES-Hardware keine Live-Reproduktion — nur den
Frame-Beweis, dass an 19/Legacy nichts gesendet wird.
