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

界面接入只有三条路（本笔范围）：

  * 状态栏：``RemedyLinkLabel`` 追加在 ``QStatusBar`` 右侧，文案
    「查看怎么办 →」，点击按 code 定位到帮助面板第三页对应条目；
  * 有正文的弹窗：``attach_remedy_link(box, code)`` 在底部加同一行链接，
    不新增按钮、不改变默认焦点；
  * 非模态通知条：``notification_bar.NotificationBar`` 对有 remedy 的错误
    显示目录 message + ``suggestion()`` + 「查看怎么办」按钮；无 remedy
    的错误只进日志，绝不出现死链接。
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
        "workflow_executor_missing", "unknown_tool", "parallel_exec_error",
        # Sprint 5 并行执行核内部异常：只在 ToolResult.error 里，桌面无渲染点
    ),
    "工具执行超时：桌面 AI 面板直接展示目录 message 原文，状态栏/帮助面板不另设入口": (
        "tool_timeout",
    ),
    "设备接入/服务端协议细项：code 仅出现在后端状态、Web JSON 或外部调用返回值，桌面控件不渲染": (
        "bad_arguments", "bad_command", "bad_config", "bad_json",
        "config_not_found", "unknown_server", "unknown_command", "unsupported",
        "identity_mismatch", "station_untrusted", "no_pass",
        "no_downlink_known", "sdr_error", "invalid_argument",
        "export_path_out_of_root", "decoder_not_available", "not_available",
        "spill_write_failed", "denied_by_policy", "manual_gate_required",
        "internal_error",
        "forbidden", "bad_token", "dist_missing", "bad_payload",
        "unknown_method", "manual_gate_unavailable", "confirm_disabled",
        "confirm_rejected",
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
        "tx_source_failed", "already_exists",
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


#: 连接/枚举错误的唯一判定顺序（``_humanize_error`` 与
#: ``classify_connect_error`` 共用这一张表，顺序即优先级）。
#:
#: 顺序原则：**具体诊断先于泛化关键词**。permission 与 busy 是两种完全不同的
#: 用户动作——前者要改系统权限（udev / WinUSB），后者要关掉占用设备的程序；
#: 判错会让用户关掉别人的软件后仍然连不上。因此带 EACCES 线索的必须先判为
#: 权限。裸 ``errno`` 不能作为占用关键词（``errno 13`` 就是 EACCES），只保留
#: 明确的 ``errno 16`` / ``EBUSY``。
CONNECT_ERROR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("missing_dependency",
     ("no module named", "importerror", "no module")),
    ("network_unreachable",
     ("connection refused", "timeout", "timed out", "network",
      "unreachable", "连接失败", "refused")),
    ("usb_permission",
     ("permission", "access denied", "libusb_error_access", "eacces",
      "errno 13", "errno -13", "udev", "uid", "root", "权限")),
    ("device_busy",
     ("busy", "ebusy", "already in use", "could not open",
      "errno 16", "errno -16", "device is used", "resource", "占用")),
    ("device_not_found",
     ("0bda", "2838", "no device", "no devices", "找不到",
      "cannot find device")),
    ("driver_not_found",
     ("no such file", "not found", "soapy", "librtlsdr", "dll", "driver",
      "驱动", "未找到", "no backend")),
    ("usb_disconnected",
     ("disconnect", "lost", "usb", "stall", "epipe", "no such device", "断开")),
    ("sample_rate_not_supported",
     ("sample rate", "samplerate", "invalid rate", "不支持", "采样率")),
)

#: 码 → 人话文案。与 ``CONNECT_ERROR_RULES`` 同源，改一处必须改另一处；
#: ``tests/test_connect_error_ordering.py`` 断言文案与码永远来自同一分支。
CONNECT_ERROR_TEXT: Dict[str, str] = {
    "missing_dependency": (
        "未安装 SDR Python 库：本机 USB 默认装 SoapySDR(SoapyRTLSDR)；"
        "pyrtlsdr 为可选 GPL-3.0，按需自装"),
    "network_unreachable": (
        "无法连接远程 rtl_tcp 源：请检查 host:port 是否正确、"
        "网络是否连通、远端 rtl_tcp 服务是否在运行（默认端口 1234）。"),
    "usb_permission": (
        "USB 权限不足：Linux 执行 sudo cp rtl-sdr.rules "
        "/etc/udev/rules.d/ 并重插；Windows 用 zadig 安装 WinUSB 驱动。"),
    "device_busy": "设备被其他软件占用，请关闭 SDR#/GQRX/HDSDR 后重试。",
    "device_not_found": (
        "未检测到 RTL-SDR 硬件，请确认 USB 棒已插入并识别"
        "（设备管理器中应为 RTL2838UHIDIR）。"),
    "driver_not_found": (
        "未找到设备驱动，请安装 librtlsdr / SoapySDR 驱动"
        "（Linux: sudo apt install librtlsdr0 soapy-sdk）。"),
    "usb_disconnected": "设备已断开，请检查 USB 连接（可换个 USB 口）后点刷新。",
    "sample_rate_not_supported": "该采样率不被设备支持，已自动切换到最近合法档位。",
}

_EMPTY_ERROR_TEXT = "设备无响应（请检查 USB 棒、驱动与设备占用）"


def _connect_error_code(raw_err: str) -> Optional[str]:
    low = _low(raw_err)
    if not low:
        return None
    for code, keys in CONNECT_ERROR_RULES:
        if any(k in low for k in keys):
            return code
    return None


def humanize_connect_error(raw_err: str) -> str:
    """连接错误的原始文本 → 人话提示；未命中保留原文（绝不吞错误）。"""
    if not raw_err:
        return _EMPTY_ERROR_TEXT
    code = _connect_error_code(raw_err)
    if code is None:
        return raw_err
    return CONNECT_ERROR_TEXT[code]


def classify_connect_error(raw_err: str) -> Optional[str]:
    """连接/枚举失败的原始错误 → error_catalog 码；认不出返回 None。

    与 :func:`humanize_connect_error` 共用 ``CONNECT_ERROR_RULES``：同一输入
    在「文案」和「补救链接」两条路径上必然同分类。
    """
    return _connect_error_code(raw_err)


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
        """「查看怎么办 →」链接；没有对应条目时保持隐藏，绝不出现假链接。

        鼠标与键盘两条路都必须能打开：只允许鼠标时，键盘用户看得见这句
        「查看怎么办」却点不开——错误发生后唯一给出路的入口就废了。
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self._code = ""
            #: 当前链接目标：``remedy:<code>`` 或 ``diagnostics:open``。
            #: 键盘回车与鼠标点击共用这一个值，避免两条路各判一套状态。
            self._href = ""
            self._diagnostics_open = None
            self.setTextFormat(Qt.RichText)
            self.setOpenExternalLinks(False)
            # 鼠标可点 + 键盘可达：Tab 能停到这里，回车等同点击。
            self.setTextInteractionFlags(
                Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
            # setTextInteractionFlags 只在部分 Qt 版本上自动放宽焦点策略，
            # 这里显式钉住，保证链接一定在 Tab 链上。
            self.setFocusPolicy(Qt.StrongFocus)
            self.linkActivated.connect(self._open)
            self.clear_code()

        def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
            """回车/Return 激活唯一那条链接。

            QLabel 内部的 QTextControl 也能用键盘激活，但只在「当前链接已被
            选中」时生效；这里显式兜底，保证 Tab 到标签后按一次回车一定打开。
            自己处理后不再交给基类，避免同一次按键触发两遍。
            """
            if self._href and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                self.linkActivated.emit(self._href)
                return
            super().keyPressEvent(event)

        def code(self) -> str:
            return self._code

        def set_code(self, code: str) -> bool:
            remedy = remedy_for_code(code)
            if remedy is None:
                self.clear_code()
                return False
            self._code = code
            self._href = f"remedy:{code}"
            self._diagnostics_open = None
            self.setText(f'<a href="remedy:{code}">查看怎么办 →</a>')
            self.setToolTip(f"打开帮助「怎么办」并定位到：{entry_name(code)}")
            self.setVisible(True)
            return True

        def set_diagnostics(self, on_open) -> None:
            """把同一个链接机制换成「查看诊断 →」，复用键盘/鼠标两条路。

            诊断页不是错误目录条目，不能走 ``set_code``；但入口行为必须与
            「查看怎么办」完全一致（Tab 可达、回车即开），否则持久化错误
            唯一的出路又只剩鼠标。
            """
            self._code = ""
            self._href = "diagnostics:open"
            self._diagnostics_open = on_open
            self.setText('<a href="diagnostics:open">查看诊断 →</a>')
            self.setToolTip("打开只读诊断页：损坏备份、日志落点、最近一次持久化错误")
            self.setVisible(True)

        def clear_code(self) -> None:
            self._code = ""
            self._href = ""
            self._diagnostics_open = None
            self.setText("")
            self.setVisible(False)

        def _open(self, _href: str = "") -> None:
            if self._code:
                open_remedy(self.window(), self._code)
            elif self._diagnostics_open is not None:
                self._diagnostics_open()

    return RemedyLinkLabel


def build_link_label(parent=None):
    """构造一个「查看怎么办 →」链接标签（默认隐藏）。"""
    return _build_link_label_class()(parent)


def build_diagnostics_link(parent=None):
    """构造一个「查看诊断 →」链接标签（默认隐藏）。

    与 ``build_link_label`` 同一个类、同一套键盘可达性；由调用方
    ``set_diagnostics(callback)`` 决定何时出现、打开哪个页面。
    """
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
