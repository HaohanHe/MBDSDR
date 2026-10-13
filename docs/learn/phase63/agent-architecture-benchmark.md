# Agent 架构侦察 + 开源通用 Agent 对照（Claude Agent SDK / OpenHands / CrewAI）

**日期**: 2026-10-13
**范围**: 只读侦察本仓库 agent 层真实能力（file:line 证据），再联网对照 3 个开源通用 Agent，产出对照表 + 真差距 + 取舍。
**红线**: 本文档不预置场景/呼号/TLE；零 LLM 依赖的 headless 工具面（86 工具 / MCP 10 域）保持不变。

> 姊妹笔记：本地记忆族设计见 [`agent-memory-notes.md`](./agent-memory-notes.md)；
> headless 协议与第三方客户端实证见 [`third-party-agent-mcp.md`](./third-party-agent-mcp.md) /
> [`third-party-mcp-client.md`](./third-party-mcp-client.md)。本文只做「架构位面对照」，不重复上述设计理由。

---

## 0. 一句话结论

MBDSDR 是一个**单 agent、零内建模型、工具面被外部通用 MCP 客户端驱动**的 SDR 控制内核：
它在「**上下文预算管理**」这一项上已对齐甚至略优于多数开源参考实现；
在「**跨会话记忆的自动注入与整理**」上有骨架（SQLite FTS5 + 后台单写者）但缺「读回注入」与「删/整理」；
在「**多 agent 下派 / 子 agent 生命周期**」上是**有意的空白**——我们靠一个确定性任务编排器（3 模板）+ 单一执行点覆盖「多步任务」，而不是靠派生子 agent。这是设计选择，不是缺陷，但要在文档里讲清楚取舍。

---

## 1. 「我们有什么」——agent 层真实能力清单（file:line 证据）

### 1.1 执行模型：单 agent 单 tool-loop，一个执行点

- **单聊天窗 agent**：`Agent`（`cpp/src/ai/agent.h:19-75`）持有一份 `history_`（`QList<ChatMessage>`），
  把消息转发给唯一的 `LLMWorker`。**没有**任何 sub-agent / delegate / spawn / handoff 概念
  （`grep -rni "subagent|delegate|spawn_agent|handoff|child_agent" cpp/src/ai` 为空）。
- **tool-loop 在 worker 线程**：`LLMWorker::doChat`（`cpp/src/ai/llm_worker.cpp:134-272`）是唯一的函数调用状态机；
  看门狗 `tokens::kAiMaxToolRounds` 限制最大 assistant 轮次（`.cpp:183`）；
  每个 tool call **先过 schema 校验再碰硬件**，校验失败把 `errorJson` 以 `role=tool` 回注自纠（`.cpp:248-258`）。
- **单一引擎执行点**：一切工具（LLM 直调 + 任务编排步骤）都汇到
  `LLMWorker::dispatchToolCall`（`llm_worker.cpp:113-124`）→ `executeTool`。
  **没有第二条执行路径**（`task_orchestrator.h:5-10` 自述）。
- **写门控**：桌面端 `manualMode` 全局 toggle（`agent.h:40-41`，`QSettings("MBDSDR","MBDSDR")` 持久化），
  手动模式下写工具在 `dispatchToolCall` 直接返回 gated 结果、**不碰 engine**（`llm_worker.cpp:120-122`）；
  headless MCP server 另有每调用 `arguments.arm_manual == true` 才真执行（`cpp/src/agentapi/mcp_server.cpp:276-279`）。

### 1.2 多步任务：确定性编排器（非 LLM 自由 plan）

- `TaskOrchestrator::run`（`cpp/src/ai/task_orchestrator.cpp:116-203`）跑一条**确定性步骤序列**，
  每步走与 LLM 完全相同的 `dispatchToolCall` 执行点（`.cpp:153`）。
- **跨步引用用 JSON-path，不用模板变量**：`resolveRef`（`.h:93-95` / `.cpp:24-101`），
  形如 `{"fromStep":0,"path":"hits[0].frequencyHz"}`——真实结果回接，无 `{{var}}` 幻觉空间。
- **3 个内置模板**：`planSweepFindAndRecord`（6 步）/ `planTargetCapture`（4 步）/ `planFixedFrequencyRecord`（5 步）
  （`.cpp:207-313`）；元数据目录 `taskTemplateCatalog()`（`.cpp:315-331`）与工具 `list_templates`/`run_template`
  （`tool_schema.cpp:1675-1732`）共用同一份 key，永不漂移。
- **异步生产执行**：`TaskRunner`（`task_runner.h:29-61`）把 plan 搬到私有 `QThread`，
  经 queued signal 把 `stepUpdated(StepResult)`/`finished(QString)` 投回 UI 线程；
  `requestStop()` 只碰 orchestrator 的原子 stop flag（`.h:9-11`），无线程数据竞争。
- **任务结果留痕 = 仅内存 + UI 信号**：`StepResult` 列表 `results_` 是 orchestrator 实例内的 `QList`
  （`.h:103`），每次 `run()` 开头 `results_.clear()`（`.cpp:117`），经 `StepCallback`/`stepUpdated`
  实时喂 UI（`.cpp:142,179`）。**不落盘、无「查询历史任务」工具**——进程结束 / 下次 run 即清空。

### 1.3 跨会话记忆族（SQLite FTS5 + 后台单写者）

- 工具面：`memory_add` / `memory_search` / `memory_update` / `checkpoint_status` / `memory_flush`
  （schema 见 `tool_schema.cpp:1476-1555`）。
- 存储：系统 SQLite C API 的 FTS5 虚表，`(scope, scope_id, key, content)`，
  `~/.mbdsdr/memory.db`（`memory_store.h:26-65`）；中文逐字分词（详见 `agent-memory-notes.md`）。
- 作用域三分：`user`（长期偏好）/ `agent`（经验踩坑）/ `session`（本会话现场）+ `scope_id` 实例标识（`tool_schema.cpp:1480`）。
- **后台单写者、无 cron**：`CheckpointWriter`（`checkpoint_writer.h:37-92`）内存队列 + 后台线程约 150ms drain；
  `submit()` 非阻塞入队，主 agent 不等落盘；`memory_flush` 同步排空并重写 `~/.mbdsdr/checkpoint.md` 人类可读快照。
- **记忆是 pull 式**：只有 agent 主动调 `memory_search` 才读回；**没有**会话开场自动把 durable user/agent 事实注入 system prompt。
- **缺删/整理**：memory_store 只有 `add/update/search`（`memory_store.h:45-57`），**无 delete、无 list、无 TTL/过期**。

### 1.4 上下文预算管理（我们做得最扎实的一项）

`compactContext`（`cpp/src/ai/ai_context.cpp:76-155`）是分层的：

1. **system prompt 隔离**：开场把首条 system 消息拆出（`llm_worker.cpp:153-155`），压缩时永远单独保留、重新置顶（`ai_context.cpp:98`）。
2. **tool 输出轻裁剪先行**：`truncateLargeToolOutputs`（`ai_context.cpp:36-51`）只动 `role=tool` 消息，超 `kAiToolOutputMaxChars` 保留头尾预览。
3. **超预算才触发摘要**：预算 = 上下文窗 × `kAiContextBudgetRatio`（`.cpp:82-89`）；超预算则保留最近 `keepRecentRounds` 个 user 轮 verbatim，
   更早的轮折成单条 `role=summary` 消息（`.cpp:106-149`）。
4. **摘要双通道**：有 key 时让 LLM 真摘要（`llm_worker.cpp:160-169`）；无 key 时退回**规则计数摘要，且逐条引用每个 user ask**（`ai_context.cpp:58-74`，照 OpenAI SDK「不把 user 消息压掉」的口径）。
5. token 估计是 CJK 感知的（1 汉字 ≈ 1 token，~4 拉丁字符/token，`.cpp:11-29`）。

---

## 2. 三个开源通用 Agent 的核心模式（一手来源）

### 2.1 Claude Agent SDK —— 多 agent 下派的标准答案
来源：<https://code.claude.com/docs/en/agent-sdk/subagents>

- **子 agent = 独立实例**，主 agent 经 `Agent` 工具派生子任务；默认后台跑，需要结果时 `run_in_background:false` 同步等。
- **上下文隔离**：每个子 agent 起一条**全新对话**（除非 `fork`）；中间 tool 调用与结果**留在子 agent 内**，
  **只有最终消息回给父 agent**——父收到的是一段精简报告，不是它读过的每个文件。
- **继承白名单**：子 agent 只拿到自己的 system prompt + Agent 工具 prompt + 项目 `CLAUDE.md` + 工具子集；
  **拿不到父对话历史 / 父 system prompt**（文档对比表）。
- **并行 + 专用 + 限权**：可并发跑多个子 agent（最慢者即总时长）；每个可配专属 system prompt；可限制只给 `Read/Grep` 等只读工具。
- **生命周期可恢复**：子 agent transcript 独立成文件、持久；`Agent` 工具结果带 `agentId`，可用 `resume: sessionId` 续接保留完整历史。
- **配额硬顶**：嵌套深度（默认 3）/ 并发数（默认 20）/ 花费 `maxBudgetUsd` 三重上限。

### 2.2 OpenHands —— 两层持久记忆 + 上下文压缩器
来源：持久记忆 <https://docs.openhands.dev/sdk/guides/persistent-memory>；压缩器 <https://docs.openhands.dev/sdk/guides/context-condenser>

- **两层记忆**：user 层 `~/.openhands/memory/`（跨项目）+ project 层 `<workspace>/.openhands/memory/`（本仓库）。
  每层 = `MEMORY.md`（durable 事实的**精炼索引，唯一被注入 prompt 的文件**）+ 按日日志 `YYYY-MM-DD.md`（**不自动注入**，agent 用文件工具按需读）。
- **注入方式**：每会话开场把两层 `MEMORY.md` 索引读回 system prompt 的 `<MEMORY_CONTEXT>` 块（**user 层在前**）。
  合并预算 ~6000 字符，超预算从各层**顶部整行丢弃**（最老的先丢），保留层头与截断提示。
- **不可信即声明**：注入块包 `<UNTRUSTED_CONTENT>`——记忆文件可能被任何人改/提交（含 prompt 注入），
  agent 被告知「当作未验证线索，不当权威指令」。
- **记忆不进对话持久化**：解析出的记忆文本每会话重读自磁盘，**排除在 `base_state.json` / API payload 之外**。
- **agent 自维护**：system prompt 教它在任务末尾把 durable 事实写进 `MEMORY.md`、合并重复、剪枝过期、不记密钥、不记「随手可重查」的事实。
- **上下文压缩器**：`LLMSummarizingCondenser`——历史超 `max_size` 触发，**保留最近消息 + 保留首 `keep_first` 条（system/初始 user）**，
  把更早的摘要成 LLM 生成的小结；官方称每轮 API 成本最高降 2 倍。

### 2.3 CrewAI —— 统一记忆类 + 复合打分 + 去重巩固
来源：<https://docs.crewai.com/v1.14.1/en/concepts/memory>

- **单一 Memory 类**取代 short/long/entity/external 四类；保存时用 LLM 推断 scope/categories/**importance**。
- **复合召回打分**：`composite = w·semantic + recency·0.5^(age_days/half_life) + importance`；
  可按场景调权（冲刺复盘偏新、架构库偏重要）。
- **巩固（consolidation）**：新记录与存量相似度 > 0.85 时，LLM 判定 keep / **update 合并** / **delete 淘汰** / insert_new——**自动去重与过期淘汰**。
- **非阻塞写 + 读屏障**：`remember_many()` 后台线程异步落库；但**每次 `recall()` 前自动 `drain_writes()`**，保证检索看到最新记录（透明 read barrier）。
- **来源 / 隐私**：每条记忆可带 `source` 标签与 `private` 标记，按来源可见。
- **代价前提**：需要一个 embedding 模型做向量检索 + 一个 LLM 做保存分析/巩固/深召回。

---

## 3. 对照表：他们的三支柱 vs 我们现状

| 维度 | Claude Agent SDK | OpenHands | CrewAI | **MBDSDR 现状** |
|---|---|---|---|---|
| **多 agent 下派** | 子 agent 独立实例/新对话，仅最终报告回父，可并行/限权/fork/resume，深度·并发·花费三硬顶 | 单 agent + Conversation 事件流（可外挂多 agent） | Crew 内多 agent 角色协作，任务串行/流传递 | **无**。单 tool-loop；多步任务由确定性 `TaskOrchestrator`+`TaskRunner` 承担，单一执行点 |
| **上下文隔离（不污染主窗）** | 子 agent 中间 tool 结果全留子内 | 记忆文本不进对话持久化、不进 payload | 每个 agent 自带独立记忆 | **部分**：tool 输出头尾裁剪 + 预算触发摘要，但**都在同一条 history 内压缩**，无子隔离 |
| **记忆持久化** | （靠 transcript 文件 + resume） | 两层 Markdown（user/project），`MEMORY.md` 索引 | LanceDB 向量库 + LLM 分析 | SQLite FTS5（~/.mbdsdr/memory.db），scope 三分 |
| **记忆检索** | transcript 续读 | 按需读日志文件；索引自动注入 | 向量语义 + 时效衰减 + 重要度复合打分 | FTS5 全文（中文逐字分词），scope/scope_id 过滤；**无向量/无重要度** |
| **记忆自动整理** | — | agent 自维护 MEMORY.md（合并/剪枝/预算顶丢弃） | 巩固：相似度>0.85 触发 LLM keep/update/**delete**/insert | **无 delete / 无整理 / 无过期**；只有 add/update/search + 后台入队 |
| **记忆注入主上下文** | 子 agent prompt 显式带入 | **开场自动注入 `<MEMORY_CONTEXT>` 块**（~6000 字符预算，UNTRUSTED 包裹） | recall 按需召回 | **pull 式**：agent 须主动调 `memory_search`；无开场自动注入 |
| **上下文预算/压缩** | 子 transcript 各自压缩 | `LLMSummarizingCondenser`：超 max_size 摘要旧消息、保 recent + keep_first | （靠记忆库外挂） | **已对齐**：system 隔离 + tool 头尾裁剪 + 超窗摘要保最近 N 轮 + user ask 全保留 + CJK 感知 token 估计 |
| **写安全门控** | 子 agent 限工具集 | — | — | 双门控：桌面 manual mode（QSettings 持久化）+ headless `arm_manual` 每调用 |
| **诚实空态/不编造** | 子 agent 错误不冒充结果 | 记忆标 UNTRUSTED、best-effort | — | 贯穿：未注入→诚实错误、空态 note、不 mock、失败 chat 不入 history |

---

## 4. 真差距清单 + 每项取舍

> 判定标准：对照一手来源后**真实存在**、且对 SDR 控制内核有意义的差距。体量估计按「新工具需过三通道（tool_schema + agent_tools 执行器 + control_hub headless）+ MCP 域钉扎 + 金集计数同步 + 单测」= 一个完整工具面单位估。

### G1. 任务执行结果不留痕、不可查询 （现状确认：`results_` 仅内存，`run()` 即 clear）
- **差距**：对照 Claude「子 agent transcript 独立持久、可 resume 回读」与 OpenHands「conversation state 可序列化恢复」，
  我们跑完一次 `sweep_find_record` 后，6 步每步真实结果只活在 UI 信号里，进程一关或下次 run 就没了；agent 无法回查「上周那次扫频命中了什么」。
- **取舍：延后**。要端到端落地需新增一个只读查询工具（如 `task_history`）+ 落盘 store + 三通道 + 金集 86→87 同步 + Python MCP 钉扎 + 单测，
  **估 0.5–1 天**，非 <1h。且本批与一个只读复核子 agent 并行、不应在此时动金集计数。
- **触发条件**：当用户开始反复问「上次那个扫频结果呢」时再做。

### G2. 记忆缺 delete / 整理 / 过期 （对照 CrewAI consolidate + OpenHands 剪枝）
- **差距**：CrewAI 保存时自动判 delete 淘汰过期项；OpenHands 超预算从最老整行丢弃。我们只增不删，`memory.db` 会单调膨胀，且 session 作用域的临时现场永不退役。
- **取舍：延后（次优先）**。`memory_delete`（按 scope+key，或按 scope 清空 session）是一个**完整工具**：
  `memory_store.delete` + schema + 执行器 + control_hub + MCP 域 + 金集 86→87 + `test_memory_store` 增补，**估 3–4h**，非 <1h。
- **建议做法**：落地时把 `memory_delete` 与 G1 一起排（同为「记忆/任务生命周期」主题，一次动金集更划算）。

### G3. 记忆不自动注入 system prompt （对照 OpenHands `<MEMORY_CONTEXT>` 开场注入）
- **差距**：OpenHands 每会话开场自动把两层 `MEMORY.md` 索引注入 system prompt；我们是纯 pull——agent 想不起来调 `memory_search` 就等于没记忆。
  这是「记忆库存在但不被利用」的最大结构性浪费。
- **取舍：延后，但价值最高**。落地需在 system prompt 组装点（`Agent`/`ai_context`）开场读回 `user`+`agent` 两层索引并注入，
  且要照 OpenHands 的**不可信包裹 + 大小预算 + 不记密钥**纪律；**估 4–6h**（含注入层单测 + 与既有 compactContext 叠加验证）。
- **红线提示**：注入内容须包「未验证」提示，防止记忆文件被改后变成 prompt 注入面；且 session 作用域**不应**跨会话注入。

### G4. 无向量语义检索，纯 FTS5 关键词 （对照 CrewAI 复合打分 / OpenHands 按需文件读）
- **差距**：CrewAI 用语义相似度 + 时效衰减 + 重要度复合召回。我们 FTS5 是词元级命中，「天线高度」搜「我把天线架多高」这类同义改写miss。
- **取舍：不做（当前阶段）**。引入 embedding = 引入一个新模型/本地推理依赖，**直接违反本项目「零内建模型、headless 第三方 client 可驱动」的立身红线**。
  FTS5 中文逐字分词对 SDR 领域短事实（频率/偏置/台站号）已够用；语义检索留到「未来真有跨库模糊检索需求」再议。

### G5. 无多 agent / 子 agent 下派
- **差距**：对照 Claude Agent SDK 子 agent 体系，我们完全没有「派一个隔离上下文的子任务、只收最终报告」的能力。
- **取舍：刻意不做（设计选择，非欠账）**。理由：
  1. 本内核的定位是**把 SDR 能力暴露成工具面**，多 agent 编排是**调用方（外部通用 MCP 客户端 / 用户自己的 LLM）的事**——
     我们已实证第三方通用 client 零 key 即可驱动（`third-party-mcp-client.md`）。
  2. 真实 SDR 硬件是**单接收机、单本振**的强共享资源，多 agent 并行驱动会互相抢硬件；
     我们的 `TaskRunner` 已用「私有 QThread + 原子 stop + 单次只跑一个 plan」（`task_runner.h:44-48` 防重叠）约束并发，方向正确。
  3. 真要「隔离上下文跑子分析」，正确形态是**让外部 LLM 用我们的只读工具做并行分析**，而非内核内建派生 agent。
- **唯一可考虑的小步**：若未来出现「后台长扫描 + 主对话不被 tool 输出淹没」的诉求，可复用 G3 的注入纪律做「结果摘要回注」，但仍不内建子 agent。

---

## 5. 与本项目立身红线的对照（为什么多数差距刻意不追）

- **零内建 LLM 依赖**：G4（向量需 embedding 模型）、CrewAI 式「保存时 LLM 判重要度/巩固」都需要内嵌模型——**与红线冲突，不追**。
  我们的 FTS5 + 规则摘要正是在「无 key 也能跑」前提下的对齐方案。
- **单硬件资源**：G5 多 agent 并行驱动接收机 = 抢本振/抢音频，**不追**；用确定性单 plan + 防重叠调度代替。
- **诚实空态**：OpenHands 的 UNTRUSTED 记忆注入纪律值得抄（若 G3 落地），但前提是先有自动注入。

---

## 6. 本次落地结论（诚实交代）

- **未动任何代码**。侦察确认的两个最小真实缺口（G1 任务结果留痕查询、G2 `memory_delete`）都属于「需新增工具 → 三通道 + 金集 86→87 同步 + 单测」，
  各自估 3–4h 以上，**超出本批 <1h 体量**；且本批与一个只读复核子 agent 并行，不应此时动金集计数。按「宁缺毋滥 / 超体量登记延后」纪律，**一律登记延后，不硬做**。
- **最值得排队的一项是 G3（记忆开场自动注入）**——它让已建成的 memory.db 真正被利用，价值最高；建议与 G1/G2 排在同一「记忆/任务生命周期」里程碑，一次动金集。
- 未完成项：无代码改动、无金集变更、无测试改动；纯只读侦察 + 对照文档。

---

### 参考来源（一手）
- Claude Agent SDK Subagents: <https://code.claude.com/docs/en/agent-sdk/subagents>
- OpenHands Persistent Memory: <https://docs.openhands.dev/sdk/guides/persistent-memory>
- OpenHands Context Condenser: <https://docs.openhands.dev/sdk/guides/context-condenser>
- CrewAI Memory: <https://docs.crewai.com/v1.14.1/en/concepts/memory>
