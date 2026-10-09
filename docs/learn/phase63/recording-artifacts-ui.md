# Phase63 — 录制产物管理 UI / 书签交互 / 状态栏密度 三候选复核

- **HEAD**: `e1b296a`（侦察前后未变，未做任何 git add/commit/push）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **运行方式**: 全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI；构建走持久 `cpp/build/` 增量；截图走既有 `ui_shot_narrow`（`cpp/tests/ui_screenshot_narrow.cpp`）通道，环境变量 `MBD_W/MBD_H/MBD_SCROLL/MBD_TAB/MBD_OUT`。真实数据源 + 诚实空态，未注入任何 mock。

> 背景：本轮回执的假设快照早于此前若干 phase 落地——假设"录制产物管理 UI 缺失、全仓无 recFileList 类"。本轮侦察核实：**该假设已被既有 `RecordingLibrary` + 录制库 tab + "打开录制目录"按钮整体覆盖**，三个候选均判非缺口（证据如下）。

## 1. 机制现状（侦察）

### 1.1 录制组入口（左栏 gRec）
`cpp/src/ui/main_window.cpp`：

| 控件 | 位置 | 说明 |
|---|---|---|
| 录制目录行 | `:747-756` | `recDirEdit_`（默认 `"record"`）+ `recDirBrowseBtn_`「浏览…」|
| 目录选择器 | `:2483-2492` | 浏览… → `QFileDialog::getExistingDirectory`，选后 `engine_->setRecordingDir(dir)` + 持久化 |
| 录制对象 | `:759-761` | `recTargetCombo_`：基带 IQ (SigMF) / 解调音频 (WAV) |
| 文件名模板 | `:763-766` | `recTemplateEdit_` `{time}_{freq}_{mode}`，带占位符 tooltip |
| 静噪/触发/值守 | `:770-790` | `recIgnoreSqlChk_`（忽略静噪）、`gatedCheck_`（触发式分段）、`watchCheck_`（值守录制，含门限/前滚/结束延时）|
| **打开录制目录按钮** | **`:822-824`（创建）/ `:2648-2657`（接线）** | `QDesktopServices::openUrl(QUrl::fromLocalFile(dir))`；先 `QDir().mkpath(dir)`（目录不存在即建），相对路径先解析到 `QDir::currentPath()` |
| 录制按钮/状态 | `:825-829` | `recordBtn_`「● 录制」+ `recStatus_` 诚实状态 |

### 1.2 录制产物管理（录制库 tab，右栏）
假设称"全仓无 recFileList 类"——实际存在 **`ui::RecordingLibrary`**（`cpp/src/ui/recording_library.{h,cpp}`）与完整录制库面板：

- tab 挂载：`main_window.cpp:1819` `rightTabs_->addTab(recPage, "录制库")`；面板构造 `:1726-1819`。
- 值守录制状态框：`:1733-1741`（真实 `watchStateChanged`/RSSI 回读，未启用诚实「值守: 未启用」）。
- 文件列表 `recLibList_`（QListWidget）：`:1748-1751`，单选、不可编辑。
- **诚实空态** `recLibEmpty_`「暂无录音」：`:1752-1755`，`recLibList_`/`recLibEmpty_` 互斥显隐（`:3625-3626`）。
- 操作按钮：刷新/复制路径/删除/播放/分析/导出解码（`:1758-1773`）+ `recLibIqBtn_`「导出原始 IQ 段」（`:1781-1784`）。
- 扫描 `refreshRecLib()`：`:3607-3638`
  - `RecordingLibrary::scan(engine_->recordingDir())` 真实扫描（`:3610`），空/缺目录 → 空 vector（诚实「暂无录音」，绝不伪造行）。
  - 两行 row：上行 time/freq/mode，下行诚实完整路径（`:3619-3622`）。
  - **诚实计数**：`recLibPlayStatus_` 空目录「未加载」/ 否则「%1 个文件」（`:3633-3637`）。
- 核心 `RecordingLibrary`（recording_library.h）：`scan()` 按 mtime 新→旧、`readSidecar()` 读 sidecar .json、`parseFileName()` 模板回退、`probeWav()` 解析 RIFF/WAVE 头、`decodePcmMonoToFloat()` 播放、`removeEntry()` 删 wav+sidecar。
- 音频回放：`recLibPlayTimer_` 20ms chunked 播放（`:1840-1850`）；非 PCM/截断诚实拒绝（`:3794-3806`「不支持: …」/「解码失败」）。

### 1.3 书签表（bmTable_）
- 5 列表头：`main_window.cpp:1536` 名称/频率(MHz)/模式/带宽(kHz)/分组。
- 添加/编辑/删除按钮：`:1546-1548`；编辑=重命名入口（prefill 频率/mode/带宽）。
- `refreshBmTable()`：`:3376-3453`
  - 诚实空态「暂无书签」（UserRole -2，不可选/不可编辑）：`:3381-3392`。
  - **分组显示**：每组一个非交互 section header 行「组名 (N)」（空组渲染「默认」），随后跟该组书签行；store 已按 `(group, freq)` 排序，视觉行≠store 行，col-0 UserRole 携带 store 索引（`:3400-3453`）。
  - `bmStoreIndexAtVisualRow()` 做视觉→store 映射（`:3455-3460`），section header(-1)/空态(-2) 永不映射到 store 行。
- 表头排序：**未启用** `setSortingEnabled`（对照：平列表 `passTable_` 启用了 `:1351-1352`）。

### 1.4 状态栏（17 widget）
`main_window.cpp:2146-2186`：14 QLabel（sbMode_/sbSr_/sbVfo_/sbRds_/sbGain_/sbSdr_/sbWatch_/sbScan_/sbRec_/sbRssi_/sbSnr_/sbSquelch_/sbGnss_/sbAudio_）+ `sMeter_` + 6px gap widget + `rssiTrend_` = 17。S-meter 端帽 S0/S9 半裁问题已在前轮修复，本轮 `test_s_meter::endCapLabelsStayInsideTrack()` 回归绿。

## 2. 构建与测试（offscreen 真实计数）

增量构建（`cpp/build/`，`-j$(nproc)`）后运行：

| 测试目标 | 计数 | 结果 |
|---|---|---|
| `test_recording_library` | 8 passed / 0 failed | PASS（含 `emptyDirIsHonest`、`rejectsNonPcm`、`scansSidecarMetadata`、`deletesWavAndSidecar`、`probesWavHeader`、`fallsBackToFilename`）|
| `test_bookmark` | 3 passed / 0 failed | PASS（lifecycle）|
| `test_s_meter` | 12 passed / 0 failed | PASS（含 `endCapLabelsStayInsideTrack`、`trendNaNClearsToEmpty`）|
| `test_ui_integration` | 22 passed / 0 failed | PASS（rightTab/调谐历史/接收链路 badge/解调带宽持久化等）|

## 3. 快照核查（ui_shot_narrow，门控先例 MBD_PEAKSHOT）

输出目录：`/home/user/Doubao/chats/38438160041798146/ci/phase63_shots/`

| 快照 | 尺寸 | 内容 | 核查结论 |
|---|---|---|---|
| `recgrp-960.png` | 960×640（`MBD_SCROLL=bottom`）| 左栏录制组滚到可见：录制目录/对象/模板/静噪/触发/值守 + **「打开录制目录」按钮** + ●录制 + 空闲 | 0 裁切、0 叠字；状态栏 17 widget 完整可读 |
| `recgrp-1920.png` | 1920×900（`MBD_SCROLL=bottom`）| 同上宽屏对照 | 0 裁切、0 叠字；S-meter/RSSI 趋势条固定宽未被挤压 |
| `reclib-960.png` | 960×640（`MBD_TAB=录制库`）| 录制库 tab：值守状态 + **诚实空态「暂无录音」** + 导出原始IQ段 + 离线分析「未打开文件/未加载」| 空态诚实、无伪造行；按钮完整 |

## 4. 三候选判定表

| 候选 | 判定 | 证据（file:line）|
|---|---|---|
| **1. IQ 录制/回放 UI 面（产物管理）** | **非缺口** | 「打开录制目录」按钮已在录制组内：创建 `main_window.cpp:822-824`，接线 `QDesktopServices::openUrl`（含 mkpath + 相对→绝对解析）`:2648-2657`；目录选择器 `:2483-2492`。产物管理 UI 完整存在：录制库 tab `:1819`、文件列表 `recLibList_` `:1748`、诚实空态「暂无录音」`:1752-1755`、真实扫描 `RecordingLibrary::scan` `:3610`、诚实计数「%1 个文件」`:3633-3637`、刷新/播放/删除/复制/分析/导出 `:1758-1784`、非 PCM 诚实拒绝 `:3794-3806`。假设的"无 recFileList 类"不成立——`ui::RecordingLibrary`（`src/ui/recording_library.h`）即此类。 |
| **2. 书签管理 UI 交互面** | **非缺口** | 5 列 `:1536` + 添加/编辑(=重命名)/删除 `:1546-1548`；分组显示已有：section header 行「组名 (N)」`:3418-3432`，store 按 `(group, freq)` 预排序 `:3400`，视觉→store 映射 `:3455-3460`；诚实空态「暂无书签」`:3381-3392`。表头排序未启用系**有意设计**：分组视图把 section header 行与数据行交错排列，`setSortingEnabled(true)` 会打散分组分隔行并破坏视觉→store 映射；平列表 `passTable_` 才需要排序（`:1351-1352`）。组织职责由「分组 section + store 预排序」承担，非"缺表头排序"。 |
| **3. 状态栏聚合密度** | **非缺口（例行复核干净）** | 17 widget（14 label + sMeter + gap + trend）`:2146-2186`；960/1920 快照均无挤压/叠字；S-meter 端帽 S0/S9 半裁前轮已修，本轮 `endCapLabelsStayInsideTrack()` 回归绿；6px gap widget 专门防止 S-meter S9 端帽贴到趋势条边线 `:2179-2183`。前轮审计见 `phase63/statusbar-density.md`（HEAD 9950cf8）。 |

## 5. 落地结论

**本轮无代码落地**——三个候选经侦察均为既有能力覆盖（非缺口），按任务回退规则「若既有 recDirRow/系统文件管理器已覆盖：判非缺口给证据」「预期非缺口给证据」「本轮例行复核给结论」执行。未新增/修改任何源码、测试或 CMake 目标。

### 观察项（未落地，记录备查）
- 改录制目录（`recDirBrowseBtn_` `:2490` / `recDirEdit_` editingFinished `:2495`）后，录制库列表**不自动重扫**，仍显示旧目录条目直到用户点「刷新」（`refreshRecLib()` 仅在构造 `:1888`、删除后 `:3659`、导出后 `:3753`、手动刷新 `:1821` 时触发）。此为既有手动刷新设计（「刷新」按钮为正当交互入口），不在本任务假设的"缺打开按钮/缺计数徽章"范围内，故不改；如后续要做目录变更即时诚实重扫，可在两处 `setRecordingDir` 后补一行 `refreshRecLib()`。

## 6. 改动文件清单 / 红线

- **改动文件**：仅新增本文档 `docs/learn/phase63/recording-artifacts-ui.md`；未改动任何 `cpp/src` / `cpp/tests` / `CMakeLists.txt`。
- **快照产物**（工作区持久目录，未入仓）：`ci/phase63_shots/recgrp-{960,1920}.png`、`reclib-960.png`。
- **未触碰**：`cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`（他会话隔离文件）。
- **红线自查**：无 git add/commit/push；活动类敏感字样零出现（项目中立）；项目 MIT（`LICENSE`）；未操控 GUI；真实数据源 + 诚实空态 + 零 mock。
