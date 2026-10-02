# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
#
# The Moza serial protocol constants used here (message start byte, checksum
# magic, device ids, command ids) were determined by the boxflat project
# <https://github.com/Lawstorant/boxflat>, GPL-3.0, through reverse engineering.
"""Moza serial protocol — minimal writer for the RPM LED bar.

Protocol constants and command IDs were taken from boxflat's serial.yml
(github.com/Lawstorant/boxflat), which reverse-engineered them.

Frame layout:  7E | len | group | dev_id | cmd_id... | payload... | checksum
    len      = len(cmd_id) + len(payload)
    checksum = (13 + sum(all preceding bytes)) % 256

Stdlib only — CDC-ACM port configured via termios, no pyserial needed.
"""

import glob
import os
import termios

import palette
from i18n import _

MSG_START = 0x7E
MAGIC = 13

DEV_WHEEL = 23   # modern rims (R9-class bases) answer here
DEV_BASE = 19    # legacy rims (ES on R3/R5 bases): the base proxies to the rim
DEV_DASH = 20

# Which protocol family and device id to talk to. "auto" decides from the
# base's USB name, the same heuristic moza-rev uses and confirmed on hardware
# there: an R3/R5-generation base carries an old-protocol rim behind the base
# id, everything else is a modern rim at the wheel id. boxflat reaches the
# same end state by cycling ids {23, 21, 19} until a read answers.
PROFILES = ("auto", "modern", "legacy")

# group, cmd_id, payload_len
CMD_SEND_RPM_TELEMETRY = (63, [26, 0], 2)  # wheel, new protocol: LED bitmask
CMD_OLD_SEND_TELEMETRY = (65, [253, 222], 4)  # wheel, legacy protocol
CMD_RPM_INDICATOR_MODE = (63, [4], 1)  # legacy rims: 1 = external telemetry
CMD_TELEMETRY_MODE = (63, [28, 0], 1)  # current rims: 1 = external telemetry
CMD_TELEMETRY_RPM_COLORS = (63, [25, 0], 20)  # 5 LEDs per frame, 2 frames
CMD_RPM_BRIGHTNESS = (63, [27, 0, 255], 1)     # current rims, percent
CMD_OLD_RPM_BRIGHTNESS = (63, [20, 0], 1)      # legacy rims
# Legacy rims store their colours persistently, one frame per LED:
# group 63, id [21, 0, n], 3 bytes RGB (boxflat: old-rpm-color1..10).
CMD_OLD_RPM_COLOR_BASE_ID = [21, 0]
CMD_RPM_DISPLAY_MODE = (63, [7], 1)
CMD_DASH_SEND_TELEMETRY = (65, [253, 222], 4)

RPM_LEDS = 10

# Without a colour table the rim happily accepts the bitmask and lights every
# segment black, which looks exactly like a dead wheel — and the base forgets
# the table when it is powered off.
DEFAULT_RPM_COLORS = palette.ramp(palette.DEFAULT_STOPS, RPM_LEDS)


def build(group: int, dev_id: int, cmd_id: list[int], payload: bytes) -> bytes:
    """Assemble one protocol frame."""
    msg = bytearray([MSG_START, len(cmd_id) + len(payload), group, dev_id])
    msg.extend(cmd_id)
    msg.extend(payload)
    msg.append((MAGIC + sum(msg)) % 256)
    return bytes(msg)


def find_base(pattern: str = None) -> str:
    """Path of the wheelbase serial port.

    Matched by name rather than hardcoded, because the by-id path carries the
    device's serial number and every base gets a different one. Pedals, hubs
    and handbrakes also show up as Gudsen serial devices, so the match is
    narrowed to the base itself.

    Override with the MOZA_SERIAL_PORT environment variable.
    """
    override = os.environ.get("MOZA_SERIAL_PORT")
    if override:
        return override

    patterns = [pattern] if pattern else [
        "/dev/serial/by-id/usb-Gudsen_MOZA_*Base*-if00",
        "/dev/serial/by-id/usb-Gudsen_MOZA_*Base*",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            return matches[0]

    raise FileNotFoundError(_(
        "No Moza wheelbase found. Is it switched on and connected? "
        "As a last resort, name the port in MOZA_SERIAL_PORT."))


def resolve_profile(profile: str, path: str) -> str:
    """Turn "auto" into "modern" or "legacy" using the base's USB name.

    An R3/R5-generation base ("_R3_"/"_R5_" in the by-id name, which also
    matches "R5_Pro") carries an old-protocol rim addressed via the base id —
    lesson of issue #1, where every frame sent to the wheel id vanished.
    """
    if profile in ("modern", "legacy"):
        return profile
    name = os.path.basename(path or "").lower()
    return "legacy" if ("_r3_" in name or "_r5_" in name) else "modern"


class MozaSerial:
    """Write-only handle on the wheelbase serial port.

    boxflat opens the port non-exclusively too, so both can be connected at
    once — but boxflat may overwrite settings it manages.
    """

    def __init__(self, path: str = None, profile: str = "auto"):
        self.path = path or find_base()
        # The rim's address and command family are a property of the base
        # generation, so they are fixed when the port is opened. Changing the
        # profile means reopening.
        self.profile = resolve_profile(profile, self.path)
        self.dev_id = DEV_BASE if self.profile == "legacy" else DEV_WHEEL
        # What a legacy rim was last given — its colours and brightness go to
        # non-volatile storage, so identical values must not be rewritten on
        # every re-assert (the daemon re-asserts every 2 s; that would wear
        # the rim's flash out in weeks).
        self._written = {}
        self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY)
        self._configure()

    def _configure(self):
        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        # raw 8N1, no flow control, no modem-control lines
        iflag = 0
        oflag = 0
        lflag = 0
        cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
        ispeed = ospeed = termios.B115200
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 5
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])

    def send(self, cmd, dev_id: int, value: int, endian: str = "big"):
        group, cmd_id, nbytes = cmd
        payload = int(value).to_bytes(nbytes, endian)
        os.write(self.fd, build(group, dev_id, cmd_id, payload))

    def send_bytes(self, cmd, dev_id: int, payload: bytes):
        """For array-typed commands, where the payload is not a number."""
        group, cmd_id, nbytes = cmd
        if len(payload) != nbytes:
            raise ValueError(f"{nbytes} bytes expected, got {len(payload)}")
        os.write(self.fd, build(group, dev_id, cmd_id, payload))

    def set_indicator_mode(self, mode: int):
        """Hand the rim's LEDs to external telemetry.

        Two generations of rim disagree on how to ask. Current ones use
        telemetry-mode; older ones only understand rpm-indicator-mode, which is
        what boxflat files under "old".

        Order matters: the legacy command goes first, the current one last. The
        other way round the bar stayed dark until boxflat's own rev-light test
        was pressed — that test sets only telemetry-mode, and sending the
        legacy command after it evidently put the rim back under the base's own
        control.

        A legacy rim gets only its own command: telemetry-mode does not exist
        there, and issue #1 showed an R5 base can wedge on frames it does not
        know.
        """
        self.send(CMD_RPM_INDICATOR_MODE, self.dev_id, mode)
        if self.profile != "legacy":
            self.send(CMD_TELEMETRY_MODE, self.dev_id, mode)

    def set_rpm_colors(self, colors=None):
        """Colour table for the rev LEDs, five per frame.

        Layout per entry is (index, r, g, b); the base wants two frames of
        twenty bytes, LEDs 0-4 then 5-9. A legacy rim instead takes one frame
        of plain RGB per LED and stores the table persistently.
        """
        colors = [palette.parse(c) for c in (colors or DEFAULT_RPM_COLORS)][:RPM_LEDS]
        while len(colors) < RPM_LEDS:
            # Repeat the caller's own top colour, not ours: padding a short
            # ramp with a foreign red would put stray colours on LEDs the rim
            # may well light.
            colors.append(colors[-1] if colors else DEFAULT_RPM_COLORS[-1])
        if self.profile == "legacy":
            # Persistent storage: skip when nothing changed. Re-asserting
            # against boxflat is a volatile-table problem the legacy rim
            # does not have.
            if self._written.get("colors") != colors:
                for index, rgb in enumerate(colors):
                    cmd = (63, CMD_OLD_RPM_COLOR_BASE_ID + [index], 3)
                    self.send_bytes(cmd, self.dev_id, bytes(rgb))
                self._written["colors"] = colors
            return
        flat = bytearray()
        for index, (r, g, b) in enumerate(colors):
            flat.extend((index, r, g, b))
        for half in (flat[:20], flat[20:40]):
            self.send_bytes(CMD_TELEMETRY_RPM_COLORS, self.dev_id, bytes(half))

    def set_rpm_brightness(self, percent: int):
        """Rim brightness in percent. Same two-generation split as the mode."""
        percent = max(0, min(100, int(percent)))
        if self.profile == "legacy":
            # Stored persistently, like the colours — write only on change.
            if self._written.get("brightness") == percent:
                return
            self._written["brightness"] = percent
            self.send(CMD_OLD_RPM_BRIGHTNESS, self.dev_id, percent)
            return
        self.send(CMD_RPM_BRIGHTNESS, self.dev_id, percent)

    def set_leds(self, mask: int):
        """Light LEDs by bitmask — bit 0 is the leftmost of 10.

        The wheel takes the 16-bit mask little-endian; sending it big-endian
        splits the bar into two halves at the byte boundary. The legacy
        command, by contrast, is four bytes big-endian.
        """
        if self.profile == "legacy":
            self.set_leds_legacy(mask)
        else:
            self.send(CMD_SEND_RPM_TELEMETRY, self.dev_id, mask, "little")

    def set_leds_legacy(self, mask: int):
        self.send(CMD_OLD_SEND_TELEMETRY, self.dev_id, mask)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def mask_for_fraction(fraction: float, leds: int = RPM_LEDS) -> int:
    """Fill the bar left-to-right; fraction is rpm/redline, clamped to 0..1."""
    lit = round(max(0.0, min(1.0, fraction)) * leds)
    return (1 << lit) - 1
