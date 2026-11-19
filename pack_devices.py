# -*- coding: utf-8 -*-
# Copyright 2026 JiChiRen Info. Tech.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# This file is part of QuillaImage.

"""CMSIS-Pack device discovery for the shared chip selector.

Wraps pyocd's CmsisPack parser to produce, for every device/variant in a
.pack: the part number shown to the operator, the Flash/RAM geometry used
by the packer, and a candidate pyocd target name for flashing.

The pack's memory map is authoritative for geometry. The pyocd target name
is only a *candidate*: it is matched against pyocd's built-in TARGET dict;
if there is no match the operator must choose/confirm the target manually.
Nothing here guesses an MCU model from a register ID.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PackDevice:
    """One selectable MCU from a CMSIS-Pack."""

    part_number: str          # e.g. PY32F040xB / GD32E230C8
    flash_size: int
    ram_size: int
    ram_start: int
    target_name: str          # pyocd target_override candidate (may be "")
    vendor: str
    label: str                # human-readable one-liner

    def geometry(self) -> dict:
        return {
            "flash_size": self.flash_size,
            "ram_size": self.ram_size,
            "ram_start": self.ram_start,
            "label": self.label,
        }


def _pyocd_target_names():
    """Return pyocd's built-in target names (lowercased) without hard-coding."""
    try:
        from pyocd.target import TARGET
        return {k.lower() for k in TARGET.keys()}
    except Exception:
        return set()


def _match_target(part_number: str, vendor: str, known: set) -> str:
    """Best-effort match of a pack part number to a pyocd target name.

    pyocd target names are typically lowercase part numbers (e.g.
    "py32f040xb"). Try the exact lowercase part number first, then a
    vendor-prefixed form. Return "" when nothing matches — the operator
    then keeps manual control instead of us guessing.
    """
    if not part_number:
        return ""
    pn = part_number.strip().lower()
    if pn in known:
        return pn
    # Some pyocd targets drop the vendor prefix or package suffix.
    for cand in (pn.rstrip("abcdefghijklmnopqrstuvwxyz"), pn.split("x")[0]):
        if cand and cand in known:
            return cand
    return ""


def load_pack_devices(pack_path: str) -> list:
    """Parse a .pack and return a list of PackDevice, sorted by name."""
    from pyocd.target.pack.cmsis_pack import CmsisPack

    pack = CmsisPack(pack_path)
    known = _pyocd_target_names()
    out = []
    seen = set()
    for dev in pack.devices:
        part = (getattr(dev, "part_number", "") or "").strip()
        if not part or part in seen:
            continue
        seen.add(part)
        flash = ram = 0
        ram_start = 0x20000000
        for region in dev.memory_map:
            name = (getattr(region, "name", "") or "").upper()
            length = int(getattr(region, "length", 0) or 0)
            if getattr(region, "type", None) is not None and "FLASH" in str(region.type).upper():
                flash = max(flash, length)
            elif "RAM" in str(getattr(region, "type", "")).upper() or name.startswith("IRAM"):
                ram = max(ram, length)
                ram_start = int(getattr(region, "start", ram_start))
        if not flash or not ram:
            continue  # geometry unknown -> cannot pack safely
        label = "%s (%d KiB Flash / %d KiB RAM)" % (
            part, flash // 1024, ram // 1024)
        out.append(PackDevice(
            part_number=part, flash_size=flash, ram_size=ram,
            ram_start=ram_start,
            target_name=_match_target(part, getattr(dev, "vendor", ""), known),
            vendor=getattr(dev, "vendor", "") or "",
            label=label))
    out.sort(key=lambda d: d.part_number)
    return out


def selftest() -> None:
    """Pure-logic self test (no .pack required)."""
    known = {"py32f040xb", "gd32f502ve"}
    assert _match_target("PY32F040xB", "", known) == "py32f040xb"
    assert _match_target("GD32F502VE", "", known) == "gd32f502ve"
    assert _match_target("UnknownMCU123", "", known) == ""
    dev = PackDevice("TEST", 0x20000, 0x4000, 0x20000000, "", "", "TEST")
    g = dev.geometry()
    assert g["flash_size"] == 0x20000 and g["ram_size"] == 0x4000
    print("pack_devices selftest: OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        for d in load_pack_devices(sys.argv[1]):
            print("%-24s flash=%6dK ram=%5dK target=%s" % (
                d.part_number, d.flash_size // 1024,
                d.ram_size // 1024, d.target_name or "(manual)"))
    else:
        print("usage: pack_devices.py <file.pack> | --selftest")
