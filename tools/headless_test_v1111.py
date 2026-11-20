# -*- coding: utf-8 -*-
"""Headless test for v1.1.11: flash never reuses the attach held session.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v1111.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

gui.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="quilla-test-"), "state.json")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

FAILS = []
DLG = []
QMessageBox.information = staticmethod(lambda *a, **k: DLG.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: DLG.append(("crit", a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: DLG.append(("warn", a[2] if len(a) > 2 else "")))


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


win = gui.MainWindow()

# ---- fake the pre-flash verify so _do_flash gets past validation ----
import package.image as core_pkg  # noqa: E402
FAKE_INFO = {
    "ok": True, "version": 1, "flag": core_pkg.FLAG_APP1, "seq": 1,
    "hash": b"\x00" * 32, "signed": False, "variant": "unknown",
    "app_msp": 0x20000000, "app_reset": 0x08000001,
    "qboot_msp": 0x20000000, "qboot_reset": 0x08000001,
}
core_pkg.verify_combined = lambda *a, **k: FAKE_INFO
core_pkg._read_file = lambda p: b"\x00" * 16

# ---- intercept the two worker classes and record what they receive ----
flash_calls = []
disc_calls = []


class _SignalStub:
    def connect(self, *a, **k):
        pass


class FakeFlashWorker:
    def __init__(self, *a, **kw):
        flash_calls.append(kw.get("session", "MISSING"))
        self.log_line = _SignalStub()
        self.progress_value = _SignalStub()
        self.done = _SignalStub()

    def start(self):
        pass

    def isRunning(self):
        return False


from PySide6.QtCore import QObject, Signal  # noqa: E402


class FakeDiscWorker(QObject):
    done = Signal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        disc_calls.append(session)

    def start(self):
        # simulate async close finishing immediately
        QTimer.singleShot(0, lambda: self.done.emit(""))


orig_flash, orig_disc = gui.FlashWorker, gui.DiscWorker
gui.FlashWorker, gui.DiscWorker = FakeFlashWorker, FakeDiscWorker

# ---- 1. no held session: flash starts directly with session=None ----
win.cmb_mcu.setEditText("cortex_m")   # pyocd built-in target, passes _target_ready
win.ed_flash_image.setText("fake.bin")
win._do_flash()
if not flash_calls:
    print("  [debug] dialogs:", DLG, "| target_ready:", win._target_ready(),
          "| held:", win._held_session)
check("idle: flash started", len(flash_calls) == 1)
check("idle: no held session passed", flash_calls[-1] is None, repr(flash_calls[-1]))
check("idle: no disconnect needed", len(disc_calls) == 0)

# ---- 2. held session: flash must disconnect first, then start with None ----
flash_calls.clear()
disc_calls.clear()
fake_held = object()
win._held_session = fake_held
win.btn_conn.setText("断开探针")
win._do_flash()
check("held: disconnect triggered first", len(disc_calls) == 1 and
      disc_calls[0] is fake_held)
check("held: flash not started before disconnect", len(flash_calls) == 0)
check("held: pending flag set", win._pending_flash is True)
# let the fake DiscWorker's queued _disconnect_done run
loop = QEventLoop()
QTimer.singleShot(0, loop.quit)
loop.exec()
check("held: flash started after disconnect", len(flash_calls) == 1)
check("held: flash uses NEW session (None)", flash_calls[-1] is None,
      repr(flash_calls[-1]))
check("held: pending flag cleared", win._pending_flash is False)
check("held: session released", win._held_session is None)

# ---- 3. stale meta mismatch no longer blocks flashing (kept target built-in) ----
DLG.clear()
win.cmb_mcu.setEditText("cortex_m")
win.ed_chip_pack.setText("some-new-pack.pack")   # differs from "at connect time"
flash_calls.clear()
win._held_session = object()
win._do_flash()
check("meta mismatch: no warning dialog",
      not any(d[0] == "warn" for d in DLG), str(DLG))
loop = QEventLoop()
QTimer.singleShot(0, loop.quit)
loop.exec()
check("meta mismatch: flash still proceeds", len(flash_calls) == 1)

# ---- 4. real FlashWorker still accepts an optional session kwarg (unused by GUI) ----
check("FlashWorker signature accepts session kwarg",
      "session" in orig_flash.__init__.__code__.co_varnames)

gui.FlashWorker, gui.DiscWorker = orig_flash, orig_disc
print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.11 headless checks passed")
