# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Shared settings for the daemon and the configuration app.

One JSON file under XDG config. The daemon polls its mtime and reloads on the
fly, so changes from the GUI take effect without restarting the service.

Writes go through a temporary file and os.replace, so a reader never observes a
half-written file — the daemon reads this at 50 Hz.
"""

import json
import os

import palette

APP_NAME = "lmu-rpm-leds"

DEFAULTS = {
    "start": 0.85,      # fraction of max RPM where the first LED lights
    "end": 0.98,        # fraction where all LEDs are lit
    "blink": 0.99,      # fraction where the bar starts flashing
    "blink_hz": 8.0,    # flashing speed
    "rate": 50.0,       # telemetry polls per second
    "leds": 10,         # LEDs on the rim
    "mode": "bar",      # "bar" fills left to right, "center" from both ends
    "adaptive": True,   # scale the curve to what each gear actually revs to
    "colors": list(palette.DEFAULT_STOPS),   # three stops: low, middle, shift
    "brightness": 100,  # percent
    "legacy": False,    # use the old telemetry command id
    "enabled": True,    # feed the LEDs at all
}

# Sanity ranges; anything outside is clamped rather than rejected, so a hand
# edited file can never brick the daemon.
MODES = ("bar", "center")

LIMITS = {
    "start": (0.0, 1.0),
    "end": (0.0, 1.2),
    "blink": (0.0, 1.5),
    "blink_hz": (1.0, 30.0),
    "rate": (5.0, 200.0),
    "leds": (1, 16),
    "brightness": (10, 100),
}


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def config_path():
    return os.path.join(config_dir(), "config.json")


def sanitise(raw):
    """Merge over the defaults, coerce types, clamp to sane ranges."""
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            # "colors" is a list; the coercion below would turn a hex string
            # into a list of its characters. It gets its own validator.
            if key not in raw or key == "colors":
                continue
            value = raw[key]
            try:
                value = bool(value) if isinstance(default, bool) else type(default)(value)
            except (TypeError, ValueError):
                continue
            if key in LIMITS:
                low, high = LIMITS[key]
                value = max(low, min(high, value))
            cfg[key] = value

    if cfg["mode"] not in MODES:
        cfg["mode"] = DEFAULTS["mode"]

    # Outside the block above on purpose, so a missing or unreadable file still
    # comes back with three usable stops — and with a list of its own, since
    # dict(DEFAULTS) copies shallowly and a shared list would let one caller's
    # edit leak into every other.
    cfg["colors"] = palette.stops(raw.get("colors") if isinstance(raw, dict) else None)

    # The curve must stay monotonic or the bar maths break down.
    if cfg["end"] <= cfg["start"]:
        cfg["end"] = cfg["start"] + 0.01
    if cfg["blink"] < cfg["end"]:
        cfg["blink"] = cfg["end"]
    return cfg


def load():
    """Current settings. Missing or damaged file yields the defaults."""
    try:
        with open(config_path(), "r") as f:
            return sanitise(json.load(f))
    except (OSError, ValueError):
        return sanitise(None)


def save(cfg):
    """Write atomically so the daemon never reads a partial file."""
    cfg = sanitise(cfg)
    os.makedirs(config_dir(), exist_ok=True)
    path = config_path()
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return cfg


def mtime():
    try:
        return os.stat(config_path()).st_mtime_ns
    except OSError:
        return 0


# --- runtime pause -----------------------------------------------------------
#
# While the GUI drives the LEDs itself (simulation, LED test) the daemon has to
# keep its hands off the serial port. That hand-off must not survive a crash, so
# it is a lease with an expiry rather than a persisted setting: the GUI keeps
# renewing it, and it lapses on its own within seconds if the GUI dies. The file
# lives in XDG_RUNTIME_DIR, which is wiped at logout anyway.

def pause_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, APP_NAME + ".pause")


def pause(seconds=3.0):
    """Ask the daemon to stay quiet for the next `seconds`. Renew to extend."""
    import time
    path = pause_path()
    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(str(time.time() + seconds))
        os.replace(tmp, path)
    except OSError:
        pass


def unpause():
    try:
        os.unlink(pause_path())
    except OSError:
        pass


def paused():
    """True while a lease is held and still valid."""
    import time
    try:
        with open(pause_path()) as f:
            return float(f.read().strip()) > time.time()
    except (OSError, ValueError):
        return False


class Watcher:
    """Reloads the config when the file changes underneath us."""

    def __init__(self):
        self._mtime = None
        self.config = dict(DEFAULTS)
        self.poll()

    def poll(self):
        """Refresh if the file changed; True when the settings differ now."""
        current = mtime()
        if current == self._mtime:
            return False
        self._mtime = current
        new = load()
        changed = new != self.config
        self.config = new
        return changed
