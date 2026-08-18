# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Learn what each gear actually revs to, and scale the curve to that.

A curve pinned to mEngineMaxRPM only works in the gears that reach the limiter.
Measured on a GT car at Spa: gears 1 to 3 hit 99–100 % of the limiter, gear 4
managed 95.7 %, gear 5 got to 89.9 %, and top gear never passed 78.7 %. With
the first LED set at 88 % of the limiter the bar therefore stayed dark for the
whole of top gear — the car is gear-limited up there, not rev-limited.

So each gear gets its own reference: the highest RPM seen in that gear this
session. Until a gear has been driven long enough to have shown its range, the
limiter is used, which is exactly the old behaviour — the display never gets
worse than before while it is still learning.

Nothing is persisted. A different setup changes the gearing, and stale numbers
would be worse than a fresh lap of learning.
"""

import json
import os

# A gear must have been driven this long, in total, before its learned peak is
# trusted. Shorter than this and a brief moment at part throttle would scale
# the whole gear to a fraction of its real range.
SETTLE_SECONDS = 3.0

# Never scale a gear to less than this fraction of the limiter. Guards against
# a gear that was only ever crawled through, e.g. leaving the pit lane.
MIN_REFERENCE = 0.55


class GearScale:
    """Per-gear reference RPM, learned while driving."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._peak = {}      # gear -> highest RPM seen
        self._time = {}      # gear -> seconds spent in it
        self._maxrpm = None
        self._max_gears = None
        self._top_seen = 0   # highest gear actually driven, sanity for the above
        self._last_sample = None

    def observe(self, rpm, maxrpm, gear, now, max_gears=None):
        """Feed one telemetry sample. Returns the reference RPM to use."""
        # A car change resets everything; the old gearing tells us nothing.
        if self._maxrpm is not None and maxrpm != self._maxrpm:
            self.reset()
        self._maxrpm = maxrpm
        if max_gears is not None:
            self._max_gears = max_gears
        if gear > self._top_seen:
            self._top_seen = gear

        # Gear 0 shows up for a few tens of milliseconds during every upshift,
        # and while parked. Neither says anything about a gear's range.
        if gear >= 1:
            elapsed = 0.0
            if self._last_sample is not None:
                prev_gear, prev_now = self._last_sample
                if prev_gear == gear:
                    elapsed = max(0.0, min(now - prev_now, 1.0))
            self._time[gear] = self._time.get(gear, 0.0) + elapsed
            if rpm > self._peak.get(gear, 0.0):
                self._peak[gear] = rpm
        self._last_sample = (gear, now)

        return self.reference(gear, maxrpm)

    def reference(self, gear, maxrpm):
        """What to treat as the top of the band in this gear.

        The top gear is never scaled. It is speed-limited, not rev-limited:
        entry revs already sit close to whatever peak the gear will ever see,
        so a scaled bar jumps to full the moment the gear engages and then
        blinks at an engine nowhere near its limiter — and there is no higher
        gear a shift indicator could be pointing at anyway. Absolute revs
        against the limiter are the honest display there.
        """
        # Trust the claimed gear count only while it is consistent with what
        # has actually been driven: the offset it is read from is derived from
        # the SDK header, and a misread byte (say, a tyre compound index of 2)
        # must not silently declare third gear "top" and switch scaling off.
        claimed = self._max_gears
        if claimed is not None and claimed >= self._top_seen and gear >= claimed:
            return maxrpm
        if gear < 1 or self._time.get(gear, 0.0) < SETTLE_SECONDS:
            return maxrpm
        peak = self._peak.get(gear, 0.0)
        floor = maxrpm * MIN_REFERENCE
        return max(peak, floor)

    def table(self, maxrpm):
        """What has been learned so far, for display."""
        return {
            gear: {
                "peak": round(self._peak.get(gear, 0.0)),
                "seconds": round(self._time.get(gear, 0.0), 1),
                "reference": round(self.reference(gear, maxrpm)),
                "learned": (self._time.get(gear, 0.0) >= SETTLE_SECONDS
                            and not (self._max_gears is not None
                                     and self._max_gears >= self._top_seen
                                     and gear >= self._max_gears)),
            }
            for gear in sorted(self._time)
        }


def state_path():
    """Where the daemon publishes what it has learned, for the app to read."""
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "lmu-rpm-leds.gears")


def publish(table, maxrpm):
    """Write the learned table where the app can pick it up. Best effort."""
    path = state_path()
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"maxrpm": maxrpm, "gears": table}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def read():
    """The daemon's learned table, or None."""
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
