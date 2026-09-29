# SPDX-License-Identifier: MIT
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

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QSettings, QEvent, QObject, QByteArray
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QPushButton, QLabel,
    QMainWindow, QTabWidget, QSplitter,
)

from tokens import tokens
from panel_registry import registry, AREA_CENTER, AREA_RIGHT, AREA_BOTTOM
from layout_store import (
    LayoutStore, layout_store, BUILTIN_LAYOUTS,
    _qbyte_to_b64, _b64_to_qbyte,
)

logger = logging.getLogger("mbdsdr.dock_layout")

# 中文显示名 -> 内置构建方法后缀（与工具栏下拉一致）
BUILTIN_LABELS = {"专注": "focus", "分析": "analysis", "网格": "grid"}


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

    #: 内置布局显示名（向后兼容常量）。实际可用布局以 list_layouts() 为准。
    PRESETS = ("专注", "分析", "网格")

    def __init__(self, main_window: QMainWindow,
                 store: Optional[LayoutStore] = None) -> None:
        self.mw = main_window
        self.t = tokens()
        self._reset_filter = _SplitterResetFilter(self)
        # 布局持久化仓库（可注入，便于 offscreen 测试）
        self.store: LayoutStore = store or layout_store()
        # 运行态：射频天空面板按需从中央 Tab 移到右侧 Dock（可移回）
        self._sky_dock: Optional[QDockWidget] = None
        self._sky_in_dock: bool = False
        # 当前布局显示名（持久化到 layout_store + QSettings）
        self.current_preset: str = BUILTIN_LAYOUTS[0]
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
        # 右侧 control/status/ai 在新三栏布局下是 QScrollArea（非 QDockWidget），
        # splitDockWidget 只接受 QDockWidget；筛掉非真实 dock 的项。
        right_docks = [d for d in self.docks.values()
                       if isinstance(d, QDockWidget) and not d.isFloating()]
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
    # 布局列表 / 切换 / 保存 / 删除
    # ------------------------------------------------------------------
    def list_layouts(self) -> List[str]:
        """所有可用布局名：内置（专注/分析/网格）在前 + 用户自定义在后。"""
        try:
            return self.store.list_presets()
        except Exception:  # noqa: BLE001
            return list(BUILTIN_LAYOUTS)

    def is_builtin(self, name: str) -> bool:
        try:
            return self.store.is_builtin(name)
        except Exception:  # noqa: BLE001
            return name in BUILTIN_LAYOUTS

    def apply_preset(self, name: str) -> None:
        """切换布局。内置走构建方法；用户预设从 store 读 data 恢复。失败不崩。"""
        if name not in self.list_layouts():
            return
        self.current_preset = name
        try:
            method_suffix = BUILTIN_LABELS.get(name)
            if method_suffix:
                getattr(self, f"_preset_{method_suffix}")()
            else:
                data = self.store.get_preset_data(name)
                if data:
                    self.apply_layout_data(data)
        except Exception:  # noqa: BLE001
            # 布局失败绝不能拖垮主窗口
            logger.warning("应用布局 %s 失败", name, exc_info=True)
        self._style_floating_docks()
        self._attach_splitter_reset()

    # ------------------------------------------------------------------
    # 布局状态抓取 / 恢复（用户自定义布局的核心）
    # ------------------------------------------------------------------
    def capture_layout(self) -> Dict:
        """抓取当前完整布局状态，返回可 JSON 序列化字典。

        含：QMainWindow.saveState + 三栏 splitter 状态/尺寸 + 左/右栏 Tab 顺序与
        当前索引 + 各面板（Tab 页签）可见性。
        """
        out: Dict = {}
        # QMainWindow 停靠状态（QDockWidget 体系；新三栏布局下通常为空但无害）
        try:
            out["qmain_state"] = _qbyte_to_b64(self.mw.saveState())
        except Exception:  # noqa: BLE001
            out["qmain_state"] = ""
        # 三栏主 splitter
        sp = getattr(self.mw, "_main_splitter", None)
        if isinstance(sp, QSplitter):
            try:
                out["splitter_state"] = _qbyte_to_b64(sp.saveState())
            except Exception:  # noqa: BLE001
                out["splitter_state"] = ""
            try:
                out["splitter_sizes"] = [int(s) for s in sp.sizes()]
            except Exception:  # noqa: BLE001
                out["splitter_sizes"] = []
        else:
            out["splitter_state"] = ""
            out["splitter_sizes"] = []
        # 左栏 Tab（顺序 + 当前索引）
        lt = self.left_tab
        if isinstance(lt, QTabWidget):
            out["left_tab_order"] = [lt.tabText(i) for i in range(lt.count())]
            out["left_tab_index"] = lt.currentIndex()
            out["left_panel_visible"] = self._capture_tab_visible(lt)
        else:
            out["left_tab_order"] = []
            out["left_tab_index"] = 0
            out["left_panel_visible"] = {}
        # 右栏 Tab
        rt = getattr(self.mw, "right_tab", None)
        if isinstance(rt, QTabWidget):
            out["right_tab_order"] = [rt.tabText(i) for i in range(rt.count())]
            out["right_tab_index"] = rt.currentIndex()
            out["right_panel_visible"] = self._capture_tab_visible(rt)
        else:
            out["right_tab_order"] = []
            out["right_tab_index"] = 0
            out["right_panel_visible"] = {}
        return out

    @staticmethod
    def _capture_tab_visible(tab: QTabWidget) -> Dict[str, bool]:
        # 左 / 右栏 Tab 页签可见性（QTabWidget.isTabVisible: True=可见）
        vis: Dict[str, bool] = {}
        for i in range(tab.count()):
            try:
                vis[tab.tabText(i)] = bool(tab.isTabVisible(i))
            except Exception:  # noqa: BLE001
                vis[tab.tabText(i)] = True
        return vis

    def apply_layout_data(self, data: Dict) -> None:
        """按 capture_layout() 产出的字典恢复布局。失败不崩。"""
        if not isinstance(data, dict):
            return
        # QMainWindow 停靠状态
        try:
            ba = _b64_to_qbyte(data.get("qmain_state", ""))
            if not ba.isEmpty():
                self.mw.restoreState(ba)
        except Exception:  # noqa: BLE001
            pass
        # 三栏 splitter：优先 setSizes，回退 saveState
        sp = getattr(self.mw, "_main_splitter", None)
        if isinstance(sp, QSplitter):
            sizes = data.get("splitter_sizes") or []
            if sizes and len(sizes) == sp.count():
                try:
                    sp.setSizes([int(s) for s in sizes])
                except Exception:  # noqa: BLE001
                    pass
            else:
                st = data.get("splitter_state", "")
                if st:
                    try:
                        sp.restoreState(_b64_to_qbyte(st))
                    except Exception:  # noqa: BLE001
                        pass
        # 左 / 右栏 Tab 顺序 + 当前索引 + 可见性
        self._restore_tab_order(self.left_tab,
                               data.get("left_tab_order", []),
                               data.get("left_tab_index", 0),
                               data.get("left_panel_visible", {}))
        self._restore_tab_order(getattr(self.mw, "right_tab", None),
                               data.get("right_tab_order", []),
                               data.get("right_tab_index", 0),
                               data.get("right_panel_visible", {}))

    @staticmethod
    def _restore_tab_order(tab: Optional[QTabWidget], order, current_index,
                           visible: Dict) -> None:
        if not isinstance(tab, QTabWidget) or not order:
            return
        try:
            pages = [(tab.tabText(i), tab.widget(i)) for i in range(tab.count())]
            by_text = dict(pages)
            while tab.count():
                tab.removeTab(0)
            for text in order:
                if text in by_text:
                    tab.addTab(by_text[text], text)
            for text, w in pages:
                if text not in order:
                    tab.addTab(w, text)
            # 可见性恢复
            if isinstance(visible, dict):
                for i in range(tab.count()):
                    t = tab.tabText(i)
                    if t in visible:
                        try:
                            tab.setTabVisible(i, bool(visible[t]))
                        except Exception:  # noqa: BLE001
                            pass
            if isinstance(current_index, int) and 0 <= current_index < tab.count():
                tab.setCurrentIndex(current_index)
        except Exception:  # noqa: BLE001
            logger.warning("恢复页签顺序失败", exc_info=True)

    # ------------------------------------------------------------------
    # 用户布局保存 / 删除
    # ------------------------------------------------------------------
    def save_user_layout(self, name: str) -> bool:
        """抓取当前布局并存为用户自定义布局（内置名可被同名自定义覆盖）。"""
        if not name:
            return False
        try:
            data = self.capture_layout()
            ok = self.store.save_preset(name, data)
            if ok:
                self.current_preset = name
                self.store.set_active(name)
            return ok
        except Exception:  # noqa: BLE001
            logger.warning("保存布局 %s 失败", name, exc_info=True)
            return False

    def delete_user_layout(self, name: str) -> bool:
        """删除用户布局（内置不可删）。"""
        try:
            return self.store.delete_preset(name)
        except Exception:  # noqa: BLE001
            return False

    def reset_to_factory(self) -> None:
        """清除所有用户布局，回到首个内置。"""
        try:
            self.store.reset_to_factory()
        except Exception:  # noqa: BLE001
            pass
        self.apply_preset(BUILTIN_LAYOUTS[0])

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
        # 控制/状态/AI 移到底部横向条（仅对真实 QDockWidget 生效；
        # 新三栏布局下它们是右栏 QScrollArea 页签，只切可见性）
        bottom = self.docks.get("control")
        if bottom is not None:
            if isinstance(bottom, QDockWidget):
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
        # 控制、状态右侧竖向叠放；AI 收起（仅真实 QDockWidget 才停靠/叠放）
        control = self.docks.get("control")
        status = self.docks.get("status")
        if control is not None:
            control.setVisible(True)
            if isinstance(control, QDockWidget):
                self.mw.addDockWidget(Qt.RightDockWidgetArea, control)
        if status is not None:
            status.setVisible(True)
            if isinstance(control, QDockWidget) and isinstance(status, QDockWidget):
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
    # 状态持久化（QSettings + layout_store JSON）
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        """落 QSettings（窗口 dock 状态）+ 记录 active 布局名到 layout_store。"""
        s = QSettings("MBDSDR", "Desktop")
        s.setValue("layout_preset", self.current_preset)
        s.setValue("dock_state", self.mw.saveState())
        try:
            self.store.set_active(self.current_preset)
        except Exception:  # noqa: BLE001
            pass

    def restore_state(self) -> None:
        """从 layout_store 读 active 布局并恢复；回退 QSettings。"""
        # 优先 layout_store 的 active 名
        active = None
        try:
            active = self.store.get_active()
        except Exception:  # noqa: BLE001
            active = None
        if active and active in self.list_layouts():
            self.current_preset = active
        else:
            s = QSettings("MBDSDR", "Desktop")
            preset = s.value("layout_preset")
            if preset in self.list_layouts():
                self.current_preset = preset
            else:
                self.current_preset = BUILTIN_LAYOUTS[0]
        # 恢复 QMainWindow dock 状态（三栏布局下多数为空，无害）
        s = QSettings("MBDSDR", "Desktop")
        state = s.value("dock_state")
        if state is not None:
            try:
                self.mw.restoreState(state)
            except Exception:
                pass
        # 启动恢复：仅恢复带 data 的用户布局（页签顺序/比例/可见性）。
        # 内置布局不在启动时重跑旧的 strip-dock 构建逻辑（保留三栏默认骨架，
        # 由用户在下拉里手动选择时再走 apply_preset -> _preset_*）。
        data = None
        try:
            data = self.store.get_preset_data(self.current_preset)
        except Exception:  # noqa: BLE001
            data = None
        if data:
            try:
                self.apply_layout_data(data)
            except Exception:  # noqa: BLE001
                logger.warning("启动恢复布局 %s 失败",
                               self.current_preset, exc_info=True)
