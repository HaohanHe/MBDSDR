# Waterfall 历史深度可配置（clean-room 机制深化）

HEAD = `7ad9a8b`。本轮对三候选先侦察核实，落地真缺口 #1（waterfall 历史深度/时间窗可配置）。全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），零 GUI 操控，零 mock，真实数据源 + 诚实空态。

## 一、三候选判定表

| # | 候选 | 判定 | 证据（file:line） |
|---|------|------|-------------------|
| 1 | waterfall 历史深度/时间窗可配置 | **真缺口 → 已落地** | 落地前 `allocateRing()` 写死 `ringDepth_ = tokens::kWaterfallHistoryLines(256)`（`cpp/src/ui/spectrum_display.cpp:202`），无 QSettings 键、无 UI 入口；depth 是编译期常量，用户无法改时间窗。 |
| 2 | 接收面板 RX 汇总行 | **非缺口（维持）** | `vfoList_` 逐 VFO 行（`cpp/src/ui/main_window.cpp:643`）；状态条常驻 `sbMode_/sbSr_/sbVfo_/sbGain_/sbSdr_/sbRssi_/sbSnr_/sbSquelch_/sbGnss_/sbAudio_` + `sMeter_`（`cpp/src/ui/main_window.cpp:2145-2174`），已覆盖 mode/采样率/VFO 频点/增益/信号源/RSSI/SNR/静噪/GNSS/声卡链路健康。无残留缺口。 |
| 3 | 频谱 marker/游标增强（dual cursor Δ） | **非缺口（维持）** | dual cursor A/B 可拖拽竖线 + 带填充的 Δ 读数框（`Δ X MHz/kHz`，`cpp/src/ui/spectrum_display.cpp:934-949`）已存在；纯函数 `measurementDeltaHz()` 有 `cpp/tests/test_dual_cursor.cpp` 覆盖。剩余"中心对称 marker/Δ 展示"属推测性润色，无用户报缺陷，判非缺口。 |

## 二、现状核实（落地前）

- Ring 分配：`SpectrumDisplay::allocateRing(int bins)`（`spectrum_display.cpp:200`）在首次 `setSpectrum`（`haveFrame_` 假）或 bins 变化时调用（`:357,:362`），其中 `ringDepth_` 被写死为 `kWaterfallHistoryLines(256)`。
- 插入路径：`pushHistoryRow()`（`:209`）按 `% ringDepth_` 推进 head；`materialiseHistory()`（`:219`）在 `history_.height() != ringDepth_` 时重建 QImage。
- 已有可持久化视图设置先例：`kSettingsKeyScrollSpeed`/`kSettingsKeyPalette`（`tokens.h:555-557`），UI 在"瀑布"行的 `scrollCombo_`/`paletteCombo_`（`spectrum_widget.cpp:351-376`）；canvas 构造时自读 `loadSpecFraction()`（`spectrum_display.cpp:61`）。depth 配置完全镜像此模式。

## 三、落地方案与 file:line

### tokens（具名 token，`cpp/src/core/tokens.h`）
- `:571` `kWaterfallDepthDefault = 256`（== `kWaterfallHistoryLines`）
- `:572` `kWaterfallDepthChoices[] = { 128, 256, 512 }`
- `:573` `kSettingsKeyWfDepth = "view/wfDepth"`

### 画布（`cpp/src/ui/spectrum_display.*`）
- `spectrum_display.cpp:75` `legalRingDepth(rows)`：仅命中具名选择集才接受，否则诚实回退 256（无连续区间可 clamp——ring 只按具名深度分配）。
- `spectrum_display.cpp:84` `loadRequestedRingDepth()`：构造时读 QSettings；缺键/非数值/越集 → 256。
- `spectrum_display.cpp:104` 构造体 `requestedRingDepth_ = loadRequestedRingDepth();`。
- `spectrum_display.cpp:231` `allocateRing()` 改用 `ringDepth_ = requestedRingDepth_;`（不再写死）。
- `spectrum_display.cpp:642` `setRingDepth(rows)`：校验 → 写 `requestedRingDepth_`；若 ring 已存在（`bins_ > 0`）则 `allocateRing(bins_)` 重建 ring（history 丢弃，因时间窗本身变了）+ `update()`。首帧前调用只记录请求。
- 头文件：`:158` 声明 `setRingDepth`；`:254/:258` 测试钩子 `ringDepthForTest()`/`requestedRingDepthForTest()`；`:459` 成员 `requestedRingDepth_`（in-class 字面量 256，避免头文件拖入 tokens.h，ctor 体总会覆盖）。

### UI（`cpp/src/ui/spectrum_widget.*`）
- 并入既有"瀑布"行（零新按钮）：`depthCombo_`（`spectrum_widget.cpp:385-406`），项 `128 行/256 行/512 行`，itemData 存数值。
- 构造时读 QSettings 落 combo 当前项（越集/缺键 → 默认 256 项，`:390-398`）；`setCurrentIndex` 在 `connect` 之前，构造期不会对未建 canvas 误触发。
- 变更回调（`:400-405`）：持久化 `kSettingsKeyWfDepth` → `canvas_->setRingDepth(depth)`。
- 持久值在 canvas 构造时也会被 `loadRequestedRingDepth()` 读到，故首帧即按所选深度分配，combo 只是 UI 反射 + 变更入口。
- `spectrum_widget.h:97` 成员 `depthCombo_`。

### 快照门控（`cpp/tests/ui_screenshot_narrow.cpp`）
- 先例 `MBD_PEAKSHOT`/`MBD_WATERTICK`/`MBD_YFREEZE`。新增 `MBD_WFDEPTH=128/256/512`：
  - `:57-62` 在 MainWindow 构造**前**把深度写进（throwaway）QSettings，使 combo 与 canvas ctor 都按所选深度起来（真实持久往返，非事后 poke）。
  - `:251-283` 窗显后按所选深度 +16 帧喂入漂移载波，填满 ring 以在快照中体现"真的按新深度重分配"。
- 全程 off 默认，其它截图不受影响；throwaway QSettings 路径不碰用户数据。

## 四、测试（真实计数）

扩既有 `test_spectrum_display` 目标（未新增 CMake 目标）：
- `waterfallDepthDefaultsTo256` — 无键 → 请求值/活动 ring 均 256。
- `waterfallDepthChangeRebuildsRing` — 30 帧后 `setRingDepth(512)`：`ringDepth_=512`、`ringCount_` 归 0；喂 700 行后封顶 512（证明 ring 真重配而非残留 256）；再 `setRingDepth(128)` 重建并封顶 128。
- `waterfallDepthInvalidFallsBackToDefault` — `999/-8/0` 均回退 256。
- `waterfallDepthPersistsRoundTrip` — 写 512 → 新实例 ctor 读出并按 512 分配；写 128 → 128；写 `"bogus-depth"` → 诚实回退 256。
- `initTestCase` 清理列表加入 `kSettingsKeyWfDepth`（`test_spectrum_display.cpp:111`）防泄漏。

offscreen 运行结果：**`Totals: 32 passed, 0 failed, 0 skipped`**（原 28 + 新增 4）。

## 五、快照核查

入 `docs/learn/phase63/`：
- `wfdepth_512_960.png`（960×680）
- `wfdepth_512_1920.png`（1920×760）

核查结论：两张均见"瀑布"行深度下拉显示 **512 行**，瀑布区填满深历史（漂移载波呈斜向虚线轨迹）；新控件在 `经典` 与色块 swatch 之间排布整齐，**0 裁切、0 叠字**。左侧瀑布时间刻度为既有 time-tick 机制渲染，非本次引入。

## 六、红线自查

- 改动文件均为职责所需：`tokens.h`、`spectrum_display.{h,cpp}`、`spectrum_widget.{h,cpp}`、`ui_screenshot_narrow.cpp`、`test_spectrum_display.cpp`。
- 未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/regen_tool_doc*` 未动。
- 未 `git add/commit/push`；中立措辞与私钥/外链 token 扫描干净（见回报）。

## 七、诚实未完成项

- depth 仅提供 128/256/512 三档（与 ring 缓冲调优点对齐）；未做任意深度输入框——任意深度会引入无界内存/渲染风险，判为有意收敛而非缺口。
- 改深度时历史按设计丢弃（时间窗变化）；未做"保留旧历史重采样到新深度"——重采样会插值造假历史，与诚实数据原则相悖。
