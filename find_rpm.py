# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Locate LMU's engine RPM inside its shared memory, from the Linux side.

LMU creates a Windows file mapping called "LMU_Data" (see the game's own
Support/SharedMemoryInterface/SharedMemoryInterface.hpp). Under Proton, Wine
backs such mappings with plain files in /dev/shm, so we can read them natively.

Inside TelemInfoV01 the two fields we want sit at a fixed distance:
    mEngineRPM     @ +368
    mEngineMaxRPM  @ +544      -> exactly 176 bytes apart
That pair is the signature we search for. Run it while sitting in the car.
"""

import glob
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import _

RPM_TO_MAX = 176  # byte distance between mEngineRPM and mEngineMaxRPM


def lmu_shm_files():
    """The /dev/shm objects LMU and its plugin host have mapped.

    LMU 1.4 does not load plugins in the game exe — a separate PluginsAdapter.exe
    hosts them, so its mappings matter just as much.
    """
    files = set()
    for pattern in ("Le Mans Ultimate.exe", "PluginsAdapter.exe"):
        pids = subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            try:
                with open(f"/proc/{pid}/maps") as f:
                    for line in f:
                        if "/dev/shm/" in line and "Valve" not in line:
                            files.add(line.rsplit(maxsplit=1)[-1].strip())
            except OSError:
                continue
    return sorted(files)


def wine_mapping_fds():
    """Wine backs named file mappings with memfd, invisible in the filesystem.

    They are still readable through /proc/<pid>/fd/<n> as the owning user, so
    that is where LMU_Data actually lives under Proton.
    """
    paths = []
    for pattern in ("Le Mans Ultimate.exe", "PluginsAdapter.exe"):
        pids = subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            for fd in glob.glob(f"/proc/{pid}/fd/*"):
                try:
                    if "memfd:wine-mapping" in os.readlink(fd):
                        paths.append(fd)
                except OSError:
                    continue
    return sorted(paths)


def all_shm_files():
    return sorted(p for p in glob.glob("/dev/shm/*")
                  if "Valve" not in p and os.path.isfile(p))


def scan(path):
    """Offsets whose (rpm, maxrpm) pair looks like a plausible engine."""
    from array import array
    hits = []
    try:
        raw = open(path, "rb").read()
    except OSError:
        return hits

    stride = RPM_TO_MAX // 8  # field distance expressed in doubles
    for base in range(8):  # the mapping's own alignment is unknown
        buf = raw[base:]
        buf = buf[:len(buf) - (len(buf) % 8)]
        if len(buf) < RPM_TO_MAX + 8:
            continue
        vals = array("d")
        vals.frombytes(buf)
        for i in range(len(vals) - stride):
            rpm = vals[i]
            if not (300.0 < rpm < 20000.0):
                continue
            mx = vals[i + stride]
            if not (3000.0 < mx < 20000.0):
                continue
            if rpm > mx * 1.05:  # revs above the limiter make no sense
                continue
            hits.append((base + i * 8, rpm, mx))
    return hits


def main():
    everywhere = "--all" in sys.argv
    files = all_shm_files() if everywhere else lmu_shm_files()
    files += wine_mapping_fds()
    if not files:
        print(_("LMU is not running (or maps nothing in /dev/shm)."))
        return 1

    print(_("{count} shared memory objects from LMU:").format(count=len(files)))
    for f in files:
        try:
            print(f"   {f}  {os.path.getsize(f)} bytes")
        except OSError:
            pass

    print(_("\nSearching for the RPM/MaxRPM pair ..."))
    found = {}
    for path in files:
        hits = scan(path)
        if hits:
            found[path] = hits
            print(_("\n  {path}: {count} candidate(s)").format(path=path, count=len(hits)))
            for off, rpm, mx in hits[:12]:
                print(f"     offset {off:>9}  rpm={rpm:9.1f}  max={mx:9.1f}")

    if not found:
        print(_("\n  Nothing found. Are you in the car, on track?"))
        return 1

    # Watch the candidates: real RPM moves, stale copies do not.
    print(_("\nWatching for 3 seconds — which value moves?"))
    for path, hits in found.items():
        offs = [h[0] for h in hits[:12]]
        series = {o: [] for o in offs}
        for _ in range(6):
            data = open(path, "rb").read()
            for o in offs:
                series[o].append(struct.unpack_from("<d", data, o)[0])
            time.sleep(0.5)
        for o in offs:
            vals = series[o]
            spread = max(vals) - min(vals)
            tag = _("  <== MOVING") if spread > 5 else ""
            print(f"   {path} +{o}: {' '.join(f'{v:7.0f}' for v in vals)}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
