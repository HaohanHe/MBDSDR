# SPDX-License-Identifier: MIT
"""
「怎么办」条目（帮助面板第三页的数据层）
==========================================

条目来源不是手写错误清单：现象与原因**逐字取自**
``mbdsdr_ai/error_catalog.py`` 的 ``ERRORS[code].message / .meaning``。
本模块只做两件事：

  * 挑出 15 条最高频/最高价值的错误码（``CURATED_CODES``）；
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
        "顶栏点「连接设备...」（Ctrl+C）选中你的 SDR；连上后「控制」面板的"
        "调谐 / 增益 / 静噪才会解除置灰。"),
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
        "在「控制」面板把「采样率」降到设备支持的档位（如 2.048 MHz）；"
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
        "在「设置」→「AI」把「请求超时（秒）」`ai_timeout` 调大后重试"
        "「更新卫星 TLE」；走代理时先把系统代理配好。"),
    "disk_full": (
        "清理磁盘后，在「设置」→「存储与日志」把「录音目录」`record_dir` 换到"
        "剩余空间更大的盘，再点「开始/停止录音」。"),
    "not_writable": (
        "在「设置」→「存储与日志」把「录音目录」`record_dir` 指到有写权限的"
        "位置（如 文档/MBDSDR），不要选 Program Files。"),
    "api_key_not_set": (
        "在「设置」→「AI」填入模型 API Key 后重开「AI 助手」页签；不看 AI "
        "时可以先忽略，频谱与音频不依赖它。"),
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
    """从真实错误目录生成 15 条「怎么办」（顺序即 CURATED_CODES）。"""
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
