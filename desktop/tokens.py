# SPDX-License-Identifier: MIT
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
  * AI 面板 / MCP 行为常量（截断长度、示例频率、ai-sdr Mini 默认端点）

只持有"数值"，不直接操作 Qt 控件。to_stylesheet(mode) 把令牌翻译成 QSS：
  * mode="dark"  → CarWith 深色方向（对应 themes.py 的 "dark_car" 主题）
  * mode="light" → 保留 default 主题配色（米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C），
                   但套用同一套圆角/间距/字号令牌，保证两个主题视觉语言一致。

红线：不造假数据；外观令牌只描述外观，不引入任何业务数值。AI 面板 / MCP
编排的行为常量（文本截断长度、示例频率、ai-sdr Mini 默认端点）另设命名区
（AI / MCP）集中收口，业务代码不得散落魔法数字；两区互不混用。
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
            # 频谱 Max/Min hold 线（SDR++ 双保持签名：暖橙=max 包络，冷蓝=min 包络）
            "spectrum_max_hold": "#C87F5A",
            "spectrum_min_hold": "#6C8EBF",
            # 频谱书签标签（gqrx 书签竖旗签名：暗金药丸 + 虚线）
            "spectrum_bookmark": "#A8924F",
            # 浅色（default）方向：保留既有米白/蓝灰/橙
            "light_bg":       "#F5F3EF",
            "light_bg_alt":   "#FAF8F5",
            "light_card":     "#FFFFFF",
            "light_text":     "#5B7B8C",
            "light_text_sub": "#8A9BA8",
            "light_border":   "#C8C0B4",
            "light_accent":   "#C4845C",
            "light_accent_hover": "#D4946C",   # 主操作按钮 hover（连接按钮高亮）
            "light_hint_bg":   "#F0E8DC",      # 无设备引导横幅底色
            "light_hint_text": "#8A6D4A",      # 无设备引导横幅文字
            "disabled_gray":   "#999999",      # 次要置灰文字/指示
            # ---- 新增语义 token（控制面板/各面板收口用）----
            "light_grid":       "#D8D2C8",      # 浅色主题网格线/分隔线
            "light_hover":      "#F0EDE8",      # 浅色主题按钮 hover 底色
            "status_busy":      "#C4B85C",      # 连接中/忙碌状态指示（低饱和黄）
            "source_node":      "#7E9BA8",      # 信号流图 source 节点标识色
            # ---- AI 对话面板（浅色方向补充：气泡/输入框/按钮）----
            "light_text_strong":    "#3D3D3D",   # 浅色方向主文字（深灰近黑）
            "light_text_secondary": "#5A5A5A",   # 浅色方向次文字
            "light_panel_bg":       "#FAFAF8",   # 对话区底色
            "light_log_bg":         "#F5F3F0",   # 工具日志底色
            "light_border_subtle":  "#E0DDD8",   # 浅分隔边框
            "light_border_strong": "#DDD8D0",   # 快捷按钮边框
            "light_border_input":   "#D0CCC4",   # 输入框边框
            "light_hover_bg_strong":"#E8E4DE",   # 按钮 hover 加深
            "light_accent_border":  "#B8A88A",   # 输入框 focus 边框
            "light_user_bubble":    "#EDE8E0",   # 用户气泡底
            "light_user_label":     "#6B5B45",   # 用户气泡标签字
            "light_ai_label":       "#4A7A4A",   # AI 气泡标签字
            "light_system_bg":      "#FFF8E8",   # 系统/提示消息底
            "accent_warm":          "#8B7355",   # 发送按钮暖棕
            "accent_warm_hover":    "#7A6449",   # 发送按钮 hover
            # ---- 面板状态指示色 ----
            "status_success":       "#5A8A5A",   # AI 在线/工具成功绿
            "status_warning":       "#C8A040",   # 未配置/警告琥珀
            "status_danger":        "#C05050",   # 错误/不可用红
            # ---- 通用辅助色 ----
            "text_muted":           "#888888",   # 弱化说明文字
            "image_placeholder_bg": "#1b1b1b",   # 图像占位底（近黑卡片）
            # ---- ADS-B 接收站标记 ----
            "station_blue":         "#4F7CAC",   # 接收站标记蓝
            # ---- 天空模式（天文/星座/热力图功能性配色）----
            "constellation_galileo":"#8C6B5B",   # Galileo 星座标识紫棕
            "sky_day_zenith":       "#DCE7EC",   # 白天天顶浅蓝
            "sky_day_ink":          "#3A4550",   # 白天前景文字/网格
            "sky_night_zenith":     "#0E1622",   # 夜晚天顶深蓝
            "sky_night_horizon":    "#1B2A3A",   # 夜晚地平线
            "sky_night_zenith_deep":"#070D18",   # 夜晚天顶最深
            "sky_day_horizon_glow": "#7FA8C8",   # 白天地平线亮蓝
            "sky_night_horizon_deep":"#16233A",  # 夜晚地平线深
            "ground_shadow_night":  "#0A0F18",   # 夜天地剪影（单层旧色，昼/夜各层见下）
            "ground_shadow_day":    "#3A4A5A",   # 昼天地剪影
            "sky_night_glow":       "#6B5B48",   # 夜剪影后暮光辉光带（暖灰）
            "ground_far_night":     "#0C1320",   # 夜远山（退晕最亮）
            "ground_mid_night":     "#070C15",   # 夜树层
            "ground_near_night":    "#03060C",   # 夜房屋近景（最黑）
            "celestial_sun":        "#D9A441",   # 太阳
            "celestial_moon":       "#9AA7B4",   # 月亮
            "heatmap_low":          "#3E5A4A",   # 热力图低
            "heatmap_mid":          "#8A7A3A",   # 热力图中
            "heatmap_high":         "#8A4A3A",   # 热力图高
        }

        # ---- 主题语义色映射（单一来源）----
        # themes.py 的每套主题只从这里取色，不再各自写 hex。键名固定为下面
        # 19 个语义槽位；新增主题只需在 THEME_COLORS 里加一列。
        # 2026-11 可读性修正：default 的正文/次级正文与 dark 的次级正文
        # 都压暗到 ≥4.5:1（见 tests/test_ui_contrast.py 的门禁）。
        self.THEME_COLORS: Dict[str, Dict[str, str]] = {
            # 低饱和浅色（默认主题）
            "default": {
                "bg": "#F5F3EF", "bg_alt": "#FAF8F5", "card": "#FFFFFF",
                # 可读性修正（2026-11）：正文 4.07→5.11:1、次级 2.86→5.14:1
                # 同色相压暗；受影响的 3 张频谱基线按受控流程重建。
                "text": "#4F6B79",
                "text_secondary": "#5F707A",
                "text_disabled": "#A0A0A0",
                "border": "#C8C0B4", "grid": "#D8D2C8",
                # 2026-11-09 WCAG 审计：强调色 2.79、成功色 2.46 都低于
                # 非文本可辨门槛 3:1（图标/描边/状态点用它们时看不见边界）。
                # 按同色相压暗到 3.10 / 3.05，色相与饱和度不动。
                "primary": "#C4845C", "accent": "#BF7A4F",
                "accent_hover": "#B0754E",
                "success": "#599788", "danger": "#B85C5C",
                "button": "#FFFFFF", "button_hover": "#F0EDE8",
                "button_pressed": "#E8E4DD", "button_disabled": "#D8D2C8",
                # 品牌橙作正文底色不足 4.5:1：文字态另取压暗档（fill 仍是原橙）
                "primary_text": "#9A5F33",    # 4.67:1 on bg
                # 2026-11-12：这两个状态色原先只走全局 COLORS，不随主题 remap。
                # 浅底上 warning 2.21:1、danger 4.21:1（bg），连图标级 3:1 都
                # 不够；沿明度压暗、色相与饱和度不动，到正文级 4.5:1。
                "status_warning": "#876B27",  # 4.55:1 on bg
                "status_danger": "#BD4848",   # 4.53:1 on bg
            },
            # 深色
            "dark": {
                "bg": "#1E1E20", "bg_alt": "#252528", "card": "#28282B",
                "text": "#D0D0D0",
                "text_secondary": "#9A9A9A",  # 原 #888888，4.15 → 5.22:1
                "text_disabled": "#555555",
                # 2026-11-09 WCAG 审计：卡片描边 1.30 低于"能看出边界"的 1.5。
                # 提到 #44444A（on card 1.52 / on bg 1.72），明暗关系不变。
                "border": "#44444A", "grid": "#44444A",
                "primary": "#7A9CAC", "accent": "#D4956A",
                "accent_hover": "#C4845C",
                "success": "#6BA89A", "danger": "#D47070",
                "button": "#333336", "button_hover": "#3D3D41",
                "button_pressed": "#2A2A2D", "button_disabled": "#262629",
                "primary_text": "#7A9CAC",
                # 2026-11-12：warning 沿用全局取值（card 上 5.99:1 已达标）；
                # danger 提亮到正文级 4.5:1，色相与饱和度不动。
                "status_warning": "#C8A040",  # 5.99:1 on card
                "status_danger": "#CE7676",   # 4.53:1 on card
            },
            # 高对比（无障碍）
            "high_contrast": {
                "bg": "#000000", "bg_alt": "#111111", "card": "#000000",
                "text": "#FFFFFF", "text_secondary": "#CCCCCC",
                "text_disabled": "#888888",
                "border": "#FFFFFF", "grid": "#444444",
                "primary": "#00BFFF", "accent": "#FF8C00",
                "accent_hover": "#FFA500",
                "success": "#6BA89A", "danger": "#FF4444",
                "button": "#222222", "button_hover": "#333333",
                "button_pressed": "#444444", "button_disabled": "#1A1A1A",
                "primary_text": "#00BFFF",
                # 2026-11-12：warning 在 bg/bg_alt 上均 ≥7.70:1；danger 提亮
                # 到正文级 4.5:1（最严苛背景是 bg_alt #111111）。
                "status_warning": "#C8A040",  # 7.70:1 on bg_alt
                "status_danger": "#C45C5C",   # 4.53:1 on bg_alt
            },
            # CarWith 深色（docs/design_ref/carwith_figma）
            "dark_car": {
                "bg": "#000000", "bg_alt": "#1f1f1f", "card": "#2b2c2f",
                "text": "#ffffff", "text_secondary": "#c7c7c7",
                "text_disabled": "#666666",
                "border": "#585c63", "grid": "#35373c",
                "primary": "#919cac", "accent": "#919cac",
                "accent_hover": "#a8b3c2",
                "success": "#6BA89A", "danger": "#B85C5C",
                "button": "#35373c", "button_hover": "#3d3f44",
                "button_pressed": "#36383a", "button_disabled": "#1f1f1f",
                "primary_text": "#919cac",
                # 2026-11-12：warning 在 card 上 5.69:1 已达标；danger 提亮到
                # 正文级 4.5:1，色相与饱和度不动。
                "status_warning": "#C8A040",  # 5.69:1 on card
                "status_danger": "#CF7B7B",   # 4.50:1 on card
            },
            # Figma 原生深色（.figma/284_1xx…284_6xx 导出稿的取值）
            "figma": {
                "bg": "#000000", "bg_alt": "#1f1f1f", "card": "#1f1f1f",
                "text": "#FFFFFF",
                # 0.78 白叠在 #1f1f1f 上的等效实色；10.58:1
                "text_secondary": "#CFCFCF",
                "text_disabled": "#707070",   # 0.3 白叠卡底（禁用态豁免）
                "border": "#404040", "grid": "#2C2C2C",
                "primary": "#D9D9D9",         # 导出稿的占位灰语法
                "accent": "#D9D9D9",
                "accent_hover": "#E6E6E6",
                # 导出稿无状态色：沿用 Qt 既有语义色（文档登记为 Qt 补充）
                "success": "#6BA89A", "danger": "#B85C5C",
                "button": "#2C2C2C", "button_hover": "#404040",
                "button_pressed": "#4F4F4F", "button_disabled": "#1F1F1F",
                "primary_text": "#D9D9D9",
                # 2026-11-12：warning 在 #1f1f1f 上 6.72:1 已达标；danger 提亮
                # 到正文级 4.5:1，色相与饱和度不动。
                "status_warning": "#C8A040",  # 6.72:1 on #1f1f1f
                "status_danger": "#C96A6A",   # 4.52:1 on #1f1f1f
            },
        }

        # 旧面板仍按 light_* 键名取色：这些别名不另存数值，直接引用
        # THEME_COLORS["default"]，保证同一语义只有一处定义。
        _light = self.THEME_COLORS["default"]
        self.COLORS["light_bg"] = _light["bg"]
        self.COLORS["light_bg_alt"] = _light["bg_alt"]
        self.COLORS["light_card"] = _light["card"]
        self.COLORS["light_text"] = _light["text"]
        self.COLORS["light_border"] = _light["border"]
        self.COLORS["light_accent"] = _light["accent"]

        # Figma 深色 QSS 的取色别名（与深色 QSS 的键一一对应）
        self.FIGMA_DARK: Dict[str, str] = {
            "bg_dark": "#000000",
            "card_1": "#1F1F1F",
            "card_2": "#1F1F1F",
            "card_3": "#2C2C2C",
            "card_4": "#404040",
            "card_hover": "#404040",
            "gray_200": "#939393",
            "gray_300": "#606060",
            "gray_400": "#404040",
            "accent": "#D9D9D9",
            "success": "#6BA89A",
            "danger": "#B85C5C",
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
            # 停靠面板最小宽（基准）。150% 档需要 +27（300→327）才能让
            # 竖排后的书签组不裁字；但写死 327 会把 125% 档的最小窗宽推到
            # 940，1024 屏连 125% 都放不下、被迫降到 100%。所以 +27 只在
            # 150% 生效，由 themes.fit_scale_to_screen() 按选中档动态设置
            # right_tab.setMinimumWidth()。见 gap-layout.md §3.10。
            "min_dock_w":      300,

            # 星象仪形态浮层窄条：外边距 6×2 + 按钮 sizeHint 136 + 边框 2，
            # 再留 2px 余量；用 collapse_strip(36) 会把「退出星象仪」裁掉。
            "planetarium_strip_w": 152,
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
            "analysis_right":    0.40,   # analysis 预设：右天空模式占比
            "focus_side":        0.28,  # focus 预设：右侧控制条占比
            "grid_cell":         0.5,   # grid 四宫格单元格占比
            "spectrum_fft":     0.60,  # 频谱 Splitter：上 FFT 曲线占比
            "spectrum_wf":       0.40,   # 频谱 Splitter：下瀑布图占比
        }

        # ---- 动效 ----
        self.MOTION: Dict[str, object] = {
            "feedback_ms":   150,
            "transition_ms": 300,
            "easing":        "OutCubic",
        }

        # ---- AI 面板行为常量（步骤卡/日志文本截断、示例频率）----
        # 与 desktop/ai_steps.py 的摘要语义配套；业务代码不得再写死这些长度。
        self.AI: Dict[str, object] = {
            "args_preview_chars":   80,   # 步骤卡关键参数预览截断
            "summary_chars":       200,   # 步骤卡结果摘要截断
            "result_preview_chars": 300,  # 工具原始结果预览截断
            "demo_freq_mhz":      98.5,   # 快捷指令/UI 兜底示例频率（FM 广播段示例值）
        }

        # ---- ai-sdr Mini（ESP32-S3）MCP 端点默认值 ----
        # 仅默认值在此收口；运行时可被环境变量 MBDSDR_MCP_HOST / MBDSDR_MCP_PORT
        # 或显式参数覆盖（见 desktop/mcp_protocol.resolve_mcp_endpoint）。
        self.MCP: Dict[str, object] = {
            "default_host": "192.168.4.1",  # ai-sdr Mini AP 默认地址
            "default_port": 81,
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

    def theme_colors(self, name: str = "default") -> Dict[str, str]:
        """取一套主题的语义色（返回副本，主题侧只引用不各自写 hex）。"""
        base = self.THEME_COLORS.get(name, self.THEME_COLORS["default"])
        return dict(base)

    def status_color(self, kind: str, bg: str = "", theme: str = "") -> str:
        """状态色统一入口（status_warning / status_danger / status_success）。

        业务代码只调这里，不直接读 ``COLORS["status_*"]``：
        * 不传 ``bg``：取当前生效主题的 ``THEME_COLORS[...][key]``；主题没这个
          键就回落全局 ``COLORS[key]``（旧主题/新增键时的兜底）。
        * 传 ``bg``：用于不随主题变的异形表面（AI 对话面板是固定深色岛屿）。
          浅色主题的压暗档放到深底上只有 3.2:1，所以在「主题值 + 全局值 +
          其它主题的同名值」里取对该背景对比度最高的一个——候选全是设计过
          的状态色，不新造颜色，也不需要调用点各写一遍 if。
        """
        key = kind if kind.startswith("status_") else f"status_{kind}"
        if key not in self.COLORS:
            raise KeyError(f"未知状态色 token: {key}")
        fallback = self.COLORS[key]
        if not theme:
            try:
                from themes import active_theme_name
                theme = active_theme_name()
            except Exception:
                theme = ""
        base = self.theme_colors(theme).get(key, fallback) if theme else fallback
        if not bg:
            return base
        from themes import contrast_ratio
        candidates = {base, fallback}
        for palette in self.THEME_COLORS.values():
            if key in palette:
                candidates.add(palette[key])
        return max(candidates, key=lambda color: contrast_ratio(color, bg))

    @staticmethod
    def _px_to_pt(px_str: str) -> float:
        """'39px' -> 29.25pt（QSS font-size 用 pt），并按界面字号倍率缩放。

        QSS 不认 em/%，写死的 pt 会让控件无视用户的字号设置。倍率取
        ``themes.font_scale()``（应用字体 / 设计基准），延迟导入避免与
        themes→tokens 的模块级依赖成环。
        """
        try:
            pt = float(px_str.replace("px", "")) * 0.75
        except Exception:
            return 10.0
        try:
            from themes import font_scale
            pt *= font_scale()
        except Exception:
            pass
        return round(pt, 1)

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
        if mode == "figma":
            # .figma 导出稿的值只换色，结构/圆角/字号沿用同一套深色 QSS
            return self._build_dark_qss(self.FIGMA_DARK)
        return self._build_dark_qss()

    # ---- 深色 QSS ----
    def _build_dark_qss(self, palette: Dict[str, str] | None = None) -> str:
        c = palette or self.COLORS
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
    background-color: {c['bg_dark']};
    border: none;
    border-top: 1px solid {c['gray_400']};
}}
QLabel#dockMode {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    font-weight: {self.FONT_WEIGHT['title']};
}}
QLabel#dockNow {{
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    color: rgba(255,255,255,{self.TEXT_ALPHA['primary']});
    font-weight: {self.FONT_WEIGHT['title']};
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
QPushButton#iconButton:pressed {{ background-color: {c['card_hover']}; }}
QPushButton#iconButton:checked {{ background-color: {c['accent']}; }}
QPushButton#recordButton {{
    background-color: {c['accent']};
    color: {c['bg_dark']};
    border: none;
    font-weight: 600;
}}
QPushButton#recordButton:pressed {{ background-color: {c['gray_200']}; }}
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
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled {{
    color: rgba(255,255,255,{self.TEXT_ALPHA['disabled']});
    background-color: {c['card_1']};
}}

QComboBox QAbstractItemView {{
    background-color: {c['card_3']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['secondary']});
    selection-background-color: {c['accent']};
    selection-color: {c['bg_dark']};
    border: none;
}}
/* 下拉项必须「点得中」：默认行高只有 ~20px，中文项挤在一起像列表而不是
   菜单，鼠标点选容易点偏（用户 2026-11-10：为什么必须滚轮才能选？）。
   给足 30px 行高与左右内边距，展开后一眼一条、直接点击即可。 */
QComboBox QAbstractItemView::item {{
    min-height: 30px;
    padding: 4px 10px;
}}
/* 右栏面板选择器（替代被截断的页签条）：整栏唯一的导航入口，要比普通输入框
   更醒目一点，但不能抢内容区的注意力。 */
QComboBox#rightTabNav {{
    background-color: {c['card_1']};
    color: rgba(255,255,255,{self.TEXT_ALPHA['primary']});
    border: 1px solid {c['border']};
    border-radius: {r['card_sm']}px;
    padding: 4px 12px;
    font-size: {self._px_to_pt(self.FONT['body'])}pt;
}}
QComboBox#rightTabNav:hover {{ border-color: {c['accent']}; }}
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
QPushButton:hover {{ background-color: {c['light_hover']}; }}
QPushButton:disabled {{
    color: {self.THEME_COLORS['default']['text_disabled']};
    background-color: {c['light_grid']};
}}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled {{
    color: {self.THEME_COLORS['default']['text_disabled']};
    background-color: {c['light_grid']};
}}

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
