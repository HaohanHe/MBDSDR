# A4 真链路闭环审计报告（只读）

- 范围：`_PHASE3_SPEC.md` §2 Wave1 / A4、§3 红线（禁假数据 / 无硬件诚实空态 / 证据带 file:line / 拿不准标「推断」/ 原文未给标「原文未给」）。
- 方式：纯只读通读，未改代码、未动 git。
- 审计时间基准：2026-10-01。
- 结论速览：**两端 LLM→工具→真实动作的「调度链」是真实闭环（校验在前、gate 一致、单执行点）；但「工具结果对模型的诚实披露」两端均有缺口——尤其是移动端断开时 4 个动作工具仍回 `ok:true`（红线级）。**

---

## 1. 全路径图（文字形式，逐环标注真实性）

### 1.1 C++ 桌面端（`cpp/src/`）

```
LLM chat（doChat 循环）
  LLMWorker::doChat                       llm_worker.cpp:84   [真执行] 最多 kAiMaxToolRounds 轮
    └─ client_->chat(msgs,tools)          llm_worker.cpp:95   [真调用] 真发 OpenAI 兼容请求；无 key 时不真调（compact 走诚实计数摘要 llm_worker.cpp:61-70）
       └─ 返回 toolCalls
          └─ validateArguments(...)       llm_worker.cpp:118  [真校验] schema(min/max/enum)前置；失败不碰硬件，errorJson 回灌（:120-128）
             └─ dispatchToolCall          llm_worker.cpp:16
                ├─ manualMode && isWriteTool  llm_worker.cpp:21 [真 gate] 直接回 gatedToolResult，不碰 engine
                └─ executeTool               agent_tools.cpp:49 [真分发] 7 工具
                   └─ dsp::SpectrumEngine 各方法（真执行在「当前活动源」上）
                      ├─ 真硬件 RtlSdrSource（connected=true）
                      ├─ 离线 TestSignalSource（connected=false，合成 IQ，诚实标注「非硬件」）
                      └─ 离线 FileSource（回放录制，connected=false）
```

逐环真实性：

| 环 | 位置 | 真实性 |
|---|---|---|
| 循环 / 校验在前 | llm_worker.cpp:84,118-128 | **真执行**：schema 校验失败绝不触硬件，错误以 role=tool 回灌让模型自纠 |
| 手动模式 gate | llm_worker.cpp:21 → agent_tools.cpp:16-38 | **真执行**：写工具在 manualMode 下根本不进 executeTool |
| executeTool 分发 | agent_tools.cpp:49-103 | **真分发**，但**忽略 engine 返回值**（见 §2 表） |
| 引擎动作 | spectrum_engine.cpp 各 slot | **真执行**（作用于当前活动源） |
| 信号源真实性 | 构造 spectrum_engine.cpp:45-51：rtl->start() 失败→TestSignalSource | **真来源选择**：无硬件=合成信号，非编造峰值 |
| 源层诚实标注 | spectrum_engine.cpp:403 isTestSignalActive / :123 sourceCapabilities / device_capabilities.cpp:67-73「RTL-SDR 未连接」 | **真诚实**：connected=false、UI 标「非硬件」 |
| **工具结果对模型披露** | agent_tools.cpp:53-101 | **空态缺口**：结果串里不带 connected / 是否测试信号（见 §2） |

### 1.2 Flutter 移动端（`mobile/lib/`）

```
LLM SSE 循环（complete）
  AiClient::complete                       ai_client.dart:429   [真执行] while < kMaxToolRounds
    └─ buildRequestJson → _transport        ai_client.dart:447-461 [真调用] dart:io SSE；无 key 不发
       └─ 解析 tool_calls（delta 累积）     ai_client.dart:469-499
          └─ validateToolArguments          ai_client.dart:530  [真校验] schema 前置；失败不 execute（:531-532）
             └─ tool.execute(parsed)         ai_client.dart:534
                └─ buildRadioTools 闭包      ai_tools.dart:33    [真分发] 5 工具
                   └─ RadioApi（RadioController）radio_controller.dart:111
                      └─ rtl_tcp 客户端     → 真 rtl_tcp 服务端（真硬件 IQ）
                         断开时：_client==null → _client?.xxx 空安全 no-op（radio_controller.dart:466 等）
```

逐环真实性：

| 环 | 位置 | 真实性 |
|---|---|---|
| 循环 / schema 校验在前 | ai_client.dart:433,530-535 | **真执行**：未知工具 / 非对象参数 / 校验失败都不触硬件 |
| manualMode gate | ai_tools.dart:211-229（构造时烘焙进闭包） | **真执行**：写工具替换为 `_gated`，不调用 radio |
| get_status | ai_tools.dart:193-207 | **真诚实**：回报 `connected: radio.status==connected` |
| 动作工具（4 个写） | ai_tools.dart:55-187 | **空态缺口**：断开时 RadioController 空安全 no-op 不抛异常，工具仍回 `ok:true`（见 §2） |
| RadioController 真实动作 | radio_controller.dart:462-504 | 连接后**真下发** rtl_tcp；断开时只改本地字段（将在下次 connect 时补发） |
| 频谱/解调数据 | radio_controller.dart:351 频谱定时器仅 connect 后启动；:369-391 真 IQ→FFT→解调 | 断开时**真空态**（无数据流动），无假峰值 |

> 说明：与 C++ 不同，Flutter 端**本地根本没有 TestSignalSource**——断开即无 IQ 流，频谱/音频都不产出（诚实空态）；但 AI 动作工具层仍接受调谐并报成功，这是两端共性问题的移动端表现。

---

## 2. 逐工具「无硬件诚实空态」核实表

### 2.1 C++（无硬件 = TestSignalSource 活动，`isTestSignalActive()==true`）

| 工具 | 未连接/测试信号时行为 | 是否诚实 | 证据 file:line / 修复建议 |
|---|---|---|---|
| tune_frequency | `onSetCenterFreq(f)` 在测试源上仅 `setCenterFreq` 存 `f0_`（合成信号内容为固定基带偏音，不随 f0_ 变）；executeTool **无条件**回「已调谐到 X MHz」 | **半诚实**：真改了中心频率读回，但不告诉模型这是测试信号/无真硬件 | agent_tools.cpp:53-57；test_signal.cpp:34,135-149。建议：结果串追加 `source`/`connected` 字段（见 §4-P1） |
| set_mode | 真重建 VFO 解调链；回「模式切换为 X」 | **半诚实**：动作真，未披露无硬件 | agent_tools.cpp:58-62；spectrum_engine.cpp:164-175 |
| start_recording | `engine->startRecording()` 返回 bool，executeTool **忽略该 bool** 直接回「开始录制」。测试源 `hasData()==true`（含合成源，:452）→ 真写一个 SigMF 文件（内容为合成 IQ，sidecar 标 hardware="Test Signal"） | **半诚实**：文件是真写的、元数据诚实标了测试信号；但 (a) 工具结果不披露 (b) 即使 recorder 失败也照样报「开始录制」 | agent_tools.cpp:63-65（忽略返回值）；spectrum_engine.cpp:447-491。建议：传播 bool，失败回 `{ok:false,error}` |
| stop_recording | 真停；未在录则无动作，仍回「停止录制」 | **基本诚实**（幂等） | agent_tools.cpp:67-70；spectrum_engine.cpp:493-520 |
| scan_band | 真做 readIQ 扫频循环并算 RMS。但测试信号偏音固定在基带 ±200k/±500k，**与被扫中心频率无关**→全扫程 RMS 近似恒定，`peakFreq` 落到第一个扫点。executeTool 包成 `{ok:true,hits:[{frequencyHz:peak,dbfs:peak}]}` | **半诚实**：dBFS 是真测合成样点，但「命中频率」在测试信号上无物理意义，却被当成真频段峰值回报 | agent_tools.cpp:71-90；spectrum_engine.cpp:179-198；test_signal.cpp:139-149。建议：`isTestSignalActive()` 时回诚实注记「合成信号扫描，非真实 RF 峰值」 |
| set_bandwidth | 真重建通道滤波；回「带宽设为 X Hz」 | **半诚实**：动作真，未披露无硬件 | agent_tools.cpp:91-95；spectrum_engine.cpp:420-427 |
| get_status | 读回 centerFreq/mode/bandwidth 拼串；**不含 connected/源名** | **不诚实（对模型）**：与 Flutter 同名工具不对齐，模型无法从这里知道没连硬件 | agent_tools.cpp:96-101。建议：补 `connected`/`source` 字段（对齐 Flutter） |

> C++ 源层本身是诚实的（`sourceChanged(name,connected=false)`、caps「RTL-SDR 未连接」、录制 sidecar 标 hardware 名）。**缺口集中在「工具结果串不把这个诚实状态透传给模型」**。

### 2.2 Flutter（未连接 = `ConnectionStatus.disconnected`，`_client==null`）

| 工具 | 未连接时行为 | 是否诚实 | 证据 file:line / 修复建议 |
|---|---|---|---|
| set_frequency | `radio.setFrequencyHz` 只更新本地 `_freqHz`，`_client?.` 空安全 no-op、**不抛异常** → execute 回 `{ok:true,frequency_hz}` | **不诚实（红线级）**：模型以为真调谐了接收机，实际无设备下发 | ai_tools.dart:72-76；radio_controller.dart:463-467。建议：execute 开头判 `radio.status!=connected` → `_err('设备未连接，未调谐')` |
| set_mode | 只更新 `_mode`，`connected` 时才重建解调；不抛异常 → `{ok:true,mode}` | **不诚实（同上）** | ai_tools.dart:105-106；radio_controller.dart:469-476 |
| set_gain | 更新 `_gainDb/_autoGain`，`_client?.` no-op → `{ok:true}` | **不诚实（同上）** | ai_tools.dart:136-146；radio_controller.dart:478-494 |
| set_sample_rate | 更新 `_sampleRateHz`，`_client?.` no-op → `{ok:true}` | **不诚实（同上）** | ai_tools.dart:179-183；radio_controller.dart:496-504 |
| get_status | 回报 `connected: radio.status==connected` + 读回值 | **诚实**：模型可据此发现未连接 | ai_tools.dart:193-207 |

> 关键不对称：移动端 4 个写工具**没有连接前置判断**，而底层 RadioController 方法是 null 安全静默成功的，try/catch 永不触发，于是 `ok:true` 一路回给模型。get_status 已诚实，但模型若只调动作工具不会先去查状态。

---

## 3. 卫星追踪工具决策

### 3.1 现状（两端均**未被 LLM 工具循环覆盖**）

**Flutter 端**——能力真实且诚实，但只在 SkyPage / 捕获服务里用，不在 `buildRadioTools`：
- TLE 来源：`services/tle_client.dart:64` 真拉 Celestrak `gp.php`，15s 超时，失败抛 `TleFetchException`、**无静默回退、无假数据**（:25 注释、:75-95）。
- 过境预测：`astro/passes.dart:67 predictPasses` 真 SGP4 步进采样 + 插值；`astro/sgp4.dart` 真传播。
- 捕获：`services/satellite_capture.dart:80 capturePass`——无下行目录命中即 `CaptureUnavailable('无下行频率数据')`（:87-90，绝不猜频率）；多普勒为「捕获时刻一次性预测」，诚实标注「未实时跟踪」（:9-11, :62-63）；真下发 `radio.setFrequencyHz`。
- 接线：`home_shell.dart:171` 只 `buildRadioTools(radio)`（5 个射频工具），**无任何 astro 工具进 AI 循环**。

**C++ 端**——已有完整 plan→run 链，但由 UI 触发，不在 LLM 循环：
- TLE：`dsp/tle_client.h` 真拉 celestrak + 磁盘缓存 + `builtinTle()`（:111-117，**明确标注 2006 陈旧、非新鲜数据替代**）；近地真 SGP4，深空诚实退 J2（:77-83，局限写在头文件）。
- 规划：`sat_task_planner.cpp:10 planSatelliteCapture`——坐标无效/无卫星/无 TLE/窗口内无过境/下行未知，全部 `ok=false + 诚实 reason`（:14-62，**绝不编频率/过境**）。
- 执行：`task_orchestrator.cpp:169` **复用 `LLMWorker::dispatchToolCall` 单执行点**（无第二条执行路径）；`task_runner.cpp:43` 把 manualMode 透传给 orchestrator。
- 触发：`main_window.cpp:4882` 由 UI 动作调用，**非 LLM 工具循环**。

### 3.2 建议：**接，但分两步——先接「只读预测」，再接「捕获动作」；不接「把陈旧 builtin TLE 当真」**

理由：
1. **数据源真实、且已诚实**——不需要造假数据即可接入（满足红线）。两端都已有 SGP4 + Celestrak + 下行目录 + 诚实空态。
2. **动作链已复用单执行点**——C++ 侧捕获本来就走 `dispatchToolCall→executeTool`，天然带 manual-mode gate；Flutter `capturePass` 也已真调 `radio.setFrequencyHz`。接入只是「在工具注册表里加一个工具」，不是新造执行路径。
3. **必须规避的坑（决定了「怎么接」）**：
   - C++ `planSatelliteCapture` 当前用的是 `builtinTle()`（`sat_task_planner.cpp:23`）——**陈旧离线 TLE**。若直接包成 LLM 工具 `track_satellite`，模型会拿到基于 2006 历元的预测并当真。→ 接入前必须改为读**新鲜缓存** `TleClient::cachedTle()`（`tle_client.h:148`），无新鲜 TLE 时回诚实空态（对齐 Flutter `TleFetchException`），**绝不把 builtin 当真实过境**。
   - 需要站点经纬度：未配置时回诚实错误（对齐 `sat_task_planner.cpp:14-17`）。
   - 捕获动作必须复用 manual-mode gate，且（移动端）先判 `radio.status==connected`——否则会重演 §2.2 的「断开报成功」。

**两端实现要点：**

| 端 | 建议新增工具 | 接什么 | 诚实空态 |
|---|---|---|---|
| Flutter | `pass_predict`（只读）、`tle_update`（只读/动作） | `tle_client.fetch` + `passes.predictPasses`；站点坐标已有 `SkyPage(manualStation)` | 无 TLE/网络失败→回 `{ok:false,error:'未获取到 TLE，无法预测'}`；绝不用内置假过境 |
| Flutter | `capture_satellite`（动作，gate 后） | 复用 `capturePass`（已有 `CaptureUnavailable`） | 下行目录无命中→`CaptureUnavailable`；断开→对齐 §2.2 连接前置判断 |
| C++ | `predict_passes`（只读） | `TleClient::computePasses`（用 `cachedTle()`，**非 builtin**） | 无新鲜缓存/窗口无过境→诚实空 |
| C++ | （可选）`capture_satellite`（动作） | `planSatelliteCapture`→`TaskRunner.runPlan`（已带 gate） | 下行未知/无过境→现有 `ok=false reason` |

**确定性测试建议（mock TLE 数据源，不得冒充生产真实）：**
- Flutter：注入 fake `HttpClient` 返回固定已知 TLE（如 NOAA-19 公开两行轨），断言 `predictPasses` 产出的 AOS/LOS/maxEl 在容差内；TLE 列表为空时断言返回空且不编造。
- C++：用 `TleClient::setBaseUrl`（`tle_client.h:145`）指向 loopback `QTcpServer`（测试惯例，见 `tests/test_tle_fetch.cpp`）回固定 TLE，断言 `computePasses` 结果；`cachedTle().valid==false` 时断言预测工具回诚实空态。
- 两端各加：传入「超出窗口的 TLE」断言无过境时不返回伪命中。

---

## 4. 闭环差距清单（P0/P1/P2）

### P0（触碰红线「无硬件假装成功」）
1. **移动端 4 个动作工具在断开时回 `ok:true`。**
   - 现状：`ai_tools.dart:72-76 / :105-106 / :136-146 / :179-183` 直接报成功；底层 `radio_controller.dart:463-467,469-476,478-494,496-504` 在 `_client==null` 时空安全 no-op、不抛异常。
   - 建议：在每个写工具 execute 开头判 `radio.status != ConnectionStatus.connected`（或 disconnect/reconnecting）→ 返回 `_err('设备未连接，未执行 X')`；连接恢复后再允许下发。
   - 建议测试：fake `RadioApi`（`status=disconnected`，方法记录调用次数）→ 断言 4 工具均回 `{ok:false}` 且底层方法 0 次被调；`status=connected` 时才真调。

### P1（工具结果不向模型披露「无硬件/测试信号」）
2. **C++ executeTool 结果串不带源诚实状态。**
   - 现状：`agent_tools.cpp:53-101` 全部结果无 connected/源名；引擎其实有 `engine->isTestSignalActive()`（`spectrum_engine.cpp:403`）与 `sourceCapabilities()`（:123）可用却未用。
   - 建议：工具结果统一追加 `source`（= `sourceCapabilities().deviceName`）与 `connected` 字段；get_status 对齐 Flutter（见 P2-5）。
   - 建议测试：engine 构造即 TestSignalSource → 断言 tune/get_status 结果含 `connected:false` / 源名「Test Signal」。
3. **C++ scan_band 在测试信号上回报「假命中频率」。**
   - 现状：`agent_tools.cpp:76-89` 包 `hits[0].frequencyHz`；测试信号偏音与扫频中心无关（`test_signal.cpp:139-149`），peakFreq 实为首个扫点。
   - 建议：`isTestSignalActive()` 时把结果标注为「合成信号扫描，非真实 RF 峰值」，或在 `hits` 里加 `synthetic:true`。
   - 建议测试：测试源上扫一段宽频，断言结果带 synthetic 标注，peakFreq 不被当作真实电台。
4. **C++ start_recording 忽略 `engine->startRecording()` 返回值。**
   - 现状：`agent_tools.cpp:63-65` 无视 bool 直接回「开始录制」；`spectrum_engine.cpp:455` 失败时返回 false。
   - 建议：传播 bool → 失败回 `{ok:false,error:'录制启动失败'}`。
   - 建议测试：force `recorder_.startWithBase` 失败（如目录不可写）→ 断言工具回 `ok:false`。

### P2（一致性 / 健壮性 / 卫星接入预留）
5. **C++ get_status 与 Flutter get_status 不对齐。**
   - 现状：C++ `agent_tools.cpp:96-101` 只拼频率/模式/带宽；Flutter `ai_tools.dart:193-207` 已含 `connected`。
   - 建议：C++ get_status 补 `connected`/`source`/`hardware`，与移动端对齐，便于模型跨端一致判断。
   - 建议测试：无硬件时 C++ get_status 输出含 `connected:false`。
6. **卫星追踪未接 LLM 工具循环（按 §3 决策分两步接）。**
   - 现状：两端能力真实但仅 UI 触发；C++ planner 用陈旧 `builtinTle()`（`sat_task_planner.cpp:23`）。
   - 建议：先接只读 `predict_passes`（接新鲜缓存），再接 gate 后的动作工具；禁用 builtin 当真实预测。
   - 建议测试：见 §3.2 mock TLE 方案。
7. **（提示，非缺陷）gate 清单两端天然不同**——C++ `writeTools`（`agent_tools.cpp:17-25`）含 scan_band/录制/带宽，Flutter `mutatingTools`（`ai_tools.dart:215-220`）含 gain/sample_rate；因两端工具集本就不同，原则一致（写 gate、读直通），无需统一清单，仅建议在文档注明。

---

## 5. 手动模式 gate 两端一致性

- C++：写工具集合 `agent_tools.cpp:16-26`；gate 在 `llm_worker.cpp:21`；任务链 `task_orchestrator.cpp:169` **复用同一 dispatchToolCall**，`task_runner.cpp:43` / `agent.cpp:45` 透传 manualMode。→ **单点 gate，LLM 循环与任务编排一致**。
- Flutter：写工具集合 `ai_tools.dart:215-220`；gate 在构造时烘焙进闭包（:221-229），不调 radio；manualMode 实时读 `settings.aiManualMode`（`home_shell.dart:173`，每轮新建 client 即时生效）。→ **gate 生效一致**。
- 结论：两端 gate 逻辑一致（写动作拦、读放行、gated JSON 回灌对话），**未发现「手动模式下动作仍触硬件」的漏洞**。差异仅在工具集合不同（见 §4-7）。

---

## 6. 自检记录

- 通读范围：`_PHASE3_SPEC.md`（A4/§3 红线）；C++ `llm_worker.cpp`/`agent_tools.cpp`/`spectrum_engine.{h,cpp}`/`test_signal.cpp`/`rtl_sdr_source.cpp`/`device_capabilities.cpp`/`sat_task_planner.{h,cpp}`/`task_orchestrator.h`/`task_runner.h`/`tle_client.h`/`tool_schema.cpp`；Flutter `ai_client.dart`/`ai_tools.dart`/`radio_controller.dart`/`tle_client.dart`/`passes.dart`/`satellite_capture.dart`/`home_shell.dart`。
- 只读：未改任何源码、未动 git。本报告落盘于本文件。
- 证据均带 file:line；推断处：①「Flutter 断开时 RadioController 不抛异常」系通读 462-504 空安全写法确认，非运行实测（标「推断」）；②C++ 测试信号扫频 RMS「近似恒定」系读 `test_signal.cpp:139-149` 固定偏音得出的推断，未实测数值。
- 未实测项：未真机/真 rtl_tcp 连接，故「连接后行为」以代码路径为准；mock/合成信号未冒充真实执行结论。
- 红线自查：本报告未把 TestSignalSource / builtinTle / mock TLE 当作真实能力；已明确区分「真执行在合成源上」与「真执行在硬件上」。
