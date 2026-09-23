# 第二轮深度代码审查：Context / Memory / ModelManager

- 审查范围：`mbdsdr_ai/context_manager.py` (393 行)、`mbdsdr_ai/memory.py` (287 行)、`mbdsdr_ai/model_manager.py` (588 行)
- 关联文件：`mbdsdr_ai/agent.py`（主循环接线）、`mbdsdr_ai/config.py`（默认参数）
- 审查方法：逐行通读三个模块，并用 `Grep` 追踪它们在 `agent.py` / `tool_registry.py` / `subagents.py` / `llm_judge.py` 中的真实调用点。

---

## 一、与 agent.py 的真实接线情况（先给结论）

三个模块**都不是空壳**，都在主循环里被真实调用：

| 模块 | 实例化 | 主循环调用点 | 结论 |
|---|---|---|---|
| `ContextManager` | `agent.py:68-75` | `add_user_message` `agent.py:2661`；`needs_compaction/compact` `agent.py:2683-2684, 2792-2793`；`build_api_messages` `agent.py:2688`；`add_assistant_message` `agent.py:2738,2743`；`add_tool_message` `agent.py:2783` | 真实接入 |
| `MemoryStore` | `agent.py:97` | `build_memory_context` `agent.py:2664`；`memory_write`/`memory_search` 工具 `agent.py:1368-1408`；`forget_old` `agent.py:2797-2798` | 真实接入 |
| `ModelManager` | `agent.py:77-85` | `chat_stream` `agent.py:2696`；`chat`（压缩摘要）`agent.py:2863`；`parse_tool_calls_from_text` `agent.py:2728`；`fetch_models` `agent.py:89` | 真实接入 |

但"接线真实"≠"功能有效"。下面按审查重点逐条给发现。

---

## 二、context_manager.py

### [真bug] 1. 上下文窗口大小与实际模型窗口严重脱节
- 位置：`config.py:43` `max_context_tokens: int = 8192`；`context_manager.py:154` 直接使用该值；`model_manager.py:72` `Qwen/Qwen3.6-35B-A3B` 标注 `context_window=131072`。
- 问题：`ContextManager` 认为窗口只有 8192 token，压缩阈值 0.8 → 6553 token 就触发压缩。但默认模型实际支持 131072 token。结果是**每说几句就压缩一次**，把仍可利用的长历史过早裁掉。
- 反向风险：若用户切到 32k 窗口的弱模型，`ContextManager` 仍按 8192 算，不会溢出但也没有按真实窗口保护。
- 根因：`ModelInfo.context_window`（`model_manager.py:31`）从未被回填到 `ContextManager.max_context_tokens`。这是典型"实验室绿、真机红"——单元测试用短对话测，压缩逻辑能跑；真机长对话下压缩频率过高。
- 建议：初始化或 `switch_model` 后，用 `ModelInfo.context_window` 自适应更新 `max_context_tokens`。

### [真bug] 2. 压缩摘要以 `role: "system"` 塞进 history，导致 API 请求出现两条 system 消息
- 位置：`context_manager.py:310-313`：
  ```python
  new_history.append({"role": "system", "content": f"[上下文压缩摘要 epoch={self.epoch}]\n{summary}"})
  ```
- `context_manager.py:341` `build_api_messages()` 又无条件 prepend `{"role":"system","content": self.system_prompt}`。
- 结果：发给 LLM 的消息序列是 `[system: 主提示词, system: 压缩摘要, ...kept]`。OpenAI 兼容网关多数能容忍多个 system，但部分严格网关 / 模型会把第二条 system 当 user 或直接 400。更隐蔽的是：下一轮压缩时，这条"摘要 system"也在 `self.history` 里，可能被当作普通消息裁掉，导致摘要丢失。
- 建议：摘要应作为 `role: "user"` 的特殊标记消息（或单独字段），不要与顶层 system 混排。

### [真bug] 3. 压缩时被裁历史的信息损失过大
- 位置：`context_manager.py:330` `content[:500]`（每条消息只取前 500 字符）；`agent.py:2861` `history_text[:3000]`（送摘要 LLM 时再截到 3000 字符）。
- 问题：一次压缩可能裁掉上万字符的历史，但送给摘要模型的文本被两道截断砍到 ~3000 字符。摘要只能覆盖最早被裁的那部分的开头，**中间被裁的频率、模式、未完成任务全部丢失**。
- 影响：长 SDR 会话（扫频、解调、参数调整）跨多次压缩后，模型彻底"忘记"早先的频率设置。

### [真bug] 4. 没有硬溢出保护
- 位置：`agent.py:2681-2688` 每轮循环开头才检查 `needs_compaction()`；`context_manager.py:210` 工具输出只按字符数截断。
- 问题：若某一轮 assistant 同时发起多个 tool_call，每个工具结果 4000 字符，单轮新增可能 = 多个 4000 字符 + 2048 token 回复。压缩只在**下一轮开头**触发，当前轮的 API 请求可能已经超过真实窗口。代码没有"发送前硬截断 / 拒绝发送"的保护。
- 叠加问题 1（窗口错配）：真实窗口 131k 时不会真溢出，但窗口错配时（如切到弱模型）就可能炸。

### [真bug] 5. 工具输出按字符硬切，可能切坏结构化数据
- 位置：`context_manager.py:210-211`：
  ```python
  content = content[:self.tool_output_max_chars] + f"\n... [输出已截断，原长度 {len(content)} 字符]"
  ```
- 问题：SDR 工具常返回 JSON / Base64 / 频谱数值数组。按字符硬切会切在 JSON 中间，下一轮模型试图 `json.loads` 工具结果时失败。应按行 / 按 JSON 边界截断。

### [占位] 6. "大输出写文件"未实现
- 位置：模块 docstring `context_manager.py:13` 声称"工具输出过大时……（大输出截断/写文件）"。
- 实际：`add_tool_message` 只做了截断，没有任何"写文件 + 在消息里留路径"的逻辑。属于文档占位。

### [建议] 7. `compact()` 中 `kept.insert(0, msg)` 是 O(n²)
- 位置：`context_manager.py:289`。
- 长历史（几千条）下每次压缩都对 list 做 n 次头部插入。数据量小时无感，但与问题 1（压缩过频）叠加会被放大。建议反向 append 后一次性 `reverse()`。

### [建议] 8. 系统提示词每轮重建导致 token 缓存失效
- 位置：`agent.py:2667-2671` 每轮都 `set_system_prompt(system_prompt)`；`context_manager.py:173` 使 `_system_tokens_cache = None`。
- 结果：每次 `get_stats()` / `needs_compaction()` 都对整个系统提示词重新跑一遍 tiktoken 编码。tiktoken 本身不快。建议只在记忆 / 工具数变化时才重建。

---

## 三、memory.py

### [真bug] 9. 中文检索近乎失效——"实验室绿、真机红"的典型
- 位置：`memory.py:130` `content_words = set(content.lower().split())`；`memory.py:175` `query_words = set(query.lower().split())`；`memory.py:185` `mem_words = set(mem.content.lower().split()) | set(mem.tags)`。
- 问题：Python `str.split()` 按**空白**分词。中文没有空格，于是：
  - 用户输入"记住我常用频率 145.5 MHz"被切成 `{"记住我常用频率", "145.5", "MHz"}`——前两个词其实是一个长 token。
  - 一条记忆"常用频率设置为 145.5MHz"被切成 `{"常用频率设置为", "145.5MHz"}`。
  - 两个集合的交集只有数字/英文部分，Jaccard 系数极低，几乎检索不到。
- 测试为什么能过：测试用例大概是英文单词或空格分隔的关键词。真机全中文 SDR 指令下，`build_memory_context`（`agent.py:2664`）每轮都跑，但返回空，记忆形同虚设。
- 这是本批审查里**最影响真实可用性的 bug**。
- 建议：接入一个轻量中文分词（jieba）或至少按 bigram / 字符 n-gram 建索引；tags 字段目前只有 LLM 主动写标签时才有用。

### [真bug] 10. `_find_similar` 去重对中文同样失效
- 位置：`memory.py:128-139`，Jaccard > 0.5 判重。
- 由于中文切词问题，两条不同的中文记忆 Jaccard 基本为 0（除非完全相同），去重永不触发。结果：LLM 反复写"记住用户喜欢 145.5MHz"会产生多条近似记忆，存储膨胀。

### [占位] 11. "记忆索引"未实现
- 位置：docstring `memory.py:9` "记忆索引：对历史对话/文件建立索引，快速检索"；`memory.py:163` `search()`。
- 实际：线性扫描所有记忆，纯词重叠打分。没有 embedding、没有向量索引、没有 BM25、没有倒排表。属于文档占位。当前规模（几十~几百条）尚可，但与"索引"这个词不符。

### [真bug] 12. 每次搜索触发全量 JSON 落盘
- 位置：`memory.py:207-211`：
  ```python
  for mem in results:
      mem.access_count += 1
      mem.last_accessed = time.time()
  if results:
      self._save()
  ```
- 问题：`agent.py:2664` 每轮用户消息都调用 `build_memory_context → search`。只要搜到任何记忆，就把**整个 memory.json** 重写一遍（`memory.py:80-88`）。记忆上千条时，每次对话都要做一次全量序列化 + 磁盘写。
- 叠加：`add()` / `update()` / `delete()` 也都立即 `_save()`。没有 dirty 标记 / 防抖 / 异步落盘。

### [真bug] 13. 单条坏记忆会导致整个记忆库被丢弃
- 位置：`memory.py:69-78`：
  ```python
  try:
      data = json.load(f)
      for item in data.get("memories", []):
          mem = Memory(**item)
          self.memories[mem.id] = mem
  except Exception as e:
      print(f"警告: 记忆文件加载失败: {e}")
  ```
- 问题：只要任意一条记忆字段不匹配（例如未来 schema 加字段、手改 JSON、某条 metadata 类型错），整个 for 循环抛异常，**所有已加载的记忆都留在内存里但不会被持久化**——下次启动时因为 `Memory(**item)` 在第一条坏记录处中断，前面的记录虽然加载了，但异常被外层 catch 后没有任何恢复。更糟的是，如果坏记录在最后，前面的加载成功；但只要 LLM 后续写入，`_save()` 会用内存里的部分数据覆盖掉原文件，**坏数据丢失**。
- 建议：逐条 try，跳过坏记录；加载失败时备份原文件而不是覆盖。

### [安全] 14. 记忆明文落盘，无敏感信息过滤
- 位置：`memory.py:59` `~/.mbdsdr/memory.json`；`memory.py:87-88` 明文 JSON。
- 问题：`memory_write` 工具（`agent.py:1368`）让 LLM 把任意内容写进记忆。如果 LLM 误把 API key、用户位置、车牌、callsign+Grid Square 等敏感信息写进去，就是明文 JSON 落在用户 home 目录，默认 umask 下其他本地用户可读。没有加密、没有敏感词过滤、没有脱敏。

### [建议] 15. `forget_old` 的触发条件偏松
- 位置：`memory.py:225-239`；`agent.py:2797` 每 10 次调用才跑一次。
- 条件：`updated_at < 90天前 AND importance < 0.3 AND access_count < 3`。高重要性 / 被访问过的记忆永远不遗忘。对于 SDR 场景（频率偏好是长期稳定的）这是合理的，但"重要性"是 LLM 写入时自己给的，没有校准。

---

## 四、model_manager.py

### [空壳] 16. 弱/强模型路由根本不存在
- 位置：`model_manager.py:32` `is_weak` 字段；`model_manager.py:155-158` `_is_weak_model`；`model_manager.py:73-74` BUILTIN 里把 4B/9B 标成 weak。
- 问题：grep 整个仓库，`is_weak` 除了定义和 `to_dict` 输出外**没有任何地方读取它做路由决策**。`agent.py:2696` 主循环永远用 `self.model_manager.model`；压缩摘要（`agent.py:2863`）也用同一个模型。
- 结果：弱模型标签只是个展示标记。文档（`model_manager.py:8`）承诺的"模型切换（弱模型/强模型）"在自动路由层面不存在，只能由用户手动 `/model` 命令切（`agent.py:2931`）。没有"简单任务用弱模型省钱、复杂任务用强模型"的逻辑。

### [空壳] 17. 无自动降级 / failover
- 位置：`model_manager.py:249-298` 重试循环。
- 问题：429/500/502/503/504 时只对**同一个模型** sleep 重试（`model_manager.py:284` `time.sleep(1*(attempt+1))`）。重试用尽后直接返回失败，不会切换到备用模型 / 弱模型 / 本地模型。文档承诺的"多 provider 支持"（`model_manager.py:11`）也没有 provider 级故障转移——base_url 是单一的。

### [真bug] 18. 流式调用拿不到 usage，token 统计系统性偏低
- 位置：`model_manager.py:336-346` 流式 payload 没有 `stream_options: {"include_usage": true}`；`model_manager.py:369-370` `if chunk.get("usage"): usage = chunk["usage"]`。
- 问题：OpenAI / SiliconFlow 流式模式下，默认不在 SSE 块里返回 usage。必须显式请求 `stream_options.include_usage=true`。当前代码没设，所以 `usage` 几乎总是空 dict。而主循环 `agent.py:2696` 走的就是 `chat_stream`——这意味着 `CallStats.total_prompt_tokens` / `total_completion_tokens` 长期为 0，`agent.py:2712-2714` 的累计用量也基本为空。
- 影响：用户看到的"调用统计"（`model_manager.py:583`）严重失真；基于 token 的成本估算完全不可信。

### [真bug] 19. `chat_stream` 没有重试，与 `chat` 行为不一致
- 位置：`model_manager.py:352-413` 整个流式函数没有重试循环。
- 问题：非流式 `chat` 对 429/5xx/超时/连接错误有重试（`model_manager.py:279-295`），但流式一遇瞬时错误就直接 `yield {"success": False}`。主循环走流式，所以真实场景下瞬时抖动直接表现为"模型调用失败"，而不是自动恢复。

### [真bug] 20. `parse_tool_call_args` 的单引号替换会破坏字符串
- 位置：`model_manager.py:432`：
  ```python
  fixed = args_str.replace("'", '"').replace("True","true").replace("False","false")
  ```
- 问题：把所有 `'` 换成 `"`。如果参数里有 `{"note": "it's a test"}`，替换后变成 `{"note": "it"s a test"}`，JSON 立刻坏。这种 naive 修复在含撇号的文本（英文/混合 SDR 描述）里必然出错。
- 对比：`model_manager.py:453-455` 的 `_coerce_tool_json` 也有同样问题。

### [占位] 21. 内置模型 ID 疑似虚构
- 位置：`model_manager.py:72-76`：
  - `Qwen/Qwen3.6-35B-A3B`
  - `Qwen/Qwen3.5-4B`
  - `Qwen/Qwen3.5-9B`
  - `deepseek-ai/DeepSeek-V3` / `DeepSeek-R1`
- 问题："Qwen3.6" / "Qwen3.5" 这种命名在 SiliconFlow 真实模型列表里对不上（截至审查时 Qwen 系列公开版本号不是这样）。如果真实 API 返回的列表里没有这些 ID，首次 `fetch_models` 会用真实列表覆盖；但**用户手动 `/model` 切到内置 ID 时会 404**。属于占位模型名。

### [安全] 22. API key 脱敏只防君子
- 位置：`model_manager.py:563`：
  ```python
  self.api_key[:8] + "..." + self.api_key[-4:] if self.api_key else "(未设置)"
  ```
- 问题：在状态接口里泄露 key 前 8 字符 + 后 4 字符。对 `sk-xxxxxxxx...yyyy` 格式，前 8 位通常是 `sk-`+4 字符，泄露风险有限；但如果 key 较短（<12 字符），整个 key 会被拼出来。另外 key 本身以明文存在内存（`self.api_key`），没有 keyring / 环境变量二次封装。建议：不返回后 4 位，长度不足时直接显示 `***`。

### [建议] 23. `_is_weak_model` 关键词判定不一致
- 位置：`model_manager.py:157` `weak_keywords = ["1.5B","1b","0.5B","tiny","small","7B"]`。
- 问题：BUILTIN 把 9B 标成 weak（`model_manager.py:74`），但关键词列表里没有 "9B"； freshly fetched 的 9B 模型会被判为强模型。判定规则自相矛盾。

---

## 五、跨模块问题

### [真bug] 24. 上下文窗口与模型能力不联动（与问题 1 合并表述）
- `ContextManager.max_context_tokens`（默认 8192）与 `ModelInfo.context_window`（131072 / 32768）完全独立。`switch_model`（`model_manager.py:174`）不通知 ContextManager 更新窗口。切到小窗口模型时没有溢出保护，切到大窗口模型时过度压缩。

### [真bug] 25. 记忆注入系统提示词，但系统提示词每轮重建
- `agent.py:2664` `build_memory_context(user_input)` 用当前用户输入检索记忆，然后拼到 SYSTEM_PROMPT 后面（`agent.py:2667-2669`）。
- 问题：
  1. 由于问题 9（中文检索失效），这一步大概率返回空。
  2. 记忆内容直接拼进系统提示词，不经过 token 预算——如果搜到 3 条长记忆，可能挤掉留给历史的 token 空间（叠加问题 1 的小窗口）。
  3. 记忆是基于"当前这一句用户输入"检索的，多轮工具调用中途的语义漂移不会触发重新检索。

### [性能] 26. 每轮对话的固定开销
- 每轮用户消息：
  - `build_memory_context` → 全量线性扫记忆 + 全量落盘（问题 12）
  - `set_system_prompt` → 使 system token 缓存失效（问题 8）
  - `needs_compaction` → `get_stats()` → 对所有 history 消息重新 `estimate_message_tokens`（`context_manager.py:226`，无缓存）
- 长会话下每轮 O(history + memories) 的 CPU + 一次磁盘写。数据量大时会明显卡顿。

---

## 六、严重度汇总

| # | 标签 | 文件:行 | 简述 |
|---|---|---|---|
| 9 | [真bug] | memory.py:130,175,185 | 中文按空白分词，记忆检索在真机中文场景近乎失效 |
| 1 | [真bug] | config.py:43 / context_manager.py:154 / model_manager.py:72 | 上下文窗口写死 8192，与真实模型 131k 脱节，过度压缩 |
| 18 | [真bug] | model_manager.py:336-346 | 流式未设 `stream_options.include_usage`，token 统计恒为 0 |
| 3 | [真bug] | context_manager.py:330 / agent.py:2861 | 压缩摘要文本被双重截断，关键历史丢失 |
| 12 | [真bug] | memory.py:207-211 | 每次搜索触发全量 memory.json 落盘 |
| 2 | [真bug] | context_manager.py:310-313,341 | 压缩摘要以 role=system 入 history，产生双 system 消息 |
| 13 | [真bug] | memory.py:69-78 | 单条坏记忆可致整个记忆库加载失败/被覆盖 |
| 4 | [真bug] | agent.py:2681 / context_manager.py:210 | 无发送前硬溢出保护 |
| 5 | [真bug] | context_manager.py:210-211 | 工具输出按字符硬切，破坏 JSON/Base64 |
| 10 | [真bug] | memory.py:128-139 | 中文下去重 Jaccard 不触发，记忆膨胀 |
| 19 | [真bug] | model_manager.py:352-413 | 流式调用无重试，瞬时抖动直接失败 |
| 20 | [真bug] | model_manager.py:432,453-455 | naive 单引号替换破坏 JSON |
| 16 | [空壳] | model_manager.py:32,155 | is_weak 字段定义了但无路由逻辑读取 |
| 17 | [空壳] | model_manager.py:249-298 | 无跨模型/跨 provider 降级 |
| 6 | [占位] | context_manager.py:13 | "大输出写文件"未实现，只截断 |
| 11 | [占位] | memory.py:9,163 | "记忆索引"未实现，线性扫描 |
| 21 | [占位] | model_manager.py:72-76 | 内置模型 ID 疑似虚构 |
| 14 | [安全] | memory.py:59,87 | 记忆明文 JSON，无脱敏/加密 |
| 22 | [安全] | model_manager.py:563 | 状态接口泄露 key 前后缀 |
| 7 | [建议] | context_manager.py:289 | compact 中 list.insert(0) O(n²) |
| 8 | [建议] | agent.py:2667 / context_manager.py:173 | 每轮重建系统提示词致缓存失效 |
| 15 | [建议] | memory.py:225 | 遗忘策略依赖 LLM 自评 importance |
| 23 | [建议] | model_manager.py:157 | weak 关键词列表与内置标注不一致 |
| 25 | [真bug] | agent.py:2664-2669 | 记忆直接拼进系统提示词，不占 token 预算 |
| 26 | [性能] | 多文件 | 每轮 O(history+memories) + 一次磁盘写 |

---

## 七、一句话结论

三个模块**接线是真的、主循环在用**，但存在两类硬伤：
1. **中文场景失效**：记忆检索（问题 9）和去重（问题 10）按空白分词，真机 SDR 中文指令下记忆基本不命中——这是"实验室绿、真机红"最典型的一处。
2. **窗口与统计失真**：上下文窗口写死 8192（问题 1）导致对 131k 模型过度压缩；流式 token 统计因缺 `stream_options` 恒为 0（问题 18），用户看到的成本 / 用量数据不可信。

弱/强模型路由（问题 16、17）目前是**接口存在、逻辑未实现**的空壳，只能手动切。
