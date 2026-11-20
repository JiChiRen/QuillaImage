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
        QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QSpinBox,
        QSplashScreen, QTabWidget, QVBoxLayout, QWidget,
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
from i18n import get_lang, set_lang, tr, LANGS, LANG_LABELS

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
            from pyocd.core.target import Target
            from pyocd.flash.file_programmer import FileProgrammer

            _register_probe_plugins()
            _PY = {"ConnectHelper": ConnectHelper,
                   "FileProgrammer": FileProgrammer,
                   "TransferError": TransferError,
                   "Target": Target}
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


def _saved_lang() -> str:
    """构建 UI 前从 state.json 读界面语言，避免启动时先中文再切换的闪烁。"""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            lang = json.load(f).get("lang")
        return lang if lang in LANGS else "zh"
    except (OSError, ValueError):
        return "zh"

# 探针 SWD 速率下拉项：(显示文本, Hz)。pyocd 会话选项 'frequency'。
RATE_OPTIONS = (
    ("10 MHz", 10000000), ("5 MHz", 5000000), ("4 MHz", 4000000),
    ("2 MHz", 2000000), ("1 MHz", 1000000), ("512 kHz", 512000),
    ("256 kHz", 256000), ("128 kHz", 128000),
)
RATE_DEFAULT_INDEX = 4          # 1 MHz（原 pyocd 默认，稳妥）
SLOW_FALLBACK_HZ = 128000       # 自动降速时的最后一档
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
    """在后台线程里执行 pyocd 烧录，避免阻塞 UI。"""

    FLASH_RETRY_DELAY_S = 1.5

    log_line = Signal(str)
    progress_value = Signal(int)
    done = Signal(bool, str)

    def __init__(self, image_path: str, probe_id: str, target: str,
                 pack_path: str, chip_erase: bool, session=None,
                 frequency: int = 0, auto_slow: bool = True,
                 base_address: int = None, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.chip_erase = chip_erase
        self.session = session          # 复用显式连接的 session（不为 None 时）
        self.used_held = session is not None
        self.frequency = frequency      # 用户设定的连接/通讯速率（Hz，0=pyocd 默认）
        self.auto_slow = auto_slow      # 失败时是否自动降速重连
        self.base_address = (core.FLASH_BASE if base_address is None
                             else base_address)

    def _retry_rates(self):
        """返回每次尝试的速率列表。

        Ali 上板：第一次失败后重建链路、同速率第二次即成功，降速是浪费。
        因此前两次都用用户设定速率（第 2 次只是重建链路重试），只有连续
        失败 2 次后才降速一档作为兜底。不自动降速时三次都同速率。
        """
        base = self.frequency or RATE_OPTIONS[RATE_DEFAULT_INDEX][1]
        if not self.auto_slow:
            return [base, base, base]
        lower = [hz for _, hz in RATE_OPTIONS if hz < base]
        # [设定, 设定, 低一档]：前两次同速率重建链路，第三次才降速兜底
        return [base, base, lower[0]] if lower else [base, base, base]

    def _session_kwargs(self, rate_hz: int) -> dict:
        """构造一次会话的连接参数（每次重试共用，保证速率按阶梯递进）。"""
        kwargs = {
            "blocking": False,
            "target_override": self.target or "py32f040xb",
            "frequency": rate_hz,
        }
        if self.pack_path:
            kwargs["pack"] = self.pack_path
        if self.probe_id:
            kwargs["unique_id"] = self.probe_id
            kwargs["return_first"] = False
        else:
            kwargs["return_first"] = True
        return kwargs

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
        # 速率阶梯：用户设定值 +（可选）逐级降档。每次都用新会话重连，
        # 并在日志打印本次重连速率（Ali 要求）。
        rates = self._retry_rates()
        max_attempts = len(rates) if owned else 1
        attempt = 0
        while True:
            attempt += 1
            if attempt > 1:
                # 给探针/链路一点恢复时间（挂死的 WCH-Link 需要收尾）。
                time.sleep(self.FLASH_RETRY_DELAY_S)
            session = None
            try:
                if owned:
                    rate_hz = rates[attempt - 1]
                    kwargs = self._session_kwargs(rate_hz)
                    rate_txt = ("%d MHz" % (rate_hz // 1000000)
                                if rate_hz % 1000000 == 0
                                else "%d kHz" % (rate_hz // 1000))
                    self.log_line.emit(
                        "第 %d 次尝试：以 %s 速率连接探针"
                        % (attempt, rate_txt))

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
                    % (self.image_path, self.base_address,
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
                ).program(self.image_path, base_address=self.base_address)
                session.target.reset()
                if attempt > 1:
                    self.log_line.emit("（第 %d 次尝试成功）" % attempt)
                self.log_line.emit("烧录完成，已复位。")
                self.done.emit(True, "烧录完成")
                return
            except TransferError as exc:
                if attempt < max_attempts:
                    # 首次抖动是已知的探针固件行为（重建链路即成功），
                    # 降为简洁提示，不刷大坨 traceback 吓人。
                    self.log_line.emit(
                        "第 %d 次 SWD 通信抖动（%s），重建链路后重试 ..."
                        % (attempt, str(exc).split(";")[0].strip()))
                    continue                    # finally 关会话；下轮重建链路
                # 三次都失败才打印完整堆栈，便于诊断真实硬件故障。
                self.log_line.emit(traceback.format_exc())
                if owned:
                    slow_note = "（末次已降速）" if self.auto_slow else ""
                    self.done.emit(False, (
                        "SWD 通信失败（%s）。已重试 %d 次%s"
                        "仍未成功：请检查杜邦线 / 供电，重插拔探针后再试。"
                        % (exc, max_attempts - 1, slow_note)))
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
                 session=None, frequency: int = 0, parent=None):
        super().__init__(parent)
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.session = session
        self.frequency = frequency

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
                if self.frequency:
                    kwargs["frequency"] = self.frequency
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


class MatchWorker(QThread):
    """按 pack 型号队列逐个配对：成功即停，全部失败报告未匹配。

    "配对成功" = 用该型号作为 target_override 能成功建立 pyocd 会话并
    读到 DP IDCODE（硬件确实响应 SWD）。注意：pyocd 的 part_number 是
    回填传入值，不能当作硬件型号；这里只把能连接的型号作为候选，并在
    日志如实说明判据边界。UI 侧显示进度条，避免用户以为程序挂了。
    """

    progress = Signal(int, int, str)    # (index, total, current model)
    log_line = Signal(str)
    done = Signal(object, str)          # (PackDevice or None, error_text)

    def __init__(self, probe_id: str, pack_path: str, devices: list,
                 frequency: int = 0, parent=None):
        super().__init__(parent)
        self.probe_id = probe_id
        self.pack_path = pack_path
        self.devices = list(devices)
        self.frequency = frequency
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            self.done.emit(None, "未检测到 pyocd：%s" % err)
            return
        ConnectHelper = mods["ConnectHelper"]
        total = len(self.devices)
        if total == 0:
            self.done.emit(None, "未导入 CMSIS Pack（型号队列为空）")
            return
        for i, dev in enumerate(self.devices, 1):
            if self._stop:
                self.done.emit(None, "已取消配对")
                return
            target = dev.part_number.strip().lower()
            self.progress.emit(i, total, dev.part_number)
            self.log_line.emit("配对 %d/%d：尝试型号 %s ..."
                               % (i, total, dev.part_number))
            session = None
            try:
                kwargs = {
                    "blocking": False,
                    "target_override": target,
                    "connect_mode": "attach",
                }
                if self.frequency:
                    kwargs["frequency"] = self.frequency
                if self.pack_path:
                    kwargs["pack"] = self.pack_path
                if self.probe_id:
                    kwargs["unique_id"] = self.probe_id
                    kwargs["return_first"] = False
                else:
                    kwargs["return_first"] = True
                session = ConnectHelper.session_with_chosen_probe(**kwargs)
                if session is None:
                    self.log_line.emit("  -> 无可用探针，跳过")
                    continue
                session.open()
                # 能 open 且 DP IDCODE 非 0 视为配对成功（硬件响应 SWD）。
                dp_idr = int(session.target.dp.dpidr.idr)
                if not dp_idr:
                    self.log_line.emit("  -> DP IDCODE 为 0，跳过")
                    continue
                self.log_line.emit("  -> 配对成功（DP IDCODE 0x%08X）" % dp_idr)
                self.done.emit(dev, "")
                return
            except Exception as exc:
                # 坏型号会在 open 中途 assert；记一行，继续下一个。
                self.log_line.emit("  -> 配对失败：%s: %s"
                                   % (type(exc).__name__, exc))
            finally:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
        self.done.emit(None, "")


class ConnWorker(QThread):
    """显式连接探针并持有 session（点"断开探针"才释放）。"""

    done = Signal(object, str)      # (Session or None, error_text)
    done_log = Signal(str)          # extra traceback for the log pane

    def __init__(self, probe_id: str, target: str, pack_path: str,
                 frequency: int = 0, parent=None):
        super().__init__(parent)
        self.probe_id = probe_id
        self.target = target
        self.pack_path = pack_path
        self.frequency = frequency

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
            if self.frequency:
                kwargs["frequency"] = self.frequency
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
        # 语言在构建 UI 前确定：state.json 里存了 lang 就直接用它，
        # 避免"先按中文构建再整体切换"的启动闪烁。
        set_lang(_saved_lang())
        self._ui_texts = []     # [(setter, key)]：语言切换时统一重设的静态文本
        self._tr_text(self.setWindowTitle, "win_title")
        self.resize(860, 680)
        self.worker = None
        self._probe_worker = None
        self.match_worker = None
        self.match_progress = None
        self._held_session = None    # 显式连接的探针 session（"连接探针"持有，仅供读型号）
        self._pending_flash = False  # 烧录前需先断开持有会话，断开完成后续烧

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_topbar())
        layout.addWidget(self._build_chip_selector())
        layout.addWidget(self._build_tabs(), 1)
        layout.addWidget(self._build_log())
        self.setCentralWidget(root)

        # pyocd 现为惰性导入，是否可用在首次枚举探针时报告（ProbeWorker）。
        self.log("烧录地址默认取自 CMSIS Pack 的 flash 起始（可勾选手动设置）；"
                 "布局参数仅打包页使用。")
        self._restore_state()
        self._hook_autosave()

    # ------------------------------------------------------------ 界面语言
    def _build_topbar(self) -> QWidget:
        """顶栏：右侧语言切换选择框（中文 / English），切换即时生效。"""
        row = QWidget(); r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.addStretch(1)
        self.lbl_lang = QLabel(tr("lbl_lang"))
        self.cmb_lang = QComboBox()
        for lang in LANGS:
            self.cmb_lang.addItem(LANG_LABELS[lang], lang)
        # 先填充并设好当前索引，再连接信号：首个 addItem 会触发
        # currentIndexChanged，此时日志区 / 探针区还没构建，不能进槽。
        idx = LANGS.index(get_lang())
        self.cmb_lang.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_lang.currentIndexChanged.connect(self._on_lang_changed)
        r.addWidget(self.lbl_lang)
        r.addWidget(self.cmb_lang)
        return row

    def _tr_text(self, setter, key: str):
        """注册静态文本：立即按当前语言设置，语言切换时统一重设。"""
        self._ui_texts.append((setter, key))
        setter(tr(key))

    def _retranslate(self):
        """语言切换后重设所有注册过的静态文本。"""
        for setter, key in self._ui_texts:
            setter(tr(key))
        # 探针下拉当前若显示的是占位项（枚举中 / pyocd 报错 / 空列表），
        # 按新语言重新枚举一次刷新占位文案；真实探针条目无需处理。
        if self.cmb_probe.count() and not (self.cmb_probe.currentData() or ""):
            self._refresh_probes()

    def _on_lang_changed(self, _idx=0):
        lang = self.cmb_lang.currentData() or "zh"
        if lang == get_lang():
            return
        set_lang(lang)
        self._retranslate()
        self._save_state()      # 语言随 state.json 持久化，下次启动直接生效
        self.log(tr("log_lang_switched") % LANG_LABELS[lang])

    # ------------------------------------------------------------ 状态持久化
    _STATE_KEYS = (
        "flash_image", "boot", "app", "key",
        "meta_base", "app_start", "chip_pack",
    )

    def _state_dict(self) -> dict:
        state = {
            "flash_image": self.ed_flash_image.text().strip(),
            "pack": self.ed_chip_pack.text().strip(),
            "target": self._mcu_target(),
            "boot": self.ed_boot.text().strip(),
            "app": self.ed_app.text().strip(),
            "key": self.ed_key.text().strip(),
            "export_out": self.ed_flash_image.text().strip(),
            "meta_base": self.ed_meta_base.text().strip(),
            "app_start": self.ed_app_start.text().strip(),
            "chip_pack": self.ed_chip_pack.text().strip(),
            "chip": self._chip(),
            "mcu": self.cmb_mcu.currentData() or "",
            "version": self.spin_version.value(),
            "chip_erase": self.cb_erase.isChecked(),
            "preset_seal": self.cb_seal.isChecked(),
            "rate_index": self.cmb_rate.currentIndex(),
            "auto_slow": self.cb_auto_slow.isChecked(),
            "manual_base": self.cb_manual_base.isChecked(),
            "flash_base": self.ed_flash_base.text().strip(),
            "lang": get_lang(),
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
        if "rate_index" in state:
            try:
                self.cmb_rate.setCurrentIndex(int(state["rate_index"]))
            except (TypeError, ValueError):
                pass
        if "auto_slow" in state:
            self.cb_auto_slow.setChecked(bool(state["auto_slow"]))
        # 烧录地址：手动设置标志与文本框值。非手动时仍以 pack 为准，
        # 所以只在手动勾选时恢复保存的地址，避免覆盖 pack 推导值。
        if "manual_base" in state:
            self.cb_manual_base.setChecked(bool(state["manual_base"]))
        if self.cb_manual_base.isChecked() and state.get("flash_base"):
            self.ed_flash_base.setText(str(state["flash_base"]))
        self.log("[提示] 已从 %s 恢复上次的路径与选项。" % STATE_PATH)

    def _hook_autosave(self):
        """任何路径/选项变化都立即落盘，无需手动保存。"""
        for key in self._STATE_KEYS:
            getattr(self, "ed_" + key).textChanged.connect(self._save_state)
        self.spin_version.valueChanged.connect(self._save_state)
        self.cb_erase.toggled.connect(self._save_state)
        self.cb_seal.toggled.connect(self._save_state)
        self.cmb_rate.currentIndexChanged.connect(self._save_state)
        self.cb_auto_slow.toggled.connect(self._save_state)
        self.cb_manual_base.toggled.connect(self._save_state)
        self.ed_flash_base.textChanged.connect(self._save_state)

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
        box = QGroupBox(tr("grp_chip"))
        self._tr_text(box.setTitle, "grp_chip")
        form = QFormLayout(box)

        self.pack_devices = []          # list[pack_devices.PackDevice]
        self.selected_device = None

        self.ed_chip_pack = QLineEdit()
        btn_pack = QPushButton()
        self._tr_text(btn_pack.setText, "btn_import_pack")
        btn_pack.clicked.connect(self._pick_chip_pack)
        prow = QWidget(); pr = QHBoxLayout(prow)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(self.ed_chip_pack, 1); pr.addWidget(btn_pack)

        # MCU 型号就是 pyocd target_override（pack 的 part_number 小写），
        # 同一个东西不分两栏。可编辑：导入 pack 后下拉选，没导入时可手输。
        self.cmb_mcu = QComboBox()
        self.cmb_mcu.setMinimumWidth(260)
        self.cmb_mcu.setEditable(True)
        self.cmb_mcu.setEditText("py32f040xb")
        self.cmb_mcu.currentIndexChanged.connect(self._on_mcu_changed)
        self.lbl_chip_geo = QLabel()
        self._tr_text(self.lbl_chip_geo.setText, "lbl_chip_geo_empty")

        # 读型号按钮移到 MCU 型号右方（与型号选择同一行）。
        self.btn_identity = QPushButton()
        self._tr_text(self.btn_identity.setText, "btn_identity")
        self.btn_identity.setEnabled(False)   # 枚举出探针后才可用
        self.btn_identity.clicked.connect(self._do_identity)
        mrow = QWidget(); mr = QHBoxLayout(mrow)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.addWidget(self.cmb_mcu, 1); mr.addWidget(self.btn_identity)

        lbl_pack = QLabel(); self._tr_text(lbl_pack.setText, "lbl_pack")
        lbl_mcu = QLabel(); self._tr_text(lbl_mcu.setText, "lbl_mcu")
        form.addRow(lbl_pack, prow)
        form.addRow(lbl_mcu, mrow)
        form.addRow("", self.lbl_chip_geo)
        return box

    def _pick_chip_pack(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("fd_pick_pack"), self._start_dir(self.ed_chip_pack),
            "CMSIS Pack (*.pack);;All files (*.*)")
        if not path:
            return
        try:
            from pack_devices import load_pack_devices
            devices = load_pack_devices(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("dlg_pack_parse_fail"), str(exc))
            self.log("Pack 解析失败：%s" % exc)
            return
        if not devices:
            QMessageBox.warning(self, tr("dlg_no_chips"), tr("msg_no_chips"))
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
            self.lbl_chip_geo.setText(tr("lbl_chip_geo_empty"))
            self._apply_pack_flash_base()       # 无 pack 时回退默认地址
            return
        d = self.selected_device
        geo = "Flash %d KiB / RAM %d KiB @ 0x%08X" % (
            d.flash_size // 1024, d.ram_size // 1024, d.ram_start)
        # 型号本身（currentData）就是 pyocd target，不再单独显示一份。
        self.lbl_chip_geo.setText(tr("geo_ready", geo))
        self._apply_pack_flash_base()           # 地址跟随 pack 的 flash region
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

    # ------------------------------------------------------------ 速率 / 地址
    @staticmethod
    def _format_rate(hz: int) -> str:
        if hz % 1000000 == 0:
            return "%d MHz" % (hz // 1000000)
        return "%d kHz" % (hz // 1000)

    def _rate_hz(self) -> int:
        return int(self.cmb_rate.currentData() or 0)

    def _on_rate_changed(self, _idx=0):
        # 速率切换立即写日志（Ali 要求：切换速率要 log 打印当前速率）。
        self.log("探针速率已设为 %s（下次连接 / 烧录生效）"
                 % self._format_rate(self._rate_hz()))

    def _apply_pack_flash_base(self):
        """未勾手动设置时，把烧录地址更新为当前 pack 芯片的 flash 起始。"""
        if self.cb_manual_base.isChecked():
            return
        base = (self.selected_device.flash_start if self.selected_device
                else core.FLASH_BASE)
        self.ed_flash_base.setText("%08X" % base)

    def _flash_base(self) -> int:
        """解析烧录地址文本框（十六进制，支持 0x 前缀）。"""
        return _parse_addr(self.ed_flash_base.text(), "烧录地址")

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
        box = QGroupBox(tr("grp_layout"))
        self._tr_text(box.setTitle, "grp_layout")
        form = QFormLayout(box)

        self.ed_meta_base = QLineEdit("%08X" % core.DEFAULT_META_BASE)
        self.ed_app_start = QLineEdit("%08X" % core.DEFAULT_APP_START)
        lbl_meta = QLabel(); self._tr_text(lbl_meta.setText, "lbl_meta")
        lbl_app = QLabel(); self._tr_text(lbl_app.setText, "lbl_app")
        addr_row = QWidget(); ar = QHBoxLayout(addr_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.addWidget(lbl_meta); ar.addWidget(self.ed_meta_base)
        ar.addWidget(lbl_app); ar.addWidget(self.ed_app_start)
        form.addRow(addr_row)
        return box

    def _chip(self) -> str:
        """打包/校验用的芯片标识；几何由 _chip_geometry() 一并提供。"""
        return self._chip_target()

    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_flash_tab(), tr("tab_flash"))
        self._tr_text(lambda t: tabs.setTabText(0, t), "tab_flash")
        tabs.addTab(self._build_export_tab(), tr("tab_pack"))
        self._tr_text(lambda t: tabs.setTabText(1, t), "tab_pack")
        return tabs

    def _build_flash_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        form = QFormLayout()

        self.ed_flash_image = QLineEdit(DEFAULT_OUT)
        btn_img = QPushButton()
        self._tr_text(btn_img.setText, "btn_browse")
        btn_img.clicked.connect(lambda: self._pick_into(
            self.ed_flash_image, tr("fd_pick_flash_bin")))
        irow = QWidget(); ir = QHBoxLayout(irow); ir.setContentsMargins(0, 0, 0, 0)
        ir.addWidget(self.ed_flash_image, 1); ir.addWidget(btn_img)

        self.cmb_probe = QComboBox()
        self.cmb_probe.setMinimumWidth(320)
        self.btn_refresh = QPushButton()
        self._tr_text(self.btn_refresh.setText, "btn_refresh")
        self.btn_refresh.clicked.connect(self._refresh_probes)
        # 显式连接/断开（仅供读型号复用，烧录用独立瞬时会话）。
        self.btn_conn = QPushButton()
        self._tr_text(self.btn_conn.setText, "btn_conn")
        self.btn_conn.setEnabled(False)       # 枚举出探针后才可用
        self.btn_conn.clicked.connect(self._toggle_conn)
        row = QWidget(); r = QHBoxLayout(row); r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.cmb_probe, 1); r.addWidget(self.btn_refresh)
        r.addWidget(self.btn_conn)

        # 探针速率（连接与通讯共用）。"失败时自动降速"控制重连是否降档。
        self.cmb_rate = QComboBox()
        for label, hz in RATE_OPTIONS:
            self.cmb_rate.addItem(label, hz)
        self.cmb_rate.setCurrentIndex(RATE_DEFAULT_INDEX)
        self.cmb_rate.currentIndexChanged.connect(self._on_rate_changed)
        self.cb_auto_slow = QCheckBox()
        self._tr_text(self.cb_auto_slow.setText, "cb_auto_slow")
        self.cb_auto_slow.setChecked(True)
        self.cb_auto_slow.toggled.connect(self._save_state)
        rate_row = QWidget(); rr = QHBoxLayout(rate_row)
        rr.setContentsMargins(0, 0, 0, 0)
        lbl_rate_hz = QLabel(); self._tr_text(lbl_rate_hz.setText, "lbl_rate_hz")
        rr.addWidget(lbl_rate_hz); rr.addWidget(self.cmb_rate)
        rr.addWidget(self.cb_auto_slow); rr.addStretch(1)

        # 烧录起始地址：默认从 CMSIS Pack 的 flash region 取，只读；
        # 勾选"手动设置"后可直接编辑。
        self.ed_flash_base = QLineEdit("%08X" % core.FLASH_BASE)
        self.ed_flash_base.setReadOnly(True)
        self.cb_manual_base = QCheckBox()
        self._tr_text(self.cb_manual_base.setText, "cb_manual_base")
        self.cb_manual_base.toggled.connect(
            lambda on: self.ed_flash_base.setReadOnly(not on))
        base_row = QWidget(); br2 = QHBoxLayout(base_row)
        br2.setContentsMargins(0, 0, 0, 0)
        lbl_base = QLabel(); self._tr_text(lbl_base.setText, "lbl_flash_base")
        br2.addWidget(lbl_base); br2.addWidget(self.ed_flash_base)
        br2.addWidget(self.cb_manual_base); br2.addStretch(1)

        self.cb_erase = QCheckBox()
        self._tr_text(self.cb_erase.setText, "cb_erase")
        self.cb_erase.setChecked(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.btn_verify_img = QPushButton()
        self._tr_text(self.btn_verify_img.setText, "btn_verify_img")
        self.btn_verify_img.clicked.connect(self._do_verify_file)
        self.btn_flash = QPushButton()
        self._tr_text(self.btn_flash.setText, "btn_flash")
        self.btn_flash.clicked.connect(self._do_flash)

        lbl_img = QLabel(); self._tr_text(lbl_img.setText, "lbl_flash_image")
        lbl_probe = QLabel(); self._tr_text(lbl_probe.setText, "lbl_probe")
        lbl_rate = QLabel(); self._tr_text(lbl_rate.setText, "lbl_rate")
        lbl_base_form = QLabel()
        self._tr_text(lbl_base_form.setText, "lbl_flash_base_form")
        form.addRow(lbl_img, irow)
        form.addRow(lbl_probe, row)
        form.addRow(lbl_rate, rate_row)
        form.addRow(lbl_base_form, base_row)
        v.addLayout(form)
        v.addWidget(self.cb_erase)
        v.addWidget(self.progress)
        rowbtn = QWidget(); rb = QHBoxLayout(rowbtn)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(self.btn_verify_img)
        rb.addWidget(self.btn_flash, 1)
        v.addWidget(rowbtn)
        v.addStretch(1)
        self._refresh_probes()
        return page

    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        # ---- 布局（芯片几何 + META/APP 起址，仅打包用） ----
        v.addWidget(self._build_layout())

        # ---- 固件镜像 ----
        box_in = QGroupBox(tr("grp_fw"))
        self._tr_text(box_in.setTitle, "grp_fw")
        form = QFormLayout(box_in)

        self.ed_boot = QLineEdit()
        self.ed_app = QLineEdit()
        self.ed_key = QLineEdit()
        self.spin_version = QSpinBox()
        self.spin_version.setRange(0, 0x7FFFFFFF)
        self.spin_version.setValue(0)

        b1 = QPushButton(); self._tr_text(b1.setText, "btn_browse")
        b1.clicked.connect(lambda: self._pick_into(
            self.ed_boot, tr("fd_pick_boot")))
        b2 = QPushButton(); self._tr_text(b2.setText, "btn_browse")
        b2.clicked.connect(lambda: self._pick_into(
            self.ed_app, tr("fd_pick_app")))
        b3 = QPushButton(); self._tr_text(b3.setText, "btn_browse")
        b3.clicked.connect(lambda: self._pick_into(
            self.ed_key, tr("fd_pick_key"), "*.hex"))

        row1 = QWidget(); r1 = QHBoxLayout(row1); r1.setContentsMargins(0, 0, 0, 0)
        r1.addWidget(self.ed_boot, 1); r1.addWidget(b1)
        row2 = QWidget(); r2 = QHBoxLayout(row2); r2.setContentsMargins(0, 0, 0, 0)
        r2.addWidget(self.ed_app, 1); r2.addWidget(b2)
        row3 = QWidget(); r3 = QHBoxLayout(row3); r3.setContentsMargins(0, 0, 0, 0)
        r3.addWidget(self.ed_key, 1); r3.addWidget(b3)

        self.cb_seal = QCheckBox()
        self._tr_text(self.cb_seal.setText, "cb_seal")
        self.cb_seal.setChecked(True)

        lbl_boot = QLabel(); self._tr_text(lbl_boot.setText, "lbl_boot")
        lbl_app_img = QLabel(); self._tr_text(lbl_app_img.setText, "lbl_app_img")
        lbl_key = QLabel(); self._tr_text(lbl_key.setText, "lbl_key")
        lbl_ver = QLabel(); self._tr_text(lbl_ver.setText, "lbl_version")
        form.addRow(lbl_boot, row1)
        form.addRow(lbl_app_img, row2)
        form.addRow(lbl_key, row3)
        form.addRow(lbl_ver, self.spin_version)
        form.addRow("", self.cb_seal)

        btn = QPushButton()
        self._tr_text(btn.setText, "btn_preview")
        btn.clicked.connect(self._preview)
        form.addRow("", btn)
        v.addWidget(box_in)

        # 导出路径不在此页设置：点导出时弹保存框，默认定位烧录页的整片 bin
        # 路径，保存后自动回填烧录页（见 _do_export / _do_meta_only）。
        btn_export = QPushButton()
        self._tr_text(btn_export.setText, "btn_export")
        btn_export.clicked.connect(self._do_export)
        btn_meta_only = QPushButton()
        self._tr_text(btn_meta_only.setText, "btn_meta_only")
        btn_meta_only.clicked.connect(self._do_meta_only)
        btn_verify = QPushButton()
        self._tr_text(btn_verify.setText, "btn_verify")
        btn_verify.clicked.connect(self._do_verify)

        btnrow = QWidget(); br = QHBoxLayout(btnrow)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(btn_export, 1)
        br.addWidget(btn_meta_only, 1)
        br.addWidget(btn_verify, 1)
        v.addWidget(btnrow)
        return page

    def _build_log(self) -> QWidget:
        box = QGroupBox(tr("grp_log"))
        self._tr_text(box.setTitle, "grp_log")
        v = QVBoxLayout(box)
        row = QWidget(); r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.addStretch(1)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(500)
        btn_copy = QPushButton()
        self._tr_text(btn_copy.setText, "btn_copy_log")
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.txt_log.toPlainText()))
        btn_clear = QPushButton()
        self._tr_text(btn_clear.setText, "btn_clear_log")
        btn_clear.clicked.connect(self.txt_log.clear)
        r.addWidget(btn_copy)
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
            QMessageBox.critical(self, tr("dlg_gen_fail"), str(exc))
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
            QMessageBox.critical(self, tr("dlg_parse_fail"),
                                 tr("msg_read_image_fail", str(exc)))
            self.log("解析失败：%s" % exc)
            return
        try:
            meta_base, app_start = self._layout_addrs()
            layout = core.resolve_layout(meta_base, app_start, self._chip(),
                                         self._chip_geometry())
        except core.ImageError as exc:
            QMessageBox.critical(self, tr("dlg_parse_fail"), str(exc))
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
            QMessageBox.critical(self, tr("dlg_parse_fail"), str(exc))
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
        self.cmb_probe.addItem(tr("probe_enum"), "")
        self.btn_identity.setEnabled(False)
        self._probe_worker = ProbeWorker()
        self._probe_worker.done.connect(self._probes_done)
        self._probe_worker.start()

    def _probes_done(self, probes, error):
        self.cmb_probe.clear()
        if error:
            self.cmb_probe.addItem(tr("probe_pyocd_err", error), "")
            self.log("[提示] pyocd 未安装或加载失败（%s），打包页可正常使用，"
                     "烧录页需先 pip install pyocd" % error)
            return
        if not probes:
            # 枚举为空不影响烧录：probe_id 为空时 FlashWorker 会用
            # return_first=True 让 pyocd 自动取第一个探针。
            self.cmb_probe.addItem(tr("probe_empty"), "")
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
            QMessageBox.information(self, tr("dlg_busy"), tr("msg_busy"))
            return
        probe_id = self.cmb_probe.currentData() or ""
        if not probe_id:
            QMessageBox.warning(self, tr("dlg_no_probe"), tr("msg_no_probe"))
            return
        if not self._target_ready():
            return
        self.btn_conn.setEnabled(False)
        self.btn_conn.setText(tr("btn_connecting"))
        self.log("连接探针（attach 模式，不打断目标运行）...")
        self.conn_worker = ConnWorker(
            probe_id, self._mcu_target(), self.ed_chip_pack.text().strip(),
            frequency=self._rate_hz())
        self.conn_worker.done_log.connect(
            lambda tb: self.log("[堆栈]\n" + tb.strip()))
        self.conn_worker.done.connect(self._connect_done)
        self.conn_worker.start()

    def _connect_done(self, session, error):
        if session is None or error:
            self.btn_conn.setText(tr("btn_conn"))
            self.btn_conn.setEnabled(True)
            QMessageBox.critical(self, tr("dlg_conn_fail"),
                                 error or tr("dlg_unknown_err"))
            self.log("探针连接失败：%s" % error)
            # 探针可能已从总线消失（固件挂死重枚举），刷新一次列表。
            self._refresh_probes()
            return
        self._held_session = session
        self._set_conn_state(True)
        self.log("探针已连接并持有；读型号将复用本次连接，"
                 "烧录使用独立会话（点击烧录时会自动断开本连接）。")

    def _disconnect_probe(self):
        """关闭持有的 session 并重新枚举。close 可能阻塞，放后台线程。"""
        if self._held_session is None:
            return
        self.btn_conn.setEnabled(False)
        self.btn_conn.setText(tr("btn_disconnecting"))
        self.disc_worker = DiscWorker(self._held_session)
        self.disc_worker.done.connect(self._disconnect_done)
        self.disc_worker.start()

    def _disconnect_done(self, error):
        had = self._held_session is not None
        self._held_session = None
        self._set_conn_state(False)
        self.log("探针已断开。" if not error else
                 "断开时出错（不影响重新连接）：%s" % error)
        if had:
            self._refresh_probes()
        # 烧录被"先断开"挂起：持有会话释放后立即用独立会话续烧。
        # attach 持有会话不适合整片擦除（SWD 易 FAULT / No ACK 且无重试），
        # 瞬时会话走 halt + 降时钟三级重试，Ali 上板已验证可自愈。
        if self._pending_flash:
            self._pending_flash = False
            self._start_flash()

    def _set_conn_state(self, connected: bool):
        self.btn_conn.setText(tr("btn_disc") if connected else tr("btn_conn"))
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
            QMessageBox.warning(self, tr("dlg_target_missing"),
                                tr("msg_target_missing"))
            return False
        if pack:
            return True
        mods, err = _ensure_pyocd()
        if mods is None:
            QMessageBox.warning(self, tr("dlg_pyocd_missing"),
                                tr("msg_pyocd_missing", err))
            return False
        from pyocd.target import TARGET
        if target not in TARGET:
            QMessageBox.warning(
                self, tr("dlg_pack_needed"), tr("msg_pack_needed", target))
            return False
        return True

    def _do_identity(self):
        """按 CMSIS Pack 型号队列逐个配对，成功即停，显示进度条。

        配对判据：用该型号能成功建立 pyocd 会话并读到非零 DP IDCODE，
        即硬件确实响应 SWD。pyocd 报告的 part_number 是回填传入值，
        不作为硬件型号依据（见 CR-0085 的边界说明）。
        """
        if self.worker is not None and getattr(self.worker, "isRunning", lambda: False)():
            QMessageBox.information(self, tr("dlg_busy"), tr("msg_busy"))
            return
        if self.match_worker is not None and self.match_worker.isRunning():
            QMessageBox.information(self, tr("dlg_busy"), tr("msg_busy_match"))
            return
        if not self.pack_devices:
            QMessageBox.warning(self, tr("dlg_no_pack"), tr("msg_no_pack"))
            return
        probe_id = self.cmb_probe.currentData() or ""
        self.btn_identity.setEnabled(False)
        total = len(self.pack_devices)
        self.match_progress = QProgressDialog(
            tr("pd_match"), tr("pd_cancel"), 0, total, self)
        self.match_progress.setWindowTitle(tr("pd_match_title"))
        self.match_progress.setMinimumDuration(0)
        self.match_progress.setValue(0)
        self.match_worker = MatchWorker(
            probe_id, self.ed_chip_pack.text().strip(),
            self.pack_devices, frequency=self._rate_hz())
        self.match_worker.progress.connect(self._match_progress)
        self.match_worker.log_line.connect(self.log)
        self.match_worker.done.connect(self._match_done)
        self.match_progress.canceled.connect(self.match_worker.stop)
        self.match_worker.start()

    def _match_progress(self, index: int, total: int, model: str):
        if self.match_progress is not None:
            self.match_progress.setLabelText(
                tr("pd_match_progress", index, total, model))
            self.match_progress.setValue(index)

    def _match_done(self, dev, error: str):
        if self.match_progress is not None:
            self.match_progress.close()
            self.match_progress = None
        self.btn_identity.setEnabled(True)
        if error and error != "已取消配对":
            self.log("配对失败：%s" % error)
        if dev is None:
            if error == "已取消配对":
                return
            QMessageBox.warning(self, tr("dlg_mcu_model"), tr("msg_no_match"))
            self.log("未匹配到正确MCU型号（已尝试 %d 个型号）"
                     % len(self.pack_devices))
            self._refresh_probes()
            return
        # 配对成功：走统一的匹配结果处理（自动选中 + 简洁提示）。
        from mcu_identity import McuIdentity
        ident = McuIdentity(part_number=dev.part_number,
                            vendor=getattr(dev, "vendor", "") or "")
        self._identity_done(ident, "")

    def _compatible_models(self, dev) -> list:
        """返回 pack 中 flash / RAM 容量与 flash 起始都一致的型号名列表。

        这些型号在 SWD 总线上无法区分，用同一个型号配对成功只说明几何
        匹配，不代表是板上真实料号；列出供操作员核对（不猜型号）。
        """
        out = []
        for d in self.pack_devices:
            if (d.flash_size == dev.flash_size and
                    d.ram_size == dev.ram_size and
                    d.flash_start == dev.flash_start):
                out.append(d.part_number)
        return out

    def _identity_done(self, ident, error):
        self.btn_identity.setEnabled(True)
        if error or ident is None:
            target = self._mcu_target() or tr("no_model_selected")
            hint = tr("hint_target", target)
            # AssertionError 无消息（pyocd 对部分 pack 芯片的兼容缺陷，
            # 如 GD32F50x pack 的 128KB 型号），堆栈已写入日志。
            if error.startswith("AssertionError"):
                hint += tr("hint_assert")
            hint += tr("hint_offline")
            QMessageBox.critical(self, tr("dlg_read_fail"),
                                 (error or tr("dlg_unknown_err")) + hint)
            self.log("读取 MCU 型号失败（target=%s）：%s" % (target, error))
            self._refresh_probes()
            return
        # 只读 pyocd target 报告的型号，不凭原始 ID 猜型号。
        read_model = (ident.part_number or "").strip()
        matched = ([d for d in self.pack_devices
                    if d.part_number.lower() == read_model.lower()]
                   if read_model else [])
        if len(matched) == 1:
            # 唯一匹配：自动切换型号，简洁提示（不弹冗长确认框）。
            dev = matched[0]
            idx = self.cmb_mcu.findData(dev.part_number)
            if idx >= 0:
                self.cmb_mcu.setCurrentIndex(idx)
            else:
                self.cmb_mcu.setEditText(dev.part_number.lower())
            compat = self._compatible_models(dev)
            msg = tr("msg_match_ok", dev.part_number)
            if compat:
                msg += "\n" + tr("msg_compat", "、".join(compat))
            QMessageBox.information(self, tr("dlg_mcu_model"), msg)
            self.log(msg.replace("\n", "；"))
        else:
            # 无回值或在 pack 中匹配不到（0 个 / 多个）：明确提示未适配。
            msg = tr("msg_not_adapted", read_model or tr("raw_no_model"))
            if not read_model:
                msg += " " + tr("msg_raw_id")
            QMessageBox.warning(self, tr("dlg_mcu_model"), msg)
            self.log(msg + "\n" + ident.summary())

    def _do_flash(self):
        mods, err = _ensure_pyocd()
        if mods is None:
            QMessageBox.critical(self, tr("dlg_dep_missing"),
                                 tr("msg_dep_missing", err))
            return
        if self.worker is not None and self.worker.isRunning():
            return
        image_path = self.ed_flash_image.text().strip()
        if not image_path:
            QMessageBox.critical(self, tr("dlg_flash_fail"),
                                 tr("msg_pick_flash_bin"))
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
                self, tr("dlg_verify_pre_fail"),
                tr("msg_verify_pre_fail", str(exc)))
            self.log("烧前校验失败：%s" % exc)
            return
        if not self._target_ready():
            return
        # 烧录一律用独立瞬时会话：显式持有的 attach 会话是为读型号设计的
        # （不 halt 目标），整片擦除期间 SWD 极易 FAULT / No ACK，且持有
        # 会话模式不做失败重试（链路状态未知），Ali 上板连续 3 次烧录失败。
        # 若当前有持有连接，先异步断开，断开回调里再续烧（瞬时会话带
        # 降时钟三级重试，已验证可自愈）。
        if self._held_session is not None:
            self._pending_flash = True
            self.log("烧录前先断开 attach 持有会话，随后用独立会话烧录 ...")
            self._disconnect_probe()
            return
        self._start_flash()

    def _start_flash(self):
        """用独立瞬时会话启动烧录（halt + 降时钟重试，不复用持有会话）。"""
        image_path = self.ed_flash_image.text().strip()
        self.progress.setValue(0)
        self.btn_flash.setEnabled(False)
        self.worker = FlashWorker(
            image_path,
            self.cmb_probe.currentData() or "",
            self._mcu_target(),
            self.ed_chip_pack.text().strip(),
            self.cb_erase.isChecked(),
            session=None,               # 始终新建会话，不复用 attach 持有会话
            frequency=self._rate_hz(),
            auto_slow=self.cb_auto_slow.isChecked(),
            base_address=self._flash_base(),
        )
        self.worker.log_line.connect(self.log)
        self.worker.progress_value.connect(self.progress.setValue)
        self.worker.done.connect(self._flash_done)
        self.worker.start()

    def _flash_done(self, ok: bool, msg: str):
        self.btn_flash.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            QMessageBox.information(self, tr("dlg_done"), msg)
        else:
            QMessageBox.critical(self, tr("dlg_flash_fail"), msg)
        # 烧录用独立瞬时会话，用完即关；持有会话只服务读型号，不受影响。

    def _do_export(self):
        # 默认定位烧录页的整片 bin 路径；用户确认后回填烧录页并生成。
        start = self.ed_flash_image.text().strip() or DEFAULT_OUT
        path, _ = QFileDialog.getSaveFileName(
            self, tr("fd_export_bin"), start, "*.bin")
        if not path:
            return
        if self._build(path, ""):
            self.ed_flash_image.setText(path)     # 自动更新烧录页路径
            QMessageBox.information(self, tr("dlg_done"),
                                    tr("msg_export_ok", path))

    def _do_meta_only(self):
        start = os.path.join(self._start_dir(self.ed_flash_image),
                             "meta_record.bin")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("fd_export_meta"), start, "*.bin")
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
            QMessageBox.critical(self, tr("dlg_export_fail"), str(exc))

    def _do_verify_file(self):
        """① 烧录页：按布局起址校验所选整片 bin。"""
        path = self.ed_flash_image.text().strip()
        if not path:
            QMessageBox.critical(self, tr("dlg_verify_fail"),
                                 tr("msg_pick_verify_bin"))
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
            QMessageBox.information(self, tr("dlg_done"),
                                    tr("msg_verify_ok", path))
        except (core.ImageError, OSError) as exc:
            QMessageBox.critical(self, tr("dlg_verify_fail"), str(exc))
            self.log("校验失败：%s" % exc)

    def _do_verify(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("fd_pick_verify"), self._start_dir(self.ed_flash_image),
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
            QMessageBox.information(self, tr("dlg_done"),
                                    tr("msg_verify_ok", path))
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
    splash.showMessage(tr("splash_msg"), Qt.AlignCenter, Qt.darkGray)
    splash.show()
    app.processEvents()
    win = MainWindow()
    win.show()
    splash.finish(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
