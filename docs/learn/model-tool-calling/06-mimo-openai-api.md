# 小米 MiMo OpenAI 兼容 Chat API 学习笔记

> **文档来源**：
> - 主页面（在线抓取，全量 3811 字符已读完）：https://mimo.mi.com/docs/api/chat/openai-api
> - 补充1（函数调用请求示例 + thinking+tool 稳定性警告）：https://mimo.mi.com/docs/en-US/quick-start/faq/api-integration
> - 补充2（role=tool 回传 + 多轮工具调用完整循环示例）：https://mimo.mi.com/docs/zh-CN/usage-guide/passing-back-reasoning_content
> - 本地壳文件（已核实为空壳，无正文）：`/home/user/Doubao/chats/38438160041798146/repos/model-docs/xiaomi-mimo/api_chat_openai-api.html`
>
> **精读方式**：web_fetch 在线抓取（pagination 模式 offset=0 一次读完全部，end_offset=3811 ≥ total_length=3811）；disable_cache=true 重试结果一致。主页面"函数调用"标签页内容为 JS 渲染，静态 HTML 未包含，经 general_search 定位到 FAQ 与 passing-back 两页补充。
> **精读日期**：2026-10-01　**作者**：s_000c4GAYYm6
> **覆盖边界**：a) Function calling OpenAI 兼容循环 ✓　c) arguments 不保证合法 JSON ✓　d) tool_choice 后端强制 auto ✓（同时覆盖 b reasoning_content 回传、e thinking+tool 不稳定）
> **本地壳性质记录**：47 行纯 SPA 引导页（`<div id="root"></div>` + rspack runtime + chunk 加载清单），title="Xiaomi MiMo Home"，与另外两个克隆 HTML md5 相同（c1000a79650eeee0895cb00ca78619b3），正文完全由前端 JS 渲染，本地无任何 API 契约文本。

---

## 1. 原文事实清单

### 1.1 Endpoint 与认证

| 事实 | 出处 | 要点 |
|---|---|---|
| 聊天补全 endpoint | https://mimo.mi.com/docs/api/chat/openai-api §请求地址 | `POST https://api.xiaomimimo.com/v1/chat/completions` |
| 认证方式一 | 同上 §请求头 | Header `api-key: $MIMO_API_KEY` + `Content-Type: application/json` |
| 认证方式二 | 同上 §请求头 | Bearer 鉴权（即 `Authorization: Bearer $MIMO_API_KEY`） |
| 模型列表 | 同上 §请求体 model | `mimo-v2.6-flash` / `mimo-v2.6-pro` / `mimo-v2.6-pro-ultraspeed` / `mimo-v2.5-pro` / `mimo-v2.5` |
| 流式开关 | 同上 §请求体 stream | `stream: true` 时走 SSE（server-sent events） |
| thinking 非标准参数 | passing-back-reasoning_content §调用示例 | "thinking 字段并非 OpenAI 标准参数。通过 OpenAI Python SDK 传入思考相关参数时，需将其置于 extra_body 中传递。" |

### 1.2 tools 请求格式

| 字段 | 类型 | 必选 | 出处 | 约束/说明 |
|---|---|---|---|---|
| `tools` | array | — | openai-api §请求体 tools | 模型可能调用的工具列表，目前仅支持函数 |
| `tools[].type` | string | 必选 | 同上 | 仅支持 `"function"` |
| `tools[].function` | object | 必选 | 同上 | 函数工具对象 |
| `tools[].function.name` | string | 必选 | 同上 | `a-z/A-Z/0-9/_/-`，长度 1–64 |
| `tools[].function.description` | string | 否 | 同上 | 供模型判断何时/如何调用 |
| `tools[].function.parameters` | object | 否 | 同上 | JSON Schema 描述；省略 = 空参数列表 |
| `tools[].function.strict` | boolean | 否(默认false) | 同上 | true 时严格遵循 parameters 模式，仅支持 JSON Schema 子集 |

官方 tools 数组结构（FAQ 示例逐字）：
```json
"tools": [{
  "type": "function",
  "function": {
    "name": "get_current_weather",
    "description": "Get the current weather in a given location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
      },
      "required": ["location"]
    }
  }
}]
```
出处：FAQ api-integration §How to make multi-turn tool calls in thinking mode。

### 1.3 响应 message.tool_calls[] 结构与 arguments 格式

| 字段 | 出处 | 说明 |
|---|---|---|
| `choices[].message.tool_calls` | openai-api §Chat 响应对象 | array，函数调用启动后返回待调用工具及参数，**可包含一个或多个** |
| `tool_calls[].id` | 同上 | string，工具调用 ID（回传时作 tool_call_id） |
| `tool_calls[].type` | 同上 | string，目前仅 `"function"` |
| `tool_calls[].function.name` | 同上 | string，要调用的函数名 |
| `tool_calls[].function.arguments` | 同上 | string，**格式为 JSON**（注意：是字符串，需客户端自行 parse） |
| 流式 delta.tool_calls | openai-api §Chat 响应 chunk | 多了 `delta.tool_calls[].index`（从 0 开始），arguments 以增量 string 分片到达 |
| `finish_reason` | 同上 | 模型调用工具时为 `"tool_calls"`；自然停止为 `"stop"` |

### 1.4 【逐字原文】arguments 警告

> "模型生成的用于调用函数的参数，格式为 JSON。**请注意，模型生成的内容并非并非总能保证是有效的 JSON，且可能会虚构出函数模式中未定义的参数。在调用函数之前，请在代码中对这些参数进行验证。**"

出处：openai-api §choices.message.tool_calls.function.arguments（非流式）；流式 chunk 的 `choices.delta.tool_calls.function.arguments` 处**重复了完全相同的警告原文**。

### 1.5 【逐字原文】tool_choice 取值与后端强制 auto

> "控制模型如何选择工具。
> **注意：当 `tool_choice` 传入非 `auto` 值时，后端会默认移除该字段，模型响应行为仍等同于 `auto` 模式（该逻辑保留调整的可能性）。**
> 可选值：`auto`"

出处：openai-api §请求体 tool_choice。即官方枚举里 tool_choice 只有 `auto` 一个合法值；传 `none` / `required` / `{"type":"function","function":{"name":"..."}}` 这类 OpenAI 标准强制值，后端静默丢弃、按 auto 跑。

### 1.6 并行工具调用行为与流式支持

| 事实 | 出处 | 要点 |
|---|---|---|
| 支持并行函数调用 | passing-back §示例输出 第一轮 Request 1-1 | 单次响应 tool_calls 数组同时含 `get_current_weather` + `get_time` 两个调用；模型 reasoning 自述 "I can call both in parallel" |
| 客户端逐个执行+逐个回传 | passing-back §run_turn 代码 | `for tool_call in assistant_message.tool_calls:` 逐个执行，每个结果各自 append 一条 role=tool 消息 |
| 流式支持工具调用 | openai-api §Chat 响应 chunk | delta.tool_calls 存在，带 index 分片；SSE 末包 choices=[] 时携带 usage |
| 流式 reasoning_content | passing-back §流式响应 | 先 delta.reasoning_content 逐步吐思考，再 delta.content 吐答案；首包 reasoning_content=null |

### 1.7 role=tool 回传格式与 tool_call_id 匹配

【逐字原文示例】（passing-back §run_turn）：
```python
for tool_call in assistant_message.tool_calls:
    func_name = tool_call.function.name
    func_args = json.loads(tool_call.function.arguments)
    result = TOOL_MAP[func_name](**func_args)
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": result
    })
```
| 要点 | 出处 |
|---|---|
| role 取值 `"tool"` | 同上 |
| `tool_call_id` 必须等于对应 `tool_calls[i].id` | 同上（一一配对，并行时各配各的） |
| `content` 为工具执行结果字符串 | 同上 |
| assistant 消息需原样回传（含 tool_calls + reasoning_content） | passing-back §多轮对话回传要求："必须完整回传 reasoning_content 字段，否则 API 将返回 400 错误" |

### 1.8 【逐字原文】reasoning_content 回传硬性要求

> "在 Agent 类产品的多轮会话中开启深度思考，且历史会话中存在工具调用时，后续所有 user 交互轮次中回传的 assistant 如果包含了工具调用，**必须完整回传 `reasoning_content` 字段，否则 API 将返回 400 错误**。正确回传方式请参考调用示例的'深度思考下的多轮工具调用'。历史 `reasoning_content` 一旦缺失，模型上下文将不完整，可能表现出指令遵循下降，幻觉增多等现象。"

出处：passing-back §多轮对话回传要求。

### 1.9 【逐字原文】thinking + tool_calls 不稳定警告

> "tool_calls 出现在 reasoning_content 是模型在调 tool 的时候开启 thinking 导致的不稳定，输出不完整；建议调用 tool calls 的时候关闭 thinking，并且参考 模型超参 进行设置，来获得更稳定且更好的使用体验。"

出处：zh-CN FAQ §Why are tool_calls sometimes included in the reasoning_content field。

### 1.10 官方请求/响应示例（原样记录）

**基础调用 curl**（openai-api §示例）：
```bash
curl --location --request POST 'https://api.xiaomimimo.com/v1/chat/completions' \
--header "api-key: $MIMO_API_KEY" \
--header "Content-Type: application/json" \
--data-raw '{
 "model": "mimo-v2.6-pro",
 "messages": [
   {"role": "system", "content": "You are MiMo, an AI assistant developed by Xiaomi. Today is date: Tuesday, December 16, 2025. Your knowledge cutoff date is December 2024."},
   {"role": "user", "content": "please introduce yourself"}
 ],
 "max_completion_tokens": 1024, "temperature": 1.0, "top_p": 0.95,
 "stream": false, "stop": null, "frequency_penalty": 0, "presence_penalty": 0,
 "thinking": {"type": "disabled"}
}'
```

**基础调用响应**（openai-api §示例）：
```json
{
    "id": "6272d55f-a84e-4664-9db1-8462899db7dc_abdfaf015d1a4623b2a4f9b99179c2f9",
    "choices": [{
        "finish_reason": "stop", "index": 0,
        "message": {
            "content": "Hi there! I'm MiMo, a friendly AI assistant created by the Xiaomi LLM Core Team...",
            "role": "assistant", "tool_calls": null
        }
    }],
    "created": 1790006669, "model": "mimo-v2.6-pro", "object": "chat.completion",
    "usage": {"completion_tokens": 34, "prompt_tokens": 57, "total_tokens": 91,
              "completion_tokens_details": {"reasoning_tokens": 0},
              "prompt_tokens_details": {"cached_tokens": 0}}
}
```

**函数调用并行输出**（passing-back §示例输出，证实并行）：
```
tool_calls: [
  ChatCompletionMessageFunctionToolCall(id='call_01e402113df94ebfb85e3056',
    function=Function(arguments='{"location": "Beijing"}', name='get_current_weather'), type='function'),
  ChatCompletionMessageFunctionToolCall(id='call_f2bf1ab9f73842baa64e93d3',
    function=Function(arguments='{"timezone": "Asia/Shanghai"}', name='get_time'), type='function')
]
```

### 1.11 与 OpenAI 标准 / 硅基流动的差异

| 维度 | MiMo 实际行为 | 出处 | 与 OpenAI 标准差异 |
|---|---|---|---|
| tool_choice | 仅 `auto` 合法；传其他值后端静默移除、按 auto | openai-api §tool_choice | OpenAI 支持 `none`/`required`/指定函数；MiMo 不支持强制调用 |
| thinking 参数 | 自定义 `thinking.type`，非 OpenAI 标准，SDK 需走 extra_body | passing-back §调用示例 | OpenAI 无此字段（o系列走 reasoning_effort） |
| thinking 下 temperature/top_p | 强制 1.0 / 0.95，传了不生效 | openai-api §thinking/temperature | OpenAI 思考模型可调采样参数（推断：MiMo 更严格） |
| reasoning_content 回传 | 历史含工具调用时**必须**逐字回传，否则 400 | passing-back §回传要求 | OpenAI 标准不要求回传 reasoning（推断：MiMo 上下文拼接更脆弱） |
| arguments 可靠性 | 官方明示不保证合法 JSON、可能虚构参数 | openai-api §arguments | OpenAI 文档一般不做此警告（推断：MiMo 模型更易生成坏 JSON） |
| 额外协议 | 另支持 Anthropic 兼容 `/anthropic/v1/messages` | FAQ §OpenAI vs Anthropic | OpenAI 官方无此协议 |
| developer 角色 | messages 支持 `role: developer` | openai-api §messages | 与 OpenAI 一致（OpenAI 2025 起支持 developer） |
| 联网搜索工具 | 内置 web_search tool（非自定义 function） | openai-api §tools/Web search tool | OpenAI 无内置联网工具 |

---

## 2. 对 MBDSDR 工具化实现的启示

> 目标：MBDSDR 的 runtime = LLM 大脑 ↔ SDR 能力函数集 的中介。下列建议具体到 `src/ai/runtime`。

### 2.1 arguments 校验器（最高优先级，对应边界 c）

MiMo 官方**逐字警告** arguments 不保证合法 JSON、可能虚构 schema 外参数。runtime 绝不能 `eval` 或直接 `json.loads` 后透传给 SDR 硬件。建议在 `src/ai/runtime/tool_arguments_validator.py`（或 C++ 侧 `llm_client` 解析层）落地：

```
收到 message.tool_calls[i]：
  1. name 白名单查表（TOOL_MAP 注册表）—— 未知函数名直接拒绝，回 role=tool 告知"无此工具"
  2. raw_args = tool_calls[i].function.arguments  (string)
  3. 尝试 json.loads(raw_args)；失败 → 不崩溃，回 tool 消息反馈"arguments 不是合法 JSON：<原文片段>"，让模型自纠
  4. parse 成功后按该函数注册的 JSON Schema 做校验：
     - required 字段缺失 → 拒绝并反馈缺哪个
     - schema 外多余字段 → 丢弃（白名单），不报错也不传给硬件
     - 类型不符（如 frequency 传成字符串）→ 尝试温和转换，失败则反馈
  5. 通过后才 TOOL_MAP[name](**validated_args)
```
红线：**禁 eval、禁直接 unpack 未校验 dict 到硬件调用**。SDR 调谐频率/采样率是硬件参数，恶意或虚构参数可能驱动错误硬件动作。

### 2.2 tool_choice 处理（对应边界 d）

MiMo 后端只认 `auto`，传 `required`/`{"function":{...}}` 会被静默丢弃。runtime 层建议：
- `src/ai/runtime/llm_client` 请求构造时：**只发送 `tool_choice: "auto"`**（或省略，让后端走默认）。
- 不要在客户端代码里写"强制模型调某个 SDR 工具"的逻辑——MiMo 不支持，写了也被后端吃掉，误导调试。
- 若产品确实需要"必须调工具"的场景（如用户点了扫描按钮但模型可能直接闲聊），改用 **prompt 层约束**（system message 写明"必须先调用 scan_band 工具再回答"），而非依赖 tool_choice。
- 在配置注释里标注："MiMo tool_choice=auto only，后端静默丢弃其他值（原文保留调整可能性，未来可能放开）"。

### 2.3 reasoning_content 逐字保留（对应边界 b）

- runtime 维护 messages 列表时，`assistant` 消息对象必须**整体 append**（`messages.append(assistant_message)`），不能只存 content。assistant_message 内含 reasoning_content + tool_calls + role，丢任何一个都会触发 400 或幻觉。
- 流式场景：`delta.reasoning_content` 分片要累积保存到该轮 assistant 的 reasoning_content 字段，与 content 分开拼接；回传时原样带回去。
- 注意 passing-back 原文的硬性 400 条件："历史会话中存在工具调用时，后续 user 轮次回传的 assistant 若含工具调用，必须完整回传 reasoning_content"。runtime 不能做"省 token 裁剪 reasoning"的优化。

### 2.4 thinking + tool 稳定性（对应边界 e）

- 官方建议：**调用工具时关闭 thinking**（`thinking.type=disabled`）以获得更稳定的 tool_calls 输出。
- MBDSDR 建议按场景分配置：纯对话闲聊 → thinking=enabled；进入工具调用循环（调谐/扫描/录制）→ thinking=disabled 或按模型配置项切换。
- 防御性解析：流式/非流式都要检查"tool_calls 是否混入了 reasoning_content 文本里"——若模型把函数调用写在 reasoning_content 字符串中而非独立 tool_calls 字段，属于不稳定信号，runtime 应记录日志并按 disabled-thinking 重试。

### 2.5 role=tool 回传与循环终止

- 循环结构对齐 passing-back 官方示例：
  ```
  while True:
    resp = llm.chat(messages, tools, tool_choice="auto", thinking=...)
    messages.append(resp.choices[0].message)          # 整体回传
    if not message.tool_calls: break                   # finish_reason=stop → 最终答案
    for tc in message.tool_calls:                      # 并行调用逐个执行
      args = validate(tc.function.name, tc.function.arguments)   # 见 2.1
      result = execute(args)
      messages.append({"role":"tool","tool_call_id":tc.id,"content":result})
  ```
- `tool_call_id` 必须与 `tc.id` 严格配对（并行时 N 个 tool_calls → N 条 role=tool），顺序不敏感但 id 不能错配。
- 终止条件：`finish_reason == "stop"`（模型不再请求工具）。另需设最大轮次上限（防模型工具死循环），达上限强制 break。

### 2.6 流式 tool_calls 拼接

- 流式 delta.tool_calls 带 `index`（从 0 开始），同一 index 的 function.name / function.arguments 是分片到达的，runtime 需按 index 聚合：name 首包确定，arguments 逐片 append，收齐后再进校验器。
- SSE 末包 `choices:[]` 携带 usage，可用于 token 计费统计。

---

## 3. 与能力边界映射

| 边界 | 文档依据 | MBDSDR 实现要点 |
|---|---|---|
| a) OpenAI 兼容 function calling 循环 | openai-api §tools/§message.tool_calls + passing-back §run_turn | 标准 tools=[{type:function,function:{name,description,parameters}}] → tool_calls[] → role=tool 回传 → 直到 stop |
| b) reasoning_content 逐字保留回传 | passing-back §多轮对话回传要求（缺则 400） | assistant 整体 append，delta.reasoning_content 累积，禁裁剪 |
| c) arguments 不保证合法 JSON/虚构参数 | openai-api §arguments【逐字警告】 | runtime 必做 schema 校验+白名单，禁 eval，坏 JSON 回喂模型自纠 |
| d) tool_choice 后端强制 auto | openai-api §tool_choice【逐字原文】 | 只发 auto，不靠强制调用，改 prompt 约束 |
| e) thinking+tool 不稳定 | FAQ §tool_calls in reasoning_content | 工具循环建议 thinking=disabled；检测混入并重试 |
| f) Anthropic 兼容协议 | FAQ §OpenAI vs Anthropic 差异 | 本笔记聚焦 OpenAI 协议；Anthropic 协议另文（tool_use/tool_result 内容块） |

---

## 4. 红线与自检记录

- [x] 文件可打开：sources/mimo-openai-api.txt 已落盘，笔记 06-mimo-openai-api.md 可写入
- [x] 引用的每个 §/URL 均在原文找到：endpoint/认证/tools/tool_calls/arguments 警告/tool_choice auto/role=tool/并行/流式 均来自上述三个 URL 实抓内容
- [x] 覆盖任务指定范围 a/c/d（及 b/e）：endpoint 结构认证、tools 格式、tool_calls 结构、arguments 警告逐字、tool_choice 逐字、并行+流式、role=tool 匹配、官方示例、与标准差异 —— 全列于 §1
- [x] 无编造：凡原文未直接给出处均标「推断」（如 2.x 的 C++/Python 文件名是落地建议而非文档事实；与 OpenAI 差异表中标"推断"行）
- [x] 本地壳已如实记录：47 行 SPA 空壳，md5 同另两文件，正文不在本地
- [x] 在线获取路径如实记录：主页面 web_fetch 一次读全（3811 字符）；"函数调用"标签页为 JS 渲染未在静态 HTML，经 general_search 定位 FAQ + passing-back 两页补充，并在元信息标注
- [x] 未做任何需要 API key 的真实调用；未抓无关页面；未改代码、未做 git
- [x] 篇幅与表格密度符合规范

**推断声明**：
- 「2.x 节具体文件名（tool_arguments_validator.py 等）」为 MBDSDR 落地建议，非 MiMo 文档内容。
- 差异表中标"推断"的行（OpenAI 是否做 JSON 可靠性警告、reasoning 回传是否为 MiMo 独有），是基于常识对照，未在 OpenAI 官方文档逐条核验。
- 主页面"函数调用"标签页的**独立响应示例**（带 tool_calls 的真实返回 JSON）未在静态 HTML 中抓到；函数调用侧的证据取自 FAQ 请求示例 + passing-back 的 Python 输出示例（后者含真实 tool_calls 结构），已如实标注来源。
