# model-tool-calling 批次学习笔记规范（v1，2026-10-01）

本规范冻结本批次全部学习笔记的结构、命名、出处与质量红线。所有子 agent 必须先读本文件与两个示例笔记，再动手。

## 0. 总目标

精读克隆到本地的模型服务商开发文档（硅基流动 siliconflow + 小米 MiMo），产出**可追溯、可直接指导 MBDSDR 工具化实现**的学习笔记。
背景：MBDSDR 要做真正的 Agent 体系——无线电能力 = 给 LLM 看的 function calling 工具集，模型 = 大脑，runtime = 中介（校验 arguments → 真实执行 → role=tool 回传 → 保留 reasoning_content → 多步循环直到 stop）。**不是注册表，不是产品叙事。**

## 1. 落盘位置与命名

- 目录：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling/`（不存在则 mkdir -p）
- 转换出的纯文本：同目录 `sources/<slug>.txt`（供他人引用）
- 笔记：同目录 `NN-<slug>.md`（NN = 两位序号，由任务指定）
- 综合稿：`/home/user/Doubao/chats/38438160041798146/docs/learn/model-tool-calling-boundaries.md`（由综合 agent 产出）

## 2. 每篇笔记固定结构（顺序不可乱）

1. 标题 `# <主题> 学习笔记`
2. 元信息引用块（> 号），至少含：
   - 文档来源（本地绝对路径 或 在线 URL 列表）
   - 精读方式（HTML转文本 / web_fetch 在线抓取 / 两者）
   - 精读日期 2026-10-01、作者 = 本 agent 名
   - 覆盖边界：a-f（见 §4）
3. `## 1. 原文事实清单`：按小节组织，**每条关键事实必须带出处**；优先表格（| 事实 | 出处 | 要点 |）
4. `## 2. 对 MBDSDR 工具化实现的启示`：具体到组件/代码位（如 src/ai/agent.cpp、llm_client.cpp、llm_worker、task_orchestrator、Flutter 端 api_client、src/core/tokens.h），可给伪代码 / 字段级建议
5. `## 3. 与能力边界映射`：表格（边界 | 文档依据 | 实现要点）
6. `## 4. 红线与自检记录`：逐条列（文件可打开 ✓ / 引用的每个 § 或 URL 都在原文找到 ✓ / 覆盖了任务指定范围 ✓ / 无编造 ✓ / 「推断」处 = 原文无直接证据）

## 3. 出处规范（硬性）

- 本地 HTML：先转纯文本存 `sources/<slug>.txt`，引用格式 `sources/<slug>.txt:<行号>` 或 `sources/<slug>.txt §<章节标题>`，并附原始 HTML 绝对路径
- 在线抓取：引用 `URL §<章节标题>`，并**把抓到的正文落盘成 sources/ 下文本文件**以便复核
- 每条断言必须可复核；禁止无出处断言；拿不准标「推断」

## 4. 六条能力边界（MainAgent 已提炼，须从原文验证并补充细节）

- a) Function calling = OpenAI 兼容标准循环：请求 tools=[{type:function,function:{name,description,parameters(JSON Schema)}}] → 模型返回 message.tool_calls[].function.{name,arguments} → 执行 → 回传 {role:"tool",content,tool_call_id} → 直到 finish_reason=stop
- b) Interleaved Thinking：思考模型返回 reasoning_content，**必须原样逐字保留并回传**（工具调用前 / 多次调用之间 / 工具结果后所有片段）；流式是 delta.reasoning_content；丢弃 → 多步工具链崩、缓存劣化
- c) 模型生成的 arguments 不保证合法 JSON、可能虚构 schema 外参数（MiMo 原文明示）；runtime 必须 schema 校验 + 参数白名单，禁 eval 直执
- d) tool_choice 后端强制 auto（MiMo），别指望强制指定工具
- e) 思考模式 + 工具调用可能不稳定（tool_calls 混入 reasoning_content 是不稳定信号），需按模型配置 thinking
- f) MiMo 另支持 Anthropic 兼容协议：tool_use/tool_result 内容块、disable_parallel_tool_use 控制并行

## 5. 篇幅与语言

- 每篇 150–500 行 markdown，密度优先，拒绝流水账；多用表格
- 全中文（协议字段名 / 代码保留原文）

## 6. 阅读纪律（硬性）

- **必须全文精读**：本地大 HTML 先转文本逐节读完；web_fetch 用分页模式读完（pagination.offset 续读，直到 end_offset >= total_length）
- 禁止只看 README / 摘要 / 目录 / 标题
- 禁止编造文档中不存在的内容；引用的每一条都能在原文找到
- 无 key 不真调 API；不做任何需要 API key 的动作

## 7. 示例（既有格式基准）

- `/home/user/Doubao/chats/38438160041798146/docs/learn/port_report.md`（表格 + file:line 引用 + 红线）
- `/home/user/Doubao/chats/38438160041798146/docs/learn/satnogs_client.md`（元信息头 + 标注约定）
