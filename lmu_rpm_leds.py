#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Drive a Moza wheel's RPM LEDs from Le Mans Ultimate telemetry, on Linux.

The piece Moza Pit House provides on Windows, and that boxflat does not (yet)
implement: feed live engine RPM to the wheelbase so the rev lights work.

How the data is obtained without any third-party plugin:

  LMU ships its own shared memory interface (see the game's
  Support/SharedMemoryInterface/SharedMemoryInterface.hpp) and hosts plugins in
  a separate PluginsAdapter.exe. Under Proton, Wine backs named file mappings
  with memfd objects, which have no path in the filesystem — but they are
  readable through /proc/<pid>/fd/<n> as the owning user. That is where the
  telemetry is read from here.

  The structs are #pragma pack(4), so inside TelemInfoV01:
      mGear        @352 (int32)
      mEngineRPM   @356 (double)
      mEngineMaxRPM@532 (double)
  and sizeof(TelemInfoV01) == 1888. The array is preceded by
      activeVehicles (u8), playerVehicleIdx (u8), playerHasVehicle (u8)
  which is how the player's own car is located.

LED output speaks the Moza serial protocol (see moza.py); frames were verified
byte-for-byte against boxflat's own encoder.
"""

import argparse
import glob
import os
import signal
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import gearscale
import moza
from i18n import _

STRIDE = 1888  # sizeof(TelemInfoV01), pack(4)
OFF_ELAPSED = 12   # mElapsedTime, the session clock
OFF_GEAR = 352
OFF_RPM = 356
OFF_MAXRPM = 532
OFF_THROTTLE = 388
OFF_NAME = 32
HDR_LEN = 4  # activeVehicles, playerVehicleIdx, playerHasVehicle, pad

GAME_PROCS = ("PluginsAdapter.exe", "Le Mans Ultimate.exe")

# How far below the blink threshold the revs must fall before flashing stops,
# as a fraction of the limiter.
BLINK_HYSTERESIS = 0.015


def candidate_fds():
    """Wine memfd mappings of the running game and its plugin host."""
    out = []
    for name in GAME_PROCS:
        pids = subprocess.run(["pgrep", "-f", name],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            for fd in glob.glob(f"/proc/{pid}/fd/*"):
                try:
                    if "memfd:wine-mapping" in os.readlink(fd):
                        out.append(fd)
                except OSError:
                    continue
    return out


def looks_like_telemetry(data, hdr):
    """True if `hdr` plausibly points at SharedMemoryTelemetryData."""
    active, player_idx, has_vehicle = data[hdr], data[hdr + 1], data[hdr + 2]
    if not (1 <= active <= 104) or has_vehicle not in (0, 1) or player_idx >= 104:
        return False
    base = hdr + HDR_LEN
    if base + active * STRIDE > len(data):
        return False
    for i in range(min(active, 6)):  # every active car must read sanely
        o = base + i * STRIDE
        rpm = struct.unpack_from("<d", data, o + OFF_RPM)[0]
        mx = struct.unpack_from("<d", data, o + OFF_MAXRPM)[0]
        if not (3000.0 <= mx <= 20000.0):
            return False
        if not (0.0 <= rpm <= mx * 1.1):
            return False
    return True


def find_sources(paths=None):
    """Every plausible telemetry block, as (path, header offset).

    Anchored on mEngineMaxRPM, never on mEngineRPM: a parked car in the garage
    revs at exactly 0, and anchoring on that made the block undiscoverable
    precisely when the player was sitting in the pits.
    """
    found = []
    for path in (candidate_fds() if paths is None else paths):
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        for off in range(0, len(data) - STRIDE, 4):
            mx = struct.unpack_from("<d", data, off)[0]
            if not (3000.0 < mx < 20000.0):
                continue
            hdr = off - OFF_MAXRPM - HDR_LEN
            if hdr >= 0 and looks_like_telemetry(data, hdr):
                found.append((path, hdr))
                break
    return found


def pick_source(settle=0.25):
    """The liveliest telemetry block.

    Several mappings hold a valid-looking block at once — the game's own, the
    plugin host's copy, and at least one that is left over and barely moves.
    Picking whichever turned up first meant sometimes latching onto the stale
    one, so candidates are sampled briefly and the one that actually moves wins.
    """
    candidates = find_sources()
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    probes = []
    for path, hdr in candidates:
        try:
            probes.append((path, hdr, Telemetry(path, hdr), []))
        except OSError:
            continue

    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        for _p, _h, tele, seen in probes:
            try:
                sample = tele.read()
            except OSError:
                sample = None
            seen.append(sample[0] if sample else None)
        time.sleep(0.01)

    def score(entry):
        seen = entry[3]
        valid = [v for v in seen if v is not None]
        changes = sum(1 for a, b in zip(valid, valid[1:]) if a != b)
        # A block that reads at all beats one that does not; among those, the
        # one whose revs move beats a frozen copy.
        return (len(valid) > 0, changes, len(valid))

    probes.sort(key=score, reverse=True)
    for _p, _h, tele, _s in probes:
        tele.close()
    if not probes:
        return None
    best = probes[0]
    return best[0], best[1]


def find_source():
    """Backwards-compatible single-source lookup."""
    return pick_source()


class Telemetry:
    def __init__(self, path, hdr):
        self.path = path
        self.hdr = hdr
        self.pid = path.split("/")[2]
        self.fd = os.open(path, os.O_RDONLY)
        # Session clock of the last sample, used to tell a frozen buffer from a
        # car that is simply standing still.
        self.elapsed = None

    def alive(self):
        """Our fd keeps the memfd alive after the game exits, so reads would
        happily return stale telemetry forever. Check the owner instead."""
        return os.path.exists(f"/proc/{self.pid}")

    def read(self):
        """(rpm, maxrpm, gear, throttle) for the player's car, or None."""
        head = os.pread(self.fd, 3, self.hdr)
        if len(head) < 3:
            return None
        active, player_idx, has_vehicle = head[0], head[1], head[2]
        if not has_vehicle or player_idx >= max(active, 1):
            return None
        entry = self.hdr + HDR_LEN + player_idx * STRIDE
        buf = os.pread(self.fd, STRIDE, entry)
        if len(buf) < OFF_MAXRPM + 8:
            return None
        self.elapsed = struct.unpack_from("<d", buf, OFF_ELAPSED)[0]
        gear = struct.unpack_from("<i", buf, OFF_GEAR)[0]
        rpm = struct.unpack_from("<d", buf, OFF_RPM)[0]
        mx = struct.unpack_from("<d", buf, OFF_MAXRPM)[0]
        throttle = struct.unpack_from("<d", buf, OFF_THROTTLE)[0]
        if mx <= 0:
            return None
        return rpm, mx, gear, throttle

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class Wheel:
    """The serial link, held loosely.

    The wheelbase may be switched off when the service starts, and it may be
    unplugged mid-session. Neither is a reason to die — as a Restart=always
    unit that would just turn into a crash loop — so the port is opened lazily
    and reopened after a failure.
    """

    RETRY = 3.0

    def __init__(self):
        self._port = None
        self._next_try = 0.0
        self._complained = False

    def _ensure(self):
        if self._port is not None:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        self._next_try = now + self.RETRY
        try:
            self._port = moza.MozaSerial()
            self._port.set_indicator_mode(1)
            print(_("Wheel connected: {path}").format(path=self._port.path))
            self._complained = False
            return True
        except OSError as exc:
            if not self._complained:
                print(_("Wheel not reachable ({error}) — will keep trying.")
                      .format(error=exc))
                self._complained = True
            return False

    def send(self, mask, legacy=False):
        """True if the frame went out."""
        if not self._ensure():
            return False
        try:
            if legacy:
                self._port.set_leds_legacy(mask)
            else:
                self._port.set_leds(mask)
            return True
        except OSError as exc:
            print(_("Wheel lost ({error}) — reconnecting.").format(error=exc))
            self.close(restore=False)
            self._complained = True
            return False

    @property
    def connected(self):
        return self._port is not None

    def close(self, restore=True):
        port, self._port = self._port, None
        if port is None:
            return
        try:
            if restore:
                port.set_leds(0)
                port.set_indicator_mode(0)
            port.close()
        except OSError:
            pass


def leds_lit(frac, start, end, leds):
    """How many LEDs the curve calls for at this fraction of the limiter."""
    if frac < start:
        return 0
    span = max(end - start, 1e-6)
    return min(leds, int((frac - start) / span * leds) + 1)


def mask_for(lit, leds, mode="bar"):
    """Turn a count of lit LEDs into the bitmask for that fill style.

    bar     fills left to right, the usual rev bar.
    center  grows inward from both ends and meets in the middle. With an odd
            count the left side takes the extra LED, so the bar never jumps
            sideways as it fills.
    """
    if lit <= 0:
        return 0
    if lit >= leds:
        return (1 << leds) - 1
    if mode == "center":
        left = (lit + 1) // 2
        right = lit // 2
        mask = (1 << left) - 1
        for i in range(right):
            mask |= 1 << (leds - 1 - i)
        return mask
    return (1 << lit) - 1


def leds_for(rpm, maxrpm, start, end, leds, mode="bar"):
    """Bitmask for the rev bar; below `start` of the limiter nothing is lit."""
    return mask_for(leds_lit(rpm / maxrpm, start, end, leds), leds, mode)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=float,
                    help=_("fraction of maximum RPM where the first LED lights"))
    ap.add_argument("--end", type=float,
                    help=_("fraction where the whole bar is lit"))
    ap.add_argument("--blink", type=float,
                    help=_("fraction where the bar starts flashing (shift point)"))
    ap.add_argument("--rate", type=float, help=_("updates per second"))
    ap.add_argument("--legacy", action="store_true", default=None,
                    help=_("use the legacy telemetry command (id 253/222)"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # Settings live in the config file so the GUI can change them on the fly;
    # anything passed on the command line wins for this run.
    watcher = config.Watcher()
    overrides = {k: v for k, v in vars(args).items()
                 if k in config.DEFAULTS and v is not None}
    cfg = dict(watcher.config, **overrides)

    # systemd stops the unit with SIGTERM, which kills the process outright —
    # the cleanup below would never run, leaving the bar frozen at its last
    # mask and the base still handing its LEDs to external telemetry. Turning
    # the signal into SystemExit makes it pass through the finally block.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    wheel = Wheel()

    def send(mask):
        """Returns the mask to remember: None when the frame did not go out,
        so the next pass resends it once the wheel is back."""
        return mask if wheel.send(mask, cfg["legacy"]) else None

    tele = None
    last_mask = None
    last_good = 0.0        # when the session clock last advanced
    last_elapsed = None
    blink_phase = False
    blinking = False
    blink_at = 0.0
    scale = gearscale.GearScale()
    last_publish = 0.0
    last_cfg_poll = 0.0
    print(_("Running. Ctrl+C to stop."))
    print(_("Configuration: {path}").format(path=config.config_path()))
    try:
        while True:
            # Pick up GUI edits without a restart, but do not stat the file at
            # the full telemetry rate.
            now = time.monotonic()
            if now - last_cfg_poll > 0.5:
                last_cfg_poll = now
                if watcher.poll():
                    cfg = dict(watcher.config, **overrides)
                    last_mask = None  # force a resend under the new curve
                    print(_("\nCurve reloaded: start={start:.2f} end={end:.2f} "
                            "blink={blink:.2f}").format(
                                start=cfg["start"], end=cfg["end"],
                                blink=cfg["blink"]))

            interval = 1.0 / cfg["rate"]

            # Switched off in the config, or the config app has taken the wheel
            # for a test. The pause is a short lease, so a crashed GUI cannot
            # leave us parked for good.
            if not cfg["enabled"] or config.paused():
                if last_mask:
                    send(0)
                # Forget what we last sent: the config app writes its own masks
                # to the same port while we are parked, so on resuming we must
                # repaint unconditionally instead of assuming the bar is dark.
                last_mask = None
                time.sleep(0.2)
                continue

            if tele is None:
                found = find_source()
                if not found:
                    if last_mask:
                        last_mask = send(0)
                    time.sleep(2.0)
                    continue
                tele = Telemetry(*found)
                last_good = now
                last_elapsed = None
                print(_("Telemetry found: {path} @ {offset}")
                      .format(path=tele.path, offset=tele.hdr))

            if not tele.alive():
                print(_("\nLMU has quit — waiting for the next start."))
                tele.close()
                tele = None
                scale.reset()
                last_mask = send(0)
                continue

            # The game can swap its mapping on a session change, leaving us
            # reading a block that is still valid but no longer fed. The test
            # is the session clock, not the revs: a car parked in the pits
            # legitimately holds the same RPM for minutes on end.
            if last_good and now - last_good > 20.0:
                print(_("\nTelemetry frozen — searching for the source again."))
                tele.close()
                tele = None
                last_good = 0.0
                continue

            try:
                sample = tele.read()
            except OSError:  # game closed
                tele.close()
                tele = None
                continue

            if sample is None:
                mask = 0
            else:
                rpm, mx, gear, throttle = sample
                if tele.elapsed != last_elapsed:
                    last_elapsed = tele.elapsed
                    last_good = now
                leds = cfg["leds"]
                # In the tall gears the car never reaches the limiter, so the
                # curve is scaled to what this gear actually revs to. Until a
                # gear has shown its range the limiter is used, which is the
                # unadapted behaviour.
                if cfg["adaptive"]:
                    reference = scale.observe(rpm, mx, gear, now)
                    if now - last_publish > 2.0:
                        last_publish = now
                        gearscale.publish(scale.table(mx), mx)
                else:
                    reference = mx
                frac = rpm / reference if reference else 0.0
                mask = mask_for(leds_lit(frac, cfg["start"], cfg["end"], leds),
                                leds, cfg["mode"])

                # Hysteresis on the blink threshold. Sitting on the limiter in
                # the top gear the revs bounce across it many times a second,
                # and without this the bar alternates between flashing and
                # showing the plain bar — which reads as a broken display.
                # Once flashing, it keeps flashing until the revs drop a clear
                # step below the threshold.
                if frac >= cfg["blink"]:
                    blinking = True
                elif frac < cfg["blink"] - BLINK_HYSTERESIS:
                    blinking = False

                if blinking:
                    if now - blink_at > 0.5 / cfg["blink_hz"]:
                        blink_phase = not blink_phase
                        blink_at = now
                    mask = (1 << leds) - 1 if blink_phase else 0
                if args.verbose:
                    print(f"\rrpm={rpm:6.0f}/{mx:6.0f} ref={reference:6.0f} "
                          f"gang={gear:2} gas={throttle:4.2f} "
                          f"leds={mask:010b}", end="", flush=True)

            if mask != last_mask:
                last_mask = send(mask)
            time.sleep(interval)
    except (KeyboardInterrupt, SystemExit):
        print(_("\nStopping ..."))
    finally:
        wheel.close()
        if tele:
            tele.close()


if __name__ == "__main__":
    main()
