# 步进 / 瀑布深度 / Marker 分层 — 三候选复核定案 + 三档 UI 快照复核

- HEAD：`bda5e48`（docs: param-boundary audit (D3 fixed) + statusbar density recon）
- 仓库根：`/home/user/Doubao/chats/38438160041798146/MBDSDR`
- 方式：全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI；截图走既有 `ui_shot_narrow`（`cpp/tests/ui_screenshot_narrow.cpp`）通道。真实数据源 + 诚实空态，未注入 mock。
- 项目许可：MIT（根 `LICENSE`）。

## 1. 三候选判定表

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| **1. 调谐步进快速预设 UI** | **非缺口** | 值表 `kStepValuesHz={1,10,100,1000,10000,100000,1000000}` 共 7 档（`cpp/src/ui/main_window.cpp:132-133`）；下拉 `stepCombo_` 7 items「1 Hz…1 MHz」、默认 index 4=10 kHz（`:541-546`）；`applyStep` 写 `currentStepHz_` + spinbox `setSingleStep` + `spectrum_->setStepHz`（`:2294-2299`），构造即 `applyStep(currentIndex)`（`:2300`）；`currentIndexChanged` 已接 `applyStep` + `scheduleSave`（`:2301-2304`）；左/右方向键按 `currentStepHz_` / 10 微调（`:2959-2968`）；PgUp/PgDn 带 wrap 循环 + 状态条反馈「调频步进: …」（`:3051-3058`，`ui::cycleStepIndex`）；持久化回环：存 `rx/tuningStep`（`:4035`），取时按值反查 index 恢复 combo + spinbox（`:4337-4344`）。书签面：`tune_to_bookmark` 工具（`cpp/src/ai/agent_tools.cpp:782,1311`；`cpp/src/control/control_hub.cpp:119` 注册为写门控命令）+ UI 书签表已在既有轮次覆盖。UI 交互面完整。 |
| **2. 瀑布时间戳 / 深度指示** | **非缺口** | 深度下拉 `depthCombo_` 直接以「128/256/512 行」文本呈现当前深度（`cpp/src/ui/spectrum_widget.cpp:385-406`，choices `tokens.h:601={128,256,512}`、默认 256、key `view/wfDepth` `tokens.h:602`），改动即写回设置 + `canvas_->setRingDepth`（`:400-405`），canvas 构造亦读同一 key 故首帧即生效。瀑布时间轴 `computeTimeTicks()`：row0=now，往下按弹性 stride 出 `-Xs/-Xm`（`cpp/src/ui/spectrum_display.cpp:336-368`），由 `effectiveSecondsPerRow()=secPerFrameSmoothed*everyNthFrame`（`:319-323`）换算，故深度（行数）一变、时间窗长度随之自动重标；弹性 thinning 保证标签不叠（`:347-354`，基准 `kWaterfallTimeTickRows=32` `tokens.h:365`）。深度有显式下拉反馈，时间窗隐含深度——已满足。 |
| **3. 双显示联动 marker 防遮挡** | **非缺口** | 绘序即 z-order（QPainter 后绘在上），四层异色分层：① 书签绿点线先绘在最下层（`spectrum_display.cpp:930-937`，`kBookmarkColor=#5fd08a` alpha0.45 DotLine，`tokens.h:160-162`，注释明示「UNDER the user fixed markers above」）；② 固定 marker 后绘在上层（`:944-959`，非选中=accent 实线、选中=琥珀 `#e0b35a` 虚线 + 手柄圆点 `tokens.h:148`）；③ 游标 A 青 `#5fe0d0` / B 粉 `#ff8fb2` 虚线 + Δ 框（`:985-1001`，`tokens.h:168-169`）；④ 中心对称镜像淡紫 `#b48fd6` alpha0.35 点线最上层（`:1011-1023`，`tokens.h:188-189`）。同频叠线时上层天然盖下层，且绿/蓝实/琥珀虚/青/粉/淡紫互不混淆——z-order 分层 + 颜色区分成立。 |

> 三候选预期均非缺口，复核后维持该判定：**无真缺口，无需改码**。

## 2. 三档 UI 快照复核

门控组合：`MBD_RSSITREND=1`（真实 `pushDbfs()` 填 S-meter/趋势条）+ `MBD_BMKSHOT=1`（书签绿点线 98.0/99.0 MHz）+ `MBD_CURSORSHOT=1`（游标 A=98.8 青/B=98.3 粉 + 镜像线）+ `MBD_WATERTICK=1`（填满 256 行 + 钉 0.05 s/行，时间轴确定性渲染）。各档产物见同目录：

- `step-depth-marker_640.png`（请求 640，实际窗口 **764×997**）
- `step-depth-marker_960.png`（**960×640**，= 生产地板 `kMainMinW=960`）
- `step-depth-marker_1920.png`（**1920×900**，宽屏对照）

| 档位 | splitter 宽 | 状态条 | 瀑布深度下拉 | marker 线簇 | 时间轴 | 结论 |
|---|---|---|---|---|---|---|
| **960×640**（生产地板） | 198/556/198 | S0…S9 全部完整；端帽修复（c725361）两端确认：S0 不再缺 S、S9 不再缺右半（「0」/「S!」旧缺陷已消失） | 「256 行」下拉文字完整 | 绿点书签/粉虚 B/粗蓝中线/青虚 A/淡紫镜像/白峰三角异色可辨，Δ 50.00 kHz 框与 A/B 标签无叠字 | now/-3s/-6s/-9s/-12s 左对齐不叠 | **0 裁切 0 叠字** |
| **640 请求 → 实际 764×997** | 100/556/100 | S-meter 固定宽（kSMeterW=220），S0…S9 可读 | 「256 行」完整 | 同 960，分层清晰无叠字 | now/-2s/-3s/-5s/-6s/-8s/-10s/-11s 负号完整 | **中心列 0 裁切 0 叠字**；左栏控件右缘被裁（「本地 RTL-S…」「开启 SpyS…」「网络音频处…」「步进 1…」）= QScrollArea flick-scroll 逃生口预期行为，非缺陷 |
| **1920×900**（宽屏） | 420/1072/420 | 全宽舒展，17 widget 无挤压 | 「256 行」完整 | 分层线簇同 960，横向更疏朗 | now/-2s…/-11s 可读 | **0 裁切 0 叠字** |

对照 Figma 弹性语言：`kMainMinW=960` 为生产地板，960 档元素排布刚好且有余量；640 为地板之下的逃生口，harness 抬了窗口最小、但子控件最小尺寸把窗口顶到 764 宽，左栏退化为横向滚动——此行为如实记录，生产不承诺 640 可用。

## 3. 诚实未完成项（本轮只读侦察，未改码）

- **固定 user marker（`fixedMarkers_`）无专用 seed 门**：`ui_screenshot_narrow.cpp` 现有门控可注书签/游标/镜像，但无放置「非选中固定 marker（accent 实线）」的入口。该层仅经源码（`:944-959`）核读，未在快照里单独点亮；快照实际分层证据为 书签绿点 + 游标青/粉 + 镜像淡紫 + 中心蓝，四层已足够佐证 z-order。
- **下拉弹层未抓**：offscreen 只能抓 combo 的闭合态（「256 行」「10 kHz」），QComboBox 弹出浮层不在 `win.grab()` 范围；步进 7 档、深度 3 档的弹层内容靠源码 items 列表核读，未逐像素拍弹开态。
- **640 档非生产承诺**：其左栏裁切为地板下逃生口如实记录，不计为待修缺陷。

## 4. 红线自查

- 未执行 `git add/commit/push`；本轮仅新增 1 个 md + 3 张 png（均未跟踪、未 stage）。
- 未触碰他会话在途未跟踪文件：`cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/regen_tool_doc*`。
- 未回滚他人改动；未操控 GUI；未注入 mock。
- 无比赛/competition 字样；GPL 中立，项目 MIT。
- 真缺口发现：**无**。
