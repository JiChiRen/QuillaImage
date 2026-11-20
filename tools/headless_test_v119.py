# -*- coding: utf-8 -*-
"""Headless test for v1.1.19: UI language switcher (zh / en) + i18n strings.

The exe/directory rename to QuillaImage is a build-script change and is
covered by the build itself, not here. All checks run without hardware.

Run with the interpreter that has PySide6 installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v119.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402
from i18n import tr, set_lang, get_lang, LANGS  # noqa: E402

TMP = tempfile.mkdtemp(prefix="quilla-test-v119-")
gui.STATE_PATH = os.path.join(TMP, "state.json")

app = QApplication.instance() or QApplication([])

FAILS = []


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


# ---- 1. i18n module basics ----
set_lang("zh")
check("default zh", get_lang() == "zh")
check("zh text", tr("btn_flash") == "烧录整片 bin")
set_lang("en")
check("en text", tr("btn_flash") == "Flash full bin")
check("en format", tr("pd_match_progress", 3, 31, "GD32F502CE")
      == "Matching 3/31: GD32F502CE")
set_lang("zh")
check("zh format", tr("pd_match_progress", 3, 31, "GD32F502CE")
      == "正在配对 3/31：GD32F502CE")
check("missing key echoes key", tr("__nope__") == "__nope__")
set_lang("fr")                          # 未知语言 -> 回退中文
check("unknown lang falls back zh", get_lang() == "zh")
set_lang("zh")

# ---- 2. window builds in zh, live switch to en ----
win = gui.MainWindow()
check("start zh: flash button", win.btn_flash.text() == "烧录整片 bin")
check("start zh: window title", "镜像合并" in win.windowTitle())
check("lang combo has 2 entries", win.cmb_lang.count() == len(LANGS))
check("lang combo shows zh", win.cmb_lang.currentData() == "zh")

# 插入一条假探针条目，避免 _retranslate 触发重新枚举探针
win.cmb_probe.addItem("FakeProbe  [ID0000]", "ID0000")

win.cmb_lang.setCurrentIndex(1)         # English
check("switch en: flash button", win.btn_flash.text() == "Flash full bin")
check("switch en: connect button", win.btn_conn.text() == "Connect probe")
check("switch en: identity button", win.btn_identity.text()
      == "Read from target")
check("switch en: erase checkbox", win.cb_erase.text()
      == "Chip erase before flashing")
check("switch en: window title", "pack & flash" in win.windowTitle())
check("switch en: tab 0", win.centralWidget().findChild(QTabWidget)
      .tabText(0) == "Flash")

# 语言持久化：切换后立即落盘
with open(gui.STATE_PATH, "r", encoding="utf-8") as f:
    saved = json.load(f)
check("state.json has lang=en", saved.get("lang") == "en",
      str(saved.get("lang")))

# ---- 3. switch back to zh ----
win.cmb_lang.setCurrentIndex(0)
check("back zh: flash button", win.btn_flash.text() == "烧录整片 bin")
check("back zh: window title", "镜像合并" in win.windowTitle())

# ---- 4. saved language is picked up at startup (no zh flash) ----
win.cmb_lang.setCurrentIndex(1)         # state.json -> en
win2 = gui.MainWindow()
check("restart en: flash button", win2.btn_flash.text() == "Flash full bin")
check("restart en: lang combo index", win2.cmb_lang.currentIndex() == 1)
win2.cmb_lang.setCurrentIndex(0)        # 恢复中文，不污染后续测试环境

# ---- 5. every key has both zh and en entries (no silent fallbacks) ----
import i18n  # noqa: E402
missing = [k for k, v in i18n._STRINGS.items()
           if not v.get("zh") or not v.get("en")]
check("all keys bilingual", not missing, str(missing))

print()
if FAILS:
    print("FAILED: %d checks: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL OK")
