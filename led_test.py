# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
#
# The Moza serial protocol constants used here (message start byte, checksum
# magic, device ids, command ids) were determined by the boxflat project
# <https://github.com/Lawstorant/boxflat>, GPL-3.0, through reverse engineering.
"""Light the wheel's RPM LEDs directly, to find which command variant it obeys.

Runs two phases — the new command (id 26,0) and the legacy one (id 253,222) —
announcing each so you can see which one the rim reacts to. Restores the
indicator mode on exit.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import moza
from i18n import _


def profile_from_argv():
    """The configured wheel profile, unless --profile=... says otherwise."""
    profile = config.load()["profile"]
    for arg in sys.argv[1:]:
        if arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
    return profile


def sweep(send, label):
    print(f"\n>>> {label}")
    print(_("    Filling 1->10 ..."))
    for i in range(moza.RPM_LEDS + 1):
        send((1 << i) - 1)
        time.sleep(0.12)
    print(_("    Emptying 10->0 ..."))
    for i in reversed(range(moza.RPM_LEDS + 1)):
        send((1 << i) - 1)
        time.sleep(0.12)
    print(_("    Chase ..."))
    for _pass in range(2):   # not "_": that is the translation function
        for i in range(moza.RPM_LEDS):
            send(1 << i)
            time.sleep(0.06)
    print(_("    Flashing (all) ..."))
    for _pass in range(3):
        send((1 << moza.RPM_LEDS) - 1)
        time.sleep(0.2)
        send(0)
        time.sleep(0.2)
    send(0)


def main():
    with moza.MozaSerial(profile=profile_from_argv()) as m:
        print(_("Port open: {path} (profile: {profile})").format(
            path=m.path, profile=m.profile))
        print(_("Setting rpm-indicator-mode = 1 (external telemetry)"))
        m.set_indicator_mode(1)
        if m.profile != "legacy":
            # Without a colour table a fresh base lights every segment black.
            # Legacy rims keep their colours persistently, so the test leaves
            # those alone — boxflat's own legacy test does the same.
            m.set_rpm_colors()
        time.sleep(0.3)

        if m.profile == "legacy":
            sweep(m.set_leds,
                  _("LEGACY profile — old-send-telemetry (id 253/222) at the base id"))
        else:
            sweep(m.set_leds, _("PHASE 1 — new command (send-rpm-telemetry, id 26/0)"))
            time.sleep(1.0)
            sweep(m.set_leds_legacy, _("PHASE 2 — legacy command (old-send-telemetry, id 253/222)"))

        print(_("\nResetting rpm-indicator-mode to 0"))
        m.set_indicator_mode(0)
        m.set_leds(0)
    print(_("Done."))


if __name__ == "__main__":
    main()
