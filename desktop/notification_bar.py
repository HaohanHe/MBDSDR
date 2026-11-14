# SPDX-License-Identifier: MIT
"""
非模态通知条（QGIS ``QgsMessageBar`` 的等价物）
==============================================

采集/扫频/录音进行中弹 ``QMessageBox`` 会打断接收，而这些错误往往并不需要
用户先做决定。本模块提供宿主顶部一条非模态通知带：

  * **有 remedy 的错误**（``error_remedy.remedy_for_code`` 有条目）→ 显示
    目录原文 + ``error_catalog.suggestion()`` 的可执行建议 + 「查看怎么办」
    按钮；可重试的码且调用方给了回调时再给「重试」按钮；
  * **无 remedy 的错误** → 只写日志（``logger.info``），不占界面、不出现
    死链接——与 ``error_remedy`` 的既有红线一致；
  * **同一个码重复发生** → 合并成一条并计数（``×N``），不刷屏；
  * **批量操作** → ``begin_batch()`` / ``end_batch()`` 期间只累计，
    结束时汇总成一条（「批量导入：N 条错误（M 类）」）。

本模块只做呈现与聚合，不产生新的错误文案：文字全部来自
``mbdsdr_ai.error_catalog``，入口判断全部来自 ``error_remedy``。
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout,
)

from mbdsdr_ai import error_catalog
from error_remedy import open_remedy, remedy_for_code

log = logging.getLogger(__name__)

#: 通知级别 → 左侧短标签（配色取自 tokens 的状态色，不写死 hex）。
LEVEL_LABELS = {
    "error": "错误",
    "warning": "警告",
    "info": "提示",
    "success": "完成",
}


class NotificationBar(QFrame):
    """顶部非模态通知条：错误汇总 + 可执行建议 + 可选动作。

    默认隐藏；只有 ``notify()`` 命中「有 remedy」或 ``show_message()`` 明确
    要求显示时才会出现。显式调用 ``dismiss()`` 关闭；带 ``timeout_ms`` 的
    提示自动关闭，错误默认常驻（用户需要时间读完建议并决定下一步）。
    """

    #: 点击「查看怎么办」时先发信号（便于宿主/测试旁路观察），再打开帮助页。
    remedy_requested = Signal(str)
    #: 点击「重试」时发信号并调用调用方给的回调。
    retry_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("notificationBar")
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        # 聚合状态：order 保留首次出现的顺序，counts 是每个码的累计次数。
        self._order: List[str] = []
        self._counts: Dict[str, int] = {}
        self._code = ""
        self._level = "error"
        self._retry: Optional[Callable[[], None]] = None
        self._active = False
        # 批量窗口：非 None 时 notify() 只累计，不立即显示。
        self._batch_label: Optional[str] = None
        self._batch: Dict[str, int] = {}
        self._batch_logged: int = 0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)

        self.level_label = QLabel("")
        self.level_label.setObjectName("notificationLevel")
        self.level_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        row.addWidget(self.level_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.message_label = QLabel("")
        self.message_label.setObjectName("notificationMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.suggestion_label = QLabel("")
        self.suggestion_label.setObjectName("notificationSuggestion")
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_col.addWidget(self.message_label)
        text_col.addWidget(self.suggestion_label)
        row.addLayout(text_col, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("notificationCount")
        self.count_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        row.addWidget(self.count_label, 0, Qt.AlignTop)

        self.remedy_btn = QPushButton("查看怎么办")
        self.remedy_btn.setObjectName("notificationAction")
        self.remedy_btn.setCursor(Qt.PointingHandCursor)
        self.remedy_btn.clicked.connect(self._on_remedy_clicked)
        row.addWidget(self.remedy_btn, 0, Qt.AlignTop)

        self.retry_btn = QPushButton("重试")
        self.retry_btn.setObjectName("notificationRetry")
        self.retry_btn.setCursor(Qt.PointingHandCursor)
        self.retry_btn.clicked.connect(self._on_retry_clicked)
        row.addWidget(self.retry_btn, 0, Qt.AlignTop)

        self.close_btn = QToolButton()
        self.close_btn.setObjectName("notificationClose")
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("关闭这条通知；同一问题再次触发时会重新出现")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.dismiss)
        row.addWidget(self.close_btn, 0, Qt.AlignTop)

        self.setVisible(False)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def notify(self, code: str, *, fields: Optional[dict] = None,
               level: Optional[str] = None, retry: Optional[Callable] = None,
               message: Optional[str] = None,
               suggestion: Optional[str] = None,
               timeout_ms: Optional[int] = None) -> bool:
        """按错误码呈报一次。返回是否真的显示（无 remedy 的只进日志）。

        文案优先级：调用方给的 ``message`` / ``suggestion`` > 目录原文。
        但**是否显示**只看 ``error_remedy`` 里有没有该码的条目——没有条目的
        错误不许出现在通知条上，避免点开一个空页。
        """
        code = (code or "").strip()
        if not code:
            return False
        fields = dict(fields or {})

        if remedy_for_code(code) is None:
            text = message or error_catalog.user_message(code, **fields)
            log.info("通知条不出入口（无 remedy）：%s | %s", code, text)
            if self._batch_label is not None:
                self._batch_logged += 1
            return False

        if self._batch_label is not None:
            self._batch[code] = self._batch.get(code, 0) + 1
            return True

        msg = message or error_catalog.user_message(code, **fields)
        sug = suggestion if suggestion is not None else \
            error_catalog.suggestion(code, **fields)
        lvl = level or self._default_level(code)
        self._counts[code] = self._counts.get(code, 0) + 1
        if code not in self._order:
            self._order.append(code)
        self._show(code, msg, sug, lvl, retry, timeout_ms)
        return True

    def show_message(self, text: str, *, suggestion: str = "",
                     level: str = "info",
                     timeout_ms: Optional[int] = None) -> None:
        """非错误通知（进度/回执）：不经 remedy 判断，按调用方要求显示。"""
        self._counts.clear()
        self._order.clear()
        self._show("", str(text or ""), str(suggestion or ""),
                   level, None, timeout_ms)

    def begin_batch(self, label: str) -> None:
        """进入批量窗口：期间的 ``notify()`` 只累计，结束时汇总成一条。"""
        self._batch_label = str(label or "批量操作")
        self._batch = {}
        self._batch_logged = 0

    def end_batch(self) -> int:
        """结束批量窗口并汇总；返回累计条数（0 表示无错误，不显示）。"""
        if self._batch_label is None:
            return 0
        label = self._batch_label
        batch = self._batch
        self._batch_label = None
        self._batch = {}
        total = sum(batch.values())
        if total <= 0:
            return 0
        # 汇总成一条：取第一类错误作为「查看怎么办」的目标，正文说明总账。
        first = next(iter(batch))
        self._counts[first] = self._counts.get(first, 0) + total
        if first not in self._order:
            self._order.append(first)
        msg = f"{label}：{total} 条错误（{len(batch)} 类）"
        self._show(first, msg, error_catalog.suggestion(first), "error",
                   None, None)
        return total

    def dismiss(self) -> None:
        """关闭通知并清空聚合状态（下次同码重新计数）。"""
        self._timer.stop()
        self._active = False
        self._code = ""
        self._retry = None
        self._order.clear()
        self._counts.clear()
        self.message_label.setText("")
        self.suggestion_label.setText("")
        self.count_label.setText("")
        self.setVisible(False)

    #: ``dismiss`` 的别名（与 QGIS messageBar 的 clearWidgets 语义对齐）。
    clear = dismiss

    def is_active(self) -> bool:
        """当前是否正在显示（测试/宿主查询用；不依赖窗口是否已 show）。"""
        return self._active

    def active_codes(self) -> List[str]:
        """当前聚合中的错误码（按首次出现顺序）。"""
        return list(self._order)

    def count(self, code: str) -> int:
        """某个码在当前通知条里的累计次数。"""
        return int(self._counts.get((code or "").strip(), 0))

    def current_code(self) -> str:
        return self._code

    def level(self) -> str:
        return self._level

    def message_text(self) -> str:
        return self.message_label.text()

    def suggestion_text(self) -> str:
        return self.suggestion_label.text()

    def has_remedy_action(self) -> bool:
        return not self.remedy_btn.isHidden()

    def has_retry_action(self) -> bool:
        return not self.retry_btn.isHidden()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _default_level(code: str) -> str:
        """码所属层 → 通知级别（接入/工具错误用错误红，其余按警告）。"""
        try:
            layer = error_catalog.spec(code).layer
        except Exception:  # noqa: BLE001 - 目录异常不得阻断错误呈现
            return "error"
        if layer in (error_catalog.LAYER_ACCESS, error_catalog.LAYER_TOOL,
                     error_catalog.LAYER_RECORDER, error_catalog.LAYER_DECODE):
            return "error"
        return "warning"

    def _show(self, code: str, message: str, suggestion: str, level: str,
              retry: Optional[Callable], timeout_ms: Optional[int]) -> None:
        self._timer.stop()
        self._code = code
        self._level = level if level in LEVEL_LABELS else "info"
        self._retry = retry if callable(retry) else None

        self.level_label.setText(LEVEL_LABELS[self._level])
        self.message_label.setText(message)
        self.suggestion_label.setText(suggestion)
        self.suggestion_label.setVisible(bool(suggestion))

        # 动作区：有码才有「查看怎么办」（notify 已保证有 remedy）；
        # 「重试」只在该码可重试且调用方确实给了回调时出现。
        self.remedy_btn.setVisible(bool(code))
        retry_ok = (bool(code) and self._retry is not None
                    and error_catalog.retryable(code))
        self.retry_btn.setVisible(retry_ok)

        self._refresh_count()
        self._apply_style()
        self._active = True
        self.setVisible(True)
        if timeout_ms is not None and int(timeout_ms) > 0:
            self._timer.start(int(timeout_ms))

    def _refresh_count(self) -> None:
        n = self._counts.get(self._code, 0)
        if self._code and n > 1:
            kinds = len(self._order)
            self.count_label.setText(f"×{n}" if kinds <= 1 else f"×{n}（共{kinds}类）")
            self.count_label.setVisible(True)
        else:
            self.count_label.setText("")
            self.count_label.setVisible(False)

    def _accent(self) -> str:
        """当前级别对应的状态色（tokens 单一来源，主题切换后重新取）。"""
        try:
            from tokens import tokens
            t = tokens()
            kind = {"error": "danger", "warning": "warning",
                    "success": "success", "info": "busy"}.get(self._level, "danger")
            return t.status_color(kind)
        except Exception:  # noqa: BLE001 - 取不到令牌就用中性色，不崩
            return "#B85C5C"

    def _apply_style(self) -> None:
        try:
            from tokens import tokens
            c = tokens().COLORS
        except Exception:  # noqa: BLE001
            c = {}
        bg = c.get("bg_alt", c.get("card", "#2A2A2A"))
        fg = c.get("text", "#FFFFFF")
        dim = c.get("text_secondary", fg)
        accent = self._accent()
        self.setStyleSheet(
            "QFrame#notificationBar {"
            f" background-color:{bg}; color:{fg};"
            f" border:1px solid {accent}; border-left:4px solid {accent};"
            " border-radius:6px; }"
            "QLabel#notificationLevel {"
            f" color:{accent}; font-weight:600; font-size:9pt;"
            " padding:0 2px; }"
            "QLabel#notificationMessage {"
            f" color:{fg}; font-size:9pt; }}"
            "QLabel#notificationSuggestion {"
            f" color:{dim}; font-size:9pt; }}"
            "QLabel#notificationCount {"
            f" color:{dim}; font-size:9pt; }}"
            "QPushButton#notificationAction, QPushButton#notificationRetry {"
            f" color:{accent}; background:transparent; border:1px solid {accent};"
            " border-radius:4px; padding:1px 8px; font-size:9pt; }"
            "QPushButton#notificationAction:hover,"
            " QPushButton#notificationRetry:hover {"
            f" background:{accent}; color:{bg}; }}"
            "QToolButton#notificationClose {"
            f" color:{dim}; background:transparent; border:none;"
            " font-size:10pt; padding:0 4px; }"
            "QToolButton#notificationClose:hover {"
            f" color:{fg}; }}")

    def _on_remedy_clicked(self) -> None:
        code = self._code
        if not code:
            return
        self.remedy_requested.emit(code)
        open_remedy(self.window(), code)

    def _on_retry_clicked(self) -> None:
        code = self._code
        if not code:
            return
        self.retry_requested.emit(code)
        cb = self._retry
        self.dismiss()
        if cb is not None:
            cb()
