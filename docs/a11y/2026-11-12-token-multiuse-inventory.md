# 令牌多用途清单（2026-11-12 · 只读）

**用途**：以后有人问「为什么不能直接把 `accent` 调暗一点」，用 §5 一句话答。
**边界**：本轮只读，未改任何色值；`themes.py` / `tokens.py` 一字未动。

---

## 0. 先纠正一条前提（实测，不是推断）

本清单的起因是「default 的 `accent` 同时是正文色、又当禁用态底色」。
**实测证明后半句不成立**：

```
token accent           = #BF7A4F
token button_disabled  = #D8D2C8
QPalette.Accent        = #d8d2c8      <- = button_disabled
QPalette.BrightText    = #bf7a4f      <- = accent
QPalette.Shadow        = #b85c5c      <- = danger

'QPushButton:disabled { background-color: palette(accent); }'       -> #efebe5（灰系，disabled 渲染后）
'QPushButton:disabled { background-color: palette(bright-text); }'  -> #e0a37e（橙系）
'QPushButton:disabled { background-color: palette(shadow); }'       -> #df8b8b（红系）
```

（探针：`%TEMP%\w92_probe_accent.py`，PySide6 6.12 / offscreen；disabled 控件渲染会把颜色
向白混合，看色系即可。）

结论：

1. QSS 里的 `palette(accent)` 指的是 **Qt `QPalette.Accent` 角色**，它在
   `themes.py:676` 被绑到 **`button_disabled`**，不是 `accent`。
   所以 6 条禁用底（`themes.py:520/526/552/834/851/870`）用的是
   `button_disabled` `#D8D2C8`，**与 `accent` 无关**。
2. `accent` 进 QSS 的唯一通道是 `palette(bright-text)`（`themes.py:671`），只有 5 处。
3. **真正的「一个值多角色」不是跨角色，而是跨命名空间**：

| 命名空间 | 取值（default 主题下） | 谁在用 | 处数 |
|---|---|---|---|
| `THEME_COLORS[主题]["accent"]` | `#BF7A4F` | 只经 QSS `palette(bright-text)` | 5 |
| `COLORS["accent"]`（全局，不随主题） | `#919CAC` | Python 直接取色 | 32 |

两者同名、不同值、不联动。**「调暗一点」这句话之所以难拍，第一层原因是
没人先说清调的是哪一个。** 第二层才是同一命名空间内的角色混用（§2）。

> 顺带纠正两处旧记录：`accent_hover` 不是 0 引用（`main_window.py:5017`）；
> `button_disabled` 也不是 0 引用（QPalette.Accent + `tests/test_ui_contrast.py:114`
> 的禁用态 1.5:1 断言）。

## 1. 口径

- 角色 = 这处颜色**画在什么上**：正文（`color:`）/ 背景（`background*`）/
  边框（`border*`）/ 图标图元（`QPen`、`QColor` 传入自绘）/ 禁用底（`:disabled`
  的 background）/ 选中底（`:checked`/`:selected`/`selection-background-color`）。
- QSS 只列 `themes.py` 的 `_build_qss()`。`tokens.py:482 to_stylesheet()` 是
  **死代码**（全仓无调用，五套主题均抛 `KeyError('border')`，见
  `themes.py:1202-1206`），其中 `{c['accent']}` 模板不计。
- QSS 实际生效的 `palette()` 声明 **159 处**（脚本口径；不含注释与 docstring 里的
  示例）。Python 侧区分 `THEME_COLORS`（随主题）与 `COLORS`（全局）。
- 行号会随编辑漂移；判定口径同 `2026-11-12-a1-a2-execution-plan.md` §7.1。

## 2. 令牌 → 角色 / QPalette 映射

| 令牌 | QPalette 角色 | 备注 |
|---|---|---|
| `bg` | Window | 页底 |
| `bg_alt` | AlternateBase | 隔行/菜单栏/状态栏底 |
| `card` | Base | 卡片、输入框底；另 ToolTipBase（:688） |
| `text` | WindowText | 另 Text / ButtonText（:686-687） |
| `text_secondary` | PlaceholderText | 次级字 |
| `text_disabled` | LinkVisited | 禁用字（Qt 无禁用字角色，借道） |
| `border` | Mid | 描边；原生框架也当分界线 |
| `grid` | Midlight | 网格/轨道/浅描边 |
| `primary` | Highlight | 选中底；HighlightedText 由 `_on_primary()` 推导（:692） |
| `primary_text` | Link | 链接色，兼焦点环/单选点 |
| `accent` | **BrightText** | 强调填充 |
| `danger` | Shadow | 危险色；原生滚动条手柄也取 Shadow（:1010 注释） |
| `button` / `button_hover` / `button_pressed` | Button / Light / Dark | 按钮三态底 |
| `button_disabled` | **Accent**（Qt 6.6+；老版本退 Shadow） | 禁用底，`themes.py:634-636/676` |
| `accent_hover` | 无 | 只被 `main_window.py:5017` 读 |
| `success` / `status_warning` / `status_danger` | 无 | Python 直接取色 / `status_color()` |

## 3. 逐个令牌清单

### 3.1 `text` — QSS 31，全是字；**角色单一，面积最大**

QSS 31（全 `color:`）：378, 384, 414, 472, 498, 556, 565, 572, 787, 798, 804, 810,
818, 830, 858, 879, 936, 952, 980, 1043, 1049, 1053, 1073, 1082, 1088, 1096,
1104, 1108, 1117, 1129, 1133。

- Qt 侧另有 Text / ButtonText（:686-687）；禁用三态改走 `text_disabled`（:695-696）。
- Python 2：`main_window.py:3884`（`_tok('text')`，全局 `#FFFFFF`，当字）、
  `main_window.py:5523`（主题 text，传入频谱 `text` 参数）。
- **判定：必然**——一个「正文」语义，三处 Qt 角色必须同值。

### 3.2 `accent` — 两个值、两个命名空间，38 处

**A. 主题 `accent`（default `#BF7A4F`）经 `palette(bright-text)`，5 处**

| 行 | 角色 | 用途 |
|---|---|---|
| 838 | 背景 | 录音按钮常态底 |
| 840 | 边框 | 录音按钮描边 |
| 844 | 背景 | 录音按钮悬停底 |
| 904 | 背景 | 滑块手柄水平悬停底 |
| 923 | 背景 | 滑块手柄垂直悬停底 |

白字压在这 5 处的底上是 3.44:1（`tests/test_ui_contrast.py:120` 有意锁在
3.0–4.5，按非文本 3:1 口径，文字场景另给了 `primary_text`）。

**B. 全局 `COLORS["accent"]`（`#919CAC`，不随主题），Python 32 处**

- 字 2：`status_panel.py:865`、`:1016`（RSSI 中等档；在浅底上 2.51:1）
- 背景 4：`status_panel.py:857`（电平条填充）、`rf_sky_view.py:2017`（按钮底）、
  `:2077`（按钮选中底）、`vfo_panel.py:191`（行强调底）
- 边框 1：`ai_panel.py:504`（输入框焦点边）
- 图标/图元 25：`adsb_map_panel.py:76`、`freq_display.py:216`、`module_panel.py:71`、
  `new_spacetime_panel.py:115`、`right_column.py:96`、`rf_sky_view.py:2117, 2139,
  2146, 2151, 2156, 5581, 6086, 6189, 6202, 6932, 7020, 7032, 7071, 7076, 7502,
  7940, 8075, 8967`（`light_accent`/`accent` 二选一）

**C. 主题 `accent` 的 Python 读取，1 处（且是死参）**

- `main_window.py:5525` 把 `theme.colors["accent"]` 传给
  `spectrum.set_theme_colors(..., marker, ...)`，而该函数的 `marker` 形参在函数体
  里**没有被使用**（`spectrum_widget.py:3317-3323`）。

**判定**
- 5 处 QSS：录音按钮底 = 滑块悬停底，两者都是「品牌强调面」，**同语义**；
  但「强调面」与「强调墨」本应是两个值（一个要压白字、一个要压浅底），
  现在没拆——**被迫**。
- 32 处 Python：同一个 `#919CAC` 既当字、又当按钮底、又当 25 处图元——**被迫**，
  且因为不随主题，它在深色主题下反而"合适"、在浅色主题下独自不达标。

### 3.3 `border`（Mid）— QSS 24 + Python 2

- QSS 边框 23：379, 400, 507, 531, 557, 792, 812, 819, 859, 880, 937, 953, 960,
  967, 985, 989, 1037, 1054, 1070, 1083, 1097, 1109, 1120
- QSS 禁用底 1：**932**（滑块手柄 `:disabled` 的填充）
- Python 边框 1：`main_window.py:3880`；Python **网格线 1**：`main_window.py:5522`
  把主题 `border` 传给 `set_theme_colors` 的 `grid` 形参（频谱网格线）
- 另：原生分隔线/框架也取 Mid。

**判定**：描边 23 处**必然**（同一「边界」语义）；932 与 5522 是**被迫**——
描边色被拿去当填充和网格线。

### 3.4 `primary`（Highlight）— QSS 18 + Python 3 + 推导

- 边框 8：479, 486, 512, 516, 536, 573, 826, 865（其中 866/865 等为 hover 边）
- 背景 8：515, 582, 892, 899, 918, 1004, 1030, 1077（含进度 chunk、滑块已选段）
- 选中底 2：881, 1112（`selection-background-color`）
- Python：`main_window.py:5016`（连接按钮底）、`:5524`（频谱谱线 `line`）、
  `:4997`（提示横幅描边的兜底）
- 推导：`HighlightedText` 由 `_on_primary(c)` 按对比度自动挑（`themes.py:619-631, 692`）

**判定**：选中底 + 选中字是**必然成对**，但**不是同一个值**——底用 `primary`，
字用推导色或 `card`（881/882、1112/1113）。hover 边框复用 primary 是**半被迫**：
hover 语义与选中语义不同，但用同一强调色可接受。

### 3.5 `danger`（Shadow）— QSS 2 + Python 23 + 原生滚动条

- QSS 选中底 1：847（录音按钮 `:checked` 底）；QSS 边框 1：848
- Python 23：
  - 底 3：`control_panel.py:999, 1007`（无效输入红底）、`main_window.py:10240`
  - 字 9：`main_window.py:2689, 3444, 11887`、`sat_track_panel.py:522`、
    `status_panel.py:360, 867, 965, 1019`、`vfo_panel.py:232`
  - 图标/指示灯 6：`rf_sky_view.py:4682, 5585, 5589, 5686`、`ntrip_panel.py:48`、
    `new_spacetime_panel.py:117`
  - 图例 2：`rf_sky_view.py:2141, 2152`
  - 边框 1：`main_window.py:4997`（提示横幅描边）
  - 指示灯 2：`sat_track_panel.py:453, 518`
- 另：Fusion 原生滚动条手柄取 Shadow（`themes.py:1010`）

**判定**：红字/红底/红灯是**必然**同族；但「原生滚动条手柄」是 Qt 取 Shadow 的
默认行为——**被迫**，调 `danger` 会顺带改滚动条。

### 3.6 `card`（Base）— QSS 17 + Python 2

- 背景 14：399, 555, 791, 811, 857, 878, 935, 951, 962, 975, 1036, 1052, 1095, 1107
- **强调底上的字 1**：839（录音按钮文字）
- 选中字 2：882, 1113（`selection-color`）
- 另：ToolTipBase（:688）；Base 还是所有面板的默认兜底底，实际面积远大于 17。
- Python：`weather_panel.py:547`（底）

**判定**：14 处「面」**必然**；839/882/1113 是**被迫**——把「面」当「墨」。
浅色主题下 `card=#FFFFFF` 压在 `accent` `#BF7A4F` 上只有 3.44，这正是
「一个值要同时当底和当字」的直接后果。

### 3.7 `grid`（Midlight）— QSS 13 + Python 1

- 边框 7：521, 525, 551, 835, 853, 871（均 `:disabled`）、1119（表头分隔）
- 背景 5：579（分隔条）、886, 911（滑块轨道）、928（滑块禁用已选段）、1069（进度条底）
- 网格线 1：1111（`gridline-color`）
- Python 1：`ai_panel.py:173`（`COLORS.get("grid")`，但 `COLORS` 顶层没有 `grid`，
  **恒走兜底** `card_3`）

**判定**：网格线 1111 **必然**；521/525/551/835/853/871 是**被迫**（网格色当
禁用描边）；886/911/928/1069 也是**被迫**（网格色当轨道/进度底）。

### 3.8 `button_disabled`（Accent）— QSS 6，单用途

520, 526, 552, 834, 851, 870，全部是 `:disabled` 背景。
**这是本清单里唯一一个「已正确拆分」的令牌**：禁用面独立，禁用字独立
（`text_disabled`），两者对比度 1.74:1（`tests/test_ui_contrast.py:111`）。
Python 0 处直接读取。

### 3.9 其余

| 令牌 | QSS 处数 | 角色 / 行号 |
|---|---|---|
| `bg` | 5 | 背景 560, 786, 965, 994, 1020；Python 1（`main_window.py:5521`，频谱底） |
| `bg_alt` | 7 | 背景 377, 983, 988, 1065, 1081, 1116, 1132；Python 1（`main_window.py:3879`）+ 主题 1（`:4995`，提示横幅底） |
| `text_secondary` | 5 | 字 576, 966, 984；**滚动条手柄底 999, 1025**；Python 1（`main_window.py:4996`，横幅字） |
| `text_disabled` | 6 | 字 424, 489, 502, 833, 852, 869（全禁用语境） |
| `button` | 3 | 背景 509, 533, 817 |
| `button_hover` | 7 | 背景 387, 458, 478, 568, 825, 1091, 1103 |
| `button_pressed` | 5 | 背景 391, 482, 485, 571, 829 |
| `primary_text` | 5 | 边框 547, 948, 1060；底 548（单选实心圆）；字 976（tab 选中） |
| `accent_hover` | 0 | 仅 `main_window.py:5017`（连接按钮 hover 底） |
| `success` | 0 | Python 20（全取全局 `#6BA89A`）：字 8 / 图元图标 9 / 边框 1 / 底 2 |
| `status_warning` | 0 | Python 8（`status_color()` 7 + `main_window.py:5273` 直读 1） |
| `status_danger` | 0 | Python 4（全经 `status_color()`） |

### 3.10 「跟随主题」与「固定取全局」的分布（本清单最该记住的一页）

同名键在两套字典里各有一份；**Python 绝大多数直接取全局那份**：

| 令牌 | 跟随主题的引用 | 固定取全局的引用 |
|---|---|---|
| `accent` | QSS 5 处（`palette(bright-text)`）+ 1 处死参 | **32 处**（2 字 / 4 底 / 1 边框 / 25 图元） |
| `danger` | 1 处（`main_window.py:4997` 提示横幅描边） | **22 处**（3 底 / 9 字 / 6 图元 / 2 图例 / 2 灯） |
| `success` | **0 处** | **20 处** |
| `primary` | 3 处（`main_window.py:4997/5016/5524`） | 0 |
| `text` / `bg` / `bg_alt` / `border` / `card` / `text_secondary` | 各 1–2 处（`main_window.py:3884/3879/3880/4995/4996/5521-5523`） | 各 0–1 处 |

两条推论：

1. **`THEME_COLORS[*]["success"]` 在运行时没有任何读取点**（只有门禁测试读它）。
   所以「改 default 的 `success` 键」不会改变界面上任何一处绿字——那 6 处字读的是
   全局 `#6BA89A`。这一条直接影响 FZ-04 决策单第 3 条的 (c) 选项，已在
   `docs/pending-patches/2026-11-12-fz04-unfreeze-request.md` 追加复核更正。
2. `accent` / `danger` / `success` 的「配色决定」目前其实落在**全局字典**上；
   主题切换只改 QSS 与少量内联重刷点。这也是「换个主题，状态色几乎不变」的原因。

`success` 20 处的行号（已剔除 `payload.get("success")` 这类布尔键），按角色分：

- 字 8：`main_window.py:3449, 5027`、`status_panel.py:863, 959, 1013, 1049, 1093`、
  `sat_track_panel.py:528`
- 图标/图元 9：`main_window.py:11869`（状态灯 `●`）、`constellation_panel.py:439`、
  `module_panel.py:79`、`rf_sky_view.py:2119, 2140, 2153`、`ntrip_panel.py:50`、
  `new_spacetime_panel.py:116`、`sat_track_panel.py:526`（仰角条）
- 边框 1：`ai_panel.py:177`（AI 气泡描边）
- 底 2：`rf_sky_view.py:2037`（按钮选中底）、`status_panel.py:855`（电平条填充）

> `status_panel.py:1013` 是 `success` 字、`:1019` 是 `danger` 字；上一轮 A2 清单把
> 两处都写成了「绿」，以本清单为准。

## 4. 必然 vs 被迫

**必然（同一语义，必须同值或必须成对）**

| 组合 | 理由 |
|---|---|
| `text` = WindowText + Text + ButtonText | 三处都是「正文」，Qt 要求一致 |
| `card` = Base + ToolTipBase | 都是「面」 |
| `border` 23 处描边 | 都是「边界」 |
| `primary` 选中底 + 推导 HighlightedText | 成对，但**不同值**，由 `_on_primary()` 保证可读 |
| `primary_text` 焦点环 + 单选实心圆（547/548, 948, 1060） | 有意为之；注释 542-545、942-946 写明 highlight 仅 2.79 不满足 3:1 |
| `danger` 字/底/灯 | 同一「危险」语义 |
| `button_disabled` + `text_disabled` | 禁用面 + 禁用字，已正确拆分 |
| `accent` 录音底 + 滑块悬停底 | 都是「品牌强调面」 |

**被迫（只是碰巧共用一个值，语义不同）**

| 复用 | 为什么是问题 |
|---|---|
| 全局 `COLORS["accent"]` 当字（`status_panel:865/1016`）+ 当按钮底（`rf_sky_view:2017/2077`）+ 当 25 处图元 | 一个值要同时满足「压浅底 ≥4.5」和「压白字 ≥4.5」，物理上做不到；现在它两边都不达标（字 2.51、白字 2.78） |
| `accent` 5 处既是「强调墨」又是「强调面」 | 调暗利于白字，调亮利于图标可辨，方向相反 |
| `card` 当「强调底上的字」（839/882/1113） | 面当墨；浅主题白字压橙 3.44 |
| `border` 当滑块禁用手柄底（932） | 描边当填充 |
| `border` 当频谱网格线（`main_window:5522` 传进 `grid` 形参） | 描边当网格 |
| `text_secondary` 当滚动条手柄底（999/1025） | 字色当填充 |
| `grid` 当滑块轨道/进度底（886/911/928/1069）与禁用描边（521/525/551/835/853/871） | 网格色被当控件面与边界 |
| `danger`（Shadow）当原生滚动条手柄 | Qt 默认行为，不是设计 |
| `primary` 当 hover 边框（8 处） | hover 与选中是两种状态 |

## 5. 影响面前五（改一个值会连带改几处）

| # | 令牌 | 处数 | 单独调它会连带改到什么 |
|---|---|---|---|
| 1 | `text` | 31 QSS + 3 Qt 角色 + 2 Python = 36 | 所有未单独设色的字、原生 Text/ButtonText、频谱坐标字。角色单一，**可以安全单独调**；风险在面积不在语义 |
| 2 | `accent` | 5 QSS（主题）+ 32 Python（全局）+ 1 死参 = 38 | 调**主题**那个：录音按钮底/边/悬停、滑块悬停，白字 3.44 跟着变；调**全局**那个：2 处 RSSI 字、4 处填充（2 按钮 / 1 行强调 / 1 电平条）、1 处焦点边、25 处图元，且与主题无关 |
| 3 | `border` | 24 QSS + 2 Python + 原生分隔线 | 23 处描边、滑块禁用填充、频谱网格线；调深全界面轮廓变硬 |
| 4 | `danger` | 2 QSS + 23 Python + 原生滚动条 | 录音「停止中」底/边、3 处红底、9 处红字、8 处图元/图例、2 处灯，外加**滚动条手柄** |
| 5 | `primary` | 18 QSS + 3 Python + 推导文字 | 8 处 hover 边、8 处填充（含进度条）、2 处选中底、连接按钮底、频谱谱线，外加**自动跟随的选中文字** |
| 6 | `card` | 17 QSS + 2 Python + Base 兜底 | 全部面板/卡片/输入框底 + 3 处「强调底上的字」；**面积第一，处数第六** |
| 7 | `success` | 20 Python | 8 处状态字、9 处图元/指示灯、1 处描边、2 处底；不随主题（全局 `#6BA89A`） |

> 「处数」不等于「面积」：`card` 是 Qt 的默认兜底底，`text` 是默认兜底字，
> 实际覆盖面远大于表里的数字。真正的「改一处炸全场」就是这两个。

## 6. 一句话答案（以后照这句说）

> **`accent` 不是「一个值」，是两个同名不同值的值：** QSS 里的 `accent`
> 是主题色（default `#BF7A4F`），只经 `palette(bright-text)` 出现 5 处，
> 全是录音按钮和滑块悬停的底；Python 里的 `COLORS["accent"]` 是全局
> `#919CAC`，出现 32 处，**根本不随主题变**，而且同一份值既当字又当按钮底
> 又当 25 处图元。所以「调暗一点」先要回答调哪个：调主题那个只动 5 处
> （白字 3.44 会一起变）；调全局那个会动 2 处 RSSI 字、4 处填充、1 处焦点边
> 和 25 处图元。**至于「禁用底」，早在 A′ 就独立成了 `button_disabled`
> （Qt Accent 角色），跟 `accent` 没有任何关系。**

## 7. 方法 / 复跑

- QSS：逐行拆声明，按属性名归角色，再匹配 `palette(<role>)`，经
  `themes.py:660-677` 的映射表换成令牌；注释与 docstring 里的示例不计。
- Python：正则扫 `COLORS["x"]` / `COLORS.get("x")` / `_tok('x')` /
  `.colors["x"]` / `_colors["x"]` / `c.get("x")` / `status_color("x")`；
  布尔键（`payload["success"]`）与 `info.get("text")` 这类同名非颜色键已剔除。
- 复跑：`%TEMP%\w92_inv.py`（角色扫描）、`%TEMP%\w92_probe_accent.py`
  （QSS 角色实测）、`%TEMP%\w92_contrast.py`（对比度）。三个都只读、不入库。
