"""
MBDSDR 桌面端 Dock 布局管理器
==============================

在已有 QMainWindow / QDockWidget / QTabWidget 之上升级出 CarWith 风格的布局
系统。**不推倒重来**：它不重建面板，只重新安排既有面板的停靠关系、弹性比例与
折叠窄条。

设计原则（流体布局，不写死像素）：
  * 分区用 stretch factor + min/max size 表达，不用 setSizes([固定值])。
  * 首次打开的比例只是"可被用户拖动改变并持久化(QSettings)"的初始建议。
  * 分隔条 handle 4px；双击 handle 复位到 token 比例。
  * 副窗可折叠成 36px 图标窄条（只显示图标，点击展开）。
  * 浮动 QDockWidget 套 24px 大圆角 + 半透明深色底（QSS 来自 tokens）。

三个布局预设：
  * focus    —— 全屏频谱 + 右侧 36px 图标窄条（控制/状态/AI 点图标展开）
  * analysis —— 中央频谱 + 右侧射频天空(弹性比例) + 底部控制条
  * grid     —— 频谱 / 射频天空 / 控制 / 状态 多区分栏

红线：不造数据；布局只动几何与可见性，不改任何业务信号。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QSettings, QEvent, QObject
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QPushButton, QLabel,
    QMainWindow, QTabWidget, QSplitter,
)

from tokens import tokens
from panel_registry import registry, AREA_CENTER, AREA_RIGHT, AREA_BOTTOM


# ----------------------------------------------------------------------
# 分隔条双击复位事件过滤器
# ----------------------------------------------------------------------
class _SplitterResetFilter(QObject):
    """双击 QSplitter handle 时回调 owner.reset_splitters()。"""

    def __init__(self, owner: "DockLayoutManager") -> None:
        super().__init__()
        self._owner = owner

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        if event.type() == QEvent.MouseButtonDblClick:
            try:
                self._owner.reset_splitters()
            except Exception:
                pass
            return True
        return False


class DockLayoutManager:
    """管理 QDockWidget 布局、预设切换、折叠窄条与状态持久化。"""

    PRESETS = ("focus", "analysis", "grid")

    def __init__(self, main_window: QMainWindow) -> None:
        self.mw = main_window
        self.t = tokens()
        self._reset_filter = _SplitterResetFilter(self)
        # 运行态：射频天空面板按需从中央 Tab 移到右侧 Dock（可移回）
        self._sky_dock: Optional[QDockWidget] = None
        self._sky_in_dock: bool = False
        # 当前预设名（QSettings 持久化）
        self.current_preset: str = "focus"
        # 折叠窄条（36px 图标条），按需创建
        self._strip: Optional[QWidget] = None
        self._strip_buttons: Dict[str, QPushButton] = {}

    # ------------------------------------------------------------------
    # 引用便捷访问
    # ------------------------------------------------------------------
    @property
    def left_tab(self) -> Optional[QTabWidget]:
        return getattr(self.mw, "left_tab", None)

    @property
    def docks(self) -> Dict[str, QDockWidget]:
        out: Dict[str, QDockWidget] = {}
        for pid in ("control", "status", "ai"):
            d = getattr(self.mw, f"{pid}_dock", None)
            if d is not None:
                out[pid] = d
        return out

    # ------------------------------------------------------------------
    # 折叠窄条（36px 图标条）
    # ------------------------------------------------------------------
    def build_collapse_strip(self) -> QWidget:
        """创建右侧 36px 图标窄条（只显示图标，点击展开对应 Dock）。"""
        if self._strip is not None:
            return self._strip
        strip = QWidget()
        strip.setObjectName("collapseStrip")
        strip.setFixedWidth(self.t.SIZE["collapse_strip"])  # 触控图标条，固定宽合理
        lay = QVBoxLayout(strip)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(4)
        for pid, dock in self.docks.items():
            spec = registry().get(pid)
            icon = spec.icon if spec else "•"
            btn = QPushButton(icon)
            btn.setObjectName("stripIcon")
            btn.setFixedSize(self.t.RADIUS["circle_sm"] - 28,
                             self.t.RADIUS["circle_sm"] - 28)
            btn.setToolTip(spec.name if spec else pid)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, p=pid: self._on_strip_icon(p, checked))
            lay.addWidget(btn, 0, Qt.AlignHCenter)
            self._strip_buttons[pid] = btn
        lay.addStretch(1)
        self._strip = strip
        return strip

    def _on_strip_icon(self, pid: str, checked: bool) -> None:
        """窄条图标点击：展开/折叠对应 Dock。"""
        dock = self.docks.get(pid)
        if dock is None:
            return
        dock.setVisible(checked)
        if checked:
            dock.raise_()

    # ------------------------------------------------------------------
    # 射频天空在 Tab / 右侧 Dock 之间搬运（流体侧栏用）
    # ------------------------------------------------------------------
    def _ensure_sky_dock(self) -> QDockWidget:
        """把 self.mw.sky_view 包进一个 QDockWidget（懒创建，可重复调用）。"""
        if self._sky_dock is not None:
            return self._sky_dock
        sky = getattr(self.mw, "sky_view", None)
        dock = QDockWidget("射频天空", self.mw)
        dock.setObjectName("skyDock")
        dock.setWidget(sky)
        dock.setFeatures(QDockWidget.DockWidgetMovable
                        | QDockWidget.DockWidgetFloatable
                        | QDockWidget.DockWidgetClosable)
        self._sky_dock = dock
        return dock

    def _move_sky_to_right(self) -> None:
        """把 sky_view 从中央 Tab 移到右侧 Dock 区域（弹性 40%）。"""
        if self._sky_in_dock:
            return
        tab = self.left_tab
        if tab is not None:
            for i in range(tab.count()):
                if tab.tabText(i).startswith("射频天空"):
                    tab.removeTab(i)
                    break
        dock = self._ensure_sky_dock()
        dock.setVisible(True)
        # 右侧已有 control/status/ai 时，把 sky 叠加到右侧顶部
        right_docks = [d for d in self.docks.values() if not d.isFloating()]
        if right_docks:
            self.mw.splitDockWidget(right_docks[0], dock, Qt.Vertical)
        else:
            self.mw.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._sky_in_dock = True

    def _move_sky_to_tab(self) -> None:
        """把 sky_view 搬回中央 Tab。"""
        if not self._sky_in_dock:
            return
        tab = self.left_tab
        sky = getattr(self.mw, "sky_view", None)
        if tab is not None and sky is not None:
            # 插入到"频谱"之后
            idx = 1 if tab.count() > 0 else 0
            tab.insertTab(idx, sky, "射频天空")
        if self._sky_dock is not None:
            self._sky_dock.setVisible(False)
        self._sky_in_dock = False

    # ------------------------------------------------------------------
    # 预设
    # ------------------------------------------------------------------
    def apply_preset(self, name: str) -> None:
        """切换布局预设。失败不崩，退回当前状态。"""
        if name not in self.PRESETS:
            return
        self.current_preset = name
        try:
            getattr(self, f"_preset_{name}")()
        except Exception:
            # 预设失败绝不能拖垮主窗口
            pass
        self._style_floating_docks()
        self._attach_splitter_reset()

    def _raise_center_tab(self, keyword: str) -> None:
        """把中央 Tab 切到标题含 keyword 的页。"""
        tab = self.left_tab
        if tab is None:
            return
        for i in range(tab.count()):
            if keyword in tab.tabText(i):
                tab.setCurrentIndex(i)
                return

    def _preset_focus(self) -> None:
        """专注：全屏频谱 + 右侧 36px 图标窄条。"""
        self._move_sky_to_tab()
        self._raise_center_tab("频谱")
        # 右侧三个 Dock 默认折叠（隐藏），只留窄条图标
        for pid, dock in self.docks.items():
            dock.setVisible(False)
            if pid in self._strip_buttons:
                self._strip_buttons[pid].setChecked(False)
        # 窄条作为停靠面板加入右侧（QMainWindow 管理其位置）
        strip = self.build_collapse_strip()
        if strip.parent() is None:
            self.mw.addDockWidget(Qt.RightDockWidgetArea, self._wrap_in_dock(strip, "面板"))

    def _preset_analysis(self) -> None:
        """分析：中央频谱 + 右侧射频天空 + 底部控制条。"""
        self._raise_center_tab("频谱")
        self._move_sky_to_right()
        # 控制/状态/AI 移到底部横向条
        bottom = self.docks.get("control")
        if bottom is not None:
            self.mw.addDockWidget(Qt.BottomDockWidgetArea, bottom)
            bottom.setVisible(True)
        for pid in ("status", "ai"):
            d = self.docks.get(pid)
            if d is not None:
                d.setVisible(False)
        # sky_dock 与 control 右侧竖向叠
        sky = self._ensure_sky_dock()
        sky.setVisible(True)

    def _preset_grid(self) -> None:
        """网格：频谱 / 射频天空 / 控制 / 状态 多区分栏。"""
        self._raise_center_tab("频谱")
        self._move_sky_to_right()
        sky = self._ensure_sky_dock()
        sky.setVisible(True)
        # 控制、状态右侧竖向叠放；AI 收起
        control = self.docks.get("control")
        status = self.docks.get("status")
        if control is not None:
            control.setVisible(True)
            self.mw.addDockWidget(Qt.RightDockWidgetArea, control)
        if status is not None:
            status.setVisible(True)
            if control is not None:
                self.mw.splitDockWidget(control, status, Qt.Vertical)
        ai = self.docks.get("ai")
        if ai is not None:
            ai.setVisible(False)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _wrap_in_dock(self, widget: QWidget, title: str) -> QDockWidget:
        dock = QDockWidget(title, self.mw)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setObjectName("stripDock")
        dock.setTitleBarWidget(QWidget())  # 窄条不显示标题栏
        dock.setFixedWidth(self.t.SIZE["collapse_strip"])
        return dock

    def _style_floating_docks(self) -> None:
        """浮动 Dock 套大圆角 + 半透明深色底（QSS 已在 tokens，这里补边距）。"""
        for dock in list(self.docks.values()):
            try:
                dock.setContentsMargins(0, 0, 0, 0)
            except Exception:
                pass

    def reset_splitters(self) -> None:
        """双击 handle：把内部频谱 FFT/瀑布 Splitter 复位到 token 默认比例。

        QMainWindow 的 dock 比例由用户拖动决定并持久化，这里只复位频谱区内部的
        垂直 Splitter（SpectrumPanel.reset_splitter 按 tokens RATIO 计算，非死像素）。
        """
        try:
            spec = getattr(self.mw, "spectrum", None)
            if spec is not None and hasattr(spec, "reset_splitter"):
                spec.reset_splitter()
        except Exception:
            pass

    def _attach_splitter_reset(self) -> None:
        """给主窗口内所有 QSplitter 的 handle 装双击复位过滤器。

        注意：QDoubleClick 发生在 handle 子控件上，必须 installEventFilter 到
        handle(i) 本身（不是 QSplitter.viewport()，那里收不到 handle 的双击）。
        """
        for sp in self.mw.findChildren(QSplitter):
            try:
                for i in range(sp.count() - 1):
                    h = sp.handle(i)
                    if h is not None:
                        h.installEventFilter(self._reset_filter)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 状态持久化（QSettings）
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        s = QSettings("MBDSDR", "Desktop")
        s.setValue("layout_preset", self.current_preset)
        s.setValue("dock_state", self.mw.saveState())

    def restore_state(self) -> None:
        s = QSettings("MBDSDR", "Desktop")
        preset = s.value("layout_preset")
        state = s.value("dock_state")
        if state is not None:
            try:
                self.mw.restoreState(state)
            except Exception:
                pass
        if preset in self.PRESETS:
            self.current_preset = preset
