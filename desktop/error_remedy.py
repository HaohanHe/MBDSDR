# SPDX-License-Identifier: MIT
"""
错误码 → 帮助「怎么办」条目的映射与入口
==========================================

错误发生的那一刻就要能点到「怎么办」，而不是让用户先想起来去帮助面板搜。
本模块是唯一的映射事实源：

  * ``REMEDY_BY_CODE``：有帮助条目的错误码（15 条精选 + 桌面 UI 真会
    渲染 code 字面量或目录 message 原文的可见码，条目正文全部来自
    ``troubleshooting``，不另抄）；
  * ``NO_ENTRY_REASONS``：其余每个 ``error_catalog`` 错误码都显式登记
    「不出入口 + 原因」——**没有条目就不许出现链接**，宁可没有入口，
    也不让用户点开一个空页；
  * ``mapping_gaps()``：error_catalog 新增了码却没登记时返回缺口，
    测试据此判红。

界面接入只有两条路（本笔范围）：

  * 状态栏：``RemedyLinkLabel`` 追加在 ``QStatusBar`` 右侧，文案
    「查看怎么办 →」，点击按 code 定位到帮助面板第三页对应条目；
  * 有正文的弹窗：``attach_remedy_link(box, code)`` 在底部加同一行链接，
    不新增按钮、不改变默认焦点。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.error_catalog import ERRORS  # noqa: E402
from troubleshooting import CURATED_CODES, Remedy, build_remedies  # noqa: E402

#: 有帮助条目的错误码 → 条目（按 code 一对一）
REMEDY_BY_CODE: Dict[str, Remedy] = {r.code: r for r in build_remedies()}

#: 无帮助条目：显式按原因分组登记，每个 error_catalog code 必须落在这里或
#: 上面的 REMEDY_BY_CODE。新增 code 忘了登记 → mapping_gaps() 非空 → 测试红。
_NO_ENTRY_BY_REASON: Dict[str, tuple] = {
    "AI/MCP 工具层细项：code 不进入任何桌面 UI 控件（仅工具返回值/服务端响应），不设入口": (
        "workflow_executor_missing", "unknown_tool",
    ),
    "设备接入/服务端协议细项：code 仅出现在后端状态、Web JSON 或外部调用返回值，桌面控件不渲染": (
        "bad_arguments", "bad_command", "bad_config", "bad_json",
        "config_not_found", "unknown_server", "unknown_command", "unsupported",
        "identity_mismatch", "station_untrusted", "no_pass",
        "no_downlink_known", "sdr_error", "invalid_argument",
        "export_path_out_of_root", "decoder_not_available", "not_available",
        "spill_write_failed", "denied_by_policy", "manual_gate_required",
        "internal_error",
    ),
    "解码层细项：code 只出现在解码器/服务端返回值，桌面解码卡不渲染该码": (
        "missing_iq", "unknown_device_type", "empty_input",
    ),
    "录音库/回放细项：桌面录制走硬件 MCP、回放直接读 IQ 文件；该模块无桌面调用方，code 不进入 UI": (
        "file_missing", "bad_field", "bad_param", "entry_not_found",
        "dest_missing", "not_found", "library_not_found", "library_busy",
        "unsupported_kind", "io", "io_error", "wav_error", "missing",
        "corrupt", "truncated", "empty", "datatype", "deleted", "eof",
        "open_failed", "no_data", "rate_unknown", "rate_mismatch", "playback",
        "playback_failed", "bad_parameter", "tx_source_deleted",
        "tx_source_failed",
    ),
    "TLE 细项（限流/HTTP/解析）：过境面板与状态栏现在直接渲染 code + 原因 + 建议动作（`tle_fetch.failure_copy`）；帮助条目只覆盖精选码": (
        "rate_limited", "http_4xx", "http_5xx", "network_other",
        "parse_failure", "empty_response", "tle_unknown",
    ),
    "回放标注（非故障）：回放面板在频率读数旁直接渲染「录制真实值 / 上报值」双值标注，不设帮助入口": (
        "freq_reported_only",
    ),
    "遗留 stdio MCP 壳（core/mcp_server.py）细项：code 只经 MCP 协议返回调用方"
    "（桌面硬件工具走 mcp_worker/MBDSDRClient），桌面 UI 无渲染点": (
        "audio_start_failed", "audio_output_unavailable", "invalid_text",
        "scan_not_found", "decoder_not_found", "vfo_not_found",
    ),
    "内部错误：只用于日志与开发排查，不面向用户操作": ("unexpected_error",),
}

#: code → 不出入口的原因（扁平索引，供界面与测试查询）
NO_ENTRY_REASONS: Dict[str, str] = {
    code: reason
    for reason, codes in _NO_ENTRY_BY_REASON.items()
    for code in codes
}


def remedy_for_code(code: str) -> Optional[Remedy]:
    """按错误码取帮助条目；没有条目返回 None（界面据此不出链接）。"""
    return REMEDY_BY_CODE.get((code or "").strip())


def no_entry_reason(code: str) -> Optional[str]:
    """该 code 为什么不出「查看怎么办」入口（已登记才有值）。"""
    return NO_ENTRY_REASONS.get((code or "").strip())


def entry_name(code: str) -> str:
    """条目的展示名（分组 · 现象原文），用于 tooltip 与回报表。

    目录里有多个码共享同一句 message（如 ``llm_http_4xx`` 与 ``llm_http_5xx``
    都是「模型接口返回 HTTP {status}：{detail}」）。展示名必须能区分，否则
    帮助面板会把用户定位到错误的条目，所以同文案的码统一追加 ``（code）``。
    """
    r = remedy_for_code(code)
    if r is None:
        return ""
    base = f"{r.group} · {r.symptom}"
    same = [c for c, rr in REMEDY_BY_CODE.items()
            if f"{rr.group} · {rr.symptom}" == base]
    return base if len(same) <= 1 else f"{base}（{code}）"


def mapping_gaps(catalog: Optional[Dict] = None) -> List[str]:
    """error_catalog 里既没帮助条目、也没登记不出入口原因的 code。"""
    keys = set((catalog if catalog is not None else ERRORS).keys())
    return sorted(keys - set(REMEDY_BY_CODE) - set(NO_ENTRY_REASONS))


def mapping_overlaps() -> List[str]:
    """同时出现在「有条目」和「不出入口」两边的 code（应为空）。"""
    return sorted(set(REMEDY_BY_CODE) & set(NO_ENTRY_REASONS))


def _low(text: str) -> str:
    return (text or "").lower()


def classify_connect_error(raw_err: str) -> Optional[str]:
    """连接/枚举失败的原始错误 → error_catalog 码；认不出返回 None。

    顺序与 ``main_window._humanize_error`` 的判定顺序保持一致：
    socket 的 "errno" 文案必须先走网络分支，否则会被「设备被占用」误命中。
    """
    low = _low(raw_err)
    if not low:
        return None
    if any(k in low for k in ("no module named", "importerror", "no module")):
        return "missing_dependency"
    if any(k in low for k in ("connection refused", "timed out", "timeout",
                              "network", "unreachable", "连接失败", "refused")):
        return "network_unreachable"
    if any(k in low for k in ("busy", "already in use", "could not open",
                              "device is used", "resource", "errno", "占用")):
        return "device_busy"
    if any(k in low for k in ("permission", "access denied", "errno 13",
                              "uid", "root", "权限")):
        # error_catalog 没有「USB 权限不足」独立条目，不硬套别的码。
        return None
    if any(k in low for k in ("0bda", "2838", "no device", "no devices",
                              "找不到", "cannot find device")):
        return "device_not_found"
    if any(k in low for k in ("no such file", "not found", "soapy",
                              "librtlsdr", "dll", "driver", "驱动", "未找到",
                              "no backend")):
        return "driver_not_found"
    if any(k in low for k in ("disconnect", "lost", "stall", "epipe",
                              "no such device", "断开")):
        return "usb_disconnected"
    if any(k in low for k in ("sample rate", "samplerate", "invalid rate",
                              "不支持", "采样率")):
        return "sample_rate_not_supported"
    return None


def classify_record_dir_error(text: str) -> str:
    """录音目录错误说明 → disk_full / not_writable（两者都有帮助条目）。"""
    low = _low(text)
    if any(k in low for k in ("no space", "disk full", "磁盘", "空间不足", "空间")):
        return "disk_full"
    return "not_writable"


def _build_link_label_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    class RemedyLinkLabel(QLabel):
        """「查看怎么办 →」链接；没有对应条目时保持隐藏，绝不出现假链接。"""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._code = ""
            self.setTextFormat(Qt.RichText)
            self.setOpenExternalLinks(False)
            self.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
            self.linkActivated.connect(self._open)
            self.clear_code()

        def code(self) -> str:
            return self._code

        def set_code(self, code: str) -> bool:
            remedy = remedy_for_code(code)
            if remedy is None:
                self.clear_code()
                return False
            self._code = code
            self.setText(f'<a href="remedy:{code}">查看怎么办 →</a>')
            self.setToolTip(f"打开帮助「怎么办」并定位到：{entry_name(code)}")
            self.setVisible(True)
            return True

        def clear_code(self) -> None:
            self._code = ""
            self.setText("")
            self.setVisible(False)

        def _open(self, _href: str = "") -> None:
            if self._code:
                open_remedy(self.window(), self._code)

    return RemedyLinkLabel


def build_link_label(parent=None):
    """构造一个「查看怎么办 →」链接标签（默认隐藏）。"""
    return _build_link_label_class()(parent)


def open_remedy(parent, code: str) -> bool:
    """打开帮助面板并定位到该 code 的「怎么办」条目；无条目返回 False。"""
    if remedy_for_code(code) is None:
        return False
    from help_panel import HelpPanel
    dlg = HelpPanel(parent, initial_tab=2)
    if not dlg.show_remedy_code(code):
        dlg.deleteLater()
        return False
    from PySide6.QtCore import Qt
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return True


def attach_remedy_link(msgbox, code: str):
    """在弹窗底部追加「查看怎么办 →」一行；不新增按钮、不动默认焦点。

    没有对应条目时返回 None（调用方不显示任何入口）。
    """
    if remedy_for_code(code) is None:
        return None
    label = build_link_label(msgbox)
    if not label.set_code(code):
        label.deleteLater()
        return None
    layout = msgbox.layout()
    if layout is not None:
        layout.addWidget(label, layout.rowCount(), 0, 1, layout.columnCount())
    return label


def __getattr__(name: str):
    """PEP 562：只有真的要用 Qt 标签时才构造类（数据/测试可先不碰 Qt）。"""
    if name == "RemedyLinkLabel":
        return _build_link_label_class()
    raise AttributeError(name)
