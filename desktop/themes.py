"""
MBDSDR 桌面端主题系统
=======================
三套主题：默认低饱和浅色 (default)、深色 (dark)、高对比 (high_contrast)。
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
            color: {c['text']};
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
        QGroupBox {{
            background-color: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            margin-top: 14px;
            padding-top: 8px;
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
            border-radius: 6px;
            padding: 4px 8px;
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
            border-radius: 6px;
            text-align: center;
            color: {c['text']};
            height: 14px;
        }}
        QProgressBar::chunk {{
            background-color: {c['primary']};
            border-radius: 5px;
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
            border-radius: 6px;
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
        """


# ============================================================================
# 三套主题实例
# ============================================================================

# 低饱和浅色（默认主题）
# 配色规范：米白底 + 蓝灰文字 + 橙强调 + 绿成功 + 红警示，全链路低饱和。
DEFAULT_LIGHT = Theme(
    name="default",
    display_name="默认",
    colors={
        # 背景 / 卡片
        "bg": "#F5F3EF",            # 主背景 米白暖灰
        "bg_alt": "#FAF8F5",        # 次级背景/状态栏/工具栏
        "card": "#FFFFFF",           # 卡片/面板
        # 文字
        "text": "#5B7B8C",           # 文字主色 蓝灰
        "text_secondary": "#8A9BA8", # 文字次色 更浅蓝灰
        "text_disabled": "#A0A0A0", # 禁用文字
        # 线条
        "border": "#C8C0B4",        # 边框
        "grid": "#D8D2C8",          # 网格线/分割线 浅米灰
        # 语义色
        "primary": "#C4845C",       # 强调/高亮/选中 橙
        "accent": "#C4845C",        # 主操作按钮 橙
        "accent_hover": "#B0754E",
        "success": "#6BA89A",       # 已连接/成功 绿
        "danger": "#B85C5C",        # 未连接/警告/错误 红
        # 按钮态
        "button": "#FFFFFF",         # 按钮默认背景
        "button_hover": "#F0EDE8",
        "button_pressed": "#E8E4DD",
        "button_disabled": "#D8D2C8",
    },
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
    "default": DEFAULT_LIGHT,
    "dark": DARK,
    "high_contrast": HIGH_CONTRAST,
}

# 兼容旧配置里写死的主题 key
_THEME_ALIASES = {"japanese_light": "default", "light": "default"}

DEFAULT_THEME = "default"


def get_theme(name: str) -> Theme:
    """按名称获取主题，不存在则返回默认。"""
    name = _THEME_ALIASES.get(name, name)
    return THEMES.get(name, THEMES[DEFAULT_THEME])
