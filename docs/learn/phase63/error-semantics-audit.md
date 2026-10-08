# 工具错误语义三通道一致性抽查（只读审计 + 落档）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `6ee1b10`（main）。
- 交付方式：**只读落档**。仅新增本文件；跑了既有测试二进制（offscreen），未改任何 `cpp/src/**`、未改任何测试、未 `git add/commit/push`。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，复用 `cpp/build` 既有二进制（未增量重编译）。
- 上轮基线：`docs/learn/phase63/gate-semantics-audit.md`（写门三元组同构已审计）。本轮聚焦**错误语义形状一致性**——把工具错误分 A/B/C 三类，逐通道核对返回信封（JSON 字段集合），不重跑全量 47。

## 0. 结论摘要

- **三通道错误信封的机器契约同构：`{"ok":false,"error":<msg>}` 扁平两字段是绝对主线。** Agent `errResult`（`agent_tools.cpp:569`）、ControlHub `errResult`（`control_hub.cpp:276`）、HTTP `errBody`（`control_http_server.cpp:27`）三个 helper **逐字节同形**。
- **CH ↔ HTTP：错误信封零漂移。** `POST /command` 一律 `hub_->execute().toUtf8()` 透传（`control_http_server.cpp:331`）；HTTP 自身的 400 传输层错误（坏 JSON / 缺 `tool` / `args` 非对象）也用同一 `{ok:false,error}` 形状（:309-328），只是 HTTP 状态码不同。
- **Agent ↔ CH：核心字段同构，Agent 是"可选超集"。** 共享 `{ok,error}`（gate 时加 `gated`）；差异仅在 **Agent 侧部分内联错误会追加 source 溯源三字段 `connected/test_signal/source`**（`addSourceFields`），CH/HTTP 控制层从不追加——属 Agent-LLM 契约有意为之（告诉模型是否在真硬件上），是**加性超集，不与 CH 矛盾**。
- **差异判定：零"该修" → 零待修。** 3 条观察项全部判"架构性不修"（§5）：Agent source 超集、`tune_frequency` 老工具无参数校验、`connect_network_source` 失败两侧均不用 `error` 键。
- 红线扫描：**CLEAN**（`ghp_` 0 真实 token；`competition/比赛/赛事` 在 `cpp/src/ai`、`cpp/src/control` 0 命中）。

### 0.1 金集实跑（本次独立执行，真实计数）

| 金集测试 | passed | failed | 关键错误语义槽结果 |
|---|---|---|---|
| `test_agent` | **30** | **0** | `vfoEditToolsBadArgsAndGate` PASS（坏参+门同槽）、`noiseBlankerLandReadbackAndGate` PASS、`manualMode_gateSpotCheckAllWrites` PASS |
| `test_control_hub` | **27** | **0** | `phase26WritesAreGatedAndBadArgsHonest` PASS（set_squelch/set_noise_blanker 坏参诚实）、`readAlwaysAllowedWriteGateBothStates` PASS |
| `test_control_http` | **14** | **0** | `postCommandArgsMustBeObject` PASS（400 信封）、`gateClosedRefusesWritePost` PASS、`postCommandNoiseBlankerRoute` PASS |
| **合计** | **71** | **0** | — |

> 运行期告警仅 `no librtlsdr ops bound; stub start()`（offscreen 无硬件，QINFO）与 HTTP 回环横幅（QWARN），均非 FAIL，不影响断言。

## 1. 三类错误的形状基线（源码事实）

### A 类 · 参数校验错误（缺参 / 类型错 / 越界）

| 通道 | helper / 判定点 | 产出信封 | file:line |
|---|---|---|---|
| Agent | `errResult(msg)` | `{"ok":false,"error":msg}`（扁平，**无 source 字段**） | `agent_tools.cpp:569-570` |
| ControlHub | `errResult(err)`，err 来自 `needBool/needDbl/needInt/needMode` | `{"ok":false,"error":err}`（扁平） | `control_hub.cpp:276-281`；helpers `:294-324` |
| HTTP | 透传 CH；自身 400 用 `errBody` | `{"ok":false,"error":...}`（扁平，HTTP 400） | `control_http_server.cpp:27-32`、透传 `:331` |

> 三通道 A 类信封字段集合**完全一致 = `{ok, error}`**。仅文案按通道区分（机器可断言的键一致）：
> - Agent：`"参数 on 缺失或不是布尔值"` / `"参数 freq_hz 缺失或不是数字"`
> - CH：`"缺少布尔参数: on"` / `"缺少数字参数: freq_hz"` / `"缺少整数参数: id"`
> - HTTP 400：`"POST /command 需要 JSON 请求体: ..."` / `"缺少字符串字段 \"tool\""`

### B 类 · 执行错误（引擎拒绝 / 无引擎 / 频率非法）

| 通道 | 产出信封（失败路径） | file:line |
|---|---|---|
| Agent | 多数 `{"ok":false,"error":msg}` 扁平；**部分内联错误追加 source 三字段** | 见 §3 逐工具 |
| ControlHub | `{"ok":false,"error":msg}` 扁平；个别带工具上下文键（`available`/`host`/`port`），**从不带 source** | 见 §3 |
| HTTP | 透传 CH，字节级一致 | `control_http_server.cpp:331` |

> CH 侧 execute() 固定前置两道诚实拒绝（均扁平 `{ok,error}`）：未知命令 `:346` `"未知命令: X"`、无引擎 `:354` `"无引擎连接（ControlHub 未 attach SpectrumEngine）"`。

### C 类 · gate 拦截（manualMode / 写门关闭）

上轮已审计信封三元组同构，本轮只复核 2 个代表，不重做：

| 通道 | 信封 | file:line | 复核 |
|---|---|---|---|
| Agent | `{"ok":false,"gated":true,"error":"手动模式：未执行 <name>"}` | `agent_tools.cpp:24-30` | `test_agent` manualMode 槽 PASS |
| ControlHub | `{"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 <cmd>"}` | `control_hub.cpp:283-289` | `test_control_hub` gate 槽 PASS |
| HTTP | 透传 CH（字节一致） | `control_http_server.cpp:331` | `test_control_http` gateClosed 槽 PASS |

## 2. A 类抽查：5 个工具 × 三通道字段逐项

| 工具（Agent 名 → CH 名） | Agent 坏参信封 | CH 坏参信封 | HTTP | 字段判定 |
|---|---|---|---|---|
| `set_noise_blanker` 缺 `on` | `{ok:false, error:"参数 on 缺失或不是布尔值"}`（:706-707） | `{ok:false, error:"缺少布尔参数: on"}`（:1267） | 透传 = CH | **一致**（文案异） |
| `set_vfo_frequency` 缺 `index`/`freq_hz` | `{ok:false, error:"参数 index 缺失或不是数字"}` 等（:944-948） | CH 名 `vfo_set_freq`，缺 `id`/`freq_hz`：`{ok:false, error:"缺少整数参数: id"}`（:696-697） | 透传 = CH | **一致**（键名 `index`↔`id` 为已登记命名漂移，非信封差异） |
| `tune_frequency` 缺 `freq_hz` | **无校验**：`args["freq_hz"].toDouble()` 静默得 0.0，返回 `{ok:true,...}`（:74-85） | CH 名 `tune`：`{ok:false, error:"缺少数字参数: freq_hz"}`（:410） | 透传 = CH | **差异（覆盖差，非字段差）** — 见 §5.2 |
| `add_bookmark` 缺 `freq_hz` | `{ok:false, error:"参数 freq_hz 缺失或不是数字"}`（:750-751） | `{ok:false, error:"缺少数字参数: freq_hz"}`（:1302） | 透传 = CH | **一致**（文案异） |
| `set_squelch` 缺 `enabled` | Agent `execSetSquelch` 对缺参**容错**（可选参，不报错，:669-678） | CH `set_squelch_enabled`：`{ok:false, error:"缺少布尔参数: enabled"}`（:500） | 透传 = CH | **差异（覆盖差）** — Agent 把 squelch 参当可选，CH 当必填；见 §5.2 |

> A 类核心字段 `{ok,error}` 三通道一致；上表 2 处"差异"是**校验严格度/覆盖**差异（Agent 老工具宽松 vs Phase26/CH 严格），不是错误信封的字段形状差异。

## 3. B 类抽查：执行错误 × Agent vs CH 字段逐项

| 场景 | Agent 失败信封 | CH 失败信封 | 字段判定 |
|---|---|---|---|
| `start_recording` 启动失败 | `{ok:false, error:"录制启动失败"}` 扁平（:114-116） | `{ok:false, error:"录制启动失败"}` 扁平（:537-539） | **完全一致** |
| `export_iq_segment` 无数据失败 | `{ok:false, error:<engine err>}` 扁平（:165-167） | `{ok:false, error:<engine err>}` 扁平（:583） | **完全一致** |
| `add_bookmark` 频率 ≤0 被拒 | `{ok:false, error:"频率非法：freq_hz 必须大于 0…", connected, test_signal, source}`（:766-770，**带 source 三字段**） | `{ok:false, error:"freq_hz 必须 >0"}` 扁平（:1310-1311） | **核心一致，Agent 超集 source** — §5.1 |
| `set_doppler_compensation` 无控制面 | `{ok:false, available:false, error:"多普勒补偿不可用（无 UI 控制面；需…）", connected, test_signal, source}`（:1174-1179） | `{ok:false, available:false, error:"多普勒补偿不可用（无 UI 控制面）"}`（:470-474，**带 available，不带 source**） | **核心 `{ok,available,error}` 一致，Agent 超集 source** — §5.1 |
| `connect_network_source` 连接失败 | `{ok:false, host, port, source:"连接失败（真实 socket 错误已回传）", connected, test_signal, source}`（:1206-1211，**无 `error` 键**，理由塞 `source`） | `{ok:false, command:"connect_network_source", host, port}`（:491-495，**无 `error` 键、无理由串**） | **两侧均不用 `error` 键**；Agent 多 `source` 理由 + source 溯源 — §5.3 |

## 4. HTTP 透传段核对（`control_http_server.cpp`）

- `POST /command`（:306-332）：先解析 body → **合法即 `return hub_->execute(tool, args).toUtf8();`**（:331）。无 per-route handler、无 HTTP 层独立 gate / 独立参数校验。
- :329-330 注释明示："Unknown command / bad args / gate-closed are ALL honest results from execute() itself … we just relay them."
- **HTTP 自有错误只有传输层 400/404**（:309 坏 JSON、:316 缺 `tool`、:324 `args` 非对象、:335 未知路径），全部走 `errBody` = `{ok:false,error}` 扁平，与 CH errResult 同形。
- 结论：**业务错误（A/B/C 类）HTTP 通道 = CH 信封逐字节继承**；传输错误 HTTP 自造但形状一致。架构上不可能与 CH 漂移。

## 5. 差异判定清单（零"该修"，3 条"架构性不修"）

**核心错误契约 `{ok:false, error}`（gate 时加 `gated`）三通道同构，零待修。** 逐条：

1. **【架构性不修】Agent 错误偶发追加 source 三字段（CH/HTTP 不加）。**
   - 位置：Agent `addSourceFields`（`agent_tools.cpp:58-62`）被部分**内联构造**的错误调用（doppler :1178、add_bookmark :769、connect_network_source :1201/:1211）；而 `errResult`（:569）路径**不加**。
   - 对照：CH `errResult`（:276）与 HTTP `errBody`（:27）从不追加 source——控制层无 `addSourceFields` 等价物。
   - 判定：**不修。** source 三字段是 Agent 面向 LLM 的"是否真硬件"溯源契约（:32-34 注释），属**加性超集**，机器断言的 `ok/error` 键不冲突；CH/HTTP 是裸控制信道，本就不应夹带 LLM 溯源。Agent 内部 `errResult`(扁平) vs 内联(带 source) 的不对称，是因为 `errResult` 是无 `src` 入参的自由函数、拿不到 source 快照；要统一需重构 `errResult` 签名贯穿全部 executor，风险大、无测试要求、收益低。
2. **【架构性不修 / 观察】`tune_frequency` Agent 侧无参数校验（老工具宽松）。**
   - 位置：`execTuneFrequency`（`agent_tools.cpp:74-85`）直接 `args["freq_hz"].toDouble()`，缺参/类型错静默得 0.0 并返回 `ok:true`；而 CH `tune`（`control_hub.cpp:410`）走 `needDbl` 诚实报错。`set_squelch` 同理（Agent :669 把参当可选，CH :500 当必填）。
   - 判定：**不修（本轮只读，且属覆盖差非字段差）。** `tune_frequency`/`set_mode` 等是最早一批 14 个工具，早于 Phase26 的 `needNum/needBool` 校验纪律；CH 与新工具已统一严格。这是"老工具宽容 vs 新工具严格"的历史分层，错误信封本身不矛盾（Agent 不报错时根本不产错误信封）。建议（不本轮做）：后续给老工具补 `needNum` 时对齐 CH。
3. **【架构性不修】`connect_network_source` 失败两侧均不用 `error` 键。**
   - 位置：Agent（:1206-1211）把失败理由放进 `source` 串；CH（:491-495）只回 `{ok:false, command, host, port}`，理由经 engine 信号带外传出（:484 注释 "the engine emits the honest socket reason"）。
   - 判定：**不修。** 两侧都以 `ok:false` 表态失败，仅"人类可读理由"的落点不同（Agent 塞 `source`，CH 走信号带外）；不影响 `ok` 机器契约，且 CH 信封本就是裸回显。

> 另：`set_vfo_frequency`(Agent) ↔ `vfo_set_freq`(CH) 的命名漂移（`index`↔`id`）为 `tool-golden-47-check.md §2.1` 已登记 4 对漂移之一，已被金集冻结，非本轮新缺陷。

## 6. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `ghp_`（GitHub token 泄露） | `cpp/src/ai/**`、`cpp/src/control/**` | **0 真实 token** |
| `competition` / `比赛` / `赛事` | `cpp/src/ai/**`、`cpp/src/control/**` | **0 命中** |

## 7. 诚实未完成项与边界

- **二进制↔源码一致性**：本轮复用 `cpp/build` 既有二进制（未重编译）。错误信封的全部源码结论落在三个文件——`ai/agent_tools.cpp`、`control/control_hub.cpp`、`control/control_http_server.cpp`——审计全程 `git status` 显示这三文件**均未被修改**（见下条），故结论行号可信。金集 71/71 PASS 佐证二进制行为与源码一致。
- **观察到他会话在途改动（已按纪律不触碰）**：开跑前 `git status` 仅 3 个未跟踪隔离文件；审计中途出现一批**非我所改**的已跟踪修改——`cpp/src/core/tokens.h`、`cpp/src/ui/spectrum_display.cpp`、`cpp/src/ui/spectrum_display.h`。我未编辑、未 revert、未 stage、未 commit 任何一个，原样留待其所属会话收尾。这三文件**均不在错误信封三文件内**（错误契约集中在 `ai/agent_tools.cpp`、`control/control_hub.cpp`、`control/control_http_server.cpp`），故本审计的信封形状结论不受在途改动影响。`tokens.h` 仅含常量（kFreqMinHz 等），我未引用其行号。
- **HTTP 66 命令自动覆盖为架构断言**：统一委托 `:331`、无 per-route handler，测试只钉代表路由（noise_blanker / vfo_armed / capabilities / recording_state / argsMustBeObject），未枚举跑全 66 条——与上轮口径一致。
- **C 类 gate 未重做**：仅按要求复核 2 个代表（Agent gatedToolResult / CH gatedResult），完整 43 写门审计见 `gate-semantics-audit.md`，本轮不重复展开。
- 未触碰 `cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp` 任何隔离文件；未执行任何 `git add/commit/push`。
