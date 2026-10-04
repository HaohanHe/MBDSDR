# Phase31 A3 — 流式输出机制学习（streaming）

> 目标：学业界 LLM 流式的「传输格式 → 客户端累积/渲染 → UI 去重 → 中断/错误诚实呈现」四层机制，
> 只学机制不抄代码，对照我方 `llm_client` / `llm_worker` 列差距（做 / YAGNI）。
> 基线：我方已有 `partialReady(accumulated)` + SSE，主请求 `llm_client.cpp:174 loop.exec()` 在 worker 线程阻塞至流结束。

## 0. 来源（已浅克隆到 repos/）

- OpenAI Python SDK @ `becc1d20`：https://github.com/openai/openai-python
  - `src/openai/_streaming.py`（SSE 帧解码 + Stream 迭代）
  - `src/openai/lib/streaming/_deltas.py`（权威 delta 累积算法）
  - `src/openai/lib/streaming/chat/_completions.py`（delta→语义事件 + 完成态去重状态机）
  - `src/openai/types/chat/chat_completion_chunk.py`（chunk/delta 形状）
  - `src/openai/resources/chat/completions/completions.py`（`stream=True` 入口）
- AutoGen（agent 框架 UI 层）@ `027ecf0`：https://github.com/microsoft/autogen
  - `python/packages/autogen-agentchat/src/autogen_agentchat/ui/_console.py`（流式 UI 渲染）
  - `.../agents/_assistant_agent.py`（agent 层流：chunk 事件 + 最终消息 + 取消）
  - `.../messages.py`（`ModelClientStreamingChunkEvent` / `full_message_id`）

---

## 1. 传输格式：SSE 增量 delta，不是全量累积

- 线上每帧是一个 `chat.completion.chunk`，`choices[0].delta` 是**增量片**而非累积全文；
  末尾 `finish_reason` 仅出现在最后一块，`[DONE]` 为结束哨兵。
  证据：`chat_completion_chunk.py:74-93`（`ChoiceDelta.content/tool_calls`）、
  `:110`（`finish_reason`）、`_streaming.py:70`（`[DONE]` break）。
- SDK **本身不累积**：`Stream.__stream__` 把每个 SSE 帧解析成 chunk 后直接 `yield` 给消费方，
  累积责任在消费方。证据：`_streaming.py:108-114`。
- SSE 解码按 SSE 规范：空行才派发一个事件、`:` 开头行是心跳忽略、多行 `data:` 用 `\n` 拼接、
  逐 TCP 片缓冲按 `\r\n|\n` 切行并在 `finish()`  flush 尾行。证据：
  `_streaming.py:302-335`（`_SSELineDecoder`）、`:383-429`（`decode`，`:404` 心跳、`:392` 多行拼接）。
- 工具调用按 `index` 槽位分片：`delta.tool_calls[i].function.arguments` 是 JSON 字符串碎片，
  同一 index 的碎片由客户端拼接。证据：`chat_completion_chunk.py:62-71`（`ChoiceDeltaToolCall.index`）。

> 对照我方：`llm_protocol.cpp:323-341`（OpenAI `delta.content/reasoning_content/tool_calls[].function.arguments`）、
> `:316`（`[DONE]`）、`llm_client.cpp:161-172`（readyRead 按 `\n` 缓冲切行 + `:182` flush 尾行）。**已一致。**

## 2. 客户端处理：增量直渲 vs 累积快照，两种都成立

- **累积算法（delta 合并语义）**：字符串 `+=`、dict 递归合并、带 `index` 的 list 按槽位 merge
  （新槽 insert、旧槽递归合并）、`index`/`type` 字段覆盖而非累加。
  证据：`_deltas.py:33-34`（字符串）、`:37-38`（dict）、`:50-70`（index 槽位）、`:29-31`（index/type 覆盖）。
- **两种渲染策略并存**：
  - (a) 增量直渲：每块 delta 直接打印（`print(delta, end="")`），不做累积。
    证据：autogen `_console.py:179-181`（`apprint(chunk, end="")`）。
  - (b) 累积快照：维护一个 running snapshot，每块同时吐出「本块 delta」和「当前全量 snapshot」，
    UI 二选一。证据：`_completions.py:513-522`（`ContentDeltaEvent` 同时带 `delta=` 与 `snapshot=`）；
    `:109-111` `current_completion_snapshot` 供中途读取累积态。
- 瞬态呈现：流式期间 UI 只维护一个「临时气泡」，随每个快照整体替换（不是把 delta 一个个往后接）。

> 对照我方：`llm_client.cpp:52-64` `StreamAccumulator.feed()`（content/reasoning `+=`、toolSlots 按 index、
> `args[index] += 碎片`）——与 `_deltas.py` 同构。我们选的是策略 (b)：`onChunk(content)` 发**全量累积**
> （`llm_client.cpp:63`），UI 「替换瞬态行不追加」（`llm_client.h:58-61`、`llm_worker.h:45-48`）。**机制正确。**

## 3. UI 去重：完成态固化只做一次，避免与瞬态重复

业界用「状态机 + 关联键」把「瞬态 delta 流」和「固化 transcript」分开：

- **一次性 done 事件**：`ChoiceEventState` 用 `_content_done` 布尔 + `_done_tool_calls: set[int]` 守卫，
  `content.done` / `tool_calls.function.arguments.done` 每个槽位**只 emit 一次**。
  证据：`_completions.py:593-597`（状态位）、`:641-675`（`if not _content_done`）、
  `:705-715`（`if index in done: return`）。UI 收到 `*.done` 才把临时气泡落进正式记录。
- **控制台去重范式**：维护 `streaming_chunks` 列表；若流式期间已经逐块打印在屏，
  最终消息到达时**只打一个换行、不重打全文**（「already printed, just newline」）；
  若非流式路径才整段打印。证据：autogen `_console.py:115`、`:179-186`。
- **关联键**：开流前先造一个 `message_id`，挂到每个 chunk 的 `full_message_id`，最终消息用同一 id，
  UI 据此知道「哪些临时块属于这条最终消息、该由谁替换谁」。
  证据：`_assistant_agent.py:948-949`（造 id）、`:1102`（chunk 带 `full_message_id`）、
  `messages.py:539-541`（字段注释）。

> 对照我方（已正确，非差距）：`main_window.cpp:2618-2621` `aiTransient_ = acc`（替换式，不追加）；
> `:2598-2603` `responseReady` 先 `aiTransient_.clear()` 再把最终文案 append 进 session 并 re-render，
> 注释明确「final content shows up exactly once」（`:2594-2597`）。这正是「瞬态→固化一次」的快照替换版，与业界等价。
> 单 worker 线程、同时只有一个在途 chat，故**不需要** `message_id` 关联键（见 YAGNI）。

## 4. 中断 / 错误：已流到屏的部分要诚实保留

- **流内错误帧**：SDK 在迭代中遇到 `{"error":...}` 帧才 raise `APIError`，**此前已 yield 的 delta 早被消费方拿到**——
  错误不是回滚，而是「部分内容已在屏 + 随后抛错」。证据：`_streaming.py:94-106`；
  `finally: response.close()` 无论是否读完都释放连接（`:115-117`）。
- **长度截断要带部分内容**：`finish_reason=length` 时 raise `LengthFinishReasonError(completion=snapshot)`，
  把**已生成的部分快照**塞进异常，让上层能看到截断前写了什么。证据：`_completions.py:424-431`。
- **usage 可能缺失**：`include_usage` 的统计块在流末尾；流被中断/取消时可能收不到，注释明说。
  证据：`chat_completion_chunk.py:333-341`。
- **取消**：取消令牌一路传进 `create_stream`；取消后已 yield 的 chunk 留在屏上，若最终结果没到则显式 raise（不静默补全）。
  证据：autogen `_assistant_agent.py:1097`（传 token）、`:1105-1106`（无最终结果即 raise）。

---

## 5. 我方现状基线（file:line）

- 传输：SSE `data:`/`[DONE]`，OpenAI+Anthropic delta 解析。`llm_client.cpp:86-105`、`llm_protocol.cpp:312-380`。
- 累积：`StreamAccumulator` 三通道（content/reasoning/toolSlots），工具参数流末一次性解析。`llm_client.cpp:45-84`。
- 信号：`onChunk(累积content)` → `partialReady(累积)`。`llm_client.cpp:63`、`llm_worker.cpp:87`。
- 阻塞：worker 线程内 `loop.exec()`，readyRead 已在流式泵 delta。`llm_client.cpp:155-174`。
- UI：瞬态替换 + `responseReady` 固化一次。`main_window.cpp:2618-2621`、`:2598-2603`。
- **错误现状**：`reply->error()` 即 `resp.error` 并**丢弃 acc 累积内容**后 return（`llm_client.cpp:176-180`）；
  worker 端 emit `chatFinished("LLM 错误: "+err)`（`llm_worker.cpp:96-99`）；
  UI 端 `responseReady` 清掉瞬态、把错误串当 assistant 消息落盘（`agent.cpp:111-116`、`main_window.cpp:2598-2603`）。
  → 用户眼睁睁看着流式出的半句话，超时/断网后**消失**，只剩一句错误。

## 6. 差距清单（做 / YAGNI）

### 做（对齐业界「诚实呈现」，且贴合 Wave2 第 2 项）
- **G1 错误时保留已流式内容**：流中途出错/截断时，不丢弃 `acc.content`。
  改 `llm_client.cpp:176-180`：出错时把 `acc.toResponse()` 的部分内容一并带回（如 `resp.content=acc.content`，
  `resp.error` 另带）；`llm_worker.cpp:96-99` 据此 emit 「已生成片段 + 错误原因」，UI 保留瞬态不抹掉。
  依据：`_completions.py:428-431`（截断带 snapshot）、`_streaming.py:94-106`（错误前 delta 已可见）。
- **G2 错误路径不落盘为正常 assistant 消息**：错误/部分内容走独立信号或标注，不要 append 成正常对话历史
  （现 `agent.cpp:114` 把错误串 append 进 history）。与「LLM 无 key 诚实 PENDING、不 mock」同一原则。

### YAGNI（记录回补条件，本期不做）
- **Y1 用户主动取消令牌**：业界传 `CancellationToken` 中断在途流。我方已有 `kAiRequestTimeoutMs` 超时兜底
  （`llm_client.cpp:157`），单轮短问答够用；回补条件：出现长流式/卡请求需手动中止时再加。
- **Y2 实时显示 reasoning_content**：已累积 thinking（`llm_client.cpp:54`）但 `onChunk` 只发 content，
  thinking 要到 `chatFinished` 才见。是否「思考中…」实时流是 UI 产品取舍，先 YAGNI。
- **Y3 工具参数 delta 实时渲染**：业界会流 `arguments.delta`；我方流末才解析参数（`llm_client.cpp:78`），
  且执行后有 `toolCalled` 可见（`llm_worker.cpp:131`）。半段 JSON 上屏是噪音，YAGNI。
- **Y4 消息关联 id（message_id）**：单 worker、单在途 chat，瞬态行天然唯一；回补条件：允许多并发会话流时再加。
- **Y5 SSE 边缘分支**：多行 `data:\n` 拼接、心跳注释——OpenAI 兼容端点每块单行 JSON，我方 `.trimmed()` 已等价处理
  （`llm_client.cpp:94`）；Anthropic `event:` 行已在 `llm_protocol.cpp:348-377` best-effort。YAGNI。

## 7. 一句话机制总结

线上传的是**增量 delta**；客户端用「字符串+= / dict递归 / list按index槽位合并」累积成快照；
UI 用「一个瞬态气泡随快照整体替换 + done/最终消息只固化一次」做到不重复；
出错/截断时**已在屏的部分要保留并附错误**，而不是清空重写。我方传输、累积、去重三步已对齐，
真正缺口只有「错误时诚实保留部分内容」(G1/G2)，其余按 YAGNI 处理。
