#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuillaImage — QBOOT 出厂镜像合并 / 烧录上位机（图形界面）。

依赖（可选，缺失时给出明确提示，核心打包逻辑不依赖它们）：
    pip install PySide6 pyocd
pyocd 参考版本 0.37.0；烧录流程基于 pyocd 官方 API。

页面（烧录在前、打包在后，烧录页不重新生成 manifest）：
    ① 烧录：选择已有整片 bin -> 烧前自动校验 -> 一次性烧到 0x08000000。
    ② 打包：选 QBOOT + APP -> 生成 manifest -> 合成整片 bin 落盘，
        供产线用 ① 直接烧录；也可仅导出 META 记录 / 校验已有 bin。
    固件镜像输入只在打包页；两页共用 META 起址 / APP 起址布局。

布局：QBOOT 固定 0x08000000；META 起址与 APP 起址可设置，
须与 QBOOT 端配置和 APP 链接基址一致。

图标：assets/quilla-favicon.svg（品牌资产）运行时渲染为多尺寸窗口图标；
quilla.ico 由 make_icon.py 生成，供打包成 exe 或快捷方式使用。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback

# 无界面自检：不导入 Qt，便于在没装 PySide6 的机器上确认核心与依赖状态。
if "--check" in sys.argv:
    import importlib.util
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from package import image as _core
        _core.selftest()
        print("image core: OK")
    except Exception as _e:
        print("image core: FAILED -", _e)
        sys.exit(1)
    print("PySide6:", "installed" if importlib.util.find_spec("PySide6") else "MISSING (pip install PySide6)")
    print("pyocd  :", "installed" if importlib.util.find_spec("pyocd") else "MISSING (pip install pyocd)")
    sys.exit(0)

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
        QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget,
        QVBoxLayout, QWidget,
    )
except ImportError:
    print("缺少 PySide6，无法启动图形界面。请先执行：")
    print("    pip install PySide6 pyocd")
    print("只做打包/校验时可以不用界面：python -m package.image --help")
    sys.exit(1)

try:
    from PySide6.QtSvg import QSvgRenderer
    QTSVG_OK = True
except ImportError:                                         # pragma: no cover
    QTSVG_OK = False

from package import image as core

try:
    from pyocd.core.helpers import ConnectHelper
    from pyocd.flash.file_programmer import FileProgrammer
    PYOCD_OK = True
    PYOCD_ERR = ""
except ImportError as exc:                                  # pragma: no cover
    PYOCD_OK = False
    PYOCD_ERR = str(exc)

def app_dir():
    """Directory that holds the application (exe when frozen, source otherwise).

    PyInstaller one-file sets ``__file__`` to a temporary extraction directory,
    so use ``sys.executable`` when frozen. Config and default output must live
    beside the real exe, not in a directory that vanishes on exit.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
DEFAULT_OUT = os.path.join(APP_DIR, "out", "combined.bin")
# 状态文件与应用同目录（删除即恢复默认）。
STATE_DIR = APP_DIR
STATE_PATH = os.path.join(STATE_DIR, "state.json")
def resource_dir():
    """Directory holding bundled read-only assets (PyInstaller extraction dir)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


# 品牌图标资源：assets/quilla-favicon.svg 是 Quilla 品牌
# 的副本；quilla.ico 由 make_icon.py 生成，供打包/快捷方式使用。
# 图标随包打进 _MEIxxxx，路径与可写的配置目录分开。
ICON_SVG = os.path.join(resource_dir(), "assets", "quilla-favicon.svg")
ICON_ICO = os.path.join(resource_dir(), "assets", "quilla.ico")
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def quilla_icon():
    """多尺寸窗口图标。优先从品牌 SVG 渲染，缺失时回退 .ico，再缺则返回 None。"""
    if QTSVG_OK and os.path.isfile(ICON_SVG):
        try:
            renderer = QSvgRenderer(ICON_SVG)
            if renderer.isValid():
                icon = QIcon()
                for size in ICON_SIZES:
                    image = QImage(size, size, QImage.Format_ARGB32)
                    image.fill(Qt.transparent)
                    painter = QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    icon.addPixmap(QPixmap.fromImage(image))
                if not icon.isNull():
                    return icon
        except Exception:                                   # pragma: no cover
            pass                                            # 图标失败不影响功能
    if os.path.isfile(ICON_ICO):
        icon = QIcon(ICON_ICO)
        if not icon.isNull():
            return icon
    return None


def _parse_addr(text: str, name: str) -> int:
    """解析十六进制地址输入（支持 0x 前缀 / 纯十六进制）。"""
    text = text.strip()
    if not text:
        raise core.ImageError("%s 未填写" % name)
    try:
        value = int(text, 0)
    except ValueError:
        raise core.ImageError("%s 不是合法的十六进制地址：%r" % (name, text))
    if not core.FLASH_BASE <= value < core.FLASH_BASE + core.FLASH_SIZE:
        raise core.ImageError(
            "%s 0x%08X 不在 FLASH [0x%08X, 0x%08X) 内"
            % (name, value, core.FLASH_BASE,
               core.FLASH_BASE + core.FLASH_SIZE))
    return value


class FlashWorker(QThread):
    """在后台线程里执行 pyocd 烧录，避免阻塞 UI。"""

    log_line = Signal(str)
    progress_value = Signal(int)
    done = Signal(bool, str)

    def __init__(self, image_path: str, probe_id: str, target: str,
                 pack_path: str, chip_erase: bool, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.chip_erase = chip_erase

    def _progress(self, value):
        try:
            self.progress_value.emit(int(value))
        except (TypeError, ValueError):
            pass

    def run(self):
        session = None
        try:
            kwargs = {
                "blocking": False,
                "target_override": self.target or "py32f040xb",
            }
            if self.pack_path:
                kwargs["pack"] = self.pack_path
            if self.probe_id:
                kwargs["unique_id"] = self.probe_id
                kwargs["return_first"] = False
            else:
                kwargs["return_first"] = True

            self.log_line.emit("连接调试探针 ...")
            session = ConnectHelper.session_with_chosen_probe(**kwargs)
            if session is None:
                raise RuntimeError("未找到可用探针（检查连接 / 驱动 / unique_id）")

            session.open()
            # pyocd 0.37 的 chip_erase 只接受 'chip' / 'sector' / None，
            # 不接受布尔值（builder.erase 会抛 invalid chip_erase value）。
            erase_arg = "chip" if self.chip_erase else None
            self.log_line.emit(
                "烧录 %s -> 0x%08X（整片擦除=%s）"
                % (self.image_path, core.FLASH_BASE,
                   "是" if self.chip_erase else "否")
            )
            FileProgrammer(
                session,
                chip_erase=erase_arg,
                smart_flash=False,
                trust_crc=False,
                keep_unwritten=True,
                no_reset=False,
                progress=self._progress,
            ).program(self.image_path, base_address=core.FLASH_BASE)
            session.target.reset()
            self.log_line.emit("烧录完成，已复位。")
            self.done.emit(True, "烧录完成")
        except Exception as exc:                            # pragma: no cover
            self.log_line.emit(traceback.format_exc())
            self.done.emit(False, str(exc))
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QuillaImage - QBOOT 镜像合并 / 烧录")
        self.resize(860, 680)
        self.worker = None

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_layout())
        layout.addWidget(self._build_tabs(), 1)
        layout.addWidget(self._build_log())
        self.setCentralWidget(root)

        if not PYOCD_OK:
            self.log("[提示] 未检测到 pyocd（%s），打包页可正常使用，"
                     "烧录页需先 pip install pyocd" % PYOCD_ERR)
        self.log("布局：QBOOT 固定 0x%08X（0x%X B）；META 起址 / APP 起址见上方（两页共用）。"
                 % (core.FLASH_BASE, core.QBOOT_SIZE))
        self._restore_state()
        self._hook_autosave()

    # ------------------------------------------------------------ 状态持久化
    _STATE_KEYS = (
        "flash_image", "pack", "target", "boot", "app", "key",
        "export_out", "meta_out", "meta_base", "app_start",
    )

    def _state_dict(self) -> dict:
        state = {
            "flash_image": self.ed_flash_image.text().strip(),
            "pack": self.ed_pack.text().strip(),
            "target": self.ed_target.text().strip(),
            "boot": self.ed_boot.text().strip(),
            "app": self.ed_app.text().strip(),
            "key": self.ed_key.text().strip(),
            "export_out": self.ed_export_out.text().strip(),
            "meta_out": self.ed_meta_out.text().strip(),
            "meta_base": self.ed_meta_base.text().strip(),
            "app_start": self.ed_app_start.text().strip(),
            "chip": self._chip(),
            "version": self.spin_version.value(),
            "chip_erase": self.cb_erase.isChecked(),
            "preset_seal": self.cb_seal.isChecked(),
        }
        return state

    def _save_state(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._state_dict(), f, ensure_ascii=False, indent=1)
        except OSError:
            pass                                    # 只读环境下静默失败

    def _restore_state(self):
        if not os.path.isfile(STATE_PATH):
            return
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError) as exc:
            self.log("[提示] 状态文件读取失败（%s），使用默认值。" % exc)
            return
        if not isinstance(state, dict):
            return
        # 芯片先恢复：切换会写默认地址，之后再用存档里的地址覆盖，
        # 这样用户自定义布局不会被默认值冲掉。
        chip = state.get("chip")
        if chip in core.TARGETS:
            idx = self.cmb_chip.findData(chip)
            if idx >= 0:
                self.cmb_chip.setCurrentIndex(idx)
        for key in self._STATE_KEYS:
            value = state.get(key)
            if value:
                edit = getattr(self, "ed_" + key)
                edit.setText(str(value))
        # pyocd target 与芯片几何保持一致（存档里的 target 可能已过时）
        if chip in core.TARGETS:
            self.ed_target.setText(core.resolve_target(chip)["name"])
        if "version" in state:
            try:
                self.spin_version.setValue(int(state["version"]))
            except (TypeError, ValueError):
                pass
        if "chip_erase" in state:
            self.cb_erase.setChecked(bool(state["chip_erase"]))
        if "preset_seal" in state:
            self.cb_seal.setChecked(bool(state["preset_seal"]))
        self.log("[提示] 已从 %s 恢复上次的路径与选项。" % STATE_PATH)

    def _hook_autosave(self):
        """任何路径/选项变化都立即落盘，无需手动保存。"""
        for key in self._STATE_KEYS:
            getattr(self, "ed_" + key).textChanged.connect(self._save_state)
        self.spin_version.valueChanged.connect(self._save_state)
        self.cb_erase.toggled.connect(self._save_state)
        self.cb_seal.toggled.connect(self._save_state)

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

    # ------------------------------------------------------------ 控件构造
    def _build_layout(self) -> QWidget:
        box = QGroupBox("布局（两页共用）")
        form = QFormLayout(box)

        self.cmb_chip = QComboBox()
        for key in sorted(core.TARGETS):
            self.cmb_chip.addItem(core.TARGETS[key][4], key)
        self.cmb_chip.currentIndexChanged.connect(self._on_chip_changed)
        form.addRow("目标芯片:", self.cmb_chip)

        self.ed_meta_base = QLineEdit("0x%08X" % core.DEFAULT_META_BASE)
        self.ed_app_start = QLineEdit("0x%08X" % core.DEFAULT_APP_START)
        form.addRow("META 起址 (十六进制):", self.ed_meta_base)
        form.addRow("APP 起址 (十六进制):", self.ed_app_start)
        return box

    def _chip(self) -> str:
        """当前选择的芯片键（几何参数用）。"""
        return self.cmb_chip.currentData() or "py32f040xb"

    def _on_chip_changed(self, _idx=0):
        """切换芯片时同步默认布局（用户仍可手动改地址）。"""
        t = core.resolve_target(self.cmb_chip.currentData())
        self.ed_meta_base.setText("0x%08X" % t["meta_base"])
        self.ed_app_start.setText("0x%08X" % t["app_start"])
        # 烧录页的 pyocd target 名与芯片键一致，顺带同步
        if hasattr(self, "ed_target"):
            self.ed_target.setText(t["name"])
        self._save_state()

    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_flash_tab(), "① 烧录")
        tabs.addTab(self._build_export_tab(), "② 打包")
        return tabs

    def _build_flash_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        form = QFormLayout()

        self.ed_flash_image = QLineEdit(DEFAULT_OUT)
        btn_img = QPushButton("浏览...")
        btn_img.clicked.connect(lambda: self._pick_into(
            self.ed_flash_image, "选择要烧录的整片 bin"))
        irow = QWidget(); ir = QHBoxLayout(irow); ir.setContentsMargins(0, 0, 0, 0)
        ir.addWidget(self.ed_flash_image, 1); ir.addWidget(btn_img)

        self.cmb_probe = QComboBox()
        self.cmb_probe.setMinimumWidth(320)
        btn_refresh = QPushButton("刷新探针")
        btn_refresh.clicked.connect(self._refresh_probes)
        row = QWidget(); r = QHBoxLayout(row); r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.cmb_probe, 1); r.addWidget(btn_refresh)

        self.ed_target = QLineEdit("py32f040xb")
        self.ed_pack = QLineEdit()
        btn_pack = QPushButton("浏览...")
        btn_pack.clicked.connect(lambda: self._pick_into(
            self.ed_pack, "选择 CMSIS Pack (*.pack)，可留空", "*.pack"))
        prow = QWidget(); pr = QHBoxLayout(prow); pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(self.ed_pack, 1); pr.addWidget(btn_pack)

        self.cb_erase = QCheckBox("烧录前整片擦除（出厂首片建议勾选）")
        self.cb_erase.setChecked(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.btn_verify_img = QPushButton("校验所选整片 bin")
        self.btn_verify_img.clicked.connect(self._do_verify_file)
        self.btn_flash = QPushButton("烧录整片 bin 到 0x%08X（不重新生成 manifest）"
                                     % core.FLASH_BASE)
        self.btn_flash.clicked.connect(self._do_flash)

        form.addRow("整片 bin 文件:", irow)
        form.addRow("调试探针:", row)
        form.addRow("目标芯片:", self.ed_target)
        form.addRow("CMSIS Pack:", prow)
        v.addLayout(form)
        v.addWidget(self.cb_erase)
        v.addWidget(self.progress)
        rowbtn = QWidget(); rb = QHBoxLayout(rowbtn)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(self.btn_verify_img)
        rb.addWidget(self.btn_flash, 1)
        v.addWidget(rowbtn)
        v.addStretch(1)
        v.addWidget(QLabel(
            "说明：烧录页只烧上面选定的整片 bin（内含 QBOOT / META / APP），\n"
            "不会重复生成 manifest；要改固件/manifest 请到 ② 打包页重新导出。\n"
            "烧录前会按上方 META/APP 起址自动校验 bin。"
        ))
        self._refresh_probes()
        return page

    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        # ---- 固件镜像（仅打包用） ----
        box_in = QGroupBox("固件镜像（仅打包用）")
        form = QFormLayout(box_in)

        self.ed_boot = QLineEdit()
        self.ed_app = QLineEdit()
        self.ed_key = QLineEdit()
        self.spin_version = QSpinBox()
        self.spin_version.setRange(0, 0x7FFFFFFF)
        self.spin_version.setValue(0)

        b1 = QPushButton("浏览...")
        b1.clicked.connect(lambda: self._pick_into(self.ed_boot,
                                                   "选择 QBOOT 镜像（.bin）"))
        b2 = QPushButton("浏览...")
        b2.clicked.connect(lambda: self._pick_into(self.ed_app,
                                                   "选择量产 APP .bin"))
        b3 = QPushButton("浏览...")
        b3.clicked.connect(lambda: self._pick_into(
            self.ed_key, "选择 Ed25519 私钥 private.hex（SEALED/SEC 必填）",
            "*.hex"))

        row1 = QWidget(); r1 = QHBoxLayout(row1); r1.setContentsMargins(0, 0, 0, 0)
        r1.addWidget(self.ed_boot, 1); r1.addWidget(b1)
        row2 = QWidget(); r2 = QHBoxLayout(row2); r2.setContentsMargins(0, 0, 0, 0)
        r2.addWidget(self.ed_app, 1); r2.addWidget(b2)
        row3 = QWidget(); r3 = QHBoxLayout(row3); r3.setContentsMargins(0, 0, 0, 0)
        r3.addWidget(self.ed_key, 1); r3.addWidget(b3)

        self.cb_seal = QCheckBox("SEALED 变体预置 seal（出厂首启免 ~20 s 验签）")
        self.cb_seal.setChecked(True)

        form.addRow("QBOOT 镜像:", row1)
        form.addRow("APP 镜像:", row2)
        form.addRow("Ed25519 私钥 (SEALED/SEC 必填):", row3)
        form.addRow("manifest 版本 (仅 CHECK):", self.spin_version)
        form.addRow("", self.cb_seal)

        btn = QPushButton("解析并预览（load 后显示 CRC32）")
        btn.clicked.connect(self._preview)
        form.addRow("", btn)
        v.addWidget(box_in)

        # ---- 输出 ----
        box_out = QGroupBox("导出")
        form_out = QFormLayout(box_out)

        self.ed_export_out = QLineEdit(DEFAULT_OUT)
        btn_out = QPushButton("另存为...")
        btn_out.clicked.connect(lambda: self._save_into(self.ed_export_out))
        row = QWidget(); r = QHBoxLayout(row); r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.ed_export_out, 1); r.addWidget(btn_out)

        self.ed_meta_out = QLineEdit()
        btn_meta = QPushButton("另存为...")
        btn_meta.clicked.connect(lambda: self._save_into(self.ed_meta_out))
        mrow = QWidget(); mr = QHBoxLayout(mrow); mr.setContentsMargins(0, 0, 0, 0)
        mr.addWidget(self.ed_meta_out, 1); mr.addWidget(btn_meta)

        form_out.addRow("输出整片 bin:", row)
        form_out.addRow("另存 256 B META 记录（可选）:", mrow)
        v.addWidget(box_out)

        btn_export = QPushButton("导出 boot + app + manifest 单 bin")
        btn_export.clicked.connect(self._do_export)
        btn_meta_only = QPushButton("仅导出 256 B META 记录")
        btn_meta_only.clicked.connect(self._do_meta_only)
        btn_verify = QPushButton("校验已有整片 bin（按布局起址）")
        btn_verify.clicked.connect(self._do_verify)

        v.addWidget(btn_export)
        v.addWidget(btn_meta_only)
        v.addWidget(btn_verify)
        v.addStretch(1)
        v.addWidget(QLabel(
            "打包结果落盘后，到 ① 烧录页烧录该 bin 到 0x%08X 即可，\n"
            "产线不必再分三次烧录，也不会重复生成 manifest。"
            % core.FLASH_BASE
        ))
        return page

    def _build_log(self) -> QWidget:
        box = QGroupBox("日志")
        v = QVBoxLayout(box)
        row = QWidget(); r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.addStretch(1)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(500)
        btn_clear = QPushButton("清除日志")
        btn_clear.clicked.connect(self.txt_log.clear)
        r.addWidget(btn_clear)
        v.addWidget(row)
        v.addWidget(self.txt_log)
        return box

    # ------------------------------------------------------------ 工具方法
    def log(self, msg: str):
        self.txt_log.appendPlainText(msg)

    def _start_dir(self, edit: QLineEdit) -> str:
        """文件对话框起始目录：输入框当前路径所在目录；不存在则回退工具目录。"""
        path = edit.text().strip()
        if path:
            d = os.path.dirname(path) or path
            if os.path.isdir(d):
                return d
        return APP_DIR

    def _pick_into(self, edit: QLineEdit, title: str, filt: str = "*.bin"):
        path, _ = QFileDialog.getOpenFileName(
            self, title, self._start_dir(edit), filt)
        if path:
            edit.setText(path)

    def _save_into(self, edit: QLineEdit):
        cur = edit.text().strip()
        start = cur or os.path.join(self._start_dir(edit),
                                    os.path.basename(DEFAULT_OUT))
        path, _ = QFileDialog.getSaveFileName(self, "输出文件", start, "*.bin")
        if path:
            edit.setText(path)

    def _layout_addrs(self) -> tuple[int, int]:
        meta_base = _parse_addr(self.ed_meta_base.text(), "META 起址")
        app_start = _parse_addr(self.ed_app_start.text(), "APP 起址")
        return meta_base, app_start

    def _load_inputs(self):
        boot = core._read_file(self.ed_boot.text().strip())
        app = core._read_file(self.ed_app.text().strip())
        version = int(self.spin_version.value())
        sign_seed = None
        key_path = self.ed_key.text().strip()
        if key_path:
            try:
                hex_text = core._read_file(key_path).decode("ascii").strip()
                hex_text = "".join(hex_text.split()).replace(":", "").replace(",", "")
                sign_seed = bytes.fromhex(hex_text)
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                raise core.ImageError("私钥文件读取/解析失败（%s）：%s"
                                      % (key_path, exc))
        meta_base, app_start = self._layout_addrs()
        return boot, app, version, sign_seed, meta_base, app_start

    def _log_common_info(self, boot: bytes, app: bytes, layout: dict,
                         variant: str):
        """load 后的通用信息：大小 / CRC32 / 布局 / 变体。"""
        self.log("  QBOOT   : %d B, CRC32 0x%08X"
                 % (len(boot), core.crc32_ieee(boot)))
        self.log("  APP     : %d B, CRC32 0x%08X"
                 % (len(app), core.crc32_ieee(app)))
        self.log("  布局    : QBOOT 0x%08X / META 0x%08X / APP 0x%08X / "
                 "APP 容量 0x%X B"
                 % (core.FLASH_BASE, layout["meta_base"],
                    layout["app_start"], layout["app_size"]))
        self.log("  变体    : %s%s"
                 % (variant,
                    "" if variant != "unknown" else "（未识别，请人工核对）"))

    def _show_info(self, info: dict):
        if info.get("boot_size"):
            self.log("  QBOOT   : %d B, CRC32 0x%08X"
                     % (info["boot_size"], info.get("boot_crc", 0)))
        if info.get("app_crc") is not None:
            self.log("  APP     : %d B (image_len), CRC32 0x%08X"
                     % (info["image_len"], info["app_crc"]))
        if info.get("meta_base") and info.get("app_start"):
            self.log("  布局    : QBOOT 0x%08X / META 0x%08X / APP 0x%08X"
                     % (core.FLASH_BASE, info["meta_base"], info["app_start"]))
        variant = info.get("variant", "unknown")
        self.log("  变体    : %s%s"
                 % (variant,
                    "" if variant != "unknown" else "（未识别，请人工核对）"))
        if variant in ("SEALED", "SEC"):
            self.log("  签名    : %s" % ("已用 Ed25519 私钥签名"
                                           if info.get("signed") else "未签名"))
            if info.get("floor"):
                self.log("  floor   : %d（防降级下限 = 镜像 secver）" % info["floor"])
            if info.get("seal_preset"):
                self.log("  seal    : 已预置（SEALED 首启免 ~20 s 验签）")
        elif variant == "CHECK" and not info.get("sig_zero", 1):
            self.log("  签名    : sig 非零（CHECK 变体不验签，仅记录）")
        self.log("  version : %d / flag : %d (APP1) / seq : %d"
                 % (info["version"], info.get("flag", core.FLAG_APP1),
                    info.get("seq", 1)))
        self.log("  SHA-256 : %s" % info["hash"].hex())

    def _build(self, out_path: str, meta_out: str = "") -> bool:
        try:
            boot, app, version, sign_seed, meta_base, app_start = \
                self._load_inputs()
            combined, rec, info = core.make_combined(
                boot, app, version, sign_seed=sign_seed,
                preset_seal=self.cb_seal.isChecked(),
                meta_base=meta_base, app_start=app_start,
                target=self._chip())
            core._write_file(out_path, combined)
            if meta_out:
                core._write_file(meta_out, rec)
            self.log("已生成整片 bin：%s (%d B)" % (out_path, len(combined)))
            self._show_info(info)
            if meta_out:
                self.log("  已导出 META 记录：%s (256 B, 0x%08X)"
                         % (meta_out, info["meta_base"]))
            return True
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(self, "生成失败", str(exc))
            self.log("生成失败：%s" % exc)
            return False

    # ------------------------------------------------------------ 槽
    def _preview(self):
        """load 后做非致命解析：向量表 / CRC / 布局 / 变体。

        SEALED/SEC 的签名前置条件（私钥、secver 区）在这里只作警告显示，
        不拦截预览；真正导出时 make_combined 仍会硬校验。
        """
        try:
            boot = core._read_file(self.ed_boot.text().strip())
            app = core._read_file(self.ed_app.text().strip())
        except OSError as exc:
            QMessageBox.critical(self, "解析失败", "镜像文件读取失败：%s" % exc)
            self.log("解析失败：%s" % exc)
            return
        try:
            meta_base, app_start = self._layout_addrs()
            layout = core.resolve_layout(meta_base, app_start, self._chip())
        except core.ImageError as exc:
            QMessageBox.critical(self, "解析失败", str(exc))
            self.log("解析失败：%s" % exc)
            return

        # 向量表硬错误仍然拦截（镜像本身不合法，后续无从谈起）
        try:
            boot_sp, boot_reset = core.validate_boot(boot, self._chip())
            app_sp, app_reset = core.validate_app(
                app, layout["app_start"], layout["app_size"], self._chip())
        except core.ImageError as exc:
            QMessageBox.critical(self, "解析失败", str(exc))
            self.log("解析失败：%s" % exc)
            return

        self.log("解析通过（未写文件）")
        variant = core.detect_variant(boot)
        self._log_common_info(boot, app, layout, variant)
        self.log("  APP MSP 0x%08X / reset 0x%08X；QBOOT MSP 0x%08X / reset 0x%08X"
                 % (app_sp, app_reset, boot_sp, boot_reset))
        self.log("  APP SHA-256 : %s" % hashlib.sha256(app).hexdigest())

        # 签名前置条件：预览只警告，不拦截
        if variant in ("SEALED", "SEC"):
            key_path = self.ed_key.text().strip()
            if not key_path:
                self.log("  [!] %s 变体导出必须提供 Ed25519 私钥（当前未选）" % variant)
            try:
                secver = core.read_secver(app)
                self.log("  secver  : %d（偏移 0x400，\"QAV1\"）" % secver)
            except core.ImageError as exc:
                self.log("  [!] %s" % exc)
        elif variant == "unknown":
            self.log("  [!] 未识别到变体串，请核对 QBOOT bin 是否串了构建产物")

    def _refresh_probes(self):
        self.cmb_probe.clear()
        if not PYOCD_OK:
            self.cmb_probe.addItem("（pyocd 未安装）", "")
            return
        try:
            probes = ConnectHelper.get_all_connected_probes(blocking=False)
        except Exception as exc:                            # pragma: no cover
            self.log("枚举探针失败：%s" % exc)
            probes = []
        if not probes:
            self.cmb_probe.addItem("（未检测到探针，烧录时将自动取第一个）", "")
            return
        for p in probes:
            self.cmb_probe.addItem(
                "%s  [%s]" % (p.description or p.product_name, p.unique_id),
                p.unique_id)

    def _do_flash(self):
        if not PYOCD_OK:
            QMessageBox.critical(
                self, "缺少依赖",
                "未检测到 pyocd，请先执行：pip install pyocd\n\n%s" % PYOCD_ERR)
            return
        if self.worker is not None and self.worker.isRunning():
            return
        image_path = self.ed_flash_image.text().strip()
        if not image_path:
            QMessageBox.critical(self, "烧录失败", "请先选择要烧录的整片 bin")
            return
        # 烧前校验（不生成 manifest）：按布局起址复核 bin
        try:
            meta_base, app_start = self._layout_addrs()
            info = core.verify_combined(core._read_file(image_path),
                                        meta_base=meta_base,
                                        app_start=app_start,
                                        target=self._chip())
            self.log("烧前校验通过：%s" % image_path)
            self._show_info(info)
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(
                self, "烧前校验失败",
                "%s\n\n请确认所选 bin 与布局起址一致，"
                "或在 ② 打包页重新导出。" % exc)
            self.log("烧前校验失败：%s" % exc)
            return
        self.progress.setValue(0)
        self.btn_flash.setEnabled(False)
        self.worker = FlashWorker(
            image_path,
            self.cmb_probe.currentData() or "",
            self.ed_target.text().strip(),
            self.ed_pack.text().strip(),
            self.cb_erase.isChecked(),
        )
        self.worker.log_line.connect(self.log)
        self.worker.progress_value.connect(self.progress.setValue)
        self.worker.done.connect(self._flash_done)
        self.worker.start()

    def _flash_done(self, ok: bool, msg: str):
        self.btn_flash.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            QMessageBox.information(self, "完成", msg)
        else:
            QMessageBox.critical(self, "烧录失败", msg)

    def _do_export(self):
        out_path = self.ed_export_out.text().strip() or DEFAULT_OUT
        if self._build(out_path, self.ed_meta_out.text().strip()):
            QMessageBox.information(self, "完成", "已导出：\n%s" % out_path)

    def _do_meta_only(self):
        start = (self.ed_meta_out.text().strip()
                 or os.path.join(self._start_dir(self.ed_export_out),
                                 "meta_record.bin"))
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 META 记录", start, "*.bin")
        if not path:
            return
        try:
            boot, app, version, sign_seed, meta_base, app_start = \
                self._load_inputs()
            _, rec, info = core.make_combined(
                boot, app, version, sign_seed=sign_seed,
                preset_seal=self.cb_seal.isChecked(),
                meta_base=meta_base, app_start=app_start,
                target=self._chip())
            core._write_file(path, rec)
            self.log("已导出 META 记录：%s" % path)
            self._show_info(info)
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _do_verify_file(self):
        """① 烧录页：按布局起址校验所选整片 bin。"""
        path = self.ed_flash_image.text().strip()
        if not path:
            QMessageBox.critical(self, "校验失败", "请先选择要校验的整片 bin")
            return
        try:
            meta_base, app_start = self._layout_addrs()
            info = core.verify_combined(core._read_file(path),
                                        meta_base=meta_base,
                                        app_start=app_start,
                                        target=self._chip())
            self.log("校验通过：%s" % path)
            self._show_info(info)
            self.log("  APP MSP 0x%08X / reset 0x%08X"
                     % (info["app_sp"], info["app_reset"]))
            QMessageBox.information(self, "完成", "校验通过：\n%s" % path)
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(self, "校验失败", str(exc))
            self.log("校验失败：%s" % exc)

    def _do_verify(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要校验的整片 bin", self._start_dir(self.ed_flash_image),
            "*.bin")
        if not path:
            return
        try:
            meta_base, app_start = self._layout_addrs()
            info = core.verify_combined(core._read_file(path),
                                        meta_base=meta_base,
                                        app_start=app_start,
                                        target=self._chip())
            self.log("校验通过：%s" % path)
            self._show_info(info)
            self.log("  APP MSP 0x%08X / reset 0x%08X"
                     % (info["app_sp"], info["app_reset"]))
            QMessageBox.information(self, "完成", "校验通过：\n%s" % path)
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(self, "校验失败", str(exc))
            self.log("校验失败：%s" % exc)


def main() -> int:
    app = QApplication(sys.argv)
    icon = quilla_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    win = MainWindow()
    if icon is not None:
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
