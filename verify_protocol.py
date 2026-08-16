# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
#
# The Moza serial protocol constants used here (message start byte, checksum
# magic, device ids, command ids) were determined by the boxflat project
# <https://github.com/Lawstorant/boxflat>, GPL-3.0, through reverse engineering.
"""Cross-check our frame builder against boxflat's own MozaCommand.

Imports boxflat's code straight out of the flatpak and compares the bytes it
produces with ours for the commands we care about. Sends nothing.
"""

import os
import sys
import glob

INSTALLS = glob.glob(
    "/var/lib/flatpak/app/io.github.lawstorant.boxflat/*/stable/*/files") + \
    glob.glob(os.path.expanduser(
        "~/.local/share/flatpak/app/io.github.lawstorant.boxflat/*/stable/*/files"))
if not INSTALLS:
    sys.exit("Dieser Vergleich braucht boxflat als Flatpak:\n"
             "  flatpak install flathub io.github.lawstorant.boxflat")

FLATPAK = INSTALLS[0]
sys.path.insert(0, f"{FLATPAK}/share/boxflat")
sys.path.extend(glob.glob(f"{FLATPAK}/lib/python3.*/site-packages"))

import yaml
from boxflat.moza_command import MozaCommand, MOZA_COMMAND_WRITE

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moza

with open(f"{FLATPAK}/share/boxflat/data/serial.yml") as f:
    data = yaml.safe_load(f)

commands = data["commands"]
msg_start = int(data["message-start"])
magic = int(data["magic-value"])
dev_ids = data["device-ids"]

CASES = [
    ("wheel", "send-rpm-telemetry", 0b0000000011, moza.CMD_SEND_RPM_TELEMETRY),
    ("wheel", "send-rpm-telemetry", 0b1111111111, moza.CMD_SEND_RPM_TELEMETRY),
    ("wheel", "send-rpm-telemetry", 0, moza.CMD_SEND_RPM_TELEMETRY),
    ("wheel", "rpm-indicator-mode", 1, moza.CMD_RPM_INDICATOR_MODE),
    ("wheel", "rpm-indicator-mode", 0, moza.CMD_RPM_INDICATOR_MODE),
    ("wheel", "old-send-telemetry", 0b1010101010, moza.CMD_OLD_SEND_TELEMETRY),
    ("wheel", "telemetry-mode", 1, moza.CMD_TELEMETRY_MODE),
    ("wheel", "telemetry-mode", 0, moza.CMD_TELEMETRY_MODE),
]

ok = True
for device, name, value, our_cmd in CASES:
    group, cmd_id, nbytes = our_cmd

    ref = MozaCommand()
    ref.set_data_from_name(name, commands, device)
    ref.device_id = dev_ids[device]
    # array-typed commands take a byte list; int-typed take the number itself
    if commands[device][name]["type"] == "array":
        ref.payload = list(int(value).to_bytes(nbytes, "big"))
    else:
        ref.payload = value
    theirs = ref.prepare_message(msg_start, MOZA_COMMAND_WRITE, magic)

    ours = moza.build(group, dev_ids[device], cmd_id,
                      int(value).to_bytes(nbytes, "big"))

    match = "OK  " if theirs == ours else "FAIL"
    if theirs != ours:
        ok = False
    print(f"{match} {device}-{name} = {value}")
    print(f"     boxflat: {theirs.hex(' ')}")
    print(f"     ours   : {ours.hex(' ')}")

print()
print("ALLE FRAMES IDENTISCH" if ok else "ABWEICHUNG GEFUNDEN")
sys.exit(0 if ok else 1)
