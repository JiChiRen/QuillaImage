# -*- coding: utf-8 -*-
# Copyright 2026 JiChiRen Info. Tech.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# This file is part of QuillaImage.

"""Freeze QuillaImage into a Windows application directory (onedir).

Usage::

    python tools/build_exe.py

Produces ``dist/quilla-image/quilla-image.exe`` plus its Qt / pyocd runtime
files in the same folder. Onedir avoids the per-launch extraction of a
one-file archive, so startup is much faster at the cost of a folder instead
of a single file. The core image packer remains usable from source with no
third-party dependencies::

    python -m package.image selftest
    python -m package.image --help

Requires ``pip install -e .[build]`` (PySide6, pyocd, PyInstaller).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WORK = os.path.join(ROOT, "build", "freeze")
DIST = os.path.join(ROOT, "dist")

ENTRY_SOURCE = (
    "from gui import main\n"
    "raise SystemExit(main() or 0)\n"
)


#: PySide6's PyInstaller hook collects the entire Qt bin/plugins tree. The
#: GUI only uses QtCore / QtGui / QtWidgets / QtSvg and the windows platform
#: plugin. These files are never loaded and are removed after collection:
#: QML / Quick / PDF, OpenGL (incl. the 20 MiB software rasterizer), virtual
#: keyboard, translations, and image-format plugins we do not open.
PRUNE_FILES = (
    "PySide6/Qt6Qml.dll", "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll", "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Quick.dll", "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6OpenGL.dll", "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/QtNetwork.pyd", "PySide6/Qt6Network.dll",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "PySide6/plugins/iconengines/qsvgicon.dll",
    "PySide6/plugins/generic/qtuiotouchplugin.dll",
    "PySide6/plugins/networkinformation/qnetworklistmanager.dll",
    "PySide6/plugins/tls/qcertonlybackend.dll",
    "PySide6/plugins/tls/qopensslbackend.dll",
    "PySide6/plugins/imageformats/qgif.dll",
    "PySide6/plugins/imageformats/qicns.dll",
    "PySide6/plugins/imageformats/qjpeg.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/imageformats/qtga.dll",
    "PySide6/plugins/imageformats/qtiff.dll",
    "PySide6/plugins/imageformats/qwbmp.dll",
    "PySide6/plugins/imageformats/qwebp.dll",
)
PRUNE_DIRS = ("PySide6/translations",)


def prune_qt(app_dir: str) -> None:
    """Drop Qt components the GUI never loads (PyInstaller over-collects)."""
    internal = os.path.join(app_dir, "_internal")
    removed = 0
    for rel in PRUNE_FILES:
        path = os.path.join(internal, *rel.split("/"))
        if os.path.isfile(path):
            removed += os.path.getsize(path)
            os.remove(path)
    for rel in PRUNE_DIRS:
        path = os.path.join(internal, *rel.split("/"))
        if os.path.isdir(path):
            for dp, _, fs in os.walk(path):
                for f in fs:
                    removed += os.path.getsize(os.path.join(dp, f))
            shutil.rmtree(path, ignore_errors=True)
    print("[build] pruned unused Qt files: %.1f MiB" % (removed / 1048576.0))


def main(argv=None) -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller missing: %s -m pip install -e .[build]"
              % os.path.basename(sys.executable), file=sys.stderr)
        return 2

    os.makedirs(WORK, exist_ok=True)
    entry = os.path.join(WORK, "_entry.py")
    with open(entry, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ENTRY_SOURCE)

    exe = "quilla-image" + (".exe" if os.name == "nt" else "")
    app_dir = os.path.join(DIST, "quilla-image")
    args = [
        sys.executable, "-m", "PyInstaller",
        entry,
        "--name", "quilla-image",
        "--onedir",
        # GUI app: use the Windows GUI subsystem, otherwise a console window
        # flashes up before Qt shows the main window.
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", DIST,
        "--workpath", os.path.join(WORK, "build"),
        "--specpath", WORK,
        "--paths", ROOT,
        # PySide6's hook pulls in the Qt libraries actually imported by the
        # code (QtCore / QtGui / QtWidgets / QtSvg) plus the required plugins.
        # Do NOT --collect-submodules PySide6.QtPlugins here: it drags in the
        # whole Qt (Qml / Quick / Pdf / OpenGL / software rasterizer), which
        # more than doubles the distribution for no functional gain.
        "--hidden-import", "PySide6.QtSvg",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "PySide6.QtCore",
        # pyocd loads target/pack backends lazily, and its CMSIS-DAP sequence
        # parser reads sequences.lark from disk at import time. Both the
        # pyocd data files and lark's grammars must be bundled.
        "--hidden-import", "pyocd.core.helpers",
        "--hidden-import", "pyocd.flash.file_programmer",
        "--hidden-import", "pyocd.target",
        "--hidden-import", "pyocd.pack",
        "--hidden-import", "pyocd.board",
        "--collect-data", "pyocd",
        "--collect-data", "lark",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        "--exclude-module", "unittest",
    ]
    icon = os.path.join(ROOT, "assets", "quilla.ico")
    if os.path.isfile(icon):
        args += ["--icon", icon]
    svg = os.path.join(ROOT, "assets", "quilla-favicon.svg")
    if os.path.isfile(svg):
        sep = ";" if os.name == "nt" else ":"
        args += ["--add-data", "%s%sassets" % (svg, sep)]

    print("[build] %s -> %s" % ("quilla-image", os.path.join(app_dir, exe)))
    # Remove stale one-file artifacts and the previous onedir tree so the
    # output directory contains exactly one build.
    stale = os.path.join(DIST, exe)
    if os.path.isfile(stale):
        os.remove(stale)
    if "--no-clean" not in (argv or []):
        shutil.rmtree(app_dir, ignore_errors=True)
        shutil.rmtree(os.path.join(WORK, "build"), ignore_errors=True)
    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit("[build] PyInstaller failed (rc=%d)" % proc.returncode)
    out = os.path.join(app_dir, exe)
    if not os.path.isfile(out):
        raise SystemExit("[build] expected output missing: %s" % out)

    # LGPLv3 compliance: the Qt dynamic libraries are bundled unmodified;
    # ship their license texts and the Qt notice next to the executable.
    licenses = os.path.join(app_dir, "licenses")
    os.makedirs(licenses, exist_ok=True)
    for name in ("LGPL-3.0.txt", "GPL-3.0.txt"):
        src = os.path.join(ROOT, "licenses", name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(licenses, name))
    notice = os.path.join(ROOT, "LICENSES-Qt.txt")
    if os.path.isfile(notice):
        shutil.copyfile(notice, os.path.join(app_dir, "LICENSES-Qt.txt"))

    prune_qt(app_dir)

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(app_dir) for f in fs)
    print("[build] %-20s exe %8.1f KiB / folder %8.1f KiB"
          % (exe, os.path.getsize(out) / 1024.0, total / 1024.0))
    print("[build] Qt LGPL/GPL license texts copied to dist/quilla-image/licenses/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
