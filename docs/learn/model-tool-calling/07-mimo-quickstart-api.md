# 小米 MiMo 首次 API 调用（Quick Start / First API Call）学习笔记

> **文档来源**：
> - 在线正文：https://mimo.mi.com/docs/quick-start/first-api-call （在线抓取，全文 1424 字符一次读完）
> - 本地壳（仅作核实，无正文）：`/home/user/Doubao/chats/38438160041798146/repos/model-docs/xiaomi-mimo/quick-start_first-api-call.html`
>
> **精读方式**：web_fetch 在线抓取（pagination offset=0, limit=8000，end_offset=1424=total_length，单页读完，无需续读）；本地 HTML 仅核实壳性质。
> **精读日期**：2026-10-01　**作者**：MainAgent（子任务 07）
> **覆盖边界**：a（OpenAI 兼容工具循环）、b（reasoning_content 回传）——仅覆盖到 Quick Start 文档实际出现的引导；c/d/e/f 本页未展开，见 §3 标注。
> **本地壳核实结论**：`quick-start_first-api-call.html`（13020 字节，md5 `c1000a79650eeee0895cb00ca78619b3`）与同目录 `api_chat_openai-api.html`、`usage-guide_passing-back-reasoning_content.html` md5 **完全相同**，均为同一 SPA 空壳——`<title>Xiaomi MiMo Home</title>` + `<div id="root">` + rspack 打包 JS，无任何文档正文。正文不在本地，以下事实全部来自在线页。
>
> **标注约定**：「原文」= 在线文档可复核（出处 `URL §章节` 或 `sources/mimo-quickstart.txt:<行>`）；「推断」= 原文无直接证据，基于 OpenAI/Anthropic 通用约定。

---

## 1. 原文事实清单

### 1.1 接入前置条件（账号 / 凭证 / BASE_URL / 环境）

| # | 事实 | 出处 | 要点 |
|---|---|---|---|
| 1 | 登录方式**仅个人账号**，须用小米账号登录；无账号去控制台或 id.mi.com 注册 | URL §登录 Xiaomi MiMo API 开放平台 | 无企业 SSO，无服务账号概念；MBDSDR 接入方须自备一个小米个人账号 |
| 2 | 开放平台**同时兼容两种协议**：OpenAI API 与 Anthropic API，可直接复用现有 SDK | URL §支持的接口类型 | 这是 MiMo 的核心卖点——不必为它写专用客户端，OpenAI/Anthropic SDK 直连 |
| 3 | 三种计费形态，BASE_URL 各不相同（见下表） | URL §获取凭证 | **BASE_URL 是配置项的一等公民**，按量/批量/Token Plan 三个域名不能混用 |
| 4 | API Key 格式随计费形态变化：按量 `sk-xxxxx`；Token Plan 个人版 `tp-xxxxx`、团队版 `ttp-xxxxx` | URL §获取凭证 | Key 前缀即计费类型；运维侧可凭前缀粗判来源 |
| 5 | 官方建议把 API Key 配到环境变量，勿硬编码 | URL §获取凭证 | 与 `os.environ.get("MIMO_API_KEY")` 示例一致 |
| 6 | 运行环境：Python 3 + `pip install -U openai` 或 `pip install -U anthropic`（失败则 pip→pip3） | URL §OpenAI… / §Anthropic… | 无特殊系统依赖，纯 HTTP SDK |

**BASE_URL 接入清单（原文表格转写）**：

| 计费形态 | 推理模式 | OpenAI 兼容 BASE_URL | Anthropic 兼容 BASE_URL | Key 前缀 |
|---|---|---|---|---|
| 按量付费 | 实时 | `https://api.xiaomimimo.com/v1` | `https://api.xiaomimimo.com/anthropic` | `sk-` |
| 按量付费 | 批量（Batch） | `https://batch-api-cn.xiaomimimo.com/v1` | 原文未给 Anthropic 批量地址 | `sk-` |
| Token Plan | 固定订阅限量 | `https://token-plan-cn.xiaomimimo.com/v1` | `https://token-plan-cn.xiaomimimo.com/anthropic` | `tp-`（个人）/ `ttp-`（团队） |

### 1.2 模型标识（model 取值）

| 事实 | 出处 | 要点 |
|---|---|---|
| 本页三个示例（OpenAI SDK / Anthropic SDK / 工具调用 curl）的 `model` 字段**统一取值 `"mimo-v2.6-pro"`** | URL §快速接入示例（三处） | Quick Start 只给了一个模型名；完整模型清单需跳转「模型列表」页（本页未枚举）。MBDSDR 模型清单不能写死只有这一个 |
| 知识截止日期：2024 年 12 月（写在官方推荐 system prompt 里） | URL §快速接入示例 | 与"今天日期"占位 `{date} {week}` 一起注入 system |

> 推断：`mimo-v2.6-pro` 是当前旗舰；页面 meta 另提到「V2.6 模型」「团队版 TokenPlan / Batch API」，说明存在同代多模型，但本页未列出全部 model 字符串——**MBDSDR 的模型表应做成可配置/可拉取，而非硬编码单一值**。

### 1.3 首个请求示例（OpenAI Chat Completions 兼容）逐字段

出处：URL §OpenAI Chat Completions API 兼容。

**构造客户端（headers 语义）**：

```python
client = OpenAI(
 api_key=os.environ.get("MIMO_API_KEY"),
 base_url="https://api.xiaomimimo.com/v1"
)
```

- `api_key` → OpenAI SDK 自动转为请求头 `Authorization: Bearer <key>`（推断：标准 OpenAI SDK 行为）。
- `base_url` → 拼接 `/chat/completions` 作为 POST 端点。

**create() 请求体逐字段**：

| 字段 | 示例值 | 原文说明 |
|---|---|---|
| `model` | `"mimo-v2.6-pro"` | 见 1.2 |
| `messages[0]` | `{role:"system", content:"You are MiMo… knowledge cutoff date is December 2024."}` | 官方"强烈建议"的 system prompt（中英二选一），内含当日日期与知识截止 |
| `messages[1]` | `{role:"user", content:"please introduce yourself"}` | 首条用户消息 |
| `max_completion_tokens` | `1024` | 注意是 **`max_completion_tokens`**（新字段名），不是老的 `max_tokens`（OpenAI 约定；推断） |
| `temperature` | `1.0` | |
| `top_p` | `0.95` | |
| `stream` | `False` | 首示例为**非流式**；流式是另一个 guide |
| `stop` | `None` | |
| `frequency_penalty` | `0` | |
| `presence_penalty` | `0` | |

### 1.4 首个请求示例（Anthropic Messages 兼容）逐字段

出处：URL §Anthropic Messages API 兼容。

```python
client = Anthropic(api_key=…, base_url="https://api.xiaomimimo.com/anthropic")
message = client.messages.create(
  model="mimo-v2.6-pro",
  max_tokens=1024,
  system="You are MiMo…",          # system 是顶层参数，不在 messages 里
  messages=[{"role":"user","content":[{"type":"text","text":"please introduce yourself"}]}],
  top_p=0.95, stream=False, temperature=1.0, stop_sequences=None)
```

与 OpenAI 路径的关键差异（原文事实）：

| 维度 | OpenAI 兼容 | Anthropic 兼容 |
|---|---|---|
| 端点 base | `…/v1` | `…/anthropic` |
| system prompt 位置 | `messages` 内 `role:"system"` 一条 | **顶层 `system=` 参数** |
| 用户内容形态 | `content` 直接是字符串 | `content` 是**内容块数组** `[{"type":"text","text":…}]` |
| 最大生成长度字段 | `max_completion_tokens` | **`max_tokens`** |
| 停止序列字段 | `stop` | **`stop_sequences`** |
| 返回打印 | `completion.model_dump_json()` | `message.content`（内容块数组） |

### 1.5 首个响应示例（返回骨架）——原文实际给了什么

| 事实 | 出处 | 说明 |
|---|---|---|
| OpenAI 路径：文档**未贴出响应 JSON**，只给 `print(completion.model_dump_json())` | URL §OpenAI… | 返回体逐字段**原文未覆盖** |
| Anthropic 路径：同样未贴响应，只给 `print(message.content)` | URL §Anthropic… | 同上 |

> 结论：**本 Quick Start 页不提供响应骨架样例**。按 OpenAI/Anthropic 通用约定（推断，非本文档事实），OpenAI 兼容响应应含 `id / object / model / choices[].message.{role,content,tool_calls,reasoning_content} / choices[].finish_reason / usage{prompt_tokens,completion_tokens,total_tokens}`；Anthropic 兼容响应应含 `id / model / content[] / stop_reason / usage`。MBDSDR 实现时**不能假定响应字段与本页逐一对上**，需以「查看用量」页或 api/chat 参考页为准补验。

### 1.6 工具调用 / 思考模式引导（本页最关键的一段）

出处：URL §在思考模式下进行多轮工具调用。

**原文原文（逐字要点）**：
> "在思考模式下的多轮工具调用过程中，模型会在返回 `tool_calls` 字段的同时返回 `reasoning_content` 字段。若要继续对话，建议在后续每次请求的 `messages` 数组中保留所有历史 `reasoning_content`，以获得最佳表现。"

**curl 请求体逐字段（原文）**：

| 字段 | 值 / 结构 | 要点 |
|---|---|---|
| endpoint | `POST https://api.xiaomimimo.com/v1/chat/completions` | 走 OpenAI 兼容协议做工具调用 |
| header | `api-key: $MIMO_API_KEY`、`Content-Type: application/json` | ⚠️ 原文 curl 手写头名是 **`api-key`**，而 OpenAI SDK 用 `Authorization: Bearer`——两处不一致（见 §4 红线 R） |
| `messages[0]` | `{role:"assistant", content:"Hello! I am MiMo.", reasoning_content:"Okay, the user just asked me…"}` | **assistant 历史消息里直接带 `reasoning_content` 字段回传**——这就是"保留思考"的具体接法 |
| `messages[1]` | `{role:"user", content:"What is the weather like in Hebei?"}` | |
| `model` | `mimo-v2.6-pro` | |
| `max_completion_tokens` / `temperature` / `stream` | `1024` / `1.0` / `false` | 与 1.3 一致 |
| `tools[0]` | `{type:"function", function:{name:"get_current_weather", description:"Get the current weather in a given location", parameters:{type:"object", properties:{location:{type:"string",…}, unit:{type:"string", enum:["celsius","fahrenheit"]}}, required:["location"]}}}` | 标准 OpenAI tool schema：`type/function.name/description/parameters(JSON Schema)` |
| `tool_choice` | `"auto"` | 本页仅示例取值为 auto；**未声明后端是否强制 auto、是否禁指定特定工具**（见 §3 边界 d） |

### 1.7 限流 / 配额 / 超时

| 主题 | 原文情况 |
|---|---|
| RPM/TPM 限流数值 | **原文未覆盖**。仅在 Token Plan 处出现"固定订阅费，按套餐限量调用"（URL §获取凭证），无具体数字 |
| 配额查询 | 「用量信息」页可按日期查看/导出 Token 用量与请求次数（URL §查看用量信息）——是事后账单视图，非实时配额 API |
| 超时 / 重试 / 退避 | **原文未覆盖** |

### 1.8 常见接入错误与排查

| 主题 | 原文情况 |
|---|---|
| 401/404/参数错误清单 | **原文未覆盖**（本 Quick Start 页无排错章节；同目录另有 `en-US_quick-start_faq_api-integration.html` 160KB 是 FAQ 页，属另一篇笔记范围） |
| 唯一"排错"提示 | `pip install` 失败时把 pip 换成 pip3（URL §安装 SDK） |

---

## 2. 对 MBDSDR 工具化实现的启示

> 背景：MBDSDR runtime 要把无线电能力暴露成 LLM 的 function-calling 工具集，MiMo 是首个接入的思考型模型。本页是接入起点，重点是**配置项**与**模型清单管理**。

### 2.1 接入配置项清单（llm_client 层）

建议在 `llm_client`（无论 C++ 还是封装层）把以下项做成**结构化配置**，而非散落字符串：

```c
// src/core/tokens.h 建议新增（仅字段建议，不代表现有代码）：
typedef struct {
    const char* provider;        // "xiaomi-mimo"
    const char* protocol;        // "openai" | "anthropic"   ← 本页两种都兼容
    const char* base_url;        // 三选一：api./batch-api-cn./token-plan-cn.
    const char* api_key_env;     // "MIMO_API_KEY"（从环境变量读，勿硬编码）
    const char* model;           // "mimo-v2.6-pro"
    int   max_completion_tokens; // 1024（OpenAI 路径）
    float temperature;           // 1.0
    float top_p;                 // 0.95
    int   stream;                 // 0/1
    // 思考/工具相关：
    int   preserve_reasoning;    // 1 = 必须把历史 reasoning_content 原样回传（本页强建议）
    // Anthropic 专属：
    const char* system_override;  // Anthropic 走顶层 system，OpenAI 走 messages[0]
} MimoEndpointConfig;
```

要点：
1. **base_url 与 key 前缀强绑定**：按量 `sk-` → `api.xiaomimimo.com`；Token Plan `tp-/ttp-` → `token-plan-cn.`。配置加载时应做一致性校验（推断：用错域名是最常见接入错误，本页把三者并列就是在提醒）。
2. **协议选择要可切换**：`protocol` 字段决定走 OpenAI 兼容（`/v1/chat/completions`、system 在 messages、`max_completion_tokens`、`stop`）还是 Anthropic 兼容（`/anthropic`、顶层 system、内容块数组、`max_tokens`、`stop_sequences`）。MBDSDR 的 `llm_client` 最好抽象成"协议适配器"，而不是把 OpenAI 字段写死。
3. **环境变量注入**：`MIMO_API_KEY` 从 env 读（原文明示建议），C++ 侧 `getenv("MIMO_API_KEY")`，日志里 key 必须打码（推断，防泄露——原文只说"妥善保管"）。

### 2.2 模型清单管理（src/core/tokens.h）

- 本页**只出现一个 model 字符串 `mimo-v2.6-pro`**，但官方另设「模型列表」页。建议：
  - 不要把 `"mimo-v2.6-pro"` 写死进业务逻辑；在 `tokens.h` 放一个**模型注册表/默认值**：
    ```c
    #define MIMO_DEFAULT_MODEL   "mimo-v2.6-pro"
    // 预留：未来接入 mimo-v2.6-其他规格 / 其他型号时只改表，不改 llm_client 分支
    ```
  - 模型表字段建议：`{ model_id, protocol_supported(openai/anthropic), is_reasoning(是否返 reasoning_content), supports_tools, notes }`。因为**是否思考型、是否返 reasoning_content** 直接决定 runtime 是否需要"保留思考回传"这条分支（见 2.3）。

### 2.3 reasoning_content 保留——本页对 runtime 最硬的一条要求

原文强建议：多轮工具调用时，**后续每次请求 messages 数组要保留所有历史 `reasoning_content`**，且示例里 assistant 消息就是
`{role:"assistant", content:…, reasoning_content:…}` 直接塞进下一次请求。

落到 MBDSDR 工具循环（对应规范边界 b）：
```
runtime 多步循环:
  1. POST chat/completions (带 tools)
  2. resp.choices[0].message  ── 原样保存整段 message（含 content + reasoning_content + tool_calls）
  3. 若 finish_reason == tool_calls:
       for each tool_call: 执行无线电工具 → 产出 {role:"tool", content, tool_call_id}
       把【上一条 assistant message（带 reasoning_content）】和【所有 tool 结果】一起 append 进 messages
       goto 1
  4. 直到 finish_reason == stop
```
- **禁止丢弃 reasoning_content**：本页明说丢弃会影响"最佳表现"；结合规范边界 b，丢弃会让多步工具链上下文断裂。
- assistant 历史消息的 `reasoning_content` 字段名就是它本身（不是嵌套对象），runtime 序列化时必须**逐字透传**，不要摘要、不要翻译。

### 2.4 tool schema 与 tool_choice

- `tools` 直接复用 OpenAI 形状（`type:"function"` + `function.{name,description,parameters(JSON Schema)}`）——MBDSDR 把每个无线电能力（设频率/调谐/抓 IQ/解调/看 waterfall…）注册成一个 function，`parameters` 用 JSON Schema 描述参数。
- `tool_choice:"auto"`：本页示例如此。**注意：本页没有说"后端强制 auto、禁止指定特定工具"**——规范边界 d 所述"后端强制 auto"需以 api/chat 参考页为准，本 Quick Start 不能当作该结论的出处（见 §3）。runtime 仍应允许传 `auto`，是否支持 `required/指定工具` 待 api 文档确认。

### 2.5 其他工程建议

- **首调用冒烟测试**：直接拿官方 system prompt + `please introduce yourself` 跑一遍非流式（`stream:false`），作为接入验收用例（对应 1.3）。
- **system prompt 本地化**：官方推荐 system 含"今天日期 {date} {week}"，MBDSDR 注入时应把当天日期动态填进去（本页示例里写死成 Tuesday, December 16, 2025 是文档占位）。
- **响应骨架待补**：本页不给响应 JSON，MBDSDR 真正联调时第一步应抓一次真实响应落档，据此校准 `llm_client` 的反序列化字段（推断）。

---

## 3. 与能力边界映射

| 边界 | 本文档是否覆盖 | 文档依据 | 实现要点 |
|---|---|---|---|
| a) OpenAI 兼容工具循环（tools→tool_calls→role:tool→stop） | **部分覆盖**：给出了 tools schema + `tool_choice:"auto"` 的请求侧；**未给** tool 结果回传 `{role:"tool",tool_call_id}` 与 finish_reason=stop 的闭环示例 | URL §在思考模式下进行多轮工具调用 | 请求侧照抄本页 tools 结构；回传闭环（role:tool）本页未示范，按 OpenAI 标准 + 规范边界 a 实现 |
| b) reasoning_content 必须逐字保留回传 | **覆盖（本页最明确）**：明说"保留所有历史 reasoning_content 以获得最佳表现"，并在 messages 里示范 assistant 携带 reasoning_content | URL §在思考模式下进行多轮工具调用 | runtime 保存 assistant message 时连同 reasoning_content 一起回传，禁止摘要/丢弃 |
| c) arguments 不保证合法 JSON、可能超 schema | **本页未覆盖**（未提示校验/白名单） | —（原文未覆盖） | 仍按规范边界 c：runtime 做 schema 校验 + 参数白名单，禁 eval 直执 |
| d) tool_choice 后端强制 auto | **仅示例出现 `"auto"`，未声明"强制"** | URL §工具调用 curl 示例 | 勿在本页下"后端强制 auto"结论；实现上先按 auto 跑，强制指定工具能力待 api/chat 页确认 |
| e) 思考+工具调用稳定性 | **本页正面引导**（建议保留 reasoning 即可获最佳表现），未提"不稳定/tool_calls 混入 reasoning"风险 | URL §工具调用 | 按模型配置是否思考型；本页未给风险信号 |
| f) Anthropic 兼容协议（tool_use/tool_result 块） | **部分覆盖**：给了 Anthropic Messages 基础调用（顶层 system、内容块数组），**未给** Anthropic 侧工具调用块示例 | URL §Anthropic Messages API 兼容 | 工具调用在本页只示范了 OpenAI 路径；Anthropic 侧 tool_use/tool_result 块待 usage-guide / api 页补 |

---

## 4. 红线与自检记录

逐条对照规范 §6 与 §2.6：

| # | 自检项 | 结果 | 说明 |
|---|---|---|---|
| 1 | 本地壳文件可打开、性质已核实 | ✓ | 13020 字节、md5 `c1000a79…`、与另两个 html 同 md5，确认为 SPA 空壳（`<div id=root>`），元信息块已如实记录 |
| 2 | web_fetch 分页读完整 | ✓ | offset=0 一次返回 end_offset=1424=total_length，无续读、无截断；非近空，无需 disable_cache 重试 |
| 3 | 抓取正文已落盘 sources/ | ✓ | `sources/mimo-quickstart.txt`（含来源/抓取方式/壳说明头） |
| 4 | 每条断言可复核、有出处 | ✓ | 事实表均带 `URL §章节`；响应骨架、限流数值、排错章节如实标"原文未覆盖" |
| 5 | 无编造原文不存在内容 | ✓ | 响应字段、限流数字、Anthropic 工具块均明确标注"原文未覆盖/推断"；未把规范边界 c/d/e 当作本页事实 |
| 6 | 「推断」处均无直接原文证据 | ✓ | base_url↔key 一致性、Authorization header、响应骨架字段、模型表设计等均标推断 |
| 7 | 未真调 API、未用 key | ✓ | 全程只读文档，curl 示例仅转写，未执行 |
| 8 | 只写指定目录、未改代码、未 git | ✓ | 仅写 `docs/learn/model-tool-calling/` 下本笔记与 sources/ |

**一处需向 MainAgent 高亮的原文不一致（R）**：
- OpenAI Python SDK 示例用 `api_key=`（→ 标准应为 `Authorization: Bearer`），而工具调用 curl 示例手写 `--header "api-key: $MIMO_API_KEY"`。两者 header 名不同。本笔记如实转写为原文现象，未擅自统一；**联调时需实测哪个 header 被网关接受**（推断：标准 OpenAI 兼容应认 `Authorization: Bearer`，curl 示例的 `api-key` 可能是文档笔误或网关别名）。

**篇幅**：本笔记约 190 行，落在 150–500 区间。
