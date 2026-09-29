# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端触屏管理器
========================

统一管理触屏/Surface/平板二合一设备的触控适配：
  - 自动检测触屏设备（QTouchDevice.devices()）
  - 手动覆盖（settings 里 touch_mode = auto/on/off）
  - 提供触控目标尺寸（≥44×44 逻辑像素）
  - 全局 WA_AcceptTouchEvents 开关
  - touch_mode_changed 信号通知各控件重新适配

设计原则：
  - 不破坏鼠标桌面手感：触屏模式只放大触控热区、补充手势，
    鼠标模式下行为与之前完全一致。
  - 自动检测优先：有 QTouchDevice 且至少一个 TouchScreen 类型 → 自动开启。
  - 可手动强制：settings.touch_mode="on" 强制开启，"off" 强制关闭。
  - 单例全局：TouchManager.instance() 拿到唯一实例。

红线：不造假触控事件；无触屏设备时自动检测返回 False，不假装支持。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, Signal
try:
    from PySide6.QtGui import QTouchDevice
except Exception:  # pragma: no cover - 部分 PySide6 构建无 QTouchDevice
    QTouchDevice = None  # type: ignore


# 触控目标最小尺寸（逻辑像素，对标 Apple HIG / Material Design 48dp）
TOUCH_TARGET_MIN = 44
# 触屏模式下 splitter 手柄加宽
TOUCH_SPLITTER_HANDLE = 10
# 触屏模式下滚动条加宽
TOUCH_SCROLLBAR_WIDTH = 14
# 触屏模式下列表行高
TOUCH_LIST_ROW_H = 40


class TouchManager(QObject):
    """全局触屏模式管理器（单例）。"""

    _instance: "Optional[TouchManager]" = None

    # 触屏模式切换信号（True=触屏模式，False=鼠标模式）
    touch_mode_changed = Signal(bool)

    def __new__(cls) -> "TouchManager":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._initialized = False
            cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._mode_override: Optional[bool] = None  # None=自动, True=强制开, False=强制关
        self._cached_touch_available: Optional[bool] = None
        self._initialized = True

    # ------------------------------------------------------------------
    # 触屏设备检测
    # ------------------------------------------------------------------
    @staticmethod
    def detect_touch_devices() -> List:
        """返回所有 TouchScreen 类型的输入设备（不包括 TouchPad）。

        部分 PySide6 构建/离屏平台无 QTouchDevice → 返回空列表（自动检测视为无触屏）。
        """
        if QTouchDevice is None:
            return []
        try:
            devices = QTouchDevice.devices()
            return [d for d in devices
                    if d.type() == QTouchDevice.DeviceType.TouchScreen]
        except Exception:
            return []

    def touch_available(self) -> bool:
        """是否检测到触屏硬件（缓存结果，避免反复查询）。"""
        if self._cached_touch_available is None:
            self._cached_touch_available = len(self.detect_touch_devices()) > 0
        return self._cached_touch_available

    def rescan(self) -> None:
        """重新扫描触屏设备（热插拔后调用）。"""
        self._cached_touch_available = None
        self.touch_available()

    # ------------------------------------------------------------------
    # 模式控制
    # ------------------------------------------------------------------
    def set_override(self, on: Optional[bool]) -> None:
        """
        设置手动覆盖。
          None  = 自动检测（默认）
          True  = 强制触屏模式
          False = 强制鼠标模式
        """
        old = self.is_touch_mode()
        self._mode_override = on
        new = self.is_touch_mode()
        if old != new:
            self.touch_mode_changed.emit(new)

    def set_from_settings(self, touch_mode: str) -> None:
        """从 settings 字符串解析覆盖模式：'auto'/'on'/'off'。"""
        if touch_mode == "on":
            self.set_override(True)
        elif touch_mode == "off":
            self.set_override(False)
        else:
            self.set_override(None)

    def is_touch_mode(self) -> bool:
        """当前是否处于触屏模式。"""
        if self._mode_override is not None:
            return self._mode_override
        return self.touch_available()

    # ------------------------------------------------------------------
    # 尺寸查询
    # ------------------------------------------------------------------
    def target_min_height(self) -> int:
        """触控模式下按钮/输入框最小高度，鼠标模式返回 0（不限制）。"""
        return TOUCH_TARGET_MIN if self.is_touch_mode() else 0

    def target_min_width(self) -> int:
        """触控模式下按钮/输入框最小宽度，鼠标模式返回 0。"""
        return TOUCH_TARGET_MIN if self.is_touch_mode() else 0

    def splitter_handle(self) -> int:
        """splitter 手柄厚度：触屏 10px，鼠标 4px（tokens 默认）。"""
        return TOUCH_SPLITTER_HANDLE if self.is_touch_mode() else 4

    def scrollbar_width(self) -> int:
        """滚动条宽度：触屏 14px，鼠标 10px。"""
        return TOUCH_SCROLLBAR_WIDTH if self.is_touch_mode() else 10

    def list_row_height(self) -> int:
        """列表行高：触屏 40px，鼠标 0（由样式决定）。"""
        return TOUCH_LIST_ROW_H if self.is_touch_mode() else 0


def touch_manager() -> TouchManager:
    """返回全局 TouchManager 单例。"""
    return TouchManager()
