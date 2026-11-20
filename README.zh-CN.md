# QuillaImage

Quilla 量产镜像合并 / 烧录上位机。把 QBOOT 与 APP 合成为带合法 META/manifest
的整片 bin，并可选用 [pyocd](https://github.com/pyocd/pyOCD) 经 SWD 烧录。
纯上位机工具，不改动 QBOOT、QIAP 协议或设备端安全启动策略。

## 功能

| 页面 | 功能 |
|---|---|
| **烧录** | 选择已有整片 bin → 烧前校验（向量表、META CRC、镜像 SHA-256）→ 经 pyocd 一次性烧到 `0x08000000`。不重新生成 manifest。 |
| **合并** | 选择 QBOOT + APP（SEALED/SEC 另需私钥）→ 生成 manifest → 合成整片 bin；也可仅导出 256 B META 记录，或校验已有镜像。 |

### 共用芯片选择区（CMSIS-Pack）

窗口顶部的「芯片 / CMSIS Pack」两页共用：

- 导入厂商 `.pack`，MCU 列表从 pack 的 device 定义解析。
- 选型号后，其 Flash/RAM 容量（来自 pack 的 memory map）用于打包时的向量表
  校验；型号名本身即 pyocd target（同一个可编辑字段，不再单列目标栏）。
- **从目标板读取**（烧录页，仅列出探针后可用）读取内核 DP IDCODE、目标已知
  的 part number/vendor，以及常见 DBGMCU_IDCODE 寄存器。**不会凭原始 ID 猜
  型号**：pyocd 报出型号时弹窗询问是否采用（防止手动选错），否则只显示原始
  ID，型号仍需手动选。

META 起址 / APP 起址只在打包页；烧录页烧已打包好的镜像，校验时沿用这些值。
命令行仍保留内建预设 `pack` / `verify --target gd32f502ve`。
CMSIS-Pack 里的任意芯片都能通过 GUI 打包，无需改代码。

### 默认镜像布局（PY32F040xB）

| 分区 | 地址 | 大小 |
|---|---|---|
| QBOOT | `0x08000000` | 24 KiB（固定） |
| META | `0x08006000` | 4 KiB（两个 2 KiB slot，256 B 记录） |
| APP | `0x08007000` | 100 KiB（至 `0x08020000`） |

manifest 覆盖 `SHA-256(APP[0:image_len])`。工具从 QBOOT banner 识别变体
（`[CHECK]` / `[SEALED]` / `[SEC]`），无法识别时拒绝合并。

- **CHECK**：签名与 seal 全零、无防回滚下限——只校验完整性，不是安全启动。
- **SEALED / SEC**：需 32 字节 Ed25519 seed（`private.hex`），且 APP 在偏移
  `0x400` 带 `QAV1` 安全版本记录。SEALED 恒定预置 seal，首启跳过约 20 s 验签
  （seal 与签名同源：同一私钥、同一描述子；设备端对失配 seal 自动回落全量
  验签并自写 seal）。

## 安装与运行

核心打包器**不需要任何第三方包**：

```bash
python -m package.image selftest       # RFC 8032 向量 + 合并/校验自检
python -m package.image --help         # pack / verify 命令行
python -m package.image pack --boot QBOOT_CHECK.bin --app app.bin --out combined.bin
```

GUI 与烧录需要：

```bash
pip install -e .[gui]        # PySide6 + pyocd
python gui.py                # 或 python gui.py --check 检查依赖
```

独立应用：从 Releases 下载整个 `QuillaImage/` 目录，运行其中的
`QuillaImage.exe`，无需安装 Python。这是 PyInstaller **onedir** 构建——请把
整个目录一起拷贝，exe 会从旁边的 `_internal/` 加载 Qt / pyocd DLL。onedir
免去了单文件版每次启动的解压过程，启动更快。设置保存在 **exe 同目录**的
`state.json`（源码运行时在仓库根目录），工具连同配置可整体拷贝、带到别的
机器；删除该文件即恢复默认。目录内的 `SHA256SUMS.txt` 覆盖所有分发文件。

## 发行包构建信息

每个发布压缩包由 `tools/build_exe.py` 构建，并以随包的 `SHA256SUMS.txt`
校验。以下构建信息对应**当前发布版本（v1.1.19）**；构建脚本从同一个
解释器取工具链版本，重建结果一致。

| 组件 | 版本 |
|---|---|
| QuillaImage 发布版本 | v1.1.19 |
| Windows 目标 | x86-64，PyInstaller **onedir** |
| Python | 3.14.7 |
| PyInstaller | 6.22.3 |
| PySide6 (Qt) | 6.11.2 |
| pyocd | 0.45.1 |
| capstone | 5.0.7 |
| pyserial | 3.5 |

### 发行包二进制白名单（DLL / PYD）

压缩包内有且仅有下列二进制，没有别的。分发的压缩包里若出现本清单之外
的二进制，则不属于本构建。

- **Qt 核心**：`Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`、`Qt6Svg.dll`
- **PySide6 绑定**：`QtCore.pyd`、`QtGui.pyd`、`QtWidgets.pyd`、
  `QtSvg.pyd`、`pyside6.abi3.dll`、`shiboken6/`（`Shiboken.pyd`、
  `shiboken6.abi3.dll`）
- **Qt 插件**：`platforms/`（qwindows、qdirect2d、qminimal、qoffscreen）、
  `imageformats/`（qico、qsvg）、`styles/`（qmodernwindowsstyle）、
  `tls/`（qschannelbackend）
- **Python 运行时**：`python314.dll`、`python3.dll` 及 CPython 标准库
  扩展（`_asyncio`、`_bz2`、`_ctypes`、`_elementtree`、`_hashlib`、
  `_lzma`、`_overlapped`、`_queue`、`_socket`、`_ssl`、`_wmi`、`_zstd`、
  `pyexpat`、`select`、`unicodedata`、`libffi-8.dll`）
- **MSVC 运行时**：`MSVCP140.dll`（含 `_1`/`_2`）、`VCRUNTIME140.dll`
  （含 `_1`）
- **pyocd 烧录链路**：`capstone/lib/capstone.dll`、
  `cmsis_pack_manager/cmsis_pack_manager.dll`、`hid.cp314-win_amd64.pyd`、
  `psutil/_psutil_windows.pyd`、`yaml/_yaml...pyd`、
  `libusb-1.0.dll` / `libusb0.dll` / `libusb_package/libusb-1.0.dll`
- **TLS（OpenSSL）**：`libcrypto-3*.dll`、`libssl-3*.dll`（CPython `_ssl`
  也会拉入）

Qt QML/Quick/PDF、OpenGL（含软件光栅化器）、虚拟键盘、翻译和用不到的
图片格式插件在构建期即被剔除，因此**不在**发行包内。今后某版发布若新增
二进制，本节和 release notes 会明确说明。

## 边界

- 工具只准备镜像，**不向设备写入密钥**，也不校验所选密钥是否与某片 QBOOT
  里烧的公钥匹配。使用仓库默认公开演示密钥时，签名镜像不具备真实性保证。
- CHECK 模式只校验完整性，不构成安全启动。
- 未声称任何 IEC 61508 / ISO 26262 认证或 SIL 等级。
- pyocd 烧录面向小批量 / 工程用途；量产线建议用自己的烧录器，配合本工具
  的「仅校验」功能。

### 分发体积

PyInstaller 的 Qt hook 会把整套 Qt 运行时（约 92 MiB）都收进来。构建脚本
会删除这个 GUI 根本不加载的组件——QML/Quick/PDF、OpenGL（含 20 MiB 的
软件光栅化器）、虚拟键盘、翻译和用不到的图片格式插件——约省 50 MiB。
最终目录约 96 MiB，只保留 QtCore / QtGui / QtWidgets / QtSvg 与 Windows
平台插件。以后若加入 QML 或新图片格式，需同步改
`tools/build_exe.py` 的 `PRUNE_FILES`。

## 许可证

QuillaImage 自身采用 Apache-2.0（见 [LICENSE](LICENSE)）。内置 Ed25519
签名模块与 [Quilla](https://github.com/JiChiRen/Quilla) 上游
`tools/quilla_ed25519_sign.py` 逐字一致，见 [NOTICE](NOTICE)。

随 exe 分发的 **Qt 6 / PySide6 6.11.2** 动态库按 LGPLv3 授权，均为官方
原始二进制，**未做任何修改、未重新链接**。二进制分发包含 `LICENSES-Qt.txt`
以及 `dist/QuillaImage/licenses/` 下完整的 `LGPL-3.0.txt` / `GPL-3.0.txt` 文本；
对应 Qt 源码获取地址见 `LICENSES-Qt.txt`。该文件同时列出各 DLL 哈希与
LGPLv3 §4(d)(1) 允许的库替换途径。
