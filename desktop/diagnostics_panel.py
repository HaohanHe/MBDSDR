# SPDX-License-Identifier: MIT
"""只读诊断页（路线图 W106）。

三页，一页看完：损坏备份（含理由全文）/ 日志 / 持久化错误。数据全部来自
``mbdsdr_ai.diagnostics.collect_diagnostics()``，本模块只做渲染。

三条硬约束：

1. **纯展示**：模块内没有任何写盘 / 删除调用（连清理按钮的挂点都不留）。
   ``tests/test_diagnostics_panel_pure_display.py`` 直接 AST 扫本文件断言零命中，
   以后想加也会被拦红。
2. **长文本不截断**：理由可能是整段异常堆栈，进只读 ``QTextEdit`` 自带滚动；
   页面本身套 ``QScrollArea``。绝不按字符数切——切掉的可能正是异常类型。
3. **查不到必须显式**：``unwired`` 实例、未写入的日志、不可读的目录，
   各占一行写清「查不到什么、为什么」，不留空白让人误以为「没有错误」。

入口挂点：``DiagnosticsPanel(parent, open_help=...)``。帮助菜单的接线由
卫星线在 ``main_window.py`` 里做，本模块不 import 主窗口。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.diagnostics import collect_diagnostics  # noqa: E402


def _backup_rows(block: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item in block.get("unreadable", ()):
        rows.append({
            "kind": "missing",
            "title": f"查不到：未检查 {item.get('dir', '')}",
            "text": f"原因：{item.get('error', '未知')}",
        })
    if not block.get("items"):
        rows.append({
            "kind": "missing",
            "title": "没有找到带理由文件的损坏备份",
            "text": ("只认与备份成对的 *.reason.txt。裸 *.corrupt-* 说不出原因，"
                     "站址 .bak 是冲突快照不是损坏——两者都不计入，"
                     "免得同一次故障在页面上出现两次。"),
        })
    for item in block.get("items", ()):
        rows.append({
            "kind": "error",
            "title": item.get("backup", ""),
            "text": item.get("reason", ""),
            # 面板据此提供「复制路径」；没有这个字段的行不出按钮。
            "path": item.get("backup", ""),
        })
    if block.get("items"):
        # 只在真有备份时提示：空清单下这句会和「没有找到损坏备份」互相打架。
        rows.append({
            "kind": "note",
            "title": "这些备份可以手动删除，本页不会替你删",
            "text": ("备份与理由文件都留在原文件旁边（*.corrupt-时间戳 与 "
                     "*.reason.txt）。本页只读：不删除、不打开文件管理器、"
                     "不启动外部程序；确认无用后你可以复制路径自行清理。"),
        })
    if block.get("stale"):
        rows.append({
            "kind": "note",
            "title": f"另有 {block['stale']} 个超过 30 天的损坏备份",
            "text": ("本页只清点、不提供删除；备份名为 *.corrupt-时间戳，"
                     "确认无用后可手动清理。"),
        })
    return rows


def _log_rows(block: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if block.get("path"):
        rows.append({"kind": "ok", "title": "本次启动日志", "text": str(block["path"])})
    else:
        rows.append({
            "kind": "missing",
            "title": "查不到本次启动日志的落点",
            "text": block.get("notice") or "本次进程尚未写入启动日志（不代表没有落点）",
        })
    buffered = block.get("buffered") or []
    if buffered:
        rows.append({
            "kind": "note",
            "title": f"内存缓冲（仅本进程有效，显示末 {len(buffered)} 行）",
            "text": "\n".join(str(x) for x in buffered),
        })
    candidates = block.get("candidates") or []
    if candidates:
        rows.append({
            "kind": "note",
            "title": "候选落点（按尝试顺序）",
            "text": "\n".join(str(x) for x in candidates),
        })
    return rows


def _error_rows(block: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not block.get("items"):
        rows.append({"kind": "ok", "title": "没有检测到持久化错误", "text": ""})
    for item in block.get("items", ()):
        rows.append({
            "kind": "error",
            "title": f"{item.get('name', '')}（{item.get('source', '')}）",
            "text": item.get("error", ""),
        })
    for item in block.get("unwired", ()):
        rows.append({
            "kind": "missing",
            "title": f"查不到：{item.get('name', '')} 的最后一次错误",
            "text": (f"原因：{item.get('source', '')} 需要活动窗口实例，"
                     "本页暂未接线——这不等于它没有错误。"),
        })
    return rows


def build_pages(data: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """把 ``collect_diagnostics()`` 的结果转成三页的展示行（纯数据，无 Qt）。

    每行 ``{"kind", "title", "text"}``，备份行另带 ``path`` 供「复制路径」：
    ``kind`` 只用于排序/强调，文案里的「查不到」必须由行本身说清，
    不能依赖颜色。
    """
    return {
        "backups": _backup_rows(data.get("backups") or {}),
        "log": _log_rows(data.get("log") or {}),
        "errors": _error_rows(data.get("errors") or {}),
    }


def _build_dialog_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
        QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )

    class DiagnosticsPanel(QDialog):
        """只读诊断页（非模态）。本类不含写盘 / 删除 / 启动外部程序的路径。"""

        def __init__(self, parent=None,
                     open_help: Optional[Callable[[], None]] = None):
            super().__init__(parent)
            self.setWindowTitle("诊断（只读）")
            self.resize(900, 640)
            self.setModal(False)
            self._pages = build_pages(collect_diagnostics())

            root = QVBoxLayout(self)
            intro = QLabel("本页只读：不删除备份、不写配置、不创建目录、"
                           "不启动外部程序；需要处理文件时用「复制路径」"
                           "自行前往。查不到的内容会逐行写明原因。", self)
            intro.setWordWrap(True)
            root.addWidget(intro)

            self.tabs = QTabWidget(self)
            root.addWidget(self.tabs, stretch=1)
            for key, title in (("backups", "损坏备份"),
                               ("log", "日志"),
                               ("errors", "持久化错误")):
                self.tabs.addTab(self._build_page(self._pages[key]), title)

            bar = QHBoxLayout()
            bar.addStretch(1)
            if open_help is not None:
                help_btn = QPushButton("打开帮助面板", self)
                help_btn.clicked.connect(open_help)
                bar.addWidget(help_btn)
            close_btn = QPushButton("关闭", self)
            close_btn.clicked.connect(self.close)
            bar.addWidget(close_btn)
            root.addLayout(bar)

        def _build_page(self, rows: List[Dict[str, str]]) -> QScrollArea:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            body = QWidget(scroll)
            lay = QVBoxLayout(body)
            for row in rows:
                title = QLabel(row["title"], body)
                title.setWordWrap(True)
                title.setTextInteractionFlags(Qt.TextSelectableByMouse)
                lay.addWidget(title)
                path = row.get("path")
                if path:
                    # 只复制路径，不打开文件管理器：用户自己去文件管理器粘贴。
                    # 打开目录会启动外部程序，越过本页只读边界。
                    copy_btn = QPushButton("复制路径", body)
                    copy_btn.setToolTip(
                        "复制备份文件路径到剪贴板（不打开文件管理器）")
                    copy_btn.clicked.connect(
                        lambda _checked=False, p=path:
                        QApplication.clipboard().setText(p))
                    lay.addWidget(copy_btn)
                if row["text"]:
                    # 长理由（异常堆栈）整段放进去，靠控件自身滚动；
                    # 不截断、不省略——切掉的可能正是异常类型。
                    box = QTextEdit(body)
                    box.setReadOnly(True)
                    box.setPlainText(row["text"])
                    box.setLineWrapMode(QTextEdit.WidgetWidth)
                    box.setMinimumHeight(48)
                    box.setMaximumHeight(180)
                    lay.addWidget(box)
            lay.addStretch(1)
            scroll.setWidget(body)
            return scroll

    return DiagnosticsPanel


def __getattr__(name: str):
    """PEP 562：``from diagnostics_panel import DiagnosticsPanel`` 时才构造 Qt 类，
    让 ``build_pages`` 的测试不必先起 QApplication。"""
    if name == "DiagnosticsPanel":
        return _build_dialog_class()
    raise AttributeError(name)
