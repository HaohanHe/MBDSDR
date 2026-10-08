# 移动端（Flutter）↔ 桌面端（C++ Qt）工具面对照核查

- 仓库：MBDSDR，核对基线 HEAD = `8dfcaa1`。
- 交付方式：**只读代码审查 + 本文档落档 + mobile/ 下最小 Dart 改动**。云端无 Flutter SDK，**未构建、未运行 flutter analyze/test**；所有 Dart 改动均标注「待 SDK 环境验证」。未 `git add/commit/push`，未操控 GUI。
- 未触碰的在途文件：`cpp/src/core/tokens.h`、`cpp/src/ui/main_window.{cpp,h}`、`cpp/src/ui/status_format.h`、未跟踪 `cpp/src/ui/tune_history.h`、`cpp/tests/ui_diag_freeze.cpp`（均属其他会话在途/隔离文件，本次一律未动）。
- 桌面侧基线沿用 `docs/learn/phase63/tool-three-channel-47.md`（47 Agent 工具 = 29 写 / 18 读；三通道一致）。本文档逐工具判定沿用其差异判定风格（命名漂移 / 通道有无 / 设计意图）。

## 0. 通道判定结论（mobile 工具可达性真实机制）

mobile 的 AI 工具**不经过桌面 ControlHub**，存在三条互不混淆的通道：

| 通道 | 载体 | 方向 | 是否接 AI 工具目录 |
|---|---|---|---|
| **本地 rtl_tcp 引擎**（AI 工具真实执行链） | `RadioApi`（`radio_controller.dart`）→ `RtlTcpClient`（`rtl_tcp_client.dart`）→ 裸 rtl_tcp TCP socket | mobile app 自己作 SDR 接收机，直连一个 rtl_tcp 服务端 | **是**。`buildRadioTools()` 的 execute 全部直调 `RadioApi` |
| **桌面 ControlHub HTTP 只读查看器** | `ControlHubClient`（`control_hub_client.dart`）`GET /status /pocsag_messages /m17_calls /vor_radial` | 只读拉桌面解码快照 | **否**。仅 `remote_decoder_panel.dart`（频谱页内）渲染；无 POST/写能力，未注入 `buildRadioTools` |
| **GNSS/串口** | `gnss/usb_serial_port.dart` + `nmea_parser.dart` | 仅取 GPS 位置 | 否（测站坐标来源，非射频控制） |

- 装配点：`home_shell.dart` 的 `ChatPage(clientFactory: () => AiClient(tools: buildRadioTools(radio, manualMode: settings.aiManualMode)))`。AI 工具 = 本地 rtl_tcp 引擎的薄包装。
- 结论：**「mobile 实际可用工具集」= `buildRadioTools()` 注册的集合**，不是 `tool_catalog.dart` 里手写的 35 条桌面快照。`tool_catalog.dart` 开宗明义是「只读参考目录，不是移动端现在能调这些工具的声明」。
- 历史文档里的 `kFlutterUngatedReadTools=18` 是**桌面侧**金集（`cpp/tests/test_tool_registry.cpp:104-114`），不是 mobile 侧读数；勿把它当作 mobile 工具数。

## 1. mobile 全端工具面盘点

### 1.1 改造前（HEAD `8dfcaa1` 原状）
`buildRadioTools()`（`ai_tools.dart`）注册 **8 个**：
- 写（6）：`set_frequency`、`set_mode`、`set_gain`、`set_sample_rate`、`start_recording`、`stop_recording`
- 读（2）：`get_status`、`predict_passes`

### 1.2 盘点发现的两个真问题（本次已处理）
1. **`predict_passes` 在生产环境形同未接线**：`buildRadioTools` 把 `passesService`/`station` 设计为可选注入（`ai_tools.dart:56-57`），但唯一装配点 `home_shell.dart` 改造前**二者都没传**。于是该工具运行时恒走 `passesService == null` 分支，回「卫星过境预测未配置」——尽管天空页（`sky_page.dart`）已经在用同一套 Celestrak TLE + 本地 SGP4 预测链。
2. **静噪能力有引擎、有 UI、却没有 AI 工具**：`RadioApi` 已完整实现 `setSquelchEnabled/setSquelchThresholdDb/setSquelchAuto` 与 `squelchEnabled/ThresholdDb/Open/LevelDb/Auto` 读取（`radio_controller.dart:100-121,821-849`），频谱页也有完整静噪滑杆（`spectrum_page.dart:846-881`），但 AI 工具目录没有 `set_squelch` / `get_squelch_status`——而桌面端这两个是正式 Agent 工具。

## 2. 写门（gate）一致性

- **桌面侧**：`llm_worker.cpp:118` `if (manualMode && isWriteTool(name)) return gatedToolResult(name);`（先于任何引擎接触），信封 `{ok:false,gated:true,error:"手动模式：未执行 <name>"}`。
- **mobile 侧**：`ai_tools.dart` 在 `manualMode == true` 时，把硬编码 `mutatingTools` 集合里的每个工具包一层 `_gated()`（`ai_tools.dart:35-40,479-487`），信封 `{ok:false,gated:true,error:"手动模式：未执行 <name>",hint:...}`；只读工具（`get_status`/`get_squelch_status`/`predict_passes`）原样放行。
- **判定：语义一致**。两侧同为「手动模式拦写、放读」，信封结构同为 `{ok:false,gated:true,error:...}`，仅 mobile 多一个 `hint` 字段（供对话 chip 展示）。
- **本次改动的门一致性**：新增写工具 `set_squelch` 已加入 `mutatingTools`（`ai_tools.dart:486`）；新增读工具 `get_squelch_status` 不入集合、保持放行。与桌面「`set_squelch` 写 / `get_squelch_status` 读」标志完全对齐。
- 差异点（架构性不修）：mobile 写门是**硬编码名单**，桌面 `isWriteTool()` 直接读 spec 的 `write` 标志（无并行硬编码集合，见三通道表 §0）。这是实现方式差异，非缺陷；mobile 工具面小、硬编码可接受。

## 3. 桌面 47 ↔ mobile 对照表

判定列含义：
- **一致**：mobile 已有等价工具（同名或命名漂移、能力等价）。
- **该修（已实现）**：缺工具且 mobile 有真实用途 + 真实数据源，本轮已改 Dart。
- **该修（仅方案）**：引擎/数据源可达，但需额外注入或长时异步语义设计，本轮不落地。
- **架构性不修**：桌面专用、无移动场景、无数据源，或 mobile 经 HTTP 通道本就可达（仅未注入 AI）。

| # | 桌面工具 | W/R | mobile 对应 | 判定 |
|---|---|---|---|---|
| 1 | tune_frequency | W | `set_frequency`（本地 rtl_tcp 直调） | 一致（命名漂移，功能等价；不冒充桌面 `tune`） |
| 2 | set_mode | W | `set_mode` | 一致（mobile 仅 nfm/wfm，桌面 6 模式子集；DSP 只实现两路解调） |
| 3 | start_recording | W | `start_recording` | 一致（同名；mobile 录解调音频 WAV vs 桌面录 IQ SigMF，产物不同入口一致，`ai_tools.dart` 注释已说明） |
| 4 | stop_recording | W | `stop_recording` | 一致 |
| 5 | scan_band | W | `RadioApi.startScan` 引擎在（真实调谐+电平量测+命中日志），无 AI 工具 | **该修（仅方案）**：长时异步扫描无「返回峰值列表」通道，需先设计同步/回调语义 |
| 6 | set_bandwidth | W | 无（`RadioApi` 无带宽缝，`FmDemod` 固定） | 架构性不修（mobile DSP 无带宽档位） |
| 7 | get_status | R | `get_status` | 一致（五态 status + connected 布尔披露对齐桌面多信号语义） |
| 8 | predict_passes | R | `predict_passes` | 一致（同名纯函数；**本轮修复接线**，见 §4-修1） |
| 9 | calibrate_frequency | R | 无（桌面参考信号 PTT 测量） | 架构性不修（无移动参考信号场景） |
| 10 | apply_frequency_correction | W | `RtlTcpClient.setPpm`（0x05）协议缝在，但 `RadioApi` 未暴露 | **该修（仅方案）**：需在 `RadioApi`/`RadioController` 加 ppm 透传缝后再包工具 |
| 11 | get_pocsag_messages | R | `ControlHubClient.fetchPocsagMessages` 已有（UI 查看器） | 架构性不修（经 HTTP 通道已可达，仅未注入 AI；注入需可选 client + 未配置空态） |
| 12 | get_m17_calls | R | 同上 `fetchM17Calls` | 架构性不修（同上） |
| 13 | get_vor_radial | R | 同上 `fetchVorRadial` | 架构性不修（同上） |
| 14 | get_acars_packets | R | 无（`ControlHubClient` 无此方法，mobile 不解码 ACARS） | 架构性不修（无数据源） |
| 15 | get_navtex_messages | R | 无（同上） | 架构性不修（无数据源） |
| 16 | export_iq_segment | W | 无（mobile 录解调音频，无 SigMF IQ 导出管线） | 架构性不修 |
| 17 | set_network_audio_sink | W | 无（mobile 是 rtl_tcp 消费方，不做网络音频转发） | 架构性不修 |
| 18 | get_network_audio_status | R | 无 | 架构性不修 |
| 19 | start_scan_link | W | 无（桌面 ScanActivityLink 活动链，mobile 频段扫描语义不同） | 架构性不修 |
| 20 | stop_scan_link | W | 无 | 架构性不修 |
| 21 | get_scan_link_status | R | 无 | 架构性不修 |
| 22 | set_squelch | W | `set_squelch` | **该修（已实现）**（本轮新增，见 §4-修2） |
| 23 | get_squelch_status | R | `get_squelch_status` | **该修（已实现）**（本轮新增） |
| 24 | set_noise_blanker | W | 无（mobile DSP 无 noise blanker） | 架构性不修（无数据源） |
| 25 | get_noise_blanker_status | R | 无 | 架构性不修 |
| 26 | list_bookmarks | R | `SettingsService.bookmarks` 有，无 AI 工具 | **该修（仅方案）**：需把书签存储注入 `buildRadioTools` |
| 27 | add_bookmark | W | 同上 | **该修（仅方案）** |
| 28 | tune_to_bookmark | W | 同上（命中后走 `radio.setFrequencyHz`） | **该修（仅方案）** |
| 29 | delete_bookmark | W | 同上 | **该修（仅方案）** |
| 30 | list_vfos | R | 无（mobile 单频点，无 VFO 抽象） | 架构性不修 |
| 31 | add_vfo | W | 无 | 架构性不修 |
| 32 | switch_vfo | W | 无 | 架构性不修 |
| 33 | rename_vfo | W | 无 | 架构性不修 |
| 34 | set_vfo_armed | W | 无 | 架构性不修 |
| 35 | set_vfo_frequency | W | 无 | 架构性不修 |
| 36 | set_vfo_mode | W | 无 | 架构性不修 |
| 37 | set_vfo_bandwidth | W | 无 | 架构性不修 |
| 38 | list_recordings | R | `RecordingStore` 有录音索引，无 AI 工具 | **该修（仅方案）**：需注入 `RecordingStore` |
| 39 | delete_recording | W | 同上 | **该修（仅方案）** |
| 40 | export_recording | W | 无（mobile 无「导出到目标路径」概念，文件在应用目录） | 架构性不修 |
| 41 | set_fft_params | W | `FftProcessor` 固定 2048，无参数缝 | 架构性不修 |
| 42 | set_color_map | W | 无（mobile 色板无 AI 持久化键） | 架构性不修 |
| 43 | get_spectrum_status | R | `get_status` 已含频率/模式/采样率；FFT 参数固定 | 架构性不修（信息已覆盖） |
| 44 | set_doppler_compensation | W | mobile 多普勒闭环活在 `SkyPage` 卫星控制器（`sky_page.dart:189`），不在 `RadioApi` | 架构性不修（多普勒与卫星过境耦合，非通用射频缝） |
| 45 | connect_network_source | W | mobile 本身就是 rtl_tcp 客户端 | 架构性不修 |
| 46 | get_capabilities | R | `get_status` 已覆盖；桌面本就回诚实空态 | 架构性不修 |
| 47 | get_recording_state | R | start/stop 返回里已带 `recording` 布尔 | 架构性不修（信息已覆盖） |

### 3.1 mobile 领先桌面（桌面 47 里没有，单独记录）
- `set_gain`、`set_sample_rate`：桌面侧这两个是 **ControlHub-only 底层原语**（三通道表 §2.3，刻意不暴露给 LLM），mobile 却把它们暴露成 AI 工具。
- 判定：**架构性不修**。这是两端设计意图差异（mobile 选择暴露、桌面选择收敛），不是 mobile 缺工具；不计入 47 对照计数。

### 3.2 计数差异表

| 判定 | 数量 | 工具 |
|---|---|---|
| 一致 | 8 | tune≈set_frequency、set_mode、start_recording、stop_recording、get_status、predict_passes、（+本轮新增后一致的 set_squelch、get_squelch_status） |
| 该修（已实现） | 2 工具 + 1 接线 | set_squelch(W)、get_squelch_status(R)；predict_passes 接线修复 |
| 该修（仅方案） | 8 | scan_band、apply_frequency_correction、list/add/tune/delete_bookmarks(4)、list_recordings、delete_recording |
| 架构性不修 | 29 | 见上表（VFO 族 8、解码快照/无数据源、网络音频/扫描链、IQ/频谱/色板/多普勒/能力态等） |
| 合计（桌面 47） | **47** | 8 + 2 + 8 + 29 = 47 ✓ |

> 说明：`predict_passes` 在表里按「一致」计（同名同能力），其接线修复单列在 §4-修1，不重复计数。改造后 mobile AI 工具集 = 10（7 写 / 3 读）。

## 4. 该修项：实现与方案

### 修1（已实现）：predict_passes 接线 —— `home_shell.dart`
- 问题：装配点没传 `passesService`/`station`，工具恒回「未配置」。
- 最小改动：
  - `home_shell.dart:19` 新增 `import '../services/sat_passes_provider.dart';`
  - `_HomeShellState` 新增共享字段 `final SatPassesService _passesService = SatPassesService();`（构造无副作用，首次 predict 才联网；与天空页同一预测路径）
  - `buildRadioTools(...)` 调用处补 `passesService: _passesService, station: station`（`station` 在 `home_shell.dart` 已由 settings 算出；未配置时为 null，工具自身回「未配置测站坐标」诚实空态）。
- 不改工具签名、不改工具数（仍走既有可选注入缝）。**待 SDK 环境验证**：确认对话内 predict_passes 能真实返回过境、未配测站时仍诚实空态。

### 修2（已实现）：新增 set_squelch(W) / get_squelch_status(R) —— `ai_tools.dart`
- `ai_tools.dart:294-364` 新增两个 `AiTool`：
  - `set_squelch`：可选 `enabled(bool)` / `threshold_db(num, -100..-20)` / `auto(bool)`，至少一项；过 `_disconnectError` 连接门；顺序「先 auto 后 threshold」使手动门限覆盖自动（与频谱页拖杆语义一致）；返回回显 `squelch_enabled/auto/threshold_db/open`。
  - `get_squelch_status`：只读，返回 `squelch_enabled/auto/threshold_db/open/level_db`，不需连接门。
- `ai_tools.dart:486` 把 `set_squelch` 加入手动模式 `mutatingTools` 写门。
- 同步：
  - `test/ai_tools_real_link_test.dart:319` 金集断言 8→10、补两个工具名（测试注释本就要求「新增工具须同步更新本断言」）。
  - `app/tool_catalog.dart:193` `kMobileImplementedToolNames` 补 `set_squelch`/`get_squelch_status`（两名本就在桌面 35 条快照内，子集校验通过）；头注释计数 8→10。
- **待 SDK 环境验证**：`flutter analyze` 无类型错、`flutter test` 下 `ai_tools_real_link_test`（10 工具审计）、`ai_mode_gate_test`（手动门）、`tools_catalog_test`（已接入子集）全绿。

### 仅方案（本轮不落地，待有 SDK/跨端改动环境）
| 方案 | file:line | 阻塞原因 |
|---|---|---|
| scan_band | `RadioApi.startScan` 已在 `radio_controller.dart:650` | 长时异步扫描无「结果返回」通道；需设计 AI 同步等待或 start/status 拆分，非纯小改 |
| apply_frequency_correction | `RtlTcpClient.setPpm` `rtl_tcp_client.dart:183` | 需先在 `RadioApi`/`RadioController` 加 ppm 透传缝，再包工具 |
| bookmarks 4 件套 | `SettingsService.bookmarks`（`home_shell.dart:198` 已用 add/remove） | 需把书签存储注入 `buildRadioTools`，涉及 settings 依赖注入 |
| list/delete_recordings | `RecordingStore`（`home_shell.dart:177` 已 `_maybeRead`） | 需注入 store 进工具构造，delete 需真实文件删除校验 |

## 5. 文档快照陈旧问题（记录，不在本轮纠正）
- `tool_catalog.dart` 自称「桌面 35 个 Agent 工具」，实际桌面已是 **47**（三通道表 §0）。这是手写只读快照滞后；`tools_catalog_test.dart:17` 还钉死 `kDesktopToolCatalog.length == 35`、页面文案「桌面端共 35 个」。
- 判定：扩到 47 属于**另一次快照同步工程**（要同时补 12 条桌面条目、改测试断言、改页面文案），超出本次「mobile↔desktop 工具面对照」职责，仅记录在案。本轮只把 mobile 已接入名单与计数改对。

## 6. 改动文件清单
- `mobile/lib/app/ai_tools.dart`（+set_squelch/get_squelch_status；写门补 set_squelch）
- `mobile/lib/app/home_shell.dart`（predict_passes 接线：import + service 字段 + 两个注入参数）
- `mobile/lib/app/tool_catalog.dart`（已接入名单 +2、头注释计数 8→10）
- `mobile/test/ai_tools_real_link_test.dart`（金集 8→10、补名）

## 7. 诚实未完成项 / 自查
- **未构建未运行**：云端无 Flutter SDK，未跑 `flutter analyze` / `flutter test`；§4 两处实现均标注「待 SDK 环境验证」，不假装通过。
- **未触碰**：cpp/ 在途改动（tokens.h、main_window.*、status_format.h、未跟踪 tune_history.h、ui_diag_freeze.cpp）一律未动；未 `git add/commit/push`；未操控 GUI。
- **计数**：8 + 2 + 8 + 29 = 47，与桌面 47 对齐；mobile AI 工具集 8→10（7 写 / 3 读）。
- **措辞**：未引入竞技/赛事类表述；GPL 相关保持中立；未编造过境/读数（predict_passes 无 TLE 时仍走既有诚实空态分支）。
