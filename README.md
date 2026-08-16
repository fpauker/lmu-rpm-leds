# lmu-rpm-leds

Drive the RPM LEDs of a MOZA wheel from **Le Mans Ultimate** telemetry, on Linux.

MOZA Pit House — the software that feeds the wheelbase on Windows — has no Linux
version, so the rev lights simply stay dark. [boxflat](https://github.com/Lawstorant/boxflat)
configures MOZA hardware on Linux beautifully, but as of August 2026 telemetry
ingestion is still listed on its roadmap. This fills that gap: a small daemon
that reads the game's telemetry and pushes the rev bar to the wheel, plus a GTK4
app to shape the curve and manage the service.

No third-party game plugin, no SimHub, no Wine shared-memory bridge.

> Available in English and German; it follows your desktop language. More
> translations are welcome — see [Translating](#translating).

## Requirements

- Linux with systemd (developed and tested on Fedora 44, GNOME/Wayland)
- Le Mans Ultimate running under Proton
- A MOZA wheelbase with rev LEDs in the rim
- `python3` for the daemon — standard library only, nothing to install
- `python3-gobject`, `gtk4`, `libadwaita`, `python3-cairo` for the configuration app

Tested with LMU 1.4 running under `GE-Proton10-34-LMU-hid_fixes`, a community
Proton build for this game, together with a MOZA R9 base and a 10-LED rim. Other
bases speak the same protocol; the LED count is configurable. Nothing here
depends on that particular Proton build — any build that runs LMU will do.

## Install

From source:

```bash
git clone https://github.com/fpauker/lmu-rpm-leds.git
cd lmu-rpm-leds && sudo make install
```

Then enable the service for your user:

```bash
systemctl --user enable --now lmu-rpm-leds.service
```

The service waits for the game by itself — it does not need LMU running to start.

### Building a package

`packaging/lmu-rpm-leds.spec` builds an RPM against a release tarball, which is
what a Fedora COPR repository needs:

```bash
rpmbuild -ba packaging/lmu-rpm-leds.spec
```

The unit is not enabled by a preset — whether the LEDs get fed is the user's
decision, and the app has a switch for it.

### Access to the wheelbase

The base appears as a USB serial device (vendor `346e`, Gudsen) at
`/dev/ttyACM*`, which is owned by `root:dialout` by default. `make install`
places a udev rule that grants the logged-in user access. It takes effect after
replugging the base, or:

```bash
sudo udevadm control --reload && sudo udevadm trigger
```

If the daemon reports that the wheel cannot be reached, this is the first thing
to check. Having boxflat installed already covers it — its own rule does the
same job.

## Usage

Launch **MOZA RPM LEDs** from your application menu, or run `lmu-rpm-leds`.

The app shows the service state, starts and stops it, toggles autostart, and
draws the curve you are editing next to a live preview of the bar.

To tune the curve without driving, flip on **Simulation**: the daemon steps
aside, and the slider drives the real LEDs directly, so you can see exactly
where each threshold sits.

## The curve

| Setting | Meaning | Default |
|---|---|---|
| `start` | fraction of max RPM where the first LED lights | 0.85 |
| `end` | fraction where the whole bar is lit | 0.98 |
| `blink` | fraction where the bar starts flashing | 0.99 |
| `blink_hz` | flashing speed | 8.0 |
| `rate` | telemetry polls per second | 50.0 |
| `leds` | LEDs in the rim | 10 |
| `mode` | `bar` fills left to right, `center` from both ends inward | `bar` |
| `colors` | three stops — low, middle, shift point — as `#rrggbb` | green/amber/red |
| `brightness` | rim brightness in percent | 100 |
| `adaptive` | scale the curve to what each gear actually revs to | true |
| `legacy` | use the older telemetry command id | false |
| `enabled` | feed the LEDs at all | true |

Settings live in `~/.config/lmu-rpm-leds/config.json`. The daemon notices
changes within half a second and reloads them — no restart. Values out of range
are clamped and the curve is forced monotonic on load, so a hand-edited file
cannot wedge the daemon.

**Pick `end` with the tall gears in mind.** In low gears an upshift drops the
revs a long way and the bar clears on its own. In the tall gears on a long
straight it may only fall from 8250 to 7800 rpm — 94.5 % — so an `end` of 0.93
leaves the bar stuck at full. The app spells each threshold out in RPM of the
car you are driving, which makes that easy to see.

### Colours

Six schemes are offered in the app, chosen by looking at a miniature of the lit
bar rather than by name: classic, vivid, warm, formula, cold and red only. Each
is three stops — low, middle, shift point — and the table for however many LEDs
the rim has is generated from them, so a rim with eight or sixteen segments gets
a sensible ramp without anyone editing ten values.

Two details that matter on real LEDs rather than on screen. The ramp is stepped,
not smooth: ten evenly interpolated colours differ by about a tenth of the range
each and read as a smear, so they are grouped into an odd number of blocks that
stay clearly apart. And mixing two far-apart hues in plain RGB collapses through
a dark middle — green to red passes through olive, which looks like a broken
segment — so each mixed colour is rescaled to the brightness its neighbours
call for.

Brightness is a separate setting; the preview dims with it, so the screen keeps
matching the wheel.

### Adapting to the gear

A curve pinned to the rev limiter only works in the gears that reach it.
Measured on a GT car at Spa, with the limiter at 7800 rpm:

| Gear | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| highest reached | 100 % | 99.5 % | 99 % | 95.7 % | 89.9 % | **78.7 %** |

With the first LED at 88 % of the limiter, the bar therefore stayed dark for
the whole of top gear — the car is gear-limited up there, not rev-limited, and
no threshold works for both ends of the gearbox.

With `adaptive` on, each gear gets its own reference: the highest RPM seen in
that gear this session. Replaying the same lap, the share of time the bar was
doing anything went from 8.5 % to 56 % in third and from 20 % to 59 % in
fourth. A gear needs about three seconds of driving before its reference is
trusted; until then the limiter is used, so the display is never worse than it
was.

Nothing is remembered between sessions. Changing the gearing in the setup would
make yesterday's numbers wrong, and one lap of relearning costs less than a
stale reference.

## How it works

### Getting the telemetry

LMU ships its own shared memory interface — the SDK header sits in the game
folder under `Support/SharedMemoryInterface/SharedMemoryInterface.hpp` — and
hosts plugins in a separate `PluginsAdapter.exe` rather than in the game process.

Under Proton, Wine backs named file mappings with **memfd** objects. Those have
no path in the filesystem, which is why nothing turns up in `/dev/shm` and why a
Wine shared-memory bridge seems necessary. They are, however, readable through
`/proc/<pid>/fd/<n>` as the owning user. That is where this reads from.

The SDK structs are `#pragma pack(4)`, so within `TelemInfoV01`:

| Field | Offset | Type |
|---|---|---|
| `mGear` | 352 | int32 |
| `mEngineRPM` | 356 | double |
| `mUnfilteredThrottle` | 388 | double |
| `mEngineMaxRPM` | 532 | double |

`sizeof(TelemInfoV01)` is 1888, and the array is preceded by `activeVehicles`,
`playerVehicleIdx` and `playerHasVehicle` (one byte each, padded to four), which
is how the player's own car is found rather than guessed.

Nothing is hardcoded: the block is located at startup by anchoring on
`mEngineMaxRPM` and then validating the surrounding header, so a game update
cannot silently shift it. Anchoring on `mEngineRPM` would be the obvious choice
and is a trap — a car parked in the garage revs at exactly 0, which made the
block undiscoverable precisely when sitting in the pits.

Several valid-looking blocks exist at once: the game's own, the plugin host's
copy, and at least one leftover that barely updates. Taking whichever was found
first meant occasionally latching onto the stale one, so all candidates are
sampled briefly and the one whose revs actually move wins. If the chosen source
later freezes, the search runs again.

Worth noting for anyone porting rFactor 2 knowledge: there, the telemetry array
order famously does not match the scoring order. In LMU `telemInfo[i].mID == i`,
so `playerVehicleIdx` can be used directly. Verified across a full grid.

### Talking to the wheel

The wheelbase is a USB CDC-ACM device at 115200 baud, configured through
`termios` — no pyserial dependency. Frames look like this:

```
7E | len | group | dev_id | cmd_id... | payload... | checksum
len      = len(cmd_id) + len(payload)
checksum = (13 + sum of all preceding bytes) % 256
```

Two commands matter: `rpm-indicator-mode` (group 63, id `[4]`) hands the LEDs
over to external telemetry when set to 1, and `send-rpm-telemetry` (group 63, id
`[26,0]`, 2 bytes) carries the LED bitmask.

The mask must be sent **little-endian**. Big-endian splits the bar in half at
the byte boundary, lighting a couple of LEDs at one end while the rest of the
bar follows the revs — a confusing failure that looks like a hardware fault.

`verify_protocol.py` builds each frame and compares it byte-for-byte against
boxflat's own encoder, which is where the protocol constants come from.

### Who owns the wheel

The daemon and the app could both write to the serial port and overwrite each
other. Instead of a socket, this is settled with an **expiring lease**: for
simulation and the LED test the app writes a timestamp to
`$XDG_RUNTIME_DIR/lmu-rpm-leds.pause` and renews it every second, and the daemon
holds off while that timestamp is in the future.

The point of an expiry rather than a flag: if the app dies mid-simulation the
lease lapses after three seconds and the daemon simply carries on. A persistent
switch would leave it parked forever.

## Troubleshooting

**The bar stays full after upshifting, especially on long straights.** `end` is
set too low — see the note under [The curve](#the-curve).

**The LEDs ignore the game entirely.** Check whether **Simulation** is still
switched on in the app; it deliberately parks the daemon. The app shows a
banner while it is active.

**The app says there is no telemetry although you are in the car.** Make sure
the game is actually running a session. If it persists, the diagnostic tools
below will show what is in memory.

**Nothing lights up at all.** Check the udev rule first, then try the `legacy`
setting — older rims use a different telemetry command.

The diagnostic tools are installed alongside the modules and are run with an
explicit interpreter:

```bash
python3 /usr/share/lmu-rpm-leds/find_rpm.py     # what telemetry is in memory
python3 /usr/share/lmu-rpm-leds/led_test.py     # drive the LEDs, no game needed
python3 /usr/share/lmu-rpm-leds/led_map.py      # which bit maps to which LED
```

`verify_protocol.py` in the repository compares every generated frame against
boxflat's own encoder. It is a development tool and not part of the package,
because it needs boxflat installed as a Flatpak — it reads the protocol
definitions straight out of that installation.

## Not planned: Flathub

The daemon reads `/proc/<pid>/fd/<n>` of the Proton game processes. A Flatpak
always gets its own PID namespace, and Flatpak can only share `network` and
`ipc` with the host — never PIDs. Inside a sandbox the game processes do not
exist, so the daemon cannot work there at all. This is a hard boundary, not a
permissions issue.

## Translating

Strings are handled with gettext. English is the source language; `po/de.po`
holds the German translation. To add one:

```bash
msginit --locale=fr --input=po/lmu-rpm-leds.pot --output=po/fr.po
# translate po/fr.po, then add fr to LINGUAS in the Makefile
make locale
```

A source checkout picks up `./locale` automatically, so `make locale` is enough
to see your work without installing anything. After changing any user-visible
string in the code, run `make update-po` to refresh the template and merge the
new strings into the existing catalogues.

To check the other language without changing your desktop settings:

```bash
LANGUAGE=en lmu-rpm-leds
```

## Files

| File | Purpose |
|---|---|
| `lmu_rpm_leds.py` | the daemon |
| `lmu_led_config.py` | the GTK4 configuration app |
| `moza.py` | MOZA serial protocol, LED output |
| `config.py` | settings, atomic writes, the pause lease |
| `service.py` | systemd over D-Bus, journal view |
| `ledview.py` | Cairo drawing for the bar and the curve |
| `i18n.py` | translation setup |
| `gearscale.py` | learns what each gear revs to |
| `po/` | translation template and catalogues |
| `verify_protocol.py` | frame comparison against boxflat |
| `find_rpm.py` | locates the telemetry in memory (diagnostics) |
| `led_test.py`, `led_map.py` | LED tests without the game |

## Credits

The MOZA serial protocol constants — message start byte, checksum magic, device
and command ids — were reverse-engineered by
[boxflat](https://github.com/Lawstorant/boxflat) by Tomasz Pakuła. This project
would not exist without that work. boxflat remains the right tool for
configuring MOZA hardware on Linux; the two run side by side.

The shared memory layout was read from the SDK header that Studio 397 ships
inside Le Mans Ultimate itself. That header is proprietary and is not included
here — only the offsets derived from it, which this repository documents openly.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
