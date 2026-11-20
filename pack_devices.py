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
    flash_start: int = 0x08000000   # base address of the flash region

    def geometry(self) -> dict:
        return {
            "flash_size": self.flash_size,
            "ram_size": self.ram_size,
            "ram_start": self.ram_start,
            "flash_start": self.flash_start,
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
    """Return the pyocd target_override name for a pack device.

    With a CMSIS-Pack loaded, pyocd accepts the pack's part number
    (lowercased) as target_override and generates the target from the pack
    — e.g. GD32F50x packs are not in pyocd's builtin TARGET dict at all.
    So the part number itself is the correct candidate, not a lookup in
    the builtin dict. ``known`` is kept for API compatibility / future use.
    """
    if not part_number:
        return ""
    return part_number.strip().lower()


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
        flash_start = 0x08000000
        for region in dev.memory_map:
            name = (getattr(region, "name", "") or "").upper()
            length = int(getattr(region, "length", 0) or 0)
            if getattr(region, "type", None) is not None and "FLASH" in str(region.type).upper():
                if length > flash:
                    flash = length
                    flash_start = int(getattr(region, "start", flash_start))
            elif "RAM" in str(getattr(region, "type", "")).upper() or name.startswith("IRAM"):
                ram = max(ram, length)
                ram_start = int(getattr(region, "start", ram_start))
        if not flash or not ram:
            continue  # geometry unknown -> cannot pack safely
        label = "%s (%d KiB Flash / %d KiB RAM)" % (
            part, flash // 1024, ram // 1024)
        out.append(PackDevice(
            part_number=part, flash_size=flash, ram_size=ram,
            ram_start=ram_start, flash_start=flash_start,
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
    # Not in pyocd builtin dict: still the pack part number, which pyocd
    # uses to generate a target from the loaded pack.
    assert _match_target("GD32F502CE", "", known) == "gd32f502ce"
    assert _match_target("", "", known) == ""
    dev = PackDevice("TEST", 0x20000, 0x4000, 0x20000000, "", "", "TEST")
    g = dev.geometry()
    assert g["flash_size"] == 0x20000 and g["ram_size"] == 0x4000
    assert g["flash_start"] == 0x08000000
    dev2 = PackDevice("T2", 0x20000, 0x4000, 0x20000000, "", "", "T2", 0x08010000)
    assert dev2.geometry()["flash_start"] == 0x08010000
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
