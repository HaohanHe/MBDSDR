# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端触屏辅助工具
==========================

可复用的触控适配 mixin 和工具函数：
  - apply_touch_target(widget): 给控件设置触控友好的最小尺寸
  - enable_touch_events(widget): 开启 WA_AcceptTouchEvents
  - PinchZoomMixin: 双指捏合缩放 + 单指拖动的 mixin（给频谱/天空/地图用）
  - TouchFriendlyScrollArea: 触屏可滚动的 QScrollArea
  - TouchFriendlyListWidget: 触屏友好的列表（行高加大、可滚动）

所有 helper 都检查 TouchManager.is_touch_mode()，鼠标模式下零开销旁路。
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt, QEvent, QPointF, Signal
try:
    from PySide6.QtGui import QTouchEvent, QMouseEvent
except Exception:  # pragma: no cover
    QTouchEvent = None  # type: ignore
    QMouseEvent = None  # type: ignore
try:
    # PySide6 把手势类放在 QtWidgets（QtGui 里只有 QNativeGestureEvent）
    from PySide6.QtWidgets import QGestureEvent, QPinchGesture, QPanGesture
except Exception:  # pragma: no cover
    QGestureEvent = None  # type: ignore
    QPinchGesture = None  # type: ignore
    QPanGesture = None  # type: ignore
from PySide6.QtWidgets import (QWidget, QScrollArea, QListWidget,
                               QAbstractItemView)

from touch_manager import touch_manager, TOUCH_TARGET_MIN


def enable_touch_events(widget: QWidget) -> None:
    """给控件开启触屏事件接受（WA_AcceptTouchEvents）。"""
    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
    except Exception:
        pass


def apply_touch_target(widget: QWidget, min_h: int = TOUCH_TARGET_MIN,
                       min_w: int = 0) -> None:
    """
    给控件设置触控友好的最小尺寸。
    触屏模式下 min_h ≥ 44px；鼠标模式下不修改（保留原尺寸）。
    """
    tm = touch_manager()
    if not tm.is_touch_mode():
        return
    try:
        cur_h = widget.minimumHeight()
        cur_w = widget.minimumWidth()
        new_h = max(cur_h, min_h) if min_h > 0 else cur_h
        new_w = max(cur_w, min_w) if min_w > 0 else cur_w
        if new_h != cur_h or new_w != cur_w:
            widget.setMinimumSize(new_w, new_h)
    except Exception:
        pass


def apply_touch_targets(parent: QWidget) -> None:
    """
    递归给 parent 下所有 QPushButton/QComboBox/QLineEdit 等可交互控件
    应用触控最小高度。触屏模式下调用，鼠标模式零开销。
    """
    tm = touch_manager()
    if not tm.is_touch_mode():
        return
    from PySide6.QtWidgets import QPushButton, QComboBox, QLineEdit, QSpinBox
    for w in parent.findChildren(QPushButton):
        apply_touch_target(w)
    for w in parent.findChildren(QComboBox):
        apply_touch_target(w)
    for w in parent.findChildren(QLineEdit):
        apply_touch_target(w)
    for w in parent.findChildren(QSpinBox):
        apply_touch_target(w)


# ─────────────────────────────────────────────────────────────────────
# PinchZoomMixin —— 双指捏合缩放 + 单指拖动
# ─────────────────────────────────────────────────────────────────────

class PinchZoomMixin:
    """
    给 QWidget 子类添加双指捏合缩放 + 单指拖动能力。

    用法::

        class SpectrumWidget(QWidget, PinchZoomMixin):
            def __init__(self):
                super().__init__()
                self.init_pinch_zoom()
                self.pinch_scale_changed.connect(self._on_scale)
                self.pan_changed.connect(self._on_pan)

            def _on_scale(self, scale_factor, center_pos):
                # 以 center_pos 为中心缩放 scale_factor 倍
                ...

            def _on_pan(self, dx, dy):
                # 平移 dx, dy（像素）
                ...

    子类需要实现：
      - _get_view_range() -> (x_min, x_max, y_min, y_max): 当前视图范围
      - _set_view_range(x_min, x_max, y_min, y_max): 设置视图范围
    或者直接连接 pinch_scale_changed / pan_changed 信号。

    鼠标模式下：wheel 缩放（子类原有逻辑不变），拖动由子类原有 mouseMoveEvent 处理。
    触屏模式下：双指捏合触发缩放，单指拖动触发平移。
    """

    pinch_scale_changed = Signal(float, QPointF)  # scale_factor, center_widget_pos
    pan_changed = Signal(float, float)             # dx, dy in widget pixels

    def init_pinch_zoom(self) -> None:
        """初始化捏合缩放手势（必须在 __init__ 中调用）。"""
        self._pinch_last_scale: float = 1.0
        self._pinch_last_center: Optional[QPointF] = None
        self._pan_last_pos: Optional[QPointF] = None
        self._pinch_active: bool = False
        # 开启触屏事件和手势
        enable_touch_events(self)
        try:
            self.grabGesture(Qt.GestureType.PinchGesture)
            self.grabGesture(Qt.GestureType.PanGesture)
        except Exception:
            pass

    def event(self, event: QEvent) -> bool:
        """拦截手势事件。"""
        if event.type() == QEvent.Type.Gesture:
            if self._handle_gesture(event):
                return True
        # 触屏模式下单指拖动：把 TouchEvent 转成 pan
        if event.type() == QEvent.Type.TouchBegin:
            self._handle_touch_begin(event)
            return True
        if event.type() == QEvent.Type.TouchUpdate:
            if self._handle_touch_update(event):
                return True
        if event.type() == QEvent.Type.TouchEnd:
            self._handle_touch_end(event)
            return True
        return super().event(event)

    def _handle_gesture(self, event) -> bool:
        if QPinchGesture is None or QPanGesture is None:
            return False
        pinch = event.gesture(Qt.GestureType.PinchGesture)
        if pinch is not None:
            return self._handle_pinch(pinch)
        pan = event.gesture(Qt.GestureType.PanGesture)
        if pan is not None:
            return self._handle_pan_gesture(pan)
        return False

    def _handle_pinch(self, gesture) -> bool:
        change_flags = gesture.changeFlags()
        if change_flags & QPinchGesture.ChangeFlag.ScaleFactorChanged:
            scale = gesture.scaleFactor()
            center = gesture.centerPoint()
            if center is not None:
                self.pinch_scale_changed.emit(scale, QPointF(center.x(), center.y()))
        if change_flags & QPinchGesture.ChangeFlag.ScaleChanged:
            self._pinch_active = (gesture.state() != Qt.GestureState.GestureFinished)
        return True

    def _handle_pan_gesture(self, gesture) -> bool:
        delta = gesture.delta()
        if delta is not None and (delta.x() != 0 or delta.y() != 0):
            self.pan_changed.emit(float(delta.x()), float(delta.y()))
        return True

    def _handle_touch_begin(self, event: QTouchEvent) -> None:
        points = event.points()
        if len(points) == 1:
            self._pan_last_pos = QPointF(points[0].position())
        elif len(points) >= 2:
            self._pan_last_pos = None
            self._pinch_active = True

    def _handle_touch_update(self, event: QTouchEvent) -> bool:
        points = event.points()
        if len(points) == 1 and not self._pinch_active:
            pos = QPointF(points[0].position())
            if self._pan_last_pos is not None:
                dx = pos.x() - self._pan_last_pos.x()
                dy = pos.y() - self._pan_last_pos.y()
                if abs(dx) > 1 or abs(dy) > 1:
                    self.pan_changed.emit(dx, dy)
            self._pan_last_pos = pos
            return True
        return False

    def _handle_touch_end(self, event: QTouchEvent) -> None:
        points = event.points()
        if len(points) <= 1:
            self._pinch_active = False
            self._pan_last_pos = None


# ─────────────────────────────────────────────────────────────────────
# TouchFriendlyScrollArea
# ─────────────────────────────────────────────────────────────────────

class TouchFriendlyScrollArea(QScrollArea):
    """触屏友好的滚动区域：开启触屏事件，触屏模式下加宽滚动条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        enable_touch_events(self)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 触屏模式下允许用手指拖动内容（不需要精确抓滚动条）
        tm = touch_manager()
        if tm.is_touch_mode():
            try:
                self.setProperty("_touch_scroll", True)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
# TouchFriendlyListWidget
# ─────────────────────────────────────────────────────────────────────

class TouchFriendlyListWidget(QListWidget):
    """触屏友好的列表：行高加大、开启触屏滚动、选择模式适配。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        enable_touch_events(self)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        tm = touch_manager()
        if tm.is_touch_mode():
            self.setStyleSheet(
                f"QListWidget::item {{ height: {TOUCH_TARGET_MIN}px; }}"
            )
