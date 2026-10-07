# Phase62 · UI 观察类裸数逐项判定（只读收尾）

- 仓库 HEAD：`5eccddc`；全程只读（grep/Read），**零代码改动**，未 git add/commit/push。
- 输入：上轮 `ui-geometry-scan.md` §2.2 留档的 `tokens::scaled(<行内裸数>)` 观察族（非违规，已走 DPI 缩放）。
- 判定标准（沿用任务口径）：
  1. **唯一出现**（单一用途）→ 留档 / 原地注释即可；
  2. **与既有 token 语义重复**（同值多次出现且同义）→ 该 token 化；
  3. **频繁修改 / 多面板共享** → 该 token 化；
  4. **布局语义一次性初始化** → 留档。
- 三档结论：**该 token 化**（提升为 `tokens::k*`）、**保留**（刻意不动，理由明确）、**留档**（保持行内 + 一句注释）。

## 1. 逐项判定表

| # | 位置 | 行内值 | 语义 | 出现频次 / 既有 token | 结论 | 理由 |
|---|---|---|---|---|---|---|
| 1 | `main_window.cpp:163` | `scaled(1280), scaled(800)` | 初始窗口尺寸 `resize()` | 仅 1 处；紧邻 `tokens.h:206-207` 已有 `kMainMinW=960 / kMainMinH=600` 地板 token | **该 token 化** | 初始尺寸与最小尺寸是同一组"窗口设计决策"，却一个走 token、一个走行内裸数，成对拆开不一致。建议补 `kMainInitW=1280 / kMainInitH=800`，与 `kMainMin*` 同区维护。 |
| 2 | `main_window.cpp:1919` | `scaled(280), scaled(800), scaled(280)` | 三栏 splitter 初始份额（左栏/中央/右栏） | `280` 在同一行重复 2 次（左右栏对称）；上一行 `setStretchFactor(0/1/2, 1/3/1)` 已是具名比例 | **该 token 化** | 同值 280 跨左右栏对称出现，属"多面板共享"；与 stretch factor 是同一组三栏布局决策。建议补 `kRailInitW=280 / kCenterInitW=800`，份额与比例同区可读。 |
| 3 | `main_window.cpp:4481` | `scaled(260)` | 状态栏 RDS 长文本 `elidedText` 宽 | 仅 1 处；全仓 `elidedText` 仅 2 处（另一处 `spectrum_widget.cpp:483` 用不同宽） | **留档** | 单一调用点、唯一值；状态栏 RDS 是唯一需要 elide 的长文本流，没有第二个面板共享该宽度。token 化后仅 1 个调用方，徒增间接层。原地保留 + 行内注释"RDS 状态栏 elide 宽"即可。 |
| 4 | `s_meter.cpp:47` | `scaled(220)` | S-meter `sizeHint()` 首选宽 | 仅 1 处；`tokens.h:610` 已有 `kSMeterH=34`（高），宽却走行内裸数 | **该 token 化** | 与既有 `kSMeterH` 成对：S-meter 是状态栏 permanent widget，宽高都是仪表外形设计决策，高已 token、宽未 token 不对称。建议补 `kSMeterW=220`，与 `kSMeterH` 同区。 |
| 5 | `data_text_panel.cpp:72` / `m17_panel.cpp:92` / `pocsag_panel.cpp:74` | `scaled(36)` | 数据表头 `setMinimumSectionSize` | 同值 36 **跨 3 个同构数据面板重复 3 次** | **该 token 化** | 显式跨文件重复（3 调用点、完全同义=数据表头最小列宽）。若未来调表头最小宽，需改 3 处且容易漏改 1 处导致面板漂移。建议补 `kTableMinSectionW=36`，3 处同引。 |
| 6a | `spectrum_display.cpp:910` | `QPen(accent, 1.2)` | 频带中央刻线描边宽 | 1.2 数值上与 `kTrajLineWidth=1.2`、`kVfoBoxLineWidth=1.2` 重合，但语义不同（卫星轨迹/VFO 框边/频带中心标记） | **保留** | 描边权重是 painter 逻辑 px，非 DPI 几何；现有 `k*LineWidth` token 族（`kCursorLineWidth/kVfoBoxLineWidth/kTrajLineWidth`）只在"同笔画复用"时才具名。此处 1.2 是中央刻线一次性笔画，与已有 1.2 token 不同义。若未来出现第 3 处 accent 笔画，再提 `kAccentLineWidth`。 |
| 6b | `vor_panel.cpp:155` | `QPen(accent, 2.0)` | VOR 方位指针描边宽 | 仅 1 处；全仓无第二个指针类笔画 | **保留** | VOR 指针是表盘上最粗的实心方位针，2.0 是刻意的重笔设计；无复用方。与 6a 同理，stroke-weight token 只在复用时具名。 |
| 7 | `world_view.cpp / sky_view.cpp / vor_panel.cpp / elevation_plot.cpp / s_meter.cpp / spectrum_display.cpp` 内 `scaled(2/3/4/5/6/7/12/14/20/22/26/44/60/64/90/110/140/160)` | 仪器画布微几何（标签内缩、圆点半径、引导线、tick 长） | 同一数字跨画布映射到**不相关语义**：例 `scaled(12)` = sky_view 标签半径外扩 / vor_panel 指针尖内缩 / elevation_plot 文本高 / s_meter tick 文本高 | **留档** | 数字重合纯属巧合、语义不互通；强行 token 化会把"标签外扩"和"指针内缩"绑成同一常量，制造虚假等价。这些是各画布一次性调参点，随画布几何一起改。全族保持行内，不提升。 |

## 2. 汇总

- **该 token 化：4 项** —— #1 初始窗 1280×800、#2 splitter 份额 280/800/280、#4 S-meter 宽 220、#5 表头 minSection 36（×3 重复）。
- **保留：2 项** —— #6a 频带刻线 1.2、#6b VOR 指针 2.0（stroke-weight，非 DPI 几何，无复用方）。
- **留档：2 项** —— #3 RDS elide 260（单点）、#7 仪器画布微几何族（数字巧合、语义不互通）。

即 **X=4 / Y=2 / Z=2**（按 8 个判定行计；#5 的"×3"是同一 token 的 3 个调用点，不重复计数）。

## 3. 后续接线建议（仅供主 agent 统一评估，本轮不动代码）

- 4 项该 token 化均落在 `cpp/src/core/tokens.h`：
  - `kMainInitW / kMainInitH` 紧挨 `kMainMinW/kMainMinH`（tokens.h:206-207）；
  - `kRailInitW / kCenterInitW` 与 splitter 比例注释同区；
  - `kSMeterW` 紧挨 `kSMeterH`（tokens.h:610）；
  - `kTableMinSectionW` 与数据面板族同区。
- 提升后调用点替换：`main_window.cpp:163`（2 处）、`main_window.cpp:1919`（3 处）、`s_meter.cpp:47`（1 处）、`data_text_panel.cpp:72` + `m17_panel.cpp:92` + `pocsag_panel.cpp:74`（3 处），共 9 个行内点。
- #6/#7 不动；#3 建议在 `main_window.cpp:4481` 上方补一行注释说明 elide 宽是状态栏 RDS 专属。

## 4. 硬约束自查

- 零代码改动（仅新增本文件）；未 git add/commit/push。
- 活动参数零硬编码；无预置 TLE / 呼号；全文无竞赛类字样；GPL 措辞中立。
- 所有判定均基于 `cpp/src/ui/` + `cpp/src/core/tokens.h` 实际 grep 证据，未推测。
