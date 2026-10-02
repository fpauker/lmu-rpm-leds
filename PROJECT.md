# lmu-rpm-leds — Projektstand und Übergabe

Stand: 2026-10-02

Daemon + GTK4-App, die die RPM-LEDs einer Moza-Wheelbase aus Le-Mans-Ultimate-
Telemetrie speisen. **Status: täglich in Benutzung** auf dem Entwicklungsrechner
(Fedora 44, Moza R9, 10-LED-Kranz). Was es ist, wie es funktioniert und wie man
es installiert, steht vollständig in der [README](README.md) — hier steht nur,
was README und Code nicht beantworten.

Die Commit-Nachrichten sind bewusst ausführlich geschrieben; `git log` ist das
Sitzungsprotokoll dieses Projekts. Bei Widersprüchen gilt: Code > README >
dieses Dokument.

## Betrieb auf diesem Rechner (abweichend vom Paket-Weg)

Es gibt **keine** Installation nach `/usr` — alles läuft direkt aus diesem
Quellordner:

- Unit: `~/.config/systemd/user/lmu-rpm-leds.service`, `ExecStart` zeigt auf
  `lmu_rpm_leds.py` **in diesem Ordner**. Code-Änderungen wirken erst nach
  `systemctl --user restart lmu-rpm-leds.service`.
- Autostart ist **absichtlich aus** (Entscheidung des Besitzers); gestartet
  wird über den Schalter in der App oder `systemctl --user start`.
- Die App braucht zwingend `/usr/bin/python3`. Das `python3` im PATH dieses
  Rechners ist **linuxbrew** und hat kein `gi` — mit ihm startet die App nicht.
- Desktop-Starter: `~/.local/share/applications/io.github.fpauker.LmuRpmLeds.desktop`
  (zeigt ebenfalls auf den Quellordner).
- Gesundheitsprüfung: Dienst starten, dann `journalctl --user -u
  lmu-rpm-leds.service -n 5` — gesund sind die Zeilen `Telemetrie gefunden:
  /proc/<pid>/fd/<n> @ <offset>` (nur wenn LMU eine Session geladen hat) und
  `Lenkrad verbunden: /dev/serial/by-id/usb-Gudsen_MOZA_*`.
- `make check` vor jedem Commit: prüft Syntax, Übersetzungskatalog und das
  gettext-`_`-Shadowing (siehe Stolperfallen).

## Zustand

- Funktioniert und am Gerät verifiziert: Daemon, App (Dienststeuerung über
  D-Bus, Kennlinie, Simulation, LED-Test), Farben inkl. Schemata und
  Helligkeit, Füllrichtung beidseitig, Gang-Anpassung mit Ausnahme für den
  höchsten Gang (`mMaxGears`, live an einem vollen Feld bestätigt),
  Deutsch/Englisch per gettext.
- Seit 2026-10-02: **Profil-Wahlschalter** (`profile`: auto/modern/legacy) in
  App, Daemon und Diagnose-Tools — schaltet Gerätekennung (23 ↔ 19) und
  Befehlsfamilie um, `auto` entscheidet nach dem USB-Namen (`_R3_`/`_R5_` →
  legacy). Alter Schlüssel `legacy` wird beim Laden migriert. Moderner Pfad am
  R9 verifiziert; **Legacy-Pfad nur auf Frame-Ebene getestet** (kein R3/R5
  vorhanden), Hardware-Bestätigung müsste vom Issue-#1-Melder kommen.
- **Tag `v1.1.0` hängt 8 Commits hinter `main`** (Farben, Gang-Anpassung,
  Top-Gang-Fix, Diversen). Die RPM-Spec baut aus dem Tag-Tarball — ein heute
  gebautes Paket hätte diese Features nicht. `v1.2.0` steht aus.
- COPR ist vorbereitet (packaging/lmu-rpm-leds.spec, Dateiliste gegen
  DESTDIR-Installation abgeglichen), aber **kein COPR-Projekt angelegt** —
  das braucht das Fedora-Konto des Besitzers.
- Die Farbtabellen-Lücke aus Issue #1 ist seit 2026-10-02 geschlossen:
  `led_test.py` und `led_map.py` setzen im modernen Profil die Tabelle
  (im Legacy-Profil absichtlich nicht — dort ist sie persistent im Kranz).
  Details: [docs/issue-1-untersuchung.md](docs/issue-1-untersuchung.md).

## Entscheidungen (und verworfene Wege)

- **Kein Flathub, stattdessen COPR.** Gemessen, nicht vermutet: Flatpak kann
  nur `network`/`ipc` mit dem Host teilen, nie den PID-Namensraum; der Daemon
  liest `/proc/<pid>/fd/` der Proton-Prozesse. Nicht erneut prüfen.
- **Config-Datei + mtime-Polling statt Socket/D-Bus** zwischen App und Daemon.
  Atomares Schreiben (`os.replace`) macht halbe Leseergebnisse unmöglich;
  ein Socket wäre nur Lebenszyklus-Komplexität gewesen.
- **Pause als ablaufende Leihgabe** (`$XDG_RUNTIME_DIR/lmu-rpm-leds.pause`,
  3-s-Lease, sekündlich erneuert) statt persistentem Schalter: Ein Absturz der
  App darf den Daemon nie dauerhaft parken. Vorgänger-Idee `enabled:false`
  in der Config wurde genau deshalb verworfen.
- **„Unsere Farben gewinnen" gegen boxflat** (Besitzer-Entscheidung):
  Daemon bekräftigt Modus+Farben+Helligkeit alle 2 s, die App während
  Simulation/Test sekündlich. boxflat schreibt auf denselben Port und spielt
  sonst seine eigene Tabelle ein.
- **Beide Modus-Kommandos senden, alt (id 4) zuerst, neu (id 28/0) zuletzt.**
  Umgekehrte Reihenfolge übergab die LEDs wieder der Base — Symptom: ging nur
  nach boxflats eigenem RPM-Test.
- **Gang-Anpassung sitzungsweise, nie persistiert** (Setup-Wechsel ändert die
  Übersetzung); höchster Gang grundsätzlich unskaliert (geschwindigkeits-,
  nicht drehzahlbegrenzt — skaliert sprang der Balken beim Einlegen sofort
  auf voll).
- **Englische msgids, Deutsch in po/de.po**; mehrdeutige Wörter über
  `pgettext` („Start" Knopf ≠ „Start" Achsenbeschriftung).
- **verify_protocol.py bleibt außerhalb des Pakets** (braucht boxflat-Flatpak;
  PyYAML bringt das Flatpak selbst mit — keine eigene Abhängigkeit).

## Offen

1. **Issue #1 „LED not working"** (Moza R5 Pro + ES-Lenkrad) — Mechanismus
   live reproduziert, ES-Frage offen; Stand und nächste Schritte in
   [docs/issue-1-untersuchung.md](docs/issue-1-untersuchung.md). Antworten auf
   GitHub auf Englisch, nur nach Freigabe des Besitzers posten.
2. ~~Fix dazu: Farbtabelle in den Diagnose-Tools~~ seit 2026-10-02 umgesetzt,
   zusammen mit dem Profil-Wahlschalter (Kennung 19 ↔ 23). Offen bleibt die
   Hardware-Bestätigung des Legacy-Pfads durch den Issue-#1-Melder —
   `led_test.py --profile=legacy` wäre dessen Ein-Kommando-Test.
3. `v1.2.0` taggen + Spec-Version und AppStream-Release nachziehen, dann COPR.
4. Helligkeit/Farben gelten nur für den Kranz; `rpm-blink-color*` (eigene
   Blinkfarbe) wäre möglich, war aber nie gefordert.

## Stolperfallen (nicht in der README)

- **systemd-D-Bus: nie auf `UnitNew`/`UnitRemoved` neu anhängen.** LoadUnit
  einer inaktiven Unit löst `UnitNew` aus → Rückkopplungsschleife aus
  synchronen D-Bus-Aufrufen im Signalhandler; die GTK-Hauptschleife verhungert
  und das Fenster bleibt 0×0 (unsichtbar trotz `mapped=True`). Kommentar in
  service.py erklärt es; Diagnose damals via `faulthandler.dump_traceback_later`.
- **SIGTERM muss durchs `finally`**: `signal.signal(SIGTERM, → sys.exit)` im
  Daemon, `exec` im Starter-Skript. Ohne beides bleibt der Balken beim
  Dienst-Stopp eingefroren und die Base im externen Modus.
- **`for _ in …` zerstört gettext** (`_` wird int). Zweimal passiert, daher
  bricht `make check` auf dieses Muster ab.
- **make-Variablen: `#` beginnt auch in Zuweisungen einen Kommentar** — sed-
  Ausdrücke in SUBST deshalb mit `|` als Trenner.
- In **Claudes Shell** ist `GDK_BACKEND=x11` gesetzt (nicht in der
  Benutzersitzung!) — bei GUI-Diagnosen `env -u GDK_BACKEND` verwenden, sonst
  jagt man Phantome. Hat einmal Stunden gekostet.
- Aufzeichnungen/Hilfsskripte im Scratchpad (`/tmp/claude-…`) überleben
  weder Neustart noch Sitzungswechsel — Messdaten, die bleiben sollen,
  gehören nach `docs/` oder in Commit-Nachrichten.
- udev: boxflats eigene Regel nutzt `ACTION=="add"` und friert dadurch die
  uaccess-ACL auf dem Benutzer ein, der beim Anstecken aktiv war (nach Reboot:
  der GDM-Greeter). Funktioniert beim Besitzer nur, weil boxflat zusätzlich
  MODE=0666 setzt. Unsere Regel absichtlich ohne beides — nicht „angleichen".
