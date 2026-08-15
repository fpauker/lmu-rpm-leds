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
import moza
from i18n import _


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
    for _ in range(2):
        for i in range(moza.RPM_LEDS):
            send(1 << i)
            time.sleep(0.06)
    print(_("    Flashing (all) ..."))
    for _ in range(3):
        send((1 << moza.RPM_LEDS) - 1)
        time.sleep(0.2)
        send(0)
        time.sleep(0.2)
    send(0)


def main():
    with moza.MozaSerial() as m:
        print(_("Port open: {path}").format(path=m.path))
        print(_("Setting rpm-indicator-mode = 1 (external telemetry)"))
        m.set_indicator_mode(1)
        time.sleep(0.3)

        sweep(m.set_leds, _("PHASE 1 — new command (send-rpm-telemetry, id 26/0)"))
        time.sleep(1.0)
        sweep(m.set_leds_legacy, _("PHASE 2 — legacy command (old-send-telemetry, id 253/222)"))

        print(_("\nResetting rpm-indicator-mode to 0"))
        m.set_indicator_mode(0)
        m.set_leds(0)
    print(_("Done."))


if __name__ == "__main__":
    main()
