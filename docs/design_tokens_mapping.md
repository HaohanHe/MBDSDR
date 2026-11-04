# `.figma` 设计语言 → Qt 主题令牌映射表

来源：`D:\mbdsdr\.figma\284_119…284_644` 共 30 个 `index.module.scss`
（Figma 单屏导出，画布 1920×1200）。Qt 侧事实源：
`desktop/tokens.py`（`THEME_COLORS` / `RADIUS` / `FONT` / `SPACING` / `SIZE`），
`desktop/themes.py` 只按语义名取值，不再各自写 hex。

## 1. 颜色语义（9 组）

| # | 语义 | `.figma` 取值（出现次数） | Qt 令牌 / 引用点 | 差异 |
|---|------|--------------------------|------------------|------|
| 1 | 页底 | `#000000`（8） | `THEME_COLORS[*].bg`；`COLORS.bg_dark` | default 主题是米白 `#F5F3EF`（浅色方向），深色方向与导出稿一致 |
| 2 | 页底辅 | `#fafafa`（1，渐变端点） | `THEME_COLORS[*].bg_alt` | `dark_car` 取 `#1f1f1f`，比导出稿纯黑底亮一档（Qt 侧为状态栏/工具栏可读性） |
| 3 | 卡片底 | `#1f1f1f`（1） | `THEME_COLORS.dark_car.card = #2b2c2f`；`THEME_COLORS.figma.card = #1f1f1f` | **差异**：旧深色主题中卡提亮到 `#2b2c2f`；新增 `figma` 主题按导出稿 `#1f1f1f` 落地 |
| 4 | 控件底 | `#2c2c2c`（2） | `COLORS.card_3`；`THEME_COLORS.figma.button = #2c2c2c` | **差异**：`dark_car` 控件底为 `#35373c`（更亮一档） |
| 5 | 描边 | 导出稿无显式描边色（靠明度分层） | `THEME_COLORS[*].border`；深色 QSS `gray_400` | **差异**：Qt 深色用 `#585c63`，`figma` 主题用导出稿色板里的 `#404040`；浅色用 `#C8C0B4` |
| 6 | 选中态 | `#d9d9d9`（33，均带 0.1–0.5 透明度） | `THEME_COLORS[*].primary/accent` | **差异**：`dark_car` 用冷蓝灰 `#919cac`；`figma` 主题用占位灰 `#d9d9d9`。浅色方向品牌橙 `#C4845C` 保留 |
| 7 | 主文本 | `#ffffff`（25，100%） | `THEME_COLORS[*].text`；`TEXT_ALPHA.primary=1.0` | 一致 |
| 8 | 次级文本 | 白字 alpha `0.78 / 0.67 / 0.5 / 0.3 / 0.23` | `TEXT_ALPHA`：`secondary/tertiary2/tertiary/quaternary/faint` | 一致；实色主题侧换算等效实色（`figma` 次级 `#CFCFCF`） |
| 9 | 禁用态 | 白字 alpha `0.2` | `TEXT_ALPHA.disabled=0.2`；`THEME_COLORS[*].text_disabled` | 一致；禁用控件另加 `:disabled` QSS（见 §5） |
| 10 | 成功/危险 | 导出稿无 | `THEME_COLORS[*].success/danger` | Qt 补充（沿用既有语义色，`figma` 主题未自造新值） |
| 11 | 渐变 | `linear-gradient(146.96deg,#fafafa→#4f4f4f) @0.2` 叠在 `#1f1f1f` | 无 | **未落位**：Qt 卡片底为纯色，不用渐变（登记，不硬编码） |
| 12 | 阴影 | `0 14px 31px rgba(0,0,0,0.10)` 等 5 层 | 无 | **未落位**：QSS 不表达阴影（登记） |

## 2. 圆角（7 槽位）

| `.figma` 值（次数） | Qt 令牌 | 引用点 | 差异 |
|---------------------|---------|--------|------|
| `24px`（3） | `RADIUS.card_lg` | 大卡片 / 浮动 Dock | 一致 |
| `13px`（8） | `RADIUS.dock_icon` | Dock 图标底 | 一致（导出稿最高频圆角） |
| `8px`（7） | `RADIUS.card_sm` | 小卡片 / 输入框 | 一致 |
| `42px`（2） | `RADIUS.circle_sm` | 圆形小按钮 | 一致 |
| `84px`（1） | `RADIUS.circle_lg` | 圆形大按钮 | 一致 |
| `12px`（1） | `RADIUS.card_md` | 中卡片 | 一致 |
| `50%`（9） | `RADIUS.circle_sm/lg` | 圆形头像/圆点 | 语义等价（Qt 用直径写死，避免百分比圆角） |
| `10px`（2）、`3px`（2） | 无 | 细滑轨 / 小标记 | **未落位**：Qt 滑轨用 `2px`，小标签用 `card_sm`；登记 |

## 3. 字号档位（5 槽位）

| `.figma` 字号/行高（次数） | Qt 令牌 | 引用点 | 差异 |
|----------------------------|---------|--------|------|
| `39px / 52px`（2） | `FONT.title_lg` / `FONT_LINEHEIGHT.title_lg` | 频率大读数 | 一致 |
| `24px / 32px`（5） | `FONT.title` / `…title` | 卡片标题 | 一致 |
| `20px / 26px`（4） | `FONT.body` / `…body` | 正文 | 一致 |
| `42px / 56px`（4） | `FONT.dock` / `…dock` | 底部 Dock 文字 | 一致 |
| `14px`（辅助） | `FONT.small` | 说明文字 | 一致 |
| `48px`、`36px`、`28px`（各 1） | 无 | 大号数字 / 次级标题 / 搜索框 | **未落位**：Qt 侧按就近档位复用（39/24/20），登记 |

字体：导出稿 18 处均为 `MiSans, "PingFang SC", …` 唯一家族；
`themes.get_app_font()` 首选项同为 MiSans，一致。

## 4. 间距 / 尺寸（4 槽位）

| `.figma` 值 | Qt 令牌 | 差异 |
|-------------|---------|------|
| 顶栏 `23px 40px 25px 36px` | `SPACING.topbar` | 一致 |
| 底 Dock `14px 40px` | `SPACING.dock` | 一致 |
| 大卡内边距 `26px 28px` | `SPACING.padding_lg` | 一致 |
| 地图全出血 `59px 280px 68px 36px` | 无 | **未落位**：Qt 地图面板全出血，边距由布局器计算 |

## 5. 落地方式

- **单一来源**：`tokens.THEME_COLORS` 是唯一写 hex 的地方；`themes.py`
  每套主题都调用 `tokens().theme_colors(<name>)`。旧面板仍在用的
  `light_*` 键在 `tokens._build()` 里直接指向 `THEME_COLORS["default"]`。
- **可切换主题**：新增 `figma`（显示名「Figma 深色」），已接入既有
  工具栏主题下拉与「视图 → 主题」菜单；`DEFAULT_THEME` 仍是 `default`，
  不改旧配色，用户手动选到 `figma` 才生效。
- **禁用态**：深色/浅色 QSS 都补了 `QComboBox/QLineEdit/QSpinBox/
  QDoubleSpinBox:disabled` 规则（颜色 + 底色都变）。**刻意不给 QSlider 加
  `::handle` 子控件禁用规则**：Qt 一旦见到该子控件规则就改走 QSS 绘制，
  滑杆尺寸随之变化并挤动频谱区，实测会打穿 3 张布局基线。
- **对比度**：`default` 正文 `#5B7B8C→#4F6B79`（4.07→5.11:1）、次级正文
  `#8A9BA8→#5F707A`（2.86→5.14:1）、`dark` 次级 `#888888→#9A9A9A`
  （4.15→5.22:1）均已修到达标。品牌橙新增 `primary_text` `#9A5F33`
  （4.67:1）只用于「橙色当文字」，填充仍是 `#C4845C`（填充上的白字按
  WCAG 1.4.11 非文本 3:1 核算）。
- **基线受控更新**：`scripts/ui_layout_audit.py --update-baselines
  --reason "<原因>"` 才允许覆盖；每张图打印尺寸/粗细色块/变化像素摘要；
  尺寸或宽高比漂移时**即使带 reason 也拒绝**（`scripts/ui_baseline.py`）。
  本次仅 4 张频谱基线因文字色变化重建，尺寸与几何签名不变。

差异合计：**6 处取值差异（#3/#4/#5/#6/#3 中卡 + 选中态）+ 4 处未落位
（渐变/阴影/10px·3px 圆角/48·36·28 字号）**；其余 23 个槽位取值一致。
