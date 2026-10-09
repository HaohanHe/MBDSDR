# Phase63 — MBDSDR 47 工具「读/写分类 ↔ 实际副作用」边界完整性核对（只读审计 + 落档）

- **HEAD**: `ac3d86cf2646469f9378beb22a2428ea3d01a09d`（审计前后均未变）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 本轮不再审 gate 语义本身（Agent 单点 `llm_worker.cpp`、CH 单点 `control_hub.cpp:360`、HTTP 透传、gated 呈现四通道历轮已审）。本轮只审**分类边界本身**——47 个工具逐一核对 `isWriteTool` 分类与真实副作用是否一致：
  - **漏 gate 的写工具** = 有副作用却被分为读 → 手动模式下绕过拦截（危险，应拦未拦）；
  - **误 gate 的读工具** = 无副作用却被分为写 → 手动模式下被误拦（保守误伤）。
- **分类真相来源**：`isWriteTool()`（`agent_tools.cpp:1365-1371`）**不再有平行硬编码写集合**，而是逐名查 `ToolSchemaSpec::write`。该字段默认 `bool write = false`（`tool_schema.h:42`），未显式置 `true` 者即读。金集断言见 `test_tool_registry.cpp:162-183`（写集契约）与 `:191-206`（读集跨端 parity）。
- **方法**: 全程只读源码；offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`QT_QPA_PLATFORM=offscreen`，clean env 未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI。

---

## 0. 分类边界统计（与金集契约一致）

| 维度 | 数量 | 来源 |
|---|---|---|
| 工具总数 | **47** | `test_tool_registry.cpp:139` 断言 `schemas.size()==47`；`dispatchTable()`（`agent_tools.cpp:1308-1362`）逐行枚举 |
| 写（write=true，手动模式拦截） | **29** | `kExpectedWriteTools`（`test_tool_registry.cpp:67-85`）逐名核对一致 |
| 读（write=false，两模式皆执行） | **18** | `kFlutterUngatedReadTools`（`test_tool_registry.cpp:104-114`）逐名核对一致 |

> 与任务书「写 29 / 读 18」预期**完全吻合**。`isWriteTool()` 与 schema 表同源，无第二份写集合可漂移。

---

## 1. 47 工具读/写分类对照表（on-wire 顺序）

「实际副作用」列追到 executor 函数体（`agent_tools.cpp`）与它下发的引擎/持久化调用。判定：✅=分类与副作用一致；⚠=边界项（结论见 §2）。

### 1.1 写工具（29）— 逐一确认确有引擎/状态/磁盘副作用

| # | 工具名 | 分类 | 实际副作用（证据） | 判定 |
|---|---|---|---|---|
| 1 | `tune_frequency` | 写 | `engine->onSetCenterFreq(f)`（`agent_tools.cpp:91`）改中心频率 | ✅ |
| 2 | `set_mode` | 写 | `engine->setDemodMode(up)`（`:114`）改解调模式；模式白名单校验 | ✅ |
| 3 | `start_recording` | 写 | `engine->startRecording()`（`:130`）起录 SigMF，真实 bool 回传 | ✅ |
| 4 | `stop_recording` | 写 | `engine->stopRecording()`（`:146`）停录 | ✅ |
| 5 | `scan_band` | 写 | `engine->scanBand()` 循环内 `source_->setCenterFreq(f)`（`spectrum_engine.cpp:272`）扫频并把机停留扫频末点 | ✅ 真扫频，见 §2.2 |
| 6 | `set_bandwidth` | 写 | `engine->setBandwidth(bw)`（`:230`）改信道带宽 | ✅ |
| 7 | `apply_frequency_correction` | 写 | `savePpmSetting(ppm)` 写 QSettings `rtl/ppm` + `engine->setPpm(ppm)`（`:404-405`）持久化并下发 | ✅ |
| 8 | `export_iq_segment` | 写 | `engine->exportIqSegment()` 落 `.sigmf-data`+`.sigmf-meta` 两文件（`:175`）；可选先重调 | ✅ 文件写 |
| 9 | `set_network_audio_sink` | 写 | `routedOk()` 路由（`:638`），本地不执行；真实效果在 CH 通道落网络音频 tap | ✅ 路由写，见 §2.3 |
| 10 | `start_scan_link` | 写 | `routedOk()` 路由（`:661`）起扫描驻留解码链路 | ✅ 路由写 |
| 11 | `stop_scan_link` | 写 | `routedOk()` 路由（`:668`）停链路 | ✅ 路由写 |
| 12 | `set_squelch` | 写 | `engine->setSquelchEnabled/Threshold/Auto`（`:694-702`）真下发静噪三参数 | ✅ 直写引擎 |
| 13 | `set_noise_blanker` | 写 | `engine->setNoiseBlanker(on)`（`:737`）真开关 | ✅ 直写引擎 |
| 14 | `add_bookmark` | 写 | `bookmarks->add(b)`（`:792`）自动排序**并落盘保存** | ✅ 真持久化，见 §2.1 |
| 15 | `tune_to_bookmark` | 写 | `engine->vfoSetOffset(selectedVfoId, b.freq)`（`:827`）实调当前选中 VFO | ✅ |
| 16 | `delete_bookmark` | 写 | `bookmarks->removeAt(i)`（`:851`）改书签集 | ✅ |
| 17 | `add_vfo` | 写 | `engine->vfoAdd()`（`:889`）新增 VFO 信道 | ✅ |
| 18 | `switch_vfo` | 写 | `engine->vfoSelect(idx)`（`:905`）改选中信道态 | ✅ |
| 19 | `rename_vfo` | 写 | `routedOk()` 路由（`:927`），重命名落 control 侧 | ✅ 路由写 |
| 20 | `set_vfo_armed` | 写 | `engine->vfoSetArmed(id,en)`（`:952`）后台并行解调开关 | ✅ |
| 21 | `set_vfo_frequency` | 写 | `engine->vfoSetFreq(id,hz)`（`:983`）调该 VFO 频率 | ✅ |
| 22 | `set_vfo_mode` | 写 | `engine->vfoSetMode(id,up)`（`:1018`）改该 VFO 模式 | ✅ |
| 23 | `set_vfo_bandwidth` | 写 | `engine->vfoSetBandwidth(id,bw)`（`:1048`）改该 VFO 带宽 | ✅ |
| 24 | `delete_recording` | 写 | `QFile::remove(recDir/file)`（`:1100`）删录制目录内文件（防路径穿越） | ✅ 破坏性文件写 |
| 25 | `export_recording` | 写 | `QFile::copy(src,outPath)`（`:1123`）复制导出新文件 | ✅ 文件写 |
| 26 | `set_fft_params` | 写 | `engine->setFftSize/setWindowType/setAverageMode`（`:1139-1156`）改频谱参数 | ✅ |
| 27 | `set_color_map` | 写 | `QSettings().setValue("view/wfColormapFile",p)`（`:1171`）持久化偏好 | ✅ |
| 28 | `set_doppler_compensation` | 写 | `surf->setDopplerCompensationEnabled(on)`（`:1210`）切 1Hz 距离率自动重调；无 UI 面时诚实 `available=false` 不动作 | ✅ 真写（无面时诚实空转） |
| 29 | `connect_network_source` | 写 | `engine->connectRtlTcp(host,port)`（`:1233`）真 TCP 握手换源 | ✅ |

### 1.2 读工具（18）— 逐一确认零持久化 / 零应用副作用

| # | 工具名 | 分类 | 实际行为（证据） | 判定 |
|---|---|---|---|---|
| 30 | `get_status` | 读 | `centerFreq/demodMode/bandwidth/digitalLockStatus` 及 Doppler 面只读 getter（`:251-264`） | ✅ |
| 31 | `predict_passes` | 读 | `predictSatellitePasses()` 读盘上新鲜 TLE；`engine` 形参未用（`:274-312`） | ✅ |
| 32 | `calibrate_frequency` | 读 | `engine->captureForCalibration(refFreq,…)`：取一次测量，`applied=false`，**不存设置/不 setPpm** | ⚠ 边界，见 §2.4 |
| 33 | `get_pocsag_messages` | 读 | `engine->pocsagMessages(ch)` 只读快照槽（`:446`） | ✅ |
| 34 | `get_m17_calls` | 读 | `engine->m17Calls(ch)` 只读快照（`:476`） | ✅ |
| 35 | `get_vor_radial` | 读 | `engine->vorResult(ch)` 只读；未锁定诚实给 null（`:563-576`） | ✅ |
| 36 | `get_acars_packets` | 读 | `engine->acarsPackets(ch)` 只读快照（`:505`） | ✅ |
| 37 | `get_navtex_messages` | 读 | `engine->navtexMessages(ch)` 只读快照（`:537`） | ✅ |
| 38 | `get_network_audio_status` | 读 | 硬编码 `enabled=false` + 出处 note；不触引擎（`:642-651`） | ✅ 诚实空态 |
| 39 | `get_scan_link_status` | 读 | 硬编码 `scanning/dwelling=false` + note；不触引擎（`:672-683`） | ✅ 诚实空态 |
| 40 | `get_squelch_status` | 读 | `squelchEnabled/ThresholdDb/Auto/Open` 真 getter（`:720-723`） | ✅ |
| 41 | `get_noise_blanker_status` | 读 | `noiseBlankerEnabled()` 真 getter（`:753`） | ✅ |
| 42 | `list_bookmarks` | 读 | 回空数组 + note（后端在 control 层），本地无任何增删（`:759-769`） | ✅ |
| 43 | `list_vfos` | 读 | 遍历 `engine->vfoMarkers()` 只读拼装（`:861-883`） | ✅ |
| 44 | `list_recordings` | 读 | `QDir::entryInfoList` 只读列目录，无写/删（`:1060-1084`） | ✅ |
| 45 | `get_spectrum_status` | 读 | `fftSize/windowType/averageMode` getter（`:1186-1188`） | ✅ |
| 46 | `get_capabilities` | 读 | `sourceCapabilities()/availableGainsDb()` getter，离线诚实空增益表（`:1251-1275`） | ✅ |
| 47 | `get_recording_state` | 读 | `recordingPath/watchEnabled/recordingDir` getter（`:1284-1290`） | ✅ |

---

## 2. 边界疑点结论（任务点名逐项）

### 2.1 存书签类 `add_bookmark` / `save_*` — ✅ 分类正确
`add_bookmark`（#14）→ `bookmarks->add()` 自动排序并落盘（`agent_tools.cpp:792`），是**真写**，写分类正确。仓内**没有**独立 `save_*` 工具名；「保存」副作用内聚在 `add()`/`removeAt()` 里。`list_bookmarks`（#42）只读列、零增删，读分类正确。无漏 gate / 误 gate。

### 2.2 控制类 `scan_band` / `start_recording` / `stop_*` — ✅ 分类正确
- `scan_band`（#5）不是「扫频谱缓冲做分析」的纯查询：`engine->scanBand()` 在循环里**真的逐点 `setCenterFreq` 调谐并读 IQ**（`spectrum_engine.cpp:268-280`），调用后机台停在扫频末点。它以「挪动收听点」为产品本身，故写分类正确、应被手动 gate 拦截。
- `start_recording`/`stop_recording`（#3/#4）真启停 SigMF 录制，写分类正确。
- `start_scan_link`/`stop_scan_link`（#10/#11）经 `routedOk` 路由到 CH 的扫描活动链路，是**路由写**，写分类正确（见 §2.3）。

### 2.3 路由型写（本地 executor 空转 echo，真实效果在 CH 通道）— ✅ 写分类正确
`set_network_audio_sink`(#9)、`start_scan_link`(#10)、`stop_scan_link`(#11)、`rename_vfo`(#19) 的 Agent 体只做参数校验 + `routedOk()` echo，**本地不改引擎**。但它们代表一条会被 CH 通道落地的控制命令（开网络音频 tap / 起停扫描链路 / 重命名 VFO）。gate 的语义是「在路由之前就拦住这个写意图」，故写分类（应拦）是**保守且正确**的，不属于「误 gate 的读」——因为这些不是无副作用读，而是「本地未执行、但命令本体是写」的诚实路由。

### 2.4 ⚠ 唯一边界邻接项：`calibrate_frequency`（#32）— 读分类，按设计，**不改**
这是全表唯一需要专门说明的边界。其引擎体 `captureForCalibration`（`spectrum_engine.cpp:294-327`）在 `:304` 会 `if (tuneHz>=0) source_->setCenterFreq(tuneHz)`——**即测量会把中心频率重调到参考频点，且函数返回后不恢复原频点**。表面上它「挪动了机台频率」，与 `scan_band` 的副作用同形。

但仓内**故意**把它钉为 `write=false`（`tool_schema.cpp:227`，注释 `:211-218`），理由是：
1. 它**不持久化任何设置**（不写 QSettings、不 `setPpm`），回包 `applied=false`（`agent_tools.cpp:362`）；测得的 ppm 要落地必须另调被 gate 的 `apply_frequency_correction`(#7)；
2. 重调的目标频点是**调用方自己显式给出的 `reference_freq_hz`**（参考信号所在频点），是「为测量而临时对准参考」的内禀动作，不是替用户改收听配置；
3. 测试把它钉进跨端读集 `kFlutterUngatedReadTools`（`test_tool_registry.cpp:105`），并在 `:87-103` 长注释明确「desktop-only 读，驱动 `captureForCalibration`，不 gate」。

**判定：这是「持久化/配置写 vs. 测量内禀重调」的一条有意划线，非漂移缺陷，不翻转。** 对照 `export_iq_segment`(#8)：它也带可选 `tune_hz` 重调（`spectrum_engine.cpp:359`），之所以被判写，是因为它**额外落磁盘文件**——写判定的单位是「配置/持久化副作用」，单看「重调一下频率」不足以单独 gate，这与 `calibrate_frequency` 的处理自洽。

> 诚实标注：此划线是**可辩护但非无争议**的设计取舍。若未来产品定义收紧为「任何挪动机台频率的动作都须手动确认」，则 `calibrate_frequency` 应改 `write=true`——届时最小改动是 `tool_schema.cpp:227` 一行 `s.write = false` → `true`，并同步 `test_tool_registry.cpp:105` 把它从 `kFlutterUngatedReadTools` 移出、`:67` `kExpectedWriteTools` 加入。本轮按既有契约**不改**。

### 2.5 `set_*` 全族 / `get_*` 纯读 — ✅ 与任务预设一致
- `set_squelch` / `set_noise_blanker`（#12/#13）直写引擎 setter，写分类 ✅；
- 其余 `set_*`（mode/bandwidth/fft/color_map/vfo_*/doppler/network_audio_sink）逐一追到真 setter / QSettings / 路由，写分类全部 ✅；
- 所有 `get_*`（#30/#33-41/#45-47）均为 getter / 只读快照槽 / 只读目录列举，零副作用，读分类 ✅。

---

## 3. 差异判定清单（该修 / 架构性不修）

| 项 | 结论 | 说明 |
|---|---|---|
| 漏 gate 的写工具（有副作用却分读） | **0 例** | 29 个写分类工具逐一追体，均确有引擎/状态/磁盘/路由副作用；无「藏写于读」。 |
| 误 gate 的读工具（无副作用却分写） | **0 例** | 18 个读分类工具逐一确认零持久化/零应用；路由型写(#9/#10/#11/#19)是真控制写，不算误拦。 |
| `calibrate_frequency` 临时重调频点 | **架构性不修（读 by design）** | 见 §2.4：有意划线、测试钉死、零持久化。最小翻转方案已在 §2.4 备记，本轮不执行。 |
| `isWriteTool` 平行写集合漂移 | **不存在** | `isWriteTool()` 逐名查 `ToolSchemaSpec::write`（`agent_tools.cpp:1365-1371`），无第二份硬编码写集可漂移。 |

**净结论：47/47 分类与实际副作用一致，本轮无「该修」项。**

---

## 4. 金集实跑（offscreen 既有二进制，clean env）

环境：`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`QT_QPA_PLATFORM=offscreen`，**未设 `MBDSDR_TEST_SOURCE`**，仓库 `cpp/build` 既有二进制直跑，未重编译、未用 `/tmp` 做工作区。

| 二进制 | 结果 | 计数 |
|---|---|---|
| `test_tool_registry` | 全绿 | **8 passed / 0 failed**（含 `writeReadSplit_registryMatchesContract`、`readOnlySet_parityWithFlutter`、`completeness_everySchemaHasExecutor` 断言 47 工具） |
| `test_agent` | 全绿 | **33 passed / 0 failed**（含 `manualMode_gateSpotCheckAllWrites`、`bookmarkToolsRealExecutionWithInjectedStore`、`vfoEditToolsLandAndReadback`） |
| `test_control_hub` | 全绿 | **28 passed / 0 failed**（含 `phase26WritesAreGatedAndBadArgsHonest`、`bookmarksPersistToQSettings`） |
| `test_control_http` | 全绿 | **14 passed / 0 failed**（HTTP 透传 gate 通道） |
| **合计** | **全绿** | **83 passed / 0 failed / 0 skipped** |

> `test_agent` 日志里 `[Recorder] started "/tmp/mbdsdr_agent_cap_rec/...sigmf-data"` 是**测试二进制自身**对 `start_recording` 的录制 exercise 行为（离线测试源真写 SigMF），非本次审计动作；审计方未往 `/tmp` 放任何工作文件。`[RtlSdrSource] no librtlsdr ops bound` 为离线桩正常提示，非失败。

---

## 5. 红线扫描

| 红线 | 结果 |
|---|---|
| 改源码 | **未改**。全程只读 `cpp/src/ai/tool_schema.cpp`、`agent_tools.cpp`、`dsp/spectrum_engine.cpp`、`tests/test_tool_registry.cpp`。 |
| git add / commit / push | **未执行**任何。 |
| HEAD 漂移 | `ac3d86c` 审计前后一致。 |
| 未跟踪隔离文件 | **未动** `cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc(.cpp)`、`cpp/tests/ui_diag_freeze.cpp`。 |
| 并行会话在途改动 | 审计中途 `git status` 出现 `M cpp/src/ui/main_window.cpp`、`M cpp/src/ui/main_window.h`（首查时尚无）——判定为**并行会话在途改动，非本轮产物**，未误判、未回滚、未触碰。 |
| mock / 操控 GUI | 无 mock；offscreen 跑既有二进制，无 GUI 交互。 |
| 禁用字样 | 全文无「比赛 / competition」字样。 |

---

## 6. 诚实未完成项

1. **未逐行下钻 CH/HTTP 两侧命令表的副作用**：本轮职责边界在 Agent 工具面的 `isWriteTool` 分类；CH 单点 gate（`control_hub.cpp:360`）与 HTTP 透传历轮已审，本轮以金集 `test_control_hub`/`test_control_http` 全绿作为「三通道 gate 一致」的回归证据，未重复逐 handler 追体。
2. **`captureForCalibration` 重调频点后是否会影响 UI 瀑布图显示状态**未做运行期观察（offscreen 无头，无 UI）；仅据源码语义判为「测量内禀、零持久化」。若产品定义收紧，见 §2.4 最小翻转方案。
3. Flutter 移动端工具面（`mobile/lib/app/ai_tools.dart`）按 `test_tool_registry.cpp:87-103` 注释视为只读参考、本轮未打开其源码逐一对账；跨端读集 equality 已由 `readOnlySet_parityWithFlutter` 金集断言背书。
