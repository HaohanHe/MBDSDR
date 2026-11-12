# 已落地视觉改动清单（冻结期，待追认或回退）

> 用途：FZ-04「颜色/样式一律暂停」期间，本线（W92）已有视觉改动落库。本页把每一处摊开，
> 供用户逐条追认或回退。**本页是只读产物，写它没有改任何代码。**
> 生成时间：2026-11-12 20:5x（`wip/main-1111`）。
> 判据：本线 11-12 落库的全部视觉相关提交；对比度均为 WCAG 公式实测（正文 4.5:1、非文本 3:1），
> 半透明值先按真实叠底合成再算，不用字面色。

## 0. 一页结论

| 提交 | 时间 | 性质 | 是否改主题令牌 | 影响面 |
|---|---|---|---|---|
| `13bbb149` | 11-12 19:38 | 状态色按主题补槽位 | **是**（新增键，不改旧键） | AI 面板/卫星/PTT 等状态文字 |
| `fa49baf2` | 11-12 19:43 | 状态色改走 `status_color` | 否（取色路径） | `ai_panel` 9 处 |
| `1b546a98` | 11-12 20:11 | 星座面板说明字改走主题 | 否 | `constellation_panel` 1 处 |
| `5a405bd1` | 11-12 20:21 | AI 面板标题栏/状态条改走 palette 角色 | 否 | `ai_panel` 5 处 |
| `96e5ff2c` | 11-12 20:38 | 非文本 3:1：border/焦点/单选/两个岛 | **是**（改 4 套主题 border） | 全仓输入框/勾选框/单选/下拉框等 |

非视觉或仅代码入口，供参考：`5b5b4d61`（记录当前主题名）、`134bf648`（`status_color` 入口）。
11-11 的 `44890b99`（滚动条手柄 3:1）不在今天这批。

**回退独立性**：四笔各自独立，可单独 revert；只有 `96e5ff2c` 同时带走
`tests/test_ui_contrast.py` 里对应的 5 条门禁（回退后门禁一起消失，是预期的）。

## 1. `13bbb149` + `fa49baf2`：状态色跟主题

| 令牌 | 主题 | 改前 | 改后 | 承载的控件 |
|---|---|---|---|---|
| `status_warning` | default | 全局 `#C8A040`，浅底 **2.21:1** | `#876B27`，浅底 4.55 / 卡片 5.04 | AI 状态标签、步骤卡、工具卡、TLE 日期行、PTT 提示 |
| `status_danger` | default | 全局 `#C05050`，浅底 **4.21:1** | `#BD4848`，浅底 4.53 / 卡片 5.02 | 同上（失败/告警） |
| `status_warning` | dark / HC / car / figma | 全局 `#C8A040` | `#C8A040`（深色主题原值就对，保持不变） | 同上 |
| `status_danger` | dark / HC / car / figma | 全局 `#C05050` | `#CE7676` / `#C45C5C` / `#CF7B7B` / `#C96A6A`（4.50–5.13） | 同上 |

- 改的是**新增同名键**（每套主题各持一份），`COLORS` 里的旧键不动；`fa49baf2` 只把 9 处读取
  改成 `status_color(kind)`，并按"压在深底还是浅底"选值。
- 影响控件见上表；深色四套主题的观感基本不变（同值或同族近似）。
- 反证：`tests/test_ui_contrast.py::test_status_colors_readable_on_theme`（逐主题、逐令牌，
  最严背景上 <4.5 即红）；把任一主题改回全局值 → default/dark 各对应参数化用例红。

## 2. `1b546a98`：星座面板 SDR 层说明字

| 位置 | 改前 | 改后 | 影响控件 |
|---|---|---|---|
| `desktop/constellation_panel.py:569` | 字面量 `#8A93A6`，default 底 **2.79:1** | `palette(placeholder-text)` = 主题 `text_secondary`，default 4.64 | 只影响那行说明字 |

反证：`tests/test_ui_contrast.py::test_constellation_sdr_hint_uses_theme_secondary`；
改回 `#8A93A6` → 红。

## 3. `5a405bd1`：AI 面板标题栏 / 常驻状态条 / 状态详情

改前（浅色 default，半透明底按合成值算）：

| 控件 | 改前字色 / 底 | 对比度 | 改后 |
|---|---|---|---|
| 标题 `title_label` | `#FFFFFF` on 面板底 `#F5F3EF` | **1.11** | `palette(window-text)` → 5.11 |
| 内核状态 `ai_status_label` | `#888888` 同底 | **3.20** | `palette(placeholder-text)` → 4.64 |
| 弹出按钮 `pop_btn` | `#B9C7DB` 同底 | **1.55** | `placeholder-text` + `mid` 描边 → 4.64 |
| 常驻状态条 `live_btn` | `#CFE0F5` 叠 `rgba(60,90,130,0.16)`（合成 `#D7DBDE`） | **1.04** | `alternate-base` 底 + `window-text` → 5.34 |
| 状态详情 `live_details` | `#B8C6D9` 叠 `rgba(14,17,24,0.55)`（合成 `#767779`） | **2.59** | `alternate-base` 底 + `placeholder-text` → 4.85 |

- **对话区 `#1f1f1f` 及其气泡 `#232528`/`#241f16` 一处未动**，那是已声明的固定深色岛屿。
- 反证：`tests/test_ui_contrast.py::test_ai_panel_header_follows_theme`；五处任一改回原值 → 红。

## 4. `96e5ff2c`：非文本 3:1（**改了主题令牌，最需要追认的一笔**）

### 4.1 `border` 令牌（`tokens.py`，四套主题）

| 主题 | 改前 | on card / bg / bg_alt / button | 改后 | on card / bg / bg_alt / button |
|---|---|---|---|---|
| default | `#C8C0B4` | 1.80 / 1.63 / 1.70 / 1.80 | `#918B82` | 3.38 / 3.05 / 3.19 / 3.38 |
| dark | `#44444A` | 1.52 / 1.72 / 1.58 / **1.30** | `#7D7D81` | 3.59 / 4.06 / 3.73 / 3.07 |
| dark_car | `#585c63` | 2.08 / 3.13 / 2.45 / 1.77 | `#7E8186` | 3.57 / 5.37 / 4.22 / 3.05 |
| figma | `#404040` | 1.59 / 2.03 / 1.59 / 1.35 | `#757575` | 3.58 / 4.56 / 3.58 / 3.03 |
| high_contrast | `#FFFFFF` | 15.91–21.00 | 不变 | — |

**影响控件**：输入框、勾选框、单选框、下拉框、数值框的边界（它们填充与底色只差 1.0–1.2，
描边是唯一轮廓）；连带卡片/分组框/标签栏/工具栏/状态栏的 1px 描边一起变深
（同一令牌兼任容器描边，已写进注释）。
**视觉后果**：浅色主题所有描边明显加深；深色主题勾选框描边明显提亮。

### 4.2 `themes.py` 两条 `:focus` + 单选选中（`QLineEdit` / `QSpinBox` / `QDoubleSpinBox` / `QRadioButton`）

| 位置 | 改前 | 改后 | 影响 |
|---|---|---|---|
| `QLineEdit:focus` | `palette(highlight)`，default 2.79（页底） | `palette(link)` = primary_text，≥4.67 | 文本输入焦点框 |
| `QSpinBox:focus` / `QDoubleSpinBox:focus` | 同上 | 同上 | 数值框焦点框 |
| `QRadioButton::indicator:checked` | highlight 实心圆，default 2.79 | `palette(link)` 实心圆，≥4.67 | 单选选中圆点（无对勾，圆点即状态） |

**视觉后果**：default 主题焦点框由品牌橙 `#C4845C` 变成压暗档橙 `#9A5F33`；其余四套主题两者同值，观感不变。

### 4.3 两个固定深色岛屿（非主题令牌）

| 文件 | 改前 | 改后 | 影响 |
|---|---|---|---|
| `capability_drawer.py:44` | 单元格边框 `rgba(180,196,220,0.18)`（合成 1.51） | `0.50`（3.39 / 3.18）；hover `0.45`→`0.70` | 能力抽屉每个可点单元格的轮廓 |
| `location_panel.py` | 控件边界 `_LINE #333845`（1.42）；列表选中底 `rgba(90,140,255,0.35)`（1.75） | 新增 `_EDGE #6A7284`（3.45）；选中底 `0.70`（3.26） | 定位面板输入框/列表/按钮边界、选中行 |

反证（以上 4.1–4.3 全部）：把四个产品文件整笔 stash 回父提交 → `tests/test_ui_contrast.py`
**8 failed**（4 条 border 参数化 + 焦点环 + 单选 + 抽屉 + 定位面板）；恢复后 76 passed。

## 5. 回退方法（按需选其一）

```powershell
# 只回退非文本这一笔（含其门禁）
git revert --no-commit 96e5ff2c
# 只回退 AI 面板那笔
git revert --no-commit 5a405bd1
# 只回退星座说明字
git revert --no-commit 1b546a98
# 状态色两笔要一起退（入口 + 调用点）
git revert --no-commit fa49baf2 13bbb149
```

⚠️ `96e5ff2c` 动的是 `tokens.py` / `themes.py`，回退等于在冻结期内再改一次主题令牌，
同样需要授权。

## 6. 需要用户决定的四件事

1. `13bbb149` + `fa49baf2`（浅色主题状态文字 2.21/4.21 → 4.53/4.55）：追认 / 回退？
2. `1b546a98`（2.79 → 4.64）：追认 / 回退？
3. `5a405bd1`（AI 面板浅色 1.04–3.20 → 4.64–5.34，对话区未动）：追认 / 回退？
4. `96e5ff2c`（主题 border 令牌 + 焦点/单选 + 两个岛）：整体追认 / 只追认岛屿部分 / 全退？
