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
import time
import sys
import threading
import traceback


def _register_probe_plugins():
    """显式注册内置 probe 插件到 pyocd 的 PROBE_CLASSES。

    PyInstaller drops package entry-point metadata, so pyocd's
    load_plugin_classes_of_type('pyocd.probe', ...) sees no plugins and
    PROBE_CLASSES stays empty -> no probes are ever enumerated. Register
    the built-in probe plugins explicitly (same classes as the entry
    points in pyocd's dist-info). Idempotent; existing entries are kept.
    """
    from pyocd.probe.aggregator import PROBE_CLASSES
    if PROBE_CLASSES:
        return
    from pyocd.probe.cmsis_dap_probe import CMSISDAPProbePlugin
    from pyocd.probe.jlink_probe import JLinkProbePlugin
    from pyocd.probe.picoprobe import PicoprobePlugin
    from pyocd.probe.stlink_probe import StlinkProbePlugin
    from pyocd.probe.tcp_client_probe import TCPClientProbePlugin
    for _plugin in (CMSISDAPProbePlugin, JLinkProbePlugin,
                    PicoprobePlugin, StlinkProbePlugin,
                    TCPClientProbePlugin):
        _p = _plugin()
        if _p.should_load():
            PROBE_CLASSES[_p.name] = _p.load()


# 无界面自检：不导入 Qt，便于在没装 PySide6 的机器上确认核心与依赖状态。
if "--check" in sys.argv or "--probe-check" in sys.argv:
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
    if "--probe-check" in sys.argv:
        # Verify probe enumeration in the SAME frozen runtime the GUI uses.
        # Useful for diagnosing a "no probe" list without clicking the GUI.
        # Plugin registration must happen here too: --probe-check exits
        # before _ensure_pyocd() would run in the GUI path.
        try:
            _register_probe_plugins()
            from pyocd.core.helpers import ConnectHelper
            from pyocd.probe.pydapaccess import dap_access_cmsis_dap as _dac
            print("v1 backend:", _dac.USB_BACKEND, "v2 backend:", _dac.USB_BACKEND_V2)
            try:
                print("interfaces:", len(_dac._get_interfaces()))
            except Exception as _e:
                print("interfaces FAILED:", type(_e).__name__, _e)
            _ps = ConnectHelper.get_all_connected_probes(blocking=False)
            print("probes:", len(_ps))
            for _p in _ps:
                print("  ", _p.description, _p.unique_id)
        except Exception as _e:
            import traceback
            traceback.print_exc()
    sys.exit(0)

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
        QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplashScreen,
        QTabWidget, QVBoxLayout, QWidget,
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

# pyocd 导入实测约 5 s，是"打开工具 6 秒没反应"的最大头，且发生在窗口
# 出现之前。改为惰性导入：首次用到探针功能（枚举 / 读型号 / 烧录）时
# 在后台线程导入并注册 probe 插件，窗口立即出现。
# _PY 状态机：None = 尚未导入；dict = 成功（存用到的符号）；str = 失败原因。
_PY = None
_PY_LOCK = threading.Lock()


def _ensure_pyocd():
    """惰性导入 pyocd，返回 (modules, error)；成功时 error 为空串。

    Probe plugin registration lives in _register_probe_plugins() (defined
    near the top so the --probe-check CLI mode can share it). Safe to call
    repeatedly; the result is cached, the dict keys are stable.
    """
    global _PY
    if isinstance(_PY, (dict, str)):
        return (_PY, "") if isinstance(_PY, dict) else (None, _PY)
    with _PY_LOCK:
        if isinstance(_PY, (dict, str)):
            return (_PY, "") if isinstance(_PY, dict) else (None, _PY)
        try:
            from pyocd.core.helpers import ConnectHelper
            from pyocd.core.exceptions import TransferError
            from pyocd.flash.file_programmer import FileProgrammer

            _register_probe_plugins()
            _PY = {"ConnectHelper": ConnectHelper,
                   "FileProgrammer": FileProgrammer,
                   "TransferError": TransferError}
            return _PY, ""
        except ImportError as exc:                          # pragma: no cover
            _PY = str(exc)
            return None, _PY

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
        # 输入栏只显示纯十六进制（如 08006000）；int(text, 0) 会把
        # 前导 0 当八进制而拒绝，所以无前缀时按 16 进制解析。
        value = int(text, 16) if not text.lower().startswith("0x") else int(text, 0)
    except ValueError:
        raise core.ImageError("%s 不是合法的十六进制地址：%r" % (name, text))
    if not core.FLASH_BASE <= value < core.FLASH_BASE + core.FLASH_SIZE:
        raise core.ImageError(
            "%s 0x%08X 不在 FLASH [0x%08X, 0x%08X) 内"
            % (name, value, core.FLASH_BASE,
               core.FLASH_BASE + core.FLASH_SIZE))
    return value


class FlashWorker(QThread):
    # SWD 瞬态 FAULT 的递进重试参数：重试间隔与重试时的调试时钟。
    # 500 kHz 对杜邦线/劣化链路明显更稳，仅重试时使用，正常烧录不减速。
    FLASH_RETRY_DELAY_S = 1.5
    FLASH_RETRY_CLOCK_HZ = 500000

    """在后台线程里执行 pyocd 烧录，避免阻塞 UI。"""

    log_line = Signal(str)
    progress_value = Signal(int)
    done = Signal(bool, str)

    def __init__(self, image_path: str, probe_id: str, target: str,
                 pack_path: str, chip_erase: bool, session=None, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.chip_erase = chip_erase
        self.session = session          # 复用显式连接的 session（不为 None 时）
        self.used_held = session is not None

    def _progress(self, value):
        """pyocd 回调 0.0~1.0 浮点比例（见 utility/progress.py ProgressReport）。

        旧代码直接 int(value)，全程只会得到 0（偶尔 1），进度条因此一直
        卡 0% 直到结束跳 100%。换算成百分比并夹在 0..100。
        """
        try:
            pct = int(float(value) * 100.0)
            self.progress_value.emit(max(0, min(100, pct)))
        except (TypeError, ValueError):
            pass

    def run(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            self.done.emit(False, "未检测到 pyocd：%s" % err)
            return
        ConnectHelper = mods["ConnectHelper"]
        FileProgrammer = mods["FileProgrammer"]
        TransferError = mods["TransferError"]
        # 复用显式连接的 session 时由 UI 负责（断开按钮/closeEvent），
        # 这里不 close；瞬时模式照旧用完即关。
        owned = self.session is None
        # Ali 上板实测（2026-11-19）：整片擦除期间偶发 SWD ACK FAULT
        # （Unexpected ACK '5'），失败后手动重开一次会话即成功——瞬态
        # 调试链路问题。递进重试：先原时钟新会话，再降时钟（pyocd 会话
        # 选项 'frequency'，见 core/session.py:660），共 3 次；复用持有
        # 会话时链路状态未知，不自动重试，交给操作员断开重连。
        max_attempts = 3 if owned else 1
        attempt = 0
        while True:
            attempt += 1
            if attempt > 1:
                # 给探针/链路一点恢复时间（挂死的 WCH-Link 需要收尾）。
                time.sleep(self.FLASH_RETRY_DELAY_S)
            session = None
            try:
                if owned:
                    kwargs = {
                        "blocking": False,
                        "target_override": self.target or "py32f040xb",
                    }
                    if attempt > 1:
                        # 错误信息自带的建议就是 reduce debugger clock。
                        kwargs["frequency"] = self.FLASH_RETRY_CLOCK_HZ
                        self.log_line.emit(
                            "第 %d 次尝试：降低调试时钟至 %d kHz"
                            % (attempt, self.FLASH_RETRY_CLOCK_HZ // 1000))
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
                else:
                    session = self.session
                    self.log_line.emit("使用已连接的探针 ...")
                # pyocd 的 chip_erase 只接受 'chip' / 'sector' / None，
                # 不接受布尔值（builder.erase 会抛 invalid chip_erase value）。
                erase_arg = "chip" if self.chip_erase else None
                self.log_line.emit(
                    "烧录 %s -> 0x%08X（整片擦除=%s）"
                    % (self.image_path, core.FLASH_BASE,
                       "是" if self.chip_erase else "否")
                )
                if attempt > 1:
                    self.progress_value.emit(0)
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
                if attempt > 1:
                    self.log_line.emit("（第 %d 次尝试成功）" % attempt)
                self.log_line.emit("烧录完成，已复位。")
                self.done.emit(True, "烧录完成")
                return
            except TransferError as exc:
                self.log_line.emit(traceback.format_exc())
                if attempt < max_attempts:
                    self.log_line.emit(
                        "SWD 通信失败（%s），正在用新会话自动重试 ..."
                        % exc)
                    continue                    # finally 负责关掉本次会话
                if owned:
                    self.done.emit(False, (
                        "SWD 通信失败（%s）。已自动重试 2 次（含降低时钟）"
                        "仍未成功：请检查杜邦线 / 供电，重插拔探针后再试。"
                        % exc))
                else:
                    self.done.emit(False, (
                        "SWD 通信失败（%s）。请点『断开探针』后重新连接，"
                        "再重试烧录。" % exc))
                return
            except Exception as exc:            # pragma: no cover
                self.log_line.emit(traceback.format_exc())
                self.done.emit(False, _friendly_pyocd_error(
                    exc, self.target, self.pack_path))
                return
            finally:
                if owned and session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass


def _friendly_pyocd_error(exc: Exception, target: str, pack_path: str) -> str:
    """把 pyocd 的裸异常翻译成操作员可执行的指引。

    已知上游缺陷（pyocd 0.37~0.46.1 均实测复现，截至 2026-11 未修复）：
    部分 CMSIS-Pack 的小容量型号（如 GD32F50x 的 128KB B 后缀）在
    session.open() 构建 flash 子区域时触发 memory_map 的无消息
    AssertionError，且连接中断可能导致探针从 USB 消失。
    """
    base = "%s: %s" % (type(exc).__name__, exc)
    if isinstance(exc, AssertionError) and pack_path:
        return (
            "pyocd 无法构建型号 %s 的 flash 内存布局（CMSIS-Pack 兼容性缺陷）。"
            "请手动选择与芯片实际容量一致的型号。" % (target or "未知"))
    return base


class IdentityWorker(QThread):
    """Read MCU identity from an attached probe without blocking the UI.

    Session handling, two modes:
      * session=None (transient): open -> read -> close, connect_mode
        "attach" so we never halt the running target. DP IDCODE and
        DBGMCU reads go through the DP / AHB-AP and do not need the CPU
        halted; halting a just-powered-on board is the most likely trigger
        for the "probe disappears after the first identity read" report.
      * session=<held>: reuse the explicitly connected session, zero
        extra USB/SWD handshake; never closed here (the owner disconnects).
    """

    done = Signal(object, str)      # (McuIdentity, error_text)
    done_log = Signal(str)          # extra traceback for the log pane

    def __init__(self, probe_id: str, target: str, pack_path: str,
                 session=None, parent=None):
        super().__init__(parent)
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.session = session

    def run(self):
        session = None
        owned = False
        try:
            mods, err = _ensure_pyocd()
            if mods is None:
                self.done.emit(None, "未检测到 pyocd：%s" % err)
                return
            ConnectHelper = mods["ConnectHelper"]
            from mcu_identity import read_identity
            if self.session is not None:
                session = self.session
            else:
                owned = True
                kwargs = {
                    "blocking": False,
                    "target_override": self.target or "py32f040xb",
                    # 读 DP IDCODE / DBGMCU 走 DP 与 AHB-AP，不需要 halt
                    # 目标 CPU；默认 "halt" 会在 connect 时打断运行中的
                    # 目标，上电初期最易诱发探针固件挂死。
                    "connect_mode": "attach",
                }
                if self.pack_path:
                    kwargs["pack"] = self.pack_path
                if self.probe_id:
                    kwargs["unique_id"] = self.probe_id
                    kwargs["return_first"] = False
                else:
                    kwargs["return_first"] = True
                session = ConnectHelper.session_with_chosen_probe(**kwargs)
                if session is None:
                    self.done.emit(None, "未找到可用探针")
                    return
                session.open()
            ident = read_identity(session)
            self.done.emit(ident, "")
        except Exception as exc:
            self.done.emit(None, _friendly_pyocd_error(
                exc, self.target, self.pack_path))
            # AssertionError 等空消息异常只有堆栈才能定位；写日志不弹窗。
            self.done_log.emit(traceback.format_exc())
        finally:
            if owned and session is not None:
                try:
                    session.close()
                except Exception:
                    pass


class ConnWorker(QThread):
    """显式连接探针并持有 session（点"断开探针"才释放）。"""

    done = Signal(object, str)      # (Session or None, error_text)
    done_log = Signal(str)          # extra traceback for the log pane

    def __init__(self, probe_id: str, target: str, pack_path: str, parent=None):
        super().__init__(parent)
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path

    def run(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            self.done.emit(None, "未检测到 pyocd：%s" % err)
            return
        ConnectHelper = mods["ConnectHelper"]
        session = None
        try:
            kwargs = {
                "blocking": False,
                "target_override": self.target or "py32f040xb",
                "connect_mode": "attach",       # 与 IdentityWorker 同理由
            }
            if self.pack_path:
                kwargs["pack"] = self.pack_path
            if self.probe_id:
                kwargs["unique_id"] = self.probe_id
                kwargs["return_first"] = False
            else:
                kwargs["return_first"] = True
            session = ConnectHelper.session_with_chosen_probe(**kwargs)
            if session is None:
                self.done.emit(None, "未找到可用探针")
                return
            session.open()
            self.done.emit(session, "")
        except Exception as exc:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            self.done.emit(None, _friendly_pyocd_error(
                exc, self.target, self.pack_path))
            self.done_log.emit(traceback.format_exc())


class DiscWorker(QThread):
    """后台关闭持有的 session——设备已死时 close 可能阻塞，不能放 UI 线程。"""

    done = Signal(str)              # error_text ("" = clean)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

    def run(self):
        err = ""
        try:
            self.session.close()
        except Exception as exc:                            # pragma: no cover
            err = str(exc)
        self.done.emit(err)


class ProbeWorker(QThread):
    """后台枚举调试探针。

    枚举含 USB HID 扫描，首次调用还要等 pyocd 惰性导入完成（约 5 s），
    放 UI 线程会让窗口冻结；期间下拉框显示"正在枚举探针"。
    """

    done = Signal(list, str)        # (probes, error_text)

    def run(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            self.done.emit([], err)
            return
        try:
            probes = mods["ConnectHelper"].get_all_connected_probes(
                blocking=False)
            self.done.emit(probes, "")
        except Exception as exc:                            # pragma: no cover
            self.done.emit([], str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QuillaImage - QBOOT 镜像合并 / 烧录")
        self.resize(860, 680)
        self.worker = None
        self._probe_worker = None
        self._held_session = None    # 显式连接的探针 session（"连接探针"持有）
        self._held_meta = None       # (target, pack, probe_id) 连接时的快照

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_chip_selector())
        layout.addWidget(self._build_tabs(), 1)
        layout.addWidget(self._build_log())
        self.setCentralWidget(root)

        # pyocd 现为惰性导入，是否可用在首次枚举探针时报告（ProbeWorker）。
        self.log("烧录页整片 bin 固定从 0x%08X 起（已打包好，无需布局信息）；"
                 "布局参数仅打包页使用。" % core.FLASH_BASE)
        self._restore_state()
        self._hook_autosave()

    # ------------------------------------------------------------ 状态持久化
    _STATE_KEYS = (
        "flash_image", "boot", "app", "key",
        "export_out", "meta_out", "meta_base", "app_start", "chip_pack",
    )

    def _state_dict(self) -> dict:
        state = {
            "flash_image": self.ed_flash_image.text().strip(),
            "pack": self.ed_chip_pack.text().strip(),
            "target": self._mcu_target(),
            "boot": self.ed_boot.text().strip(),
            "app": self.ed_app.text().strip(),
            "key": self.ed_key.text().strip(),
            "export_out": self.ed_export_out.text().strip(),
            "meta_out": self.ed_meta_out.text().strip(),
            "meta_base": self.ed_meta_base.text().strip(),
            "app_start": self.ed_app_start.text().strip(),
            "chip_pack": self.ed_chip_pack.text().strip(),
            "chip": self._chip(),
            "mcu": self.cmb_mcu.currentData() or "",
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
        for key in self._STATE_KEYS:
            value = state.get(key)
            if value:
                edit = getattr(self, "ed_" + key)
                text = str(value)
                if key in ("meta_base", "app_start") and text.lower().startswith("0x"):
                    text = text[2:]       # 输入栏只显示纯十六进制
                edit.setText(text)
        # 恢复 CMSIS Pack + 上次选中的 MCU（pack 可能已被移动/删除，
        # 失败就静默保持空选择，不影响其余状态）。
        pack_path = state.get("chip_pack", "")
        mcu = state.get("mcu", "")
        if pack_path and os.path.isfile(pack_path):
            try:
                from pack_devices import load_pack_devices
                self.pack_devices = load_pack_devices(pack_path)
                self.cmb_mcu.blockSignals(True)
                self.cmb_mcu.clear()
                for d in self.pack_devices:
                    self.cmb_mcu.addItem(d.label, d.part_number)
                idx = self.cmb_mcu.findData(mcu) if mcu else -1
                self.cmb_mcu.setCurrentIndex(idx if idx >= 0 else 0)
                self.cmb_mcu.blockSignals(False)
                self._on_mcu_changed()
                # pack 推导的 target 是权威的（pyocd 从 pack 生成 target），
                # 不用存档里可能已过时的 target 覆盖它。用户仍可随时手改。
            except Exception as exc:
                self.log("[提示] 恢复 CMSIS Pack 失败（%s）。" % exc)
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
        # 释放显式连接的探针 session；设备已死时 close 可能慢，但关窗时
        # 同步等待是可接受的（失败只吞异常，不让窗口关不掉）。
        if self._held_session is not None:
            try:
                self._held_session.close()
            except Exception:
                pass
            self._held_session = None
        self._save_state()
        super().closeEvent(event)

    # ------------------------------------------------------------ 公共芯片选择区
    def _build_chip_selector(self) -> QWidget:
        """公共区：导入 CMSIS Pack → 选 MCU 型号 → 供两页共用。"""
        box = QGroupBox("芯片 / CMSIS Pack")
        form = QFormLayout(box)

        self.pack_devices = []          # list[pack_devices.PackDevice]
        self.selected_device = None

        self.ed_chip_pack = QLineEdit()
        btn_pack = QPushButton("导入 Pack…")
        btn_pack.clicked.connect(self._pick_chip_pack)
        prow = QWidget(); pr = QHBoxLayout(prow)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(self.ed_chip_pack, 1); pr.addWidget(btn_pack)

        # MCU 型号就是 pyocd target_override（pack 的 part_number 小写），
        # 同一个东西不分两栏。可编辑：导入 pack 后下拉选，没导入时可手输。
        self.cmb_mcu = QComboBox()
        self.cmb_mcu.setMinimumWidth(300)
        self.cmb_mcu.setEditable(True)
        self.cmb_mcu.setEditText("py32f040xb")
        self.cmb_mcu.currentIndexChanged.connect(self._on_mcu_changed)
        self.lbl_chip_geo = QLabel("未选择芯片；可从 Pack 导入，或直接输入型号。")

        form.addRow("CMSIS Pack:", prow)
        form.addRow("MCU 型号 / pyocd 目标:", self.cmb_mcu)
        form.addRow("", self.lbl_chip_geo)
        return box

    def _pick_chip_pack(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CMSIS Pack (*.pack)", self._start_dir(self.ed_chip_pack),
            "CMSIS Pack (*.pack);;All files (*.*)")
        if not path:
            return
        try:
            from pack_devices import load_pack_devices
            devices = load_pack_devices(path)
        except Exception as exc:
            QMessageBox.critical(self, "Pack 解析失败", str(exc))
            self.log("Pack 解析失败：%s" % exc)
            return
        if not devices:
            QMessageBox.warning(self, "Pack 无可用芯片",
                                "这个 Pack 里没有带 Flash/RAM 容量定义的芯片。")
            return
        self.ed_chip_pack.setText(path)
        self.pack_devices = devices
        # 保持导入前的型号选择：下拉重建后当前项会落到排序第一（可能是
        # 128KB 小容量型号，pyocd 对部分这类型号构建 target 会断言失败
        # 并把探针拖下线）。原型号在新 pack 里存在就选回去，恢复不了
        # 才落到第一项，并把选中项明确写进日志。
        prev_target = self._mcu_target()
        self.cmb_mcu.blockSignals(True)
        self.cmb_mcu.clear()
        for d in devices:
            self.cmb_mcu.addItem(d.label, d.part_number)
        restore = self.cmb_mcu.findData(prev_target) if prev_target else -1
        if restore < 0 and prev_target:
            # findData is exact-match; part_number stored with original
            # casing vs prev_target lowercased. Hand-rolled case-insensitive
            # scan avoids the Qt.MatchFlag enum relocation across PySide6
            # versions (6.x moved it to Qt.MatchFlag.MatchCaseInsensitive).
            for i in range(self.cmb_mcu.count()):
                if self.cmb_mcu.itemData(i).lower() == prev_target:
                    restore = i
                    break
        if restore >= 0:
            self.cmb_mcu.setCurrentIndex(restore)
        self.cmb_mcu.blockSignals(False)
        self._on_mcu_changed()
        cur = self._mcu_target()
        if prev_target and cur != prev_target:
            self.log("[提示] 原型号 %s 不在此 Pack 中，已切换到 %s；"
                     "请确认后再读取型号 / 烧录。" % (prev_target, cur))
        else:
            self.log("当前选中型号：%s" % cur)
        self.log("Pack 已导入：%s（%d 个芯片）" % (path, len(devices)))
        self._save_state()

    def _mcu_by_part(self, part: str):
        for d in self.pack_devices:
            if d.part_number == part:
                return d
        return None

    def _on_mcu_changed(self, _idx=0):
        part = self.cmb_mcu.currentData() or ""
        self.selected_device = self._mcu_by_part(part)
        if self.selected_device is None:
            self.lbl_chip_geo.setText("未选择芯片；可从 Pack 导入，或用预设芯片。")
            return
        d = self.selected_device
        geo = "Flash %d KiB / RAM %d KiB @ 0x%08X" % (
            d.flash_size // 1024, d.ram_size // 1024, d.ram_start)
        # 型号本身（currentData）就是 pyocd target，不再单独显示一份。
        self.lbl_chip_geo.setText(geo + " | 已就绪，可打包 / 烧录")
        # 打包页布局：按选中的 MCU 几何推导 META/APP 起址仍用默认偏移，
        # image.py 打包时会用 geometry 校验；这里只更新显示。
        self._save_state()

    def _mcu_target(self) -> str:
        """当前 MCU 型号即 pyocd target_override，同一个字段。

        Editable combobox 陷阱：手输新值时 currentData() 仍返回旧下拉项的
        data。只有"编辑文本 == 选中项文本"（未手动改动）才信任 data，
        否则以编辑文本为准。
        """
        text = self.cmb_mcu.currentText().strip()
        idx = self.cmb_mcu.currentIndex()
        data = self.cmb_mcu.currentData()
        if idx >= 0 and data and text == self.cmb_mcu.itemText(idx):
            return str(data).strip().lower()
        return text.lower()

    def _chip_geometry(self) -> dict | None:
        return self.selected_device.geometry() if self.selected_device else None

    def _chip_target(self) -> str:
        """打包/校验用的 target 名；动态芯片用 part_number 作标识。"""
        if self.selected_device:
            return self.selected_device.part_number.lower()
        return "py32f040xb"

    # ------------------------------------------------------------ 控件构造
    def _build_layout(self) -> QWidget:
        """打包布局：芯片几何来自公共区，这里只留可覆盖的 META/APP 起址。"""
        box = QGroupBox("镜像布局（芯片容量取自上方公共区）")
        form = QFormLayout(box)

        self.ed_meta_base = QLineEdit("%08X" % core.DEFAULT_META_BASE)
        self.ed_app_start = QLineEdit("%08X" % core.DEFAULT_APP_START)
        addr_row = QWidget(); ar = QHBoxLayout(addr_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.addWidget(QLabel("META 起址 0x")); ar.addWidget(self.ed_meta_base)
        ar.addWidget(QLabel("APP 起址 0x")); ar.addWidget(self.ed_app_start)
        form.addRow(addr_row)
        return box

    def _chip(self) -> str:
        """打包/校验用的芯片标识；几何由 _chip_geometry() 一并提供。"""
        return self._chip_target()

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
        self.btn_refresh = QPushButton("刷新探针")
        self.btn_refresh.clicked.connect(self._refresh_probes)
        self.btn_identity = QPushButton("从目标板读取 MCU 型号")
        self.btn_identity.setEnabled(False)   # 探针连上后才可用
        self.btn_identity.clicked.connect(self._do_identity)
        # 显式连接/断开：连接后 session 由 GUI 持有，读型号与烧录复用，
        # 不再每次操作都做一遍 SWD 握手（上电初期握手失败会让 WCH-Link
        # 固件挂死、探针从 USB 消失，必须重新插拔）。
        self.btn_conn = QPushButton("连接探针")
        self.btn_conn.setEnabled(False)       # 枚举出探针后才可用
        self.btn_conn.clicked.connect(self._toggle_conn)
        row = QWidget(); r = QHBoxLayout(row); r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.cmb_probe, 1); r.addWidget(self.btn_refresh)
        r.addWidget(self.btn_conn)
        r.addWidget(self.btn_identity)

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
        v.addLayout(form)
        v.addWidget(self.cb_erase)
        v.addWidget(self.progress)
        rowbtn = QWidget(); rb = QHBoxLayout(rowbtn)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(self.btn_verify_img)
        rb.addWidget(self.btn_flash, 1)
        v.addWidget(rowbtn)
        self._refresh_probes()
        return page

    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        # ---- 布局（芯片几何 + META/APP 起址，仅打包用） ----
        v.addWidget(self._build_layout())

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
        btn_verify = QPushButton("校验已有整片 bin")
        btn_verify.clicked.connect(self._do_verify)

        btnrow = QWidget(); br = QHBoxLayout(btnrow)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(btn_export, 1)
        br.addWidget(btn_meta_only, 1)
        br.addWidget(btn_verify, 1)
        v.addWidget(btnrow)
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
                target=self._chip(), geometry=self._chip_geometry())
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
            layout = core.resolve_layout(meta_base, app_start, self._chip(),
                                         self._chip_geometry())
        except core.ImageError as exc:
            QMessageBox.critical(self, "解析失败", str(exc))
            self.log("解析失败：%s" % exc)
            return

        # 向量表硬错误仍然拦截（镜像本身不合法，后续无从谈起）
        try:
            boot_sp, boot_reset = core.validate_boot(boot, self._chip(),
                                                     self._chip_geometry())
            app_sp, app_reset = core.validate_app(
                app, layout["app_start"], layout["app_size"], self._chip(),
                self._chip_geometry())
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
        """后台枚举探针；完成前下拉框显示进行中提示，读型号按钮不可用。"""
        if self._probe_worker is not None and self._probe_worker.isRunning():
            return                                    # 正在枚举，忽略重入
        self.cmb_probe.clear()
        self.cmb_probe.addItem("正在枚举探针 ...", "")
        self.btn_identity.setEnabled(False)
        self._probe_worker = ProbeWorker()
        self._probe_worker.done.connect(self._probes_done)
        self._probe_worker.start()

    def _probes_done(self, probes, error):
        self.cmb_probe.clear()
        if error:
            self.cmb_probe.addItem("（pyocd 不可用：%s）" % error, "")
            self.log("[提示] pyocd 未安装或加载失败（%s），打包页可正常使用，"
                     "烧录页需先 pip install pyocd" % error)
            return
        if not probes:
            # 枚举为空不影响烧录：probe_id 为空时 FlashWorker 会用
            # return_first=True 让 pyocd 自动取第一个探针。
            self.cmb_probe.addItem("（列表为空；烧录时自动取第一个探针）", "")
            self.btn_conn.setEnabled(False)
            return
        for p in probes:
            self.cmb_probe.addItem(
                "%s  [%s]" % (p.description or p.product_name, p.unique_id),
                p.unique_id)
        self.btn_identity.setEnabled(True)
        self.btn_conn.setEnabled(self._held_session is None)

    # ------------------------------------------------------------ 探针显式连接
    def _toggle_conn(self):
        if self._held_session is not None:
            self._disconnect_probe()
        else:
            self._connect_probe()

    def _connect_probe(self):
        """显式打开并持有探针 session（connect_mode=attach，不 halt 目标）。"""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "忙", "上一次操作还在进行中。")
            return
        probe_id = self.cmb_probe.currentData() or ""
        if not probe_id:
            QMessageBox.warning(self, "未选择探针",
                                "请先刷新并选择一个调试探针。")
            return
        if not self._target_ready():
            return
        self.btn_conn.setEnabled(False)
        self.btn_conn.setText("连接中 ...")
        self.log("连接探针（attach 模式，不打断目标运行）...")
        self.conn_worker = ConnWorker(
            probe_id, self._mcu_target(), self.ed_chip_pack.text().strip())
        self.conn_worker.done_log.connect(
            lambda tb: self.log("[堆栈]\n" + tb.strip()))
        self.conn_worker.done.connect(self._connect_done)
        self.conn_worker.start()

    def _connect_done(self, session, error):
        if session is None or error:
            self.btn_conn.setText("连接探针")
            self.btn_conn.setEnabled(True)
            QMessageBox.critical(self, "连接失败", error or "未知错误")
            self.log("探针连接失败：%s" % error)
            # 探针可能已从总线消失（固件挂死重枚举），刷新一次列表。
            self._refresh_probes()
            return
        self._held_session = session
        self._held_meta = (self._mcu_target(),
                           self.ed_chip_pack.text().strip(),
                           self.cmb_probe.currentData() or "")
        self._set_conn_state(True)
        self.log("探针已连接并持有；读型号 / 烧录将复用本次连接。")

    def _disconnect_probe(self):
        """关闭持有的 session 并重新枚举。close 可能阻塞，放后台线程。"""
        if self._held_session is None:
            return
        self.btn_conn.setEnabled(False)
        self.btn_conn.setText("断开中 ...")
        self.disc_worker = DiscWorker(self._held_session)
        self.disc_worker.done.connect(self._disconnect_done)
        self.disc_worker.start()

    def _disconnect_done(self, error):
        had = self._held_session is not None
        self._held_session = None
        self._held_meta = None
        self._set_conn_state(False)
        self.log("探针已断开。" if not error else
                 "断开时出错（不影响重新连接）：%s" % error)
        if had:
            self._refresh_probes()

    def _set_conn_state(self, connected: bool):
        self.btn_conn.setText("断开探针" if connected else "连接探针")
        self.btn_conn.setEnabled(True)
        # 连接期间探针选择固定，不许换；刷新也无意义（枚举到的还是同一台）。
        self.cmb_probe.setEnabled(not connected)
        self.btn_refresh.setEnabled(not connected)
        self.btn_identity.setEnabled(connected)
        if not connected:
            # 恢复到枚举结果决定的状态：有探针条目即可用。
            self.btn_identity.setEnabled(bool(self.cmb_probe.currentData()))

    def _target_ready(self) -> bool:
        """连探针前校验 target：没有 pack 时必须是 pyocd 内建型号。

        py32f040xb 不是 pyocd 内建 target，必须靠 CMSIS pack 提供；
        空有默认值而不导入 pack 会抛晦涩的 TargetSupportError。
        """
        target = self._mcu_target()
        pack = self.ed_chip_pack.text().strip()
        if not target:
            QMessageBox.warning(self, "目标型号未填",
                                "请在公共区选择 MCU 型号或手输 pyocd 目标。")
            return False
        if pack:
            return True
        mods, err = _ensure_pyocd()
        if mods is None:
            QMessageBox.warning(self, "pyocd 不可用",
                                "未检测到 pyocd（%s），无法校验目标型号。\n"
                                "请先执行：pip install pyocd" % err)
            return False
        from pyocd.target import TARGET
        if target not in TARGET:
            QMessageBox.warning(
                self, "需要 CMSIS Pack",
                "目标 %r 不是 pyocd 内建型号。\n"
                "请在公共区导入对应 CMSIS Pack，或手输一个内建型号\n"
                "（pyocd list --targets 可查看）。" % target)
            return False
        return True

    def _do_identity(self):
        """连上探针后读取芯片标识，让用户确认型号再继续烧录。"""
        if self.worker is not None and getattr(self.worker, "isRunning", lambda: False)():
            QMessageBox.information(self, "忙", "上一次操作还在进行中。")
            return
        if not self._target_ready():
            self.btn_identity.setEnabled(False)
            return
        probe_id = self.cmb_probe.currentData() or ""
        self.btn_identity.setEnabled(False)
        self.log("读取 MCU 型号 ..." +
                 ("（复用已连接探针）" if self._held_session is not None else ""))
        self.identity_worker = IdentityWorker(
            probe_id, self._mcu_target(),
            self.ed_chip_pack.text().strip(),
            session=self._held_session)
        self.identity_worker.done_log.connect(
            lambda tb: self.log("[堆栈]\n" + tb.strip()))
        self.identity_worker.done.connect(self._identity_done)
        self.identity_worker.start()

    def _identity_done(self, ident, error):
        self.btn_identity.setEnabled(True)
        if error or ident is None:
            target = self._mcu_target() or "(未选型号)"
            hint = ("\n\n目标型号：%s" % target)
            # AssertionError 无消息（pyocd 对部分 pack 芯片的兼容缺陷，
            # 如 GD32F50x pack 的 128KB 型号），堆栈已写入日志。
            if error.startswith("AssertionError"):
                hint += ("\n\npyocd 在创建该型号的 target 时断言失败"
                         "（pack 数据兼容缺陷，完整堆栈见日志）。"
                         "请换用同系列其它容量型号。")
            hint += ("\n\n探针可能已掉线（assert 发生在连接中途，"
                     "半初始化的会话未能干净释放）。请刷新探针列表；"
                     "若列表为空需重新插拔 USB。")
            QMessageBox.critical(self, "读取失败", (error or "未知错误") + hint)
            self.log("读取 MCU 型号失败（target=%s）：%s" % (target, error))
            self._refresh_probes()
            return
        body = ident.summary()
        read_model = (ident.part_number or "").strip()
        # 读到型号且有回值：弹窗问用户是否采用，避免手动选错。
        # 只有 pyocd target 自己报告的型号才算数；原始 ID 不用来猜型号。
        if read_model:
            matched = [d for d in self.pack_devices
                       if d.part_number.lower() == read_model.lower()]
            body += "\n\npyocd 报告目标型号：%s" % read_model
            if len(matched) == 1:
                body += "\n已导入 Pack 中对应：%s" % matched[0].label
            body += ("\n\n当前公共区选择：%s\n是否改用读到的型号 %s？"
                     % (self._mcu_target(), read_model))
            ans = QMessageBox.question(
                self, "芯片型号确认", body,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans == QMessageBox.Yes:
                idx = self.cmb_mcu.findData(read_model)
                if idx >= 0:
                    self.cmb_mcu.setCurrentIndex(idx)
                    self.log("已按读取结果切换型号：%s" % read_model)
                else:
                    self.cmb_mcu.setEditText(read_model.lower())
                    self.log("已按读取结果填写型号：%s" % read_model)
            else:
                self.log("用户保留当前型号：%s" % self._mcu_target())
        else:
            # 没有型号回值：只展示原始 ID，不猜型号、不做选择
            body += ("\n\npyocd 未报告具体型号（只读到上面的原始 ID）。"
                     "请在公共区手动确认型号后继续。")
            QMessageBox.information(self, "芯片型号", body)
            self.log("MCU 型号读取完成（无型号回值）：\n" + ident.summary())
            return
        self.log("MCU 型号读取完成：\n" + ident.summary())

    def _do_flash(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            QMessageBox.critical(
                self, "缺少依赖",
                "未检测到 pyocd，请先执行：pip install pyocd\n\n%s" % err)
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
                                        target=self._chip(),
                                        geometry=self._chip_geometry())
            self.log("烧前校验通过：%s" % image_path)
            self._show_info(info)
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(
                self, "烧前校验失败",
                "%s\n\n请确认所选 bin 与布局起址一致，"
                "或在 ② 打包页重新导出。" % exc)
            self.log("烧前校验失败：%s" % exc)
            return
        if not self._target_ready():
            return
        # 复用显式连接的 session：target / pack 必须与连接时一致，
        # 否则 session 的 target_override 已经固定，烧进去会张冠李戴。
        use_held = None
        if self._held_session is not None:
            if self._held_meta != (self._mcu_target(),
                                   self.ed_chip_pack.text().strip(),
                                   self.cmb_probe.currentData() or ""):
                QMessageBox.warning(
                    self, "型号 / 探针已变更",
                    "探针是用旧的型号 / 探针连接的（公共区或探针选择后来改过）。\n"
                    "请先点『断开探针』再重新连接，或直接烧录（自动开关连接）。")
                return
            use_held = self._held_session
        self.progress.setValue(0)
        self.btn_flash.setEnabled(False)
        self.worker = FlashWorker(
            image_path,
            self.cmb_probe.currentData() or "",
            self._mcu_target(),
            self.ed_chip_pack.text().strip(),
            self.cb_erase.isChecked(),
            session=use_held,
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
        # 复用显式连接烧录后自动断开并重新枚举，恢复产线"烧完即收"状态。
        if ok and self._held_session is not None:
            self._disconnect_probe()

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
                target=self._chip(), geometry=self._chip_geometry())
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
                                        target=self._chip(),
                                        geometry=self._chip_geometry())
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
                                        target=self._chip(),
                                        geometry=self._chip_geometry())
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
    # 启动画面：pyocd 已改为惰性导入（约 5 s 的等待移到首次用探针时），
    # 窗口构建本身很快，但仍给用户一个明确的"正在打开"反馈，
    # 避免双击 exe 后几秒白屏让人以为没点开。
    splash = QSplashScreen()
    splash.setFixedSize(340, 140)
    splash.showMessage("正在启动 QuillaImage ...",
                       Qt.AlignCenter, Qt.darkGray)
    splash.show()
    app.processEvents()
    win = MainWindow()
    win.show()
    splash.finish(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
