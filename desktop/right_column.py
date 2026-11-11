# SPDX-License-Identifier: MIT
"""
右栏：一根可滚动的纵向长列（SDR++ 侧栏形态）
=============================================

用户 2026-11-10 对着 SDR++ 截图裁决：右栏是「一根竖列、可以滚动」的侧栏，
不是十几个页签一次只显示一个。SDR++ 的左栏就是分组标题 + 展开内容连续堆叠、
滚轮上下扫；这里复刻同一形态：

  * QScrollArea 里纵向堆叠 RightSection（标题可点折叠/展开）
  * 仍然保留 QTabWidget 的最小 API（count/widget/tabText/setCurrentIndex/
    currentChanged/addTab/removeTab/insertTab/tabBar 兼容壳），
    让布局持久化、浮动面板、下拉导航这些既有代码不必分叉成两套。

默认只展开第一节（控制）：其余节折叠成一行标题，看一眼就知道有什么、
点一下展开。全部默认展开会让 19 个面板同时跑数据 tap（ADSB/APRS 轮询等），
既费电又违背「一次只关心一件事」的产品直觉；而且折叠节的 body 不参与
最小宽计算，只展开控制节时整栏才能真的落在 320 宽里（状态面板 4 列网格
的 minimumSizeHint 是 368，一展开就把右栏顶出横向滚动条）。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QRectF, QPoint, QPointF, QEvent, QSettings,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QMenu, QScrollArea, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

try:
    from tokens import tokens as _tokens
except Exception:  # pragma: no cover - 以 desktop 包方式导入时
    _tokens = None  # type: ignore

#: 节标题的固定行高（像素）。SDR++ 侧栏的分组标题就是一条 24px 的灰字行。
_SECTION_HEADER_H = 24


def _section_colors() -> dict:
    if _tokens is None:
        return {"text_primary": "#ffffff", "gray_200": "#939393",
                "card_2": "#2b2c2f", "accent": "#919cac"}
    return _tokens().COLORS


def _remembered_settings() -> QSettings:
    """W48 记忆上次展开节的 QSettings。

    必须走**显式 IniFormat 构造器**：本机 PySide6 6.12 上
    ``QSettings("MBDSDR", "Desktop")`` 无视 ``setDefaultFormat()``，恒定用
    NativeFormat（Windows 注册表）。测试里 conftest 的隔离只重定向了
    IniFormat 的 path，用两参构造器会直接写进用户真实注册表——2026-11-11
    实测在注册表 HKCU\\Software\\MBDSDR\\Desktop 里留下了
    ``right_column/expanded_section``。
    """
    return QSettings(QSettings.IniFormat, QSettings.UserScope,
                     "MBDSDR", "Desktop")


class _SectionHeader(QToolButton):
    """节标题行：左对齐标题 + 折叠箭头（展开时左侧一条强调竖线）。

    QToolButton 的文字恒居中，QSS 的 text-align 对 QToolButton 不生效；
    而 SDR++ 侧栏的分组标题是「一行靠左的灰字 + 分隔线」。之前它吃
    ``QToolButton:checked`` 的全局样式，展开态变成一块带边框的亮色大按钮
    ——整栏看起来像被一个控件占满（2026-11-11 实拍）。这里自己画：无边框、
    无圆角、只有悬停时一层极淡底色。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("rightSectionHeader")
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(_SECTION_HEADER_H)
        self.setAttribute(Qt.WA_Hover, True)

    # 悬停/按下都要重画（自定义绘制不经过 QStyle，不会自动刷新）。
    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        c = _section_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.rect()
        expanded = self.isChecked()
        if self.underMouse() or self.isDown():
            bg = QColor(c.get("card_2", "#2b2c2f"))
            bg.setAlpha(150)
            p.fillRect(rect, bg)
        if expanded:
            accent = QColor(c.get("accent", "#919cac"))
            accent.setAlpha(200)
            p.fillRect(QRectF(rect.left(), rect.top() + 5, 2.0,
                              rect.height() - 10), accent)

        f = QFont(self.font())
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        color = QColor(c.get("text_primary", "#ffffff") if expanded
                       else c.get("gray_200", "#939393"))
        if self.underMouse():
            color = QColor(c.get("text_primary", "#ffffff"))
        p.setPen(QPen(color, 1))
        fm = p.fontMetrics()
        baseline = rect.center().y() + fm.ascent() / 2.0 - 1.0
        arrow = "▾" if expanded else "▸"
        x = rect.left() + 7.0
        p.drawText(QPointF(x, baseline), arrow)
        x += fm.horizontalAdvance(arrow) + 6.0
        text = fm.elidedText(self.text(), Qt.ElideRight,
                             max(0.0, rect.right() - x - 8.0))
        p.drawText(QPointF(x, baseline), text)


class RightSection(QWidget):
    """右栏中的一节：可折叠标题 + 内容体。"""

    #: 展开状态变化。用信号而不是只靠 header.toggled：setCurrentIndex /
    #: setTabText 等程序化路径也会调用 set_expanded，而 set_expanded 会
    #: blockSignals 再 setChecked——toggled 不发，长列就收不到「内容高度变了」。
    expanded_changed = Signal(bool)

    def __init__(self, title: str, body: QWidget, parent: Optional[QWidget] = None,
                 expanded: bool = False):
        super().__init__(parent)
        self._title = str(title)
        self._body = body
        self._expanded = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = _SectionHeader(self)
        self.header.toggled.connect(self.set_expanded)
        lay.addWidget(self.header)

        self._line = QFrame(self)
        self._line.setObjectName("rightSectionLine")
        self._line.setFrameShape(QFrame.HLine)
        self._line.setFrameShadow(QFrame.Plain)
        self._line.setFixedHeight(1)
        self._line.setStyleSheet(
            "QFrame#rightSectionLine { border: none;"
            " border-top: 1px solid rgba(255,255,255,0.10); }")
        lay.addWidget(self._line)

        body.setParent(self)
        lay.addWidget(body)
        self.set_expanded(expanded)

    # ------------------------------------------------------------------
    def title(self) -> str:
        return self._title

    def body(self) -> QWidget:
        return self._body

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        changed = self._expanded != expanded
        self._expanded = expanded
        self._body.setVisible(expanded)
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        # 箭头由 _SectionHeader 自己画（▾ 展开 / ▸ 收起），文本只存标题。
        self.header.setText(self._title)
        self.header.update()
        # 只在状态真的变化时发信号：setTabText 会为了刷新标题重新调
        # set_expanded(现值)，若无条件 emit，切语言时每改一个标题都会被
        # 手风琴当成「用户展开了这一节」，把别的节全部收起。
        if changed:
            self.expanded_changed.emit(expanded)


class _TabBarShim:
    """QTabBar 兼容壳：只为 tabData / hide 这几个既有调用点存在。

    纵向长列没有真正的页签条，但布局持久化与浮动面板用 tabData 存稳定 id，
    这里把这层语义保留下来，避免主窗分叉出「页签模式/长列模式」两套代码。
    """

    def __init__(self, owner: "RightColumn"):
        self._owner = owner

    def tabData(self, index: int):
        return self._owner.tabData(index)

    def setTabData(self, index: int, data) -> None:
        self._owner.setTabData(index, data)

    # 以下在 QTabWidget 时代有意义的调用，在长列里是合法空操作。
    def hide(self) -> None:
        pass

    def show(self) -> None:
        pass

    def setUsesScrollButtons(self, _on: bool) -> None:
        pass

    def setElideMode(self, _mode) -> None:
        pass

    def setExpanding(self, _on: bool) -> None:
        pass


class RightColumn(QScrollArea):
    """可滚动的纵向面板长列（QTabWidget 最小兼容 API）。"""

    currentChanged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("rightColumnScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._container = QWidget()
        self._container.setObjectName("rightColumnBody")
        self._lay = QVBoxLayout(self._container)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._lay.addStretch(1)
        self.setWidget(self._container)

        self._sections: List[RightSection] = []
        self._titles: List[str] = []
        self._data: List[object] = []
        self._current = -1
        self._tabbar = _TabBarShim(self)
        # 手风琴（D-10 用户裁定 A）：默认单展开，同一时刻最多一节展开。
        # 「全部展开」是用户显式动作，允许短暂多开，用这个标志绕开互斥。
        self._suspend_accordion = False
        self._restored = False
        # 「全部折叠 / 全部展开」两个显式动作（右键菜单，不做隐藏快捷键）。
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------------ 注册
    def addTab(self, widget: QWidget, title: str) -> int:
        return self.insertTab(self.count(), widget, title)

    def insertTab(self, index: int, widget: QWidget, title: str) -> int:
        index = max(0, min(int(index), self.count()))
        # 只展开第一节（控制）：折叠节的 body 不参与最小宽计算，
        # 整栏才能落在 320 宽里而不出横向滚动条。
        section = RightSection(title, widget, expanded=(index == 0))
        section.expanded_changed.connect(
            lambda on, s=section: self._on_section_expanded(s, on))
        # 用户点节标题 = 切节（W47）：旧滚动位置对新内容没有意义，先归零。
        # 程序化路径（setCurrentIndex / 跳到分组 / 浮动面板归位）走的是
        # 「展开并滚到目标节」，那条路不能归零，否则目标节被甩到视口外。
        section.header.clicked.connect(
            lambda _checked=False, s=section: self._on_section_clicked(s))
        self._sections.insert(index, section)
        self._titles.insert(index, str(title))
        self._data.insert(index, None)
        self._lay.insertWidget(index, section)
        if index <= self._current:
            self._current += 1
        return index

    def _sync_container_height(self) -> None:
        """把容器最小高对齐到内容 sizeHint，并让滚动上限真的跟上。

        QScrollArea（widgetResizable）只保证容器不低于 minimumSizeHint；而
        每个面板的 minimumSizeHint 常常远小于 sizeHint（内部 QTextEdit /
        日志区带 stretch），于是一节展开时布局只按最小值分配高度，面板底部
        的控件被裁掉半行——2026-11-11 实拍 AI 面板「显示执行步骤」只剩上半截。
        容器最小高 = 布局 sizeHint 之后，面板拿到它想要的高度，滚动条负责其余。

        三个坑（2026-11-11 实测，展开末节后上限恒 414、`setValue(689)` 只能
        落到 414、末节标题停在视口 y=275）：
          * 布局 sizeHint 有缓存：body 刚显示时各节 layout 没重算，`_lay` 仍
            报旧高度（714），拿它设最小高等于没设——先把每节 layout 失效并
            激活一次，把新展开的内容算进去；
          * 容器被拉高后不会自己重排，各节仍按旧高度挤压（两条各 525 的节
            被压成 275，正文被裁）——显式 resize 到目标高后再激活一次布局；
          * QScrollArea 只在**自己**收到 LayoutRequest 时重算滚动范围，容器的
            minimumHeight 变化传不到它那里，上限就停在旧值——显式补一条事件，
            否则展开末节后末几节永远顶不到视口顶部。
        """
        for section in self._sections:
            sec_lay = section.layout()
            if sec_lay is not None:
                sec_lay.invalidate()
                sec_lay.activate()
        lay = self._lay
        lay.invalidate()
        lay.activate()
        target = lay.sizeHint().height()
        if abs(self._container.minimumHeight() - target) >= 2:
            self._container.setMinimumHeight(target)
        # 容器拉高/压矮后重排一次：只改 minimumHeight 时布局仍按旧高度分配。
        self._container.resize(self._container.width(),
                               max(target, self.viewport().height()))
        lay.invalidate()
        lay.activate()
        # QScrollArea 不监听容器的 LayoutRequest（中间隔着 viewport），显式通知。
        QApplication.sendEvent(self, QEvent(QEvent.LayoutRequest))

    def removeTab(self, index: int) -> None:
        if not (0 <= index < len(self._sections)):
            return
        section = self._sections.pop(index)
        self._titles.pop(index)
        self._data.pop(index)
        body = section.body()
        # 与 QTabWidget.removeTab 同语义：只脱管，不删控件（可能被浮动窗接管）
        body.setParent(None)
        self._lay.removeWidget(section)
        section.setParent(None)
        section.hide()
        if index == self._current:
            self._current = min(index, self.count() - 1)
        elif index < self._current:
            self._current -= 1
        QTimer.singleShot(0, self._sync_container_height)

    # ------------------------------------------------------------------ 查询
    def count(self) -> int:
        return len(self._sections)

    def widget(self, index: int) -> Optional[QWidget]:
        if 0 <= index < len(self._sections):
            return self._sections[index].body()
        return None

    def tabText(self, index: int) -> str:
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return ""

    def setTabText(self, index: int, text: str) -> None:
        if not (0 <= index < len(self._sections)):
            return
        self._titles[index] = str(text)
        self._sections[index]._title = str(text)
        self._sections[index].set_expanded(
            self._sections[index].is_expanded())

    def tabData(self, index: int):
        if 0 <= index < len(self._data):
            return self._data[index]
        return None

    def setTabData(self, index: int, data) -> None:
        if 0 <= index < len(self._data):
            self._data[index] = data

    def tabBar(self) -> _TabBarShim:
        return self._tabbar

    def setTabVisible(self, index: int, visible: bool) -> None:
        """面板菜单勾选：整节显示/隐藏（不删控件，可随时再开）。"""
        if 0 <= index < len(self._sections):
            self._sections[index].setVisible(bool(visible))
            QTimer.singleShot(0, self._sync_container_height)

    def isTabVisible(self, index: int) -> bool:
        if 0 <= index < len(self._sections):
            return not self._sections[index].isHidden()
        return False

    # ------------------------------------------------------------------ 导航
    def currentIndex(self) -> int:
        return self._current

    def scroll_to_top(self) -> None:
        """把纵向滚动位置复位到 0（W47「回到顶部」的行为入口）。"""
        self.verticalScrollBar().setValue(0)

    def _on_section_clicked(self, section: "RightSection") -> None:
        """用户点了节标题（切节）→ 滚动位置归零。

        改前实测（HEAD）：滚到第 6 节后点标题，滚动条停在原值
        （gap-right-column #4，最坏 10,149 px，目标标题直接被切在视口外）。
        这里只复位滚动位置，不动任何尺寸/颜色/分组顺序。
        """
        if section not in self._sections:
            return
        self.scroll_to_top()

    # ------------------------------------------------------- 手风琴（W48 / D-10）
    def _on_section_expanded(self, section: "RightSection", expanded: bool) -> None:
        """展开互斥：同一时刻最多一节展开（D-10 用户裁定 A）。

        用户 2026-11-11 裁定：默认单展开，展开第二节时第一节自动收起；
        「全部折叠 / 全部展开」是仅有的两个显式动作，不做 Ctrl 叠加这类
        隐藏交互；展开状态跨会话记住上次那一节。
        """
        if expanded and not self._suspend_accordion:
            for other in self._sections:
                if other is not section and other.is_expanded():
                    other.set_expanded(False)
            self._remember_expanded(section)
        # 几何要在手风琴收起其它节之后才算得准，等一帧。
        QTimer.singleShot(0, self._sync_container_height)

    def _section_key(self, index: int) -> str:
        """节的稳定身份：优先 tabData（面板 id），没有就用标题。

        不用下标：右栏增删节（面板菜单开关）后下标会漂，记住的节会错位。
        也不用译文兜底成唯一身份——tabData 由 main_window 按 attr_name 写入，
        语言切换不变。
        """
        data = self.tabData(index)
        if isinstance(data, str) and data:
            return data
        return self.tabText(index)

    def _remember_expanded(self, section: "RightSection") -> None:
        try:
            index = self._sections.index(section)
        except ValueError:
            return
        s = _remembered_settings()
        s.setValue("right_column/expanded_section", self._section_key(index))
        s.sync()

    def _restore_expanded(self) -> None:
        """跨会话恢复上次展开的那一节（用户 2026-11-11 裁定）。"""
        want = _remembered_settings().value("right_column/expanded_section")
        if not want:
            return
        want = str(want)
        for index in range(self.count()):
            if self._section_key(index) == want:
                section = self._sections[index]
                if not section.is_expanded():
                    section.set_expanded(True)
                return

    def showEvent(self, event):
        super().showEvent(event)
        # 只在首次显示时恢复：此后再 show 不应把用户当前的选择改掉。
        if not self._restored:
            self._restored = True
            if self._current < 0:
                self._restore_expanded()

    def _show_context_menu(self, pos) -> None:
        """右键：两个显式动作——全部折叠 / 全部展开（不做隐藏快捷键）。"""
        menu = QMenu(self)
        expand_act = menu.addAction("全部展开")
        collapse_act = menu.addAction("全部折叠")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is expand_act:
            self.expand_all(True)
        elif chosen is collapse_act:
            self.expand_all(False)

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._sections)):
            return
        section = self._sections[index]
        section.set_expanded(True)
        changed = index != self._current
        self._current = index
        # 展开会改变几何，等一帧再算落点（否则算出来的是折叠前的高度）。
        QTimer.singleShot(0, lambda s=section: self._reveal_section(s))
        if changed:
            self.currentChanged.emit(index)

    def _reveal_section(self, section: "RightSection", _attempt: int = 0) -> None:
        """程序化跳转的落点：把该节标题顶到视口顶部（W46 / P1-01）。

        `ensureWidgetVisible(s, 0, 8)` 只做「最小滚动」，实测两处失真
        （真 MainWindow、1366×768、视口 562）：
          * 目标节已经在视口里露出一条边时它一帧都不动 → 跳「控制」后
            标题 y=−281，被切在视口上方；
          * 目标节比视口高时它只把标题压到视口**底边** → y=529/562，
            正文全在视口外，等于跳过去只看到一条标题。
        这里直接按标题的内容坐标设滚动值（超上限贴底），跳完标题完整可见。
        滚动上限要等容器 resize 事件处理完才变大，首帧可能还没跟上，这时
        下一帧再补一次（最多补 2 次）。
        """
        self._sync_container_height()
        lay = self._container.layout()
        if lay is not None:
            lay.activate()
        bar = self.verticalScrollBar()
        y = int(section.mapTo(self._container, QPoint(0, 0)).y())
        bar.setValue(max(0, y))
        if _attempt < 2 and bar.maximum() < y:
            QTimer.singleShot(
                0, lambda s=section, n=_attempt + 1: self._reveal_section(s, n))

    # ------------------------------------------------------------------ 折叠
    def expand_all(self, expanded: bool = True) -> None:
        """两个显式动作之一：全部展开 / 全部折叠（W48）。

        这是用户主动要求的多开，不在手风琴互斥范围内——用 _suspend_accordion
        绕开 `_on_section_expanded`，否则每展开一节就把前一节收起，最终只剩
        最后一节，按钮等于没反应。全部折叠后同样同步一次容器高度，滚动上限
        随之变小。
        """
        self._suspend_accordion = True
        try:
            for section in self._sections:
                section.set_expanded(bool(expanded))
        finally:
            self._suspend_accordion = False
        self._sync_container_height()
