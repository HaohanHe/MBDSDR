# 工具 JSON Schema + arguments 校验规则 学习笔记

> 文档来源（本地纯文本，均为 2026-10-01 精读落盘）：
> - `sources/sf-function-calling.txt`（硅基流动 Function Calling 使用指南，在线抓取转文本）
> - `sources/sf-chat-completions-api.txt`（硅基流动 创建对话请求 OpenAI 兼容 API 参考）
> - `sources/mimo-openai-api.txt`（小米 MiMo OpenAI Chat Completions 兼容 API 正文 + 两个补充来源转录）
> - `sources/mimo-faq.txt`（小米 MiMo FAQ — API Integration）
> - `sources/mimo-reasoning.txt`（小米 MiMo「深度思考/回传 reasoning_content」官方页全文转录）
>
> 精读方式：在线 web_fetch 抓取 + HTML 转文本落盘（MiMo 本地壳为 SPA 空壳，正文在线抓取逐节转录）。
> 精读日期：2026-10-01；作者：MainAgent。
> 覆盖边界：本笔记聚焦 **c**（arguments 校验 / schema 校验 / 参数白名单 / 禁 eval），并在 §3 连带 a（FC 循环、role=tool 回传）。b/d/e/f 仅在交叉处提及，不展开。
>
> **标注约定**：「原文」= 可在 sources/*.txt 指定行号复核；「设计」= 本笔记基于原文约束给出的实现设计，非文档原文；「推断」= 原文无直接证据、靠常识补全。

---

## 1. 原文事实清单

### 1.1 tools.parameters 的 JSON Schema 结构（两家逐字段对照）

两家都把 `parameters` 描述为「JSON Schema 格式参数描述」，官方示例字段完全一致。

| 字段 | 类型/取值 | 作用 | 出处 |
|---|---|---|---|
| `tools[].type` | 固定 `"function"` | 工具类型，目前仅支持 function | `mimo-openai-api.txt:64`；`sf-function-calling.txt:199` |
| `tools[].function.name` | string；MiMo 限定 `a-z/A-Z/0-9` 加 `_`、`-`，最长 64 | 实际执行函数名；模型返回的 `function.name` 与之对应 | `mimo-openai-api.txt:66` |
| `tools[].function.description` | string，**可选** | 函数功能描述 | `mimo-openai-api.txt:67`；`sf-function-calling.txt:202` |
| `tools[].function.parameters` | object，**JSON Schema 格式**；省略则参数列表为空 | 描述函数入参的 schema | `mimo-openai-api.txt:68` |
| `parameters.type` | 固定 `"object"` | 顶层参数是一个对象 | `sf-function-calling.txt:204`；`sf-chat-completions-api.txt:434`；`mimo-openai-api.txt:193` |
| `parameters.properties` | object：每个参数一个键，值为 `{type, description?}` | 声明每个入参的类型与说明 | `sf-function-calling.txt:205-214` |
| `parameters.required` | string[]，列必填参数名 | 声明哪些参数必填 | `sf-function-calling.txt:215`；`sf-chat-completions-api.txt:442`；`mimo-openai-api.txt:198` |
| 参数级 `type` | 示例取值 `int` / `str` / `float` / `string` | 单参数类型 | `sf-function-calling.txt:207,228,249,271`；`sf-chat-completions-api.txt:437` |
| 参数级 `description` | string，可选 | 给模型看的参数说明（示例带例子：`"The city and state, e.g. San Francisco, CA"`） | `sf-chat-completions-api.txt:438`；`mimo-openai-api.txt:195` |
| 参数级 `enum` | string[]，枚举值 | 限定参数取值范围，示例 `["celsius","fahrenheit"]` | `sf-chat-completions-api.txt:440`；`mimo-openai-api.txt:196`；`mimo-faq.txt:180-186` |
| `tools[].function.strict` | bool，默认 `false`；`true` 时**仅支持 JSON Schema 子集** | 严格模式开关（仅 MiMo 文档出现） | `mimo-openai-api.txt:69` |

**两家对 description 质量的强调（如实记录）**：两家原文都**没有**专门段落强调 description 的写法、长度或质量约束；description 仅作为可选字段出现在示例中。原文能确认的只有两点：① 硅基流动示例里 description 直接给英文短句（`sf-function-calling.txt:202,223,244,265`）；② 硅基流动 API 参考示例在参数 description 里内嵌了使用范例（`e.g. San Francisco, CA`，`sf-chat-completions-api.txt:438`）。「description 写得越准、模型越会填对参」属于**推断**，原文未明文。

### 1.2 arguments 的传输形式：是 string，不是 object；必须二次 parse

| 事实 | 出处 | 原文要点 |
|---|---|---|
| 非流式响应里 `arguments` 是 **string** | `mimo-openai-api.txt:87` | `choices.message.tool_calls.function.arguments string — 调用参数，格式为 JSON` |
| 流式 chunk 里 `arguments` 仍是 **string**（增量拼接） | `mimo-openai-api.txt:108-114` | `delta.tool_calls.function.arguments string`，且带同样的合法性警告（见 1.3） |
| 官方 SDK 示例用 `json.loads()` 二次解析后再执行 | `mimo-reasoning.txt:118-119` | `func_args = json.loads(tool_call.function.arguments)` → `TOOL_MAP[func_name](**func_args)` |
| 多轮工具调用示例里 arguments 以字符串字面量呈现 | `mimo-reasoning.txt:139-140` | `arguments:'{"location": "Beijing"}'`、`arguments:'{"timezone": "Asia/Shanghai"}'` |
| 硅基流动示例里 arguments 直接进 eval 拼接 | `sf-function-calling.txt:296-297` | `func1_args = ...function.arguments`；`func1_out = eval(f'{func1_name}(**{func1_args})')` |

**结论（原文可证）**：无论硅基流动还是 MiMo，模型回的 `arguments` 都是一段 **JSON 字符串**，runtime 拿到后必须自己 parse（`json.loads` / `JSON.parse`）成对象才能用；它**不是**已经反序列化好的 dict。流式场景下还要先把多次 delta 片段**累加拼成完整字符串**再 parse（`mimo-openai-api.txt:108-114` 的 index/增量结构即此用途）。

### 1.3 MiMo 原文警告逐字记录（边界 c 的核心证据）

> **非流式 message 上（`mimo-openai-api.txt:87-89`）**：
> `arguments string — 调用参数，格式为 JSON。`
> 【原文警告】`请注意，模型生成的内容并非并非总能保证是有效的 JSON，且可能会虚构出函数模式中未定义的参数。在调用函数之前，请在代码中对这些参数进行验证。`
> （注：原文落盘处「并非并非」为文档自身叠字，照录不改。）

> **流式 delta 上（`mimo-openai-api.txt:114`）**：
> `choices.delta.tool_calls.function.arguments string — 【同上警告】不保证有效 JSON，可能虚构 schema 外参数`

**出现位置小结**：同一警告在 MiMo API 文档出现**两次**——一次挂在非流式 `message.tool_calls.function.arguments` 字段下（`:88-89`），一次挂在流式 `delta.tool_calls.function.arguments` 字段下（`:114`）。即流式/非流式两条路径都明示了同一条风险，不是只针对其中一条。

风险拆成两条独立断言：
1. **不保证合法 JSON**：`json.loads(arguments)` 可能直接抛解析异常；
2. **可能虚构 schema 外参数**：即使 parse 成功，参数集也可能比 `parameters.properties` 声明的多出未定义键（幻觉参数）。

### 1.4 硅基流动官方示例用 eval() 执行 —— 教学反例，如实记录

| 事实 | 出处 | 原文代码 |
|---|---|---|
| 示例 1（数值计算）用 eval 直接执行 | `sf-function-calling.txt:297` | `func1_out = eval(f'{func1_name}(**{func1_args})')` |
| 示例 2（天气查询）同样用 eval | `sf-function-calling.txt:403` | `func1_out = eval(f'{func1_name}(**{func1_args})')` |
| eval 前**没有任何** schema 校验 / 白名单 | `sf-function-calling.txt:295-298,401-403` | 直接取 `function.name` + `function.arguments` 拼进 eval |

**标注**：该 eval 写法是硅基流动官方文档的**教学示例原文**，客观存在；但它把模型生成的字符串直接交给 `eval`，同时命中 MiMo 警告里的两条风险（非法 JSON 抛异常、幻觉参数越界），且 eval 会执行任意表达式。**MBDSDR 生产环境严禁照搬**——本笔记将其作为反面教材记录（出处如上），正确做法见 §2.1 校验器设计。

### 1.5 工具数量上限与其他约束（交叉事实）

| 事实 | 出处 | 要点 |
|---|---|---|
| tools 最多 **128 个函数** | `sf-chat-completions-api.txt:173,317` | "最多支持 128 个函数" |
| `tool_choice` 传非 auto 值会被后端移除，行为等同 auto | `mimo-openai-api.txt:57-59` | 别指望强制指定某工具 |
| 思考模式下 tool_calls 与 reasoning_content 同时返回 | `mimo-openai-api.txt:50-51,62-63` | 与边界 b/e 交叉 |
| 工具结果回传格式 `{role:"tool", tool_call_id, content}` | `mimo-openai-api.txt:260-264`；`mimo-reasoning.txt:120-124` | `tool_call_id` 必须与 assistant.tool_calls[i].id 严格匹配 |
| 模型可一次返回多个 tool_calls（并行调用） | `mimo-openai-api.txt:251-256`；`mimo-reasoning.txt:138-140` | 客户端需逐个执行、各自回一条 role=tool |

---

## 2. 对 MBDSDR 工具化实现的启示

> 本节除标注「原文」外均为 **设计**，落点是 MBDSDR runtime（推断对应 `src/ai/` 下的工具注册与循环，如 `agent_tools.cpp`、`llm_client`、`task_orchestrator`；具体文件名为任务背景给出，未在本批源码内逐行核对）。

### 2.1 runtime arguments 校验器规格【设计】

依据 1.3 的两条原文风险，校验器放在 **「parse 之后、真实执行之前」**，流水线如下：

```
模型返回 tool_call
  │  arguments (string)
  ▼
[阶段1 解析] JSON.parse(arguments)  ──失败──► 走「反馈路径」§2.4（不执行、不崩溃）
  │  成功 → object
  ▼
[阶段2 Schema 校验]  对照本工具注册时保存的 JSON Schema：
  │   a. 类型检查：每个已知键的 value 是否匹配 properties[k].type（int/string/float/bool…）
  │   b. 必填检查：required[] 列出的键是否都在
  │   c. 枚举检查：带 enum 的键取值是否在枚举内
  │   d. 额外键白名单：object 里出现 properties 未声明的键 = 幻觉参数
  ▼
[阶段3 越界/幻觉参数处理]  见 §2.2 策略
  │  通过
  ▼
执行真实 SDR 能力 → role=tool 回传
```

| 校验项 | 依据的原文事实 | 失败后果（设计） |
|---|---|---|
| JSON.parse 合法性 | 1.3 风险①（`mimo-openai-api.txt:88`） | 整次调用拒绝，错误回传模型自纠 |
| 必填键齐全 | 1.1 `required[]`（`sf-chat-completions-api.txt:442`） | 缺失即拒绝 |
| 类型匹配 | 1.1 `properties[k].type`（`sf-function-calling.txt:207`） | 类型错即拒绝 |
| enum 取值 | 1.1 `enum`（`sf-chat-completions-api.txt:440`） | 越界即拒绝 |
| 无 schema 外键 | 1.3 风险②（`mimo-openai-api.txt:88`「虚构未定义参数」） | 见 §2.2 |

### 2.2 越界 / 幻觉参数处理策略取舍【设计】

对「parse 成功但带 schema 外键 / 缺必填 / 类型错」三种情况，给出两条路线及取舍：

| 策略 | 做法 | 优点 | 缺点 / 风险 | 适用 |
|---|---|---|---|---|
| **A. 拒绝整次调用 + 回传错误** | 任一校验不过就不执行，把错误描述当 tool 结果回传（§2.4），让模型自纠 | 安全、语义干净；绝不会把幻觉键喂给真实硬件 | 多一轮往返；若模型反复错会放大循环次数 | 缺必填、类型错、enum 越界（这些无法安全猜） |
| **B. 裁剪幻觉键后执行** | 只丢弃 schema 外键，保留合法键继续执行 | 少一轮往返、对小幻觉鲁棒 | 静默丢弃可能掩盖模型误解；若缺必填则裁剪后仍无法执行 | 仅「多了无关幻觉键、必填/类型都对」时 |

**建议（设计）**：分层——缺必填 / 类型错 / enum 错走 A（不可推断）；仅多出未声明键时走 B（裁剪），并在回传 content 里**注明被丢弃了哪些未知键**，让模型下一轮自我修正。绝对不允许的是 eval 直执（1.4 反例）。

### 2.3 工具注册侧自动生成 Schema + description【设计】

原文里 schema 是手写 JSON（1.1 各示例）。MBDSDR 在 `src/ai/` 注册 SDR 能力时建议：

1. **注册即带 Schema**：每个工具注册项除函数指针外，携带 `name / description / parameters(JSON Schema)` 三件套，与 `tools[].function` 结构一一对应（`mimo-openai-api.txt:64-69`），避免手写漂移。
2. **name 合规**：工具名只用 `a-z/A-Z/0-9`、`_`、`-`，≤64 字符（`mimo-openai-api.txt:66` 硬约束）。
3. **description 写实**：把「这个 SDR 能力干什么、什么时候该用」写进 description；参数级 description 里**内联取值范例**（学硅基流动 `"e.g. San Francisco, CA"` 的写法，`sf-chat-completions-api.txt:438`），能降低模型填错参概率（推断）。
4. **枚举即边界**：离散参数（如解调模式、增益档位）一律用 `enum` 写死（`sf-chat-completions-api.txt:440`），让模型只能在合法集合里选——这是把「校验」前置到生成端的最便宜手段。
5. **strict 模式**：若切 MiMo，可考虑 `strict:true`（`mimo-openai-api.txt:69`），但代价是只能用 JSON Schema 子集；MBDSDR 初期建议 `false`，避免被子集限制卡住复杂参数（设计取舍）。

### 2.4 校验失败的反馈路径（与边界 a 循环衔接）【设计】

原文给出的成功反馈格式是 `{role:"tool", tool_call_id, content}`（`mimo-openai-api.txt:260-264`）。校验失败时**复用同一条通道**，只是 content 换成错误说明：

```jsonc
{
  "role": "tool",
  "tool_call_id": "<与 assistant.tool_calls[i].id 严格一致>",   // mimo-openai-api.txt:262
  "content": "参数校验失败: 工具 set_freq 缺少必填参数 frequency;
              未知参数 band 已被忽略; 合法参数示例: {\"frequency\": 145000000}"
}
```

要点：
- `tool_call_id` 必须严格对齐，否则下一轮 API 行为不可预期（原文 `mimo-openai-api.txt:262` 明确「必须严格匹配」）。
- content 里**说清错在哪、合法长什么样**，模型下一轮就能自纠——这就是把「校验错误」转成「一次正常 tool 结果」喂回边界 a 的循环。
- 注意 MiMo 红线：回传的 assistant 消息若含工具调用，必须**原样带 reasoning_content**，否则 API 直接 400（`mimo-reasoning.txt:41-42`）；校验失败回传也不例外，assistant_message 要整条 append（`mimo-reasoning.txt:113`）。

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| **c（本笔记主边界）** | `mimo-openai-api.txt:88-89,114` 两条警告；`sf-function-calling.txt:297,403` eval 反例 | runtime 必须：parse → Schema 校验（类型/必填/enum/额外键白名单）→ 分层处理幻觉参数；**禁 eval 直执** |
| a（FC 循环） | `mimo-openai-api.txt:260-264`；`mimo-reasoning.txt:107-124`；`sf-function-calling.txt:300-305` | 执行（含校验）后以 role=tool + tool_call_id 回传，直到无 tool_calls；校验失败也走这条回传 |
| b（reasoning_content 保留） | `mimo-openai-api.txt:50-51`；`mimo-reasoning.txt:41-45,113` | 含工具调用的 assistant 整条原样 append，缺 reasoning_content → 400 |
| d（tool_choice） | `mimo-openai-api.txt:57-59` | 后端强制 auto，别依赖强制指定工具 |
| e（thinking+tool 稳定性） | `mimo-faq.txt:200-202`；`mimo-openai-api.txt:206-215` | tool_calls 混入 reasoning_content 是不稳定信号，按模型配 thinking |

---

## 4. 红线与自检记录

逐条对照规范 §6 与 §2.6：

- [x] 文件可打开：本笔记为新建写盘文件，路径见标题块下方约定（`09-tool-schema-and-validation.md`）。
- [x] 引用的每个行号/章节都在 sources/*.txt 原文找到：
  - JSON Schema 字段 → `sf-function-calling.txt:203-216`、`sf-chat-completions-api.txt:433-443`、`mimo-openai-api.txt:64-69` ✓
  - arguments 为 string / json.loads → `mimo-openai-api.txt:87`、`mimo-reasoning.txt:118` ✓
  - MiMo 警告逐字 → `mimo-openai-api.txt:88-89`（非流式）+ `:114`（流式）✓
  - eval 反例 → `sf-function-calling.txt:297` 与 `:403` ✓
  - 回传格式 / 400 红线 → `mimo-openai-api.txt:260-264`、`mimo-reasoning.txt:41-42` ✓
- [x] 覆盖任务指定 6 个重点：① parameters Schema 字段（§1.1）② arguments 字符串 vs 对象 + 二次 parse（§1.2）③ MiMo 警告逐字 + 流式/非流式位置（§1.3）④ eval 反例如实记录并标注严禁（§1.4）⑤ 校验器规格 + 两策略取舍 + 自动生成 Schema 建议（§2.1–2.3，均标「设计」）⑥ 校验失败反馈路径（§2.4）。
- [x] 无编造：两家原文「未强调 description 质量」已如实写明（§1.1 末），未杜撰「description 很重要」之类原文没有的话。
- [x] 「推断」处已标注：description 写法影响填参正确率（§1.1 末、§2.3-3）；`src/ai/agent_tools.cpp` 等具体文件名为任务背景给定、未在本批源码逐行核对（§2 引言）。
- [x] 设计部分均标「设计」（§2 全节标题与内联）。
- [x] 只读文档、只写本目录文件；未改代码、未做 git、未调任何带 key 的接口。
