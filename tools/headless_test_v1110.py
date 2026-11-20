# -*- coding: utf-8 -*-
"""Headless UI checks for v1.1.10: layout cleanup + bare-hex address fields.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v1110.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

STATE_DIR = tempfile.mkdtemp(prefix="quilla-test-")
gui.STATE_PATH = os.path.join(STATE_DIR, "state.json")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication([])

FAILS = []


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


def all_text(widget):
    parts = []
    for lbl in widget.findChildren(QLabel):
        parts.append(lbl.text())
    for btn in widget.findChildren(QPushButton):
        parts.append(btn.text())
    for gb in widget.findChildren(type(widget)):
        pass
    return "\n".join(parts)


win = gui.MainWindow()

# ---- 1. flash tab: no redundant explanation block ----
flash_tab = win._tabs.widget(0) if hasattr(win, "_tabs") else None
# find the tab widget generically
from PySide6.QtWidgets import QTabWidget  # noqa: E402
tabs = win.findChild(QTabWidget)
check("tab widget present", tabs is not None)
flash_tab = tabs.widget(0)
export_tab = tabs.widget(1)
ftxt = all_text(flash_tab)
check("flash explanation removed", "说明：烧录页只烧" not in ftxt)
check("flash layout-ref removed", "烧前校验用的 META" not in ftxt)

# ---- 2. address fields on one row, bare hex, label uses 0x ----
meta = win.ed_meta_base.text()
app_txt = win.ed_app_start.text()
check("meta field bare hex", not meta.lower().startswith("0x") and meta.isdigit() or
      all(c in "0123456789ABCDEFabcdef" for c in meta), meta)
check("app field bare hex", not app_txt.lower().startswith("0x"), app_txt)
labels = [lbl.text() for lbl in flash_tab.findChildren(QLabel)] + \
         [lbl.text() for lbl in export_tab.findChildren(QLabel)]
check("META label uses 0x", "META 起址 0x" in labels)
check("APP label uses 0x", "APP 起址 0x" in labels)
check("old hex label gone", not any("十六进制" in t for t in labels))

# ---- 2b. address parser accepts both bare and 0x-prefixed ----
bare = gui._parse_addr("08006000", "META")
pref = gui._parse_addr("0x08006000", "META")
check("bare hex parses", bare == 0x08006000, "0x%08X" % bare)
check("prefixed hex parses", pref == 0x08006000, "0x%08X" % pref)

# ---- 2c. state restore strips 0x from legacy saved addresses ----
import json  # noqa: E402
with open(gui.STATE_PATH, "w", encoding="utf-8") as f:
    json.dump({"meta_base": "0x08006000", "app_start": "0x08007000"}, f)
win._restore_state()
check("restore strips 0x prefix",
      win.ed_meta_base.text() == "08006000" and
      win.ed_app_start.text() == "08007000",
      win.ed_meta_base.text() + " / " + win.ed_app_start.text())

# ---- 4. three export buttons on one row, layout suffix removed ----
btn_texts = [b.text() for b in export_tab.findChildren(QPushButton)]
verify_btns = [b for b in export_tab.findChildren(QPushButton)
               if b.text().startswith("校验已有整片 bin")]
check("verify button label shortened",
      len(verify_btns) == 1 and "（按布局起址）" not in verify_btns[0].text())
export_btn = [b for b in export_tab.findChildren(QPushButton)
              if b.text().startswith("导出 boot + app")]
meta_btn = [b for b in export_tab.findChildren(QPushButton)
            if b.text().startswith("仅导出 256 B META")]
check("three export buttons found",
      len(export_btn) == 1 and len(meta_btn) == 1 and len(verify_btns) == 1)
if len(export_btn) == 1 and len(meta_btn) == 1 and len(verify_btns) == 1:
    parents = {id(export_btn[0].parent()), id(meta_btn[0].parent()),
               id(verify_btns[0].parent())}
    check("three buttons share one row container", len(parents) == 1)

# ---- 5/6. redundant texts removed ----
etxt = all_text(export_tab)
check("packaging-result blurb removed", "打包结果落盘后" not in etxt)
# groupbox titles are also text(): find any widget text via findChildren of QGroupBox
from PySide6.QtWidgets import QGroupBox  # noqa: E402
gtxt = "\n".join(g.title() for g in win.findChildren(QGroupBox))
check("two-page-public label removed", "（两页共用）" not in gtxt)

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.10 headless checks passed")
