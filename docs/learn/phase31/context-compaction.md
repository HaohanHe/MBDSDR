# Phase 31 · A1：开源 Agent 框架上下文压缩机制调研

> 目标：学习业界 context compaction 实现，复核/改进我方 `ai_context::compactContext`。只学机制，不抄代码。

## 0. 我方现状基线

- `cpp/src/ai/ai_context.h:19` `kAiContextBudgetTokens=8192`；`:22` `kAiContextKeepRecentRounds=4`
- 实现：`cpp/src/ai/ai_context.cpp:49` `compactContext()`
- 策略：总 token 超预算 → 保留最近 N 轮 user turn 原文，更早的全部折叠为一条 `role="summary"` 消息
- 摘要：可选 `SummaryFn` 回调（LLM 摘要）；无回调走规则降级（计数 + 前 3 个 user ask 截断 24 字符）
- Token 估算：`estimateTokens()` 启发式（CJK ≈1 token/字，Latin ≈4 字/token）；UI 标注为「已摘要」

---

## 1. OpenAI Agents SDK

- URL：https://github.com/openai/openai-agents-python
- clone：`repos/phase31_openai_agents/`

### 1.1 OpenAIResponsesCompactionSession

- 文件：`src/agents/memory/openai_responses_compaction_session.py:91`，装饰器模式包装 Session

**触发条件（item-count，非 token）**
- `:36` `DEFAULT_COMPACTION_THRESHOLD = 10`
- `:67-69` 默认触发：`len(compaction_candidate_items) >= 10`
- `:50-64` 候选 item 排除 user message 和已有 compaction 条目，只压 assistant/tool/reasoning
- `:120` 可传入 `should_trigger_compaction` 钩子自定义决策

**摘要策略：服务端 API 摘要**
- `:449` 调 OpenAI `responses.compact` API，不由 SDK 本地 LLM 生成
- `:39` 三种模式：`previous_response_id`（服务端增量摘要）/ `input`（全量发给 compact API）/ `auto`
- `:459-504` 原文处理：compact 输出替换 session 历史；自动模式只做 suffix 替换，前缀保留

**触发时机**
- `src/agents/run_internal/session_persistence.py:634` `_apply_post_write_compaction()`
- 每轮模型响应写入 session 后立即评估；有本地 tool output 待处理时 defer 到下一轮（`:654-666`）
- `:246` mutation lock 保护并发快照-替换

### 1.2 ToolOutputTrimmer（独立工具输出修剪）

- 文件：`src/agents/extensions/tool_output_trimmer.py:87`
- 定位：单条大 tool output 的滑动窗口修剪，独立于全量摘要
- 默认值：`:112-114` `recent_turns=2` / `max_output_chars=500` / `preview_chars=200`
- 修剪格式：`:262` `[Trimmed: tool_name output — N chars → preview]\n{preview}...`
- 运行时机：每次模型调用前，作为 `call_model_input_filter` 钩子执行
- `:457` 还会递归剥离 tool schema 中的 description/examples 散文字段

---

## 2. LangGraph

- URL：https://github.com/langchain-ai/langgraph
- clone：`repos/phase31_langgraph/`

### 2.1 框架不内置压缩，只提供扩展点

- `libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py:296` `pre_model_hook` 参数
- 注释：`:397` "Useful for managing long message histories (e.g., message trimming, summarization, etc.)"
- 机制：pre_model_hook 是图节点，插在 agent 节点前；可改 `messages`（动状态）或 `llm_input_messages`（只改 LLM 输入）
- 生态惯例：`trim_messages` + summarization node 组合，用户自己拼；哲学是图引擎不管压缩策略

---

## 3. AutoGen

- URL：https://github.com/microsoft/autogen
- clone：`repos/phase31_autogen/`

### 3.1 TokenLimitedChatCompletionContext

- 文件：`python/packages/autogen-core/src/autogen_core/model_context/_token_limited_chat_completion_context.py:19`

**触发条件（纯 token）**
- `:57-77` `get_messages()` 每次取消息时动态裁剪
- 显式 `token_limit`：`:68` `count_tokens() > token_limit` 就裁
- 隐式（None）：`:62` 调 `model_client.remaining_tokens()` 查模型窗口剩余

**摘要策略：无摘要，中间截断**
- `:63-66` middle-out：从列表正中间 `middle_index = len(messages)//2` 删消息，循环直到 fit
- 不生成摘要文本，直接丢消息
- `:73-76` 兜底：首条是 FunctionExecutionResultMessage 则直接丢弃
- 工具 schema 占 token 也计入预算

---

## 4. MetaGPT

- URL：https://github.com/FoundationAgents/MetaGPT
- clone：`repos/phase31_metagpt/`

### 4.1 CompressType 枚举

- `metagpt/configs/compress_msg_config.py:4` 四种策略：
  - `POST_CUT_BY_MSG/TOKEN`：保留最新，从头丢 / 截断 fit 不进的那条
  - `PRE_CUT_BY_MSG/TOKEN`：保留最早，从尾丢 / 截断 fit 不进的那条

### 4.2 compress_messages()

- `metagpt/provider/base_llm.py:340`
- `:352` 阈值 `threshold=0.8` —— 用窗口的 80%，预留 20% 给 completion
- `:357` token 上限从 `TOKEN_MAX` 字典按模型查，默认 128000
- `:362-373` system message 永久保留
- 无 LLM 摘要，纯截断；POST_CUT_BY_TOKEN 从内容末尾往前截（`:386`）

---

## 5. 横向对比

| 维度 | OpenAI SDK | LangGraph | AutoGen | MetaGPT | 我方 |
|------|-----------|-----------|---------|---------|------|
| 触发信号 | item 数 ≥10 | 用户自定义 | token 实时算 | token×0.8 | 估算 token>8192 |
| 触发时机 | 每轮写 session 后 | 调 LLM 前 hook | 每次取消息时 | 每次 completion 前 | 组装 prompt 时 |
| 摘要生成 | 服务端 API | 用户自己接 | 无 | 无 | 可选 LLM+规则降级 |
| 原文处理 | suffix 替换，user 不压 | 用户决定 | 中间删除 | 前后截断 | 早轮次折叠 summary |
| 保留策略 | user msg 永不压 | 用户实现 | 不感知轮次 | system msg 永不压 | 最近 4 轮原文 |
| Token 精度 | 不做本地计算 | 依赖 langchain | model.count_tokens | TOKEN_MAX 字典 | 启发式估算 |

---

## 6. 对我方 compactContext 的差距项清单

### 建议做（ROI 高、复杂度低）

1. **Tool output 单独修剪（对齐 ToolOutputTrimmer）**
   - 现状：tool result 和普通消息一视同仁，超预算整轮折叠
   - 差距：SDR 场景 tool result（IQ 摘要、日志）可能很大，单条就撑爆预算
   - 做法：加 `truncateLargeToolOutputs()` 前置步骤，>N 字符时截断保留头尾预览，标注 `[已截断]`
   - 依据：OpenAI SDK 证明这是独立于全量摘要的正交优化

2. **budget 改为模型窗口比例而非硬编码 8192**
   - 现状：`kAiContextBudgetTokens=8192` 写死
   - 差距：换大窗口模型（32k/128k）会过早触发压缩
   - 做法：`budget = modelContextWindow * 0.75`，预留 25% 给 completion + tool result
   - 依据：MetaGPT `threshold=0.8`（base_llm.py:352）是标准做法

3. **确认 user message 在摘要中的保留策略**
   - 现状：早轮 user message 被摘要掉，只保留前 3 个 ask 原文
   - 差距：OpenAI SDK `select_compaction_candidate_items()` 明确排除 user message（:58-64）
   - 折中：summary 里保留所有 user ask 的一行式列表，而非只前 3 个

### YAGNI（暂不做，记录备查）

4. **Middle-out 截断（AutoGen 模式）** —— 我方单用户单会话，从前到后摘要+保留最近轮次语义更好
5. **服务端 compaction API** —— 通用 LLM API 无此端点；我方 SummaryFn 回调已覆盖 LLM 摘要能力
6. **pre_model_hook 插件化管线（LangGraph 模式）** —— 嵌入式 Qt 应用非图引擎，CompactOptions+回调已够用
7. **多策略枚举（MetaGPT 四种 cut）** —— SDR 对话场景只有"保留最近+摘要更早"一种合理策略
8. **mutation lock / 快照回滚** —— 单线程 Qt 事件循环，无并发写入问题

---

## 7. 结论

业界分两大流派：**摘要派**（OpenAI SDK + 我方）超阈值调 LLM 生成摘要替换原文，信息保留好但多一次调用；**截断派**（AutoGen + MetaGPT）超阈值直接删消息，零开销但信息有损。

我方走摘要派路线方向正确。主要改进：①加 tool output 修剪作为前置轻量压缩（避免动不动触发全量摘要）；②budget 改为窗口比例；③确认 user message 摘要保留策略。整体设计与业界主流一致，无结构性缺陷。
