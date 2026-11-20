# -*- coding: utf-8 -*-
# Copyright 2026 JiChiRen Info. Tech.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# This file is part of QuillaImage.

"""Audit which bundled files a running app actually loads.

Method (runtime module enumeration)::

    1. Start dist/QuillaImage/QuillaImage.exe.
    2. Exercise every feature (pack, verify, flash-page load, key tools).
    3. Run this script: it snapshots the live process's loaded modules and
       compares them against every file in the app directory, reporting
       files that were never loaded.

Unloaded DLLs are *candidates* for pruning, not proof: plugins loaded only
by a feature you forgot to exercise will show up as unloaded. Always
re-run the full smoke sequence after pruning (see tools/smoke.py).

Usage::

    python tools/audit_runtime.py                # report against running app
    python tools/audit_runtime.py --wait 20      # wait N seconds for app start

No third-party packages required (ctypes + stdlib only).
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_DIR = os.path.join(ROOT, "dist", "QuillaImage")
EXE = "QuillaImage.exe"

# Win32 Toolhelp32 snapshot API
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_PATH = 260


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * MAX_PATH),
    ]


def find_pid(exe_name: str) -> int:
    """Find the first process whose image name matches exe_name."""
    k32 = ctypes.windll.kernel32
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * MAX_PATH),
        ]
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snap == -1:
        raise OSError("CreateToolhelp32Snapshot failed")
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode("utf-8", "replace").lower() == exe_name.lower():
                return entry.th32ProcessID
            ok = k32.Process32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return 0


def loaded_paths(pid: int) -> set:
    """Lower-case absolute paths of modules loaded by pid."""
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == -1:
        raise OSError("CreateToolhelp32Snapshot(module) failed for pid %d" % pid)
    out = set()
    try:
        entry = MODULEENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Module32First(snap, ctypes.byref(entry))
        while ok:
            path = entry.szExePath.decode("utf-8", "replace")
            if path:
                out.add(os.path.normcase(os.path.abspath(path)))
            ok = k32.Module32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wait", type=float, default=0.0,
                    help="seconds to wait for the app to appear before auditing")
    ap.add_argument("--app-dir", default=APP_DIR)
    ap.add_argument("--exe", default=EXE)
    ap.add_argument("--show-all", action="store_true",
                    help="also list files that were loaded")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.app_dir):
        print("app directory missing: %s" % args.app_dir, file=sys.stderr)
        return 2

    deadline = time.time() + args.wait
    pid = 0
    while True:
        pid = find_pid(args.exe)
        if pid or time.time() >= deadline:
            break
        time.sleep(0.2)
    if not pid:
        print("app not running: %s (start it and exercise all features first)"
              % args.exe, file=sys.stderr)
        return 2

    loaded = loaded_paths(pid)
    all_files = []
    for dp, _, fs in os.walk(args.app_dir):
        for f in fs:
            p = os.path.join(dp, f)
            if f == "SHA256SUMS.txt" or f.endswith(".json"):
                continue  # runtime state / checksums are not runtime deps
            all_files.append(p)

    unused = []
    used = []
    for p in all_files:
        if os.path.normcase(os.path.abspath(p)) in loaded:
            used.append(p)
        else:
            unused.append(p)

    def size(paths):
        return sum(os.path.getsize(p) for p in paths if os.path.isfile(p))

    print("pid %d: loaded modules %d, app files %d" % (pid, len(loaded), len(all_files)))
    print("loaded from app dir: %d files / %.1f MiB" % (len(used), size(used) / 1048576))
    print("NOT loaded        : %d files / %.1f MiB" % (len(unused), size(unused) / 1048576))
    print("\n--- top 30 unloaded files (prune candidates) ---")
    for p in sorted(unused, key=lambda x: os.path.getsize(x), reverse=True)[:30]:
        print("%9.1f KiB  %s" % (os.path.getsize(p) / 1024.0,
                                 os.path.relpath(p, args.app_dir)))
    if args.show_all:
        print("\n--- loaded from app dir ---")
        for p in sorted(used):
            print("  " + os.path.relpath(p, args.app_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
