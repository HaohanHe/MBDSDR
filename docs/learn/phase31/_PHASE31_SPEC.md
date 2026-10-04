# 第三十一阶段实现规格：通用 Agent 学习 → Agent 层推进

> 基线：HEAD = c5c54b2（已推送），ctest 123/123、flutter 331/analyze 0。
>
> 复核事实（2026-10）：ai_session_store（index.json + sessions/<id>.json，CRUD）、ai_context::compactContext（kAiContextBudgetTokens=8192/kAiContextKeepRecentRounds=4、「已摘要」标注）、llm_worker partialReady 流式信号 + llm_client SSE（:59/:174 loop.exec 阻塞主请求）**均已存在并已提交**（Phase28/29 产物，test_ai_session.cpp 覆盖）。**真实缺口**：①35 工具 schema → Agent 可见能力文档（无 toolDocumentation/能力清单生成）；②工具调用错误恢复无显式 retry/降级路径（仅 compactContext fallback 一行）；③移动端无 AI 会话/流式（评估做或 YAGNI）；④学习结论对现有压缩/会话/流式实现的差距复核。

## Wave 1：通用 Agent 学习（docs/learn/phase31/ 笔记，带来源 URL + file:line 证据；只学机制不抄代码）
| 子任务 | 主题 | 精读对象 |
|---|---|---|
| A1 | 上下文压缩 | OpenAI Agents SDK / LangGraph 的 compaction（budget token、摘要策略、触发条件） |
| A2 | 多会话/多对话管理 | AutoGen / MetaGPT（session 存储结构、切换、持久化、隔离） |
| A3 | 流式输出 | OpenAI/Anthropic SSE streaming、瞬态呈现与去重（客户端累积 vs 增量） |
| A4 | 工具调用能力边界 + 模型文档 | SiliconFlow API 文档（function-calling 格式）、小米 MiMo 开发文档（能 clone 到 repos/ 或 docs/learn/phase31/）——含并发/批量、错误恢复、gate 设计 |

## Wave 2：Agent 层改造（C++ 为主，落地 Wave1 结论；不重复已有实现）
1. **能力边界文档化**（真缺口）：35 工具 schema 全量生成 Agent 可见文档（能力清单/参数/边界/示例）——运行时注给模型或写 docs；带测试（文档生成断言：35 工具全在、参数/边界字段完整）；
2. **工具调用错误恢复**（真缺口）：重试/降级/如实报错显式化（LLM 无 key 时诚实 PENDING，不 mock 输出）；
3. **移动端**：会话列表/流式行与桌面对齐或**诚实 YAGNI+回补条件**；
4. **差距复核**：Wave1 学习结论 → 对照现有压缩/会话/流式实现，如实输出差距项（做或 YAGNI，file:line 证据）。

## Wave 3：验证
- 每项带测试；ctest 123 基线不回归（全量输出）；flutter 331 不回归 + analyze 0（若动 mobile）；
- 干净室 MIT、通用非专用、无假数据假空态（LLM 无 key 诚实 PENDING）；活动参数禁入通用代码；无比赛字样；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、学习笔记与落地映射、全量输出、未解决项。
