"""
MBDSDR 桌面端主题系统
=======================
三套主题：日式低饱和浅色 (japanese_light)、深色 (dark)、高对比 (high_contrast)。
设计原则：不喧宾夺主、稳定可读优先、低饱和扁平化、禁用 emoji。
字体优先 MiSans，回退 Noto Sans CJK / 系统无衬线。
"""

from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtWidgets import QApplication


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


def get_monospace_font() -> QFont:
    """等宽字体（频率显示、日志）。"""
    font = QFont()
    font.setFamilies([
        "JetBrains Mono",
        "Fira Code",
        "Source Code Pro",
        "Menlo",
        "Consolas",
        "monospace",
    ])
    font.setPointSize(11)
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
        """应用主题到 QApplication。"""
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
        palette.setColor(QPalette.Link, QColor(c["primary"]))
        palette.setColor(QPalette.Highlight, QColor(c["primary"]))
        palette.setColor(QPalette.HighlightedText, QColor(c["card"]))

        # 禁用状态
        palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["text_disabled"]))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["text_disabled"]))

        app.setPalette(palette)
        app.setFont(get_app_font())

        # 应用 QSS 样式表
        app.setStyleSheet(self._build_qss())

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
            border-radius: 8px;
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
            color: {c['primary']};
        }}
        QLabel#freqDisplay {{
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 28pt;
            font-weight: 700;
            color: {c['text']};
            background-color: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 8px 16px;
        }}
        QPushButton {{
            background-color: {c['button']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 6px 14px;
            font-size: 10pt;
        }}
        QPushButton:hover {{
            background-color: {c['button_hover']};
            border-color: {c['primary']};
        }}
        QPushButton:pressed {{
            background-color: {c['primary']};
            color: {c['card']};
        }}
        QPushButton:disabled {{
            color: {c['text_disabled']};
            background-color: {c['button']};
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
        QComboBox {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 5px 10px;
            font-size: 10pt;
        }}
        QComboBox:hover {{
            border-color: {c['primary']};
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
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 10pt;
        }}
        QLineEdit:focus {{
            border-color: {c['primary']};
        }}
        QTextEdit, QPlainTextEdit {{
            background-color: {c['card']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 6px;
            font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
            font-size: 9pt;
        }}
        QTabWidget::pane {{
            border: 1px solid {c['border']};
            border-radius: 6px;
            background-color: {c['card']};
        }}
        QTabBar::tab {{
            background-color: {c['bg']};
            color: {c['text_secondary']};
            border: 1px solid {c['border']};
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 6px 16px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background-color: {c['card']};
            color: {c['primary']};
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
        """


# ============================================================================
# 三套主题实例
# ============================================================================

# 日式低饱和浅色
JAPANESE_LIGHT = Theme(
    name="japanese_light",
    display_name="日式浅色",
    colors={
        "bg": "#F5F3EF",           # 米白暖灰
        "bg_alt": "#EDEBE6",       # 稍深
        "card": "#FFFFFF",          # 纯白卡片
        "text": "#2C2C2C",         # 深灰文字
        "text_secondary": "#6B6B6B",  # 次要文字
        "text_disabled": "#A8A8A8",
        "border": "#E0DEDA",       # 浅灰边框
        "primary": "#5B7B8C",      # 低饱和蓝灰
        "accent": "#C4845C",       # 低饱和橙
        "accent_hover": "#B0754E",
        "danger": "#B85C5C",       # 低饱和红
        "button": "#F0EEE9",
        "button_hover": "#E8E5DF",
    },
    spectrum_colors=[
        "#4A6B7C",  # 深蓝灰
        "#5B8C9A",  # 蓝青
        "#6BA89A",  # 青绿
        "#8FB87A",  # 黄绿
        "#C4B85C",  # 黄
        "#C49A5C",  # 橙黄
        "#C4845C",  # 橙
        "#B86B5C",  # 橙红
    ],
)

# 深色
DARK = Theme(
    name="dark",
    display_name="深色",
    colors={
        "bg": "#1E1E20",
        "bg_alt": "#252528",
        "card": "#28282B",
        "text": "#D0D0D0",
        "text_secondary": "#888888",
        "text_disabled": "#555555",
        "border": "#3A3A3E",
        "primary": "#7A9CAC",
        "accent": "#D4956A",
        "accent_hover": "#C4845C",
        "danger": "#D47070",
        "button": "#333336",
        "button_hover": "#3D3D41",
    },
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
    colors={
        "bg": "#000000",
        "bg_alt": "#111111",
        "card": "#000000",
        "text": "#FFFFFF",
        "text_secondary": "#CCCCCC",
        "text_disabled": "#888888",
        "border": "#FFFFFF",
        "primary": "#00BFFF",
        "accent": "#FF8C00",
        "accent_hover": "#FFA500",
        "danger": "#FF4444",
        "button": "#222222",
        "button_hover": "#333333",
    },
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

THEMES = {
    "japanese_light": JAPANESE_LIGHT,
    "dark": DARK,
    "high_contrast": HIGH_CONTRAST,
}

DEFAULT_THEME = "japanese_light"


def get_theme(name: str) -> Theme:
    """按名称获取主题，不存在则返回默认。"""
    return THEMES.get(name, THEMES[DEFAULT_THEME])
