# Phase31 A2：多对话 / 多会话管理学习笔记

> 目标：精读 AutoGen（microsoft/autogen）与 MetaGPT（geekan/MetaGPT）的 session/memory 层，
> 复核我方 `cpp/src/ai/ai_session_store.{h,cpp}` 的设计。只学机制，不抄代码。
> clone：`repos/phase31_autogen`、`repos/phase31_metagpt`（depth=1，main/master 快照）。

## 0. 来源

- AutoGen 仓库：<https://github.com/microsoft/autogen>
  - Studio 后端（多会话持久化的主战场）：`python/packages/autogen-studio/autogenstudio/`
  - agentchat 内存态状态机：`python/packages/autogen-agentchat/src/autogen_agentchat/`
- MetaGPT 仓库：<https://github.com/geekan/MetaGPT>
  - `metagpt/memory/`、`metagpt/environment/base_env.py`、`metagpt/team.py`、`metagpt/exp_pool/manager.py`

---

## 1. AutoGen Studio：Session / Run / Message 三级模型

### 1.1 存储结构（SQLite + SQLModel，关系型而非文件树）

文件：`python/packages/autogen-studio/autogenstudio/datamodel/db.py`

- `BaseDBModel`（db.py:24-48）：所有表公共字段 —— `id`(自增 PK)、`created_at`、`updated_at`（DB 端 `onupdate=now()`）、`user_id`、`version="0.0.1"`。**版本字段内建**，后续 schema 演进靠 Alembic（`database/schema_manager.py`）。
- `Team`（db.py:51-53）：一个可复用的 agent 团队配置，整段 JSON 序列化进 `component` 列。
- `Session`（db.py:70-73）：一次对话线程。字段仅 `team_id`(FK→Team, CASCADE)、`name`，外加继承的 `user_id/created_at/updated_at`。**没有 "current session" 指针列**——谁当前打开哪条会话是前端状态，不落库。
- `Run`（db.py:91-110）：会话内的一次执行（一次用户提问 → 团队跑完）。字段：`session_id`(FK, CASCADE)、`status`(枚举 CREATED/ACTIVE/COMPLETE/ERROR/STOPPED，db.py:83-88)、`task`(原始用户消息 JSON)、`team_result`(最终 TaskResult JSON)、`error_message`、`messages`(冗余 JSON 快照)。
- `Message`（db.py:56-67）：单条消息。`config`(MessageConfig JSON：source/content/type)、`session_id`(FK)、`run_id`(FK→Run, CASCADE)、`message_meta`(可选 JSON)。

层级：`Team 1—* Session 1—* Run 1—* Message`。会话本身只是一个"线程壳"，真正的消息流按 Run 分组——同一 Session 内的多次提问天然分段。

DB 初始化（`database/db_manager.py:63-106`）：建表 + Alembic migration；SQLite 打开 `PRAGMA foreign_keys=ON`（db_manager.py:78）；`_init_lock: threading.Lock()`（db_manager.py:32）做初始化/reset 互斥。连接 `check_same_thread=True`（db_manager.py:43）——SQLAlchemy 引擎在驱动层串行化。

### 1.2 切换 / 恢复机制

- 列表：`web/routes/sessions.py:13-17` `GET /sessions/?user_id=...`，按 `created_at desc` 返回（db_manager.py:230-232 默认 order="desc"）。
- 恢复整条会话：`web/routes/sessions.py:65-119` `GET /sessions/{id}/runs` —— 先校验 session 属于该 user，再 `Run where session_id=? order=asc`，对每个 Run 再 `Message where run_id=? order=asc`。**恢复 = 按 Run 时间正序拼回消息流**，单条 Run 失败不阻塞其他 Run（sessions.py:104-117 把失败 Run 标成 ERROR 仍返回）。
- 运行时状态机：`web/managers/connection.py:85-171` `start_stream()` 把 Run 从 CREATED → ACTIVE（connection.py:110-111）→ COMPLETE/STOPPED/ERROR（connection.py:149/164/342）。
- 进程级 checkpoint：agentchat 侧 `teams/_group_chat/_base_group_chat.py:748-829` 的 `save_state()/load_state()` 把每个 agent + group manager 的内部状态（含 `AssistantAgentState.llm_context["messages"]`，见 `state/_states.py:13-18`、`BaseGroupChatManagerState.message_thread + current_turn`，_states.py:27-32）序列化成嵌套 dict；`load_state` 拒绝在 team running 时调用（_base_group_chat.py:808-809）。Studio 后端本身**不调**这个——它只存消息流，不存 agent 内部寄存器；重开对话时团队从空状态冷启动，靠消息流重建上下文。

### 1.3 持久化时机：每条消息流式落库

`web/managers/connection.py:115-146`：`async for message in team_manager.run_stream(...)` 每产出一条具体类型消息（TextMessage/ToolCallRequestEvent/ToolCallExecutionEvent/…），先 `_send_message` 推 WebSocket，再 `await self._save_message(run_id, message)`（connection.py:143, 173-186）同步 upsert 一行 Message。Run 元状态在 start/finish/error/cancel 四个点各 upsert 一次。**没有批 flush、没有定时器、没有"退出时才保存"**——崩溃丢的是"正在生成中那半条"，不是整段对话。服务关停时 `cleanup()`（connection.py:438-484）把所有 ACTIVE Run 改写为 STOPPED，team_result 塞一条 "Run interrupted by server shutdown"。

### 1.4 隔离与并发

- 多用户：每张表都带 `user_id`（db.py:47），**每个路由查询都强制 `filters={"user_id": user_id}`**（sessions.py:16/23/46/61，runs.py:25）——行级隔离靠查询条件，不是靠分库。
- 多 Run 并发：`WebSocketManager._connections/_cancellation_tokens/_input_responses` 全部以 **run_id** 为键（connection.py:47-51），同一 Session 可同时跑多个 Run，互不串。
- 消息写库时 `user_id=None`（connection.py:184 注释 TODO）——Message 行本身不存用户，靠 run_id→session_id 间接归属。这是一个已知粗糙点。

---

## 2. MetaGPT：Memory 分层，不做"用户会话"

MetaGPT 是"一个项目 = 一个公司 = 一组角色跑一遍"的范式，**没有用户可切换的多会话 UI**。它的"会话"概念拆成三层：

### 2.1 存储结构

- 短期 Memory（`metagpt/memory/memory.py:20-35`）：纯内存 `storage: list[Message]` + `index: defaultdict[cause_by, list[Message]]`。add 时去重（`if message in self.storage: return`，memory.py:31-33）并按 `cause_by` 建反向索引。**不落盘**。
- Environment.history（`metagpt/environment/base_env.py:134`）：`history: Memory = Field(default_factory=Memory)  # For debug`。所有 `publish_message` 追加进来（base_env.py:193），注释明说是调试用，不持久化。
- 长期 Memory（`metagpt/memory/memory_storage.py:19-54`）：FAISS 向量库，目录 `DATA_PATH/role_mem/<role_id>/default__vector_store.json`（memory_storage.py:40-44）。**命名空间 = role_id**，不是 user、不是 session。
- 项目级序列化（`metagpt/team.py:59-81`）：`Team.serialize()` 把整支团队 + context dump 成 `SERDESER_PATH/team/team.json`（const.py:58 指向 `<workspace>/storage/team`）；`deserialize` 时若文件不存在直接 raise "not to recover and please start a new project"（team.py:72-75）。
- 经验池（`metagpt/exp_pool/manager.py:70-91`）：`create_exps` 写完向量后立刻 `self.storage.persist(self.config.exp_pool.persist_path)`（manager.py:91）——**批量写后立即刷盘**。

### 2.2 切换 / 恢复机制

- 启动恢复：`LongTermMemory.recover_memory(role_id, rc)`（`metagpt/memory/longterm_memory.py:31-40`）——目录存在就 load FAISS index（memory_storage.py:44-49），不存在就建空库并打 warning "first time to run Role ..."。恢复进来的消息打 `msg_from_recover=True` 标记，避免被二次写回向量库（longterm_memory.py:42-48）。
- 项目恢复：`software_company.py:68` `Team.deserialize(stg_path, context=ctx)`——整支团队从 `team.json` 反序列化，context（含 CostManager）单独走 `context.serialize()/deserialize()`（context.py:102-127）。
- **没有"当前会话指针"概念**——一次只跑一个项目，跑完即结束；要开新对话就是新起一个 Team。

### 2.3 持久化时机

- 短期 Memory：不持久化。
- 长期 Memory：`persist()` 是显式方法（memory_storage.py:75-77），由调用方决定何时刷盘；RoleZero 里通过配置 `longterm_memory_persist_path` 注入（`metagpt/roles/di/role_zero.py:181-190`）。**不是每条消息都落盘**——内存 list 是热路径，向量库是冷层。
- 经验池：批量写后立即 persist（manager.py:91）。
- 项目快照：`Team.serialize()` 由业务流程显式调用，不是消息驱动。

### 2.4 隔离

- 命名空间按 **role_id**（`role_mem/<role_id>/`）和 **project workspace**（`DEFAULT_WORKSPACE_ROOT/<project>/`，const.py:41）切分。
- 无多用户、无并发会话模型——单进程单项目假设。

---

## 3. 机制对照表

| 维度 | AutoGen Studio | MetaGPT | 我方 ai_session_store |
|---|---|---|---|
| 存储介质 | SQLite (SQLModel) | 内存 + FAISS/JSON | 文件树 index.json + sessions/<id>.json |
| 层级 | Team→Session→Run→Message | 无 Run；Memory / Env.history / 长期向量库 | Session→Message（扁平） |
| 当前指针 | 前端状态，不落库 | 无（单项目） | **落库** index.json.current（ai_session_store.cpp:158,197） |
| 元数据 | user_id/version/created_at/updated_at/run_status/error_message | role_id/workspace 路径 | id/title/updatedAt（SessionInfo） |
| 持久化时机 | 每条消息流式 upsert | 短期不落盘；长期显式 persist | 每条 appendMessage 后 saveSession+saveIndex（ai_session_store.cpp:132-134） |
| 恢复 | runs asc + messages asc 拼回 | team.json 整包反序列化 | 启动 load()，切换时懒加载 cache_（ai_session_store.h:83） |
| 崩溃中断标记 | Run.status=STOPPED + shutdown 消息 | 无 | 无 |
| 隔离 | user_id 过滤 + run_id 并发键 | role_id / workspace 目录 | 单用户桌面，无隔离维度 |

---

## 4. 对我方 ai_session_store 的差距清单

我方已有：index.json{current,sessions[]} + sessions/<id>.json{messages}、CRUD、UI `aiSessionCombo_` 切换器、懒加载 cache_、context-compaction 后 `setMessages` 整段替换（ai_session_store.h:62）、appendMessage 后立即落盘（ai_session_store.cpp:132-134）。逐条对照：

### 4.1 建议做（Do）

1. **消息元数据字段补 `role` 之外的 type/时间戳**：我方 `SessionMessage{role,content}`（ai_session_store.h:18-21）无法区分"普通对话"与"工具调用事件/错误事件/已摘要标记"。AutoGen 把 ToolCallRequestEvent、ToolCallExecutionEvent、StopMessage 都当独立消息行存（connection.py:131-143）。最小改动：给 `SessionMessage` 加可选 `kind`(user/assistant/tool_call/tool_result/system) 与 `ts`，JSON 向后兼容（缺省回退 user/assistant）。理由：Phase31 Wave2 要做"工具调用错误恢复显式化"，UI 需要把工具调用与普通回复视觉分开。
2. **持久化 schema 加 `version` 字段**：我方 index.json / session json 无版本号。AutoGen BaseDBModel 内建 `version="0.0.1"`（db.py:48）+ Alembic；我方文件树改造迟早需要迁移钩子。最小改动：index.json 顶层加 `"version": 1`，load() 时 if 旧字段缺失走默认路径。
3. **崩溃/中途退出的会话标记**：我方进程被杀后重启，无法区分"用户正常聊完"与"流式回复写到一半进程崩了"。AutoGen 用 Run.status=STOPPED + shutdown 消息兜底（connection.py:448-460）。最小改动：session json 顶层加 `"incomplete": true`（append assistant 时置位、收到完整回复/用户下一条消息时清除）；恢复时 UI 在最后一条旁打"〔上次未完成〕"角标。成本极低，收益是流式半截内容不再伪装成完整回复。

### 4.2 建议 YAGNI（暂不做）

1. **Run 三级模型（Session→Run→Message）**：AutoGen 拆 Run 是因为 Web 服务要在同一线程里并发跑多次团队任务、要给每次执行独立的取消按钮和状态。我方是单用户桌面、LLM 串行 worker（llm_worker），一次只跑一轮，扁平消息流足够。**YAGNI**——除非未来要在同一会话里并发跑两条任务。
2. **user_id 多用户隔离**：桌面单用户应用，无登录态。**YAGNI**。
3. **SQLite / 关系型存储**：我方 <100 条会话、每会话几十条消息，JSON 文件树读写字节数都在 KB 级，SQLite 的迁移/事务/并发收益为零。AutoGen 用 SQLite 是因为它是 Web 服务、要并发、要 SQL 查询。**YAGNI**。
4. **agent 内部状态 checkpoint（save_state/load_state）**：AutoGen agentchat 的 `TeamState.agent_states`（_states.py:20-24）是为了支持远程 agent、跨 runtime 迁移。我方 agent 无内部寄存器（每次请求从消息流重建上下文），没有可 checkpoint 的东西。**YAGNI**。
5. **向量长期记忆 / FAISS**：MetaGPT 的长期记忆是给多角色协作项目做跨会话经验复用。我方是单操作员电台助手，历史消息全量进上下文窗口（8192 budget + compaction），向量检索是过度工程。**YAGNI**——除非未来做跨会话"你上次问过的卫星频率"这类语义召回。
6. **"当前会话"前端状态化**：AutoGen 不落 current 是因为它是多 tab Web 服务，current 属于浏览器。我方是单窗口桌面，落库 `current` 反而是优点——重启后回到上次会话。**保持现状**，不要改。
7. **消息级 message_meta JSON**：AutoGen 留这个字段是为了塞图片 base64、LLM 调用统计等扩展。我方无多模态、无 token 计费展示需求。**YAGNI**。

---

## 5. 一句话结论

AutoGen Studio 的精髓不是"会话怎么存"，而是 **Run 状态机 + 每条消息流式落库 + user_id 查询过滤**；MetaGPT 的精髓是 **短期内存 / 长期向量 / 项目快照三层分离，持久化时机由调用方显式决定**。我方文件树 + current 指针 + append 即落盘的设计对单用户桌面已经够用；真正值得补的是 **消息 kind/ts 元数据、schema version、崩溃未完成标记** 这三件小事，其余 Web 服务级复杂度（Run 分层、多用户、SQLite、向量库）当前都是 YAGNI。
