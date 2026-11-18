# QuillaImage

Quilla 量产镜像合并 / 烧录上位机。把 QBOOT 与 APP 合成为带合法 META/manifest
的整片 bin，并可选用 [pyocd](https://github.com/pyocd/pyOCD) 经 SWD 烧录。
纯上位机工具，不改动 QBOOT、QIAP 协议或设备端安全启动策略。

## 功能

| 页面 | 功能 |
|---|---|
| **烧录** | 选择已有整片 bin → 烧前校验（向量表、META CRC、镜像 SHA-256）→ 经 pyocd 一次性烧到 `0x08000000`。不重新生成 manifest。 |
| **合并** | 选择 QBOOT + APP（SEALED/SEC 另需私钥）→ 生成 manifest → 合成整片 bin；也可仅导出 256 B META 记录，或校验已有镜像。 |

两页共用 META 起址 / APP 起址。默认值对应 PY32F040xB 参考布局；改地址前须确认
QBOOT 构建与 APP 链接基址一致。

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
  `0x400` 带 `QAV1` 安全版本记录。SEALED 可预置 seal，首启跳过约 20 s 验签。

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

独立 exe：从 Releases 下载 `quilla-image.exe`，无需安装 Python。GUI 把上次
路径存在 `~/.quilla_image/state.json`（删除即恢复默认），不会往 exe 目录写文件。

## 边界

- 工具只准备镜像，**不向设备写入密钥**，也不校验所选密钥是否与某片 QBOOT
  里烧的公钥匹配。使用仓库默认公开演示密钥时，签名镜像不具备真实性保证。
- CHECK 模式只校验完整性，不构成安全启动。
- 未声称任何 IEC 61508 / ISO 26262 认证或 SIL 等级。
- pyocd 烧录面向小批量 / 工程用途；量产线建议用自己的烧录器，配合本工具
  的「仅校验」功能。

## 许可证

Apache-2.0（见 [LICENSE](LICENSE)）。内置的 Ed25519 签名模块与
[Quilla](https://github.com/JiChiRen/Quilla) 上游
`tools/quilla_ed25519_sign.py` 逐字一致，见 [NOTICE](NOTICE)。
