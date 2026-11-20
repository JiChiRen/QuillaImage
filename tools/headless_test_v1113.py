# -*- coding: utf-8 -*-
"""Headless checks for v1.1.13: queue-based MCU pairing + progress.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v1113.py
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
from pack_devices import PackDevice  # noqa: E402

app = QApplication.instance() or QApplication([])

FAILS = []
DLG = []
QMessageBox.information = staticmethod(lambda *a, **k: DLG.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: DLG.append(("warn", a[2] if len(a) > 2 else "")))


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


DEVS = [PackDevice("M%02d" % i, 0x20000, 0x4000, 0x20000000, "", "", "M%02d" % i)
        for i in range(1, 6)]


# ---- fake pyocd: the 3rd model connects successfully ----
class FakeIDR:
    idr = 0x0BE12477        # int-able value, matching pyocd's dpidr.idr


class FakeDP:
    dpidr = FakeIDR()


class FakeTarget:
    dp = FakeDP()


class FakeSession:
    def open(self):
        pass

    def close(self):
        pass

    target = FakeTarget()


class RecordingHelper:
    def __init__(self, succeed_on):
        self.calls = []
        self.succeed_on = succeed_on

    def session_with_chosen_probe(self, **kwargs):
        self.calls.append(kwargs["target_override"])
        if kwargs["target_override"] == self.succeed_on:
            return FakeSession()
        return None        # "no probe" -> worker skips and continues


def run_match(helper):
    gui._PY = {"ConnectHelper": helper,
               "FileProgrammer": object,
               "TransferError": Exception}
    w = gui.MatchWorker("probe1", "pack.pack", DEVS, frequency=1000000)
    progress, logs, done = [], [], []
    w.progress.connect(lambda i, t, m: progress.append((i, t, m)))
    w.log_line.connect(logs.append)
    w.done.connect(lambda d, e: done.append((d, e)))
    w.run()                     # synchronous, no real thread/hardware
    return progress, logs, done


# ---- 1. success on 3rd model: stops there ----
progress, logs, done = run_match(RecordingHelper("m03"))
check("done reports match", done and done[0][0] is DEVS[2], str(done))
check("tried exactly 3 models", len(progress) == 3,
      str([p[2] for p in progress]))
check("progress values 1..3 of 5",
      [p[0] for p in progress] == [1, 2, 3] and
      all(p[1] == 5 for p in progress))
check("success logged with DP IDCODE",
      any("配对成功" in s and "0x0BE12477" in s for s in logs))
check("skips logged for failures",
      sum("跳过" in s for s in logs) == 2)

# ---- 2. all fail: done(None, "") ----
progress, logs, done = run_match(RecordingHelper("nonexistent"))
check("all-fail reports None", done and done[0][0] is None, str(done))
check("all-fail tried all 5", len(progress) == 5)
check("all-fail no error text", done[0][1] == "", repr(done[0][1]))

# ---- 3. assert on first model is caught, continues ----
class AssertHelper(RecordingHelper):
    def session_with_chosen_probe(self, **kwargs):
        self.calls.append(kwargs["target_override"])
        if kwargs["target_override"] == "m01":
            raise AssertionError()
        if kwargs["target_override"] == "m02":
            return FakeSession()
        return None


progress, logs, done = run_match(AssertHelper("m02"))
check("assert caught and continues", done and done[0][0] is DEVS[1], str(done))
check("assert logged as failure",
      any("配对失败" in s and "AssertionError" in s for s in logs))

# ---- 4. GUI: _match_done success selects model; all-fail dialog ----
win = gui.MainWindow()
win.pack_devices = DEVS
win.cmb_mcu.blockSignals(True)
win.cmb_mcu.clear()
for d in DEVS:
    win.cmb_mcu.addItem(d.part_number, d.part_number)
win.cmb_mcu.blockSignals(False)

DLG.clear()
win._match_done(DEVS[3], "")
check("GUI selects matched model", win._mcu_target() == "m04", win._mcu_target())
check("GUI pairing-success dialog",
      any("配对成功：M04" in d[1] for d in DLG), str(DLG))

DLG.clear()
win._match_done(None, "")
check("GUI all-fail dialog text",
      any("未匹配到正确MCU型号" in d[1] for d in DLG), str(DLG))

# ---- 5. _do_identity refuses without a pack ----
win.pack_devices = []
DLG.clear()
win._do_identity()
check("no-pack guard dialog",
      any("请先导入 CMSIS Pack" in d[1] for d in DLG), str(DLG))
check("no worker started without pack",
      win.match_worker is None)

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.13 headless checks passed")
