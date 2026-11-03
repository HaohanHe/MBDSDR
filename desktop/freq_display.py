# SPDX-License-Identifier: MIT
"""
desktop/freq_display.py
=======================
大字体频率显示 + 按位编辑（SDR++/gqrx "radio dial" 手感）。

Python 侧移植自 ``cpp/src/ui/freq_display_widget.h``（同一交互契约，勿分叉）：
9 位数字网格 "DDD.DDD.DDD"（左起第 0 位 = 100 MHz … 第 8 位 = 1 Hz）——

  * 左键点数字上半：该位 +1；下半：−1
  * 滚轮悬停某位：该位 ±1
  * 右键：该位及以右全部清零

前导零暗显（SDR++ frequency_select.cpp:117-123 惯例），显著位数一眼可读。
纯 UI + 算术：只发 ``frequency_edited(Hz)``，由属主决定如何调谐（面板侧再钳位
并走既有 tune 信号，widget 不直接碰引擎）。
"""
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetricsF, QPen

from tokens import tokens


def digits_of(hz: int) -> str:
    """Hz -> 9 位数字串（超界高位被截断，调用方负责钳位）。"""
    return f"{max(0, int(hz)):0{FreqDisplay.DIGIT_COUNT}d}"[-FreqDisplay.DIGIT_COUNT:]


class FreqDisplay(QWidget):
    DIGIT_COUNT = 9  # "DDD.DDD.DDD"

    frequency_edited = Signal(int)   # Hz（只发变化后的请求值，不直接调谐）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hz: Optional[int] = None
        self._min_hz = 0
        self._max_hz = 2_000_000_000
        self._hover_digit = -1         # 悬停的数字位（0..8，-1=无）
        self.setMouseTracking(True)
        self.setMinimumHeight(60)
        self.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------ API
    def set_frequency(self, hz: Optional[float]):
        """推送当前频率真值；None=无真值（显占位，不编造数字）。"""
        if hz is None:
            self._hz = None
        else:
            try:
                v = int(round(float(hz)))
            except (TypeError, ValueError, OverflowError):
                self._hz = None
            else:
                self._hz = v
        self.update()

    def frequency(self) -> Optional[int]:
        return self._hz

    @property
    def has_frequency(self) -> bool:
        return self._hz is not None

    def set_bounds(self, min_hz: int, max_hz: int):
        self._min_hz = int(min_hz)
        self._max_hz = int(max_hz)

    # ------------------------------------------------------ 纯算术（可测）
    @staticmethod
    def place_value_hz(digit_index: int) -> int:
        """位权（Hz）：第 0 位=1e8，逐位 ÷10，第 8 位=1。"""
        if not 0 <= digit_index < FreqDisplay.DIGIT_COUNT:
            raise IndexError(f"digit index {digit_index} out of range")
        return 10 ** (FreqDisplay.DIGIT_COUNT - 1 - digit_index)

    @classmethod
    def nudge_digit(cls, hz: int, digit_index: int, direction: int,
                    min_hz: int = 0, max_hz: int = 2_000_000_000) -> int:
        step = cls.place_value_hz(digit_index)
        v = int(hz) + (step if direction >= 0 else -step)
        return max(int(min_hz), min(int(max_hz), v))

    @classmethod
    def truncate_below(cls, hz: int, digit_index: int) -> int:
        """该位及以右清零（右键语义）。"""
        step = cls.place_value_hz(digit_index)
        return (int(hz) // step) * step

    # ------------------------------------------------------------ 命中/布局
    def _cells(self) -> List[Tuple[str, QRectF, bool, int]]:
        """字符格布局：[(字符, rect, 是否数字, 数字位号)]。

        整体缩放适配（窄栏不修边距糊）：title_lg 基准字号放不下整行时
        逐级缩字号（下限 8px），极端窄再按比例压格宽——整行永远完整、
        居中、单排，绝不溢出裁切、绝不按字符换行（1 5 6 - 5 2 裁尾事故）。
        """
        _t = tokens()
        text = (digits_of(self._hz) if self._hz is not None
                else "-" * self.DIGIT_COUNT)
        grouped = []
        for i, ch in enumerate(text):
            grouped.append(ch)
            if i in (2, 5):
                grouped.append(".")
        font = QFont("JetBrains Mono, Fira Code, Consolas, monospace")
        font.setWeight(QFont.Weight(max(1, min(99, round(
            _t.FONT_WEIGHT["title_lg"] / 10)))))
        base_pt = int(_t.FONT["title_lg"].removesuffix("px"))
        avail = max(1.0, float(self.width()) - 4.0)
        fm = cell_w = dot_w = total = None
        for pt in range(base_pt, 7, -1):
            font.setPixelSize(pt)
            fm = QFontMetricsF(font)
            cell_w = fm.horizontalAdvance("0") * 1.18
            dot_w = cell_w * 0.5
            total = cell_w * self.DIGIT_COUNT + dot_w * 2
            if total <= avail:
                break
        if total > avail:
            scale = avail / total
            cell_w *= scale
            dot_w *= scale
            total = avail
        x = (self.width() - total) / 2.0
        y = (self.height() - fm.height()) / 2.0
        cells: List[Tuple[str, QRectF, bool, int]] = []
        digit_idx = -1
        for ch in grouped:
            w = dot_w if ch == "." else cell_w
            is_digit = ch != "."
            if is_digit:
                digit_idx += 1
            cells.append((ch, QRectF(x, y, w, fm.height()), is_digit,
                          digit_idx if is_digit else -1))
            x += w
        return cells

    def digit_at(self, x: float) -> int:
        """x 坐标 -> 数字位号（未命中返回 -1）。"""
        for _ch, rect, is_digit, idx in self._cells():
            if is_digit and rect.left() - 2 <= x <= rect.right() + 2:
                return idx
        return -1

    # ------------------------------------------------------------------ 交互
    def _apply_digit_delta(self, digit_index: int, direction: int):
        if self._hz is None or not self.isEnabled():
            return
        v = self.nudge_digit(self._hz, digit_index, direction,
                             self._min_hz, self._max_hz)
        if v != self._hz:
            self.frequency_edited.emit(v)

    def _apply_truncate(self, digit_index: int):
        if self._hz is None or not self.isEnabled():
            return
        v = self.truncate_below(self._hz, digit_index)
        if v != self._hz:
            self.frequency_edited.emit(v)

    def mouseMoveEvent(self, event):
        self._hover_digit = self.digit_at(event.position().x())
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_digit = -1
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        idx = self.digit_at(event.position().x())
        if idx >= 0:
            self._apply_digit_delta(idx, 1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        idx = self.digit_at(event.position().x())
        if idx < 0:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            half = self.height() / 2.0
            self._apply_digit_delta(idx, 1 if event.position().y() < half else -1)
        elif event.button() == Qt.RightButton:
            self._apply_truncate(idx)
        event.accept()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        _t = tokens()

        bg = QColor(_t.COLORS["card_1"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect(), _t.RADIUS["card_md"],
                                _t.RADIUS["card_md"])

        def _alpha(level: str) -> int:
            return round(255 * float(_t.TEXT_ALPHA[level]))

        enabled = self.isEnabled() and self._hz is not None
        strong = QColor(255, 255, 255, _alpha("primary"))
        dim = QColor(255, 255, 255, _alpha("tertiary"))
        if not enabled:
            strong = QColor(_t.COLORS["gray_300"])
            dim = QColor(_t.COLORS["gray_400"])
        accent = QColor(_t.COLORS["accent"])

        digits = (digits_of(self._hz) if self._hz is not None
                  else "-" * self.DIGIT_COUNT)
        # 前导零暗显：找到第一个非零位，其左侧的 0 全部 dim
        first_sig = 0
        while first_sig < self.DIGIT_COUNT - 1 and digits[first_sig] == "0":
            first_sig += 1

        for ch, rect, is_digit, idx in self._cells():
            if ch == ".":
                painter.setPen(QPen(dim, 2))
                cx = rect.center().x()
                cy = rect.center().y()
                painter.drawLine(QPointF(cx - 3, cy), QPointF(cx + 3, cy))
                continue
            if not enabled:
                col = strong
            elif idx < first_sig:
                col = dim
            else:
                col = strong
            if idx == self._hover_digit and enabled:
                col = accent
            painter.setPen(col)
            painter.drawText(rect, Qt.AlignCenter, ch)
            if idx == self._hover_digit and enabled:
                painter.setPen(QPen(accent, 2))
                painter.drawLine(
                    QPointF(rect.left() + 4, rect.bottom() - 2),
                    QPointF(rect.right() - 4, rect.bottom() - 2))
        painter.end()
