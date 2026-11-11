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
# 数值微调框箭头图标
# ============================================================================
def _spin_arrow_url(color: str, up: bool) -> str:
    """生成（并复用）一枚主题色的三角形箭头 PNG，返回 QSS `url()` 用的路径。

    为什么非要落成文件：Qt 的 `::up-arrow` / `::down-arrow` 只认 `image`。
    用 border 拼三角形的写法在 6.x 上只会画出一块**实心矩形**（三种写法
    实测都是白方块，见 2026-11-11 探针），而系统样式在深色主题下画的是
    **深色位图**——右栏「下行频率」右侧因此是一条完全看不见箭头的空条。
    颜色随主题取次级文字色，按色值缓存，换主题自动换图。
    """
    tag = "up" if up else "down"
    d = os.path.join(tempfile.gettempdir(), "mbdsdr_ui_assets")
    path = os.path.join(d, f"spin_{tag}_{color.lstrip('#').lower()}.png")
    if os.path.exists(path):
        return path.replace("\\", "/")
    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import QImage, QPainter, QPolygonF

        os.makedirs(d, exist_ok=True)
        # 2 倍图：QSS 里按 9×6 逻辑像素摆放，高分屏缩小采样仍然锐利。
        img = QImage(22, 14, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
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


# ============================================================================
# 复选 / 单选指示符图标
# ============================================================================
def _indicator_asset_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "mbdsdr_ui_assets")
    os.makedirs(d, exist_ok=True)
    return d


#: 图标几何版本。改了勾/圆点的形状必须 +1，否则临时目录里的旧图会被复用
#: （文件名只按颜色区分），升级后用户看到的还是老形状。
_ASSET_VERSION = 1


def _check_mark_url(color: str) -> str:
    """生成一枚「对勾」PNG，给 ``QCheckBox::indicator:checked`` 当 image。

    为什么非要落成文件：Qt 的 ``::indicator`` 只认 ``image``（与 ``::up-arrow``
    同一条规则）。CSS 式那块纯色填充在勾选后只显示成一个亮方块，既没有勾，
    也与"不确定态"长得一样——2026-11-11 放大实拍，用户只能靠亮/暗猜。
    """
    path = os.path.join(_indicator_asset_dir(),
                        f"check_v{_ASSET_VERSION}_{color.lstrip('#').lower()}.png")
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
        pen = QPen(QColor(color), 3.4)
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


def _radio_dot_url(color: str) -> str:
    """生成单选按钮的实心圆点 PNG（口径与 ``_check_mark_url`` 相同）。"""
    path = os.path.join(_indicator_asset_dir(),
                        f"radiodot_v{_ASSET_VERSION}_{color.lstrip('#').lower()}.png")
    if os.path.exists(path):
        return path.replace("\\", "/")
    try:
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtGui import QImage, QPainter

        img = QImage(26, 26, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(13.0, 13.0), 6.0, 6.0)
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

def _control_coverage_qss(c: dict) -> str:
    """所有主题共用的「原生控件接管」样式。

    2026-11-09 审计（主窗实测控件普查）：``QToolButton`` **138 个**、
    ``QCheckBox`` 28 个、``QListView`` 42 个、``QSplitterHandle`` 11 个
    从未被任何主题的 QSS 选中过——它们在深色主题下仍是系统自带的白底方角，
    是「设计语言不统一」最直接的来源：同一屏里既有按 token 画的卡片，
    也有 Qt 默认皮肤画的按钮。

    这里不改任何布局与行为，只按**同一个形状语法**接管它们：
    8px 圆角、1px 描边、hover 提亮 + 强调色描边、选中用强调色左标。
    颜色一律从当前主题的 ``colors`` 取，缺键有兜底，因此五个主题共用一份。
    """
    bg = c["bg"]
    bg_alt = c.get("bg_alt", bg)
    card = c.get("card", bg)
    text = c["text"]
    border = c.get("border", text)
    grid = c.get("grid", border)
    primary = c.get("primary", text)
    hover = c.get("button_hover", c.get("bg_alt", card))
    pressed = c.get("button_pressed", grid)
    button = c.get("button", card)
    disabled = c.get("text_disabled", c.get("text_secondary", text))
    disabled_bg = c.get("button_disabled", card)
    secondary = c.get("text_secondary", text)
    arrow_up = _spin_arrow_url(secondary, True)
    arrow_down = _spin_arrow_url(secondary, False)
    # 勾/圆点色：勾画在 primary 填充上，用页面底色反白；单选的圆点画在按钮
    # 底色上，用 primary。两者都按色值缓存，换主题自动换图。
    check_mark = _check_mark_url(bg)
    check_mark_off = _check_mark_url(disabled)
    radio_dot = _radio_dot_url(primary)
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
            background-color: {bg_alt};
            color: {text};
            border-bottom: 1px solid {border};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 6px 12px;
            color: {text};
        }}
        QMenuBar::item:selected {{
            background-color: {hover};
            border-radius: 4px;
        }}
        QMenuBar::item:pressed {{
            background-color: {pressed};
            border-radius: 4px;
        }}
        /* QGroupBox：dark_car 与 figma 走的 tokens.to_stylesheet() 里**没有**
           这条规则，分组框一直吃 Qt 默认的 3D 边与标题间距——既和卡片不是
           一套形状，也把面板撑高了 9px（2026-11-09 基线实测
           adsb 449→458）。这里按 default 主题已有的数值统一，几何随之一致。 */
        QGroupBox {{
            background-color: {card};
            border: 1px solid {border};
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
            color: {text};
        }}
        /* 断连卡片整组 setEnabled(False)：标题跟着变灰。
           必须写成全局选择器——若改成给单张卡片 setStyleSheet，Qt 会切到
           "控件自带样式表"的绘制路径，标题从边框上方挪进边框内，卡片顶边
           被擦出黑色缺口（2026-11-11 实拍右栏状态面板）。
           选属性选择器而不是伪状态：实测 `QGroupBox:disabled::title` 在
           QGroupBox 的子控件上不生效（标题仍是纯白），`[enabled="false"]`
           生效。 */
        QGroupBox[enabled="false"]::title {{
            color: {c['text_disabled']};
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
            background-color: {hover};
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
            color: {text};
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 4px 8px;
        }}
        QToolButton:hover {{
            background-color: {hover};
            border-color: {primary};
        }}
        QToolButton:pressed {{
            background-color: {pressed};
        }}
        QToolButton:checked {{
            background-color: {pressed};
            border-color: {primary};
        }}
        QToolButton:disabled {{
            color: {disabled};
            background-color: transparent;
        }}
        QToolButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QCheckBox, QRadioButton {{
            background-color: transparent;
            color: {text};
            spacing: 8px;
        }}
        QCheckBox:disabled, QRadioButton:disabled {{
            color: {disabled};
        }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid {border};
            border-radius: 4px;
            background-color: {button};
        }}
        QCheckBox::indicator:hover {{
            border-color: {primary};
        }}
        QCheckBox::indicator:checked {{
            background-color: {primary};
            border-color: {primary};
            image: url("{check_mark}");
        }}
        QCheckBox::indicator:checked:disabled {{
            background-color: {disabled_bg};
            border-color: {grid};
            image: url("{check_mark_off}");
        }}
        QCheckBox::indicator:disabled {{
            border-color: {grid};
            background-color: {disabled_bg};
        }}
        QRadioButton::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid {border};
            border-radius: 8px;
            background-color: {button};
        }}
        QRadioButton::indicator:hover {{
            border-color: {primary};
        }}
        /* 选中态用「描边 + 圆心」，不用 4px 粗边框：15px 方框减去 4px 描边后
           内圆半径只剩 3.5px，实测画出来是一个方角深坑（2026-11-11 放大图），
           看着像禁用而不是选中。 */
        QRadioButton::indicator:checked {{
            border: 1px solid {primary};
            background-color: {button};
            image: url("{radio_dot}");
        }}
        QRadioButton::indicator:disabled {{
            border-color: {grid};
            background-color: {disabled_bg};
        }}
        QListView, QListWidget {{
            background-color: {card};
            color: {text};
            border: 1px solid {border};
            border-radius: 8px;
            outline: none;
            alternate-background-color: {bg};
        }}
        QListView::item, QListWidget::item {{
            padding: 6px 8px;
            border-radius: 6px;
            color: {text};
        }}
        QListView::item:hover, QListWidget::item:hover {{
            background-color: {hover};
        }}
        QListView::item:selected, QListWidget::item:selected {{
            background-color: {pressed};
            color: {text};
            border-left: 3px solid {primary};
        }}
        QListView::item:disabled, QListWidget::item:disabled {{
            color: {secondary};
        }}
        QSplitter::handle {{
            background-color: {grid};
        }}
        QSplitter::handle:hover {{
            background-color: {primary};
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


class Theme:
    """主题数据类。"""

    def __init__(self, name: str, display_name: str, colors: dict, spectrum_colors: list):
        self.name = name
        self.display_name = display_name
        self.colors = colors
        self.spectrum_colors = spectrum_colors  # 频谱渐变颜色列表 (hex)

    def apply(self, app: QApplication):
        """应用主题到 QApplication（幂等：状态相同则跳过重抛光）。

        setPalette/setFont/setStyleSheet 都是 app 级全量操作——重抛光**全部**
        控件，成本 O(全部控件)。每个新 MainWindow 构造都会应用一次主题，N 个
        窗口累计 O(N²)：实测 20+ 个 MainWindow 后单次构造从 1.7s 恶化到 30s+
        且持续恶化，长驻进程与 pytest 套件（tests/ 根 90% 处挂死）直接把 UI
        线程卡死。状态（palette/font/stylesheet）完全相同时重复应用不改变任何
        视觉表现，跳过是安全的；三者任一不同才真正应用。
        """
        palette = QPalette()
        c = self.colors

        palette.setColor(QPalette.Window, QColor(c["bg"]))
        palette.setColor(QPalette.WindowText, QColor(c["text"]))
        palette.setColor(QPalette.Base, QColor(c["card"]))
        palette.setColor(QPalette.AlternateBase, QColor(c["bg_alt"]))
        palette.setColor(QPalette.ToolTipBase, QColor(c["card"]))
        palette.setColor(QPalette.ToolTipText, QColor(c["text"]))
        palette.setColor(QPalette.Text, QColor(c["text"]))
        palette.setColor(QPalette.Button, QColor(c["button"]))
        palette.setColor(QPalette.ButtonText, QColor(c["text"]))
        palette.setColor(QPalette.BrightText, QColor(c["accent"]))
        # Link 是"橙色当文字用"：取压暗档 primary_text（fill 仍用 primary）
        palette.setColor(QPalette.Link, QColor(c.get("primary_text", c["primary"])))
        palette.setColor(QPalette.Highlight, QColor(c["primary"]))
        # 选中行上的文字不能一律用 card：浅色主题 card 是纯白，白字压在原
        # 强调色上只有 3.09:1（选中一行读不出来）。按实际对比度挑达标的墨色。
        palette.setColor(QPalette.HighlightedText, QColor(_on_primary(c)))

        # 禁用状态
        palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["text_disabled"]))

        # 主题自带的 QSS + 所有主题共用的原生控件接管。
        # 后者必须并进同一个字符串：Qt 只认最后一次 setStyleSheet，
        # 分两次设置会把主题自带的那份整体覆盖掉。
        qss = self._build_qss() + _control_coverage_qss(self.colors)
        font = get_app_font()
        try:
            if (app.palette() == palette and app.font() == font
                    and app.styleSheet() == qss):
                return                      # 已应用过同一状态：跳过全量重抛光
        except Exception:
            pass                            # 比较失败（异常平台）则照常应用

        app.setPalette(palette)
        app.setFont(font)

        # 应用 QSS 样式表
        app.setStyleSheet(qss)

    def _build_qss(self) -> str:
        """构建 QSS 样式表。"""
        c = self.colors
        return f"""
        QWidget {{
            background-color: {c['bg']};
            color: {c['text']};
            font-family: "MiSans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        }}
        QFrame#card {{
            background-color: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 12px;
        }}
        QLabel#sectionTitle {{
            font-size: 11pt;
            font-weight: 600;
            color: {c['text']};
            padding: 4px 0px;
        }}
        QLabel#statusValue {{
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 11pt;
            color: {c['text']};
        }}
        QLabel#freqDisplay {{
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 28pt;
            font-weight: 700;
            color: {c['text']};
            background-color: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 12px;
            padding: 8px 16px;
        }}
        QPushButton {{
            background-color: {c['button']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 10pt;
        }}
        QPushButton:hover {{
            background-color: {c['button_hover']};
            border-color: {c['primary']};
        }}
        QPushButton:pressed {{
            background-color: {c['button_pressed']};
            color: {c['text']};
        }}
        QPushButton:disabled {{
            color: {c['text_disabled']};
            background-color: {c['button_disabled']};
            border-color: {c['grid']};
        }}
        QPushButton#recordButton {{
            background-color: {c['accent']};
            color: {c['card']};
            border: 1px solid {c['accent']};
            font-weight: 600;
        }}
        QPushButton#recordButton:hover {{
            background-color: {c['accent_hover']};
        }}
        QPushButton#recordButton:checked {{
            background-color: {c['danger']};
            border-color: {c['danger']};
        }}
        QPushButton#recordButton:disabled {{
            background-color: {c['button_disabled']};
            color: {c['text_disabled']};
            border-color: {c['grid']};
            font-weight: 400;
        }}
        QComboBox {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 10pt;
        }}
        QComboBox:hover {{
            border-color: {c['primary']};
        }}
        QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled,
        QDoubleSpinBox:disabled {{
            color: {c['text_disabled']};
            background-color: {c['button_disabled']};
            border-color: {c['grid']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            selection-background-color: {c['primary']};
            selection-color: {c['card']};
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            background: {c['grid']};
            border-radius: 2px;
        }}
        /* 手柄左侧填 primary：没有填充时滑杆只有一个小圆点，看不出"当前值
           占了几分"（ANR 强度、音量都靠这个读数）。 */
        QSlider::sub-page:horizontal {{
            background: {c['primary']};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -5px 0;
            background: {c['primary']};
            border: none;
            border-radius: 7px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {c['accent']};
        }}
        /* 竖滑杆（频谱右侧缩放/dB 量程）：只给轨道和手柄，不做填充——三根里
           两根是 setInvertedAppearance(True)，sub/add-page 的上下方向会跟着
           翻转，填充方向写错就是"越推越少"的错误读数。 */
        QSlider::groove:vertical {{
            width: 4px;
            background: {c['grid']};
            border-radius: 2px;
        }}
        QSlider::handle:vertical {{
            width: 14px;
            height: 14px;
            margin: 0 -5px;
            background: {c['primary']};
            border: none;
            border-radius: 7px;
        }}
        QSlider::handle:vertical:hover {{
            background: {c['accent']};
        }}
        /* 禁用态必须变灰：QSS 只要写了 groove/handle，Qt 就不会再按"禁用"自动
           调暗（实测 ANR 面板无硬件时滑杆看起来完全可拖）。 */
        QSlider::sub-page:horizontal:disabled {{
            background: {c['grid']};
        }}
        QSlider::handle:horizontal:disabled,
        QSlider::handle:vertical:disabled {{
            background: {c['border']};
        }}
        QLineEdit {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 7px 12px;
            font-size: 10pt;
        }}
        QLineEdit:focus {{
            border-color: {c['primary']};
        }}
        QTextEdit, QPlainTextEdit {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 6px;
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 9pt;
        }}
        QTabWidget::pane {{
            border: 1px solid {c['border']};
            border-radius: 10px;
            background-color: {c['card']};
        }}
        QTabBar::tab {{
            background-color: {c['bg']};
            color: {c['text_secondary']};
            border: 1px solid {c['border']};
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 6px 14px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: {c['card']};
            color: {c.get('primary_text', c['primary'])};
            font-weight: 600;
        }}
        QTabBar::tab:hover {{
            color: {c['text']};
        }}
        QStatusBar {{
            background-color: {c['bg_alt']};
            color: {c['text_secondary']};
            border-top: 1px solid {c['border']};
        }}
        QToolBar {{
            background-color: {c['bg_alt']};
            border-bottom: 1px solid {c['border']};
            spacing: 4px;
            padding: 4px;
        }}
        QScrollBar:vertical {{
            background: {c['bg']};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border']};
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c['primary']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QGroupBox {{
            background-color: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 12px;
            margin-top: 16px;
            padding: 12px 10px 10px 10px;
            font-size: 11pt;
            font-weight: 600;
            color: {c['text']};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {c['text']};
        }}
        QSpinBox, QDoubleSpinBox {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 5px 10px;
            font-size: 10pt;
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {c['primary']};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 16px;
            background: {c['bg_alt']};
            border: none;
        }}
        QProgressBar {{
            background-color: {c['grid']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            text-align: center;
            color: {c['text']};
            height: 14px;
        }}
        QProgressBar::chunk {{
            background-color: {c['primary']};
            border-radius: 6px;
        }}
        QMenuBar {{
            background-color: {c['bg_alt']};
            color: {c['text']};
            border-bottom: 1px solid {c['border']};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 6px 12px;
            color: {c['text']};
        }}
        QMenuBar::item:selected {{
            background-color: {c['button_hover']};
            border-radius: 4px;
        }}
        QMenu {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
        }}
        QMenu::item {{
            padding: 6px 24px;
        }}
        QMenu::item:selected {{
            background-color: {c['button_hover']};
            color: {c['text']};
        }}
        QTableWidget, QTableView, QTreeWidget, QTreeView {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            gridline-color: {c['grid']};
            selection-background-color: {c['primary']};
            selection-color: {c['card']};
        }}
        QHeaderView::section {{
            background-color: {c['bg_alt']};
            color: {c['text']};
            border: none;
            border-right: 1px solid {c['grid']};
            border-bottom: 1px solid {c['border']};
            padding: 4px 8px;
            font-weight: 600;
        }}
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QDockWidget {{
            color: {c['text']};
        }}
        QDockWidget::title {{
            background: {c['bg_alt']};
            color: {c['text']};
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

    QSS 不在此处手写，而是委托 tokens.DesignTokens 单例——所有圆角/字号/间距/
    比例都与 Figma 设计稿（1920x1200）严格对齐。颜色字典保留供频谱组件调色与
    QPalette 使用；主题 key 为 "dark_car"（额外主题，不替换 default）。
    """

    def _build_qss(self) -> str:
        try:
            from tokens import tokens
            return tokens().to_stylesheet("dark")
        except Exception:
            return super()._build_qss()


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
    """

    def _build_qss(self) -> str:
        try:
            return tokens().to_stylesheet("figma")
        except Exception:
            return super()._build_qss()


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
