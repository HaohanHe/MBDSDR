# SPDX-License-Identifier: MIT
"""键盘焦点可见性与列表选中边框门禁（W55 / W56 / W61）
======================================================

W55：按钮与下拉框按 Tab 聚焦时必须有看得见的焦点环。
W56：列表选中行必须有不靠「底色浓淡」的第二线索，且本身 ≥3:1。
W61：本文件即那两条的验收——**颜色算得出来，还得真的画出来**。

判据分两层：

1. **静态**（QSS 文本）：五套主题共用一份样式表；`QPushButton` /
   `QToolButton` / `QComboBox` 都有 `:focus` 规则；焦点规则**只允许改
   `border-color`**（出现 padding/border-width/height 就是尺寸变化，
   违反 FZ-04「零位置尺寸变化」）；焦点色取 `palette(link)`（primary_text），
   五套主题对四个相邻面均 ≥3:1；列表选中行左标 3px 走同一支墨色，
   `outline` 不再被写成 `none`。

2. **渲染**（离屏抓像素）：真的把主题应用到 QApplication，给控件
   `setFocus()`，`grab()` 后取外框像素，必须等于主题的 primary_text；
   未聚焦的按钮外框必须是 border 色（证明探针确实在测边框，而不是
   碰巧全绿）。列表选中行同理：第 1–3 列是 3px 左标，行内是选中底色。

   为什么必须抓像素：`palette(link)` 写错角色、规则被后面的同名选择器
   覆盖、子控件伪状态不生效——这三种错都「QSS 文本看着对」，只有渲染
   能证伪。反证：把 `:focus` 的 `border-color` 改回 `palette(highlight)`
   → 静态与渲染同时红。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_focus_visibility.py -q
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tokens import contrast_ratio, tokens  # noqa: E402

_ALL_THEMES = sorted(tokens().THEME_COLORS)

#: 焦点环要对这些相邻面都成立（与 test_ui_contrast 的非文本口径同一组面）。
_SURFACES = ("card", "bg", "bg_alt", "button")

#: 必须自带 :focus 规则的控件：按钮、工具按钮、下拉、文本框、两个数值框。
#: 输入框那三条是 96e5ff2c 已有的，一并钉住，防止后续改动把它们丢了。
_FOCUS_REQUIRED = (
    "QPushButton", "QToolButton", "QComboBox",
    "QLineEdit", "QSpinBox", "QDoubleSpinBox",
)

#: 焦点规则允许出现的属性：只有描边颜色。出现尺寸类属性 = 违反 FZ-04。
_FOCUS_ALLOWED_PROPS = {"border-color"}


def _qss(theme: str = "default") -> str:
    """主题 QSS，**注释已剥掉**：注释里会写 `palette(highlight)` 这类
    反例说明，不剥掉会被下面的断言当成真的用了那个颜色。"""
    from themes import THEMES
    return re.sub(r"/\*.*?\*/", "", THEMES[theme]._qss(), flags=re.S)


_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules_for(qss: str, selector: str):
    """取「选择器列表中含该选择器」的全部规则体（同名规则可能有多条）。"""
    hits = []
    for selectors, body in _RULE.findall(qss):
        parts = [p.strip() for p in selectors.split(",")]
        if selector in parts:
            hits.append(body)
    return hits


def _decls(body: str):
    """把规则体拆成 {属性: 值}；注释先剥掉（注释里也有冒号）。"""
    clean = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    out = {}
    for line in clean.split(";"):
        if ":" not in line:
            continue
        prop, _, value = line.partition(":")
        out[prop.strip().lower()] = value.strip()
    return out


def _focus_rules(qss: str, widget: str):
    """该控件的焦点规则体（选择器里同时含控件名与 :focus）。"""
    hits = []
    for selectors, body in _RULE.findall(qss):
        parts = [p.strip() for p in selectors.split(",")]
        for part in parts:
            if part.startswith(widget) and ":focus" in part:
                hits.append((part, body))
    return hits


# ============================================================================
# 静态门禁
# ============================================================================

def test_five_themes_share_one_qss():
    """五套主题的 QSS 必须逐字相同：一处改动才对全部主题生效。

    这不是风格要求——A′ 之后切主题不再 ``setStyleSheet``，靠的正是
    「QSS 相同、只换 QPalette」。若某套主题单独覆写，焦点环会在一半
    主题里消失，而静态测试仍读 default 的串，看上去全绿。
    """
    sheets = {name: _qss(name) for name in _ALL_THEMES}
    first = sheets[_ALL_THEMES[0]]
    for name, sheet in sheets.items():
        assert sheet == first, (
            f"{name} 的 QSS 与 {_ALL_THEMES[0]} 不同"
            f"（{len(sheet)} vs {len(first)} 字符）")


@pytest.mark.parametrize("widget", _FOCUS_REQUIRED)
def test_required_widgets_have_focus_rule(widget):
    """W55：这些控件必须各有一条 :focus 规则（缺一条就是键盘看不见）。"""
    assert _focus_rules(_qss(), widget), (
        f"QSS 里找不到 {widget} 的 :focus 规则——键盘焦点对这个控件不可见")


@pytest.mark.parametrize("widget", _FOCUS_REQUIRED)
def test_focus_rule_only_changes_border_color(widget):
    """W55：焦点规则只许改描边颜色（FZ-04：零位置尺寸变化）。"""
    for sel, body in _focus_rules(_qss(), widget):
        decls = _decls(body)
        extra = set(decls) - _FOCUS_ALLOWED_PROPS
        assert not extra, (
            f"{sel} 除了 border-color 还改了 {sorted(extra)}；"
            f"焦点态必须零尺寸变化")
        assert decls.get("border-color") == "palette(link)", (
            f"{sel} 焦点色是 {decls.get('border-color')!r}，"
            f"不是 palette(link)（primary_text）")
        assert "highlight" not in body, (
            f"{sel} 又用回 palette(highlight)：default 主题对它只有 2.79:1")


@pytest.mark.parametrize("theme", _ALL_THEMES)
def test_focus_ink_meets_non_text_contrast(theme):
    """焦点环墨色（primary_text）对四个相邻面均 ≥3:1（WCAG 1.4.11）。"""
    c = tokens().theme_colors(theme)
    for surface in _SURFACES:
        ratio = contrast_ratio(c["primary_text"], c[surface])
        assert ratio >= 3.0, (
            f"{theme}: 焦点环 {c['primary_text']} on {surface} {c[surface]} "
            f"= {ratio:.2f} < 3.0")


def test_list_selected_bar_uses_focus_ink():
    """W56：选中行 3px 左标走 palette(link)，宽度与 padding 保持原值。"""
    bodies = _rules_for(_qss(), "QListView::item:selected")
    assert bodies, "QListView::item:selected 规则不见了"
    body = bodies[-1]
    decls = _decls(body)
    assert decls.get("border-left") == "3px solid palette(link)", (
        f"选中行左标不是 3px palette(link)：{decls.get('border-left')!r}")
    assert "highlight" not in body, "选中行左标又用回 highlight（default 2.79:1）"
    # 零尺寸变化：内边距与边框宽度都是原值
    item = _decls(_rules_for(_qss(), "QListView::item")[-1])
    assert item.get("padding") == "6px 8px", (
        f"列表项 padding 被改成 {item.get('padding')!r}，行高会变")
    assert decls.get("border") is None and decls.get("border-width") is None, (
        "选中行加了整圈边框：行高 +2px，违反零尺寸变化")


@pytest.mark.parametrize("theme", _ALL_THEMES)
def test_selected_bar_readable_on_both_sides(theme):
    """W56：选中左标要同时对「选中底」和「未选中底」成立（≥3:1）。

    选中底本身对未选中底只有 1.03–2.16:1，形状线索不能靠它，所以左标
    必须自己达标——这正是本用例的存在理由。
    """
    c = tokens().theme_colors(theme)
    for surface in ("button_pressed", "card"):
        ratio = contrast_ratio(c["primary_text"], c[surface])
        assert ratio >= 3.0, (
            f"{theme}: 选中左标 {c['primary_text']} on {surface} "
            f"{c[surface]} = {ratio:.2f} < 3.0")


def test_list_outline_is_not_disabled():
    """W56：列表自身的键盘聚焦框不许再被 ``outline: none`` 关掉。"""
    bodies = _rules_for(_qss(), "QListView")
    assert bodies, "QListView 规则不见了"
    outline = _decls(bodies[-1]).get("outline")
    assert outline is not None, (
        "QListView 规则里 outline 又没了：写上 1px palette(link)，"
        "别把列表的聚焦框交回给系统默认")
    assert outline.strip().lower() != "none", (
        "outline 又被写回 none：Tab 进入列表时没有任何焦点提示")
    assert "palette(link)" in outline, (
        f"outline 没走 palette(link)：{outline!r}（highlight 在 default 2.79:1）")


# ============================================================================
# 渲染门禁（真把主题应用上去、真抓像素）
# ============================================================================

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    saved = (app.styleSheet(), app.palette(), app.font())
    yield app
    # 本模块会把 default 主题应用到全局，退出时还原，不污染别的测试。
    app.setStyleSheet(saved[0])
    app.setPalette(saved[1])
    app.setFont(saved[2])


@pytest.fixture(scope="module")
def applied(qapp):
    from themes import THEMES
    THEMES["default"].apply(qapp)
    return qapp


def _row(win, *widgets):
    box = QWidget()
    lay = QVBoxLayout(box)
    for w in widgets:
        lay.addWidget(w)
    win.addWidget(box)
    return box


def _edge_pixels(widget, app, count: int = 2):
    """取控件左外框的像素（垂直居中处），返回小写 hex 列表。"""
    widget.setFocus()
    app.processEvents()
    assert widget.hasFocus(), f"{type(widget).__name__} 拿不到焦点（FocusPolicy？）"
    img = widget.grab().toImage()
    y = img.height() // 2
    return [img.pixelColor(x, y).name().lower() for x in range(count)]


def test_focus_ring_is_actually_painted(applied):
    """W55：聚焦的按钮/工具按钮/下拉框，外框像素 == primary_text。"""
    app = applied
    c = tokens().theme_colors("default")
    win = QWidget()
    lay = QVBoxLayout(win)
    button = QPushButton("按钮")
    tool = QToolButton()
    tool.setText("工具")
    combo = QComboBox()
    combo.addItems(["甲", "乙"])
    _row(lay, button, tool, combo)
    win.resize(320, 240)
    win.show()
    app.processEvents()
    try:
        for widget, name in ((button, "QPushButton"), (tool, "QToolButton"),
                             (combo, "QComboBox")):
            # 未聚焦时的外框：按钮/下拉是 border 色，工具按钮的描边本来就
            # 是 transparent（背景透明，抓出来是黑）。两种都要求「不等于
            # 焦点色」——这一步只证明探针确实落在边框上，不是在量底色。
            widget.clearFocus()
            app.processEvents()
            idle = widget.grab().toImage()
            idle_edge = idle.pixelColor(0, idle.height() // 2).name().lower()
            if name != "QToolButton":
                assert idle_edge == c["border"].lower(), (
                    f"{name} 未聚焦外框 {idle_edge} != border {c['border']}，"
                    f"探针没落在边框上")
            assert idle_edge != c["primary_text"].lower(), (
                f"{name} 没聚焦时外框就已经是焦点色——探针或规则有问题")

            edge = _edge_pixels(widget, app)
            assert c["primary_text"].lower() in edge, (
                f"{name} 聚焦后外框像素 {edge}，期望含 primary_text "
                f"{c['primary_text']}——焦点环没画出来")
    finally:
        win.close()


def test_tab_key_reaches_button(applied):
    """W61 键盘可达：Tab 能从输入框走到按钮，焦点不是只有鼠标能给。"""
    app = applied
    win = QWidget()
    lay = QVBoxLayout(win)
    edit = QLineEdit()
    button = QPushButton("下一步")
    _row(lay, edit, button)
    win.resize(240, 120)
    win.show()
    edit.setFocus()
    app.processEvents()
    assert edit.hasFocus(), "初始焦点没落到输入框"
    try:
        QTest.keyClick(edit, Qt.Key_Tab)
        app.processEvents()
        assert button.hasFocus(), (
            f"按 Tab 后焦点仍在 {type(app.focusWidget()).__name__}，没到按钮")
    finally:
        win.close()


def test_selected_row_bar_painted_and_geometry_unchanged(applied):
    """W56：选中行真的画出 3px 左标，且行高与未选中时逐像素相同。"""
    app = applied
    c = tokens().theme_colors("default")
    win = QWidget()
    lay = QVBoxLayout(win)
    lst = QListWidget()
    lst.addItems(["第一行", "第二行", "第三行"])
    _row(lay, lst)
    win.resize(280, 200)
    win.show()
    app.processEvents()
    try:
        lst.clearSelection()
        app.processEvents()
        before = lst.visualItemRect(lst.item(0)).height()

        lst.setCurrentRow(0)
        app.processEvents()
        after = lst.visualItemRect(lst.item(0)).height()
        assert before == after, (
            f"选中让行高从 {before} 变成 {after}：违反零尺寸变化")

        img = lst.grab().toImage()
        rect = lst.visualItemRect(lst.item(0))
        y = rect.center().y()
        bar = [img.pixelColor(x, y).name().lower() for x in range(1, 4)]
        assert bar == [c["primary_text"].lower()] * 3, (
            f"选中行 1–3 列是 {bar}，期望 3 个 primary_text "
            f"{c['primary_text']}（3px 左标没画出来或颜色不对）")
        fill = img.pixelColor(rect.left() + 6, y).name().lower()
        assert fill == c["button_pressed"].lower(), (
            f"选中行底色 {fill} != button_pressed {c['button_pressed']}")
    finally:
        win.close()


def _count_ink(widget, app, hex_color: str) -> int:
    """控件抓图里有多少像素等于指定颜色（用来量「边框画了多长」）。"""
    widget.repaint()
    app.processEvents()
    img = widget.grab().toImage()
    want = hex_color.lstrip("#").lower()
    return sum(
        1
        for y in range(img.height())
        for x in range(img.width())
        if img.pixelColor(x, y).name().lstrip("#").lower() == want
    )


def test_focused_row_outline_is_painted(applied):
    """W56：列表拿到键盘焦点时，当前行外围画出一圈 primary_text 边框。

    `outline` 是 Qt 画 item view 聚焦框的唯一入口：焦点在列表上时它会沿
    当前行画一圈，焦点离开就消失。这里用像素数判定——只靠左标时约 3px×
    行高（几十像素），带整圈后是行周长（数百像素），两者量级差一个数量级。
    同时钉住 item 矩形在「选中前/选中后」「聚焦/失焦」四种状态完全一致，
    证明这圈框是画上去的，没有挤动布局。
    """
    app = applied
    c = tokens().theme_colors("default")
    win = QWidget()
    lay = QVBoxLayout(win)
    lst = QListWidget()
    lst.addItems(["第一行", "第二行", "第三行"])
    lay.addWidget(lst)
    win.resize(280, 200)
    win.show()
    app.processEvents()
    try:
        lst.clearSelection()
        lst.setCurrentRow(-1)
        app.processEvents()
        blank_rect = lst.visualItemRect(lst.item(0))

        lst.setCurrentRow(0)
        lst.setFocus()
        app.processEvents()
        focused_rect = lst.visualItemRect(lst.item(0))
        focused = _count_ink(lst, app, c["primary_text"])

        lst.clearFocus()
        app.processEvents()
        blurred_rect = lst.visualItemRect(lst.item(0))
        blurred = _count_ink(lst, app, c["primary_text"])

        assert focused_rect == blank_rect == blurred_rect, (
            f"聚焦/选中改变了 item 矩形：{blank_rect} → {focused_rect} "
            f"→ {blurred_rect}（违反零尺寸变化）")
        assert blurred >= 40, (
            f"失焦时连 3px 左标都没有（{blurred} 像素）")
        assert focused >= blurred * 3, (
            f"聚焦后焦点框没画出来：聚焦 {focused} 像素 vs 只有左标 "
            f"{blurred} 像素")
    finally:
        win.close()
