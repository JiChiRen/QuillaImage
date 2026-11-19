# -*- coding: utf-8 -*-
# Copyright 2026 JiChiRen Info. Tech.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# This file is part of QuillaImage.

"""Read on-target MCU identity through an attached probe.

Design rule: NEVER claim a specific MCU model from a raw register value.
What is actually readable, reliably, through CMSIS-DAP:

  * DP IDCODE  (target.dp.dpidr.idr) -- identifies the Cortex-M core, not
    the MCU part number.
  * Vendor DBGMCU_IDCODE registers -- address varies by vendor/family.

Only when the imported CMSIS-Pack yields a *unique* candidate matching the
observed core/geometry do we present a model name, and even then the GUI
asks the operator to confirm. Otherwise raw IDs are shown verbatim and the
operator chooses the model manually.
"""

from __future__ import annotations

from dataclasses import dataclass


# Common DBGMCU_IDCODE addresses. Keyed by core family hint from DP IDCODE.
# These are well-known locations, but NOT guaranteed for every part: reads
# are best-effort and a failed read is reported, never substituted with a
# guessed value.
DBGMCU_ADDRESSES = (0x40015800, 0xE0042000, 0x40001000)

# DP IDCODE -> core display name (ARM Cortex DP IDR, well documented).
DP_CORE_NAMES = {
    0x0BC11477: "Cortex-M0+",
    0x0BC11478: "Cortex-M0+",
    0x0BB11477: "Cortex-M0",
    0x2BA01477: "Cortex-M3/M4 (DPv1)",
    0x2BA01478: "Cortex-M4 (DPv2)",
    0x0BC12477: "Cortex-M23",
    0x0BC10877: "Cortex-M55",
}


@dataclass
class McuIdentity:
    dp_idcode: int = 0
    core_name: str = ""
    part_number: str = ""        # pyocd target.part_number, if known
    vendor: str = ""
    dbgmcu: dict = None          # {addr_hex: value} for successful reads
    notes: str = ""

    def summary(self) -> str:
        lines = []
        if self.part_number:
            lines.append(("目标定义型号 : %s %s" % (
                self.vendor, self.part_number)).strip())
        if self.dp_idcode:
            lines.append("DP IDCODE : 0x%08X%s" % (
                self.dp_idcode,
                ("  (%s)" % self.core_name) if self.core_name else ""))
        if self.dbgmcu:
            for addr, val in self.dbgmcu.items():
                lines.append("DBGMCU @%s : 0x%08X" % (addr, val))
        else:
            lines.append("DBGMCU 寄存器：读取失败或该芯片不支持上述地址")
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines) if lines else "未读到任何芯片标识"


def read_identity(session) -> McuIdentity:
    """Read raw identity registers from an OPEN pyocd session.

    Never raises for a register read failure: each read is caught and the
    failure is recorded, so the caller always gets something to show.
    """
    ident = McuIdentity(dbgmcu={})
    target = session.target
    # pyocd's target object may already know its part number (from the
    # selected target definition or an imported CMSIS-Pack). That is the
    # most trustworthy source -- prefer it over raw registers.
    try:
        pn = getattr(target, "part_number", "") or ""
        vendor = getattr(target, "vendor", "") or ""
        if pn:
            ident.part_number = pn
            ident.vendor = vendor
    except Exception:
        pass
    try:
        ident.dp_idcode = int(target.dp.dpidr.idr)
        ident.core_name = DP_CORE_NAMES.get(ident.dp_idcode & 0xFFFFFFFF, "")
    except Exception as exc:
        ident.notes = "DP IDCODE 读取失败：%s" % exc

    for addr in DBGMCU_ADDRESSES:
        try:
            val = int(target.read_memory(addr, 32))
            ident.dbgmcu["0x%08X" % addr] = val
        except Exception:
            pass  # unsupported address: simply not reported
    return ident


def match_candidates(ident: McuIdentity, devices: list) -> list:
    """Return pack devices whose vendor IDCODE matches, if any.

    DBGMCU_IDCODE layout is vendor-specific (low 12 bits usually hold the
    part number, high 16 bits the vendor ID). We only compare the raw value
    against a device's declared IDCODE when the pack actually provides one;
    no heuristics, no rounding, no name similarity. Returns [] when there
    is no trustworthy match.
    """
    if not ident.dbgmcu or not devices:
        return []
    seen = set()
    vals = set(ident.dbgmcu.values())
    for dev in devices:
        # PackDevice has no idcode field today; if one is added later this
        # hook will use it. Until then: no fabricated matches.
        idcode = getattr(dev, "idcode", None)
        if idcode is None:
            continue
        if int(idcode) in vals and dev.part_number not in seen:
            seen.add(dev.part_number)
    return [d for d in devices if d.part_number in seen]


def selftest() -> None:
    d = DP_CORE_NAMES[0x0BC11477]
    assert d == "Cortex-M0+"
    ident = McuIdentity(dp_idcode=0x0BC11477, core_name=d,
                        dbgmcu={"0x40015800": 0x1234ABCD})
    assert "0x1234ABCD" in ident.summary()
    assert match_candidates(ident, []) == []
    print("mcu_identity selftest: OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest()
    else:
        print("usage: mcu_identity.py --selftest")
