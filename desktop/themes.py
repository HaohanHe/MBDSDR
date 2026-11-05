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

# 设计令牌单一来源：主题色只从 tokens.THEME_COLORS 取，不在本文件写 hex。
from tokens import tokens


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
        palette.setColor(QPalette.HighlightedText, QColor(c["card"]))

        # 禁用状态
        palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["text_disabled"]))

        qss = self._build_qss()
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
            background: {c['border']};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -6px 0;
            background: {c['primary']};
            border-radius: 8px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {c['accent']};
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

DEFAULT_THEME = "default"


def get_theme(name: str) -> Theme:
    """按名称获取主题，不存在则返回默认。"""
    name = _THEME_ALIASES.get(name, name)
    return THEMES.get(name, THEMES[DEFAULT_THEME])
