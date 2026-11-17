# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端触屏管理器
========================

统一管理触屏/Surface/平板二合一设备的触控适配：
  - 自动检测触屏硬件（Qt 指针设备枚举 + Windows 数字化仪标志）
  - 运行时补测：真收到触控事件就自动升级为触屏模式（枚举不到也不漏）
  - 手动覆盖（settings 里 touch_mode = auto/on/off），只作逃生口，默认 auto
  - 提供触控目标尺寸（≥44×44 逻辑像素）
  - 全局 WA_AcceptTouchEvents 开关
  - touch_mode_changed 信号通知各控件重新适配

设计原则：
  - 不破坏鼠标桌面手感：触屏模式只放大触控热区、补充手势，
    鼠标模式下行为与之前完全一致。
  - 自动检测优先：Qt6 的 QTouchDevice 已被移除（PySide6 里根本没有这个类），
    旧写法必然恒为 False，用户只能靠"强制触屏"菜单——这就是"为什么有一个
    触屏模式"的根因。现在改为 Qt6 QPointingDevice + Win32 SM_DIGITIZER，
    并在真机触控事件到达时自动升级，无需用户选模式。
  - 可手动强制：settings.touch_mode="on"/"off" 仅用于排障。
  - 单例全局：TouchManager.instance() 拿到唯一实例。

红线：不造假触控事件；无触屏设备时自动检测返回 False，不假装支持。
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtCore import QObject, Signal
try:
    from PySide6.QtGui import QTouchDevice      # Qt5 路径
except Exception:  # pragma: no cover - PySide6(Qt6) 无此类
    QTouchDevice = None  # type: ignore
try:
    from PySide6.QtGui import QPointingDevice   # Qt6 路径
except Exception:  # pragma: no cover
    QPointingDevice = None  # type: ignore


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
    def _qt_touch_devices() -> List:
        """Qt 指针设备枚举（Qt6 用 QPointingDevice，Qt5 才用 QTouchDevice）。"""
        if QPointingDevice is not None:
            try:
                return [d for d in QPointingDevice.devices()
                        if d.deviceType() == QPointingDevice.DeviceType.TouchScreen]
            except Exception:
                pass
        if QTouchDevice is not None:
            try:
                return [d for d in QTouchDevice.devices()
                        if d.type() == QTouchDevice.DeviceType.TouchScreen]
            except Exception:
                pass
        return []

    @staticmethod
    def _windows_has_touch() -> bool:
        """Win32 数字化仪能力查询（Qt 枚举在 Windows 上常返回 0 个设备）。

        SM_DIGITIZER(94) 的 0x40/0x80 位分别代表内置/外接触摸数字化仪；
        SM_MAXIMUMTOUCHES(95) > 0 也说明存在触点。仅 Windows 生效。
        """
        if not sys.platform.startswith("win"):
            return False
        try:
            # 离屏/最小平台没有真实输入通道：即便宿主有触屏也不该算数
            # （否则 offscreen 测试结果随开发机硬件漂移）。
            from PySide6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is not None and app.platformName() in ("offscreen", "minimal"):
                return False
            import ctypes
            user32 = ctypes.windll.user32
            digitizer = int(user32.GetSystemMetrics(94))
            max_touches = int(user32.GetSystemMetrics(95))
            return bool(digitizer & 0xC0) or max_touches > 0
        except Exception:
            return False

    @classmethod
    def detect_touch_devices(cls) -> List:
        """返回检测到的触控设备列表（空列表 = 没有触屏）。

        Qt 枚举为空但当前处于真实触控事件上下文中时，会由
        ``note_touch_event()`` 直接把能力标记为 True，不依赖这里。
        """
        return cls._qt_touch_devices()

    def touch_available(self) -> bool:
        """是否检测到触屏硬件（缓存结果，避免反复查询）。"""
        if self._cached_touch_available is None:
            self._cached_touch_available = (
                len(self.detect_touch_devices()) > 0 or self._windows_has_touch())
        return self._cached_touch_available

    def note_touch_event(self) -> None:
        """运行时补测：真收到触控事件就自动升级为触屏模式。

        枚举不到触屏（Windows 常见）时，第一次真实触摸就是最硬的证据；
        只改能力缓存，不碰用户的手动覆盖。
        """
        if self._cached_touch_available:
            return
        self._cached_touch_available = True
        if self._mode_override is None:
            self.touch_mode_changed.emit(self.is_touch_mode())

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
