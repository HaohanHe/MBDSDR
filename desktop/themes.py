# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端主题系统
=======================
三套主题：默认低饱和浅色 (default)、深色 (dark)、高对比 (high_contrast)。
设计原则：不喧宾夺主、稳定可读优先、低饱和扁平化、禁用 emoji。
字体优先 MiSans，回退 Noto Sans CJK / 系统无衬线。
"""

from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import QApplication

import os
import tempfile

# 设计令牌单一来源：主题色只从 tokens.THEME_COLORS 取，不在本文件写 hex。
from tokens import tokens


# ============================================================================
# QSS 位图图标（定色，与主题无关）
# ============================================================================
# 为什么图标颜色**不能**跟主题走（2026-11-11 实测）：
#   A′ 要求 QSS 字符串在五套主题之间逐字相同（否则每次切主题都要重新
#   setStyleSheet，回到 3.3 s 的老路）。而 `image: url(...)` 的路径是 QSS
#   文本的一部分，一旦把主题色烤进文件名，QSS 就不再相同。
#   反过来「路径不变、原地改写 PNG」也不行：实测改写同一路径后即使
#   `QPixmapCache.clear()` + 全树重抛光，Qt 仍用旧像素（QStyleSheetStyle
#   把图片缓存在渲染规则里，规则按控件签名缓存，不按文件内容失效）。
# 因此这三类图标改为**定色**，颜色按「对五套主题底色的对比度」挑选：
#   * 勾：      #1A1A1A 画在 primary 填充上，对比度 5.64 / 5.95 / 8.20 /
#               6.26 / 12.33（default / dark / high_contrast / dark_car / figma）
#   * 勾(禁用)：#8A8A8A 画在 button_disabled 上，2.30 / 4.37 / 5.04 / 4.77 / 4.77
#               （浅色主题偏低，但仍高于改造前的 #A0A0A0 on #D8D2C8 ≈ 1.7）
#   * 箭头：    #808080 画在 card 上，3.95 / 3.72 / 5.32 / 3.54 / 4.17
#               （非文本图形 3:1 达标）
# 单选按钮的圆点**取消图片**：圆点画在 15px 框里、背景是 button 色，
# 任何定色都会在浅色或深色主题之一里看不清（#808080 对 button 最低 3.02）。
# 选中态改成 primary 实心圆（border-radius），颜色由 palette(highlight) 给，
# 天然跟主题走。
_CHECK_INK = "#1A1A1A"
_CHECK_INK_OFF = "#8A8A8A"
_ARROW_INK = "#808080"

#: 图标几何版本。改形状必须 +1，否则临时目录里的旧图会被复用。
_ASSET_VERSION = 2


def _asset_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "mbdsdr_ui_assets")
    os.makedirs(d, exist_ok=True)
    return d


def _spin_arrow_url(up: bool) -> str:
    """生成（并复用）一枚定色三角箭头 PNG，返回 QSS 用的路径。

    为什么非要落成文件：Qt 的 `::up-arrow` / `::down-arrow` 只认 `image`。
    用 border 拼三角形的写法在 6.x 上只会画出一块**实心矩形**（三种写法
    实测都是白方块，见 2026-11-11 探针），而系统样式在深色主题下画的是
    **深色位图**——右栏「下行频率」右侧因此是一条完全看不见箭头的空条。
    """
    tag = "up" if up else "down"
    path = os.path.join(_asset_dir(),
                        f"spin{_ASSET_VERSION}_{tag}_{_ARROW_INK.lstrip('#')}.png")
    if os.path.exists(path):
        return path.replace("\\", "/")
    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import QImage, QPainter, QPolygonF

        # 2 倍图：QSS 里按 9×6 逻辑像素摆放，高分屏缩小采样仍然锐利。
        img = QImage(22, 14, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_ARROW_INK))
        if up:
            pts = [QPointF(1.0, 12.0), QPointF(11.0, 2.0), QPointF(21.0, 12.0)]
        else:
            pts = [QPointF(1.0, 2.0), QPointF(11.0, 12.0), QPointF(21.0, 2.0)]
        p.drawPolygon(QPolygonF(pts))
        p.end()
        img.save(path, "PNG")
    except Exception:
        # 生成失败不阻断主题应用：只是没有箭头，输入框本身仍可用。
        return ""
    return path.replace("\\", "/")


def _check_mark_url(disabled: bool = False) -> str:
    """生成一枚「对勾」PNG，给 ``QCheckBox::indicator:checked`` 当 image。

    为什么非要落成文件：Qt 的 ``::indicator`` 只认 ``image``（与 ``::up-arrow``
    同一条规则）。CSS 式那块纯色填充在勾选后只显示成一个亮方块，既没有勾，
    也与"不确定态"长得一样——2026-11-11 放大实拍，用户只能靠亮/暗猜。
    """
    ink = _CHECK_INK_OFF if disabled else _CHECK_INK
    path = os.path.join(
        _asset_dir(),
        f"check{_ASSET_VERSION}_{'off' if disabled else 'on'}_"
        f"{ink.lstrip('#')}.png")
    if os.path.exists(path):
        return path.replace("\\", "/")
    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import QImage, QPainter, QPen, QPolygonF

        # 2 倍图：QSS 把它缩到 15px 指示符里，高分屏仍有足够采样点。
        img = QImage(26, 26, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(ink), 3.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(5.0, 13.5), QPointF(10.5, 19.0),
                                  QPointF(21.0, 7.5)]))
        p.end()
        img.save(path, "PNG")
    except Exception:
        return ""
    return path.replace("\\", "/")


# ============================================================================
# 字体
# ============================================================================

def get_app_font() -> QFont:
    """获取应用字体：优先 MiSans，回退 Noto Sans CJK，最后系统无衬线。"""
    font = QFont()
    # 优先列表（Qt 会按顺序找第一个可用的）
    font.setFamilies([
        "MiSans",
        "MiSans Normal",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "PingFang SC",
        "Microsoft YaHei",
        "Helvetica Neue",
        "Arial",
        "sans-serif",
    ])
    font.setPointSize(10)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


# ============================================================================
# 主题定义
# ============================================================================

def _control_coverage_qss() -> str:
    """所有主题共用的「原生控件接管」样式（与主题无关）。

    2026-11-09 审计（主窗实测控件普查）：``QToolButton`` **138 个**、
    ``QCheckBox`` 28 个、``QListView`` 42 个、``QSplitterHandle`` 11 个
    从未被任何主题的 QSS 选中过——它们在深色主题下仍是系统自带的白底方角，
    是「设计语言不统一」最直接的来源：同一屏里既有按 token 画的卡片，
    也有 Qt 默认皮肤画的按钮。

    这里不改任何布局与行为，只按**同一个形状语法**接管它们：
    8px 圆角、1px 描边、hover 提亮 + 强调色描边、选中用强调色左标。

    颜色一律写成 QSS 的 ``palette(<role>)``，由 ``Theme.apply`` 装进
    QPalette——本函数因此**不接受任何颜色参数**，五套主题共用一份逐字相同的
    字符串（切主题只换 QPalette + 重抛光，见 ``Theme.apply``）。
    """
    arrow_up = _spin_arrow_url(True)
    arrow_down = _spin_arrow_url(False)
    check_mark = _check_mark_url(False)
    check_mark_off = _check_mark_url(True)
    return f"""
        /* ---------- 原生控件接管（菜单栏/工具栏/复选/列表/分隔条/分组框） ---------- */
        /* QLabel 必须显式透明：tokens 的 QSS 给 `QMainWindow, QWidget` 统一压了
           底色（#000），而 QLabel 是 QWidget 的子类、会一起吃到这条规则。结果
           卡片里每一个裸 QLabel（「步进:」「RSSI:」「设备名:」…）都变成一块
           纯黑小方块——2026-11-11 实拍右栏状态面板，整页像一排黑洞。id 选择器
           （#freqDisplay 等）优先级更高，自带底色的标签不受影响。 */
        QLabel {{
            background: transparent;
        }}
        /* QMenuBar：default 有这条规则，dark_car/figma 走的
           tokens.to_stylesheet() 里没有——菜单栏高度实测 31px vs 33px，
           中央区因此少 2px，所有面板跟着漂。补齐后两套主题同高。 */
        QMenuBar {{
            background-color: palette(alternate-base);
            color: palette(window-text);
            border-bottom: 1px solid palette(mid);
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 6px 12px;
            color: palette(window-text);
        }}
        QMenuBar::item:selected {{
            background-color: palette(light);
            border-radius: 4px;
        }}
        QMenuBar::item:pressed {{
            background-color: palette(dark);
            border-radius: 4px;
        }}
        /* QGroupBox：dark_car 与 figma 走的 tokens.to_stylesheet() 里**没有**
           这条规则，分组框一直吃 Qt 默认的 3D 边与标题间距——既和卡片不是
           一套形状，也把面板撑高了 9px（2026-11-09 基线实测
           adsb 449→458）。这里按 default 主题已有的数值统一，几何随之一致。 */
        QGroupBox {{
            background-color: palette(base);
            border: 1px solid palette(mid);
            border-radius: 12px;
            /* 20px 而不是 16px：标题行高（9pt 中文 ≈ 18px）比 16px 的 margin
               高 2px，标题框会压到卡片顶边上——Qt 用父窗口底色擦掉标题占的
               那块，于是卡片顶边在标题下面被啃出一个黑色缺口，字底也贴着
               边框（2026-11-11 实拍「连接」「信号质量」）。 */
            margin-top: 20px;
            padding: 12px 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            color: palette(window-text);
        }}
        /* 断连卡片整组 setEnabled(False)：标题跟着变灰。
           必须写成全局选择器——若改成给单张卡片 setStyleSheet，Qt 会切到
           "控件自带样式表"的绘制路径，标题从边框上方挪进边框内，卡片顶边
           被擦出黑色缺口（2026-11-11 实拍右栏状态面板）。
           选属性选择器而不是伪状态：实测 `QGroupBox:disabled::title` 在
           QGroupBox 的子控件上不生效（标题仍是纯白），`[enabled="false"]`
           生效。 */
        QGroupBox[enabled="false"]::title {{
            color: palette(link-visited);
        }}
        /* 可折叠分组框：标题里已经用 ▼/▶ 表达展开态，Qt 自带的可选中勾选框
           会把它重复一次（2026-11-11 实拍卫星跟踪面板「☑ ▼ 目标卫星 · 2 项」）。
           只关掉带 collapseBox 属性的分组框，不碰别处可能真需要勾选框的组。 */
        QGroupBox[collapseBox="true"]::indicator,
        QGroupBox[collapseBox="true"]::indicator:checked,
        QGroupBox[collapseBox="true"]::indicator:unchecked {{
            width: 0px;
            height: 0px;
            margin: 0px;
            image: none;
            border: none;
            background: transparent;
        }}
        /* 数值微调框的上下箭头：dark_car/figma 的 tokens QSS 里没有这条，
           箭头由系统样式用**深色位图**画在深色按钮上——实测右栏「下行频率」
           右侧是一块看不见箭头的空条。这里显式接管按钮与箭头，颜色取主题
           次级文字色，保证任何主题下都读得出来。 */
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            width: 14px;
            background: transparent;
            border: none;
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-position: top right;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-position: bottom right;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover,
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
            background-color: palette(light);
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image: url("{arrow_up}");
            width: 9px;
            height: 6px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image: url("{arrow_down}");
            width: 9px;
            height: 6px;
        }}
        QToolButton {{
            background-color: transparent;
            color: palette(window-text);
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 4px 8px;
        }}
        QToolButton:hover {{
            background-color: palette(light);
            border-color: palette(highlight);
        }}
        QToolButton:pressed {{
            background-color: palette(dark);
        }}
        QToolButton:checked {{
            background-color: palette(dark);
            border-color: palette(highlight);
        }}
        QToolButton:disabled {{
            color: palette(link-visited);
            background-color: transparent;
        }}
        QToolButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QCheckBox, QRadioButton {{
            background-color: transparent;
            color: palette(window-text);
            spacing: 8px;
        }}
        QCheckBox:disabled, QRadioButton:disabled {{
            color: palette(link-visited);
        }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid palette(mid);
            border-radius: 4px;
            background-color: palette(button);
        }}
        QCheckBox::indicator:hover {{
            border-color: palette(highlight);
        }}
        QCheckBox::indicator:checked {{
            background-color: palette(highlight);
            border-color: palette(highlight);
            image: url("{check_mark}");
        }}
        QCheckBox::indicator:checked:disabled {{
            background-color: palette(accent);
            border-color: palette(midlight);
            image: url("{check_mark_off}");
        }}
        QCheckBox::indicator:disabled {{
            border-color: palette(midlight);
            background-color: palette(accent);
        }}
        QRadioButton::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid palette(mid);
            border-radius: 8px;
            background-color: palette(button);
        }}
        QRadioButton::indicator:hover {{
            border-color: palette(highlight);
        }}
        /* 选中态改成 primary 实心圆（不再用「描边 + 圆心」的位图）：
           圆点必须与 button 底色形成对比，而 button 底在浅色主题是白、深色
           主题是 #2c–#35 灰，没有任何一个定色能在两边都看清（实测最低
           3.02:1）；位图颜色又不能随主题变（见文件顶部的图标说明）。
           实心圆的颜色由 palette(highlight) 供给，天然跟主题走。 */
        QRadioButton::indicator:checked {{
            border: 1px solid palette(highlight);
            background-color: palette(highlight);
        }}
        QRadioButton::indicator:disabled {{
            border-color: palette(midlight);
            background-color: palette(accent);
        }}
        QListView, QListWidget {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            outline: none;
            alternate-background-color: palette(window);
        }}
        QListView::item, QListWidget::item {{
            padding: 6px 8px;
            border-radius: 6px;
            color: palette(window-text);
        }}
        QListView::item:hover, QListWidget::item:hover {{
            background-color: palette(light);
        }}
        QListView::item:selected, QListWidget::item:selected {{
            background-color: palette(dark);
            color: palette(window-text);
            border-left: 3px solid palette(highlight);
        }}
        QListView::item:disabled, QListWidget::item:disabled {{
            color: palette(placeholder-text);
        }}
        QSplitter::handle {{
            background-color: palette(midlight);
        }}
        QSplitter::handle:hover {{
            background-color: palette(highlight);
        }}
        QSplitter::handle:horizontal {{
            width: 4px;
        }}
        QSplitter::handle:vertical {{
            height: 4px;
        }}
        QSizeGrip {{
            background-color: transparent;
            width: 12px;
            height: 12px;
        }}
        """


def _srgb_channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """sRGB 相对亮度（WCAG 2.1 定义）。"""
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """两色对比度，1.0–21.0。设计约束住在主题层，脚本与测试都从这里取。"""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _on_primary(c: dict) -> str:
    """强调色底上该用什么颜色的文字（保证 >= 4.5:1）。

    原来一律取 ``c["card"]``——浅色主题下 ``card`` 是纯白，白字压在原强调色
    ``#C4845C`` 上只有 **3.09**（2026-11-09 审计实测），列表选中一行几乎读不出
    字。这里按实际对比度挑第一个达标者，深浅主题共用一条规则。
    """
    primary = c.get("primary", c["text"])
    for cand in ("#FFFFFF", c.get("card", "#FFFFFF"),
                 "#1A1A1A", "#000000"):
        if contrast_ratio(cand, primary) >= 4.5:
            return cand
    return "#000000"


#: Qt 6.6 才有的 Accent 角色；老版本退回 Shadow，只影响 button_disabled 的
#: 一个通道（QSS 仍能解析，只是这个颜色在旧版本上与 danger 共用）。
_ACCENT_ROLE = getattr(QPalette, "Accent", QPalette.Shadow)

#: 主题令牌 → QPalette 角色。**QSS 只写 palette(<role>)，所以这张表就是
#: 主题色进入界面的唯一通路**——改这里等于改 QSS 的颜色。
#:
#: 17 个令牌里有 13 个能对上 Qt 的同义角色（bg→Window、card→Base…）；
#: text_disabled / button_disabled / danger 三个 Qt 没有语义角色，借用
#: 参与绘制最少的三个角色纯当颜色通道：
#:   text_disabled   → LinkVisited（只有「已访问过的超链接」用它）
#:   button_disabled → Accent（Fusion 不读它；QSS 已接管全部焦点边框）
#:   danger          → Shadow（Fusion 的阴影绘制几乎全被 QSS 接管）
#: 这比「把颜色字面量留在 QSS 里」唯一多出的风险是：若将来某个原生控件
#: 直接读这三个角色，颜色会是灰/灰/红。可用 5 个主题的 36 点像素取样复验。
_THEME_ROLE_MAP = (
    ("bg", QPalette.Window),
    ("bg_alt", QPalette.AlternateBase),
    ("card", QPalette.Base),
    ("text", QPalette.WindowText),
    ("text_secondary", QPalette.PlaceholderText),
    ("text_disabled", QPalette.LinkVisited),
    ("border", QPalette.Mid),
    ("grid", QPalette.Midlight),
    ("primary", QPalette.Highlight),
    ("primary_text", QPalette.Link),
    ("accent", QPalette.BrightText),
    ("danger", QPalette.Shadow),
    ("button", QPalette.Button),
    ("button_hover", QPalette.Light),
    ("button_pressed", QPalette.Dark),
    ("button_disabled", _ACCENT_ROLE),
)


def _build_palette(c: dict) -> QPalette:
    """把一套主题的 colors 装成 QPalette（QSS 与原生控件的共同颜色来源）。"""
    palette = QPalette()
    for token, role in _THEME_ROLE_MAP:
        palette.setColor(role, QColor(c[token]))
    # 原生路径（QSS 覆盖不到的部分）读的角色：正文/输入/按钮文字都用 text。
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ToolTipBase, QColor(c["card"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["text"]))
    # 选中行上的文字不能一律用 card：浅色主题 card 是纯白，白字压在原
    # 强调色上只有 3.09:1（选中一行读不出来）。按实际对比度挑达标的墨色。
    palette.setColor(QPalette.HighlightedText, QColor(_on_primary(c)))
    # 禁用组（QSS 的 palette(role) 只读 Active 组，所以禁用色另有角色通道；
    # 这里仍设一份，供 QSS 覆盖不到的原生控件使用）。
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(c["text_disabled"]))
    return palette


def _repolish_all(app: QApplication) -> None:
    """遍历全部控件 unpolish/polish，让 QSS 里的 palette(role) 重新取值。

    用 ``app.allWidgets()`` 而不是「顶层窗口 + findChildren」：隐藏页、尚未
    show 的控件也必须刷新，否则切回来时才第一次取值——那正是「切主题时看着
    不卡、切回来才发现没变色」的来源。
    """
    for w in app.allWidgets():
        style = w.style()
        if style is None:
            continue
        style.unpolish(w)
        style.polish(w)
        w.update()


class Theme:
    """主题数据类。"""

    def __init__(self, name: str, display_name: str, colors: dict, spectrum_colors: list):
        self.name = name
        self.display_name = display_name
        self.colors = colors
        self.spectrum_colors = spectrum_colors  # 频谱渐变颜色列表 (hex)

    def apply(self, app: QApplication):
        """应用主题到 QApplication（幂等：状态相同则跳过重抛光）。

        2026-11-11 改造（A′）：QSS 里不再写任何颜色字面量，颜色一律写成
        ``palette(<role>)``。于是五套主题的样式表**逐字相同**——
        切换主题 = 换 QPalette + 全树重抛光，不再 ``setStyleSheet``。
        同一主窗实测（1582 个控件，每次切换含重绘）：改前 3.3–4.6 s
        （重新解析 + 重抛光；同表 setStyleSheet 复测 5.6–6.6 s），
        改后 0.54–0.70 s（机器有并行负载时 0.7–1.1 s），其中 setPalette
        只占 44–73 ms，其余是 unpolish/polish。
        数据见 docs/perf-evidence/2026-11-11/theme_switch_after.json。

        为什么必须手动重抛光：实测 ``app.setPalette`` 不会让已经固化下来的
        QSS 颜色重新解析（palette(role) 只在解析样式规则时取值），
        必须 unpolish/polish 才会重新走一遍规则解析。反证见
        docs/perf-evidence/2026-11-11/theme_palette_feasibility.json。

        幂等性保留的原因见下方历史注释：每个新 MainWindow 构造都会应用一次
        主题，N 个窗口累计 O(N²)，20+ 个窗口后单次构造从 1.7 s 恶化到 30 s+，
        pytest 套件直接在 UI 线程挂死。状态完全相同时跳过重抛光是安全的。
        """
        palette = _build_palette(self.colors)
        font = get_app_font()
        qss = self._qss()
        try:
            if (app.palette() == palette and app.font() == font
                    and app.styleSheet() == qss):
                return                  # 已应用过同一状态：跳过全量重抛光
            qss_same = app.styleSheet() == qss
        except Exception:
            qss_same = False            # 比较失败（异常平台）则照常应用

        app.setPalette(palette)
        app.setFont(font)
        if not qss_same:
            # 首次应用 / 样式表本身变了：setStyleSheet 自带全树刷新
            app.setStyleSheet(qss)
            return
        _repolish_all(app)

    def _qss(self) -> str:
        """本主题的最终样式表（五套主题逐字相同：颜色全走 palette(role)）。"""
        return self._build_qss() + _control_coverage_qss()

    def _build_qss(self) -> str:
        """构建 QSS 样式表（不含颜色字面量，颜色由 QPalette 供给）。"""
        return f"""
        QWidget {{
            background-color: palette(window);
            color: palette(window-text);
            font-family: "MiSans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        }}
        QFrame#card {{
            background-color: palette(base);
            border: 1px solid palette(mid);
            border-radius: 12px;
        }}
        QLabel#sectionTitle {{
            font-size: 11pt;
            font-weight: 600;
            color: palette(window-text);
            padding: 4px 0px;
        }}
        QLabel#statusValue {{
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 11pt;
            color: palette(window-text);
        }}
        QLabel#freqDisplay {{
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 28pt;
            font-weight: 700;
            color: palette(window-text);
            background-color: palette(base);
            border: 1px solid palette(mid);
            border-radius: 12px;
            padding: 8px 16px;
        }}
        QPushButton {{
            background-color: palette(button);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 10pt;
        }}
        QPushButton:hover {{
            background-color: palette(light);
            border-color: palette(highlight);
        }}
        QPushButton:pressed {{
            background-color: palette(dark);
            color: palette(window-text);
        }}
        QPushButton:disabled {{
            color: palette(link-visited);
            background-color: palette(accent);
            border-color: palette(midlight);
        }}
        QPushButton#recordButton {{
            background-color: palette(bright-text);
            color: palette(base);
            border: 1px solid palette(bright-text);
            font-weight: 600;
        }}
        QPushButton#recordButton:hover {{
            background-color: palette(bright-text);
        }}
        QPushButton#recordButton:checked {{
            background-color: palette(shadow);
            border-color: palette(shadow);
        }}
        QPushButton#recordButton:disabled {{
            background-color: palette(accent);
            color: palette(link-visited);
            border-color: palette(midlight);
            font-weight: 400;
        }}
        QComboBox {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 10pt;
        }}
        QComboBox:hover {{
            border-color: palette(highlight);
        }}
        QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled,
        QDoubleSpinBox:disabled {{
            color: palette(link-visited);
            background-color: palette(accent);
            border-color: palette(midlight);
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            selection-background-color: palette(highlight);
            selection-color: palette(base);
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            background: palette(midlight);
            border-radius: 2px;
        }}
        /* 手柄左侧填 primary：没有填充时滑杆只有一个小圆点，看不出"当前值
           占了几分"（ANR 强度、音量都靠这个读数）。 */
        QSlider::sub-page:horizontal {{
            background: palette(highlight);
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -5px 0;
            background: palette(highlight);
            border: none;
            border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{
            background: palette(bright-text);
        }}
        /* 竖滑杆（频谱右侧缩放/dB 量程）：只给轨道和手柄，不做填充——三根里
           两根是 setInvertedAppearance(True)，sub/add-page 的上下方向会跟着
           翻转，填充方向写错就是"越推越少"的错误读数。 */
        QSlider::groove:vertical {{
            width: 4px;
            background: palette(midlight);
            border-radius: 2px;
        }}
        QSlider::handle:vertical {{
            width: 14px;
            height: 14px;
            margin: 0 -5px;
            background: palette(highlight);
            border: none;
            border-radius: 7px;
        }}
        QSlider::handle:vertical:hover {{
            background: palette(bright-text);
        }}
        /* 禁用态必须变灰：QSS 只要写了 groove/handle，Qt 就不会再按"禁用"自动
           调暗（实测 ANR 面板无硬件时滑杆看起来完全可拖）。 */
        QSlider::sub-page:horizontal:disabled {{
            background: palette(midlight);
        }}
        QSlider::handle:horizontal:disabled,
        QSlider::handle:vertical:disabled {{
            background: palette(mid);
        }}
        QLineEdit {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            padding: 7px 12px;
            font-size: 10pt;
        }}
        QLineEdit:focus {{
            border-color: palette(highlight);
        }}
        QTextEdit, QPlainTextEdit {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            padding: 6px;
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 9pt;
        }}
        QTabWidget::pane {{
            border: 1px solid palette(mid);
            border-radius: 10px;
            background-color: palette(base);
        }}
        QTabBar::tab {{
            background-color: palette(window);
            color: palette(placeholder-text);
            border: 1px solid palette(mid);
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 6px 14px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: palette(base);
            color: palette(link);
            font-weight: 600;
        }}
        QTabBar::tab:hover {{
            color: palette(window-text);
        }}
        QStatusBar {{
            background-color: palette(alternate-base);
            color: palette(placeholder-text);
            border-top: 1px solid palette(mid);
        }}
        QToolBar {{
            background-color: palette(alternate-base);
            border-bottom: 1px solid palette(mid);
            spacing: 4px;
            padding: 4px;
        }}
        QScrollBar:vertical {{
            background: palette(window);
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: palette(mid);
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: palette(highlight);
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QGroupBox {{
            background-color: palette(base);
            border: 1px solid palette(mid);
            border-radius: 12px;
            margin-top: 16px;
            padding: 12px 10px 10px 10px;
            font-size: 11pt;
            font-weight: 600;
            color: palette(window-text);
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: palette(window-text);
        }}
        QSpinBox, QDoubleSpinBox {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            padding: 5px 10px;
            font-size: 10pt;
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: palette(highlight);
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 16px;
            background: palette(alternate-base);
            border: none;
        }}
        QProgressBar {{
            background-color: palette(midlight);
            border: 1px solid palette(mid);
            border-radius: 8px;
            text-align: center;
            color: palette(window-text);
            height: 14px;
        }}
        QProgressBar::chunk {{
            background-color: palette(highlight);
            border-radius: 6px;
        }}
        QMenuBar {{
            background-color: palette(alternate-base);
            color: palette(window-text);
            border-bottom: 1px solid palette(mid);
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 6px 12px;
            color: palette(window-text);
        }}
        QMenuBar::item:selected {{
            background-color: palette(light);
            border-radius: 4px;
        }}
        QMenu {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
        }}
        QMenu::item {{
            padding: 6px 24px;
        }}
        QMenu::item:selected {{
            background-color: palette(light);
            color: palette(window-text);
        }}
        QTableWidget, QTableView, QTreeWidget, QTreeView {{
            background-color: palette(base);
            color: palette(window-text);
            border: 1px solid palette(mid);
            border-radius: 8px;
            gridline-color: palette(midlight);
            selection-background-color: palette(highlight);
            selection-color: palette(base);
        }}
        QHeaderView::section {{
            background-color: palette(alternate-base);
            color: palette(window-text);
            border: none;
            border-right: 1px solid palette(midlight);
            border-bottom: 1px solid palette(mid);
            padding: 4px 8px;
            font-weight: 600;
        }}
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QDockWidget {{
            color: palette(window-text);
        }}
        QDockWidget::title {{
            background: palette(alternate-base);
            color: palette(window-text);
            border-radius: 8px;
            padding: 6px 12px;
        }}
        """


# ============================================================================
# 三套主题实例
# ============================================================================

# 低饱和浅色（默认主题）
# 配色规范：米白底 + 蓝灰文字 + 橙强调 + 绿成功 + 红警示，全链路低饱和。
DEFAULT_LIGHT = Theme(
    name="default",
    display_name="默认",
    colors=tokens().theme_colors("default"),
    spectrum_colors=[
        "#5B7B8C",  # 蓝灰
        "#6B8C9A",  # 蓝青
        "#6BA89A",  # 青绿(成功)
        "#8FAF8A",  # 灰绿
        "#B8B08A",  # 灰黄
        "#C4A87A",  # 暖米
        "#C4845C",  # 橙(强调)
        "#B87C6B",  # 砖红
    ],
)

# 深色
DARK = Theme(
    name="dark",
    display_name="深色",
    colors=tokens().theme_colors("dark"),
    spectrum_colors=[
        "#5B8C9A",
        "#6BA89A",
        "#8FB87A",
        "#C4B85C",
        "#D4A55C",
        "#D4956A",
        "#D4806A",
        "#D47070",
    ],
)

# 高对比（无障碍）
HIGH_CONTRAST = Theme(
    name="high_contrast",
    display_name="高对比",
    colors=tokens().theme_colors("high_contrast"),
    spectrum_colors=[
        "#00BFFF",
        "#00FF7F",
        "#ADFF2F",
        "#FFFF00",
        "#FFD700",
        "#FF8C00",
        "#FF4500",
        "#FF0000",
    ],
)

# ============================================================================
# CarWith 深色（设计体系落地，QSS 委托 tokens.py 单例）
# ============================================================================
class CarDarkTheme(Theme):
    """CarWith 设计体系深色主题。

    历史：曾把 QSS 委托给 ``tokens.DesignTokens.to_stylesheet("dark")``。该入口
    在当前 tokens 数据上**五种模式全部抛 KeyError('border')**，于是每次都静默
    回退到 ``Theme._build_qss``——等于这段委托从未生效过。
    2026-11-11 起 QSS 统一为「颜色只写 palette(role)」的共享模板（A′ 的前提是
    五套主题样式表逐字相同），这里不再保留会复活另一份 QSS 的覆写点。
    颜色字典仍保留，供频谱组件调色与 QPalette 使用。
    """


DARK_CAR = CarDarkTheme(
    name="dark_car",
    display_name="CarWith 深色",
    colors=tokens().theme_colors("dark_car"),
    spectrum_colors=[
        "#919cac",
        "#7d8a9c",
        "#6BA89A",
        "#8fa3a8",
        "#a89f8a",
        "#c4b89a",
        "#c4a88a",
        "#b89a8a",
    ],
)

# ============================================================================
# Figma 原生深色（.figma 导出稿的设计语言落地，QSS 委托 tokens 的 "figma" 模式）
# ============================================================================
class FigmaTheme(Theme):
    """按 .figma/284_1xx…284_6xx 导出稿实现的深色主题。

    与 dark_car 的差别：页底/卡片底取导出稿的 #000000 / #1f1f1f，
    控件底取 #2c2c2c，描边取 #404040，选中态取占位灰 #d9d9d9。
    默认不启用（DEFAULT_THEME 仍是 default），只在主题下拉/菜单里可选。

    历史同 ``CarDarkTheme``：曾委托 ``tokens.to_stylesheet("figma")``，而该入口
    对五种模式全部抛 KeyError，委托从未生效；2026-11-11 起统一走共享的
    palette(role) 模板，不再覆写 ``_build_qss``。
    """


FIGMA = FigmaTheme(
    name="figma",
    display_name="Figma 深色",
    colors=tokens().theme_colors("figma"),
    spectrum_colors=[
        "#D9D9D9",
        "#B8B8B8",
        "#939393",
        "#6BA89A",
        "#C4B89A",
        "#C4A88A",
        "#B89A8A",
        "#B85C5C",
    ],
)

THEMES = {
    "default": DEFAULT_LIGHT,
    "dark": DARK,
    "high_contrast": HIGH_CONTRAST,
    "dark_car": DARK_CAR,
    "figma": FIGMA,
}

# 兼容旧配置里写死的主题 key（未知名一律回退默认主题）
_THEME_ALIASES = {"light": "default"}

# 默认主题：CarWith 深色。
#
# 2026-11-09 改默认（原为 "default" 浅色），依据是三条可复核的事实：
#   1. 星象仪的天空与频谱画布**物理上必须是深色**（夜空、瀑布图都是深底）。
#      外壳用浅色时，同一屏里必然并存两套明度体系——实测截图里右侧米白卡片
#      直接贴着深蓝夜空，这是「设计语言不统一」最直观的来源。
#   2. 深色主题的对比度审计全项达标；浅色主题当时有 3 项不达标
#      （强调色 2.79、成功色 2.46、选中态白字 3.09），已在本轮修正，
#      但修正后的浅色仍与深色画布冲突。
#   3. 同类工具（Stellarium / SDR++ / SDR# / GQRX）默认都是深色外壳。
# 浅色主题完整保留在主题菜单里，用户随时可切回，配置里存过就尊重用户选择。
DEFAULT_THEME = "dark_car"


def get_theme(name: str) -> Theme:
    """按名称获取主题，不存在则返回默认。"""
    name = _THEME_ALIASES.get(name, name)
    return THEMES.get(name, THEMES[DEFAULT_THEME])
