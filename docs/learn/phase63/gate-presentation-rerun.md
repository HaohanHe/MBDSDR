# Gate 拦截后呈现实测复核（UI 渲染快照 + 金集复跑）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），复核基线 HEAD = `abf5cc4`（main）。
- 交付方式：**只读复核落档**。新增本 md + 一张实测快照 `gated-steps-rerun.png`；未改任何 `cpp/src/**`、未改任何既有测试、未 `git add/commit/push`。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，全程 CLI/offscreen，未操控任何 GUI；金集一律 **clean env（不设 `MBDSDR_TEST_SOURCE`）**。
- 上轮基线：`gate-post-action-audit.md`（HEAD `eb3a1f0`，四通道诚实结论 + 金集 91/91）。该轮是**源码审计 + 断言存在性**，缺 UI 渲染实测。本轮补：**真实 offscreen 渲染一个 Gated 步骤并 Read 快照核查**，同时按现行源码逐项复核既有结论。

## 0. 结论摘要

- **Gated 警示态 UI 渲染实测诚实，无假成功。** 用一次性 offscreen 驱动（真实 `SpectrumEngine` + 真实 `TaskOrchestrator` 手动模式 + 真实 `ui::TaskStepsView`，不 mock）跑 `planTargetCapture`：4 个写步骤全部 `state=StepState::Gated(4)`、`gated=true`、`elapsedMs=0`、引擎零调谐。快照 Read 核查：每步圆点为**警示琥珀 `#e0b35a`（kWarning）**，标题带 **`[已手动拦截]`**，摘要行同为琥珀色且原样透出 `{"error":"手动模式：未执行 <tool>","gated":true,"ok":false}`；**无一处绿色「成功」、无一处红色「失败」**；底部报告行如实「完成 0/4 步 / 4 步被手动模式拦截，未真正动作」。见 `gated-steps-rerun.png`。
- **会话拦截标注实测被既有测试钉住。** `test_ui_integration::aiManualToggleWiresAgentAndAnnotatesGated` 在 offscreen 下构造**真实 MainWindow**、经真实 `Agent::toolCalled` 连接投递真实 gated JSON，断言真实 `QPlainTextEdit("aiChat")` 文本含 `[已拦截·手动模式: tune_frequency]`，并断言已执行工具保留 `[调用工具: get_status`——gated 与 executed 在真实聊天控件里肉眼可分。本轮 PASS。
- **既有档四通道诚实结论逐项复核仍成立**（行号仅 `main_window.cpp` 的 `aiToolNotes_` 路径下移约 7 行，gate 三通道判定点行号全部原地未动）；半执行零路径复核成立。
- **差异判定：零"该修" → 零待修。** 既有 2 条架构性观察依旧（§5），本轮新增 1 条良性观察（§5.3），均不修。
- 金集复跑：**92 passed / 0 failed**（test_agent 31、control_hub 27、control_http 14、ui_integration 20；另 test_task_orchestrator 9/0 佐证 Gated 分类）。
- 红线扫描：**CLEAN**（`ghp_` 0 真实 token；竞技措辞关键词在 `cpp/src/ai`、`cpp/src/control` 0 命中）。

### 0.1 金集实跑（本次独立执行，clean env 真实计数）

| 金集测试 | passed | failed | 关键后置行为槽（本轮实跑） |
|---|---|---|---|
| `test_agent` | **31** | **0** | `manualMode_gateSpotCheckAllWrites` PASS（写全 gated + 后端字段 byte 不变）、`noiseBlankerLandReadbackAndGate`、`testManualModeGatesWriteTool`、`testManualModeAllowsReadTool`、`vfoEditToolsBadArgsAndGate` 全 PASS |
| `test_control_hub` | **27** | **0** | `readAlwaysAllowedWriteGateBothStates`、`phase26WritesAreGatedAndBadArgsHonest`、`exportIqSegment_gatedWhenWriteGateClosed` PASS |
| `test_control_http` | **14** | **0** | `gateClosedRefusesWritePost`（tune 拦 + 频率不变）、`postCommandNoiseBlankerRoute`（引擎不动）PASS |
| `test_ui_integration` | **20** | **0** | `aiManualToggleWiresAgentAndAnnotatesGated` PASS（chat 含 `[已拦截·手动模式: tune_frequency]`，已执行工具保留 `[调用工具: ...]`） |
| **合计** | **92** | **0** | — |

> 附带佐证：`test_task_orchestrator::testManualGateHonestReport` **9/0 PASS**，报告文本「完成 0/4 步 / 4 步被手动模式拦截，未真正动作」与本驱动实跑输出逐字一致。
>
> 计数对比：上轮 91/91，本轮 92/0。`test_agent` 由 30→31（HEAD 相对上轮基线 `eb3a1f0` 期间新增 1 个槽；gate 相关槽全部在列且 PASS）。运行期告警仅 `no librtlsdr ops bound; stub start()`（offscreen 无硬件 QINFO）与 HTTP 回环横幅（QWARN），均非 FAIL。
>
> 本轮**未设** `MBDSDR_TEST_SOURCE`（沿用前轮自我纠正：该 env 只属 `test_ui_integration` 源注入，不用于 agent/hub/http）。

## 1. Gated 警示态 UI 渲染实测（本轮新增，补既有档缺口）

### 1.1 实测方式（真实数据，无 mock）

既有 `ui_screenshot_task_steps.cpp` 快照 harness 跑的是**真实自主任务按钮**，但其 `MBD_OUT`/`MBD_MODE` 两个 env 旋钮里**没有手动模式开关**（手动态来自 `Agent` ctor 读 QSettings `aiManualMode`，harness 用全新 `QTemporaryDir` 故默认关）。按任务口径「harness 无现成通道则不造 mock、退而确认断言覆盖」之外，本轮选择**另写一次性 offscreen 驱动**（`cpp/scratch/gated_render_snapshot.cpp`，未入 CMake、不 commit），它复用**生产组件本身**：

- 真实 `dsp::SpectrumEngine`；
- 真实 `ai::TaskOrchestrator` `setManualMode(true)` 跑 `planTargetCapture(145.8e6,"NFM")`——Gated 记录由真实 `dispatchToolCall` 在 `llm_worker.cpp:119` 产生，不是手搓的假 StepResult；
- 真实 `ui::TaskStepsView`（生产视图）`setRun(results, report)` 后 `grab()` 成 PNG。

### 1.2 运行期真实数据（stdout 实录）

```
STEP tool=tune_frequency   state=4 gated=1 err=手动模式拦截：未执行 tune_frequency
STEP tool=set_mode         state=4 gated=1 err=手动模式拦截：未执行 set_mode
STEP tool=start_recording  state=4 gated=1 err=手动模式拦截：未执行 start_recording
STEP tool=stop_recording   state=4 gated=1 err=手动模式拦截：未执行 stop_recording
REPORT: 任务「目标频率捕获录制」：完成 0/4 步
        4 步被手动模式拦截，未真正动作
```

`state=4` = `StepState::Gated`（枚举序 Pending0/Running1/Succeeded2/Failed3/Gated4/Aborted5）。4/4 步 gated、0 步成功，与 orchestrator 分类（`task_orchestrator.cpp:162-165`）一致。

### 1.3 快照 Read 核查（`gated-steps-rerun.png`，460×860）

| 核查项 | 期望（生产代码语义） | 快照实测 |
|---|---|---|
| 状态圆点颜色 | Gated→`tokens::kWarning`=`#e0b35a` 琥珀（非绿非红，`task_steps_view.cpp:31`） | **琥珀圆点，逐步可见**；无绿色(`#5fd08a`)、无红色(`#e74c3c`) |
| 状态文字 | Gated→「已手动拦截」(`:21`) | 每步标题均含 **`[已手动拦截]`** |
| 摘要行配色 | `r.gated` 时摘要 `color:kWarning`(`:119`) | 摘要行呈琥珀色 |
| 摘要内容诚实 | 透出 gated 信封，不显假成功 | 原样透出 `{"error":"手动模式：未执行 X","gated":true,"ok":false}`（含 `gated":true`、`ok":false`） |
| 耗时 | 未真动作→0ms | 每步 `0 ms` |
| 底部报告行 | gateCount>0 追加拦截行(`task_orchestrator.cpp:188-189`) | 「完成 0/4 步 / 4 步被手动模式拦截，未真正动作」如实可见 |
| 无假成功 | Succeeded 才绿；Gated 不进绿分支 | 全图**无一个「成功」/绿勾** |

**判定：Gated 警示态渲染诚实，无假成功、无半执行视觉暗示。** 这正是上轮缺的"UI 渲染实测"环节，本轮补齐。

## 2. 会话拦截标注实测（桌面 Agent 对话通道）

- 渲染路径（现行行号，相对上轮下移）：`main_window.cpp:2895` `connect(agent_, &Agent::toolCalled, ...)` → `:2905` `gated:true` 时 `aiToolNotes_.append("[已拦截·手动模式: %1]")`，否则 `:2907` `[调用工具: ...]` → `:6348` `aiRenderChat()` 逐行 append 进聊天框 → `:6363` 随最终回复 `clear()`（瞬态注记，设计如此）。
- **实测被钉住（offscreen 真实控件）**：`test_ui_integration.cpp:111 aiManualToggleWiresAgentAndAnnotatesGated` 构造真实 `MainWindow`，`:135-137` 经 `QMetaObject::invokeMethod(agent,"toolCalled",DirectConnection,...)` 投递真实 gated JSON 走**真实连接**，`:138` 断言 `aiChat` 纯文本含 `[已拦截·手动模式: tune_frequency]`，`:145` 断言已执行工具保留 `[调用工具: get_status`。本轮 PASS。
- 结论：**该标注在 offscreen 下可实测，且既有槽已钉住渲染语义**（gated 注记 vs executed 注记在真实聊天控件里可区分）。按任务口径"确认测试钉住即可，不强行截图"，本轮不另截聊天图。

## 3. 既有档结论逐项复核（现行源码）

| 既有档断言 | 现行源码复核 | 结论 |
|---|---|---|
| Agent 判定点 `llm_worker.cpp:119` 先返 `gatedToolResult` 不进 executeTool | `:119 if (manualMode && isWriteTool(name)) return gatedToolResult(name);` 原地 | **成立** |
| Agent 信封 `agent_tools.cpp:24-30` = `{ok:false,gated:true,error:"手动模式：未执行 X"}` | `:24 gatedToolResult()`、`:28 error="手动模式：未执行 %1"` 原地 | **成立** |
| CH 单点门 `control_hub.cpp:360` | `:360 if (row->write && !writeEnabled_.load())` + `:361-362 gatedResult/emit` 原地；execute() 顺序=未知命令`:345`→无引擎`:353`→门`:360`→dispatch`:385+` | **成立** |
| HTTP 无独立门、`:331` 字节透传 | `:331 return hub_->execute(tool,args)`；注释 `:329-330`「we just relay them」原地 | **成立** |
| 自主任务单点执行 `task_orchestrator.cpp:153` | `:153 dispatchToolCall(...)`；`:162-165` gated→`StepState::Gated`/`gated=true`/error；`:188-189` 报告拦截行——与本驱动 stdout 逐字一致 | **成立** |
| TaskStepsView `:21` 文本「已手动拦截」/`:31` 配色 kWarning | 源码同；**本轮快照实测琥珀+文字**（§1.3） | **成立 + 实测补齐** |
| mobile gated 归 failed 红叉 + 红字原因 | `mobile/lib/models/task_step.dart:79-99`：`isFailureResult` 正则 `"ok"\s*:\s*false` 命中 gated→`TaskStepStatus.failed`，`summarizeResult` 提取 error「手动模式：未执行 X」 | **成立** |
| 半执行零路径（门在动引擎/参数解析之前） | Agent `:119` return 早于 executeTool；CH `:360` 早于 dispatch/handler 参数校验；HTTP 纯委托 | **成立** |

- **行号漂移**：仅 `main_window.cpp` 的 `aiToolNotes_` 路径由上轮 `:2898/:6341/:6377` 下移到 `:2905/:6348/:6363`（并行会话 RSSI/状态条改动所致）；gate 三通道判定点（`llm_worker`/`agent_tools`/`control_hub`/`control_http_server`/`task_orchestrator`/`task_steps_view`）行号**原地未动**。
- **金集**：92/0（§0.1），上轮 91/91 结论在新计数下依然全绿。

## 4. 半执行检查（复核）

- Agent 对话路径：`llm_worker.cpp:119` 在 `executeTool` 之前直接 return，引擎零触碰（注释 "Skip executeTool entirely"）。
- Agent 自主任务：`task_orchestrator.cpp:153` 复用同一 `dispatchToolCall`，`resolveArgs` 仅本地解析不碰引擎。
- ControlHub：`control_hub.cpp:360` 在 `dispatch()` `:385` 之前；handler 体内才做参数校验。
- HTTP：`control_http_server.cpp:331` 纯委托，门由 CH 在 dispatch 前完成。
- 结论：gated 命令连参数都不向引擎落地，**无"先执行后拦截/部分参数落地"路径**，与上轮一致。

## 5. 差异判定清单（零"该修"，3 条不修/观察）

**核心后置行为诚实（拦截当下有可见提示、终态引擎零漂移）四通道成立，零待修。**

1. **【架构性不修 / 观察，沿用】** `[已拦截]` 是 turn 内瞬态注记，终态清除后"未执行"的持久留存依赖 LLM 转述。`aiToolNotes_` 在最终回复到达后于 `main_window.cpp:6363` `clear()`。gated 结果已作为 `role=tool` 回灌上下文，LLM 有诚实数据可转述。判定：不修（聊天 UX 固有形态，非静默丢弃）。
2. **【观察，沿用】** mobile 把 gated 视觉归入 failed（红叉）vs 桌面独立 gated（琥珀警示）。两侧都不假成功、都带真实原因文案；仅状态粒度不同。判定：不修。
3. **【本轮新增·良性观察】** TaskStepsView 的 gated 摘要行透出的是**原始紧凑 JSON 信封**（`{"error":...,"gated":true,"ok":false}`），而非一句人话短摘要。原因是 `task_orchestrator.cpp:172` `r.summary = resultText.left(kTaskSummaryMaxChars)` 对 gated/成功统一截断原文。
   - 判定：**不修。** 该信封本身诚实（含 `gated:true`/`ok:false`/error 文案），且被 `:119` 染成琥珀警示色，操作员不会误读为成功；只是机器味偏重。要美化需给 gated 摘要单独取 `r.error` 人话文案，属 UI 润色、非诚实性缺陷，本轮只读不做。

> 其余对照（信封三元组同构、命名漂移对、门序观察）均为前轮已登记项，本轮无新增缺陷。

## 6. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `ghp_`（GitHub token 泄露） | `cpp/src/**`、`mobile/lib/**` | **0 真实 token** |
| 竞技措辞关键词（前轮口径） | `cpp/src/ai/**`、`cpp/src/control/**` | **0 命中** |

## 7. 诚实未完成项与边界

- **二进制↔源码一致性**：本轮先对四个目标做了增量构建（`make test_agent test_control_hub test_control_http test_ui_integration`），构建系统重编了 `test_control_http` 并重链 `test_ui_integration`（即跑前二者相对源码是 stale 的，已补齐），其余 up-to-date；工作树 `git status` 除未跟踪 scratch 外与 HEAD `abf5cc4` 一致。金集 92/0 佐证行为一致。
- **一次性驱动**：`cpp/scratch/gated_render_snapshot.cpp`（及未入版本控制的 `cpp/build/gated_render_snapshot` 二进制）为本轮新增的 offscreen 快照驱动，**未改任何既有源码/测试、未入 CMake、未 commit**；它只复用生产组件，非 mock。快照产物 `gated-steps-rerun.png` 随本 md 落档。
- **观察到他会话在途改动（按纪律未触碰）**：收尾时工作树出现非本任务产物的新增未跟踪档 `docs/learn/phase63/waterfall-ticks-mode-zoom-recon.md`；既有未跟踪 `cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp` 原样未动。本任务未编辑、未 revert、未 stage、未 commit 任何他人文件。
- **会话标注未强行截图**：按任务口径，`[已拦截·手动模式]` 标注由既有 offscreen 真实控件测试槽钉住（§2），本轮不另截聊天图；Gated 任务视图则按要求补了真实快照（§1）。
- **HTTP 66 命令自动覆盖仍为架构断言**：统一委托 `:331`、无 per-route handler，测试只钉代表路由（tune/noise_blanker），未枚举全 66 条——与前轮口径一致。
- 未执行任何 `git add/commit/push`。
