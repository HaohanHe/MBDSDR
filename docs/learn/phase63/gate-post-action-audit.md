# Gate 后置行为一致性抽查（拦截后呈现 = 只读审计 + 落档）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `eb3a1f0`（main）。
- 交付方式：**只读落档**。仅新增本文件；跑了既有测试二进制（offscreen），未改任何 `cpp/src/**`、未改任何测试、未 `git add/commit/push`。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，复用 `cpp/build` 既有二进制（未增量重编译）。
- 上轮基线：`gate-semantics-audit.md`（信封三元组同构）、`error-semantics-audit.md`（错误信封同构）。本轮聚焦**gate 拦截之后**的呈现行为——手动模式拦截后，UI / 日志 / 客户端是否**如实反映"未执行"**（无假成功、无半执行、无误导性状态变化）。

## 0. 结论摘要

- **三通道拦截后呈现全部诚实，零假成功、零半执行。** 桌面 Agent 对话在拦截当下给内联标注 `[已拦截·手动模式: <tool>]`（`main_window.cpp:2898`）；自主任务路径给独立 `已手动拦截` 警示态 + 报告行"N 步被手动模式拦截，未真正动作"（`task_orchestrator.cpp:189`、`task_steps_view.cpp:21`）；mobile 把 gated 结果归入**红叉失败态**并附摘要"手动模式：未执行 X"（`task_step.dart:79-99`），绝不显示绿勾成功；HTTP 通道响应体本身就是 `{ok:false,gated:true,error}` 透传。
- **半执行检查：三通道 gate 判定点全部在"动引擎/解析参数之前"。** Agent `llm_worker.cpp:119` 先返 `gatedToolResult` 不进 `executeTool`；CH `control_hub.cpp:360` 在 `dispatch()` `:385` 之前；自主任务 `task_orchestrator.cpp:153` 复用同一 `dispatchToolCall`。gated 命令连参数都不向引擎落地。
- **引擎零漂移断言已存在且实跑通过**：`test_agent.cpp:763-778` 对 29 个写工具 gated 后逐字段 byte 比对 16 个观测点（频率/模式/带宽/VFO/静噪/noise_blanker/录制/FFT/QSettings）；HTTP 侧 `gateClosedRefusesWritePost`（频率不变）、`postCommandNoiseBlankerRoute`（引擎不动）同槽锁定。
- **差异判定：零"该修" → 零待修。** 2 条"架构性不修/观察"（§5）：① `[已拦截]` 是 turn 内瞬态注记，终态清除后"未执行"的持久留存依赖 LLM 把 role=tool 结果转述进最终回复（设计如此，注释 `main_window.cpp:2886-2888` 明示）；② mobile 把 gated 视觉归入 failed（红）vs 桌面独立 gated（警示色）——粒度差异，两侧都诚实。
- 红线扫描：**CLEAN**（`ghp_` 0 真实 token；`competition/比赛/赛事` 在 `cpp/src/ai`、`cpp/src/control` 0 命中）。

### 0.1 金集实跑（本次独立执行，真实计数）

| 金集测试 | passed | failed | 关键后置行为槽 |
|---|---|---|---|
| `test_agent` | **30** | **0** | `manualMode_gateSpotCheckAllWrites` PASS（29 写全 gated + 16 字段 byte 不变）、`noiseBlankerLandReadbackAndGate` PASS |
| `test_control_hub` | **27** | **0** | `readAlwaysAllowedWriteGateBothStates`、`phase26WritesAreGatedAndBadArgsHonest` PASS |
| `test_control_http` | **14** | **0** | `gateClosedRefusesWritePost`（tune 拦 + 频率不变）、`postCommandNoiseBlankerRoute`（引擎不动）PASS |
| `test_ui_integration` | **20** | **0** | `aiManualToggleWiresAgentAndAnnotatesGated` PASS（chat 含 `[已拦截·手动模式: tune_frequency]`，已执行工具保留 `[调用工具: ...]`） |
| **合计** | **91** | **0** | — |

> 运行期告警仅 `no librtlsdr ops bound; stub start()`（offscreen 无硬件，QINFO）与 HTTP 回环横幅（QWARN），均非 FAIL。
>
> **诚实记录（我自己的环境污染，已纠正）**：首跑时我误设 `MBDSDR_TEST_SOURCE=1`（照搬 `test_ui_integration` 的 init），导致 `manualMode_gateSpotCheckAllWrites` 首条断言 `:763` 失败（期望 freqBefore=98.5MHz、实际 after=0）——该 env 改变了引擎源构造，非 gate 缺陷。`unset` 后重跑 30/30 全绿。此变量**不该**用于 test_agent，本审计后续一律不设。

## 1. 拦截后信封真实性（3 代表复核）

| 代表写工具 | Agent 侧（manualMode） | CH 表行 / handler | HTTP 透传 | 引擎零变化证据 |
|---|---|---|---|---|
| `set_noise_blanker` | `gatedToolResult` `{ok:false,gated:true,error:"手动模式：未执行 set_noise_blanker"}`（`agent_tools.cpp:24-30`），在 `llm_worker.cpp:119` 拦截 | 表行 `control_hub.cpp:115` `write=true` → `cmdSetNoiseBlanker :1265`（经单点 `:360`） | `control_http_server.cpp:331` 字节透传 | `test_agent:770` `noiseBlankerEnabled()` 前后相等；`test_control_http:533-540` 门关闭引擎仍 true |
| `tune`（Agent 名 `tune_frequency`） | 同上信封（args 给 98.5MHz `test_agent:700`），`:119` 不进 executeTool | 表行 `:84` `write=true` → `cmdTune :408`（经 `:360`） | 透传 | `test_agent:763` `centerFreq()` 前后相等；`test_control_http:217-228` GET /status 频率逐分不差 |
| `start_recording` | 同上信封（无参写，`test_agent:697` 注释明示） | 表行 `:92` `write=true` → `cmdStartRecording :530`（经 `:360`） | 透传 | `test_agent:771-772` `recordingPath()/recordingDir()` 前后相等 |

- **"引擎零漂移"断言覆盖确认：已存在，不缺。** `test_agent.cpp:674-779` 不是只断言信封，而是先在 `:678-693` 快照 16 个观测字段、跑完 29 写 gated 后在 `:763-778` 逐字段 `QCOMPARE` byte 相等；`QCOMPARE(writes,29)/(reads,18)` 冻结（`:757-758`）。HTTP 侧两个代表槽同样钉住引擎读数不变。本轮**无需补断言**。
- gated 信封同时作为 `role=tool` 消息回灌对话上下文（`llm_worker.cpp:262-266`），故 LLM 下一轮**看得到**"手动模式：未执行 X"，可据此向操作员解释——不是把错误吞掉。

## 2. UI / 日志呈现（桌面 Agent 通道）

### 2.1 对话内联标注（有可见"未执行"提示，非静默丢弃）

- 连接点：`main_window.cpp:2888` `connect(agent_, &ai::Agent::toolCalled, ...)`。
- 判定与渲染：`:2894-2901` 解析结果 JSON；
  - `gated:true` → 追加 `aiToolNotes_.append("[已拦截·手动模式: <tool>]")`（`:2898`）；
  - 否则 → `[调用工具: <tool> — <result>]`（`:2900`，保持原措辞）。
- 渲染：`aiRenderChat()` `:6341-6342` 把 `aiToolNotes_` 逐行 append 进聊天框。
- 测试钉住：`test_ui_integration.cpp:138` 断言 chat 含 `[已拦截·手动模式: tune_frequency]`，`:145` 断言已执行工具保留 `[调用工具: get_status`——**gated 与 executed 在 UI 上肉眼可区分**。本轮 20/20 PASS。
- 代码注释 `:2889-2893` 明示设计意图："Annotate it RESTRAINED (a quiet inline note, not a loud sticker); an executed tool keeps the original wording."

### 2.2 自主任务路径（独立 gated 状态 + 报告行）

- 单一引擎执行点：`task_orchestrator.cpp:153` `LLMWorker::dispatchToolCall(st.tool, resolved, engine_, manualMode_, ...)`，注释 `:149-152` 明示"THE single engine execution point … including the bookmark step … flows through this one dispatch point"——**add_bookmark 也走同一 gate**，无旁路。
- gated 步骤分类：`:160-165` 检测 `gated:true` → `r.state = StepState::Gated`、`r.gated = true`、`r.error="手动模式拦截：未执行 <tool>"`（区别于 `Failed`）。
- 终态报告：`:188-189` 若 `gateCount>0` 追加一行"N 步被手动模式拦截，未真正动作"。
- UI 渲染：`task_steps_view.cpp:21` `StepState::Gated` → 文本"已手动拦截"，`:31` 配色 `tokens::kWarning`（警示色，非绿非红）。

### 2.3 活动日志（按设计不记 agent 动作）

- `activityLog_` 仅在 `main_window.cpp:2806-2818` 自动落 `scan_band` 命中（信号活动），**不**记录 AI 工具调用。被 gate 拦的工具调用因此不进活动日志——但它们已在对话面板内联呈现（§2.1）+ 任务视图呈现（§2.2）。属设计分工（活动日志 = 信号事件，≠ agent 动作流），非呈现缺口。

## 3. CH / HTTP 通道的拦截呈现（客户端侧）

- **HTTP 响应体即呈现**：`POST /command` 被拦时，服务器原样返回 CH 的 `{ok:false,gated:true,error}`（`control_http_server.cpp:331` 透传，注释 `:329-330` "we just relay them"）。无 per-route handler、无 HTTP 层二次判定。客户端拿到的 JSON 本身就是诚实信封。
- **mobile ControlHubClient = 纯只读查看器**：`mobile/lib/services/control_hub_client.dart` 仅 GET `/status` `/pocsag_messages` `/m17_calls` `/vor_radial`（`:93-120`），**无任何 POST /command 路径**。mobile 根本不会经 HTTP 触发写、也就不会经 HTTP 收到 gated 响应——架构上不可能"静默丢弃"一个它从不发起的写。
- **mobile 端上 AI 工具的 gated 呈现**（与桌面 HTTP 无关，是 mobile 本地 agent）：
  - `_gated` 信封 `ai_tools.dart:35-40` = `{ok:false,gated:true,error:"手动模式：未执行 X", hint:"当前为手动模式，AI 只对话不动作…"}`（多一个用户可读 `hint`）。
  - 渲染：`chat_page.dart:852` 把工具调用包成 `TaskStep` 交给 `TaskProgressView`。`task_step.dart:79-86` `isFailureResult` 用正则 `"ok"\s*:\s*false` 命中 gated 结果 → 归入 `TaskStepStatus.failed`（红叉 `task_progress.dart:139-140`）；`summarizeResult` `:89-99` 提取 `error` 字段作为红字摘要"手动模式：未执行 X"。
  - **判定：非假成功。** mobile 把 gated 画成红叉失败态 + 真实原因文案，操作员不会误以为"已调谐"。

## 4. 半执行检查（无"先执行后拦截 / 部分参数落地"）

| 通道 | gate 判定点 | 相对参数解析 / 引擎触碰的位置 | 证据 |
|---|---|---|---|
| Agent（对话） | `llm_worker.cpp:119` | 在 `executeTool` `:122` **之前**直接 return；注释 `:116-118` "Skip executeTool entirely (no engine touch)" | `:119-122` |
| Agent（自主任务） | `task_orchestrator.cpp:153` | 复用同一 `dispatchToolCall`；参数 `resolveArgs` `:135` 只本地解析、不碰引擎 | `:135,:153` |
| ControlHub | `control_hub.cpp:360` | 在 `dispatch()` `:385` / handler `:400` **之前**；handler 内部才做 `needDbl/needBool` 参数校验（如 `cmdTune:410`） | execute() 顺序 未知命令`:345`→无引擎`:353`→门`:360`→dispatch`:385` |
| HTTP | 无独立判定 | 直接委托 `hub_->execute()`（`:331`），gate 由 CH 在 dispatch 前完成 | `control_http_server.cpp:329-331` |

- 补充：`test_agent.cpp:695-697` 注释明示"gate fires before validation, so these also demonstrate 'gated' precedes arg checking"——gated 命令连"参数是否合法"都不走到引擎侧，零参数落地。
- 全 `cpp/src` grep `writeEnabled_.load()/row->write` gate 谓词仅命中 CH `:360` 一处（+ 只读 getter），43 个写 handler 体内无第二处 gate、也无"先动引擎再判门"路径（沿用上轮 `gate-semantics-audit §2.1` 结论）。

## 5. 差异判定清单（零"该修"，2 条"架构性不修/观察"）

**核心后置行为诚实（拦截当下有可见提示、终态引擎零漂移）三通道成立，零待修。** 逐条：

1. **【架构性不修 / 观察】`[已拦截]` 是 turn 内瞬态注记，终态清除后"未执行"的留存依赖 LLM 转述。**
   - 位置：`aiToolNotes_` 在 `aiOnResponseReady` `main_window.cpp:6377` 随最终回复到达而 `clear()`。即拦截当下操作员看到 `[已拦截·手动模式: X]`，但该注记**不持久化**；持久会话里只剩 LLM 的最终文本。
   - 为何不留存：gated 工具结果已作为 `role=tool` 消息进上下文（`llm_worker.cpp:262-266`），LLM 下一轮理应在最终回复里说"我没有执行 X，因为当前是手动模式"。代码注释 `:2889-2893` 明示瞬态设计。
   - 判定：**不修。** 这是聊天 UX 的固有形态（工具注记瞬态、最终文本是持久记录），不是静默丢弃——turn 进行中操作员看得见，且 LLM 有诚实数据可转述。测试只钉了 turn 中存在性（`test_ui_integration:138`），未钉终态留存，与该设计一致。**残留软依赖**：若 LLM 行为异常、最终回复漏掉拦截说明，持久会话会丢这条痕迹——但这属于模型输出质量，非 gate 代码缺陷，且 UI 已在拦截当下如实标注。
2. **【观察】mobile 把 gated 视觉归入 failed（红）vs 桌面独立 gated（警示色）。**
   - 桌面：`StepState::Gated` 独立态、警示色"已手动拦截"（`task_steps_view.cpp:21,31`）。
   - mobile：gated 无独立枚举，并入 `failed` 红叉（`task_step.dart:7-8`、`task_progress.dart:54-58`），但红字摘要明确"手动模式：未执行 X"。
   - 判定：**不修。** 两侧都不显示假成功、都带真实原因文案；仅状态粒度不同（桌面区分"拦截"与"失败"，mobile 合并）。mobile 是 10 工具小面，合并未造成误导。要统一需给 mobile `TaskStepStatus` 加 `gated` 枚举 + 配色，收益低、本轮只读不做。

> 其余对照（信封三元组、命名漂移 `set_vfo_frequency↔vfo_set_freq`、门序观察）均为前轮已登记项，本轮不重复展开，无新增缺陷。

## 6. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `ghp_`（GitHub token 泄露） | `cpp/src/**`、`mobile/lib/**` | **0 真实 token**（仅 docs 把该词当红线关键词自述命中） |
| `competition` / `比赛` / `赛事` | `cpp/src/ai/**`、`cpp/src/control/**` | **0 命中** |

## 7. 诚实未完成项与边界

- **我自己引入并纠正的环境污染（如实记）**：首跑误设 `MBDSDR_TEST_SOURCE=1`，致 `manualMode_gateSpotCheckAllWrites` `:763` 假失败（freqBefore=98.5MHz、after=0）。`unset` 后 30/30 全绿。该 env 属于 `test_ui_integration` 的源注入专用，不该用于 test_agent；本审计结论基于 clean env 复跑。
- **二进制 ↔ 源码一致性**：`cpp/build/test_agent` mtime 2026-10-09 01:31，**新于** `cpp/src/ai/*.cpp`（2026-10-08 16:xx），即二进制由当前源码编译；金集 91/91 PASS 佐证行为一致。本审计**唯一新增文件**为本 md；未改任何源码/测试、未 stage/commit。
- **观察到他会话在途改动（已按纪律不触碰）**：审计收尾时工作树出现一批**非我所改**的已跟踪修改——`cpp/src/core/tokens.h`、`cpp/src/ui/main_window.*`、`cpp/tests/test_s_meter.cpp`、`cpp/tests/ui_screenshot_narrow.cpp`、`cpp/CMakeLists.txt`，以及未跟踪新文件 `cpp/src/ui/rssi_trend.*`（一个 RSSI 趋势特性）。我未编辑、未 revert、未 stage、未 commit 任何一个，原样留待其所属会话收尾。这些改动集中在状态条 RSSI 部件（`main_window.cpp:327,2172-2198,3575` 等），与本审计的 gate 后置路径（`toolCalled` 标注 `:2888-2900`、`aiToolNotes_` 生命周期 `:6341,6377`）**逻辑无关**，仅使这些行号相对我初读时下移约 5-10 行（本报告已按收尾时快照行号校正）；gate 三通道文件 `ai/llm_worker.cpp`、`ai/agent_tools.cpp`、`control/control_hub.cpp`、`control/control_http_server.cpp`、`ai/task_orchestrator.cpp` 均不在被改清单内。
- **HTTP 66 命令自动覆盖为架构断言**：统一委托 `:331`、无 per-route handler，测试只钉代表路由（tune / noise_blanker），未枚举全 66 条——与前轮口径一致。
- **UI 终态留存未钉测试**：仅钉 turn 中 `[已拦截]` 存在性（§5.1 观察 1）；这是设计使然，非本轮要修的缺口。
- 未触碰 `cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp` 任何隔离文件；未执行任何 `git add/commit/push`。
