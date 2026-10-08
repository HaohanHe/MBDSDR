# Phase63 — Waterfall 时间轴刻度 / 解调模式快捷切换 / 频谱-瀑布联动缩放 复核

HEAD = `abf5cc4`。只读复核轮（不改码）。全程 CLI/offscreen
（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），零 GUI
操控、零 mock，真实数据源 + 诚实空态。三候选先侦察核实，预期均非缺口，本轮给证据定案，
并补 128/256/512 深度时间轴对比快照 + 640/960/1920 三档 UI 复核。

## 一、三候选判定表

| # | 候选 | 判定 | 证据（file:line） |
|---|------|------|-------------------|
| 1 | 瀑布时间轴刻度自适应（computeTimeTicks/effectiveSecondsPerRow + 弹性 thinning） | **非缺口（维持）** | `cpp/src/ui/spectrum_display.cpp:319-368`：`effectiveSecondsPerRow()`（:319-323）测试 seam 优先、无诚实基准则返 0；`computeTimeTicks()`（:336-368）`rows<=0‖spr<=0` 直接返空（:340 诚实空态）；弹性 stride（:347-354）把行步长放宽到屏幕刻度间距 ≥ 一个标签高（`kTimeLabelH+kTimeLabelPadY`），短瀑布自动稀疏、高瀑布保持密排；now 在 row0（:357）、内部刻度（:359-360）、最旧行边界刻度防挤（:364-366）。128/256/512 三档实测无叠字（见下）。 |
| 2 | 解调模式快捷切换 UX（demodCombo_ + 带宽随模式联动） | **非缺口（维持）** | `cpp/src/ui/main_window.cpp:589-590` demodCombo_ 实装 **14 档**（AM/NFM/WFM/USB/LSB/CW/BPSK/QPSK/ADS-B/POCSAG/m17/VOR/ACARS/NAVTEX；任务书写 9 档，实际更全）。一键切换 handler（:2320-2364）：`currentIndexChanged→engine_->setDemodMode()` + `sbMode_`；带宽联动走纯覆盖规则 `core::bandwidthOnModeSwitch`（:2356-2357）→ `engine_->setBandwidth` + `spectrum_->setBandwidthHz` + bwCombo_ 镜像（:2358-2363）。每模式默认带宽表 `cpp/src/core/bandwidth_preset.h:69-85` 全 14 模式具名常量表；覆盖规则 :97-106 保留用户手调带宽。ADS-B 特判自动归位 1090 MHz + 升采样率（:2336-2347）；WFM 强制单声道门控（:2331-2333）；持久化 scheduleSave（:3110）+ `rx/demodMode`（:4045）。 |
| 3 | 频谱 trace 与 waterfall 联动缩放一致性（Ctrl+滚轮 zoom-to-cursor / tick strip 滚轮 pan / zoomFactor_/viewCenterHz_ 共享） | **非缺口（维持）** | **同源单视图，天生锁定**：`visibleWindow()`（`spectrum_display.cpp:157-167`）唯一计算 `fLo/fHi/spanHz = viewCenterHz_ ± (frameFsHz_/zoomFactor_)/2`；`paintEvent` 只调用一次（:853-854），同时喂 trace 的 `xForFreq`（:884）与瀑布的源图裁剪 `srcL/srcR`（:1035-1038）。共享几何 `recomputeGeometry()`（:112-143）：traceRect/stripRect/fallsRect 共用同一 `plotX0/plotW`（:140/:141/:143）。Ctrl+滚轮 zoom-to-cursor（:1525-1536）以光标频率锚定、`zoomFactor_` clamp 到 `kZoomMin..kZoomMax`；tick strip 滚轮 pan（:1537-1547）走 `wheelPanView` 钳到捕获带边。`zoomFactor_`/`viewCenterHz_` 为同一对成员（:578/:584-585/:693/:702），trace 与瀑布共用，不存在两套需同步的变量——对齐是构造保证而非事后对齐。 |

## 二、128 / 256 / 512 时间轴密度对比（实测快照）

组合通道拍摄（多通道组合先例）：`MBD_WFDEPTH=128/256/512` 在 MainWindow 构造前把深度写进
throwaway QSettings（真实持久往返，使深度下拉与 canvas ring 同档起来），叠 `MBD_WATERTICK=1`
钉 0.05 s/row（offscreen 紧循环测不出真实帧间隔，生产 EWMA 诚实静默，故用既有测试 seam 钉
行周期——与 live engine 收敛值一致）。960×700、scaleFactor=1.0、splitter 198/556/198。

| 深度 | 瀑布行下拉 | 时间轴刻度（顶→底） | 刻度间距 | 叠字 |
|------|-----------|--------------------|---------|------|
| 128 行 | `128 行` 正确 | `now, -2s, -3s, -5s`（4 个） | 行步长 32，屏幕间距 ≈ fallsH·32/128 ≈ 21px ≫ 14px 标签高 | **0** |
| 256 行 | `256 行` 正确 | `now, -3s, -6s, -9s, -12s`（5 个） | 弹性放宽步长，屏幕间距保持 ≥ 标签高 | **0** |
| 512 行 | `512 行` 正确 | `now, -6s, -12s, -18s, -24s`（5 个） | 512 行挤进同一 fallsH，弹性 thinning 把行步长放得最宽，屏幕间距仍 ≥ 标签高 | **0** |

快照（`docs/learn/phase63/`）：
- `recon-wftick-128-960.png`
- `recon-wftick-256-960.png`（与既有 `waterfall-time-tick.md` 的 256 记录可对照）
- `recon-wftick-512-960.png`

**结论**：任务书担心「128 深度（时间窗更短）下 tick 是否仍防叠」——实测成立。128 行时行被
摊高、固定 32 行步长的屏幕间距天然很大，刻度稀疏不叠；512 行时行被压矮，弹性 thinning 主动
放宽行步长把屏幕间距拉回一个标签高。三档刻度均右对齐在瀑布左 gutter、不遮挡频谱图，
**0 裁切、0 叠字**。漂移载波在三档瀑布区呈斜向虚线，视觉深度随 128→256→512 递增，证明 ring
确按所选深度重分配。

## 三、640 / 960 / 1920 三档 UI 快照复核

默认（无数据源、诚实空态）拍摄，逐档 Read 核查：

- `recon-ui-640.png` — 实出 **764×997**：`MBD_W=640 < kMainMinW` 触发 harness 逃生舱
  （`setMinimumSize(0,0)`，`ui_screenshot_narrow.cpp:80-81`），窗口落到最小 sizeHint。
  左控制轨在 splitter 边被视口横向裁切（「RTL-SDR 未连」「本地 RTL-SL」等半词）——左轨本就是
  QScrollArea，这是 below-floor 的预期滚动视口行为，非生产地板（960）上的布局缺陷；无控件碰撞/叠字。
- `recon-ui-960.png` — 960×700：左轨全可读（源与连接/设备信息/SpServer/网络音频外送/频率），
  工具行（清/标记/游标A/B/清游标）、门限 15dB、瀑布 1x 经典 256行、dB 网格 0…-100、中心 98.50、
  峰值表头、底部三步上手 banner、状态栏（NFM 未连接 VFO A 98.500 MHz / 静噪 OFF / S-meter 无设备）
  全部 legible。**0 裁切、0 叠字**。
- `recon-ui-1920.png` — 1920×1080：左右轨展开，右栏可见 解码/A…/寻呼/数据 可关闭页签，
  左轨 中心频率 98.500 MHz / 步进 10 kHz / 最近「无调谐记录」诚实空态。**0 裁切、0 叠字**。

## 四、红线自查

- 只读复核：未改任何生产码；仅增量构建既有目标 `ui_shot_narrow`，仅新建本 md + 6 张对比快照
  （`*.png` 被 `.gitignore:4` 全局忽略，不入版本）。
- 未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/regen_tool_doc*` 未动（mtime 早于本轮）；
  工作树另有他会话在途的 `cpp/scratch/gated_render_snapshot.cpp`，未触碰、未回滚。
- 未 `git add/commit/push`。无「比赛/competition」字样；GPL 中立、项目 MIT；活动参数只进 docs。
- 私钥/外链 token 扫描干净。

## 五、诚实未完成项

- offscreen harness 的 `MBD_WATERTICK` 通道把喂帧循环写死 256 行（`ui_screenshot_narrow.cpp:286`）
  且不读 `MBD_WFDEPTH`；本轮 128/512 的带刻度时间轴是靠 **叠通道**（WFDEPTH 定 QSettings 深度+填
  ring，再 WATERTICK 钉 spr）拍得——ring 已先按深度分配，后续 256 帧填充只在该 ring 上回绕饱和。
  这是工具组合而非产品缺口；若想让 tick 通道原生按深度拍，需一处小 harness 扩展（待决策，未落地）。
- 未在 offscreen 真注入 Ctrl+滚轮/strip 滚轮事件；候选 #3 的一致性改为代码级核实（单 `visibleWindow()`
  同源喂 trace 与瀑布裁剪，不可能漂移），未补一次实机滚轮目视。
- 640 档是 below-floor 逃生舱（生产地板 960），其左轨视口裁切按设计属滚动行为，不计缺陷。
