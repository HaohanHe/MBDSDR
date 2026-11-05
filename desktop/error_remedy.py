# SPDX-License-Identifier: MIT
"""
错误码 → 帮助「怎么办」条目的映射与入口
==========================================

错误发生的那一刻就要能点到「怎么办」，而不是让用户先想起来去帮助面板搜。
本模块是唯一的映射事实源：

  * ``REMEDY_BY_CODE``：15 条有帮助条目的错误码（逐条来自
    ``troubleshooting.CURATED_CODES``，不另抄一份条目内容）；
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
    "AI/MCP 工具层错误：桌面状态栏与弹窗不直接展示，入口在 AI 助手的工具结果里": (
        "tool_not_found", "tool_unavailable", "invalid_arguments_type",
        "invalid_arguments", "missing_required_params", "param_type_mismatch",
        "param_out_of_range", "param_not_in_enum", "invalid_json_arguments",
        "handler_error", "exception", "empty_result_without_reason",
        "invalid_rs_shape", "empty_taps", "compose_failed",
        "gimbal_stop_failed", "no_rssi", "subagent_not_found",
        "recording_not_found", "workflow_executor_missing", "NEED_HW",
        "unknown_tool", "device_unavailable", "capability_unknown",
        "offline_or_api_error",
    ),
    "设备接入/参数细项：没有对应用户操作路径，帮助不设入口以免落到空页": (
        "ptt_not_on", "bad_arguments", "bad_command", "bad_config", "bad_json",
        "config_not_found", "unknown_server", "unknown_command", "unsupported",
        "identity_mismatch", "station_untrusted", "no_pass",
        "no_downlink_known", "sdr_error", "invalid_argument",
        "export_path_out_of_root", "decoder_not_available", "not_available",
        "spill_write_failed", "sdr_set_frequency_failed",
        "sdr_set_sample_rate_failed", "sdr_set_gain_failed",
        "sdr_set_bandwidth_failed", "doppler_compensation_failed",
        "denied_by_policy", "manual_gate_required", "internal_error",
    ),
    "解码层错误：结果显示在对应解码面板内，没有桌面级排查入口": (
        "missing_iq", "unknown_mode", "missing_sample", "unsupported_format",
        "decode_error", "not_implemented", "invalid_vcdu", "decode_failed",
        "unknown_device", "unknown_device_type", "empty_input", "bad_hex",
        "doppler_failed", "ogn_decode_failed", "schedule_failed", "meta_failed",
    ),
    "录音库/回放细项：在录音与回放面板内展示，没有独立帮助条目": (
        "file_missing", "bad_field", "bad_param", "entry_not_found",
        "dest_missing", "not_found", "library_not_found", "library_busy",
        "unsupported_kind", "io", "io_error", "wav_error", "missing",
        "corrupt", "truncated", "empty", "datatype", "deleted", "eof",
        "open_failed", "no_data", "rate_unknown", "rate_mismatch", "playback",
        "playback_failed", "bad_parameter", "tx_source_deleted",
        "tx_source_failed",
    ),
    "TLE 细项（限流/HTTP/解析）：在卫星面板内展示；超时与不可达已有帮助条目": (
        "rate_limited", "http_4xx", "http_5xx", "network_other",
        "parse_failure", "empty_response", "tle_unknown",
    ),
    "AI 面板内部错误：在对话里展示，没有独立帮助条目": (
        "agent_busy", "cancelled", "llm_offline", "llm_rate_limited",
        "llm_http_4xx", "llm_http_5xx", "llm_timeout", "llm_unreachable",
        "llm_unexpected",
    ),
    "内部错误：只用于日志与开发排查，不面向用户操作": (
        "unexpected_error", "missing_error_detail",
    ),
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
    """条目的展示名（分组 · 现象原文），用于 tooltip 与回报表。"""
    r = remedy_for_code(code)
    return "" if r is None else f"{r.group} · {r.symptom}"


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
