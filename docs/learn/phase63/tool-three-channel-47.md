# 工具三通道逐字段核对表（47 版）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），核对基线 HEAD = `9efbdcb`（main）
- 交付方式：**只读落档**。仅新增本文件，未改任何 `cpp/src/**`、未 `git add/commit/push`。工作树中既有的未跟踪文件 `cpp/tests/ui_diag_freeze.cpp` 与本次无关，未触碰。
- 三通道定义（沿用上轮 phase62 口径）：
  1. **Agent spec**：`cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`（JSON Schema + `write` 声明；`tool_schema.h` 默认 `write=false`）
  2. **dispatch**：`cpp/src/ai/agent_tools.cpp::dispatchTable()`（name → executor）；Agent 运行时入口 `llm_worker.cpp::dispatchToolCall()`（先 manual-mode 写门，再 `executeTool`）
  3. **ControlHub**：`cpp/src/control/control_hub.cpp::table()`（命令名 + write 标志 + handler）；HTTP `POST /command` 统一委托 `ControlHub::execute()`（无 per-route handler），故 HTTP 可达性由 CH 表自动覆盖

> 上轮对照：`docs/learn/phase62/tool-three-channel-audit.md`（45 版，HEAD `e85a88a`）、`docs/learn/phase62/tool-schema-field-audit.md`（45 版）。本表独立成篇，不覆盖旧档；本轮相对上轮的增量是 **+2 个 noise_blanker 工具**。

## 0. 结论摘要

| 通道 | 规模 | 写 / 读 |
|---|---|---|
| Agent spec | **47** | **29 / 18** |
| dispatch | **47** | —（与 spec 集合完全相等） |
| ControlHub 命令 | **66** | **43 / 23** |

（上轮：Agent 45＝28/17；CH 64＝42/22。本轮 +1 写 `set_noise_blanker`、+1 读 `get_noise_blanker_status`，两端各 +2。）

- **spec ↔ dispatch：集合完全相等（47=47）**，无缺行、无孤儿 executor。`isWriteTool()` 直接读 spec 的 `write` 标志（`agent_tools.cpp:1231`），无并行硬编码写集合可漂移。
- **47 个 Agent 工具中 41 个与 ControlHub 同名同标志**；4 个存在命名漂移（见 §2.1），2 个 Agent 独有无 HTTP 通道（§2.2）。**无一处 read/write 标志错误**。
- **新增 noise_blanker 对干净一致**：Agent↔CH 同名、同参数 `on`(boolean, required)、同缺参/非法类型即 `{ok:false}`；两侧金集各自钉住（§4）。
- 差异判定：**未发现需要改码的真缺陷**。所有差异均为 phase62 已登记、且被金集冻结/有意设计的架构性漂移（§2）。本轮零待修。

## 1. 47 工具逐行一致性表

列说明：Agent W/R = spec `write` 标志；CH 列 = ControlHub 同名/异名命令；HTTP 可达性 = 该命令是否存在于 CH 表（写门关闭时写命令被 `gatedResult` 拦截，读命令放行）。

| # | Agent 工具 | Agent | dispatch | ControlHub 对应 | CH 标志 | HTTP 可达 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | tune_frequency | W | ✓ | `tune`（control_hub.cpp:84） | W | 可达（走 tune，写门关闭则被拦） | 命名漂移，参数同为 `freq_hz`，**架构性不修** |
| 2 | set_mode | W | ✓ | set_mode | W | 可达 | 一致 |
| 3 | start_recording | W | ✓ | start_recording | W | 可达 | 一致（两侧失败文案均"录制启动失败"） |
| 4 | stop_recording | W | ✓ | stop_recording | W | 可达 | 一致 |
| 5 | scan_band | W | ✓ | scan_band | W | 可达 | 一致（step_hz 缺省 200k，CH 另有下限保护） |
| 6 | set_bandwidth | W | ✓ | set_bandwidth | W | 可达 | 一致 |
| 7 | get_status | R | ✓ | get_status | R | 可达 | 一致 |
| 8 | predict_passes | R | ✓ | predict_passes | R | 可达 | 一致（纯函数，不伪造过境） |
| 9 | calibrate_frequency | R | ✓ | — | — | **无 HTTP 通道** | Agent 独有（桌面 AI 测量），**架构性不修** |
| 10 | apply_frequency_correction | W | ✓ | — | — | **无 HTTP 通道** | Agent 独有（写 QSettings rtl/ppm），**架构性不修** |
| 11 | get_pocsag_messages | R | ✓ | get_pocsag_messages | R | 可达；参数 `channel_id`(Agent)↔`channel`(CH:327) | 同名同标志；参数名漂移，**架构性不修** |
| 12 | get_m17_calls | R | ✓ | get_m17_calls | R | 可达；同上 `channel_id`↔`channel` | 同上 |
| 13 | get_vor_radial | R | ✓ | get_vor_radial | R | 可达；同上 | 同上 |
| 14 | get_acars_packets | R | ✓ | get_acars_packets | R | 可达；同上 | 同上 |
| 15 | get_navtex_messages | R | ✓ | get_navtex_messages | R | 可达；同上 | 同上 |
| 16 | export_iq_segment | W | ✓ | export_iq_segment | W | 可达 | 一致 |
| 17 | set_network_audio_sink | W | ✓ | set_network_audio_sink | W | 可达 | 一致（routed 注册层，见 §2.4） |
| 18 | get_network_audio_status | R | ✓ | get_network_audio_status | R | 可达 | 一致 |
| 19 | start_scan_link | W | ✓ | start_scan_link | W | 可达 | 一致（routed） |
| 20 | stop_scan_link | W | ✓ | stop_scan_link | W | 可达 | 一致（routed） |
| 21 | get_scan_link_status | R | ✓ | get_scan_link_status | R | 可达 | 一致 |
| 22 | set_squelch | W | ✓ | set_squelch | W | 可达 | 一致 |
| 23 | get_squelch_status | R | ✓ | get_squelch_status | R | 可达 | 一致 |
| 24 | **set_noise_blanker** | W | ✓ | set_noise_blanker（:115） | W | 可达 | **一致（本轮新增）**；参数 `on`(bool,required) 两侧同名同义 |
| 25 | **get_noise_blanker_status** | R | ✓ | get_noise_blanker_status（:151） | R | 可达 | **一致（本轮新增）**；无参，回读 `enabled` |
| 26 | list_bookmarks | R | ✓ | list_bookmarks | R | 可达 | 一致 |
| 27 | add_bookmark | W | ✓ | add_bookmark | W | 可达 | 一致（routed） |
| 28 | tune_to_bookmark | W | ✓ | tune_to_bookmark | W | 可达 | 一致（routed；index 两侧同名） |
| 29 | delete_bookmark | W | ✓ | delete_bookmark | W | 可达 | 一致（routed） |
| 30 | list_vfos | R | ✓ | list_vfos | R | 可达 | 一致 |
| 31 | add_vfo | W | ✓ | add_vfo | W | 可达 | 一致 |
| 32 | switch_vfo | W | ✓ | switch_vfo | W | 可达 | 一致 |
| 33 | rename_vfo | W | ✓ | rename_vfo | W | 可达 | 一致 |
| 34 | set_vfo_armed | W | ✓ | set_vfo_armed（:108） | W | 可达 | 一致（两侧同参数 `index`，同"VFO index 越界"文案） |
| 35 | set_vfo_frequency | W | ✓ | `vfo_set_freq`（:104） | W | 可达（走 vfo_set_freq） | 命名+参数漂移（`index` 序号↔`id` 原值），**架构性不修** |
| 36 | set_vfo_mode | W | ✓ | `vfo_set_mode`（:107） | W | 可达（走 vfo_set_mode） | 同上 |
| 37 | set_vfo_bandwidth | W | ✓ | `vfo_set_bandwidth`（:106） | W | 可达（走 vfo_set_bandwidth） | 同上 |
| 38 | list_recordings | R | ✓ | list_recordings | R | 可达 | 一致 |
| 39 | delete_recording | W | ✓ | delete_recording | W | 可达 | 一致 |
| 40 | export_recording | W | ✓ | export_recording | W | 可达 | 一致 |
| 41 | set_fft_params | W | ✓ | set_fft_params | W | 可达 | 一致 |
| 42 | set_color_map | W | ✓ | set_color_map | W | 可达 | 一致（写 QSettings 色板键） |
| 43 | get_spectrum_status | R | ✓ | get_spectrum_status | R | 可达 | 一致 |
| 44 | set_doppler_compensation | W | ✓ | set_doppler_compensation | W | 可达 | 一致 |
| 45 | connect_network_source | W | ✓ | connect_network_source | W | 可达 | 一致 |
| 46 | get_capabilities | R | ✓ | get_capabilities | R | 可达 | 一致（诚实空态 connected=false/空 gains） |
| 47 | get_recording_state | R | ✓ | get_recording_state | R | 可达 | 一致 |

## 2. 差异清单与判定

### 2.1 命名漂移（同名不同入口名，能力/标志等价）——架构性不修

| Agent | ControlHub | 参数语义 | 不修理由 |
|---|---|---|---|
| `tune_frequency` | `tune` | 均 `freq_hz`；Agent executor 直接 `onSetCenterFreq`（不 clamp），CH `cmdTune` 弹性 clamp 到 token 范围并回 `clamped` 标志 | Wave1 历史命名；两侧金集各自钉住（`test_tool_registry.cpp` 钉 `tune_frequency`、`test_control_hub.cpp:312` 钉 `tune` 为写）。改名同时破坏 LLM on-wire 契约与 HTTP API，超审计范围 |
| `set_vfo_frequency` | `vfo_set_freq` | Agent 用 marker 序号 `index`（executor 内 `vfoMarkers()[i].id` 解析，越界报"VFO index %1 越界（共 %2 个）"）；CH 用原始 VFO `id`（`needInt "id"`，**不做越界检查**） | Phase61 Agent 工具与 Phase26 前 CH 底层命令并行；两套参数语义各自有意设计、各自金集冻结。HTTP 要暴露 marker-index 语义应作独立增量（别名行），非本轮小修 |
| `set_vfo_mode` | `vfo_set_mode` | 同上 `index`↔`id`；两侧模式校验都走同一张 `tokens::kControlHubModes`，故非法模式两侧同拒 | 同上 |
| `set_vfo_bandwidth` | `vfo_set_bandwidth` | 同上 `index`↔`id`；Agent 另查 `bandwidth_hz>0` | 同上 |

> 对照：`set_vfo_armed`（#34）虽属 Phase59 VFO 族，却**同名同参 `index`** 两侧一致（CH `:749-759` 也按 marker index 越界检查），是该族里唯一未漂移的。

### 2.2 Agent 独有（无 HTTP 通道）——架构性不修

`calibrate_frequency`(R)、`apply_frequency_correction`(W)。CH 表无对应行。源码注释明确该对为桌面 AI 专用（校准需参考信号 PTT 场景，headless HTTP 暴露非设计意图；`tool_schema.cpp:211-281`）。`calibrate_frequency` 的 `write=false` 是有意分类（测量不得被手动门拦、不持久化），且有 `manualMode_allowsCalibrateRead` 金集钉住。

### 2.3 ControlHub 独有（21 个，未暴露给 LLM）——架构性不修

- **写 15**：`set_sample_rate`、`set_gain`、`set_squelch_enabled`、`set_squelch_threshold`、`set_muted`、`set_anr`、`set_gated_recording`、`set_watch`、`set_tuner_agc`、`set_rtl_agc`、`vfo_add`、`vfo_remove`、`vfo_select`、`vfo_set_offset`、`clear_digital_outputs`
- **读 6**：`get_frequency`、`get_mode`、`get_bandwidth`、`get_telemetry`、`list_gains`、`get_vfos`

判定：headless/HTTP 底层控制原语（增益/AGC/静音/遥测等），有意不暴露给 LLM，避免 AI 直接操纵底层射频原语。标志分类本身由 `test_control_hub.cpp::commandTableIsClassified` 校验通过。

### 2.4 routed 注册层工具的"ok:true 不落地"——架构性不修（记录在案）

`add_bookmark`/`tune_to_bookmark`/`delete_bookmark`/`start_scan_link`/`stop_scan_link`/`set_network_audio_sink` 的 Agent executor 只做**参数存在/类型校验**，随后返回 `routedOk`：

```json
{"ok":true,"<回显参数>","routed_command":"add_bookmark","note":"AI 注册层已接收；实际硬件效果由 ControlHub 命令 add_bookmark 落地", <source字段>}
```

- Agent 运行时链路 `llm_worker::dispatchToolCall → executeTool → executor` **并不回调 ControlHub**（已核实 `llm_worker.cpp:113-122`、`agent_tools.cpp:1257-1268`）。因此经 App 内 LLM 调用时，这些工具在 AI 层即返回 `ok:true`，**不实际增删书签/启停扫描**。
- 这与 CH/HTTP 侧不同：HTTP 经 `execute()` 真正执行命令，并做深层校验（越界、文件不存在、端口范围），失败回 `{ok:false,"error":"书签下标越界: N"}`。
- **判定：架构性不修**。源码注释（`agent_tools.cpp:567-576`）明确这是"AI 注册层接收、真实效果落在 CH 同名命令"的契约式设计；返回体诚实标注 `routed_command` 而非谎称已生效。写门仍正确生效（这些工具 spec `write=true`，手动模式被 `gatedToolResult` 拦截，见 §3）。若未来要让 App 内 LLM 路径真正落地书签，应作为独立增量接一条 `executor → ControlHub` 转发缝，非本轮核对范围。

### 2.5 参数名 `channel_id`(Agent) ↔ `channel`(CH)——架构性不修

5 个解码快照工具（#11-15）。两侧缺省都取当前选中 VFO（Agent `resolveChannelId` `agent_tools.cpp:398-403`；CH `resolveChannel` `control_hub.cpp:326-334`），语义等价，仅 JSON key 名不同。两侧金集各自钉住（CH 侧测试用 `channel`）。改任一侧都破坏冻结契约，留待后续契约统一决策。

### 2.6 未发现的类别（真缺陷为零）

- **read/write 标志错**：无。29 写工具集合与 `kExpectedWriteTools` 严格相等（`test_tool_registry.cpp:67-85`）；18 读与 `kFlutterUngatedReadTools`（:104-114）严格相等。
- **dispatch 缺行/孤儿**：无。spec 集合 == dispatch 集合 == 47。
- **HTTP 不可达的已暴露工具**：无。除 2.2 的桌面专用对外，每个 Agent 写/读工具都有对应 CH 行（同名或 §2.1 异名）。

## 3. 写门（gate）语义核对

- **Agent 侧**：`llm_worker.cpp:118` `if (manualMode && isWriteTool(name)) return gatedToolResult(name);`（先于任何引擎接触）。门结果信封：`{"ok":false,"gated":true,"error":"手动模式：未执行 <name>"}`。
- **CH 侧**：`control_hub.cpp:360` `if (row->write && !writeEnabled_.load()) return gatedResult(row->name);`。门结果信封：`{"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 <command>"}`。
- 两侧**结构一致**（同为 `{ok:false,gated:true,error:...}`），仅中文措辞不同（"手动模式" vs "write gate 关闭"）。读命令两侧均不拦截、真实执行。

## 4. 金集交叉验证（只读 grep，未运行）

| 钉住项 | 位置 | 与本表结论 |
|---|---|---|
| spec/dispatch 规模 = 47 | `test_tool_registry.cpp:139`（`expected 47 tools`）、`:234`（defs==47） | 一致 |
| 29 写工具集合相等 | `kExpectedWriteTools`（:67-85），含 `"set_noise_blanker"`；`:166 QCOMPARE(actualWrite, kExpectedWriteTools)`；`:176-177` 钉 `set_noise_blanker` 写 / `get_noise_blanker_status` 非写 | 一致 |
| 18 读工具与 Flutter 互补集相等 | `kFlutterUngatedReadTools`（:104-114），含 `"get_noise_blanker_status"`；`:196 QCOMPARE(readOnly, ...)` | 一致 |
| CH 命令分类（tune 写 / get_frequency 读 / start_recording 写 / get_status 读 / clear_digital_outputs 写 / 三个快照读） | `test_control_hub.cpp:304-324 commandTableIsClassified` | 一致 |
| noise_blanker CH 落地+回读+缺参/错型拒 | `test_control_hub.cpp:537-567 noiseBlankerSetLandAndStatusReadsBack`（`{on:true}` 翻转引擎、缺 `on`/`"yes"` 串均 `ok:false`） | 一致（§5.1） |
| Phase26 写命令写门关闭即拦、读命令放行 | `test_control_hub.cpp:747-764`，循环含 `"set_noise_blanker"` | 一致 |
| noise_blanker Agent 落地+回读+写门+缺参/错型 | `test_agent.cpp:782-828 noiseBlankerLandReadbackAndGate`（manualMode=false 翻转引擎、manualMode=true `gated:true,ok:false`、缺 `on`/`"yes"` 串 `ok:false`） | 一致（§5.1） |
| HTTP 走通用 `POST /command → ControlHub.execute()` 委托，noise_blanker 写翻真开关、读回读、写门关闭即拦引擎不动 | `test_control_http.cpp:501-540` | 一致（HTTP 自动覆盖 CH 66 命令） |
| 未知命令/缺 tool 字段/畸形 JSON → HTTP 200 但 `{ok:false}` | `test_control_http.cpp:282-294` | 一致 |

## 5. 错误语义抽查（4 个写工具，Agent ↔ CH 逐字段）

统一前置事实：
- **Agent 运行时**在 executor 之前有一道 JSON-Schema 上游校验 `arguments_validator.cpp::validateArguments`（`llm_worker.cpp:246-256`）：缺必填→"缺少必填参数：X"；类型错→"参数 X 类型错误：期望 T 实得 V"；越界/枚举不符/幻觉字段各有文案；错误信封 `{"ok":false,"error":"参数校验失败：<首条>","reasons":[...全部理由...]}`，以 `role=tool` 回注自纠。executor 自身再做深层校验（越界/bool）时用 `errResult`→`{"ok":false,"error":msg}`。
- **CH/HTTP 运行时**无上游 schema 校验，每条命令自行 `needDbl/needBool/needInt/needMode`：缺 double→"缺少数字参数: X"；缺 bool→"缺少布尔参数: X"；缺 int→"缺少整数参数: X"；模式→"缺少模式参数: X"/"未知解调模式: X"。错误信封统一 `{"ok":false,"error":msg}`。
- 两侧错误信封**结构一致**（均 `{ok:false,error:...}`；Agent 上游校验额外带 `reasons[]`，供 LLM 自纠），仅中文文案措辞不同。

### 5.1 set_noise_blanker（本轮新增）

| 场景 | Agent executor（`agent_tools.cpp:676-688`） | CH handler（`control_hub.cpp:1265-1273`） |
|---|---|---|
| 正常 `{on:true}` | `{ok:true,enabled:true,message:"噪声抑制开关已下发",<src>}` | `{ok:true,command:"set_noise_blanker",enabled:true}` |
| 缺 `on` | `{ok:false,error:"参数 on 缺失或不是布尔值"}`（executor 自校验，`!contains("on")\|\|!isBool()`） | `{ok:false,error:"缺少布尔参数: on"}`（`needBool`） |
| `on:"yes"`(串) | `{ok:false,error:"参数 on 缺失或不是布尔值"}` | `{ok:false,error:"缺少布尔参数: on"}` |

→ 判定：**一致**。参数名 `on`、布尔必填、缺/错即 `ok:false`，两侧金集各钉（§4）。仅文案措辞差异（"参数…缺失或不是布尔值" vs "缺少布尔参数: on"），不构成缺陷。

### 5.2 set_vfo_frequency（异名对）

| 场景 | Agent（`agent_tools.cpp:850-873`） | CH `vfo_set_freq`（`control_hub.cpp:694-704`） |
|---|---|---|
| 参数名 | `index`(marker 序号) + `freq_hz` | `id`(原始 VFO id) + `freq_hz` |
| 缺 index/freq_hz | 上游 schema 先拦（index/freq_hz 均 required）；executor 再 `needNum`→"参数 index/freq_hz 缺失或不是数字" | `needInt "id"`→"缺少整数参数: id"；`needDbl "freq_hz"`→"缺少数字参数: freq_hz" |
| 越界 | `{ok:false,error:"VFO index %1 越界（共 %2 个）"}`（按 `vfoMarkers()` 个数查） | **不做越界检查**，直接 `engine_->vfoSetFreq(id,hz)` |

→ 判定：**架构性不修**（§2.1）。index↔id 语义漂移 + Agent 多一层越界护栏，CH 信任传入的原始 id。已被两侧设计意图与冻结测试覆盖。

### 5.3 start_recording（同名）

| 场景 | Agent（`agent_tools.cpp:97-114`） | CH（`control_hub.cpp:530-541`） |
|---|---|---|
| 参数 | 无 | 无 |
| 成功 | `{ok:true,message:"开始录制",path:<recordingPath>,<src>}` | `{ok:true,command:"start_recording",path:<recordingPath>}` |
| 录制器失败 | `{ok:false,error:"录制启动失败"}`（`engine->startRecording()` 真实 bool） | `{ok:false,error:"录制启动失败"}` |

→ 判定：**一致**。失败文案两侧逐字相同，均来自 recorder 真实 bool，不伪造成功。

### 5.4 tune_to_bookmark（同名，routed）

| 场景 | Agent（`agent_tools.cpp:728-735`） | CH（`control_hub.cpp:1319-1332`） |
|---|---|---|
| 参数名 | `index`(required) | `index`(required) |
| 缺 index | 上游 schema 先拦；executor `needNum`→"参数 index 缺失或不是数字" | `needInt "index"`→"缺少整数参数: index" |
| index 越界 | **不查**：直接 `routedOk{ok:true,index:<回显>,routed_command:"tune_to_bookmark",note:...}`（§2.4） | `{ok:false,error:"书签下标越界: <idx>"}`（按 `bookmarks_->count()` 查） |

→ 判定：**架构性不修**（§2.4 routed 设计）。Agent 注册层只校验类型、不做越界/落地；CH 真正执行并回越界错。两侧缺参即 `ok:false` 一致；深层错误仅 CH 侧有。

## 6. 关键引用索引

- Agent spec 表：`cpp/src/ai/tool_schema.cpp:63`（本轮新增块 `set_noise_blanker` :533-547、`get_noise_blanker_status` :549-557）
- dispatch 表：`cpp/src/ai/agent_tools.cpp:1174`（noise_blanker 行 :1201-1202）；`isWriteTool` 读 spec :1231；`errResult`/`needNum`/`routedOk` :552-577
- Agent 写门：`cpp/src/ai/llm_worker.cpp:118`；上游 schema 校验：`:246`、`arguments_validator.cpp:45-145`
- ControlHub 表：`cpp/src/control/control_hub.cpp:81`（写门 :360；`resolveChannel` 读 `channel` :326-334；`needDbl/needBool/needInt/needMode` :294-324；`gatedResult` :283；`tune` :408、`vfo_set_freq` :694、`cmdSetNoiseBlanker` :1265、`cmdStartRecording` :530、`cmdTuneToBookmark` :1319）
- HTTP 委托：`cpp/src/control/control_http_server.cpp` 统一 `POST /command → ControlHub::execute()`（测试钉住 `test_control_http.cpp:501-540`）
- 金集：`test_tool_registry.cpp:67,104,139,166,196,234`；`test_control_hub.cpp:304,537,747`；`test_agent.cpp:782`

## 7. 自查（硬约束）

- 零代码改动：本文件为唯一产出。本会话只做只读解析（Read/Grep/只读 Bash）+ 写入本新档，未编辑任何既有 `cpp/src` 文件。
- 未执行任何 `git add/commit/push`。
- 措辞中立（未引入竞技/赛事类营销表述）；GPL 相关表述保持中立；未预置任何 TLE/呼号；`repos/` 下 SDR++ 源码仅作机制参考、未复制。
- 待修项：**无**。本轮所有差异均判定为架构性冻结/有意设计，列于 §2，供后续契约统一决策时复用。
