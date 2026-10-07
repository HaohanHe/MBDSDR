# Phase62 · 轻量功能闭环孤儿走查（只读）

- 仓库 HEAD：`5eccddc`；全程只读（grep/Read），**零代码改动**，未 git add/commit/push。
- 对照面：Agent 45 工具（`cpp/src/ai/agent_tools.cpp:1147-1198` dispatchTable）× 桌面主界面（`cpp/src/ui/`）× 后端能力（`cpp/src/dsp/` + `cpp/src/control/control_hub.cpp`）。
- 判定口径：只接真实数据源，禁 mock；每个孤儿给出"该补 UI / 该补工具 / 架构性不补（如实理由）"。

## 1. 45 工具的 UI 接线普查（先排雷）

任务点名的 5 个嫌疑项逐一复核，**全部已接线，不是孤儿**：

| 工具 | UI 落点（file:line） | 数据源 |
|---|---|---|
| `get_capabilities` | `main_window.cpp:353-369` "设备信息" group box（设备/调谐范围/采样率范围/来源 label）；增益表经 `main_window.cpp:2790,4287 refreshGainControl()` 进离散 combo | `engine_->sourceCapabilities()` + `availableGainsDb()` |
| `get_recording_state` | `main_window.cpp:731 recordBtn_` 录制/停止切换（`:4423-4426` 染色）；`recordingDir` 经 `:3204` 喂给 `RecordingLibrary` 面板 | `engine_->recordingPath()/recordingDir()/watchEnabled()` |
| `predict_passes` | `main_window.cpp:1254-1263 passTable_`（卫星/AOS/LOS/最大仰角/预测多普勒/距今 6 列）；`:1146 elevationPlot_` 仰角曲线；`:1140 skyView_` 极坐标；TLE 来自 celestrak.org（`:1210 刷新 TLE` 按钮） | 真实 SGP4 + 在线 TLE，无内置呼号 |
| `get_acars_packets` | `data_text_panel.cpp:83 setAcars()`；`main_window.cpp:2370-2373` 接 `acarsPacketsChanged` 信号 | 引擎真实解码帧 |
| `get_navtex_messages` | `data_text_panel.cpp:88 setNavtex()`；`main_window.cpp:2374-2377` 接 `navtexMessagesChanged` | 引擎真实解码帧 |

其余 40 工具抽查（tune_frequency/set_mode/scan_band/set_bandwidth/set_squelch/书签 CRUD/VFO CRUD/录制库 CRUD/set_fft_params/set_color_map/set_doppler_compensation/connect_network_source/calibrate_frequency/get_vor_radial 等）均在左栏源/连接组、频谱工具条、sky 页、书签/扫描页、校准对话框等处有可见入口或状态展示，不一一列。

## 2. 孤儿清单

### 2.1 有工具无 UI 入口（后端真实存在，桌面主界面无可见入口/状态展示）

#### 孤儿 A：`export_iq_segment`
- **后端**：`cpp/src/dsp/spectrum_engine.h:134` `exportIqSegment(sampleCount, tuneHz, path, ...)`；实现 `spectrum_engine.cpp:335`。
- **工具调用链**：`agent_tools.cpp:146 execExportIqSegment` → 引擎环形缓冲落盘。
- **UI 现状**：录制库面板（`main_window.cpp:1604-1607`）只有 `删除 / 播放 / 导出解码(文本)` 三个按钮；`recLibExportBtn_` 导的是解码文本（`:3278 "导出解码文本"`），不是原始 IQ 段。全 UI grep `exportIqSegment/导出 IQ/导出片段` 0 命中。
- **判定**：**该补 UI**。
- **建议接线点**：录制库面板 `main_window.cpp:1607` 旁加"导出当前缓冲 IQ 段…"按钮（或频谱工具条加菜单项），调 `engine_->exportIqSegment()`；参数（样本数/中心频率/路径）走文件对话框，不硬编码。真实数据源=引擎实时环形缓冲。

#### 孤儿 B：`set_network_audio_sink` / `get_network_audio_status`
- **后端**：`cpp/src/dsp/network_audio_sink.h:48 NetworkAudioSink`（解调后 48 kHz PCM 经 UDP/TCP 裸流外送）；`control_hub.cpp:1129-1180` 已接 `setNetworkAudioSink` / `getNetworkAudioStatus`。
- **UI 现状**：UI 只有本地音频通路——`main_window.cpp:1968-1969 setVolume`、`:2508-2512` 音频设备选择；grep `网络音频/audioSink/netAudio/udp audio` 在 `cpp/src/ui/` 0 命中。本地音量/设备组里没有"音频外送网络"开关。
- **判定**：**该补 UI**。
- **建议接线点**：左栏源/连接组（`main_window.cpp:262 gSrc`）或音频相关区域加一组"网络音频外送"控件：协议 UDP/TCP + host:port + 使能勾选；状态回显（listening/no client/dropped frames）走 `getNetworkAudioStatus` 同款字段。真实数据源=引擎解调后音频 tap（`engine_->setNetworkAudioSink()`），不 mock。

#### 孤儿 C：`start_scan_link` / `stop_scan_link` / `get_scan_link_status`
- **后端**：`cpp/src/dsp/scan_link.h:64 ScanActivityLink`——在 `FrequencyScanner` 裸"发现载波"之上做"停驻→解码→录制"的无头桥（`onRetune/onActivityFound/onDwellEnded` 三个 seam）；`control_hub.cpp:1200-1230` 已接 start/stop/state（dwell_count/parked_freq/retune_log）。
- **UI 现状**：UI 书签/扫描页（`main_window.cpp:1266-1371`）用的是**另一个类** `dsp::FrequencyScanner`（`:1269 scanner_`），`scanStartBtn_/scanPauseBtn_/scanStopBtn_` 只做手动波段扫描、命中即停，**不会**自动 armed 录制/解码器。grep `ScanActivityLink/scanLink` 在 `cpp/src/ui/` 0 命中——UI 完全不知道这个"活动自动记录"桥存在。
- **判定**：**该补 UI**。
- **建议接线点**：书签/扫描页 `main_window.cpp:1353 scanBtnRow` 旁加"活动自动记录"开关：勾选后构造 `ScanActivityLink` 并把三个 seam 绑到现有 `engine_`（retune→`setCenterFrequency`；activityFound→切 parked VFO 解码模式 + `startRecording`；dwellEnded→`stopRecording`）。状态行回显 dwell/parked/retune 计数。真实数据源=`FrequencyScanner` RSSI + 真实 recorder，与 `scan_link.h` 的 honesty contract 一致（静默频带不产生动作）。

### 2.2 有后端无工具（引擎/面板有真实能力，但 45 工具面未覆盖）

#### 孤儿 D：`list_gains`
- **后端**：`control_hub.cpp:873 cmdListGains` → `engine_->availableGainsDb()` 返回离散增益步长数组。
- **工具面现状**：HTTP 线命令存在，但 **Agent dispatchTable（`agent_tools.cpp:1147-1198`）无此工具**——45 清单里没有 `list_gains`。
- **但**：`cmdGetCapabilities`（`control_hub.cpp:897-899`）已经把**同一个** `availableGainsDb()` 数组塞进 `get_capabilities` 返回的 `gains_db` 字段。
- **判定**：**架构性不补（作为独立 Agent 工具）**。理由：增益步长与设备能力是同一份快照，`get_capabilities` 已带 `gains_db`；再开一个 `list_gains` 工具会把同一数据拆两处、日后漂移。HTTP 线命令 `list_gains` 可保留（已有外部调用方兼容），Agent 面不需要新工具。AI 需要增益表时读 `get_capabilities.gains_db` 即可。

#### 已排查、非孤儿的"后端能力"
- `control_hub` 里其余未进 45 清单的写命令（`set_watch/set_gated_recording/set_anr/set_tuner_agc/set_rtl_agc/set_muted/set_sample_rate/set_gain`）在 UI 均有对应勾选/滑杆/下拉（grep `watch/anr/AGC/muted` 均命中 `main_window.cpp`），是桌面直控路径，不缺 UI；Agent 面是否补写命令属另一个议题（写命令面），本轮不展开。
- `get_telemetry`（1 Hz 遥测快照，含 connected/center_hz/gain_db/squelch_open/recording）在 UI 状态栏（`main_window.cpp:1933-1962`）已呈现；Agent 已有 `get_status` 覆盖核心字段，不再单列。

## 3. 汇总

- **有工具无 UI 入口：3 组（5 个工具）** —— A `export_iq_segment`、B `set_network_audio_sink`+`get_network_audio_status`、C `start_scan_link`+`stop_scan_link`+`get_scan_link_status`。判定全部为**该补 UI**，且都有真实数据源（引擎缓冲 / 音频 tap / FrequencyScanner+recorder），建议接线点见 §2.1。
- **有后端无工具：1 项** —— D `list_gains`，判定**架构性不补**（`get_capabilities.gains_db` 已覆盖）。
- **任务点名的 5 个嫌疑（get_capabilities/get_recording_state/predict_passes/get_acars_packets/get_navtex_messages）经逐项核实均已接线，非孤儿**，见 §1。

## 4. 硬约束自查

- 零代码改动（仅新增本文件）；未 git add/commit/push。
- 所有"该补 UI"建议只接真实数据源（engine 现有 getter/signal），禁 mock；活动参数零硬编码（对话框/勾选输入）。
- 不预置 TLE / 呼号（predict_passes 的 TLE 走 celestrak 在线拉取，本走查未改动）。
- 全文无竞赛类字样；GPL 机制参考（SDR++ scanner/scheduler/network_sink）仅作 clean-room 对照说明，措辞中立。
