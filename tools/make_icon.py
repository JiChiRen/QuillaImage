#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the multi-resolution Windows .ico for the provisioning GUI.

Source asset: assets/quilla-favicon.svg -- the compact Quilla mark (dark disc,
heavier frame, bilge keels dropped), which the brand manifest designates for
small-icon use down to 16 px. Rendering goes through PySide6 QtSvg, so no
extra imaging dependency is required.

Usage:
    python make_icon.py            # write quilla.ico next to this file
    python make_icon.py --check    # render all sizes, print results, write nothing

Headless machines: set QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import os
import struct
import sys

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG_PATH = os.path.join(ROOT, "assets", "quilla-favicon.svg")
ICO_PATH = os.path.join(ROOT, "assets", "quilla.ico")


def render_pngs() -> list[tuple[int, bytes]]:
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(SVG_PATH)
    if not renderer.isValid():
        raise RuntimeError("SVG 无效或无法读取：%s" % SVG_PATH)

    out = []
    for size in SIZES:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        blob = QByteArray()
        buf = QBuffer(blob)
        buf.open(QBuffer.WriteOnly)
        if not image.save(buf, "PNG"):
            raise RuntimeError("尺寸 %d 渲染后无法编码为 PNG" % size)
        buf.close()
        out.append((size, bytes(blob)))
    return out


def build_ico(entries: list[tuple[int, bytes]]) -> bytes:
    """Assemble a Vista+ ICO with PNG-compressed entries (one per size)."""
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = bytearray()
    payload = bytearray()
    for size, png in entries:
        dim = 0 if size >= 256 else size       # 0 encodes 256 in ICONDIRENTRY
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                 len(png), offset)
        payload += png
        offset += len(png)
    return header + bytes(directory) + bytes(payload)


def verify_ico(data: bytes) -> None:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1 or count != len(SIZES):
        raise RuntimeError("ICO 头不合法")
    for i, size in enumerate(SIZES):
        w, h, colors, res, planes, bpp, length, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        dim = 0 if size >= 256 else size
        if (w, h, planes, bpp) != (dim, dim, 1, 32):
            raise RuntimeError("ICO 第 %d 项尺寸/格式异常" % i)
        if data[off:off + 4] != b"\x89PNG":
            raise RuntimeError("ICO 第 %d 项不是 PNG 数据" % i)
        if off + length > len(data):
            raise RuntimeError("ICO 第 %d 项越界" % i)


def main() -> int:
    if not os.path.isfile(SVG_PATH):
        print("缺少资源文件：%s" % SVG_PATH)
        return 1
    entries = render_pngs()
    data = build_ico(entries)
    verify_ico(data)
    if "--check" in sys.argv:
        print("渲染通过：%s" % ", ".join("%dpx=%dB" % (s, len(p))
                                       for s, p in entries))
        print("ICO 校验通过：%d 项，共 %d B（未写文件）" % (len(SIZES), len(data)))
        return 0
    with open(ICO_PATH, "wb") as f:
        f.write(data)
    print("已生成：%s (%d B, %d 个尺寸)" % (ICO_PATH, len(data), len(SIZES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
