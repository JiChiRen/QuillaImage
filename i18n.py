#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuillaImage — 界面双语文案（zh / en）。

范围：静态界面文本（标签 / 按钮 / 分组框标题）、弹窗、文件对话框、
进度对话框、窗口标题。运行日志（后台线程输出的诊断行）不在本表内，
保持中文——它们面向调试排查，改动会波及刚调稳的重试文案。

用法：
    from i18n import tr, set_lang, get_lang, LANGS, LANG_LABELS
    tr("btn_flash")                      # 当前语言文本
    tr("pd_match_progress", 3, 31, name) # 带位置的格式化文本

约定：任何 key 缺失或语言缺项时回退中文，中文也缺失时回退 key 本身
（保证界面永远有字可显示，不至于崩在文案层）。
"""

LANGS = ("zh", "en")
LANG_LABELS = {"zh": "中文", "en": "English"}

# key -> {"zh": 文案, "en": 文案}。带 %s / %d 占位符的按位置格式化。
_STRINGS = {
    # ---- 全局 ----
    "win_title": {
        "zh": "QuillaImage - QBOOT 镜像合并 / 烧录",
        "en": "QuillaImage - QBOOT image pack & flash",
    },
    "splash_msg": {
        "zh": "正在启动 QuillaImage ...",
        "en": "Starting QuillaImage ...",
    },
    "lbl_lang": {
        "zh": "Language / 语言:",
        "en": "Language / 语言:",
    },
    "log_lang_switched": {
        "zh": "界面语言已切换为 %s。",
        "en": "UI language switched to %s.",
    },

    # ---- 公共芯片选择区 ----
    "grp_chip": {"zh": "芯片 / CMSIS Pack", "en": "Chip / CMSIS Pack"},
    "lbl_pack": {"zh": "CMSIS Pack:", "en": "CMSIS Pack:"},
    "btn_import_pack": {"zh": "导入 Pack…", "en": "Import Pack…"},
    "lbl_mcu": {"zh": "MCU 型号:", "en": "MCU model:"},
    "btn_identity": {"zh": "从目标板读取", "en": "Read from target"},
    "lbl_chip_geo_empty": {
        "zh": "未选择芯片；可从 Pack 导入，或直接输入型号。",
        "en": "No chip selected; import a Pack or type a model.",
    },
    "geo_ready": {
        "zh": "%s | 已就绪，可打包 / 烧录",
        "en": "%s | Ready to pack / flash",
    },

    # ---- 页签 / 布局 ----
    "tab_flash": {"zh": "烧录", "en": "Flash"},
    "tab_pack": {"zh": "打包", "en": "Pack"},
    "grp_layout": {"zh": "镜像布局", "en": "Image layout"},
    "lbl_meta": {"zh": "META 起址 0x", "en": "META base 0x"},
    "lbl_app": {"zh": "APP 起址 0x", "en": "APP start 0x"},

    # ---- 打包页 ----
    "grp_fw": {"zh": "固件镜像", "en": "Firmware images"},
    "lbl_boot": {"zh": "QBOOT 镜像:", "en": "QBOOT image:"},
    "lbl_app_img": {"zh": "APP 镜像:", "en": "APP image:"},
    "lbl_key": {
        "zh": "Ed25519 私钥 (SEALED/SEC 必填):",
        "en": "Ed25519 private key (required for SEALED/SEC):",
    },
    "lbl_version": {
        "zh": "manifest 版本 (仅 CHECK):",
        "en": "manifest version (CHECK only):",
    },
    "cb_seal": {
        "zh": "SEALED 变体预置 seal（出厂首启免 ~20 s 验签）",
        "en": "Pre-set seal for SEALED variant (skips ~20 s verify at first boot)",
    },
    "btn_preview": {
        "zh": "解析并预览（load 后显示 CRC32）",
        "en": "Parse & preview (shows CRC32 after load)",
    },
    "btn_export": {
        "zh": "导出 boot + app + manifest 单 bin",
        "en": "Export boot + app + manifest as one bin",
    },
    "btn_meta_only": {
        "zh": "仅导出 256 B META 记录",
        "en": "Export 256 B META record only",
    },
    "btn_verify": {"zh": "校验已有整片 bin", "en": "Verify existing full bin"},

    # ---- 烧录页 ----
    "lbl_flash_image": {"zh": "整片 bin 文件:", "en": "Full-image bin:"},
    "btn_browse": {"zh": "浏览...", "en": "Browse..."},
    "lbl_probe": {"zh": "调试探针:", "en": "Debug probe:"},
    "btn_refresh": {"zh": "刷新探针", "en": "Refresh probes"},
    "btn_conn": {"zh": "连接探针", "en": "Connect probe"},
    "btn_disc": {"zh": "断开探针", "en": "Disconnect probe"},
    "btn_connecting": {"zh": "连接中 ...", "en": "Connecting ..."},
    "btn_disconnecting": {"zh": "断开中 ...", "en": "Disconnecting ..."},
    "lbl_rate_hz": {"zh": "探针速率:", "en": "Probe rate:"},
    "cb_auto_slow": {"zh": "失败时自动降速", "en": "Auto slow-down on failure"},
    "lbl_rate": {"zh": "速率:", "en": "Rate:"},
    "lbl_flash_base": {"zh": "烧录地址 0x", "en": "Flash base 0x"},
    "lbl_flash_base_form": {"zh": "烧录地址:", "en": "Flash address:"},
    "cb_manual_base": {"zh": "手动设置", "en": "Manual"},
    "cb_erase": {"zh": "烧录前整片擦除", "en": "Chip erase before flashing"},
    "btn_verify_img": {"zh": "校验所选整片 bin", "en": "Verify selected bin"},
    "btn_flash": {"zh": "烧录整片 bin", "en": "Flash full bin"},

    # ---- 日志区 ----
    "grp_log": {"zh": "日志", "en": "Log"},
    "btn_copy_log": {"zh": "复制日志", "en": "Copy log"},
    "btn_clear_log": {"zh": "清除日志", "en": "Clear log"},

    # ---- 探针下拉占位项 ----
    "probe_enum": {
        "zh": "正在枚举探针 ...",
        "en": "Enumerating probes ...",
    },
    "probe_pyocd_err": {
        "zh": "（pyocd 不可用：%s）",
        "en": "(pyocd unavailable: %s)",
    },
    "probe_empty": {
        "zh": "（列表为空；烧录时自动取第一个探针）",
        "en": "(empty; the first probe is used automatically when flashing)",
    },

    # ---- 弹窗（通用） ----
    "dlg_busy": {"zh": "忙", "en": "Busy"},
    "msg_busy": {
        "zh": "上一次操作还在进行中。",
        "en": "The previous operation is still running.",
    },
    "msg_busy_match": {
        "zh": "配对进行中，请稍候。",
        "en": "Matching is in progress; please wait.",
    },
    "dlg_unknown_err": {"zh": "未知错误", "en": "Unknown error"},

    # ---- Pack 导入 ----
    "dlg_pack_parse_fail": {"zh": "Pack 解析失败", "en": "Pack parse failed"},
    "dlg_no_chips": {"zh": "Pack 无可用芯片", "en": "No usable chips in Pack"},
    "msg_no_chips": {
        "zh": "这个 Pack 里没有带 Flash/RAM 容量定义的芯片。",
        "en": "This Pack has no chips with Flash/RAM size definitions.",
    },

    # ---- 探针连接 ----
    "dlg_no_probe": {"zh": "未选择探针", "en": "No probe selected"},
    "msg_no_probe": {
        "zh": "请先刷新并选择一个调试探针。",
        "en": "Refresh and select a debug probe first.",
    },
    "dlg_conn_fail": {"zh": "连接失败", "en": "Connection failed"},

    # ---- 目标型号校验 ----
    "dlg_target_missing": {"zh": "目标型号未填", "en": "Target model missing"},
    "msg_target_missing": {
        "zh": "请在公共区选择 MCU 型号或手输 pyocd 目标。",
        "en": "Select an MCU model or type a pyocd target.",
    },
    "dlg_pyocd_missing": {"zh": "pyocd 不可用", "en": "pyocd unavailable"},
    "msg_pyocd_missing": {
        "zh": "未检测到 pyocd（%s），无法校验目标型号。\n请先执行：pip install pyocd",
        "en": "pyocd not found (%s); cannot validate the target model.\n"
              "Run: pip install pyocd",
    },
    "dlg_pack_needed": {"zh": "需要 CMSIS Pack", "en": "CMSIS Pack required"},
    "msg_pack_needed": {
        "zh": "目标 %r 不是 pyocd 内建型号。\n请在公共区导入对应 CMSIS Pack，"
              "或手输一个内建型号\n（pyocd list --targets 可查看）。",
        "en": "Target %r is not a built-in pyocd model.\n"
              "Import the matching CMSIS Pack, or type a built-in model\n"
              "(see pyocd list --targets).",
    },

    # ---- MCU 型号配对 ----
    "dlg_no_pack": {"zh": "未导入 Pack", "en": "Pack not imported"},
    "msg_no_pack": {
        "zh": "请先导入 CMSIS Pack，再按型号队列配对。",
        "en": "Import a CMSIS Pack before matching models.",
    },
    "pd_match": {"zh": "正在配对 MCU 型号 ...", "en": "Matching MCU models ..."},
    "pd_cancel": {"zh": "取消", "en": "Cancel"},
    "pd_match_title": {"zh": "MCU 型号配对", "en": "MCU model matching"},
    "pd_match_progress": {
        "zh": "正在配对 %d/%d：%s",
        "en": "Matching %d/%d: %s",
    },
    "dlg_mcu_model": {"zh": "MCU 型号", "en": "MCU model"},
    "msg_no_match": {
        "zh": "未匹配到正确MCU型号",
        "en": "No matching MCU model found",
    },
    "msg_match_ok": {"zh": "配对成功：%s", "en": "Matched: %s"},
    "msg_compat": {"zh": "兼容烧录型号：%s", "en": "Flash-compatible models: %s"},
    "msg_not_adapted": {"zh": "未适配：%s", "en": "Not adapted: %s"},
    "raw_no_model": {"zh": "未读到型号", "en": "no model read"},
    "no_model_selected": {"zh": "(未选型号)", "en": "(no model selected)"},
    "msg_raw_id": {
        "zh": "（仅读到原始 ID，不据此猜型号）",
        "en": "(raw ID only; the model is not guessed)",
    },
    "dlg_read_fail": {"zh": "读取失败", "en": "Read failed"},
    "hint_target": {"zh": "\n\n目标型号：%s", "en": "\n\nTarget model: %s"},
    "hint_assert": {
        "zh": "\n\npyocd 在创建该型号的 target 时断言失败"
              "（pack 数据兼容缺陷，完整堆栈见日志）。请换用同系列其它容量型号。",
        "en": "\n\npyocd asserted while creating the target for this model "
              "(Pack compatibility defect; full traceback in the log). "
              "Use another capacity variant of the same series.",
    },
    "hint_offline": {
        "zh": "\n\n探针可能已掉线（assert 发生在连接中途，半初始化的会话"
              "未能干净释放）。请刷新探针列表；若列表为空需重新插拔 USB。",
        "en": "\n\nThe probe may have dropped off the bus (the assert "
              "happened mid-connect; the half-initialized session was not "
              "released cleanly). Refresh the probe list; if it is empty, "
              "re-plug the USB cable.",
    },

    # ---- 打包 / 导出 / 校验弹窗 ----
    "dlg_gen_fail": {"zh": "生成失败", "en": "Generation failed"},
    "dlg_parse_fail": {"zh": "解析失败", "en": "Parse failed"},
    "msg_read_image_fail": {
        "zh": "镜像文件读取失败：%s",
        "en": "Failed to read image file: %s",
    },
    "dlg_dep_missing": {"zh": "缺少依赖", "en": "Missing dependency"},
    "msg_dep_missing": {
        "zh": "未检测到 pyocd，请先执行：pip install pyocd\n\n%s",
        "en": "pyocd not found; run: pip install pyocd\n\n%s",
    },
    "dlg_flash_fail": {"zh": "烧录失败", "en": "Flash failed"},
    "msg_pick_flash_bin": {
        "zh": "请先选择要烧录的整片 bin",
        "en": "Select a full-image bin to flash first",
    },
    "dlg_verify_pre_fail": {
        "zh": "烧前校验失败",
        "en": "Pre-flash verify failed",
    },
    "msg_verify_pre_fail": {
        "zh": "%s\n\n请确认所选 bin 与布局起址一致，或在 ② 打包页重新导出。",
        "en": "%s\n\nMake sure the selected bin matches the layout bases, "
              "or re-export on the Pack page.",
    },
    "dlg_done": {"zh": "完成", "en": "Done"},
    "msg_export_ok": {"zh": "已导出：\n%s", "en": "Exported:\n%s"},
    "dlg_export_fail": {"zh": "导出失败", "en": "Export failed"},
    "dlg_verify_fail": {"zh": "校验失败", "en": "Verify failed"},
    "msg_pick_verify_bin": {
        "zh": "请先选择要校验的整片 bin",
        "en": "Select a full-image bin to verify first",
    },
    "msg_verify_ok": {"zh": "校验通过：\n%s", "en": "Verify passed:\n%s"},

    # ---- 文件对话框 ----
    "fd_pick_pack": {
        "zh": "选择 CMSIS Pack (*.pack)",
        "en": "Select CMSIS Pack (*.pack)",
    },
    "fd_pick_flash_bin": {
        "zh": "选择要烧录的整片 bin",
        "en": "Select the full-image bin to flash",
    },
    "fd_pick_boot": {
        "zh": "选择 QBOOT 镜像（.bin）",
        "en": "Select QBOOT image (.bin)",
    },
    "fd_pick_app": {
        "zh": "选择量产 APP .bin",
        "en": "Select production APP .bin",
    },
    "fd_pick_key": {
        "zh": "选择 Ed25519 私钥 private.hex（SEALED/SEC 必填）",
        "en": "Select Ed25519 private key private.hex "
              "(required for SEALED/SEC)",
    },
    "fd_export_bin": {"zh": "导出整片 bin", "en": "Export full-image bin"},
    "fd_export_meta": {"zh": "导出 META 记录", "en": "Export META record"},
    "fd_pick_verify": {
        "zh": "选择要校验的整片 bin",
        "en": "Select the full-image bin to verify",
    },
}

_LANG = "zh"


def set_lang(lang: str) -> None:
    """设置当前界面语言；未知值回退中文。"""
    global _LANG
    _LANG = lang if lang in LANGS else "zh"


def get_lang() -> str:
    return _LANG


def tr(key: str, *args) -> str:
    """按当前语言取文案；带参数时按位置格式化。

    三级回退：当前语言 -> 中文 -> key 本身。
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_LANG) or entry.get("zh") or key
    if args:
        text = text % args
    return text
