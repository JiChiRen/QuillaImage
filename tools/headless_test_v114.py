# -*- coding: utf-8 -*-
"""Headless GUI test for v1.1.4: lazy pyocd, splash call, probe worker, progress fix.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v114.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402  (module import must NOT pull pyocd at top level)

# Redirect state persistence to a throwaway file: tests must never touch the
# real state.json beside the app (a previous run wiped its chip_pack entry).
import tempfile  # noqa: E402
gui.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="quilla-test-"), "state.json")
from PySide6.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


# ---- 1. lazy import: module top level must not have imported pyocd yet ----
check("gui module import does not import pyocd", "pyocd" not in sys.modules)
t0 = time.time()
mods, err = gui._ensure_pyocd()
t1 = time.time()
check("_ensure_pyocd success", mods is not None, "(%.1fs)" % (t1 - t0))
check("pyocd now cached", isinstance(gui._PY, dict))
mods2, err2 = gui._ensure_pyocd()
check("_ensure_pyocd cached (fast)", mods2 is mods and err2 == "")
from pyocd.probe.aggregator import PROBE_CLASSES  # noqa: E402
check("probe plugins registered", len(PROBE_CLASSES) > 0,
      "(%d classes)" % len(PROBE_CLASSES))

# ---- 2. FlashWorker._progress: pyocd sends 0.0..1.0 floats ----
app = QApplication([])
worker = gui.FlashWorker("dummy.bin", "", "", "", False)
seen = []


def collect(v):
    seen.append(v)


worker.progress_value.connect(collect)
for v in (0.0, 0.001, 0.37, 0.5, 0.999, 1.0):
    worker._progress(v)
check("progress conversion 0..1 -> 0..100",
      seen == [0, 0, 37, 50, 99, 100], str(seen))
worker._progress(1.5)          # out of bounds clamps to 100
worker._progress(-0.2)         # clamps to 0
check("progress clamped", seen[-2:] == [100, 0], str(seen[-2:]))

# ---- 3. window builds without pyocd in UI thread; probe enum goes async ----
win = gui.MainWindow()
check("MainWindow built", win is not None)
check("probe combo shows busy state",
      "正在枚举" in win.cmb_probe.currentText(),
      repr(win.cmb_probe.currentText()))
check("identity button disabled during enum", not win.btn_identity.isEnabled())

# wait for ProbeWorker to finish (up to 20 s)
loop = QEventLoop()
t = QTimer()
t.setSingleShot(True)
t.timeout.connect(loop.quit)
t.start(20000)
win._probe_worker.finished.connect(loop.quit)
loop.exec()
app.processEvents()
text = win.cmb_probe.currentText()
check("probe enum finished", "正在枚举" not in text, repr(text))
check("probe enum shows empty-or-device entry",
      ("列表为空" in text) or ("[" in text), repr(text))

# ---- 4. state persistence still intact ----
check("state keys no stale refs",
      all(hasattr(win, "ed_" + k) for k in win._STATE_KEYS),
      str(win._STATE_KEYS))

win._save_state()
check("state saved", os.path.isfile(gui.STATE_PATH))

# ---- 5. _target_ready with a pack path -> returns True without builtin check ----
win.ed_chip_pack.setText("")   # no pack; target py32f040xb not builtin -> dialog
from PySide6.QtWidgets import QMessageBox  # noqa: E402
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
ok = win._target_ready()
check("_target_ready warns for non-builtin target without pack", ok is False)

win.close()
print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all headless checks passed")
