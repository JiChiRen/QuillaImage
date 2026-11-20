# -*- coding: utf-8 -*-
"""Headless checks for v1.1.12: rate control, pack-based flash base,
auto model match, export path linkage, UI text cleanup.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v1112.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

gui.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="quilla-test-"), "state.json")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QGroupBox, QLabel, QMessageBox, QPushButton,
    QTabWidget,
)

app = QApplication.instance() or QApplication([])

FAILS = []
DLG = []
QMessageBox.information = staticmethod(lambda *a, **k: DLG.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: DLG.append(("warn", a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: DLG.append(("crit", a[2] if len(a) > 2 else "")))


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


win = gui.MainWindow()

# ---- 1. rate options + selection + frequency passed to workers ----
rate_labels = [win.cmb_rate.itemText(i) for i in range(win.cmb_rate.count())]
check("8 rate options", len(rate_labels) == 8, str(rate_labels))
check("max 10 MHz / min 128 kHz",
      rate_labels[0] == "10 MHz" and rate_labels[-1] == "128 kHz")
check("default 1 MHz", win._rate_hz() == 1000000)
win.cmb_rate.setCurrentIndex(0)
check("select 10 MHz", win._rate_hz() == 10000000)
check("rate change logged", any("探针速率已设为 10 MHz" in s for s in win.txt_log.toPlainText().splitlines()))

# worker frequency plumbing (FlashWorker)
fw = gui.FlashWorker("x.bin", "", "cortex_m", "", False,
                     frequency=2000000, auto_slow=True, base_address=0x08000000)
check("FlashWorker frequency stored", fw.frequency == 2000000)
rates = fw._retry_rates()
check("retry ladder lowers clock", rates[0] == 2000000 and rates[1] < rates[0],
      str(rates))
fw_noslow = gui.FlashWorker("x.bin", "", "cortex_m", "", False,
                            frequency=4000000, auto_slow=False)
check("no auto-slow keeps rate",
      fw_noslow._retry_rates() == [4000000, 4000000, 4000000])

# ---- 2. flash base from pack / manual toggle ----
from pack_devices import PackDevice  # noqa: E402
check("default base 0x08000000", win.ed_flash_base.text() == "08000000")
check("base read-only by default", win.ed_flash_base.isReadOnly())
win.selected_device = PackDevice("X", 0x20000, 0x4000, 0x20000000, "", "", "X", 0x08010000)
win._apply_pack_flash_base()
check("base follows pack flash_start", win.ed_flash_base.text() == "08010000",
      win.ed_flash_base.text())
win.cb_manual_base.setChecked(True)
check("manual unlocks base field", not win.ed_flash_base.isReadOnly())
win.ed_flash_base.setText("08001000")
check("manual base parsed", win._flash_base() == 0x08001000)
win.selected_device = PackDevice("Y", 0x20000, 0x4000, 0x20000000, "", "", "Y", 0x08005000)
win._apply_pack_flash_base()
check("manual prevents pack override", win.ed_flash_base.text() == "08001000")
win.cb_manual_base.setChecked(False)
win._apply_pack_flash_base()
check("uncheck restores pack base", win.ed_flash_base.text() == "08005000")

# ---- 3. auto model match / 未适配 ----
win.pack_devices = [
    PackDevice("GD32F502VE", 0x40000, 0x10000, 0x20000000, "", "", "VE"),
]
win.cmb_mcu.blockSignals(True)
win.cmb_mcu.clear()
for d in win.pack_devices:
    win.cmb_mcu.addItem(d.part_number, d.part_number)
win.cmb_mcu.blockSignals(False)

from mcu_identity import McuIdentity  # noqa: E402
DLG.clear()
ident = McuIdentity(part_number="GD32F502VE", vendor="GigaDevice")
win._identity_done(ident, "")
check("unique match auto-selects",
      win._mcu_target() == "gd32f502ve", win._mcu_target())
check("match message concise",
      any("配对成功" in d[1] for d in DLG), str(DLG))

DLG.clear()
ident2 = McuIdentity(part_number="GD32F502CB", vendor="GigaDevice")
win._identity_done(ident2, "")
check("unmatched -> 未适配 dialog",
      any("未适配" in d[1] for d in DLG), str(DLG))
check("unmatched keeps current model", win._mcu_target() == "gd32f502ve")

DLG.clear()
ident3 = McuIdentity(part_number="", vendor="")
win._identity_done(ident3, "")
check("no model -> 未适配 dialog",
      any("未适配" in d[1] for d in DLG), str(DLG))

# ---- 4. export path linkage (mock QFileDialog.save) ----
SAVE_PATH = [os.path.join(tempfile.mkdtemp(prefix="quilla-exp-"), "out.bin")]
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (SAVE_PATH[0], "*.bin"))
import package.image as core_pkg  # noqa: E402
core_pkg.make_combined = lambda *a, **k: (b"COMBINED", b"META", {"ok": True, "version": 1, "flag": core_pkg.FLAG_APP1, "seq": 1, "hash": b"\0"*32, "signed": False, "variant": "unknown", "app_msp": 0x20000000, "app_reset": 0x08000001, "qboot_msp": 0x20000000, "qboot_reset": 0x08000001, "meta_base": 0x08006000})
core_pkg._read_file = lambda p: b"\0" * 16
core_pkg._write_file = lambda p, d: None

win._build = lambda path, meta_out="": True   # bypass real build
win.ed_flash_image.setText("old.bin")
win._do_export()
check("export writes to chosen path",
      win.ed_flash_image.text() == SAVE_PATH[0], win.ed_flash_image.text())

# ---- 5. UI text cleanup ----
tabs = win.findChild(QTabWidget)
check("tab labels have no numbers",
      [tabs.tabText(i) for i in range(tabs.count())] == ["烧录", "打包"])
all_labels = [l.text() for l in win.findChildren(QLabel)]
group_titles = [g.title() for g in win.findChildren(QGroupBox)]
all_buttons = [b.text() for b in win.findChildren(QPushButton)]
check("no pyocd-target label", not any("pyocd 目标" in t for t in all_labels))
check("no 'only for packaging' text",
      not any("仅打包用" in t or "芯片容量取自" in t for t in group_titles))
check("flash button has no hardcoded address",
      not any("不重新生成" in t or "0x08000000" in t for t in all_buttons))
check("copy-log button present", any(t == "复制日志" for t in all_buttons))
check("identity button near MCU combo parent",
      win.btn_identity.parent() is win.cmb_mcu.parent())

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.12 headless checks passed")
