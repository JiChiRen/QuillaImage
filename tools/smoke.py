# -*- coding: utf-8 -*-
# Copyright 2026 JiChiRen Info. Tech.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# This file is part of QuillaImage.

"""Release smoke checklist for a pruned QuillaImage build.

This is a manual checklist, not an automated GUI driver. Run it from the
command line with real inputs; it prints each step and the pass criteria.
A pruned build is only valid if EVERY step passes.

    python tools/smoke.py

The runtime audit (tools/audit_runtime.py) must be taken AFTER step 1 has
exercised all features below; otherwise unexercised plugins look unused.
"""

STEPS = [
    ("1. Launch", "Run dist/QuillaImage/QuillaImage.exe; window appears, "
                  "no console window, stderr empty."),
    ("2. Pack / CHECK", "Pack tab: pick a CHECK QBOOT + APP, click preview "
                        "and export; 512 KiB image generated, variant CHECK."),
    ("3. Verify", "Verify tab: verify the image produced in step 2; passes."),
    ("4. Flash page", "Open flash tab; probe list populates. Do NOT flash "
                      "unless a board is attached."),
    ("5. Key tools", "Key tool page: derive public key from a known seed; "
                     "matches expected value."),
    ("6. Reboot", "Close and relaunch; state.json still in the app folder, "
                  "paths restored."),
]


def main() -> int:
    print("QuillaImage release smoke checklist")
    print("Run each step against dist/QuillaImage/. All must pass.\n")
    for title, desc in STEPS:
        print("[ ] %s\n    %s\n" % (title, desc))
    print("After steps 1-5, run: python tools/audit_runtime.py")
    print("Prune only file families that are unloaded AND not needed by "
          "any step above. Re-run this checklist after every prune.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
