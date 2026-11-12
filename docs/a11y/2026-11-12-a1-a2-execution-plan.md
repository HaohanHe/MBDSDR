# A1 / A2 解冻后执行计划（按文件分组）

> 前置：用户对 `2026-11-12-landed-visual-changes.md` 的追认/回退答复，以及解冻放行。
> 本文档只读，未改任何代码（`themes.py` / `tokens.py` 未动）。
> 定义：**A1 = 只换取值路径，不产生配色决定**；**A2 = 必须先有一次拍板**。
> 对比度口径：状态**文字** 4.5:1，边框/图形/轨道 3:1；半透明先合成再算。
> 所在表面：右栏普通 QWidget 底 = `palette(window)` = 主题 `bg`（浅色 `#F5F3EF`）；
> 若某块实际落在卡片上，按 `card`（`#FFFFFF`）计。下表取**最差**那个。

## 0. 先纠一处计数

上轮口头报「A1 的 12 处 `status_color()`」，逐行复核为 **10 处**；做棘轮基线时又发现
`sat_track_panel.py:453` 也是直读（同函数 :450 已走 `status_color("warning")`，:453 仍读
`COLORS["danger"]`）——**最终是 11 个单元 / 12 行**。
`control_panel.py:999/1007` 是同一输入框的两条错误分支，算 1 个单元（之前按 2 处算过）。

11 个单元：`status_panel` 4（danger 文字）、`sat_track_panel` 3（453/518/522 danger）、
`vfo_panel` 1（danger 文字）、`ntrip_panel` 2（48 danger + 49 busy→warning）、
`control_panel` 2 行 = 1 个单元（danger 底）。

## 1. A1 清单（按文件；改什么 / 由哪个 token 决定 / 影响主题 / 反证）

### 1.1 `status_panel.py`（4 处 danger 文字）

| 行 | 现在 | 改成 | 决定取值 | 影响主题 |
|---|---|---|---|---|
| 360 | `COLORS["danger"]`（16pt 错误字） | `status_color("danger")` | default `#BD4848` 4.53(bg)/5.02(card)；dark `#CE7676` 4.53；HC `#C45C5C` 4.53；car `#CF7B7B` 4.50；figma `#C96A6A` 4.52 | 浅色实质变（4.45→5.02）；深色同族提亮 |
| 867 | 同上（RSSI 弱信号） | 同上 | 同上 | 同上 |
| 965 | 同上（连接失败） | 同上 | 同上 | 同上 |
| 1019 | 同上（弱信号） | 同上 | 同上 | 同上 |

- 反证：`tests/test_ui_contrast.py::test_no_new_direct_semantic_color_reads`（棘轮，
  `2c66b2b7` 已落地；基线与 `_BASELINE_CEILING` 见 §4）；任一改回 `COLORS[...]` 必红。
- 现有门禁 `test_status_colors_readable_on_theme` 保证换上的值 ≥4.5，不需要改它。

### 1.2 `status_panel.py`（3 类 gray：一致性，非对比度）

| 行 | 现在 | 改成 | 现值是否 <4.5 |
|---|---|---|---|
| 415/825/833/841/859 | `COLORS["gray_300"]`（电平条填充/文字） | `palette(placeholder-text)` | 否：6.29(card)/5.67(bg) |
| 945-946 | `gray_400` 描边 + `card_2` 轨道 | 归 A2（轨道） | 轨道 chunk 2.22（与主题无关） |

### 1.3 `sat_track_panel.py`

| 行 | 现在 | 改成 | 现值 |
|---|---|---|---|
| 453 / 518 / 522 | `COLORS["danger"]` | `status_color("danger")` | **4.45(card)/4.01(bg)，擦边** |
| 492 | `COLORS["gray_300"]` | `palette(placeholder-text)` | 6.29/5.67，达标 |
| 552-556 | `gray_400` + `card_2` + chunk | 归 A2（轨道） | 2.22 |

### 1.4 `vfo_panel.py`（最严重：1.23:1）

| 行 | 现在 | 改成 | 影响 |
|---|---|---|---|
| 191-194 | `accent` 35% 底 + `text_primary` 白字（主听行） | `palette(highlight)` + `palette(highlighted-text)` | 白字 1.23 → 角色保证值；五套主题都修 |
| 199-208 | `card_2` 35% 底 + `gray_200` 字（普通行） | 不设底色（用表默认 `palette(base)`）+ `palette(text)` | 1.93 → 主题正文值 |
| 232 | `COLORS["danger"]`（「删」按钮） | `status_color("danger")` | 4.45/4.01 → ≥4.5 |

- 反证：新增 `test_vfo_row_tint_uses_palette_roles`（源码断言 `setBackground` 只接受
  `palette(...)` 角色，出现 `COLORS[` 即红）。

### 1.5 `ntrip_panel.py`（1 处 + 求值时机）

| 行 | 现在 | 改成 | 现值 |
|---|---|---|---|
| 47-49 | 模块级 `COLORS["danger"/"status_busy"/"success"]`，导入时求值 | 收进 `_indicator_colors()`，调用时求值；danger→`status_color("danger")`，busy→`status_color("warning")`，**success→被 A2 卡住** | busy **2.03(card)/1.83(bg)，严重**；danger 4.45/4.01 擦边 |
| 求值时机 | `_COLOR_*` 常量永不随主题更新 | 同上改造 | 零视觉变化，纯 bug |

- 反证：新增 `test_ntrip_indicator_colors_follow_active_theme`（import 后切主题，
  两次取值必须不同；改回模块级常量必红）。
- 注意：success 未定之前，这个文件的「已连接」点仍停在 2.73/2.46，不能整文件宣布修完。

### 1.6 `control_panel.py`（1 个控件、2 行）

| 行 | 现在 | 改成 | 现值 |
|---|---|---|---|
| 999 / 1007 | 只设 `background-color: COLORS["danger"]` | 底 `status_color("danger")`，**并显式给前景** | 主题文字压 `#B85C5C` 只有 **1.27:1**（default）、2.88:1（dark）；白字 4.45 |

- 这一处的「前景取白还是 `palette(bright-text)`」需要一次确认（见 A2 第 4 行），
  确认后才算 A1；否则整条留在 A2。

## 2. A1 里现值已低于 4.5 的点位（单独标）

**严重（<3:1，必须最先修）**

| 点位 | 现值 | 底色 |
|---|---|---|
| `vfo_panel.py:191-201` 主听行/普通行 | **1.23 / 1.93** | 半透明 accent/card_2 叠在浅色表底上 |
| `ntrip_panel.py:48` busy 指示灯 | **2.03 / 1.83** | card / bg |
| `control_panel.py:999/1007` 红底上的主题文字 | **1.27**（default）/ 2.88（dark） | danger 底 |

**擦边（4.0–4.5，只差一点但仍在正文阈值下）**

| 点位 | 现值 | 说明 |
|---|---|---|
| danger 文字：`status_panel:360,867,965,1019`、`sat_track:453,518,522`、`vfo:232` | **4.45**（card）/ **4.01**（bg） | 4.45 离 4.5 只差 0.05；换上 `status_danger` 后 4.50–5.02 |
| `control_panel` danger 作背景 | 白字 **4.45** | 换 `status_danger` 后 default 为 5.02 |

**已达标、只需一致性（不要因为对比度去改）**

| 点位 | 现值 |
|---|---|
| `gray_300 #606060` | 6.29(card) / 5.67(bg) |
| `gray_400 #585c63` | 6.72(card) / 6.06(bg) |

## 3. A2 清单（一项一行：谁拍板、拍什么）

| # | 项 | 谁拍板 | 拍什么 |
|---|---|---|---|
| 1 | `success` / `accent` 当正文（`status_panel:863,865,1013,1016`、`sat_track:528`；另 `959/1049/1093`） | 设计 | **已定调：一律不许直接当正文**；先出「确实需要绿/琥珀文字」清单（§7.3），再决定是否新增 `status_success` |
| 2 | `accent` 当正文（`status_panel:857,865`） | 设计 | 同上；`857/865` 属清单内 |
| 3 | 进度条 track/chunk 2.22（`status_panel:945-947`、`sat_track:552-556`） | 设计 | 轨道判装饰（与滑块方案 C 同口径），或新增 track 令牌（与滑块那份合并决策） |
| 4 | `control_panel` 红底前景 | 设计 | **已停**：五套主题仅 1 套「白 / bright-text」能到 4.5，见 §7.2；不自行调红底 |
| 5 | `gain_panel:67` 8pt 提示字 | 产品 | **已执行**（`9b3b055b`）：按禁用态豁免，并加门禁——可用档必须回到 ≥4.5 的角色 |
| 6 | `floating_panel:94` 外框 2.04、`freq_display` 禁用档 2.62/2.45（岛内） | 设计 | 岛内是否也按 3:1；若要修，用岛内既有白色 alpha 阶梯，不引入主题色 |

### 3.1 决策项 6 的实测量化（2026-11-12 复测，未改代码）

两项都在**固定深色岛屿**内（底色不随主题变），且都**无法**用「换成已有 token
且外观不变」解决，结论是**两项都留在本决策项、等设计拍板**，本轮未动代码。

**`freq_display` 禁用档**：`paintEvent` 底色 `card_1 #1F1F1F`，禁用态显著位
`gray_300 #606060`、前导零 `gray_400 #585C63`。实测：

| 组合 | 对比度 |
|---|---|
| `gray_300` on `card_1` | **2.62**（`gray_400` on `card_1` = 2.45，两档都未达 3:1） |
| `gray_400` on `card_1` | 2.45 |
| 白 @ `TEXT_ALPHA.tertiary` 0.50 | **5.10** |
| 白 @ `TEXT_ALPHA.quaternary` 0.30 | 2.70 |
| 白 @ `TEXT_ALPHA.tertiary2` 0.67 | 8.04 |

> 审计那句「2.22」的来历：同表里 `success` / `accent` 对 `card_2` 是
> 5.115 / 5.023（文档记 5.11 / 5.02），说明那一列的底是电平条轨道
> `card_2`；`gray_300` 对 `card_2` 正好 2.220。审计把这条比值抄进了
> `freq_display` 行，同时把底改写成了 `#1f1f1f`（`card_1`）。该面板实际
> 底色是 `card_1`（`freq_display.py:201` `fillRect card_1`），所以复测是
> 2.62 / 2.45。`git log -S'#606060' -- desktop/tokens.py` 只有 2026-11-04
> 一次取值提交，排除色值漂移。

岛内既有阶梯（primary 1.0 / secondary 0.78 / tertiary2 0.67 / tertiary 0.5 /
quaternary 0.3 / faint 0.23 / disabled 0.2）里**没有任何一档**能保持「显著位 /
前导零」两级结构同时过 3:1：唯一保两级的组合是 `tertiary` + `quaternary`，
后者 2.70 仍不达标；两级都过 3:1 只能用 `tertiary2` + `tertiary`，禁用态会亮到
8.04 / 5.10，和可用态前导零同档甚至更亮。**不需要新增色值，但必然改变禁用态
外观**——这正是本项要拍的「岛内是否也按 3:1」。已备好一行改法，拍板即可落：

```python
strong = QColor(255, 255, 255, _alpha("tertiary"))     # 5.10
dim = QColor(255, 255, 255, _alpha("quaternary"))      # 2.70（前导零，仍 <3:1）
```

**`floating_panel:94` 外框**：`rgba(180,196,220,0.30)` 压在
`rgba(14,17,24,0.94)` 合成底上实测 **2.00–2.04**。alpha 0.40 → 2.65、
**0.45 → 3.04**、0.50 → 3.49。要过非文本 3:1 必须把 0.30 改成 **0.45**——
这是在改一个既有色值，按纪律**不动代码**，写进本决策项。


## 4. 每步的反证测试

**现有、可直接用**

- `tests/test_ui_contrast.py::test_status_colors_readable_on_theme`：每主题每状态色 ≥4.5。
- `tests/test_ui_contrast.py::test_no_new_direct_semantic_color_reads`（棘轮，`2c66b2b7`）：
  正则拦 `danger|success|accent|status_busy|status_warning|status_danger` 的直读；
  58 条现状命中逐条登记（文件:行 + token + kind），新增一处即红，
  从基线删一条（代码未修）也红；`_BASELINE_CEILING = 11` 只许降。
  追加：`len(a1_units) == _BASELINE_CEILING`（`==` 不是 `<=`），且基线里出现
  「代码已改、行还留着」的过期项也红——即「每修一处必须同笔删行 + 降 1」。
- `tests/test_ui_contrast.py::test_gain_range_hint_exempt_only_when_stage_unsupported`
  （`9b3b055b`）：禁用档才允许用豁免灰；可用档必须回到 `palette(placeholder-text)` 且五套主题 ≥4.5。
- `tests/test_theme_contrast_gate.py::test_scrollbar_handle_meets_non_text_contrast`：
  非文本 3:1 的写法先例（可照抄给进度条/滑块）。

**解冻后与修复同批新增**

| 测试 | 覆盖单元 | 反证 |
|---|---|---|
| `test_vfo_row_tint_uses_palette_roles` | 1.4 | 出现 `COLORS[` 或十六进制字面量即红 |
| `test_ntrip_indicator_colors_follow_active_theme` | 1.5 | 切主题前后取值相同即红 |
| `test_invalid_freq_input_foreground_on_danger` | 1.6 | 前景/背景对比 <4.5 即红 |
| `test_progress_track_and_chunk_non_text_contrast` | A2 #3 | 轨道/chunk <3 即红（决定落地后再写，避免固化未定值） |

## 5. 建议批次（每批可独立回退）

1. `ntrip_panel` 求值时机 + danger/busy 接线（零视觉→小视觉；busy 2.03 最严重）。
2. `vfo_panel` 行底色改 palette 角色（1.23/1.93 最严重）+ 删按钮 danger。
3. danger 10 处 + warning 1 处统一接线（11 个单元，与第 1、2 批之外的点位一起收口）。
4. `control_panel` 红底前景（需 A2 #4 先拍板）。
5. A2 #1–#6（拍板后逐项落地）。

**回退边界**：全部改动只落在 `status_panel.py`、`sat_track_panel.py`、`vfo_panel.py`、
`ntrip_panel.py`、`control_panel.py`、`gain_panel.py` 六个文件；不碰 `themes.py` / `tokens.py`，
回退按文件整笔 revert 即可。

## 7. 执行状态（2026-11-12 追加）

### 7.1 已落地（不需要视觉的三笔）

| 提交 | 内容 | 反证 |
|---|---|---|
| `153b21e8` | 4 处「声称跟随主题」的错误注释（adsb / ntrip / location；module_panel 待他线释放） | 只改注释，无行为变化 |
| `2c66b2b7` | 直读全局语义色**棘轮**：58 条基线 + `_BASELINE_CEILING = 11` | 新加一行直读 danger → 红；删 `ntrip:50` 基线 → 红 |
| `9b3b055b` | `gain_panel` 提示字：仅禁用档用豁免灰，可用档回 `palette(placeholder-text)` | 把 `exempt=not supported` 改成 `exempt=True` → 红 |

棘轮的基线按「文件 × token 计数」判定、行号只作注释：行号会随上方编辑漂移，
计数不会。

**降天花板的规则（2026-11-12 追加，已写进门禁）**：`_BASELINE_CEILING` 的判定是
`len(a1_units) == _BASELINE_CEILING`（不是 `<=`）。**每修完一处必须同一笔做三件事**：
① 把该点位改成 `tokens().status_color(...)`；② 删掉对应基线行；③ `_BASELINE_CEILING -= 1`。
三种漏法都会红：只删行不降数（11→10 却仍写 11）、只降数不删行（10 行却写 10 但实际 11 个单元）、
代码改了基线行留着（新增的「基线里已不存在的直读」断言）。**上限只许降**；万一发现新的
待迁单元，必须单列说明并同步 +1，不许用涨上限吸收新代码。

### 7.2 `control_panel` 红底前景：停在门口（未改）

`status_color("danger")` 作底时，只在「白」与 `bright-text`（= 主题 accent）之间挑：

| 主题 | danger 底 | 白 | bright-text | 结论 |
|---|---|---|---|---|
| default | `#BD4848` | **5.02** | `#BF7A4F` 1.46 | 白可用 |
| dark | `#CE7676` | 3.24 | `#D4956A` 1.28 | **都不够** |
| dark_car | `#CF7B7B` | 3.10 | `#919cac` 1.12 | **都不够** |
| high_contrast | `#C45C5C` | 4.17 | `#FF8C00` 1.79 | **都不够** |
| figma | `#C96A6A` | 3.65 | `#D9D9D9` 2.59 | **都不够** |

按指令「任何一套两者都不够就停下报」，**未改 `control_panel`、未调红底**。
根因是 `status_danger` 在深色主题里为了当「字」而提亮到 4.5，
当「底」又不够深；要同时满足只能给「危险底」单独一个更深的值（一次视觉决定）。

### 7.3 `success` / `accent` 当正文：需要绿/琥珀文字的地方（等拍板）

**文字类（8 行 / 5 个语义槽）**

| # | 语义 | 位置 | 替代方案 |
|---|---|---|---|
| 1 | RSSI 强（绿） | `status_panel:863`（串口链）、`1013`（网络链） | 数字改主题正文色，强度交给已有电平条（非文本 3:1 已过） |
| 2 | RSSI 中（琥珀） | `status_panel:865`、`1016` | 同上 |
| 3 | 连接状态「ON」 | `status_panel:959`（12pt 600） | 彩色圆点图标 + 中性文字 |
| 4 | GPS「已定位」 | `status_panel:1049`、`1093` | 彩色圆点图标 + 中性文字 |
| 5 | 卫星「跟踪中」 | `sat_track_panel:528`（600） | 彩色圆点图标 + 中性文字 |

- 加粗/放大**不能单独解决**：只有「ON」是 12pt 粗体，仍不算 WCAG 大字号
  （≥14pt 粗体或 ≥18pt 常规），阈值仍是 4.5；放大到 14pt 粗体会改变版面，是视觉决定。
- 图标路线可行：主题 `success` 对深色底 3.05–3.38，作非文本图形已达标。

**非文字类（不需要正文色，保持现值）**

| 位置 | 用途 | 现状 |
|---|---|---|
| `status_panel:855/857` | 电平条填充 | 对轨道 5.11 / 5.02，已过 3:1 |
| `sat_track_panel:526` | 仰角图填充 | 非文本，过 3:1 |
| `ntrip_panel:50` | 指示灯圆点 | 非文本，过 3:1 |
| `vfo_panel:191` | 行底色 | 走 palette 角色（A1 清单已列） |

**结论**：0 处「必须」新增 `status_success`。二选一请你拍板——
(a) 全部改「图标 + 中性文字」，不加 token；(b) 保留彩色文字，则需新增正文级
`status_success`（浅色要 ≤ L 0.184）与正文级 accent，两个值都要设计出。
