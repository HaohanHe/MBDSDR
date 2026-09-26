"""
MBDSDR 桌面端 CarWith 设计体系 Token 单例
==========================================

把 Figma 设计稿（docs/design_ref/carwith_figma/，画板 284_75 / 284_410 /
284_456 / 284_566，画布 1920x1200）从 SCSS 提取的精确设计令牌收敛到一处：

  * 颜色（深色方向，bg #000000，卡片 #1f1f1f/#2b2c2f/#35373c/#36383a）
  * 文字 alpha 层级（1.0 / 0.78 / 0.67 / 0.5 / 0.3 / 0.23 / 0.2）
  * 圆角（大卡 24 / 中卡 12 / 小卡按钮 8 / 胶囊 18 / Dock 图标 13 / 圆形 42/84）
  * 字号（大标题 39/52、标题 24/32、正文 20/26 w500、Dock 42/56、小 14）
  * 间距（顶栏 23px 40px 25px 36px、底 Dock 14px 40px、卡片 26px 28px、gutter 20）
  * 尺寸（顶栏 56、底 Dock 64、QSplitter handle 4、折叠窄条 36）
  * 动效（反馈 150ms、过渡 300ms、OutCubic）

只持有"数值"，不直接操作 Qt 控件。to_stylesheet(mode) 把令牌翻译成 QSS：
  * mode="dark"  → CarWith 深色方向（对应 themes.py 的 "dark_car" 主题）
  * mode="light" → 保留 default 主题配色（米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C），
                   但套用同一套圆角/间距/字号令牌，保证两个主题视觉语言一致。

红线：不造假数据；令牌只描述外观，不引入任何业务数值。
"""

from __future__ import annotations

from typing import Dict


class DesignTokens:
    """CarWith 设计令牌单例。

    通过 ``DesignTokens()`` 拿到全局唯一实例（首次调用即构造）。所有字典都按
    "语义名 -> 精确值" 组织，业务代码只引用语义名，不散落魔法数字。
    """

    _instance: "DesignTokens | None" = None

    def __new__(cls) -> "DesignTokens":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._built = False
            cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        # 单例：__init__ 每次都跑，但只真正构建一次（重复实例化同一对象时幂等）。
        if getattr(self, "_built", False):
            return
        self._build()
        self._built = True

    # ------------------------------------------------------------------
    # 令牌本体（数值严格对照 Figma / SCSS）
    # ------------------------------------------------------------------
    def _build(self) -> None:
        # ---- 颜色（深色方向）----
        # bg 纯黑；卡片四级明度从 #1f1f1f 起逐级提亮；强调用冷蓝灰 #919cac。
        self.COLORS: Dict[str, str] = {
            "bg_dark":      "#000000",
            "card_1":       "#1f1f1f",   # 最深卡片（底卡）
            "card_2":       "#2b2c2f",   # 中卡片
            "card_3":       "#35373c",   # 亮卡片（悬浮）
            "card_4":       "#36383a",   # 近白卡片
            "card_hover":   "#3d3f44",
            "text_primary": "#ffffff",
            "gray_100":     "#d9d9d9",   # 透明灰（描边/分隔）
            "gray_200":     "#939393",
            "gray_300":     "#606060",
            "gray_400":     "#585c63",
            "accent":       "#919cac",   # 强调蓝灰
            "success":      "#6BA89A",
            "danger":       "#B85C5C",
            # 浅色（default）方向：保留既有米白/蓝灰/橙
            "light_bg":       "#F5F3EF",
            "light_bg_alt":   "#FAF8F5",
            "light_card":     "#FFFFFF",
            "light_text":     "#5B7B8C",
            "light_text_sub": "#8A9BA8",
            "light_border":   "#C8C0B4",
            "light_accent":   "#C4845C",
        }

        # ---- 文字 alpha 层级（叠加在白字上）----
        self.TEXT_ALPHA: Dict[str, float] = {
            "primary":   1.0,
            "secondary": 0.78,
            "tertiary2": 0.67,
            "tertiary":  0.5,
            "quaternary":0.3,
            "faint":     0.23,
            "disabled":  0.2,
        }

        # ---- 圆角（px）----
        self.RADIUS: Dict[str, int] = {
            "card_lg":    24,   # 大悬浮卡（全屏地图上的圆角卡）
            "card_md":    12,    # 中卡片
            "card_sm":    8,     # 小卡片 / 输入框
            "button":     18,    # 胶囊按钮
            "dock_icon":  13,    # Dock 图标底
            "circle_sm":  42,    # 圆形小按钮
            "circle_lg":  84,    # 圆形大按钮（播放面板）
        }

        # ---- 字号（px，行高见 FONT_LINEHEIGHT）----
        # Figma 给的是 px；QSS 用 pt 换算系数约 0.75（px*0.75=pt）。
        # 这里保留 px 字符串语义名，QSS 里统一转 pt。
        self.FONT: Dict[str, str] = {
            "title_lg": "39px",   # 大标题（播放面板封面区）
            "title":   "24px",   # 标题
            "body":    "20px",    # 正文（w500）
            "dock":    "42px",    # Dock 图标下文字
            "small":   "14px",    # 辅助说明
        }
        self.FONT_LINEHEIGHT: Dict[str, int] = {
            "title_lg": 52,
            "title":    32,
            "body":     26,
            "dock":     56,
            "small":    20,
        }
        self.FONT_WEIGHT: Dict[str, int] = {
            "title_lg": 600,
            "title":    600,
            "body":     500,
            "dock":     500,
            "small":    400,
        }

        # ---- 间距 ----
        self.SPACING: Dict[str, object] = {
            "gutter":      20,           # 卡片之间等距
            "gutter_min":  16,
            "gutter_max":  24,
            "padding_lg":  "26px 28px",   # 大卡内边距
            "padding_md":  "16px 20px",
            "topbar":      "23px 40px 25px 36px",  # 顶栏 padding
            "dock":        "14px 40px",    # 底 Dock padding
        }

        # ---- 尺寸 ----
        self.SIZE: Dict[str, int] = {
            "topbar_h":        56,    # 顶部全局栏高度
            "dock_h":          64,    # 底部 Dock 高度
            "splitter_handle": 4,     # QSplitter handle 厚度
            "collapse_strip":  36,    # 副窗折叠窄条宽度
            "min_dock_w":      300,   # 停靠面板最小宽
        }

        # ---- 比例约束（流体布局：用 stretch/min-max，不写死像素）----
        # 所有分区比例都来自 tokens，业务代码不再散落 magic number。
        self.RATIO: Dict[str, float] = {
            "splitter_center":   0.62,  # 中央主表面占比（频谱/天空）
            "splitter_side":     0.38,  # 侧栏占比
            "card_pref":         0.33,  # 单张卡推荐占可用宽度（三卡并排）
            "card_min_w":        200.0, # 卡片最小宽（再窄就换行/折叠）
            "card_max_w":        560.0, # 卡片最大宽（超宽时居中不拉伸到无穷）
            "analysis_left":     0.60,  # analysis 预设：左频谱占比
            "analysis_right":    0.40,   # analysis 预设：右射频天空占比
            "focus_side":        0.28,  # focus 预设：右侧控制条占比
            "grid_cell":         0.5,   # grid 四宫格单元格占比
        }

        # ---- 动效 ----
        self.MOTION: Dict[str, object] = {
            "feedback_ms":   150,
            "transition_ms": 300,
            "easing":        "OutCubic",
        }

        # ---- 画布比例（1920x1200 设计稿按实际窗口缩放）----
        self.CANVAS_W = 1920
        self.CANVAS_H = 1200

    # ------------------------------------------------------------------
    # 便捷取色（带 alpha 的白字，深色方向用）
    # ------------------------------------------------------------------
    def text(self, level: str = "primary") -> str:
        """返回带 alpha 的白字色值（rgba(255,255,255,a)）。"""
        a = self.TEXT_ALPHA.get(level, 1.0)
        return f"rgba(255, 255, 255, {a})"

    @staticmethod
    def _px_to_pt(px_str: str) -> float:
        """'39px' -> 29.25pt（QSS font-size 用 pt）。"""
        try:
            return round(float(px_str.replace("px", "")) * 0.75, 1)
        except Exception:
            return 10.0

    # ------------------------------------------------------------------
    # QSS 生成
    # ------------------------------------------------------------------
    def to_stylesheet(self, mode: str = "dark") -> str:
        """生成整套 QSS 样式表。

        mode:
          * "dark"  —— CarWith 深色（黑底 + 四级灰卡 + 白字 alpha）
          * "light" —— default 浅色（米白底 + 蓝灰字 + 橙强调），套同一套圆角/字号
        """
        if mode == "light":
            return self._build_light_qss()
        return self._build_dark_qss()

    # ---- 深色 QSS ----
    def _build_dark_qss(self) -> str:
        c = self.COLORS
        r = self.RADIUS
        gap = self.SPACING["gutter"]
        return f"""
/* ===== CarWith dark_car 主题（由 tokens.py 生成，勿手改数值）===== */
QMainWindow, QWidget {{
    background-color: {c['bg_dark']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    font-family: "MiSans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
QWidget#topBar {{
    background-color: {c['bg_dark']};
    border: none;
    border-bottom: 1px solid {c['gray_400']};
}}
QWidget#bottomDock {{
    background-color: {c['card_1']};
    border: none;
    border-top: 1px solid {c['gray_400']};
}}
QFrame#card, QFrame#sectionCard {{
    background-color: {c['card_2']};
    border: none;
    border-radius: {r['card_md']}px;
}}
QFrame#cardLg {{
    background-color: {c['card_2']};
    border: none;
    border-radius: {r['card_lg']}px;
}}
QLabel#sectionTitle {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    font-size: {self._px_to_pt(self.FONT['title'])}pt;
    font-weight: {self.FONT_WEIGHT['title']};
}}
QLabel#freqDisplay {{
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    font-size: {self._px_to_pt(self.FONT['title_lg'])}pt;
    font-weight: {self.FONT_WEIGHT['title_lg']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['primary']});
    background-color: {c['card_1']};
    border: none;
    border-radius: {r['card_md']}px;
    padding: 8px 16px;
}}
QLabel#statusValue {{
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    color: rgba(255,255,255,{self.TEXT_ALPHA['tertiary']});
}}
QLabel#dockHint {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['disabled']});
    font-size: {self._px_to_pt(self.FONT['small'])}pt;
}}
QPushButton {{
    background-color: {c['card_3']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    border: none;
    border-radius: {r['button']}px;
    padding: 8px 18px;
    font-size: {self._px_to_pt(self.FONT['body'])}pt;
    font-weight: {self.FONT_WEIGHT['body']};
}}
QPushButton:hover {{ background-color: {c['card_hover']}; }}
QPushButton:pressed {{ background-color: {c['card_4']}; }}
QPushButton:disabled {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['disabled']});
    background-color: {c['card_1']};
}}
QPushButton#iconButton {{
    background-color: transparent;
    border-radius: {r['dock_icon']}px;
    padding: 6px;
}}
QPushButton#iconButton:hover {{ background-color: {c['card_3']}; }}
QPushButton#recordButton {{
    background-color: {c['accent']};
    color: {c['bg_dark']};
    border: none;
    font-weight: 600;
}}
QPushButton#recordButton:checked {{ background-color: {c['danger']}; color: white; }}
QPushButton#recordButton:disabled {{
    background-color: {c['card_1']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['disabled']});
}}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {c['card_2']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    border: none;
    border-radius: {r['card_sm']}px;
    padding: 6px 12px;
}}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {c['accent']}; }}
QComboBox QAbstractItemView {{
    background-color: {c['card_3']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    selection-background-color: {c['accent']};
    selection-color: {c['bg_dark']};
    border: none;
}}
QSlider::groove:horizontal {{
    height: {self.SIZE['splitter_handle']}px;
    background: {c['gray_400']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    background: {c['accent']};
    border-radius: 8px;
}}
QSplitter::handle {{
    background-color: {c['bg_dark']};
}}
QSplitter::handle:horizontal {{ width: {self.SIZE['splitter_handle']}px; }}
QSplitter::handle:vertical {{ height: {self.SIZE['splitter_handle']}px; }}
/* 浮动 Dock：大圆角 + 半透明深色底 + 轻微边（阴影 QSS 难表达，靠圆角+底色）*/
QDockWidget {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {c['card_1']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    border: none;
    border-top-left-radius: {r['card_lg']}px;
    border-top-right-radius: {r['card_lg']}px;
    padding: 6px 12px;
    margin: {gap//4}px;
}}
QDockWidget[floating="true"] {{
    background-color: rgba(31,31,31,0.96);
    border: 1px solid {c['gray_400']};
    border-radius: {r['card_lg']}px;
}}
QDockWidget[floating="true"] > QWidget > QScrollArea {{
    border-radius: {r['card_lg']}px;
}}
QTabWidget::pane {{
    border: none;
    background: {c['bg_dark']};
}}
QTabBar::tab {{
    background: {c['card_1']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['tertiary']});
    border: none;
    padding: 6px 14px;
    border-top-left-radius: {r['card_sm']}px;
    border-top-right-radius: {r['card_sm']}px;
}}
QTabBar::tab:selected {{
    background: {c['card_2']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['primary']});
}}
QStatusBar {{
    background: {c['card_1']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['tertiary']});
    border-top: 1px solid {c['gray_400']};
}}
QScrollBar:vertical {{ background: {c['bg_dark']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['gray_400']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['gray_200']}; }}
QToolTip {{
    background-color: {c['card_3']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    border: none;
    border-radius: {r['card_sm']}px;
    padding: 4px 8px;
}}
QMenu {{
    background-color: {c['card_2']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    border: none;
}}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background-color: {c['card_hover']}; }}
"""

    # ---- 浅色 QSS（保留 default 配色，套 token 圆角/字号）----
    def _build_light_qss(self) -> str:
        c = self.COLORS
        r = self.RADIUS
        return f"""
/* ===== Carwith light 主题（default 配色 + token 圆角/字号）===== */
QMainWindow, QWidget {{
    background-color: {c['light_bg']};
    color: {c['light_text']};
    font-family: "MiSans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
QFrame#card, QFrame#sectionCard {{
    background-color: {c['light_card']};
    border: 1px solid {c['light_border']};
    border-radius: {r['card_md']}px;
}}
QFrame#cardLg {{
    background-color: {c['light_card']};
    border: 1px solid {c['light_border']};
    border-radius: {r['card_lg']}px;
}}
QPushButton {{
    background-color: {c['light_card']};
    color: {c['light_text']};
    border: 1px solid {c['light_border']};
    border-radius: {r['button']}px;
    padding: 8px 18px;
    font-size: {self._px_to_pt(self.FONT['body'])}pt;
}}
QPushButton:hover {{ background-color: #F0EDE8; }}
QPushButton:disabled {{ color: #A0A0A0; background-color: #D8D2C8; }}
QPushButton#recordButton {{
    background-color: {c['light_accent']};
    color: white; border: none; font-weight: 600;
}}
QDockWidget::title {{
    background: {c['light_bg_alt']};
    border-radius: {r['card_sm']}px;
    padding: 6px 12px;
}}
QSplitter::handle:horizontal {{ width: {self.SIZE['splitter_handle']}px; }}
QSplitter::handle:vertical {{ height: {self.SIZE['splitter_handle']}px; }}
"""


# 模块级便捷单例入口
def tokens() -> DesignTokens:
    """返回全局 DesignTokens 单例。"""
    return DesignTokens()
