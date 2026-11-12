# QSS/绘制代码里的硬编码颜色：按「该主题化但没主题化」排序

> 用途：FZ-04 冻结期内**只读盘点**，给解冻申请单排优先级用。本文档不改任何代码，
> `desktop/themes.py` / `desktop/tokens.py` 一字未动（本页落盘前后 `git status` 均无这两个文件）。
> 口径：只看**颜色取值来源**与**所在表面**，不看观感偏好；对比度一律 WCAG 公式实测
> （正文 4.5:1、非文本 3:1），半透明先按真实叠底合成再算。
> 复跑：`grep -n 'COLORS\[' desktop/*.py`、`grep -n 'fillRect' desktop/*.py`。

## 0. 判定标准（三条轴，决定排序）

1. **表面归属**：控件是否自绘/自填底色？自绘成固定色块的是「岛屿」，里面用全局
   `COLORS` 是**对的**（岛屿不随主题）；继承主题表面（QSS/palette 上色）却用深色常量的，
   才是错。
2. **取值来源**：读 `tokens().COLORS[...]`（全局深色一套）还是读
   `theme_colors(active_theme_name())[...]` / `status_color(...)`（跟随主题）。
3. **阈值级别**：当正文用（4.5:1）还是当边框/图形用（3:1）。同一条颜色，级别不同结论不同。

排序键：**继承主题表面 + 读全局深色 + 当正文用** 最该先修；自绘岛屿里的常量排后；
冻结/他线文件只登记不动。

## 1. A 类（最该主题化：继承主题表面，却读全局深色常量）

这些文件里**没有**给自身设 `background`/`fillRect` 底色（已核：仅进度条轨道与按钮底色例外），
面板表面由 QSS/palette 按主题给。默认主题是浅色（`card #FFFFFF`、`bg #F5F3EF`），
全局 `COLORS` 是深色主题的一套值，于是浅色下直接失效：

| 文件:行 | 现值 | 用途/级别 | 浅底实测（card / bg） | 判定 |
|---|---|---|---|---|
| `status_panel.py:360` | `COLORS["danger"]` | 文字 16pt | `#B85C5C` **4.45 / 4.01** | 正文不足 4.5 |
| `status_panel.py:415,825,833,841` | `gray_300` | 电平条填充 | `#606060` 6.29 / 5.67 | 文字级达标；但对轨道只有 **2.22**（见 A2） |
| `status_panel.py:855-859` | `success`/`accent`/`gray_300` | 电平条填充 | 5.11 / 5.02 / 2.22（对轨道） | 灰档不合格 |
| `status_panel.py:863,865,867` | `success`/`accent`/`danger` | RSSI 文字 | **2.73 / 2.78 / 4.45**（card） | success、accent 不合格 |
| `status_panel.py:945-946` | `gray_400` 边框 + `card_2` 底 | 进度条轨道 | `#2b2c2f` 是深色块 | 浅色下成一块黑板（轨道需决定，A2） |
| `status_panel.py:959,965,1013,1016,1019,1049,1093` | `success`/`accent`/`danger` | 状态文字 | 同上 | 同前三行 |
| `sat_track_panel.py:453` | `danger`（经 `status_color` 回落） | 过境文字 | 已走 `status_color`，合规 | **无需改** |
| `sat_track_panel.py:492,518,522,526,528` | `gray_300`/`danger`/`success` | 仰角/状态文字 | 2.73（success）/ 4.45（danger，card） | success 不合格 |
| `sat_track_panel.py:552-556` | `gray_400` + `card_2` + chunk | 进度条 | 与 status_panel 同构 | 同 A2 |
| `vfo_panel.py:191-194` | `accent` 35% 底 + `text_primary` 字 | 主听行 | 合成底 `#E5E8EB`，白字 **1.23** | 最严重：几乎看不见 |
| `vfo_panel.py:199,208` | `card_2` 35% 底 + `gray_200` 字 | 普通行 | 合成底 `#CDCDCE`，字 **1.93** | 不合格 |
| `vfo_panel.py:232` | `danger` | 「删」按钮文字 | 4.45 / 4.01 | 不足 4.5 |
| `ntrip_panel.py:47-49` | `danger`/`status_busy`/`success` | 模块级常量 | 2.03（busy，card）/ 4.01（danger） | 且**导入时求值**，切主题永不更新 |
| `control_panel.py:999,1007` | `danger` | 按钮底色 | 4.45 / 4.01 | 白字压其上够，但底色随主题应走主题 |
| `gain_panel.py:67` | `disabled_gray #999999` | 提示文字 8pt | **2.85 / 2.57** | 不合格 |

**A 类合计 40 处读取点（status 20 / sat 9 / vfo 5 / ntrip 3 / control 2 / gain 1）**，
全部是「读错来源」而非「配色不好看」——同一个控件在深色主题下没问题，一换浅色就掉。

### A1. 不需要动视觉就能修（纯接线，不产生配色决定）

| 修法 | 涉及 | 为什么不需要决定 |
|---|---|---|
| `COLORS["danger"]` → `status_color("danger")` | status_panel:360,867,965,1019；sat_track:518,522；vfo:232 | 五套主题的 `status_danger` 都已按 ≥4.5 设计（4.50–5.02），改的是读取路径 |
| `COLORS["status_busy"]/["warning"]` → `status_color(...)` | ntrip:48 | `status_warning` 五套主题均有设计值（4.55–7.70） |
| 模块级常量改为调用时求值 | ntrip:47-49 | 这是求值时机 bug（import 时锁死主题），与颜色无关 |
| 表格行底色改用 `palette(Highlight)/palette(HighlightedText)` | vfo:191-201 | Qt 已有角色，随主题走；不新增颜色 |
| `COLORS["gray_*"]` → `palette(placeholder-text)` 等已有角色 | sat_track:492；status:415 等 | 层级文字本来就有对应角色，取色路径换一下 |

### A2. 必须带一次配色决定（目标槽位缺值，或与主题无关的结构缺陷）

| 项 | 现状 | 为什么算决定 |
|---|---|---|
| `success` 当正文（status:863,1013；sat:526,528） | 主题 `success` 浅色 3.38/3.05，全局 2.73 | 只够非文本 3:1，正文 4.5:1 无现成槽位（`THEME_COLORS` 没有 `status_success`） |
| `accent` 当正文（status:857,865；sat） | 主题 `accent` 浅色约 3.0–3.4 | 同上，需补正文级强调色或降级用法 |
| 进度条轨道 `card_2` + 灰档 chunk（status:945-947；sat:552-556） | chunk/轨道 **2.22**，与主题无关（两套主题都错） | 缺「轨道」语义槽位；要么给轨道新角色，要么把灰档 chunk 提亮——都是视觉决定 |
| `gain_panel:67` 提示字 | `disabled_gray` 2.85/2.57 | 是「禁用态豁免」还是「确实要读」，需产品口径 |

> 注意：A1 修完，浅色下 `status_panel` 的 RSSI/状态文字仍有 success、accent 两类不合格——
> 它们不是接线问题，请不要和 A1 混成一批提交。

## 2. B 类（固定岛屿：不该主题化，但岛内可能有独立缺陷）

这些控件**自绘底色**，用全局 `COLORS` 是有意的（星图、地图纸、LCD、AI 对话区）。
列在这里只为说明「不是遗漏」，以及岛内仍有的独立问题：

| 文件 | 自绘底色 | 说明 |
|---|---|---|
| `ai_panel.py`（16 处 `COLORS`） | 对话区 `#1f1f1f` | 岛内正确；`fa49baf2` 已把 9 处状态色接 `status_color(bg=...)` |
| `capability_drawer.py` | `rgba(14,17,24,0.94)` | 岛内正确；边框已在 `96e5ff2c` 修（冻结期，见追认单） |
| `location_panel.py` | `#14161c` / `#1b1e26` | 岛内正确；`_EDGE` 同上 |
| `time_machine.py` | 深底 | 岛内正确（10 个字面量集中在 :134 一带） |
| `floating_panel.py` | 深底 | :94 外框 2.04（非文本不足 3:1，岛内独立缺陷） |
| `freq_display.py:201,214-216` | `card_1 #1f1f1f` 填充 | 岛；禁用态 `gray_300` 在 `#1f1f1f` 上 2.22（岛内独立缺陷） |
| `adsb_panel.py:58-62` | `light_bg` 米白纸 | 岛；**注释写「主题联动」是错的**——模块级常量，永不随主题变 |
| `constellation_panel.py`（14 处） | `card_1` 填充（:211,:412） | 星图岛；:571 那处文字已改走 `palette(placeholder-text)`（`1b546a98`） |
| `module_panel.py:68-72,76,78` | `card_1` 画布（PAPER） | 岛；画布内配色是信息图语汇，不属于主题化范围 |
| `new_spacetime_panel.py:111-117,218` | `card_1` 填充 | 岛，同上 |
| `rf_sky_view.py`（此前扫描 114 处） | 星图画布 | 岛；文件他线在途，**禁碰** |

## 3. C 类（画布/装饰，不算「没主题化」）

- `spectrum_widget.py` 2 处：频谱画布背景与网格，属数据可视化图层。
- `tokens.py` 自身的 15 处：定义处，不是使用点。

## 4. D 类（本轮禁碰，只登记）

| 文件 | 内容 | 状态 |
|---|---|---|
| `main_window.py` | 11 处 QSS 字面量 + 6 处 `COLORS` | 冻结；已知 `:5273` 须与 `:5275` 的 `rgba(200,160,64,0.18)` 同改，另 `:2174-2177` |
| `right_column.py` | `:149` `rgba(255,255,255,0.10)` + 1 处 `COLORS` | `:148-150` 已判**纯装饰**（前一轮结论），挂账 |
| `settings_panel.py` | `:1333/1341` | 他线占用 |

## 5. 解冻后的建议顺序（每步可独立回退）

1. **ntrip_panel:47-49 求值时机**（最小、纯 bug、零视觉变化）。
2. **vfo_panel:191-208 行底色改 palette 角色**（1.23/1.93 → 角色保证值，观感最接近原意）。
3. **danger / warning 的 12 处接线**（A1，五套主题已有 ≥4.5 的设计值）。
4. **success / accent 正文级**（A2，先给一次配色决定，再接线）。
5. **进度条轨道**（A2，与滑块轨道那份 `2026-11-12-slider-track-three-constraints.md` 合并决策）。

## 6. 复跑与反证口径

```powershell
# 读取点普查（A 类）
Select-String -Path desktop\*.py -Pattern 'COLORS\[' | Group-Object Filename

# 自绘底色普查（B 类判定）
Select-String -Path desktop\*.py -Pattern 'fillRect|setStyleSheet\(' | Select-String 'background'

# 浅底实测（本文档全部数字）
python -c "from themes import contrast_ratio as c; print(c('#6BA89A','#F5F3EF'))"
```

反证方式：A1 每改一处，先在浅色主题下取该控件实际合成底色，断言 ≥ 目标阈值；
把取值改回 `COLORS[...]` 应立即变红。A2 在配色决定落地前**不写断言**，避免把未定值固化进门禁。
