# 剔除不需要的文件 / DLL —— 方法学

适用于 PyInstaller onedir 产物（`dist/quilla-image/`）。目标：把"靠猜删"
变成"可复现判定 + 分级验证"。

## 四种方法，按可信度排序

### 方法 1：运行时模块快照（最可靠，主用）

工具：`tools/audit_runtime.py`

原理：PyInstaller onedir 的 DLL 是按需 `LoadLibrary` 的。启动 App、
**把每个功能都点一遍**，然后用 Win32 `CreateToolhelp32Snapshot` 抓进程
实际加载的模块表，跟目录内文件清单做差集。未加载者 = 裁剪候选。

```bash
# 1) 启动 App，逐项操作（见 tools/smoke.py 清单）：
#    打包 CHECK/SEALED/SEC、校验、烧录页加载探针、密钥工具
# 2) App 保持运行时：
python tools/audit_runtime.py
```

输出示例（本项目实测）：

```
pid 23568: loaded modules 119, app files 82
loaded from app dir: 40 files / 63.5 MiB
NOT loaded        : 42 files / 32.9 MiB
```

**局限（必须知道）**：
- 插件是按需加载且可能加载后立即释放，`Toolhelp32Snapshot` 快照时刻
  可能错过（`qsvg.dll` 就是图标渲染用的，实测未出现在快照里）。
- 只在特定条件下加载的模块（USB 探针、HTTPS、反汇编）不会出现在
  普通用法快照中。

所以：**"未加载"只说明"这次没用到"，不等于"永远用不到"。**

### 方法 2：静态依赖树（辅助，查 DLL 链）

```bash
# Windows SDK 自带：
dumpbin /dependents dist/quilla-image/quilla-image.exe
dumpbin /dependents dist/quilla-image/_internal/PySide6/Qt6Gui.dll
```

或第三方 Dependencies（Dependency Walker 现代版）。能列出 PE 静态
依赖，适合确认"删掉 A 会不会断 B"。缺点：抓不到 `LoadLibrary` 动态
加载的 Qt 插件。

### 方法 3：源头控制（最干净，但要改构建）

PyInstaller 的 `hook-PySide6.py` 通过
`pyside6_library_info.collect_extra_binaries()` 硬收 ANGLE、
`opengl32sw.dll`、ICU——跟代码 import 无关。要源头裁剪只能：

- 写自定义 hook 放到 `--additional-hooks-dir`，覆盖默认 hook；或
- 在 spec 文件里操作 `binaries` 列表过滤；或
- 保持现在 `tools/build_exe.py::prune_qt()` 的"构建后白名单删除"。

本项目选第三种：可读、可测、不依赖 PyInstaller 内部 API 稳定。

### 方法 4：分组二分 + 冒烟（最终验证，必做）

任何候选删除都要按族分组，删一组跑一次 `tools/smoke.py` 全部步骤。
通过再删下一组。失败则回退该组。这是唯一能证明"删了没事"的方法。

## 候选分类规则（实测经验）

| 类别 | 例子 | 处置 |
|---|---|---|
| 明确用不到的大件 | QML/Quick/PDF、软件光栅化器、翻译、虚拟键盘 | 直接列 deny-list（已固化在 `prune_qt()`） |
| 重复/冗余副本 | 同名 DLL 不同来源（哈希不同！） | 谨慎，需跑功能验证；本项目未删 |
| 条件加载 | `capstone.dll`、`svd_data.zip`、探针 DLL | 保留——烧录/调试用 |
| 插件类 | `qsvg.dll`、`qico.dll`、平台插件 | 保留——快照抓不到但图标/主题要用 |
| 纯数据/许可证 | `licenses/`、`*.dist-info` | 保留——LGPL 合规要求 |
| Python 模块 | `base_library.zip`、`_decimal.pyd` | 保留——虽未出现在模块表，但可能被引擎延迟导入 |

## 当前结果

`prune_qt()` 已按类别 1 删除约 50 MiB：

| 阶段 | 大小 | 文件数 |
|---|---|---|
| hook 全收 | 147 MiB | 203 |
| 类别 1 裁剪后 | 96 MiB | 82 |
| 运行时快照显示"未加载" | 32.9 MiB / 42 文件 | — |

剩余 32.9 MiB 中，绝大多数属于"条件加载 / 插件 / 数据"，**不建议再删**。
真正能安全拿掉的只有少量明确无用的（如 `_multiprocessing.pyd`、
`wcwidth/textwrap.py,cover`、多余的 license 副本），收益 < 1 MiB，不值得
增加回归风险。


## pyd 文件的判定（Python C 扩展，比 DLL 好判）

`.pyd` 是 Windows 上的 Python 扩展模块，由 `import` 触发加载。除了运行时
快照，还能用**静态依赖图**精确判定——这是 DLL 做不到的：

```bash
# PyInstaller 把依赖图写在 Analysis-00.toc（构建后保留）
python - <<'PY'
import ast
d = ast.literal_eval(open("build/freeze/build/quilla-image/Analysis-00.toc",
                          encoding="utf-8").read())
entries = d[14] + d[19]     # 二进制模块 + 纯 Python 模块
for name in ("decimal", "_decimal", "cffi", "hid"):
    hits = [e[0] for e in entries if e and e[0] == name]
    print(name, hits or "NOT in graph")
PY
```

判定规则：

| 情况 | 结论 |
|---|---|
| 模块在图中、C 加速 pyd 不在 | 可删，自动回退纯 Python（稍慢，功能不变） |
| 模块和 pyd 都不在图中 | 可删（无 importer） |
| 有模块在图中引用该 pyd | **必须保留**，即使运行时快照显示未加载 |

本项目实测（25 个 pyd / 15.0 MiB，22 个已加载）：

- 删：`_decimal.pyd` (285 KiB)、`_multiprocessing.pyd` (38 KiB)、
  `_cffi_backend.cp314-win_amd64.pyd` (177 KiB) —— 纯 Python 回退 /
  无 importer，合计约 0.5 MiB。
- 留：`hid.cp314-win_amd64.pyd` —— `pyocd` 的 HID 探针后端和
  `pyserial` 的 CP2110 handler 会条件加载它；快照没抓到但绝不能删。

## 关于"110M 精简到 15M"类文章（CSDN 实战帖）

参考了两篇 CSDN 文章（110M→15M / 110M→46M），方法是同一套：删
Qt 多余模块/插件/翻译 + 功能回归，并强调"重命名为 .bak 而非直接删"
和"每删一组就完整测试"。这跟本项目的 `prune_qt()` + `smoke.py` 一致。

关键差异在**压缩**：文章里的 46M 是 Inno Setup 打包后的安装包体积，
15M 是进一步 solid/LZMA2 压缩（且砍掉更多组件）的结果；我们的 96 MiB
是**未压缩目录**。实测对本目录做 ZIP deflate-9 已得到 **46.4 MiB**，
与那篇文章的 46M 档吻合。也就是说：

- 若目标是"分发包小"，上 Inno Setup / 7z solid 压缩即可拿到 ~46 MiB；
- 若目标是"解压后占用小"，只能继续砍组件，但本项目已到收益拐点；
- 文章未公开完整的 15M 文件清单（付费墙），其数字不可复现，不作为目标。

## 一句话结论

**方法 1 找候选，方法 2 查依赖，方法 4 验证；方法 3 决定是否值得改构建。**
本项目已到收益拐点：再压 1 MiB 要冒功能损坏的风险，不划算。
