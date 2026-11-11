# SPDX-License-Identifier: MIT
"""
「怎么办」条目（帮助面板第三页的数据层）
==========================================

条目来源不是手写错误清单：现象与原因**逐字取自**
``mbdsdr_ai/error_catalog.py`` 的 ``ERRORS[code].message / .meaning``。
本模块只做三件事：

  * 挑出 15 条最高频/最高价值的错误码（``CURATED_CODES``）；
  * 把「桌面 UI 真会渲染 code 字面量或目录 message 原文」的错误码按族补成条目
    （``VISIBLE_BY_FAMILY``，现象/原因仍逐字取自目录）；
  * 给每条写「下一步」——必须点到具体控件名或设置项键名，禁止
    「请检查配置 / 请排查环境」这类空话（``validate_remedies`` 会红）。

搜索别名（``ALIASES``）只用用户口语补检索（「没声音」「静噪太紧」），
不改变条目的现象/原因原文。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.error_catalog import ERRORS  # noqa: E402

#: 15 条最高价值条目：连接/设备/音频/采样率/存储/网络/AI/弱信号
CURATED_CODES: Tuple[str, ...] = (
    "not_connected",
    "device_not_found",
    "device_busy",
    "usb_disconnected",
    "no_audio_output",
    "sample_rate_not_supported",
    "frequency_out_of_range",
    "driver_not_found",
    "missing_dependency",
    "network_unreachable",
    "network_timeout",
    "disk_full",
    "not_writable",
    "api_key_not_set",
    "empty_signal",
)

#: 错误码 → 界面分组（只用于树形分组，不是错误分类的第二份事实源）
GROUP_OF: Dict[str, str] = {
    "not_connected": "连接",
    "device_not_found": "连接",
    "device_busy": "连接",
    "usb_disconnected": "连接",
    "no_audio_output": "音频",
    "sample_rate_not_supported": "设置",
    "frequency_out_of_range": "设置",
    "driver_not_found": "连接",
    "missing_dependency": "安装",
    "network_unreachable": "网络",
    "network_timeout": "网络",
    "disk_full": "录制",
    "not_writable": "录制",
    "api_key_not_set": "AI",
    "empty_signal": "信号",
}

#: 错误码 → 下一步（具体到控件名 / 设置项键名）
NEXT_STEPS: Dict[str, str] = {
    "not_connected": (
        "顶栏点「连接」（Ctrl+C，等同「文件」→「连接设备...」）选中你的 SDR；"
        "连上后「控制」面板的调谐 / 增益 / 静噪才会解除置灰。"),
    "device_not_found": (
        "插好 USB 后重新点「连接设备...」枚举；RTL-SDR 需先用 Zadig 安装 "
        "WinUSB 驱动，远端设备在设备对话框选「远程 rtl_tcp 源」。"),
    "device_busy": (
        "先关掉占用设备的其它程序（SDR++ / SDR# / rtl_tcp 服务），再点"
        "「连接设备...」；仍失败就拔插 USB 后重新枚举。"),
    "usb_disconnected": (
        "重新插好 USB 线，等两秒后点「连接设备...」重新枚举；连接恢复后"
        "再点「开始/停止录音」。"),
    "no_audio_output": (
        "在「设备」页签的「音频输出 → 输出设备」选「系统默认」或扬声器；"
        "仍未出声就看「控制」面板的音量与静噪门限。"),
    "sample_rate_not_supported": (
        "在「控制」面板把「采样率」降到设备支持的档位（如 2.048 MS/s）；"
        "调谐步进可在「设置」→「频谱与解码」的「调谐步进」`tuning_step_hz` 调整。"),
    "frequency_out_of_range": (
        "把频率改回设备标称范围：在「控制」面板直接输入，或按「频率 −1 格」"
        "逐步调；书签里的超范围频率也要一并改掉。"),
    "driver_not_found": (
        "RTL-SDR 用 Zadig 安装 WinUSB 驱动，装完在「连接设备...」里重新枚举；"
        "驱动未装好前界面会如实显示无设备，不会伪造信号。"),
    "missing_dependency": (
        "把缺失的内置模块文件恢复到程序目录后重启；若只想先录音，在「设置」"
        "→「存储与日志」把「录音目录」`record_dir` 指到可写目录再试。"),
    "network_unreachable": (
        "检查网络或 DNS 后重试「更新卫星 TLE」；rtl_tcp 远端连不上时，在设备"
        "对话框选「远程 rtl_tcp 源」并填对方 IP 与端口。"),
    "network_timeout": (
        "先确认网络/DNS 与系统代理可用，再重试「更新卫星 TLE」；远端 rtl_tcp 超时"
        "就在设备对话框选「远程 rtl_tcp 源」，确认对方 IP 与端口可达后再连。"),
    "disk_full": (
        "清理磁盘后，在「设置」→「存储与日志」把「录音目录」`record_dir` 换到"
        "剩余空间更大的盘，再点「开始/停止录音」。"),
    "not_writable": (
        "在「设置」→「存储与日志」把「录音目录」`record_dir` 指到有写权限的"
        "位置（如 文档/MBDSDR），不要选 Program Files。"),
    "api_key_not_set": (
        "在「AI 助手」页签点「配置」填入模型 API Key（存到 ~/.mbdsdr/config.json，"
        "保存即生效）；不看 AI 时可以先忽略，频谱与音频不依赖它。"),
    "empty_signal": (
        "先在「频谱」页签确认有信号，再把「控制」面板的「静噪门限」往左调松；"
        "勾选「自动」时门限按默认值 `squelch_default_db` 生效。"),
}

#: 用户口语检索别名（只补搜索，不改现象/原因原文）
ALIASES: Dict[str, Tuple[str, ...]] = {
    "not_connected": ("连不上", "没连上", "灰色的"),
    "device_not_found": ("找不到设备", "没有设备", "识别不到"),
    "device_busy": ("设备被占用", "打不开设备"),
    "usb_disconnected": ("掉了", "掉线", "拔了"),
    "no_audio_output": ("没声音", "没有声音", "听不到声音"),
    "sample_rate_not_supported": ("采样率", "改采样率失败"),
    "frequency_out_of_range": ("调不过去", "频率超范围"),
    "driver_not_found": ("没驱动", "驱动没装"),
    "missing_dependency": ("缺模块", "模块缺失"),
    "network_unreachable": ("连不上服务器", "rtl_tcp 连不上", "网络不可达"),
    "network_timeout": ("超时", "TLE 更新失败"),
    "disk_full": ("磁盘满", "空间不足"),
    "not_writable": ("没有写权限", "权限不足", "写不进去"),
    "api_key_not_set": ("没配 Key", "模型不能用"),
    "empty_signal": ("静噪", "静噪太紧", "没声音", "信号太弱"),
}

#: 「桌面 UI 真会渲染 code 字面量或目录 message 原文」的目录错误码，按族共享
#: 一条具体下一步。
#:
#: 判据（逐条链路证据见回报）：AI 助手步骤卡在 ai_panel._append_step_card
#: 渲染 ``错误 {step.error}``，``step.error`` 就是 ToolResult.error 的 code
#: 原文；状态面板解码卡在 status_panel.format_decode_result 渲染
#: ``错误码 {code}: ...``。这两条显示 code 字面量。
#:
#: 2026-11-05 追加两条 message 通道（用户看不到 code，但看得到目录 message
#: 原文，同样算「用户可见」）：
#:   * 状态栏：SDRBackend 调谐/采样率/增益/带宽/多普勒失败时把
#:     ``status.error = user_message(code)``，main_window._on_health_tick
#:     经 _handle_backend_lost 用状态栏 ``设备已断开：{message}`` 显示原文；
#:   * AI 对话：model_manager 失败时返回 ``error = user_message(code)``，
#:     ai_panel._on_ai_response 渲染 ``[错误] {error}``；工具失败且正文为空时
#:     tool_registry 用 user_message("missing_error_detail") 兜底正文，步骤卡
#:     摘要显示该 message。
#: 其余 code 只出现在日志/库 API/服务端返回值/MCP 客户端，仍登记为「不出入口」。
VISIBLE_BY_FAMILY: Dict[str, Tuple[str, ...]] = {
    "工具参数": (
        "tool_not_found", "tool_unavailable", "invalid_arguments_type",
        "invalid_arguments", "missing_required_params", "param_type_mismatch",
        "param_out_of_range", "param_not_in_enum", "invalid_json_arguments",
        "invalid_rs_shape", "empty_taps",
    ),
    "工具执行": (
        "handler_error", "exception", "empty_result_without_reason",
        "compose_failed", "gimbal_stop_failed", "no_rssi",
        "offline_or_api_error", "recording_not_found", "subagent_not_found",
    ),
    "AI 会话": ("agent_busy", "cancelled"),
    "设备能力": (
        "device_unavailable", "ptt_not_on", "capability_unknown", "NEED_HW",
    ),
    "解码工具": (
        "invalid_vcdu", "decode_failed", "unknown_device", "doppler_failed",
        "ogn_decode_failed", "schedule_failed", "meta_failed", "bad_hex",
    ),
    # 状态面板解码卡显式映射了这 15 个 code（DECODE_ERROR_LABELS）。
    "解码结果": (
        "unknown_mode", "missing_sample", "unsupported_format",
        "decode_error", "not_implemented",
        "crc_failed", "insufficient_samples", "invalid_input", "invalid_wav",
        "no_frame_recovered", "nothing_detected", "preamble_not_found",
        "sample_rate_mismatch", "truncated_frame",
        "signal_present_no_frame",
    ),
    # 状态栏 message 通道：sdr_backend 设置 status.error = user_message(code)，
    # main_window._handle_backend_lost 显示「设备已断开：{message}」。
    "设备调谐": (
        "sdr_set_frequency_failed", "sdr_set_sample_rate_failed",
        "sdr_set_gain_failed", "sdr_set_bandwidth_failed",
        "sdr_set_agc_failed", "sdr_frequency_out_of_range",
        "doppler_compensation_failed",
    ),
    # AI 对话 message 通道：model_manager 返回 error=user_message(code)，
    # ai_panel 在对话区显示「[错误] {error}」。
    "AI 模型": (
        "llm_offline", "llm_rate_limited", "llm_http_4xx", "llm_http_5xx",
        "llm_timeout", "llm_unreachable", "llm_unexpected",
    ),
    # AI 步骤卡 message 通道：tool_registry 对「失败且正文为空」用
    # user_message("missing_error_detail") 补正文，步骤卡摘要显示该 message。
    "工具结果": ("missing_error_detail",),
    # 状态栏错误码通道：main_window 在「设备已断开 / 数据流中断」时同时显示
    # status.error_code（rtl_tcp/SpyServer 由 link_state 类别映射得到）。
    "接入链路": (
        "link_timeout", "link_refused", "link_half_open", "link_reset",
        "buffer_overflow",
    ),
}

#: 每个族一条「下一步」：必须点到具体控件名，禁止空话。
VISIBLE_NEXT_STEPS: Dict[str, str] = {
    "工具参数": (
        "在「AI 助手」页签把任务拆成单个工具调用，按「能力目录」里的参数名、"
        "类型与取值范围重发；不要原样重复同一组坏参数。"),
    "工具执行": (
        "在「AI 助手」页签改用「能力目录」里标记已接线的工具重试；涉及硬件的"
        "先在顶栏点「连接」确认设备在线。"),
    "AI 会话": (
        "在「AI 助手」页签等当前这一轮跑完后再发送同一条指令，"
        "避免在上一轮未结束时连续追问。"),
    "设备能力": (
        "先在顶栏点「连接」连上真实设备并核对采样率/增益读数；"
        "无硬件时改用「能力目录」里标注只读的工具，不要伪造发射或采样。"),
    "解码工具": (
        "在「AI 助手」页签改用「能力目录」里已接线的解码工具，并把输入换成"
        "完整样本；采样率与增益到「控制」页签核对后重试。"),
    "解码结果": (
        "在「状态」页签看「解码结果」卡片，换用可用模式或完整样本；采样率到"
        "「控制」页签核对后重试。"),
    "设备调谐": (
        "先在顶栏点「连接」确认设备在线；频率/采样率/增益超出"
        "设备能力就改回设备支持的值，仍失败则断开重连后再试一次。"),
    "AI 模型": (
        "在「AI 助手」页签点「配置」核对 API Key 与模型名；限流或 5xx 等几分钟"
        "再发同一条指令，4xx 先改模型名或重新填 Key 后重试。"),
    "工具结果": (
        "在「AI 助手」页签展开该步骤卡看返回码，按「能力目录」改用同一能力的"
        "具体工具重发；连续两次空结果就停止重试并记录步骤卡内容。"),
    "接入链路": (
        "先在顶栏点「连接」重连；rtl_tcp 确认启动命令 `rtl_tcp -a <本机IP> -p <端口>` "
        "仍在跑、SpyServer 确认端口号；防火墙放行本机 IP。数据流中断但连接未断时"
        "先等两倍心跳（10s）看是否自动恢复，仍无数据再重连。"),
}

#: 解码结果族里原因码各异，「下一步」逐码写具体动作（族级一条盖不住：
#: 采样率档位、样本长度、CRC 是三种完全不同的修法）。
CODE_NEXT_STEPS: Dict[str, str] = {
    "crc_failed": (
        "「控制」页签降低解调带宽并提高增益，改善天线后重录；"
        "APRS 类录满一整帧（实战建议 ≥5s），ADS-B 保持 1090MHz 与 fs=2 MS/s 或 4 MS/s。"),
    "insufficient_samples": (
        "点「工具」菜单的「开始/停止录音」（Ctrl+R）延长录制时长到至少一整帧再"
        "重试（代码按最短一帧校验：APRS 0.2s，FT4 75s 整帧）。"),
    "invalid_input": (
        "按步骤卡返回的错误信息修正参数，再到「控制」页签核对解调模式后重试，"
        "不要原样重复同一组参数。"),
    "invalid_wav": (
        "换用 16-bit PCM 单声道 WAV（或先修复容器）后重新解码，"
        "结果在「状态」页签的「解码结果」卡里看。"),
    "no_frame_recovered": (
        "补足样本并提高信噪比（「控制」页签加增益、换更好的天线）后重试。"),
    "nothing_detected": (
        "「控制」页签核对频点与增益，确认真有信号后再重试；"
        "没有信号时不要继续重试。"),
    "preamble_not_found": (
        "在「控制」页签核对频点与采样率（ADS-B 需 fs=2 MS/s 或 4 MS/s），"
        "确认信号存在后再重试。"),
    "signal_present_no_frame": (
        "有信号但解不出：在「控制」页签核对调制方式与频偏，再确认协议前导"
        "（ACARS 需 SYN,SYN,SOH）后重试；别把 0 帧当成解成功。"),
    "sample_rate_mismatch": (
        "在「控制」页签把采样率改成解码器要求的整数倍档位（如 ADS-B 用 "
        "2 MS/s 或 4 MS/s，即 1 MS/s 的整数倍）后重新录制，不要让它静默取整。"),
    "truncated_frame": (
        "点「工具」菜单的「开始/停止录音」延长采集时长，保证覆盖完整的一帧后再重试。"),
}

#: 上述各族的扁平索引（测试与映射完整性检查用）
VISIBLE_CODES: Tuple[str, ...] = tuple(
    code for codes in VISIBLE_BY_FAMILY.values() for code in codes)

#: 空话黑名单：出现即视为没给下一步
BANNED_PHRASES: Tuple[str, ...] = (
    "请检查配置", "请排查环境", "检查一下配置", "排查环境",
    "请自行排查", "重启试试", "多试几次",
)


@dataclass(frozen=True)
class Remedy:
    """一条「怎么办」：现象（界面原文）/ 可能原因 / 下一步。"""

    code: str
    group: str
    symptom: str
    cause: str
    next_step: str
    aliases: Tuple[str, ...] = ()


def _ui_keys_and_terms() -> Tuple[set, set]:
    """具体引用的合法词表：设置项键名 + 界面词汇（面板名/快捷键标签/设置标签）。"""
    from config_schema import ALL_ITEMS
    from panel_registry import registry
    from shortcuts import SHORTCUTS

    terms = {p.name for p in registry().list()}
    terms |= {s.label for s in SHORTCUTS}
    terms |= {i.label for i in ALL_ITEMS if i.label}
    return {i.key for i in ALL_ITEMS}, terms


def concrete_refs(text: str, keys: set, terms: set) -> List[str]:
    """抽出「」引用与反引号键名，返回其中真正落在界面词表里的那些。"""
    quoted = re.findall(r"「([^」]+)」", text or "")
    backticked = re.findall(r"`([^`]+)`", text or "")
    found = []
    for tok in quoted:
        if any(tok in t or t in tok for t in terms if t):
            found.append(f"「{tok}」")
    for tok in backticked:
        if tok in keys:
            found.append(f"`{tok}`")
    return found


def validate_remedies(remedies: Sequence[Remedy],
                      keys: set | None = None,
                      terms: set | None = None) -> List[str]:
    """逐条校验三要素；返回问题列表（空 = 全过）。

    红线：``next_step`` 至少 8 字、不得含空话黑名单、必须引用具体控件名或
    设置项键名——三者缺一就返回错误，由测试变红。
    """
    if keys is None or terms is None:
        keys, terms = _ui_keys_and_terms()
    problems: List[str] = []
    for r in remedies:
        step = (r.next_step or "").strip()
        if len(step) < 8:
            problems.append(f"{r.code}: 下一步不足 8 字：{step!r}")
            continue
        banned = [p for p in BANNED_PHRASES if p in step]
        if banned:
            problems.append(f"{r.code}: 下一步是空话 {banned}：{step}")
            continue
        if not concrete_refs(step, keys, terms):
            problems.append(
                f"{r.code}: 下一步没有具体控件名/设置项键名：{step}")
        if not (r.symptom or "").strip():
            problems.append(f"{r.code}: 缺现象原文")
        if not (r.cause or "").strip():
            problems.append(f"{r.code}: 缺可能原因")
    return problems


def build_remedies() -> List[Remedy]:
    """从真实错误目录生成条目：15 条精选 + UI 可见码按族补齐。

    现象/原因一律逐字取 ``ERRORS[code].message / .meaning``；下一步按族
    复用一条具体指引。精选码若同时出现在可见族，只保留精选条目。
    """
    out: List[Remedy] = []
    for code in CURATED_CODES:
        spec = ERRORS[code]
        out.append(Remedy(
            code=code,
            group=GROUP_OF.get(code, "其他"),
            symptom=spec.message,
            cause=spec.meaning,
            next_step=NEXT_STEPS[code],
            aliases=ALIASES.get(code, ()),
        ))
    for group, codes in VISIBLE_BY_FAMILY.items():
        for code in codes:
            if code in CURATED_CODES:
                continue
            spec = ERRORS[code]
            out.append(Remedy(
                code=code,
                group=group,
                symptom=spec.message,
                cause=spec.meaning,
                next_step=(CODE_NEXT_STEPS.get(code)
                           or VISIBLE_NEXT_STEPS[group]),
            ))
    return out


def search_remedies(remedies: Sequence[Remedy], query: str) -> List[Remedy]:
    """按现象/原因/下一步/口语别名过滤（空关键词返回全部）。"""
    q = (query or "").strip().lower()
    if not q:
        return list(remedies)
    out = []
    for r in remedies:
        hay = " ".join((r.code, r.group, r.symptom, r.cause, r.next_step,
                        " ".join(r.aliases))).lower()
        if q in hay:
            out.append(r)
    return out
