# SPDX-License-Identifier: MIT
"""可拖动的自由浮层（对齐原型 ``position:absolute`` 浮层 / "自由窗口"）。

用户口径（2026-11-07）：「UI 要弹性可动，像水一样，可以拖动放哪儿，
不是嘎巴就定死了」「参照 figma 目录 / 原型的那种动态可调整化」。
原型 index.html 的 ``#satCard`` / ``#capDrawer`` / ``#sky-hud`` /
``.sky-toolbar`` 全部是 absolute 浮层，``#proSplit`` 可拖；本模块把这套
语义落到 Qt 桌面端：

* ``FloatingPanel`` 是 central widget 的子 widget（**不进任何布局**），
  靠 ``move()`` 定位；标题条按住即拖，拖到哪儿停哪儿。
* 「⤢」把浮层提升为独立 Tool 窗口（真·自由窗口，可拖到任意屏幕位置），
  再点一次回到画布内；两种形态的位置分别记住。
* 「—」折叠成一条标题（不遮挡展示页），「⤶ 归位」把面板交还主窗口
  重新插回三栏，功能一点不删。
* 位置 / 尺寸 / 折叠态 / 是否独立窗口写 QSettings，下次启动原样恢复。

设计边界：面板对象身份全程不变（只换 parent），主窗口里所有
``self.left_tab`` / ``self.right_tab`` 引用继续有效；三栏 QSplitter 的
``count() == 3`` 契约由占位 widget 保持，不破坏既有布局测试。
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget,
)


class _DragHandle(QFrame):
    """标题条：按住拖动，双击折叠/展开。"""

    def __init__(self, panel: "FloatingPanel"):
        super().__init__()
        self._panel = panel
        self._grab_offset: Optional[QPoint] = None
        self.setCursor(Qt.SizeAllCursor)
        self.setObjectName("floatingPanelHandle")

    def mousePressEvent(self, e) -> None:  # noqa: N802 (Qt 命名)
        if e.button() != Qt.LeftButton:
            return
        g = e.globalPosition().toPoint()
        self._grab_offset = g - self._panel.mapToGlobal(QPoint(0, 0))
        self._panel.raise_()
        e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._grab_offset is None:
            return
        g = e.globalPosition().toPoint() - self._grab_offset
        self._panel.move_to_global(g)
        e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._grab_offset is not None:
            self._grab_offset = None
            self._panel.save_geometry()
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._panel.toggle_collapse()
        e.accept()


class FloatingPanel(QFrame):
    """一块可拖动的自由面板。

    ``on_return()`` 由主窗口实现：把 ``payload`` 插回原来的布局并销毁浮层。
    """

    _ORG = "MBDSDR"
    _APP = "Desktop"

    def __init__(self, key: str, title: str, payload: QWidget,
                 host: QWidget, on_return: Callable[[], None]):
        super().__init__(host)
        self._key = str(key)
        self._payload = payload
        self._host = host
        self._on_return = on_return
        self._detached = False
        self._collapsed = False
        self._expanded_size = None  # type: Optional[QPoint]

        self.setObjectName("floatingPanel")
        self.setFrameShape(QFrame.NoFrame)
        # 无条件规则直接落在面板自身：带 objectName 选择器的样式表会被
        # 主题 polish 冲掉，实测整块浮层变成白纸、能力矩阵的字全糊掉。
        # 子控件各自另设样式，互不干扰。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "background-color: rgba(14,17,24,0.94);"
            " border: 1px solid rgba(180,196,220,0.30);"
            " border-radius: 12px;")
        self.setMinimumSize(300, 200)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)

        self._handle = _DragHandle(self)
        self._handle.setFixedHeight(30)
        hl = QHBoxLayout(self._handle)
        hl.setContentsMargins(8, 0, 4, 0)
        hl.setSpacing(2)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("floatingPanelTitle")
        self._title_label.setStyleSheet(
            "color: #DCE4EF; background: transparent; font-size: 12px;")
        hl.addWidget(self._title_label, 1)

        self._collapse_btn = QToolButton()
        self._collapse_btn.setText("—")
        self._collapse_btn.setToolTip("折叠 / 展开这块面板（双击标题条同样有效）")
        self._collapse_btn.clicked.connect(self.toggle_collapse)
        hl.addWidget(self._collapse_btn)

        self._detach_btn = QToolButton()
        self._detach_btn.setText("⤢")
        self._detach_btn.setToolTip("弹出为独立自由窗口 / 收回画布内")
        self._detach_btn.clicked.connect(self.toggle_detach)
        hl.addWidget(self._detach_btn)

        self._return_btn = QToolButton()
        self._return_btn.setText("⤶")
        self._return_btn.setToolTip("归位：把面板放回原来的三栏位置")
        self._return_btn.clicked.connect(self.request_return)
        hl.addWidget(self._return_btn)
        root.addWidget(self._handle)

        payload.setParent(self)
        root.addWidget(payload, 1)
        payload.show()

        self._restore_geometry()
        self.show()
        self.raise_()

    # ---------------------------------------------------------------- 几何
    def _settings(self) -> QSettings:
        return QSettings(self._ORG, self._APP)

    def _restore_geometry(self) -> None:
        s = self._settings()
        base = "floating_panel/" + self._key
        size = s.value(base + "/size")
        pos = s.value(base + "/pos")
        if size is not None:
            try:
                q = size if hasattr(size, "width") else None
                if q is not None:
                    self.resize(q)
            except (TypeError, ValueError):
                pass
        if s.value(base + "/collapsed") in (True, "true", "1", 1):
            self._collapsed = True
            self._payload.hide()
            self._collapse_btn.setText("+")
            self.apply_collapsed_geometry()
        if s.value(base + "/detached") in (True, "true", "1", 1):
            self._detach_btn.setText("⤡")
            self._set_detached(True)
        if pos is not None:
            p = pos if hasattr(pos, "x") else None
            if p is not None:
                self.move_to_global(QPoint(int(p.x()), int(p.y())))
        else:
            # 首次：落在画布右侧偏上，不压住中央频谱/天空
            self.move(max(24, self._host.width() - self.width() - 48), 90)

    def save_geometry(self) -> None:
        s = self._settings()
        base = "floating_panel/" + self._key
        g = self.mapToGlobal(QPoint(0, 0))
        from PySide6.QtCore import QSize
        s.setValue(base + "/pos", g)
        s.setValue(base + "/size", QSize(self.width(), self.height()))
        s.setValue(base + "/collapsed", self._collapsed)
        s.setValue(base + "/detached", self._detached)

    def move_to_global(self, g: QPoint) -> None:
        """把左上角移到屏幕坐标 ``g``；画布内形态会夹在 host 可见区域内。"""
        if self._detached:
            self.move(g)
            return
        host = self.parentWidget() or self._host
        local = host.mapFromGlobal(g)
        # 允许贴边，但至少留 64px 标题条可见，免得拖飞了找不回来
        max_x = max(0, host.width() - 64)
        max_y = max(0, host.height() - 32)
        local.setX(min(max(0, local.x()), max_x))
        local.setY(min(max(0, local.y()), max_y))
        self.move(local)

    # ---------------------------------------------------------------- 形态
    def toggle_collapse(self) -> None:
        from PySide6.QtCore import QSize
        if self._collapsed:
            self._payload.show()
            self._collapsed = False
            self._collapse_btn.setText("—")
            # 先解掉折叠时钉死的高度，否则展开后永远只有一条标题
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            if self._expanded_size is not None:
                self.resize(self._expanded_size)
        else:
            self._expanded_size = QSize(self.width(), self.height())
            self._payload.hide()
            self._collapsed = True
            self._collapse_btn.setText("+")
            # adjustSize() 在隐藏 payload 后仍会留出整块空白（实测折叠态
            # 变一大坨黑压住频谱）。折叠就是只留标题条，高度显式钉死。
            self.setFixedHeight(self._collapsed_height())
        self.save_geometry()

    def _collapsed_height(self) -> int:
        """标题条真实高度 + 布局边距：拍 26 会让 30px 的 handle 越出父容器。"""
        return max(40, int(self._handle.height()) + 10)

    def apply_collapsed_geometry(self) -> None:
        """从 QSettings 恢复折叠态时同样压成一条标题，不显示空白大块。"""
        if self._collapsed:
            self.setFixedHeight(self._collapsed_height())

    def _set_detached(self, on: bool) -> None:
        if on == self._detached:
            return
        g = self.mapToGlobal(QPoint(0, 0))
        if on:
            self.setParent(None)
            self.setWindowFlag(Qt.Tool, True)
            self.setWindowFlag(Qt.FramelessWindowHint, False)
            self.setWindowTitle(self._title_label.text())
            self._detached = True
            self.show()
            self.move(g)
        else:
            self.setParent(self._host)
            self.setWindowFlag(Qt.Tool, False)
            self._detached = False
            self.show()
            self.move_to_global(g)

    def toggle_detach(self) -> None:
        self._set_detached(not self._detached)
        self._detach_btn.setText("⤡" if self._detached else "⤢")
        self.save_geometry()

    def attach_to_canvas(self) -> None:
        """强制收回画布内，并把「已弹出」落盘改写掉。

        给「独立窗口态不跨重启恢复」的面板在构造后调用：``_restore_geometry``
        原样还原 detached 后，顶层窗口没有主窗口当宿主，主窗口最小化时
        它仍独自留在桌面上——看起来像一块来历不明的黑框。
        """
        if not self._detached:
            return
        self._set_detached(False)
        self._detach_btn.setText("⤢")
        self.save_geometry()

    # ---------------------------------------------------------------- 归位
    def request_return(self) -> None:
        self.save_geometry()
        self._on_return()

    def take_payload(self) -> QWidget:
        """把面板内容交还调用方（脱离浮层，parent 置空）。"""
        self._payload.setParent(None)
        self._payload.show()
        return self._payload
