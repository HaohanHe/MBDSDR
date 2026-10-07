# 工具 Schema 逐字段核对表（Phase62 增补）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `5eccddc`
- 审计方式：只读源码逐字段解析 + offscreen 真实编译运行金集；除 `cpp/tests/test_agent.cpp` 新增一个测试槽外**未改动任何 src/**
- 范围：在 `tool-three-channel-audit.md`（名/标志三通道对照）基础上，向下钻一层——**每个工具的参数名 / 类型 / 必填 / 默认值 / 边界 / 枚举，Agent spec ↔ Agent 执行器(dispatch) ↔ ControlHub 命令 ↔ HTTP 可达性 / 错误语义**逐字段核对。
- 三通道定义同前：
  1. **Agent spec**：`cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`
  2. **Agent dispatch**：`cpp/src/ai/agent_tools.cpp::dispatchTable()` + 各 `exec*` 读参键
  3. **ControlHub / HTTP**：`cpp/src/control/control_hub.cpp::table()` 命令表 + 各 `cmd*` 读参键；HTTP `POST /command` 委托 `ControlHub::execute()`

## 0. 结论摘要

| 通道 | 规模 | 写 / 读 |
|---|---|---|
| Agent spec | 45 | 28 / 17 |
| Agent dispatch | 45 | — |
| ControlHub 命令 | 64 | 42 / 22 |

- **Agent spec ↔ Agent dispatch：45=45，参数名 1:1 对齐**（`test_tool_registry` 真实钉住集合相等；本表逐字段复核：每个 spec 参数名都被对应 `exec*` 以同键读取，无"声明了却没人读"、无"读了却没声明"的字段）。
- **Agent ↔ ControlHub 字段级漂移**仅 4 处（§2），全部判定**架构性不修**（两侧金集各自冻结）；**无 read/write 标志错、无 dispatch 缺行、无参数名内部不一致**。
- **gate 全覆盖实测**（§3）：新增 `manualMode_gateSpotCheckAllWrites`，遍历 spec 表本身（非硬编码名单），**28 个写工具手动模式全部 `gated:true,ok:false` 且引擎/QSettings 零改动；17 个只读工具全部放行**。test_agent 由 27 → **28**。

## 1. 45 工具逐字段对照表

列说明：
- **Agent 参数** = spec 声明（`必填*`；默认值取描述文案或执行器兜底）。
- **exec 读键** = `exec*` 实际 `args[...]` 键（与 spec 同键记 `=同`）。
- **CH 命令 / CH 读键** = ControlHub 同名（或映射）命令及其读参键；`-` = 无同名命令。
- **HTTP** = gate=关（manual 等价）时 HTTP 通道可达性。

| # | Agent 工具 (W/R) | Agent 参数（必填* / 默认） | exec 读键 | CH 命令 (W/R) | CH 读键 | HTTP | 字段判定 |
|---|---|---|---|---|---|---|---|
| 1 | tune_frequency (W) | freq_hz* number [kFreqMin,Max] | =同 | tune (W) | freq_hz | 被拦 | 参数名同 `freq_hz`；**命令名漂移** tune_frequency↔tune，架构性不修 |
| 2 | set_mode (W) | mode* enum{AM,NFM,WFM,USB,LSB,CW} | =同 | set_mode (W) | mode | 被拦 | 一致（见 §2.3 枚举收紧观察） |
| 3 | start_recording (W) | （无参） | — | start_recording (W) | — | 被拦 | 一致 |
| 4 | stop_recording (W) | （无参） | — | stop_recording (W) | — | 被拦 | 一致 |
| 5 | scan_band (W) | low_hz* number / high_hz* number / step_hz 可选=200000, min1 | =同 | scan_band (W) | low_hz/high_hz/step_hz | 被拦 | 一致（默认值、键全同） |
| 6 | set_bandwidth (W) | bandwidth_hz* number, enum{7 档预设} | =同 | set_bandwidth (W) | bandwidth_hz | 被拦 | 键同；枚举为 LLM 顾问性（引擎任意正数），见 §2.3 |
| 7 | get_status (R) | （无参） | — | get_status (R) | — | 放行 | 一致 |
| 8 | predict_passes (R) | satellite_name* string / hours_ahead 可选=24[1,168] / station_lat_deg 可选 / station_lon_deg 可选 | =同 | predict_passes (R) | satellite_name/hours_ahead/station_lat_deg/station_lon_deg | 放行 | 一致（键、默认 24 全同） |
| 9 | calibrate_frequency (R) | reference_freq_hz* number / reference_type* enum{handheld,gsm_fcch,manual} / sample_count 可选=32768,min4096 | =同 | - | - | **无通道** | Agent 独有（桌面 PTT 测量），架构性不修 |
| 10 | apply_frequency_correction (W) | ppm* number [kPpmMin,Max] / reference_freq_hz 可选 | ppm=同（reference_freq_hz 不读，仅出处） | - | - | **无通道** | Agent 独有（写 QSettings rtl/ppm），架构性不修 |
| 11 | get_pocsag_messages (R) | channel_id 可选（缺省=选中 VFO） | channel_id | get_pocsag_messages (R) | **channel** | 放行 | 参数名漂移 channel_id↔channel，架构性不修 |
| 12 | get_m17_calls (R) | channel_id 可选 | channel_id | get_m17_calls (R) | **channel** | 放行 | 同上 |
| 13 | get_vor_radial (R) | channel_id 可选 | channel_id | get_vor_radial (R) | **channel** | 放行 | 同上 |
| 14 | get_acars_packets (R) | channel_id 可选 | channel_id | get_acars_packets (R) | **channel** | 放行 | 同上 |
| 15 | get_navtex_messages (R) | channel_id 可选 | channel_id | get_navtex_messages (R) | **channel** | 放行 | 同上 |
| 16 | export_iq_segment (W) | sample_count 可选=65536,min1024 / tune_hz 可选（缺省=保持当前） | =同 | export_iq_segment (W) | sample_count/tune_hz | 被拦 | 一致（键、默认、tune_hz 语义全同） |
| 17 | set_network_audio_sink (W) | enable* boolean / port* number / format 可选 string | =同 | set_network_audio_sink (W) | enable/port/format | 被拦 | 一致 |
| 18 | get_network_audio_status (R) | （无参） | — | get_network_audio_status (R) | — | 放行 | 一致 |
| 19 | start_scan_link (W) | target_freq_hz* number [kFreqMin,Max] | =同 | start_scan_link (W) | target_freq_hz | 被拦 | 一致 |
| 20 | stop_scan_link (W) | （无参） | — | stop_scan_link (W) | — | 被拦 | 一致 |
| 21 | get_scan_link_status (R) | （无参） | — | get_scan_link_status (R) | — | 放行 | 一致 |
| 22 | set_squelch (W) | enabled 可选 bool / threshold_db 可选 number / auto 可选 bool | =同 | set_squelch (W) | enabled/threshold_db/auto | 被拦 | 一致 |
| 23 | get_squelch_status (R) | （无参） | — | get_squelch_status (R) | — | 放行 | 一致 |
| 24 | list_bookmarks (R) | （无参） | — | list_bookmarks (R) | — | 放行 | 一致 |
| 25 | add_bookmark (W) | freq_hz* number [kFreqMin,Max] / name 可选 string / mode 可选 string | =同 | add_bookmark (W) | freq_hz/name/mode | 被拦 | 一致 |
| 26 | tune_to_bookmark (W) | index* number | =同 | tune_to_bookmark (W) | index | 被拦 | 一致 |
| 27 | delete_bookmark (W) | index* number | =同 | delete_bookmark (W) | index | 被拦 | 一致 |
| 28 | list_vfos (R) | （无参） | — | list_vfos (R) | — | 放行 | 一致 |
| 29 | add_vfo (W) | （无参） | — | add_vfo (W) | — | 被拦 | 一致 |
| 30 | switch_vfo (W) | index* number | =同（解析为 VFO id） | switch_vfo (W) | **index 或缺省 id**（兼容） | 被拦 | CH 兼容 index/id；键对齐 |
| 31 | rename_vfo (W) | index* number / name* string | =同 | rename_vfo (W) | **index 或缺省 id** / name | 被拦 | CH 兼容 index/id；键对齐 |
| 32 | set_vfo_armed (W) | index* number / enabled* bool | =同（marker index→id） | set_vfo_armed (W) | index（marker index→id） | 被拦 | 一致（两侧同 marker-index 语义，control_hub.cpp:741） |
| 33 | set_vfo_frequency (W) | index* number / freq_hz* number | index=marker序号, freq_hz | vfo_set_freq (W) | **id**（原始 VFO id） / freq_hz | 被拦 | **参数语义漂移**：Agent marker 序号 ↔ CH 原始 id；架构性不修 |
| 34 | set_vfo_mode (W) | index* number / mode* string（描述列 11 种） | index=marker序号；mode 校验 kControlHubModes | vfo_set_mode (W) | **id** / mode | 被拦 | 同上（id 语义漂移）；mode 校验两侧同表 |
| 35 | set_vfo_bandwidth (W) | index* number / bandwidth_hz* number | index=marker序号, bandwidth_hz | vfo_set_bandwidth (W) | **id** / bandwidth_hz | 被拦 | 同上（id 语义漂移） |
| 36 | list_recordings (R) | （无参） | — | list_recordings (R) | — | 放行 | 一致 |
| 37 | delete_recording (W) | name* string（纯文件名，禁路径） | =同 | delete_recording (W) | name | 被拦 | 一致（两侧同安全约束） |
| 38 | export_recording (W) | name* string / out_path* string | =同 | export_recording (W) | name/out_path | 被拦 | 一致 |
| 39 | set_fft_params (W) | fft_size* number[256,65536] / window 可选 enum{Hann,Flattop,Blackman} / average 可选 enum{Off,Slow,Fast} | =同 | set_fft_params (W) | fft_size/window/average | 被拦 | 一致 |
| 40 | set_color_map (W) | file_path* string | =同 | set_color_map (W) | file_path | 被拦 | 一致 |
| 41 | get_spectrum_status (R) | （无参） | — | get_spectrum_status (R) | — | 放行 | 一致 |
| 42 | set_doppler_compensation (W) | enable* bool | =同 | set_doppler_compensation (W) | enable | 被拦 | 一致（无 UI 面时两侧同报 available=false） |
| 43 | connect_network_source (W) | host* string / port 可选 number=1234 | =同 | connect_network_source (W) | host/port=1234 | 被拦 | 一致（键、默认 1234 全同） |
| 44 | get_capabilities (R) | （无参） | — | get_capabilities (R) | — | 放行 | 一致 |
| 45 | get_recording_state (R) | （无参） | — | get_recording_state (R) | — | 放行 | 一致 |

## 2. 不一致清单与判定

### 2.1 判定"架构性不修"（真实漂移，但改任一侧都破坏冻结契约）

| 漂移 | 事实 | 不修理由 |
|---|---|---|
| 命令名 `tune_frequency`(Agent) ↔ `tune`(CH) | 参数同为 `freq_hz`，写标志同为 W | 历史命名；两侧金集各自冻结（test_tool_registry 钉 `tune_frequency`；test_control_hub 钉 `tune`）。改名同时破坏 LLM on-wire 与 HTTP API |
| 参数语义 `index` marker 序号(Agent) ↔ `id` 原始 VFO id(CH) | set_vfo_frequency/mode/bandwidth 三对；Agent 执行器把 marker index 解析为 id（agent_tools.cpp:830-836），CH 直接要 id（control_hub.cpp:692-695） | Phase61 新增 Agent 工具与 Phase26 前既有 CH 底层命令并行；两套语义各自有意设计、各自金集冻结。未来若要 HTTP 暴露 marker-index，应作独立增量别名行 |
| 参数名 `channel_id`(Agent) ↔ `channel`(CH) | 5 个解码快照工具（tool_schema.cpp:300/318/336/354/372 ↔ control_hub.cpp:325） | LLM 通道与 HTTP 通道各自独立消费，两侧金集钉住。同一参数两个入口不同名是真实漂移，记录在案留待契约统一 |
| `calibrate_frequency`/`apply_frequency_correction` 无 HTTP 通道 | CH 表无同名命令 | 桌面 AI 专用（测量需 PTT 参考信号），headless HTTP 暴露非设计意图（tool_schema.cpp:211-281） |

### 2.2 架构性观察（非缺陷，不改动）

- **set_mode 的 schema enum(6) vs set_vfo_mode 的实际允许集(11)**：`set_mode` spec 声明 enum{AM,NFM,WFM,USB,LSB,CW}，执行器 `engine->setDemodMode(m)` 不做枚举校验；`set_vfo_mode` spec 不声明 enum（描述列 11 种），执行器用 `tokens::kControlHubModes` 严格校验。两者对 LLM 的枚举承诺宽窄不同，但这是**顾问性 schema**（引擎对未知模式容错），不构成运行时错误；判定**架构性不修**（若收紧 set_mode 枚举会改变 on-wire schema 金集）。
- **set_bandwidth 的 enum(7 预设) vs 执行器任意正数**：schema 枚举为 LLM 引导，执行器接受任意带宽。顾问性，非缺陷。
- **错误文案跨通道不同**：Agent gated = `{"ok":false,"gated":true,"error":"手动模式：未执行 X"}`（agent_tools.cpp:23）；ControlHub gated = `{"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 X"}`（control_hub.cpp:285）。触发源不同（manualMode vs writeEnabled_），文案各自诚实，架构性不修。

### 2.3 未发现的类别（真实缺陷为零）

- **spec 声明了参数但执行器不读**：无（唯一例外 apply_frequency_correction 的可选 `reference_freq_hz`，spec 明注"仅用于出处"，设计如此）。
- **执行器读了参数但 spec 未声明**：无。
- **spec↔dispatch 参数名不一致**：无（45 工具逐字段复核）。
- **read/write 标志错**：无（28 写集合 == `kExpectedWriteTools`，test_tool_registry 8/8）。

## 3. gate 全覆盖实测（offscreen，本次新增）

门实现：`llm_worker.cpp:118` `if (manualMode && isWriteTool(name)) return gatedToolResult(name);` —— **先于任何引擎接触、先于参数校验**。`isWriteTool` 直接读 spec 表 `write` 标志，无第二份写集合。

新增测试槽 `cpp/tests/test_agent.cpp::manualMode_gateSpotCheckAllWrites`：

- **遍历 spec 表本身**（`registeredToolSpecs()`），不硬编码名单——新增写工具自动纳入。
- 对每个**写工具**：构造合法参数调用 `LLMWorker::dispatchToolCall(..., manualMode=true)`，断言 `"gated":true`、`"ok":false`、且错误文案点名工具；证明 gate 优先于参数校验（合法参数下唯一失败路径就是 gate）。
- 对每个**只读工具**：断言不含 `"gated"`；除 `predict_passes`（无新鲜 TLE 时诚实返回 ok:false 空态，非 gate 拦截）外，其余 16 个确定性 `ok:true`。
- **零改动快照**：执行前后比对 centerFreq/demodMode/bandwidth/VFO 数量/selectedVfoId/squelch 使能与门限/recordingPath/recordingDir/watchEnabled/fftSize/windowType/averageMode/QSettings `rtl/ppm`/`view/wfColormapFile`，全部逐字段相等。
- 断言分裂计数严格 == **28 写 / 17 读**。

实测（Qt 6.8.2，offscreen）：

```
PASS : TestAgent::manualMode_gateSpotCheckAllWrites()
test_agent 全量: 28 passed, 0 failed（原 27 + 新增 1）
test_tool_schema: 10 passed | test_tool_registry: 8 passed
test_ai_real_link: 17 passed | test_control_hub: 26 passed
```

**gate 全覆盖真实计数**：28 写工具 100% gated（gated:true,ok:false）；17 只读工具 100% 放行（无 gated）；引擎/设置零改动。**未发现 gate 缺口**（无需修 agent_tools.cpp 的 gate 判定或 spec write 标志）。

## 4. 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `cpp/tests/test_agent.cpp` | +约 110 行 | 新增测试槽 `manualMode_gateSpotCheckAllWrites`（含 `#include "ai/tool_schema.h"`、槽声明、实现），仅测试无行为变更 |
| `docs/learn/phase62/tool-schema-field-audit.md` | 新增本文件 | 45 工具逐字段核对表 |
| `cpp/src/ai/*`、`cpp/src/control/*` | **无改动** | 逐字段复核结论：无真实不一致需要修；无 gate 缺口 |

附注：未执行任何 git add/commit/push；未引入任何赛事相关字样，GPL 措辞保持中立；活动参数零硬编码、未预置 TLE/呼号。

## 5. 关键引用索引

- Agent spec 表：`cpp/src/ai/tool_schema.cpp:63`（write 默认 false：`tool_schema.h:42`）
- Agent dispatch 表：`cpp/src/ai/agent_tools.cpp:1146`；gate 结果：`:23`；gate 判定源：`:1201 isWriteTool`
- Agent 手动门：`cpp/src/ai/llm_worker.cpp:118`
- ControlHub 表：`cpp/src/control/control_hub.cpp:81`；写门：`:358`；HTTP 委托：`control_http_server.cpp:306,331`
- 字段漂移点：`tune` :84 / `vfo_set_freq` :104 / `vfo_set_bandwidth` :106 / `vfo_set_mode` :107 / `channel` 读键 :325；Agent `channel_id` tool_schema.cpp:300,318,336,354,372
- 金集：`test_tool_registry.cpp:137,155,160`；`test_control_hub.cpp:303`；新增槽 `test_agent.cpp`（声明 + `manualMode_gateSpotCheckAllWrites`）
