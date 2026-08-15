# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
#
# The Moza serial protocol constants used here (message start byte, checksum
# magic, device ids, command ids) were determined by the boxflat project
# <https://github.com/Lawstorant/boxflat>, GPL-3.0, through reverse engineering.
"""Map the wheel's RPM LEDs: how many are ours, and in which order.

Phase 1 blanks everything, so you can see which LEDs are NOT under our control.
Phase 2 walks a single lit bit from 0 to 15, one at a time, so you can count the
LEDs and see which end the bar starts at.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moza
from i18n import _

HOLD = 1.2


def main():
    legacy = "--legacy" in sys.argv
    with moza.MozaSerial() as m:
        m.set_indicator_mode(1)
        send = m.set_leds_legacy if legacy else m.set_leds
        time.sleep(0.3)

        print(_("PHASE 1: everything OFF — 6 seconds."))
        print(_("         Note which LEDs stay lit anyway."))
        for i in range(6, 0, -1):
            send(0)
            print(f"   {i} ...", flush=True)
            time.sleep(1.0)

        print(_("\nPHASE 2: one LED after another, bit 0 to 15."))
        print(_("         Count how many distinct LEDs light up"))
        print(_("         and whether the run starts left or right.\n"))
        for bit in range(16):
            send(1 << bit)
            print(f"   Bit {bit:2}", flush=True)
            time.sleep(HOLD)

        send(0)
        m.set_indicator_mode(0)
    print(_("\nDone — all off, mode reset."))


if __name__ == "__main__":
    main()
