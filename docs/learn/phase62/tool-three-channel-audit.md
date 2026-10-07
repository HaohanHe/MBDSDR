# 工具面三通道一致性核查（Phase62）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `e85a88a`
- 审计方式：只读源码解析（脚本提取三张表逐行比对）+ offscreen 真实编译/运行既有金集测试；除本报告列出的两处外**未改动任何 src/**
- 三通道定义：
  1. **Agent spec**：`cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`（JSON Schema + `write` 声明，`tool_schema.h:42` 默认 `write=false`）
  2. **dispatch**：`cpp/src/ai/agent_tools.cpp::dispatchTable()`（name → 执行函数）
  3. **ControlHub**：`cpp/src/control/control_hub.cpp::table()`（命令名 + write 标志 + handler）；HTTP `POST /command` 直接委托 `ControlHub::execute()`（`control_http_server.cpp:306,331`）

## 0. 结论摘要

| 通道 | 规模 | 写 / 读 |
|---|---|---|
| Agent spec | 45 | 28 / 17 |
| dispatch | 45 | — |
| ControlHub 命令 | 64 | 42 / 22 |

- **spec ↔ dispatch：集合完全相等（45=45），无缺行、无孤儿执行器**；顺序差异（`export_iq_segment` 在 spec 第 16 位、dispatch 第 5 位）仅影响内部查表顺序，on-wire 列表取自 spec，金集钉住首尾名（`test_tool_registry.cpp:137,155`），判定**良性不修**。
- **45 个 Agent 工具中 38 个与 ControlHub 同名同标志**；7 个存在命名/覆盖差异（见 §2），**无一处 read/write 标志错误**，无 dispatch 缺行。
- **gate 抽查真实结果**：5 写工具手动模式全部被拦（`gated:true,ok:false`，引擎/QSettings 零改动），5 只读工具全部放行执行（§3）。
- 文档同步：`docs/learn/phase31/agent-tool-documentation.md` 由 35 同步为 45（§4）。

## 1. 45 工具逐项一致性表

列说明：Agent W/R = spec `write` 标志；CH 列 = ControlHub 同名命令（`-` = 无同名行）；gate=关 时 = HTTP 通道在写门关闭（manual 等价）下的可达性。

| # | Agent 工具 | Agent | dispatch | ControlHub 同名 | CH 标志 | gate=关时 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | tune_frequency | W | ✓ | `tune`（control_hub.cpp:84） | W | HTTP 走 `tune`，被拦 | 命名漂移，参数同为 freq_hz，**架构性不修** |
| 2 | set_mode | W | ✓ | set_mode | W | 被拦 | 一致 |
| 3 | start_recording | W | ✓ | start_recording | W | 被拦 | 一致 |
| 4 | stop_recording | W | ✓ | stop_recording | W | 被拦 | 一致 |
| 5 | scan_band | W | ✓ | scan_band | W | 被拦 | 一致 |
| 6 | set_bandwidth | W | ✓ | set_bandwidth | W | 被拦 | 一致 |
| 7 | get_status | R | ✓ | get_status | R | 放行 | 一致 |
| 8 | predict_passes | R | ✓ | predict_passes | R | 放行 | 一致 |
| 9 | calibrate_frequency | R | ✓ | - | - | **HTTP 无通道** | Agent 独有（桌面 AI 测量），**架构性不修** |
| 10 | apply_frequency_correction | W | ✓ | - | - | **HTTP 无通道** | Agent 独有（写 QSettings rtl/ppm），**架构性不修** |
| 11 | get_pocsag_messages | R | ✓ | get_pocsag_messages | R | 放行；参数名 `channel_id`(Agent) ↔ `channel`(CH:325) | 同名同标志；参数名漂移，**架构性不修**（两侧均被金集冻结） |
| 12 | get_m17_calls | R | ✓ | get_m17_calls | R | 放行；同上 channel_id ↔ channel | 同上 |
| 13 | get_vor_radial | R | ✓ | get_vor_radial | R | 放行；同上 channel_id ↔ channel | 同上 |
| 14 | get_acars_packets | R | ✓ | get_acars_packets | R | 放行；同上 channel_id ↔ channel | 同上 |
| 15 | get_navtex_messages | R | ✓ | get_navtex_messages | R | 放行；同上 channel_id ↔ channel | 同上 |
| 16 | export_iq_segment | W | ✓ | export_iq_segment | W | 被拦 | 一致 |
| 17 | set_network_audio_sink | W | ✓ | set_network_audio_sink | W | 被拦 | 一致 |
| 18 | get_network_audio_status | R | ✓ | get_network_audio_status | R | 放行 | 一致 |
| 19 | start_scan_link | W | ✓ | start_scan_link | W | 被拦 | 一致 |
| 20 | stop_scan_link | W | ✓ | stop_scan_link | W | 被拦 | 一致 |
| 21 | get_scan_link_status | R | ✓ | get_scan_link_status | R | 放行 | 一致 |
| 22 | set_squelch | W | ✓ | set_squelch | W | 被拦 | 一致 |
| 23 | get_squelch_status | R | ✓ | get_squelch_status | R | 放行 | 一致 |
| 24 | list_bookmarks | R | ✓ | list_bookmarks | R | 放行 | 一致 |
| 25 | add_bookmark | W | ✓ | add_bookmark | W | 被拦 | 一致 |
| 26 | tune_to_bookmark | W | ✓ | tune_to_bookmark | W | 被拦 | 一致 |
| 27 | delete_bookmark | W | ✓ | delete_bookmark | W | 被拦 | 一致 |
| 28 | list_vfos | R | ✓ | list_vfos | R | 放行 | 一致 |
| 29 | add_vfo | W | ✓ | add_vfo | W | 被拦 | 一致 |
| 30 | switch_vfo | W | ✓ | switch_vfo（兼容 index/id） | W | 被拦 | 一致 |
| 31 | rename_vfo | W | ✓ | rename_vfo（兼容 index/id） | W | 被拦 | 一致 |
| 32 | set_vfo_armed | W | ✓ | set_vfo_armed | W | 被拦 | 一致（CH 注释对齐 marker-index 语义，control_hub.cpp:741） |
| 33 | set_vfo_frequency | W | ✓ | `vfo_set_freq`（control_hub.cpp:104） | W | HTTP 走 vfo_set_freq，被拦 | 命名+参数漂移（Agent `index` 序号 ↔ CH `id`），**架构性不修** |
| 34 | set_vfo_mode | W | ✓ | `vfo_set_mode`（control_hub.cpp:107） | W | HTTP 走 vfo_set_mode，被拦 | 同上 |
| 35 | set_vfo_bandwidth | W | ✓ | `vfo_set_bandwidth`（control_hub.cpp:106） | W | HTTP 走 vfo_set_bandwidth，被拦 | 同上 |
| 36 | list_recordings | R | ✓ | list_recordings | R | 放行 | 一致 |
| 37 | delete_recording | W | ✓ | delete_recording | W | 被拦 | 一致 |
| 38 | export_recording | W | ✓ | export_recording | W | 被拦 | 一致 |
| 39 | set_fft_params | W | ✓ | set_fft_params | W | 被拦 | 一致 |
| 40 | set_color_map | W | ✓ | set_color_map | W | 被拦 | 一致 |
| 41 | get_spectrum_status | R | ✓ | get_spectrum_status | R | 放行 | 一致 |
| 42 | set_doppler_compensation | W | ✓ | set_doppler_compensation | W | 被拦 | 一致 |
| 43 | connect_network_source | W | ✓ | connect_network_source | W | 被拦 | 一致 |
| 44 | get_capabilities | R | ✓ | get_capabilities | R | 放行 | 一致 |
| 45 | get_recording_state | R | ✓ | get_recording_state | R | 放行 | 一致 |

## 2. 差异清单与判定

### 2.1 判定"架构性不修"的差异（均不构成 src 改动）

| 差异 | 事实 | 不修理由 |
|---|---|---|
| `tune_frequency`(Agent) ↔ `tune`(CH) | 参数同为 `freq_hz`，写标志同为 W（tool_schema.cpp:69；control_hub.cpp:84） | Wave1 历史命名；两侧金集各自冻结（`test_tool_registry.cpp:34` 钉 `tune_frequency`；`test_control_hub.cpp:311` 钉 `tune`）。改名会同时破坏 LLM on-wire 契约与 HTTP API，超出审计范围 |
| `set_vfo_frequency/mode/bandwidth`(Agent) ↔ `vfo_set_freq/mode/bandwidth`(CH) | 能力等价、标志同为 W；参数语义漂移：Agent 用 **marker 序号 index**（executor 内部解析为 VFO id，agent_tools.cpp:830-836），CH 用原始 **VFO id**（control_hub.cpp:692-695） | Phase61 新增 Agent 工具与 Phase26 前既有 CH 底层命令并行存在；两套参数语义各自有意设计、各自金集冻结。若未来要在 HTTP 暴露 marker-index 语义，应作为独立增量（加别名行）而非本轮审计小修 |
| `calibrate_frequency` / `apply_frequency_correction` 无 HTTP 通道 | CH 表无同名命令 | 代码注释明确该对为桌面 AI 专用（测量需参考信号 PTT 场景，headless HTTP 暴露非设计意图；tool_schema.cpp:211-281）。`kFlutterUngatedReadTools` 亦标注 calibrate 为 desktop-only（test_tool_registry.cpp:93） |
| 参数名 `channel_id`(Agent, tool_schema.cpp:300/318/336/354/372) ↔ `channel`(CH, control_hub.cpp:325) | 5 个解码快照工具；LLM 通道与 HTTP 通道各自独立消费 | 两侧均被金集钉住（CH 侧 test_control_hub.cpp:359 用 `channel`）。同一 JSON 参数在两个入口不同名是真实漂移，但改任一侧都破坏冻结契约——记录在案，留待后续契约统一决策 |

### 2.2 反向：ControlHub 有而 Agent 未暴露（26 个命令）

`tune`、`set_sample_rate`、`set_gain`、`set_squelch_enabled`、`set_squelch_threshold`、`set_muted`、`set_anr`、`set_gated_recording`、`set_watch`、`set_tuner_agc`、`set_rtl_agc`、`vfo_add`、`vfo_remove`、`vfo_select`、`vfo_set_freq`、`vfo_set_offset`、`vfo_set_bandwidth`、`vfo_set_mode`、`clear_digital_outputs`、`get_frequency`、`get_mode`、`get_bandwidth`、`get_telemetry`、`list_gains`、`get_vfos`。

判定：**架构性不修**。这些是 headless/HTTP 底层控制原语（底层 AGC/增益/静音/遥测等），有意不暴露给 LLM（避免 AI 直接操纵底层射频原语；Phase26 注释仅承诺"21 个冻结工具"同名对等，tool_schema.cpp:412-418）。标志分类本身全部通过 `test_control_hub.cpp::commandTableIsClassified` 校验。

### 2.3 未发现的类别（真实缺陷为零）

- **read/write 标志错**：无。38 个同名工具标志逐一比对全部一致；28 个写工具集合与 `kExpectedWriteTools` 严格相等（`test_tool_registry.cpp:160-179` 真实运行 8/8 通过）。
- **dispatch 缺行**：无。spec 集合 == dispatch 集合。
- 说明：`calibrate_frequency` 虽会临时把源调到参考频点做测量，但其 `write=false` 是**有意分类**（测量不得被手动门拦截、不持久化；tool_schema.cpp:211-218，且有 `manualMode_allowsCalibrateRead` 测试钉住）。

## 3. gate 抽查真实结果（offscreen 实测）

门实现：Agent 侧 `llm_worker.cpp:118` `if (manualMode && isWriteTool(name)) return gatedToolResult(name);`（先于任何引擎接触）；ControlHub 侧 `control_hub.cpp:358` `if (row->write && !writeEnabled_)`。

### 3.1 既有覆盖（核对，未重复加）

| 抽查项 | 既有测试 |
|---|---|
| tune_frequency / set_mode 写被拦且引擎不动 | `test_agent.cpp::testManualModeGatesWriteTool` |
| apply_frequency_correction 写被拦且 rtl/ppm 设置不动 | `test_agent.cpp::manualMode_gatesApplyCorrection` |
| set_vfo_bandwidth 写被拦且带宽不动 | `test_agent.cpp::vfoEditToolsBadArgsAndGate` |
| get_status 手动模式放行 | `test_agent.cpp::testManualModeAllowsReadTool` |
| calibrate_frequency 手动模式放行 | `test_agent.cpp::manualMode_allowsCalibrateRead` |
| 全部 28 写/17 读标志集合相等 | `test_tool_registry.cpp::writeReadSplit_registryMatchesContract` / `readOnlySet_parityWithFlutter` |

### 3.2 本次新增 1 个聚焦测试槽

`cpp/tests/test_agent.cpp::manualMode_gateSpotCheckTenTools`（声明 :56，实现 :599），覆盖任务指定的其余抽查项：

- **5 个写工具（手动模式）**：`set_vfo_frequency`、`start_recording`、`apply_frequency_correction`、`set_squelch`、`delete_recording` —— 逐一断言返回含 `"gated":true` 与 `"ok":false`，并快照比对 VFO 频率/静噪使能与门限/录制路径/QSettings rtl/ppm 全部未变。
- **5 个只读工具（手动模式）**：`get_capabilities`、`get_recording_state`、`get_status`、`get_vor_radial`、`list_vfos` —— 逐一断言不含 `"gated"` 且 `"ok":true`（真实执行）。

实测（Qt 6.8.2，offscreen）：

```
PASS : TestAgent::manualMode_gateSpotCheckTenTools()
test_agent 全量: 27 passed, 0 failed（原 26 + 新增 1）
test_tool_registry: 8 passed | test_tool_schema: 10 passed | test_control_hub: 26 passed
```

## 4. 文档同步

- 定位：`docs/learn/phase31/agent-tool-documentation.md`（文件自述"自动生成"，由 `generateToolDocumentation()` 产出；金集测试只校验内存输出，不校验该存档文件，故存档腐化无人发现）。
- 实况：旧档头部写"35 个工具"，缺 10 个后续新增工具块（get_acars_packets、get_navtex_messages、set_vfo_armed、set_vfo_frequency/mode/bandwidth、set_doppler_compensation、connect_network_source、get_capabilities、get_recording_state）。
- 处理：用同一生成函数（链接 `libmbdsdr_core.a` 的一次性小程序）重生成并覆盖该文件，头部同步为"45 个工具"，工具块由 35 → 45。格式与旧档逐行一致（标题/固定字段行相同）。
- 其余 docs/ 下未发现第二处工具计数清单（`docs/learn/phase3/audits/A2-fake-tools.md` 的"45 个工具"指 Python 原型时代的假闭环工具，非本注册表，不动）。

## 5. 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `cpp/tests/test_agent.cpp` | +61 行 | 新增聚焦测试槽 `manualMode_gateSpotCheckTenTools`（仅测试，无行为变更） |
| `docs/learn/phase31/agent-tool-documentation.md` | +52/-2 行 | 由 `generateToolDocumentation()` 同源重生成：35 → 45 |
| `cpp/src/ai/*`、`cpp/src/control/*` | **无改动** | 审计结论：无真实不一致需要修 |

附注：工作树中 `cpp/src/ui/main_window.cpp` 的未提交改动**不是本次审计产生的**（并行 UI 任务会话所致），本审计未触碰、未回滚。未执行任何 git add/commit/push。

## 6. 关键引用索引

- Agent spec 表：`cpp/src/ai/tool_schema.cpp:63`（write 默认 false：`tool_schema.h:42`）
- dispatch 表：`cpp/src/ai/agent_tools.cpp:1146`；门结果构造：`:23`
- Agent 手动门：`cpp/src/ai/llm_worker.cpp:118`
- ControlHub 表：`cpp/src/control/control_hub.cpp:81`；写门：`:358`；HTTP 委托：`cpp/src/control/control_http_server.cpp:306,331`
- 差异点：`tune` :84 / `vfo_set_freq` :104 / `vfo_set_bandwidth` :106 / `vfo_set_mode` :107 / `channel` 参数 :325；Agent 侧 `channel_id` tool_schema.cpp:300,318,336,354,372
- 金集：`cpp/tests/test_tool_registry.cpp:137,155,160`；`cpp/tests/test_control_hub.cpp:303`；新增槽 `cpp/tests/test_agent.cpp:56,599`
