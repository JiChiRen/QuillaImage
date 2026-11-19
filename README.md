# QuillaImage

**English** · [中文](README.zh-CN.md)

Standalone Windows GUI for preparing and flashing Quilla manufacturing images.
It merges a QBOOT binary and an APP binary into one full-chip image with a
valid META/manifest record, verifies the result, and optionally flashes it
over SWD with [pyocd](https://github.com/pyocd/pyOCD).

This is a host-side production tool. It does not change QBOOT, the QIAP
protocol, or the on-target secure-boot policy.

## What it does

| Tab | Function |
|---|---|
| **Flash** | Pick an existing full-chip `combined.bin` → verify it (vector tables, META CRC, image SHA-256) → flash once at `0x08000000` via pyocd. Does **not** regenerate the manifest. |
| **Pack** | Pick QBOOT + APP (+ private key for SEALED/SEC) → generate the manifest → produce one full-chip image for the Flash tab. Can also export only the 256-byte META record, or verify an existing image. |

Both tabs share the META base / APP start layout fields. Defaults match the
PY32F040xB reference layout (see below); change them only when the QBOOT build
and the APP linker base agree with the new values.

### Supported targets

| Target | Flash | RAM | Full image |
|---|---|---|---|
| `py32f040xb` (default) | 128 KiB | 16 KiB | 128 KiB |
| `gd32f502ve` | 512 KiB | 64 KiB | 512 KiB |

Select the chip in the shared layout box. Switching it updates the default
META / APP addresses (both `0x08006000` / `0x08007000`) and the vector-table
check uses that chip's RAM and flash boundaries. The CLI takes
`--target gd32f502ve` for `pack` and `verify`.

### Image layout (PY32F040xB default)

| Region | Address | Size |
|---|---|---|
| QBOOT | `0x08000000` | 24 KiB (fixed) |
| META | `0x08006000` | 4 KiB (two 2 KiB slots, 256 B records) |
| APP | `0x08007000` | 100 KiB (to `0x08020000`) |

The manifest covers `SHA-256(APP[0:image_len])`. The tool detects the QBOOT
variant from its banner (`[CHECK]` / `[SEALED]` / `[SEC]`) and refuses to pack
if the variant cannot be determined.

- **CHECK**: zero signature, zero seal, no anti-rollback floor — integrity
  only, not authenticated boot.
- **SEALED / SEC**: requires a 32-byte Ed25519 seed (`private.hex`) and an APP
  image carrying the `QAV1` security-version record at offset `0x400`. SEALED
  may preset the seal so first boot skips the ~20 s verification.

## Install & run

From source (core packer needs **no third-party packages**):

```bash
python -m package.image selftest       # RFC 8032 vectors + pack/verify checks
python -m package.image --help         # pack / verify CLI
python -m package.image pack --boot QBOOT_CHECK.bin --app app.bin --out combined.bin
```

GUI and flashing need:

```bash
pip install -e .[gui]        # PySide6 + pyocd
python gui.py                # or: python gui.py --check  (dependency check)
```

Standalone application: download the `quilla-image/` directory from
Releases and run `quilla-image.exe` inside it, no Python installation
required. This is a PyInstaller **onedir** build — keep the whole directory
together; the exe loads Qt / pyocd DLLs from `_internal/` beside it. Onedir
skips the per-launch extraction of a one-file archive, so startup is fast.
Settings are stored in `state.json` **next to the executable** (or at the
repository root when run from source), so a portable copy carries its paths
and options with it. Delete that file to reset. `SHA256SUMS.txt` in the
folder covers every shipped file.

## Boundaries

- The tool prepares images. It does not **provision keys** into devices and
  does not verify that a chosen key matches what is baked into a given QBOOT.
  With the default public demo key, signed images are not authenticated.
- CHECK mode is integrity-only. It is not secure boot and makes no
  authenticity claim.
- No IEC 61508 / ISO 26262 certification or SIL level is claimed.
- pyocd flashing is a convenience for low-volume / engineering use. High-volume
  production lines should use their own programmer and the Flash tab's
  "verify only" check.

## License

QuillaImage itself is Apache-2.0 (see [LICENSE](LICENSE)). The vendored
Ed25519 signer is identical to the one in [Quilla](https://github.com/JiChiRen/Quilla)
(`tools/quilla_ed25519_sign.py`); see [NOTICE](NOTICE).

The bundled executable ships unmodified **Qt 6 / PySide6 6.11.2** dynamic
libraries under LGPLv3. No Qt library is patched or relinked. The binary
distribution includes `LICENSES-Qt.txt` and the full `LGPL-3.0.txt` /
`GPL-3.0.txt` texts in `dist/quilla-image/licenses/`; corresponding Qt source is linked
in `LICENSES-Qt.txt`. See that file for the bundled DLL hashes and the
library-replacement route permitted by LGPLv3 §4(d)(1).
