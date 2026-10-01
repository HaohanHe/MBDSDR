# 硅基流动「流式输出（Stream Mode）」学习笔记

> 文档来源：本地 HTML `/home/user/Doubao/chats/38438160041798146/repos/model-docs/siliconflow/docs_userguide_capabilities_stream-mode.html`
> （Fumadocs 路由 `userguide/capabilities/stream-mode`，页面标题「流式输出」；204KB 中绝大部分为 Next.js/Fumadocs 站点导航与 RSC 数据壳，正文仅两小节）
> 精读方式：HTML → 纯文本（Python stdlib `html.parser`，取 `<main>…</main>` 切片，`<pre>` 代码块按原样合并以还原语法高亮拆散的 token），全文逐节读完。
> 转换产物：`sources/sf-stream-mode.txt`（173 行，供他人引用复核）
> 精读日期：2026-10-01；作者：MainAgent
> 覆盖边界：**b) 的流式侧**（`delta.reasoning_content`，本页有直接证据）；**a) 的流式侧**（流式 tool_calls 本页未讲，见 §1.4 缺口声明）；c/d/e/f 本页均未涉及。
> 标注约定：「原文」= `sources/sf-stream-mode.txt:<行号>` 可直接复核；「推断」= 本页无直接证据，依据 OpenAI 兼容协议常识，**必须**对照导航中独立的「创建对话请求（OpenAI）POST」API 手册页确认后方可入实现。

---

## 1. 原文事实清单

### 1.1 页面定位与覆盖范围（先划清本文讲什么、不讲什么）

| 事实 | 出处 | 要点 |
|---|---|---|
| 流式输出模式的官方定义 | `sf-stream-mode.txt:58` | 「流式输出模式让模型响应实时返回，适用于聊天、长文本生成等需要即时反馈的场景」 |
| 本页只有两节 | `sf-stream-mode.txt:172-173`（On this page 目录） | ①「在 python 中使用流式输出」（1.1 OpenAI 库 / 1.2 requests 库）；②「curl 中使用流式输出」。**没有**单独的 SSE 协议字段章节 |
| 本页展示的 delta 字段 | `sf-stream-mode.txt:84-88, 140-142` | 只有 `delta.content` 与 `delta.reasoning_content` 两个字段的读取示例 |
| 本页**未出现**的字段（全文关键词检索为 0） | 原始 HTML 全文 strip 标签后检索 | `tool_calls`、`finish_reason`、`usage`、`prompt_tokens`、`completion_tokens`、`arguments`、SSE `event:`/`id:` 行 —— 均为 0 次出现 |
| 字段级 schema 在别处 | `sf-stream-mode.txt:28` | 站点导航列出独立页「创建对话请求（OpenAI）POST」；本页为「使用指南/功能特性」快速入门，不是 API 参考 |
| 限流在别处 | `sf-stream-mode.txt:19` | 导航列出独立页「Rate Limits」（原文描述：速率限制与升级指南）；本页无限流/断线重连内容 |

### 1.2 流式开启：`stream` 参数（**双端都要开**，官方明示）

| 事实 | 出处 | 要点 |
|---|---|---|
| 请求体开关 | `sf-stream-mode.txt:76, 120, 165` | Python SDK `stream=True`；requests payload `"stream": True`；curl JSON `"stream": true` |
| **requests 必须第二次开 stream** | `sf-stream-mode.txt:103-104, 129` | 原文：「除了 payload 中的 stream 需要设置外，request 请求的参数也需要设置 stream = True，才能正常按照 stream 模式进行返回」；即 `requests.post(url, json=payload, headers=headers, stream=True)` |
| 端点 | `sf-stream-mode.txt:66, 110, 157` | `https://api.siliconflow.cn/v1`（SDK base_url）/ `https://api.siliconflow.cn/v1/chat/completions`（直连） |
| 示例模型 | `sf-stream-mode.txt:72, 113, 161` | Python 两例用 `Pro/zai-org/GLM-5.1`；curl 例用 `Qwen/Qwen2.5-72B-Instruct` |

> 对 MBDSDR 的直接含义：C++ 侧任何 HTTP 客户端（libcurl）必须 `CURLOPT_WRITEFUNCTION` 逐回调返回即吐，等价于 `stream=True`——不能等到响应体收完才解析。curl 侧等价物见 §1.6。

### 1.3 SSE 传输格式（requests 示例逐行抠字段）

官方唯一一段「裸协议」解析代码在 `sf-stream-mode.txt:135-148`，逐行拆解：

| 行号 | 代码 | 协议含义（原文直接展示的部分） |
|---|---|---|
| `:135` | `for chunk in response.iter_lines():` | 按**行**迭代响应体（不是按字节 chunk） |
| `:136` | `if chunk:` | 跳过空行（SSE 事件之间的空行分隔） |
| `:137` | `chunk_str = chunk.decode('utf-8').replace('data: ', '')` | 每行剥掉前缀 `data: `（注意：是 `data:` 后带一个空格） |
| `:138` | `if chunk_str != "[DONE]":` | 终止哨兵字面量就是 `[DONE]`（剥掉 `data: ` 后的裸文本） |
| `:139` | `chunk_data = json.loads(chunk_str)` | 除 `[DONE]` 外，每行 `data:` 载荷都是一个完整 JSON chunk |
| `:140` | `delta = chunk_data['choices'][0].get('delta', {})` | chunk 结构 = `{"choices": [{"delta": {...}}]}`；`delta` 可能缺省（用 `.get('delta', {})` 兜底） |
| `:141` | `content = delta.get('content', '')` | 正式回复增量字符串，缺省按空串 |
| `:142` | `reasoning_content = delta.get('reasoning_content', '')` | 思考链增量字符串，缺省按空串 |
| `:143-145` | `if content: ... full_content += content` | content **逐片追加累加**，片与片之间不加分隔符 |
| `:146-148` | `if reasoning_content: ... full_reasoning_content += reasoning_content` | reasoning_content 同样**逐片追加累加** |

由此可确认的 SSE 事实（原文直接演示）：

- 传输层是 HTTP 长连接 + 文本行流；每个事件 = 一行 `data: <json>`；事件间空行；最后一行 `data: [DONE]`。（`:135-138`）
- 官方示例**没有**处理 `event:`、`id:`、`retry:` 行——本页范围内只需要识别 `data: ` 前缀行。「推断」：OpenAI 兼容服务通常不发具名 event 行，但解析器应**忽略无法识别的行前缀**而非报错。
- `delta` 里某个字段「没出现」≠ 空串：官方用 `.get('delta', {})` / `.get('content', '')` 两级容错（`:140-142`），即**增量式**——每个 chunk 只带本次新生成的那部分字段。

### 1.4 本文档未覆盖的流式协议字段（缺口声明，禁止拿本笔记当依据去实现）

| 字段/语义 | 本页证据 | 状态 |
|---|---|---|
| `delta.tool_calls[]`（含 `index` / `id` / `type` / `function.name` / `function.arguments` 增量） | 0 次出现 | **未覆盖**；MBDSDR 所需的 tool_calls 增量拼接算法（见 §2.3）在本页无依据，属推断 |
| `choices[0].finish_reason`（`stop` / `tool_calls` / `length` 等） | 0 次出现 | **未覆盖**；官方示例连 `choices[0]` 的 `finish_reason` 键都没读（`:140` 只读了 `delta`） |
| `usage` / `stream_options.include_usage` | 0 次出现 | **未覆盖**；本页示例从不消费 token 计数 |
| 多工具并存（多个 tool_call 靠 `index` 区分） | 0 次出现 | **未覆盖** |
| 流式错误事件（HTTP 200 后在流里发 error chunk） | 仅 `:131,149-150`：非 200 状态码直接打印退出 | 200 之后流内错误格式本页未讲 |
| 心跳/保活/断线重试/超时 | 0 次出现 | **未覆盖** |

### 1.5 OpenAI SDK 用法要点（`sf-stream-mode.txt:80-100`）

| 事实 | 出处 | 要点 |
|---|---|---|
| 空 choices 跳过 | `:81-82` | `if not chunk.choices: continue`——存在 `choices` 为空的 chunk（推断：通常是末尾 usage chunk，本页未明说） |
| 属性安全读取 | `:86-88` | 注释原文：「使用 getattr 安全获取属性，如果属性不存在则返回 None，避免报错」；`getattr(delta, 'reasoning_content', None)`、`getattr(delta, 'content', None)` |
| 思考过程单独上色 | `:90-93` | 打印时包 `\033[90m…\033[0m`（终端灰色），注释：「让思考过程呈现灰色，更易区分」——**官方把 reasoning_content 与 content 当作两类独立流分别渲染** |
| 不缓冲打印 | `:93, :97` | `print(..., end="", flush=True)`——每片立刻 flush，这就是「实时返回」的客户端侧实现 |

### 1.6 curl 注意事项（`sf-stream-mode.txt:152-153`）

| 事实 | 出处 | 要点 |
|---|---|---|
| curl 默认缓冲 | `:153` | 原文：「默认情况下，curl 会缓冲输出流，所以即使服务器分块（chunk）发送数据，也需要等缓冲区填满或连接关闭后才看到内容」 |
| 必须加 `-N`（`--no-buffer`） | `:153, :155` | 「传入 -N（或 --no-buffer）选项，可以禁止此缓冲，让数据块立即打印到终端」 |

> 对 MBDSDR 的直接含义：C++ 侧用 libcurl 时同理——要注册 `CURLOPT_WRITEFUNCTION` 并在回调里立刻推给上层，等价于 `--no-buffer`；若误用「收满整个响应体再解析」就会失去流式语义。

### 1.7 错误处理

| 事实 | 出处 | 要点 |
|---|---|---|
| 只判 HTTP 状态码 | `:131, :149-150` | `if response.status_code == 200: … else: print(f"请求失败，状态码：{response.status_code}")` |
| 本页无重试、无错误体解析 | 全文 | 未演示 401/429/5xx 的 body 结构；「API 请求错误排查」是导航中另一独立页（`sf-stream-mode.txt:22`） |

---

## 2. 对 MBDSDR 工具化实现的启示

> 背景：MBDSDR runtime 以流式方式消费 LLM 输出（含思考与工具调用增量），C++（`src/ai/llm_client` 等）+ Flutter 双端 SSE 解析。以下 §2.1/§2.2/§2.5 有原文直接依据；§2.3/§2.4 因本页未覆盖，标注「推断」，落地前需对照「创建对话请求（OpenAI）」API 手册页核实。

### 2.1 SSE 行解析器（C++ `src/ai/llm_client`）

官方 `:135-142` 的循环就是解析器的最小正确形态，C++ 侧按此建模：

```text
# 伪代码（依据 sf-stream-mode.txt:135-148 直译）
on_http_headers(status==200):
    line_buf = ""
on_write_function(bytes):               # libcurl CURLOPT_WRITEFUNCTION
    line_buf += bytes.split('\n')        # 按行切，残余留 buffer
for line in completed_lines:
    if line.empty(): continue            # :136 空行跳过
    payload = strip_prefix(line, "data: ")   # :137 只认 "data: "（含一个空格）
    if payload == "[DONE]": break        # :138 终止哨兵
    chunk = json_parse(payload)          # :139 每行一个完整 JSON
    delta = chunk["choices"][0].get("delta", {})   # :140 两级 .get 兜底
    emit(delta)                          # 逐片上抛，不要攒完整个响应
```

设计要点（全部有原文依据）：

1. **按行缓冲，不按字节交付**（`:135` `iter_lines`）：TCP 回调里遇到半个 `data:` 行要粘包，`\n` 才是事件边界。
2. **前缀精确匹配 `data: `**（`:137`）：剥前缀而不是 split；空行直接跳过（`:136`）。
3. **未知行容错**（推断，依据 `:136-137` 的宽松风格）：遇到 `event:`/`id:`/`retry:` 或空 `data:` 行只忽略不报错。
4. **`[DONE]` 后不再 json_parse**（`:138`）；但 `[DONE]` 只是客户端结束信号，HTTP 连接关闭前仍要走完 write_function 收尾。
5. **非 200 直接终止流并上报状态码**（`:131, :149-150`），body 留作日志，不在流式循环里解析。

### 2.2 reasoning_content 保留管道（对应能力边界 b，有原文直接依据）

这是本页对 MBDSDR 最有价值的一节——官方流式示例证明了边界 b 的流式侧语义：

| 官方做法 | 出处 | MBDSDR 落地要求 |
|---|---|---|
| `delta.reasoning_content` 与 `delta.content` 是**两个独立键**，分别 `.get` | `:140-142` | C++ 模型里两个独立字段 `reasoning_delta` / `content_delta`，不许合并成一个 text 流 |
| 两者都**逐片 += 累加**（`full_reasoning_content += reasoning_content`） | `:146-148` | 思考片必须**原样逐字拼接**，不能去重、不能改写、不能丢弃空片间的分隔 |
| 渲染时 reasoning 单独灰色上色、content 正常色 | `:90-93` | Flutter UI 把思考流与正文流分两条 channel 展示（如思考流灰色、可折叠） |
| 缺省即空串，静默跳过 | `:141-142`（`if content:` / `if reasoning_content:`） | 非思考模型的 chunk 不带 reasoning_content 键，属正常，不是错误 |

> 边界 b 结论（流式侧证实）：流式下思考链 = 所有 chunk 的 `delta.reasoning_content` 按到达顺序拼接的字符串。MBDSDR runtime 必须把它完整保留进消息历史，供下一轮 tool 回传时一并带上（丢弃 → 多步工具链崩）。这与规范 §4.b 完全一致，且本页给出了「逐片 +=」的权威实现方式。

### 2.3 tool_calls 增量拼接器（**推断**，本页 0 证据，需 API 手册页核实）

> 本节是 MBDSDR 最需要、但**本页文档没有讲**的部分。以下为 OpenAI 兼容协议的标准形态（硅基流动明示 OpenAI 兼容，`sf-stream-mode.txt:63-68` 用 OpenAI SDK 直连），标注推断，实现前必须核对「创建对话请求（OpenAI）」手册页。

按标准 OpenAI 流式 tool_calls 协议（推断）：

| 分片形态 | 拼接动作 |
|---|---|
| 首片：`delta.tool_calls = [{index:0, id:"call_xxx", type:"function", function:{name:"get_freq", arguments:""}}]` | 建一个 `Map<index, ToolCall>`；存 `id`、`type`、`function.name`（name 一般首片给全，不增量） |
| 中间片：`delta.tool_calls = [{index:0, function:{arguments:"{\"freq\""}}]` | 只追加 `arguments += 分片字符串`；**不 JSON.parse**（分片可能切在任何字符中间） |
| 同一片内 `delta.tool_calls` 是数组：可能一次带多个 index 的增量 | 遍历数组，每个 index 各自累加 |
| 末片：`choices[0].finish_reason = "tool_calls"`（推断） | 拼接结束标志；此时才对每个 index 的完整 arguments 做 `json_parse` + schema 校验 |

C++ 侧组件建议（`src/ai/llm_client` 内新增 `ToolCallStreamMerger`）：

```text
merger.on_delta(delta):
    for tc in delta.tool_calls or []:
        e = map[tc.index]                    # index 即工具序号，多工具并存靠它区分
        e.id        = tc.id        or e.id
        e.name      = tc.name      or tc.function.name   # 推断：name 一般不增量
        e.arguments_raw += tc.function.arguments or ""
merger.finished():                           # 收到 finish_reason 后调用
    for e in map.values():
        args = json_parse(e.arguments_raw)   # 可能失败 → schema 校验失败 → 按边界 c 处理
        validate_against_tool_schema(e.name, args)
```

红线（与规范 §4.c 对齐）：arguments_raw 全程**字符串追加、中途不解析**；只有完整后才 parse + schema 校验 + 参数白名单；禁止 eval 直执。

### 2.4 finish_reason 判定与多步循环终止（**推断**）

| 判定 | 推断依据 | MBDSDR 行为 |
|---|---|---|
| `finish_reason == "tool_calls"` | OpenAI 兼容标准；本页未出现该字段 | merger 产出工具调用 → 执行 → role=tool 回传 → 继续下一轮请求 |
| `finish_reason == "stop"` | 同上 | 一轮对话结束，退出循环 |
| `finish_reason == "length"` | 同上 | 达到 max_tokens，按截断错误处理 |
| `[DONE]` vs finish_reason 的关系 | `sf-stream-mode.txt:138`：`[DONE]` 只是传输层哨兵 | 推断：业务终止语义以 `finish_reason` 为准；解析器收到 `[DONE]` 只关流，不判业务完成 |

> 注意：本页官方示例**从不读 `choices[0].finish_reason`**（`:140` 只取 delta）。MBDSDR 作为 Agent runtime 必须自己读这个字段，这是本页没有、但 runtime 不能没有的最小补齐（推断）。

### 2.5 Flutter 端建议

| 事项 | 依据 | 建议 |
|---|---|---|
| 传输实现 | `:135` 按行迭代；`:137-138` 剥前缀、判 `[DONE]` | Dart `http`/`dio` 的 `Response` 用 `Stream<List<int>>` → `utf8.decoder` → `LineSplitter`，与 Python `iter_lines` 等价 |
| UI 分两路渲染 | `:90-93` 思考灰色、正文正常 | 思考流发 `reasoning` 事件（灰色/可折叠），正文发 `content` 事件；工具调用增量发 `toolcall_delta` 事件（先显示「正在调用工具…」，拼接完成后展示 name+args） |
| 逐片 flush 不缓冲 | `:93,:97` `end="", flush=True` | 每个 SSE 行解析后立刻 `StreamController.add`，不要在 UI 层攒批 |
| 空 chunk 容忍 | `:81-82` `if not chunk.choices: continue` | Dart 端解析器对 `choices == []` 的 chunk 直接忽略 |
| libcurl 等价物 | `:153` curl 需 `-N/--no-buffer` | 所有端的 HTTP 客户端都必须「来一行推一行」，禁用缓冲式一次性读取 |

---

## 3. 与能力边界映射

| 边界 | 本文档依据 | 实现要点 |
|---|---|---|
| a) function calling 流式侧 | **本页未覆盖**（`tool_calls`/`finish_reason` 0 证据）；仅证实流式端点 = OpenAI 兼容 `/v1/chat/completions`（`:110,:157`） | SSE 解析器可直接复用本页 §1.3；tool_calls 拼接器按 §2.3 推断实现，上线前对照 API 手册页核实 |
| b) interleaved thinking 流式侧 | **本页直接证实**：`delta.reasoning_content` 逐片累加（`:142,:146-148`）、与 content 分键分渲染（`:87-88,:90-93`） | 思考片原样逐字保留进消息历史；UI 与正文分流；缺键即非思考模型，静默跳过 |
| c) arguments 合法性 | 本页未涉及（连 tool_calls 都没有） | 维持规范 §4.c：完整 arguments 必 parse+schema 校验，禁 eval |
| d) tool_choice | 本页未涉及 | 维持规范 §4.d 结论 |
| e) 思考+工具稳定性 | 本页未涉及 | 维持规范 §4.e；GLM-5.1 示例（`:72`）是否支持流式工具调用未在本页说明，按模型配置项处理 |
| f) Anthropic 兼容协议 | 本页是 OpenAI 兼容页（`openai` SDK 直连 `:63-68`） | f 边界与本页无关；导航另有「创建对话请求（Anthropic）POST」页（`:29`），不在本笔记范围 |

---

## 4. 红线与自检记录

| 自检项（规范 §2.4 / §6） | 结果 |
|---|---|
| 文件可打开、转换文本已落盘 `sources/sf-stream-mode.txt`（173 行） | ✓ |
| 引用的每个行号都在原文 txt 中可找到（§1 全部表格逐行核对 `:58/:76/:81-88/:90-93/:103-104/:129/:131/:135-148/:149-150/:152-153/:155/:161`） | ✓ |
| 覆盖了任务指定范围：流式开启方式/SSE 格式/delta 字段/拼接算法/reasoning 语义/组合示例/非流式差异注意事项 | ✓（能覆盖的均给原文依据；本页**没有**的——tool_calls 拼接、finish_reason、usage、限流——在 §1.4 显式声明缺口并标「推断」，未编造） |
| 无编造：全文关键词检索确认 `tool_calls`/`finish_reason`/`usage` 在原始 HTML 为 0 次，故 §2.3/§2.4 全部标注「推断」 | ✓ |
| 「推断」处清单：① 未知 SSE 行前缀应忽略；② choices 空 chunk 通常是 usage chunk；③ tool_calls 分片形态与 index 语义；④ finish_reason 枚举与终止判定；⑤ `[DONE]` 与 finish_reason 的关系。以上均非本页原文 | ✓（共 5 处，集中在 §2.3/§2.4） |
| 只读该文档、只写 `docs/learn/model-tool-calling/` 下文件；未改代码、未做 git、未调任何 API（无 key 动作）、未抓无关页面 | ✓ |
| 篇幅 150–500 行、全中文、多表格、含「对 MBDSDR 工具化实现的启示」（具体到 `src/ai/llm_client`、Flutter 端） | ✓ |

### 一句话总结

硅基流动这份「流式输出」是**快速入门页而非协议手册**：它权威证实了三件事——① 流式要在请求体和 HTTP 客户端两处同时开 `stream`（`:103-104,:129`）；② SSE 就是 `data: <json>` 行流 + `[DONE]` 哨兵，按行解析、逐片上抛（`:135-139`）；③ `delta.content` 与 `delta.reasoning_content` 是两个独立键、各自逐字累加（`:140-148`）——这正是 MBDSDR 边界 b 流式侧的官方实现范式。而 runtime 真正刚需的 **tool_calls 增量拼接、finish_reason 终止判定、usage 统计，本页一个字都没写**，必须回到「创建对话请求（OpenAI）」API 手册页核实后才能落到 `ToolCallStreamMerger`。
