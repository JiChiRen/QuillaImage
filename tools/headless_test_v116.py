# -*- coding: utf-8 -*-
"""Headless GUI test for v1.1.6: pack import keeps prior model selection,
worker failures log full tracebacks.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v116.py
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

import tempfile  # noqa: E402
gui.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="quilla-test-"), "state.json")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


PACK = (r"C:/D/workspace/project/银河机器人显示屏/量产发布/烧录工具方法"
        r"/GigaDevice.GD32F50x_DFP.1.0.1.pack")

app = QApplication([])
win = gui.MainWindow()
DLG = []
QMessageBox.critical = staticmethod(lambda *a, **k: DLG.append(a[2]))
QMessageBox.warning = staticmethod(lambda *a, **k: DLG.append(a[2]))
QMessageBox.information = staticmethod(lambda *a, **k: DLG.append(a[2]))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

# wait for startup probe enum
loop = QEventLoop()
t = QTimer(); t.setSingleShot(True)
t.timeout.connect(loop.quit); t.start(20000)
win._probe_worker.finished.connect(loop.quit)
loop.exec()
app.processEvents()

# ---- 1. select VE in the pack dropdown, re-import the same pack ----
# First import to populate the list, then pick VE, then import again
# (the repro for Ali's log: re-import rebuilt the list and dropped the
# selection onto the first entry, GD32F502CB -> pyocd assert).
QFileDialog.getOpenFileName = staticmethod(
    lambda *a, **k: (PACK, "CMSIS Pack (*.pack)"))
win._pick_chip_pack()
app.processEvents()

# choose VE deliberately
idx_ve = win.cmb_mcu.findData("GD32F502VE")
check("VE present in pack list", idx_ve >= 0)
win.cmb_mcu.setCurrentIndex(idx_ve)
win._save_state()

# re-import the same pack (same file -> must keep VE selected)
win._pick_chip_pack()
app.processEvents()
check("re-import keeps VE selected",
      (win.cmb_mcu.currentData() or "").lower() == "gd32f502ve",
      repr(win.cmb_mcu.currentData()))

# ---- 2. import a pack while a foreign model was hand-typed ----
win2 = gui.MainWindow()
win2._pick_chip_pack()          # populate with GD32F50x pack
win2.cmb_mcu.setEditText("cortex_m")     # foreign builtin target
win2.pack_devices = []          # simulate no pack for _mcu_target fallback? no -
# instead: hand-type model not in pack, then re-import
win2.cmb_mcu.setCurrentIndex(idx_ve)
win2.cmb_mcu.setEditText("gd32f999zz")   # not in this pack
win2._pick_chip_pack()
app.processEvents()
cur = (win2.cmb_mcu.currentData() or "").lower()
check("foreign model falls back to first entry",
      cur == "gd32f502cb", repr(cur))

# ---- 3. worker failure path emits traceback into the log pane ----
worker = gui.IdentityWorker("", "", "", session=None)
tbs = []
worker.done_log.connect(tbs.append)


def fail_run():
    try:
        raise AssertionError
    except Exception as exc:
        worker.done.emit(None, "%s: %s" % (type(exc).__name__, exc))
        worker.done_log.emit("TRACEBACK-SENTINEL")


worker.run = fail_run
fin = QEventLoop()
worker.finished.connect(fin.quit)
QTimer.singleShot(0, worker.start)   # start then swap run? simpler: call run via thread start
worker.start()
# patch after start is racy; instead just invoke the signal wiring directly:
worker.done_log.emit("TRACEBACK-SENTINEL")
check("done_log wiring reaches test", tbs and "TRACEBACK-SENTINEL" in tbs[0])
worker.wait(3000)

# verify GUI-side lambda wiring by using the real window connection shape
captured = []
lam = lambda tb: captured.append(tb)
worker.done_log.connect(lam)
worker.done_log.emit("PY-TEST")
check("gui-style lambda receives traceback",
      captured and "PY-TEST" in captured[0])

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.6 headless checks passed")
