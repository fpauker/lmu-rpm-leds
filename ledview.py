# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Cairo drawing for the LED bar and the curve chart.

Kept apart from the window code so the drawing can be reasoned about — and
tweaked — without touching any GTK plumbing.
"""

import math

# Rev-light colouring: green through the low segments, amber, then red at the
# shift point. Index into these by LED position.
def led_colour(index, total):
    frac = index / max(total - 1, 1)
    if frac < 0.5:
        return (0.20, 0.85, 0.35)
    if frac < 0.8:
        return (1.00, 0.75, 0.10)
    return (1.00, 0.25, 0.25)


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_bar(cr, width, height, mask, total, dark=True):
    """The rev bar as the wheel shows it: lit segments left to right."""
    gap = max(3.0, width * 0.008)
    seg_w = (width - gap * (total - 1)) / total
    seg_h = min(height, seg_w * 2.2)
    y = (height - seg_h) / 2
    radius = min(seg_w, seg_h) * 0.22

    for i in range(total):
        x = i * (seg_w + gap)
        lit = bool(mask >> i & 1)
        r, g, b = led_colour(i, total)
        _rounded_rect(cr, x, y, seg_w, seg_h, radius)
        if lit:
            cr.set_source_rgb(r, g, b)
            cr.fill_preserve()
            cr.set_source_rgba(r, g, b, 0.35)
            cr.set_line_width(3)
            cr.stroke()
        else:
            # Unlit segments keep a hint of their colour so the layout reads
            # even when the bar is dark.
            if dark:
                cr.set_source_rgba(r * 0.30, g * 0.30, b * 0.30, 0.55)
            else:
                cr.set_source_rgba(r * 0.45 + 0.35, g * 0.45 + 0.35, b * 0.45 + 0.35, 0.75)
            cr.fill()


def draw_curve(cr, width, height, cfg, marker=None, dark=True):
    """How many LEDs are lit across the rev range, plus the thresholds.

    x runs from LOW to 1.02 of the limiter; y is the number of lit LEDs.
    `marker` is the current rev fraction, drawn as a vertical line.
    """
    LOW = 0.50
    HIGH = 1.02
    total = cfg["leds"]
    pad_l, pad_r, pad_t, pad_b = 34.0, 10.0, 10.0, 22.0
    plot_w = max(width - pad_l - pad_r, 1.0)
    plot_h = max(height - pad_t - pad_b, 1.0)

    fg = (0.85, 0.87, 0.90) if dark else (0.18, 0.20, 0.24)
    muted = (*fg, 0.30)

    def sx(frac):
        return pad_l + (frac - LOW) / (HIGH - LOW) * plot_w

    def sy(leds):
        return pad_t + plot_h - (leds / total) * plot_h

    # frame
    cr.set_source_rgba(*muted)
    cr.set_line_width(1)
    cr.rectangle(pad_l, pad_t, plot_w, plot_h)
    cr.stroke()

    # x labels every 10 %
    cr.set_font_size(10)
    pct = 50
    while pct <= 100:
        x = sx(pct / 100)
        cr.set_source_rgba(*fg, 0.18)
        cr.move_to(x, pad_t)
        cr.line_to(x, pad_t + plot_h)
        cr.stroke()
        cr.set_source_rgba(*fg, 0.65)
        cr.move_to(x - 9, height - 7)
        cr.show_text(f"{pct}%")
        pct += 10

    cr.set_source_rgba(*fg, 0.65)
    cr.move_to(2, sy(total) + 4)
    cr.show_text(f"{total}")
    cr.move_to(2, sy(0) + 4)
    cr.show_text("0")

    # The staircase, drawn from its actual step edges rather than by sampling
    # the curve: it has exactly `total` steps, so there is nothing to
    # approximate, and one stroke per step instead of a few hundred keeps the
    # repaint cheap enough to run at preview rate.
    start, end = cfg["start"], cfg["end"]
    span = max(end - start, 1e-6)
    cr.set_line_width(2.5)

    # the dark stretch below the first LED
    cr.set_source_rgba(*fg, 0.35)
    cr.move_to(sx(LOW), sy(0))
    cr.line_to(sx(min(start, HIGH)), sy(0))
    cr.stroke()

    for step in range(total):
        frac_from = start + span * step / total
        frac_to = start + span * (step + 1) / total if step < total - 1 else HIGH
        if frac_from > HIGH:
            break
        x0, x1 = sx(max(frac_from, LOW)), sx(min(frac_to, HIGH))
        y = sy(step + 1)
        cr.set_source_rgb(*led_colour(step, total))
        cr.move_to(x0, sy(step))    # the riser
        cr.line_to(x0, y)
        cr.line_to(x1, y)           # the tread
        cr.stroke()

    # thresholds
    for frac, label, rgb in ((start, "Start", (0.20, 0.85, 0.35)),
                             (end, "Voll", (1.00, 0.75, 0.10)),
                             (cfg["blink"], "Blinken", (1.00, 0.25, 0.25))):
        if not (LOW <= frac <= HIGH):
            continue
        x = sx(frac)
        cr.set_source_rgba(*rgb, 0.85)
        cr.set_line_width(1.5)
        cr.set_dash([3, 3])
        cr.move_to(x, pad_t)
        cr.line_to(x, pad_t + plot_h)
        cr.stroke()
        cr.set_dash([])
        cr.set_font_size(9)
        cr.move_to(x + 3, pad_t + 10)
        cr.show_text(label)

    if marker is not None and LOW <= marker <= HIGH:
        x = sx(marker)
        cr.set_source_rgb(*fg)
        cr.set_line_width(2)
        cr.move_to(x, pad_t)
        cr.line_to(x, pad_t + plot_h)
        cr.stroke()
