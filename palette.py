# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Colour schemes for the rev bar.

A scheme is three stops — low, middle, shift point — and the table for however
many LEDs the rim has is generated from them. Ten individual colour pickers
would be a lot of clicking for something you look at out of the corner of your
eye at 250 km/h.

Deliberately stdlib only and free of GTK and serial imports, so the daemon, the
app and the drawing code can all use it without an import cycle.
"""

DEFAULT_STOPS = ("#20d835", "#ffc21a", "#ff3838")

# Names are identifiers; the app translates them for display.
PRESETS = {
    "classic": ("#20d835", "#ffc21a", "#ff3838"),
    "vivid":   ("#00ff00", "#ffe000", "#ff0000"),
    "warm":    ("#c8ff00", "#ffb400", "#e00000"),
    "formula": ("#00d82b", "#ff2000", "#0032ff"),
    "cold":    ("#00e0ff", "#0028ff", "#ff00c8"),
    "red":     ("#320000", "#960000", "#ff1400"),
}
PRESET_ORDER = ("classic", "vivid", "warm", "formula", "cold", "red")


def parse(text):
    """'#rrggbb' or 'rrggbb' to (r, g, b). Raises ValueError otherwise."""
    if isinstance(text, (tuple, list)) and len(text) == 3:
        return tuple(max(0, min(255, int(v))) for v in text)
    s = str(text).strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"not a colour: {text!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(v))) for v in rgb))


def stops(raw):
    """Exactly three valid stops, always. Never rejects, only substitutes.

    Returns a fresh list every time: the settings dict is copied shallowly in
    places, and a shared list would let one caller's edit leak into everyone
    else's defaults.
    """
    out = list(DEFAULT_STOPS)
    if isinstance(raw, (list, tuple)):
        for i in range(3):
            if i < len(raw):
                try:
                    out[i] = to_hex(parse(raw[i]))
                except (ValueError, TypeError):
                    pass  # keep the default in this slot
    return out


def preset_of(current):
    """Name of the matching preset, or None for a custom scheme."""
    wanted = [c.lower() for c in stops(current)]
    for name in PRESET_ORDER:
        if [c.lower() for c in PRESETS[name]] == wanted:
            return name
    return None


def _blend(lo, hi, u):
    """Linear mix, then re-lift to the brightness the two stops call for.

    Plain RGB interpolation between two far-apart hues collapses through a
    dark, desaturated middle — green to red passes through olive, which on a
    saturated RGB LED reads as a broken segment. Rescaling to the interpolated
    peak channel keeps every mixed colour as bright as its neighbours.
    """
    mix = [lo[k] + (hi[k] - lo[k]) * u for k in range(3)]
    peak = max(lo) + (max(hi) - max(lo)) * u
    top = max(mix)
    if top > 0:
        mix = [v * peak / top for v in mix]
    return tuple(max(0, min(255, round(v))) for v in mix)


def _sample(points, t):
    a, b, c = points
    return _blend(a, b, t * 2) if t <= 0.5 else _blend(b, c, (t - 0.5) * 2)


def ramp(scheme, n):
    """Colour per LED, as blocks rather than a smooth gradient.

    A smooth ten-step gradient makes neighbouring LEDs differ by about a tenth
    of the range, which reads as a smear rather than a scale. Grouping them
    into an odd number of blocks keeps adjacent colours clearly apart and
    guarantees the middle stop actually appears.
    """
    points = [parse(s) for s in stops(scheme)]
    if n <= 1:
        return [points[-1]]           # a single LED is a shift light
    steps = max(3, (n // 2) | 1)
    return [_sample(points, round(i / (n - 1) * (steps - 1)) / (steps - 1))
            for i in range(n)]


def dim(colors, percent):
    """Scale for the on-screen preview, so it tracks the brightness setting."""
    factor = max(0.0, min(1.0, percent / 100.0))
    return [tuple(round(v * factor) for v in c) for c in colors]
