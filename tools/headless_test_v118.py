# -*- coding: utf-8 -*-
"""Headless test for v1.1.8: flash auto-retry on SWD TransferError,
simplified assert guidance. All pyocd objects are fakes - no hardware.

Run with the interpreter that has PySide6 + pyocd installed:
    QT_QPA_PLATFORM=offscreen py -3 tools/headless_test_v118.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

gui.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="quilla-test-"), "state.json")

FAILS = []


def check(name, cond, extra=""):
    print("[%-4s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


class FakeTransferError(Exception):
    """Stands in for pyocd.core.exceptions.TransferError."""


class FakeTarget:
    def reset(self):
        pass


class FakeSession:
    opens = 0
    closes = 0

    def open(self):
        FakeSession.opens += 1

    def close(self):
        FakeSession.closes += 1

    target = FakeTarget()


class FlakyProgrammer:
    """Raises TransferError on the first N program() calls, then succeeds."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, session, **kwargs):
        outer = self

        class _Prog:
            def program(self, path, base_address=None):
                outer.calls += 1
                if outer.calls <= outer.fail_times:
                    raise FakeTransferError(
                        "SWD/JTAG communication failure (Unexpected ACK '5')")
                return None
        return _Prog()


def run_worker(session=None):
    logs = []
    done = []
    progress = []
    w = gui.FlashWorker("fake.bin", "", "gd32f502ve", "", False, session=session)
    w.log_line.connect(logs.append)
    w.done.connect(lambda ok, msg: done.append((ok, msg)))
    w.progress_value.connect(progress.append)
    w.run()          # synchronous: no real thread, no hardware
    return logs, done, progress


# ---- 1. transient fault: auto-retry with a fresh session succeeds ----
FakeSession.opens = FakeSession.closes = 0
gui._PY = {"ConnectHelper": type(
                "CH", (), {"session_with_chosen_probe":
                           staticmethod(lambda **k: FakeSession())}),
           "FileProgrammer": FlakyProgrammer(fail_times=1),
           "TransferError": FakeTransferError}
logs, done, progress = run_worker()
check("transient: reported success", done and done[-1][0], str(done))
check("transient: two sessions opened", FakeSession.opens == 2,
      "opens=%d" % FakeSession.opens)
check("transient: both sessions closed", FakeSession.closes == 2,
      "closes=%d" % FakeSession.closes)
check("transient: retry logged",
      any("自动重试" in s for s in logs))
check("transient: progress reset before retry",
      any(v == 0 for v in progress))

# ---- 2. persistent fault: gives up after one retry with guidance ----
FakeSession.opens = FakeSession.closes = 0
gui._PY["FileProgrammer"] = FlakyProgrammer(fail_times=99)
logs, done, progress = run_worker()
check("persistent: reported failure", done and not done[-1][0], str(done))
check("persistent: exactly two attempts", FakeSession.opens == 2,
      "opens=%d" % FakeSession.opens)
check("persistent: guidance mentions re-plug",
      "重插拔" in done[-1][1], done[-1][1][:50])

# ---- 3. held session: no auto-retry, operator guidance instead ----
FakeSession.opens = FakeSession.closes = 0
gui._PY["FileProgrammer"] = FlakyProgrammer(fail_times=99)
held = FakeSession()
logs, done, progress = run_worker(session=held)
check("held: reported failure", done and not done[-1][0], str(done))
check("held: no retry (single use)", FakeSession.opens == 0,
      "opens=%d" % FakeSession.opens)
check("held: session not closed by worker", held.closes == 0)
check("held: guidance mentions disconnect button",
      "断开探针" in done[-1][1], done[-1][1][:50])

# ---- 4. simplified assert guidance (Ali's wording request) ----
err = gui._friendly_pyocd_error(AssertionError(), "gd32f502cb", "p.pack")
check("assert msg simplified",
      err.endswith("请手动选择与芯片实际容量一致的型号。") and
      "0.46.1" not in err and "GD32F502VE" not in err, err[:60])

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("all v1.1.8 headless checks passed")
