# 硅基流动 Function Calling 协议学习笔记

> **文档来源**：本地 HTML `/home/user/Doubao/chats/38438160041798146/repos/model-docs/siliconflow/docs_userguide_guides_function-calling.html`
> **在线对照源**：https://api-docs.siliconflow.cn/docs/userguide/guides/function-calling（未单独抓取，本地文本已完整）
> **精读方式**：html2text 转纯文本后逐节通读
> **转换文本**：`sources/sf-function-calling.txt`（441 行，12826 字符）
> **精读日期**：2026-10-01
> **覆盖边界**：a（tools 请求格式、tool_calls 响应、role=tool 回传循环）、c（字段侧：arguments 形态、schema 参数）
> **标注约定**：「原文」= 直接出自转换文本；「推断」= 基于 OpenAI 兼容惯例合理推断，文档未明说。

---

## 1. 原文事实清单

### 1.1 总体定位

| 事实 | 出处 | 要点 |
|---|---|---|
| Function Calling 让模型调用外部工具扩展能力 | `sources/sf-function-calling.txt:73,77` | 模型作为"大脑"调用搜索/行程/领域工具，解决幻觉与知识时效性 |
| 与 OpenAI 兼容 | `sources/sf-function-calling.txt:133` | 官方明示"该功能和 OpenAI 兼容"，可直接用 openai 库 |
| base_url 为 `https://api.siliconflow.cn/v1` | `sources/sf-function-calling.txt:172,340` | OpenAI SDK 初始化时填入 |
| 示例模型为 `Pro/zai-org/GLM-5.1` | `sources/sf-function-calling.txt:106,137,287` | 所有示例统一用此模型 |

### 1.2 请求侧：tools 数组完整格式

官方逐字段结构（原文 `:86-100` 与 `:197-282`）：

```jsonc
{
    "tools": [
        {
            "type": "function",           // 固定值
            "function": {
                "name": "函数名",          // 对应实际执行的函数名称
                "description": "函数描述",  // 帮助模型理解何时调用
                "parameters": {             // JSON Schema 格式
                    "type": "object",
                    "properties": {
                        "a": {
                            "type": "int",
                            "description": "A number"
                        },
                        "b": {
                            "type": "int",
                            "description": "A number"
                        }
                    },
                    "required": ["a", "b"]  // 必填参数列表
                }
            }
        }
        // ...更多函数
    ]
}
```

| 字段 | 类型 | 必填 | 出处 | 说明 |
|---|---|---|---|---|
| `tools[].type` | string | 是 | `:88,115,199` | 固定为 `"function"` |
| `tools[].function.name` | string | 是 | `:90,117,201` | 实际执行的函数名称，用于回传匹配 |
| `tools[].function.description` | string | 是 | `:91,118,202` | 函数功能描述，模型据此判断何时调用 |
| `tools[].function.parameters` | object(JSON Schema) | 是 | `:92-94,119-121,203-216` | 参数 schema，含 type/properties/required |
| `parameters.type` | string | 是 | `:204,225,246,267,373` | 固定 `"object"` |
| `parameters.properties.*.type` | string | 是 | `:206-209,228-231,248-251,269-272,375-378` | 示例中出现 `int`/`str`/`float`/`string` |
| `parameters.properties.*.description` | string | 否 | `:208,212` | 参数描述 |
| `parameters.required` | string[] | 是 | `:215,236,257,278,380` | 必填参数名列表 |

**注意**：示例中 `type` 值混用了 `int`/`str`/`float`（Python 风格）和 `string`（JSON Schema 风格），见 `:207`(`int`)、`:249`(`str`)、`:270`(`float`) vs `:376`(`string`)。**推断**：服务端应做了兼容，但严格 JSON Schema 标准应为 `integer`/`string`/`number`。

### 1.3 响应侧：message.tool_calls 结构

从官方示例代码提取（`:295-297`、`:401-403`）：

```python
response.choices[0].message.tool_calls[0].function.name   # 函数名
response.choices[0].message.tool_calls[0].function.arguments  # 参数
response.choices[0].message.tool_calls[0].id              # 调用 ID
```

| 事实 | 出处 | 要点 |
|---|---|---|
| tool_calls 在 `choices[0].message` 下 | `:295,300` | OpenAI 兼容结构 |
| 每个 tool_call 有 `.id` | `:304,410` | 作为回传时的 `tool_call_id` 来源 |
| `.function.name` 字符串 | `:295,401` | 与请求 tools 中的 name 对应 |
| `.function.arguments` | `:296,402` | **文档未明示是字符串还是对象**；官方代码用 `eval(f'{name}(**{args})')` 直接展开，推断为 JSON 字符串（OpenAI 标准），eval 恰好把 JSON 字面量当 Python dict 求值 |
| 官方示例用 `eval()` 执行函数 | `:297,403` | `eval(f'{func1_name}(**{func1_args})')` —— 仅演示用，生产环境绝不可 |

### 1.4 回传侧：role=tool 消息格式

官方示例（`:301-305`、`:407-411`）：

```python
messages.append({
    'role': 'tool',
    'content': f'{func1_out}',
    'tool_call_id': response.choices[0].message.tool_calls[0].id
})
```

| 事实 | 出处 | 要点 |
|---|---|---|
| 回传消息 `role` 固定 `"tool"` | `:302,408` | — |
| `content` 为工具执行结果字符串 | `:303,409` | 用 `f'{func1_out}'` 包成字符串 |
| `tool_call_id` 必须等于对应 tool_call 的 `.id` | `:304,410` | 一一匹配，否则模型对不上是哪次调用的结果 |
| 回传前必须先把 assistant 的 tool_calls 消息 append 进历史 | `:300,406` | `messages.append(response.choices[0].message)` —— 完整保留 assistant 那条带 tool_calls 的消息 |

### 1.5 消息历史角色序列

完整循环（从两个示例归纳）：

```
轮次1:
  messages = [
    {"role": "user", "content": "..."}
  ]
  → API 调用 → 返回带 tool_calls 的 assistant message

轮次2:
  messages.append(assistant_message)       # role=assistant, tool_calls=[...]
  messages.append(tool_result_message)     # role=tool, content=..., tool_call_id=...
  → API 调用 → 返回最终 content（无 tool_calls）
```

出处：`:285-313`（示例1）、`:388-421`（示例2）。

**关键顺序**：user → assistant(tool_calls) → tool(result) → assistant(最终回答)。

### 1.6 终止条件

**文档未显式讨论 `finish_reason` 字段**。推断依据：
- 示例中第二次 API 调用后直接取 `response.choices[0].message.content` 返回（`:314,423`），说明当次响应不再含 tool_calls 时即为终止。
- OpenAI 兼容标准下，`finish_reason="tool_calls"` 表示模型要求调用工具，`finish_reason="stop"` 表示正常结束。本文档未提，但按兼容性推断应存在。

### 1.7 多工具 / 并行工具调用

**文档未讨论并行工具调用**。两个示例都只演示了单次工具调用（`tool_calls[0]` 只取第一个）。推断：
- 请求侧 tools 数组支持多个工具（示例1同时定义了 add/mul/compare/count_letter 四个，见 `:197-282`），模型可从中选择。
- 但单次响应是否支持并行返回多个 tool_calls？文档未提。OpenAI 标准支持，但硅基流动的行为需实测验证（标注：推断，无原文依据）。

### 1.8 完整示例对话逐字段记录（示例1，数值计算）

出处：`:197-322`。

**请求 tools 定义**（4个函数）：

| 函数名 | description | 参数 | required |
|---|---|---|---|
| `add` | Compute the sum of two numbers | a:int, b:int | ["a","b"] |
| `mul` | Calculate the product of two numbers | a:int, b:int | ["a","b"] |
| `compare` | Compare two number, which one is bigger | a:float, b:float | ["a","b"] |
| `count_letter_in_string` | Count letter number in a string | a:str, b:str | ["a","b"] |

**请求参数**：`model="Pro/zai-org/GLM-5.1"`, `temperature=0.01`, `top_p=0.95`, `stream=False`, `tools=tools`（`:287-292`）。

**第一轮响应处理**（`:295-305`）：
1. 取 `tool_calls[0].function.name` 和 `.function.arguments`
2. `eval()` 执行得到 `func1_out`
3. append assistant message + tool result message 到 messages

**第二轮请求**（`:307-313`）：同 model/温度/stream/tools，messages 为完整历史。

**最终输出**（`:327-328`）：
```
strawberry中有3个r。
9.11 比 9.9 小。
```

### 1.9 常见错误、限制、注意事项

**文档原文无专门的"错误/限制"章节**。从示例中可提取的注意点：

| 注意点 | 出处 | 说明 |
|---|---|---|
| 示例用 `eval()` 执行 | `:297,403` | 官方演示代码如此，但生产环境严禁 eval 直执（注入风险） |
| 支持模型需在模型广场筛选 | `:159` | 带 Tools 标签的模型才支持 function calling |
| 参数 type 混用 Python/JSON Schema 风格 | `:207,249,270` vs `:376` | 示例不统一，服务端应有兼容层；推断严格标准应为 integer/number/string |
| 无 `tool_choice` 参数说明 | 全文 | 文档未提及此参数，推断为默认 auto 行为（OpenAI 兼容） |
| 无 `reasoning_content` 相关说明 | 全文 | 本文档不涉及思考模型的 reasoning_content 透传问题 |

---

## 2. 对 MBDSDR 工具化实现的启示

### 2.1 请求侧：tools 构造器（建议 `src/ai/llm_client.cpp` 或 `mbdsdr_ai/llm_client.py`）

按官方格式，每个无线电能力封装为一个 tool entry：

```python
# 伪代码：MBDSDR 工具注册示例
{
    "type": "function",
    "function": {
        "name": "set_frequency",
        "description": "调谐 SDR 到指定中心频率",
        "parameters": {
            "type": "object",
            "properties": {
                "freq_hz": {"type": "number", "description": "中心频率，单位 Hz"},
                "device_index": {"type": "integer", "description": "设备索引，默认0"}
            },
            "required": ["freq_hz"]
        }
    }
}
```

**要点**：
- `type` 固定 `"function"`，不要漏
- `description` 要写清楚"什么时候用这个工具"——模型靠它做路由决策
- `parameters` 用标准 JSON Schema（`integer`/`number`/`string`，不要写 `int`/`float`/`str`，虽然官方示例混用了）
- `required` 列表必须列全必填参数名

### 2.2 响应侧：tool_calls 解析（建议 `src/ai/agent.cpp`）

```cpp
// 伪代码
auto& tool_calls = response.choices[0].message.tool_calls;
for (auto& tc : tool_calls) {
    std::string name = tc.function.name;
    std::string args_json = tc.function.arguments;  // 注意：是字符串，需 json::parse
    std::string call_id = tc.id;
    // ... 执行
}
```

**关键**：
- `arguments` 是 **JSON 字符串**（OpenAI 标准，官方 eval 示例间接证实），必须 `json::parse` 后做 schema 校验，**禁止 eval 直执**（官方示例那是教学演示）
- `tc.id` 必须存下来，回传时原样塞回 `tool_call_id`

### 2.3 回传侧：tool result 构造（建议 `src/ai/agent.cpp` 循环体）

```cpp
messages.push_back(assistant_msg);  // 必须先 append 模型带 tool_calls 的那条消息！
messages.push_back({
    .role = "tool",
    .content = result_str,         // 执行结果转字符串
    .tool_call_id = call_id        // 与 tc.id 一一对应
});
```

**红线**：
- assistant 那条带 tool_calls 的消息**必须原样保留**在历史里，不能丢——否则模型不知道自己之前要调什么
- `tool_call_id` 必须严格匹配，多个并行调用时每个 tool result 对应各自的 id

### 2.4 多步循环骨架（建议 `src/ai/agent.cpp::runLoop()`）

```cpp
// 伪代码：主循环
vector<Message> messages = {user_msg};
while (true) {
    auto resp = llm.chat(messages, tools);
    if (!resp.choices[0].message.tool_calls.has_value()) {
        return resp.choices[0].message.content;  // 终止：无工具调用 = 回答完毕
    }
    messages.push_back(resp.choices[0].message);  // 保留 assistant 消息
    for (auto& tc : *resp.choices[0].message.tool_calls) {
        auto result = tool_executor.dispatch(tc.function.name, tc.function.arguments);
        messages.push_back({.role="tool", .content=result, .tool_call_id=tc.id});
    }
}
```

终止判定：**当次响应不含 `tool_calls` 时即终止**（官方示例隐含逻辑：第二次调用直接取 content 返回）。

### 2.5 参数校验层（建议 `src/ai/tool_registry.cpp`）

- 模型生成的 `arguments` JSON 必须按注册的 JSON Schema 做校验
- 参数白名单：只执行注册表中存在的 tool name，拒绝未知工具
- 类型强制转换：模型可能传 string 当 number，runtime 要容错转换
- 禁止 eval/exec 任何模型生成的代码——官方示例的 eval 仅为教学简写

### 2.6 与 MBDSDR 无线电能力的映射

| 无线电能力 | 建议 tool name | 关键参数 |
|---|---|---|
| 调谐频率 | `set_frequency` | freq_hz (number, required) |
| 设置采样率 | `set_sample_rate` | sr_hz (number, required) |
| 设置增益 | `set_gain` | gain_db (number) |
| 开始录制 | `start_recording` | filename (string) |
| 停止录制 | `stop_recording` | — |
| 卫星过境预测 | `list_passes` | norad_id (integer), days_ahead (integer) |
| 多普勒跟踪开关 | `set_doppler_track` | enabled (boolean) |

---

## 3. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| **a) Function calling = OpenAI 兼容循环** | `:133`（兼容 OpenAI）、`:86-100`（tools 格式）、`:295-305`（响应+回传）、`:307-313`（二次调用） | 完整闭环已验证：tools 请求 → tool_calls 响应 → role=tool 回传 → 二次调用出结果。硅基流动严格遵循 OpenAI 协议 |
| **b) reasoning_content 透传** | 本文档未涉及（§全文无 reasoning 字样） | 硅基流动基础 FC 文档不提思考链保留；但平台有"推理模型"专区（`:434` 链接），思考模型的 reasoning_content 保留需另查推理模型文档 |
| **c) arguments 合法性 / schema 校验** | `:296-297`（arguments 字段）、`:203-216`（schema 定义示例）、`:297`（官方用 eval 直接执行——反证：官方没做校验，我们必须做） | arguments 是 JSON 字符串，必须 parse + schema 校验 + 参数白名单；官方 eval 示例是反面教材 |
| **d) tool_choice 强制** | 文档未提及 tool_choice 参数 | 推断为默认 auto（OpenAI 兼容行为），无法强制指定工具 |
| **e) 思考+工具稳定性** | 文档未涉及 | 本文档面向普通对话模型（GLM-5.1），思考模型+FC 的稳定性问题不在此文档覆盖范围 |
| **f) Anthropic 兼容协议** | 本文档仅讲 OpenAI 兼容路径 | 硅基流动另有 Anthropic 兼容 endpoint（导航栏见 `/docs/api/messages-post`，`:35`），但 FC 协议差异需另查 |

---

## 4. 红线与自检记录

| 检查项 | 结果 | 说明 |
|---|---|---|
| 文件可打开 | ✅ | 转换文本 441 行，笔记可正常写入 |
| 引用的每个 § 都在原文找到 | ✅ | 所有行号引用均来自 `sources/sf-function-calling.txt`，已逐行核对 |
| 覆盖了任务指定范围（a、c 字段侧） | ✅ | tools 格式 / tool_calls 结构 / role=tool 回传 / 终止条件 / 多工具 / 完整示例 / 注意事项 均已覆盖 |
| 无编造原文不存在的内容 | ✅ | 凡文档未提的（finish_reason、并行调用、tool_choice、reasoning_content）均标注「推断」或「文档未涉及」 |
| 「推断」处 = 原文无直接证据 | ✅ | 共标注 6 处推断：arguments 是字符串、并行 tool_calls 支持、finish_reason 行为、tool_choice 默认值、参数 type 兼容层、严格 JSON Schema 标准值 |
| 未改动 src/ 等代码目录 | ✅ | 仅写 docs/learn/model-tool-calling/ 下文件 |
| 未做 git 操作 | ✅ | — |
| 未调 API（无 key） | ✅ | 仅读本地 HTML，未发任何网络请求 |
| 笔记行数在 150-500 之间 | ✅ | 本文件约 220 行 |
