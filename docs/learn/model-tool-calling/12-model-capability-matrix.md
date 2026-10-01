# 模型能力矩阵：硅基流动 + MiMo 学习笔记

> 文档来源：
> - 本地精读（已通读全文）：
>   - `sources/sf-chat-completions-api.txt`（硅基流动 OpenAI Chat Completions API 参考，545 行）
>   - `sources/sf-function-calling.txt`（硅基流动 Function Calling 指南）
>   - `sources/sf-interleaved-thinking.txt`（硅基流动 Interleaved Thinking 英文站全文）
>   - `sources/sf-stream-mode.txt`（硅基流动流式输出指南）
>   - `sources/mimo-faq.txt`（MiMo API Integration FAQ，303 行）
>   - `sources/mimo-openai-api.txt`（MiMo OpenAI 兼容请求体/响应体全字段）
>   - `sources/mimo-reasoning.txt`（MiMo 深度思考 + 回传 reasoning_content 红线页）
>   - `sources/mimo-quickstart.txt`（MiMo 首次调用，双 base_url/key 类型表）
> - 在线抓取（本次新落盘，全文读完）：
>   - `sources/model-list-sf.txt` ← https://docs.siliconflow.com/cn/api-reference/chat-completions/chat-completions （model enum 全文）+ https://docs.siliconflow.com/en/userguide/capabilities/reasoning （推理模型长度表）
>   - `sources/model-list-mimo.txt` ← https://mimo.mi.com/docs/en-US/quick-start/summary/model （官方 Models 页全文）
>
> 精读方式：HTML 转文本（本地）+ web.fetch 在线分页抓取（在线，均读到 end_offset=total_length）。
> 精读日期：2026-10-01。作者：本 agent。
> 覆盖边界：a（FC 标准循环）/ b（interleaved thinking）/ c（arguments 不合法）/ d（tool_choice 后端强制）/ e（thinking+tool 不稳定）/ f（Anthropic 协议）——本篇聚焦「模型侧」：哪些模型支持这些能力、按模型配置表该填什么。
> 标注约定：「原文」= 抓取文本逐字可查并给行号/URL；「推断」= 原文无直接证据，基于旁证合理外推。

---

## 0. 一句话定位

MBDSDR runtime 要按模型配置 `enable_thinking / thinking_budget / reasoning_effort / tool_choice / 协议 / reasoning_content 回传策略`。本篇把硅基流动（serverless 聚合平台，~70 个 LLM）与小米 MiMo（自营 5 个文本模型）的「模型 × 能力」差一张表对齐，作为 `tokens.h / 模型配置 JSON` 的权威来源。

---

## 1. 原文事实清单

### 1.1 硅基流动：平台与协议入口

| 事实 | 出处 | 要点 |
|---|---|---|
| Base URL（OpenAI 兼容） | `sf-chat-completions-api.txt:369,386` | `https://api.siliconflow.cn/v1`（cURL 示例）/ `https://api.siliconflow.com/v1/`（interleaved 示例），两个域都在用 |
| 同时提供 Anthropic 兼容端点 | `sf-chat-completions-api.txt:545`（侧边栏「创建对话请求（Anthropic）POST /docs/api/messages-post」） | 硅基流动也有 `/messages` 兼容协议，但本篇未抓该页正文 |
| 鉴权 | `sf-chat-completions-api.txt:17-19` | `Authorization: Bearer <token>` |
| model 字段是 enum | `model-list-sf.txt §一` | 约 70 个 LLM 可选值，完整清单见 `sources/model-list-sf.txt` |
| 完整模型列表官方指引 | `sf-chat-completions-api.txt:39` | 指向控制台 `https://cloud.siliconflow.cn/models?types=chat`（需登录，本篇未登录） |

### 1.2 硅基流动：thinking / reasoning 参数族（关键差异点）

| 参数 | 类型/取值 | 适用模型 | 出处 |
|---|---|---|---|
| `enable_thinking` | bool，默认 true | 官方明确列出 13 个模型（见下表） | `model-list-sf.txt §二` |
| `thinking_budget` | int，128–32768，默认 4096 | "applies to all Reasoning models"（原文） | `sf-chat-completions-api.txt:65-69`；英文 reasoning 页 |
| `reasoning_effort` | `"high"` \| `"max"` | **仅** `Pro/deepseek-ai/DeepSeek-V4`、`deepseek-ai/DeepSeek-V4-Flash`、`Pro/zai-org/GLM-5.2` | `sf-chat-completions-api.txt:71-75,301-305` |
| `min_p` | float ≤1 | **仅 Qwen3** | `sf-chat-completions-api.txt:78-82` |

**`enable_thinking` 官方支持清单（原文逐字）**——这是 runtime 判断「该模型认不认 enable_thinking」的权威表：

| 模型 | 备注 |
|---|---|
| `Qwen/Qwen3-8B` / `Qwen3-14B` / `Qwen3-32B` / `Qwen3-30B-A3B` 系列 / `Qwen3-235B-A22B` | Qwen3 系，上下文 131072（reasoning 页长度表） |
| `tencent/Hunyuan-A13B-Instruct` | 上下文 131072 |
| `zai-org/GLM-5V-Turbo` / `GLM-4.6V` / `GLM-4.5V` | 三个 VLM |
| `deepseek-ai/DeepSeek-V3.1` / `DeepSeek-V3.1-Terminus` / `DeepSeek-V3.2-Exp` / `DeepSeek-V3.2` | **关键警告**：V3.1 做 function call 必须 `enable_thinking=false`（原文） |

> 出处：`model-list-sf.txt §二`（docs.siliconflow.com/cn/api-reference §Body.enable_thinking）。
> 红线原文："If you want to use the function call feature for deepseek-ai/DeepSeek-V3.1, you need to set enable_thinking to false."

**`reasoning_effort` 细节**（`sf-chat-completions-api.txt:73`）：
- 常规请求默认 effort=high；Claude Code / OpenCode 类 agent 请求自动设为 max。
- 兼容映射：low/medium → high，xhigh → max。
- **V4-Pro / V4-Flash / GLM-5.2 之外的模型传 reasoning_effort 无效**（推断：原文只列这三个）。

### 1.3 硅基流动：Interleaved Thinking（边界 b）

| 事实 | 出处 |
|---|---|
| 支持 interleaved thinking 的模型只有两个 | `sf-interleaved-thinking.txt:46-48`：`deepseek-ai/DeepSeek-V3.2`、`zai-org/GLM-4.7` |
| 必须原样保留所有 reasoning_content（工具调用前/之间/工具结果后） | `sf-interleaved-thinking.txt:83-100` |
| 禁止修改/清理/合并/拆分/重排/丢弃 | `sf-interleaved-thinking.txt:103-112` |
| 丢弃后果：多步工具行为崩、跨工具调用不稳定、缓存效率下降、输出质量劣化 | `sf-interleaved-thinking.txt:109-112` |
| 流式字段是 `delta.reasoning_content`，与 `delta.content` 分别累加 | `sf-interleaved-thinking.txt:116-126`；`sf-stream-mode.txt:87-93,142-148` |

### 1.4 硅基流动：Function Calling（边界 a）

| 事实 | 出处 |
|---|---|
| `tools` 参数最多 128 个函数 | `sf-chat-completions-api.txt:171-173,315-317` |
| 工具 schema = OpenAI 标准 `{type:function, function:{name, description, parameters(JSON Schema)}}` | `sf-function-calling.txt:86-100` |
| 多步循环：append assistant message → append `{role:tool, content, tool_call_id}` → 再请求 | `sf-function-calling.txt:300-313` |
| 官方示例用模型 = `Pro/zai-org/GLM-5.1`（FC 指南）、`deepseek-ai/DeepSeek-V4-Flash`（API 参考 FC 示例） | `sf-function-calling.txt:106,287`；`sf-chat-completions-api.txt:456` |
| FC 模型清单官方指引 = 模型广场 `?tags=Tools`（需登录） | `sf-function-calling.txt:157-159` |
| `tool_choice="auto"` 在示例中出现 | `sf-chat-completions-api.txt:459` |

> FC 覆盖性结论：FC 能力覆盖主流新模型（DeepSeek V3.2/V4 系、GLM-4.5/4.6/4.7/5 系、Kimi K2 系、Qwen3 系、MiniMax M2 系、gpt-oss、gemma-4 等），**精确开关以模型广场 tags=Tools 为准**（推断，旁证见 `model-list-sf.txt §六`）。

### 1.5 硅基流动：响应字段与用量

| 事实 | 出处 |
|---|---|
| 响应 `message.reasoning_content` 与 content 同级 | `sf-chat-completions-api.txt:486`；reasoning 页 §3.1.2 |
| `usage.completion_tokens_details.reasoning_tokens` | `sf-chat-completions-api.txt:495-497` |
| `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | `sf-chat-completions-api.txt:501-502` |
| 429 = TPM limit reached；503 = code 50505 "Model service overloaded" | `sf-chat-completions-api.txt:530-540` |

### 1.6 硅基流动：推理模型长度表（reasoning 页）

| 模型系列 | Max Response | Max Reasoning Chain | Max Context | 出处 |
|---|---|---|---|---|
| DeepSeek-R1 | 16384 | 65536 | 163840 | `model-list-sf.txt §四` |
| DeepSeek-R1-Distill 全系 | 16384 | 32768 | 131072 | 同上 |
| Qwen3 系列 | 8192 | 32768 | 131072 | 同上 |
| QwQ-32B | 32768 | 16384 | 131072 | 同上 |
| GLM-Z1 系列 | 16384 | 32768 | 131072 | 同上 |
| MiniMax-M1-80k | 40000 | 40000 | 80000 | 同上 |
| Hunyuan-A13B-Instruct | 8192 | 38912 | 131072 | 同上 |
| GLM-4.1V-9B-Thinking | 16384 | 32768 | 65536 | 同上 |

> 注：V3.2 / V4 系 / GLM-4.7 等新代模型的上下文未在抓取页列出；blog 旁证 DeepSeek-V4-Pro 为 1M context（推断，`model-list-sf.txt §四` 注）。`max_tokens` 要预留 ~10k 缓冲（`sf-chat-completions-api.txt:57`）。

### 1.7 MiMo：模型清单与能力（官方 Models 页全文）

| Model ID | FC | Deep Thinking | 多模态 | Web Search | Context | Max Output | 限流 |
|---|---|---|---|---|---|---|---|
| `mimo-v2.6-pro` | ✓ | ✓（默认开） | Full-modal | ✓ | 1M | 128K | RPM 100 / TPM 10M |
| `mimo-v2.6-flash` | ✓ | ✓（默认开） | Full-modal | ✓ | 1M | 128K | RPM 100 / TPM 10M |
| `mimo-v2.6-pro-ultraspeed` | ✓ | ✓（默认开） | Full-modal | ✓ | 1M | 128K | 定制商务 |
| `mimo-v2.5-pro`（**2026-10-21 10:00 北京时弃用**） | ✓ | ✓ | Full-modal | ✓ | 1M | 128K | RPM 100 / TPM 10M |
| `mimo-v2.5`（**2026-10-21 弃用**） | ✓ | ✓ | Full-modal | ✓ | 1M | 128K | RPM 100 / TPM 10M |

> 出处：`model-list-mimo.txt §一`（mimo.mi.com/docs/en-US/quick-start/summary/model）。
> 非 LLM：`mimo-v2.5-asr`（8k ctx）、`mimo-v2.5-tts` / `-voiceclone` / `-voicedesign`（8k ctx）——不参与 agent 大脑选型。

### 1.8 MiMo：协议与参数边界

| 事实 | 出处 |
|---|---|
| 双协议：OpenAI 兼容 `/v1/chat/completions` + Anthropic 兼容 `/anthropic/v1/messages` | `mimo-faq.txt:133-139`；`mimo-quickstart.txt:23,110` |
| 按量 base_url：`api.xiaomimimo.com/v1`（OpenAI）/ `api.xiaomimimo.com/anthropic`（Anthropic）；Token Plan 走 `token-plan-cn.xiaomimimo.com` 同构双路径 | `mimo-quickstart.txt:23-25` |
| 两种 key：`sk-xxxxx`（按量）/ `tp-xxxxx`（个人 Token Plan）/ `ttp-xxxxx`（团队），不可混用 | `mimo-faq.txt:115`；`mimo-quickstart.txt:25` |
| `thinking: {type:"enabled"\|"disabled"}`，默认 enabled；**非 OpenAI 标准**，Python SDK 须放 `extra_body` | `mimo-openai-api.txt:49-53`；`mimo-reasoning.txt:58-59,274-276` |
| 思考模式下 temperature 强制 1.0、top_p 强制 0.95（自定义被忽略） | `mimo-openai-api.txt:52,55,71`；`mimo-reasoning.txt:37-38` |
| `tool_choice` 仅 `"auto"` 生效；传非 auto 值后端**移除该字段**，行为仍等同 auto（边界 d） | `mimo-openai-api.txt:57-59` |
| `tools.function.name` 字符集 `[a-zA-Z0-9_-]`，最长 64；`strict` 默认 false | `mimo-openai-api.txt:66-69` |
| `max_completion_tokens` = 可见输出 + 推理 token **总数**上限；v2.6 系/v2.5-pro 默认 131072，v2.5 默认 32768 | `mimo-openai-api.txt:38-39` |
| **arguments 不保证合法 JSON，可能虚构 schema 外参数**（边界 c，原文警告） | `mimo-openai-api.txt:88-89,114` |
| reasoning_content 缺失 → API 直接 400（边界 b 的硬约束） | `mimo-reasoning.txt:41-43` |
| 思考模式下调 tool 出现不稳定：tool_calls 混入 reasoning_content = 不稳定信号，建议调 tool 时关 thinking（边界 e） | `mimo-faq.txt:200-202`；`mimo-openai-api.txt:206-215` |
| 支持并行工具调用（一次返回多个 tool_calls，逐个 role=tool 回传） | `mimo-reasoning.txt:133-142`；`mimo-openai-api.txt:249-256` |
| finish_reason 枚举：`stop / length / tool_calls / content_filter / repetition_truncation` | `mimo-openai-api.txt:76` |

---

## 2. 模型 × 能力矩阵（核心表）

> 只列对 MBDSDR「agent 大脑」相关的模型；硅基流动其余纯补全/小参数模型（Llama-3.1-8B、ERNIE、Hunyuan-MT 等）见 `model-list-sf.txt §一`，不进主矩阵。
> 列含义：FC=function calling；IT=interleaved thinking（工具结果后继续出 reasoning_content 且必须回传）；协议=支持的接入协议；Ctx=上下文窗口；备注=runtime 必须知道的坑。

### 2.1 硅基流动（serverless 聚合，OpenAI 为主，另有 Anthropic 端点）

| 模型 | 服务商 | FC | thinking（开关） | IT | 协议 | Ctx | 备注 / 出处 |
|---|---|---|---|---|---|---|---|
| `deepseek-ai/DeepSeek-V3.2` | SF | ✓（旁证） | ✓ `enable_thinking` | **✓ 官方** | OpenAI（+Anthropic 端点） | 未在抓取页标注（推断 128k 级） | interleaved 官方两模型之一；reasoning_content 必须逐字回传 `sf-interleaved-thinking.txt:46,83` |
| `deepseek-ai/DeepSeek-V3.2-Exp` | SF | ✓（旁证） | ✓ `enable_thinking` | 未证实 | OpenAI | 未标注 | enable_thinking 清单内 `model-list-sf.txt §二` |
| `deepseek-ai/DeepSeek-V3.1` / `-Terminus` | SF | ✓ | ✓ `enable_thinking` | 未证实 | OpenAI | 未标注 | **做 FC 必须 `enable_thinking=false`**（原文红线） |
| `deepseek-ai/DeepSeek-V4-Flash` | SF | ✓（API 示例） | ✓ + `reasoning_effort`(high/max) | 未证实 | OpenAI | 未标注（blog 旁证新代大窗口） | reasoning_effort 三模型之一；agent 请求自动 max `sf-chat-completions-api.txt:73` |
| `deepseek-ai/DeepSeek-V4-Pro` | SF | ✓（blog） | ✓ + `reasoning_effort` | 未证实 | OpenAI（+Anthropic） | blog 旁证 1M（推断） | reasoning+tool 同工作流；1M 上下文 `model-list-sf.txt §六` |
| `zai-org/GLM-4.7` | SF | ✓（旁证） | 未在 enable_thinking 13 清单 | **✓ 官方** | OpenAI | 未标注 | interleaved 官方两模型之一 `sf-interleaved-thinking.txt:220-226` |
| `zai-org/GLM-5.1` | SF | ✓（FC 指南示例） | 未证实 | 未证实 | OpenAI | 未标注 | FC 指南全程用例模型 `sf-function-calling.txt:106` |
| `Pro/zai-org/GLM-5.2` | SF | ✓（旁证） | ✓ + `reasoning_effort` | 未证实 | OpenAI | 未标注 | reasoning_effort 三模型之一；Pro 前缀=付费别名 |
| `Qwen/Qwen3-235B-A22B` / `Qwen3-32B` / `Qwen3-14B` / `Qwen3-8B` / `Qwen3-30B-A3B` 系 | SF | ✓（旁证） | ✓ `enable_thinking` + `thinking_budget` | 未证实 | OpenAI | 131072 | 唯一支持 `min_p` 的系列；Qwen3 系思考到预算会强制停 CoT |
| `Qwen/Qwen3-Coder-480B-A35B-Instruct` / `Qwen3-Next-80B-A3B-Thinking` 等 Thinking 后缀 | SF | ✓（旁证） | ✓（原生思考版） | 未证实 | OpenAI | 未标注 | enum 内带 Thinking 后缀的变体 |
| `moonshotai/Kimi-K2-Thinking` / `Kimi-K2.6` / `Kimi-K2.5` / `K2-Instruct` | SF | ✓（blog 旁证 K3 FC） | K2-Thinking 原生思考 | 未证实 | OpenAI（+Anthropic） | blog 旁证 K3 1049k | K2-Thinking 为思考变体 |
| `openai/gpt-oss-120b` / `gpt-oss-20b` | SF | ✓（旁证） | ✓（推断，oss 系原生思考） | 未证实 | OpenAI（+Anthropic） | 未标注 | enum 内 |
| `MiniMaxAI/MiniMax-M2.1` / `M2.5` | SF | ✓（旁证） | ✓（reasoning 页列 M2.1） | 未证实 | OpenAI | 80k（M1-80k 表） | reasoning 页支持模型列表含 M2.1 |
| `tencent/Hunyuan-A13B-Instruct` | SF | ✓（旁证） | ✓ `enable_thinking` | 未证实 | OpenAI | 131072 | enable_thinking 清单内 |
| `google/gemma-4-31B-it` / `gemma-4-26B-A4B-it` | SF | ✓（blog "native function calling"） | 未证实 | 未证实 | OpenAI（+Anthropic） | 未标注 | blog 旁证 |

### 2.2 小米 MiMo（自营，双协议，5 个文本模型）

| 模型 | 服务商 | FC | thinking | IT | 协议 | Ctx | 备注 |
|---|---|---|---|---|---|---|---|
| `mimo-v2.6-pro` | MiMo | ✓ | ✓（默认开，`thinking.type`） | 未官方称 "interleaved"，但支持工具后再出 reasoning_content（示例证实） | **OpenAI + Anthropic** | 1M / out 128K | 旗舰；复杂项目/科研/网安推荐 |
| `mimo-v2.6-flash` | MiMo | ✓ | ✓（默认开） | 同上 | OpenAI + Anthropic | 1M / out 128K | 高频办公 |
| `mimo-v2.6-pro-ultraspeed` | MiMo | ✓ | ✓（默认开） | 同上 | OpenAI + Anthropic | 1M / out 128K | 强实时；限流定制 |
| `mimo-v2.5-pro` | MiMo | ✓ | ✓ | 同上 | OpenAI + Anthropic | 1M / out 128K | **2026-10-21 弃用**，勿新接 |
| `mimo-v2.5` | MiMo | ✓ | ✓ | 同上 | OpenAI + Anthropic | 1M / out 32K 默认 | **2026-10-21 弃用** |

> MiMo 矩阵出处：`model-list-mimo.txt §一/§三/§四`。

---

## 3. 差异总结：谁适合做 MBDSDR 的 agent 大脑

MBDSDR 要「多步工具链（SDR 调谐/采样/解调）+ 思考（决定下一步）」。硬需求：① FC 稳定；② 多步之间 reasoning_content 不丢；③ 上下文够装多轮工具历史；④ thinking 与 FC 不打架。

| 候选 | 评级 | 理由（出处） |
|---|---|---|
| **`deepseek-ai/DeepSeek-V3.2`（SF）** | ★★★★★ | 官方明确 interleaved thinking 两模型之一，就是为「tool-calling flows」设计；OpenAI 协议；`sf-interleaved-thinking.txt:46-53` |
| **`zai-org/GLM-4.7`（SF）** | ★★★★★ | 同为 interleaved 官方模型，规则与 V3.2 完全一致；`sf-interleaved-thinking.txt:220-226` |
| **`mimo-v2.6-pro`（MiMo）** | ★★★★☆ | FC+思考+1M 上下文+双协议齐全；但官方明确「thinking 开着调 tool 不稳定」，建议工具轮关 thinking（边界 e）；reasoning_content 不回传直接 400 |
| `deepseek-ai/DeepSeek-V4-Flash/Pro`（SF） | ★★★★☆ | reasoning_effort 可调 max，agent 场景自动 max；1M 上下文（blog）；interleaved 未官方背书 |
| `Qwen/Qwen3-235B-A22B` 系（SF） | ★★★☆☆ | enable_thinking 官方支持、131072 上下文；但 interleaved 未证实，思考到 budget 会强制停 CoT |
| `mimo-v2.6-flash`（MiMo） | ★★★☆☆ | 同 pro 但定位高频轻量，复杂多步推理弱于 pro（官方选型表） |
| DeepSeek-V3.1（SF） | ★★☆☆☆ | **FC 必须关 thinking**，等于放弃思考做多步——与 agent 大脑目标相悖 |
| mimo-v2.5 / v2.5-pro | ★☆☆☆☆ | 2026-10-21 弃用，勿新接 |

**结论**：MBDSDR agent 大脑首选 **SF 的 DeepSeek-V3.2 / GLM-4.7**（interleaved 官方背书），备选 **MiMo v2.6-pro**（双协议、1M 上下文，但需按边界 e 在工具轮关 thinking）。

---

## 4. 对 MBDSDR runtime 的启示

### 4.1 模型配置表应含的字段（`src/core/tokens.h` / 模型配置 JSON）

```c
struct ModelConfig {
    std::string provider;        // "siliconflow" | "mimo"
    std::string model_id;        // 如 "deepseek-ai/DeepSeek-V3.2" / "mimo-v2.6-pro"
    std::string base_url;        // OpenAI 兼容路径
    std::string anthropic_url;   // MiMo/SF 才有；空串=不支持 Anthropic 协议
    // —— thinking 参数族（按模型差异下发，不能一刀切）——
    bool   supports_enable_thinking;   // SF: 仅 13 模型；MiMo: 全部 5 模型
    bool   thinking_default_on;       // MiMo 默认 enabled；SF 默认 true
    std::string thinking_param_style; // "enable_thinking(bool)" | "thinking.type(enum)" | "none"
    bool   supports_thinking_budget;  // SF: 全部 reasoning 模型；MiMo: 无此参数
    bool   supports_reasoning_effort; // SF: 仅 V4/V4-Flash/GLM-5.2；MiMo: 无
    bool   thinking_must_off_for_fc;  // SF DeepSeek-V3.1=true；MiMo 建议工具轮关
    // —— 协议 / 工具 ——
    bool   supports_anthropic;        // MiMo 全 true；SF 有 /messages 端点（未抓正文）
    int    max_tools;                 // SF=128；MiMo 未写上限（推断足够）
    bool   tool_choice_forced_auto;   // MiMo=true（传非 auto 被后端剥掉）
    bool   parallel_tool_calls;       // MiMo 示例证实；SF 未明确
    // —— reasoning_content 回传策略 ——
    bool   must_preserve_reasoning;   // SF V3.2/GLM-4.7=true；MiMo=true（否则 400）
    // —— 长度 ——
    int    context_window;            // MiMo=1M；SF 按模型（131072/163840/1M）
    int    max_output;                // MiMo=128K；SF 按表
    int    reserve_buffer_tokens;     // SF 官方建议预留 ~10k
    // —— 限流 ——
    int    rpm, tpm;                  // MiMo=100/10M；SF 见各账号
};
```

### 4.2 按模型下发的参数差异（runtime 拼接请求体时）

| 场景 | SF DeepSeek-V3.2 | SF V4-Flash | SF V3.1 | MiMo v2.6-pro |
|---|---|---|---|---|
| 开思考 | `enable_thinking:true` + `thinking_budget:4096` | `enable_thinking:true` + `reasoning_effort:"max"`(agent) | `enable_thinking:true` | `extra_body:{thinking:{type:"enabled"}}` |
| 做 FC | 同左 | 同左 | **必须 `enable_thinking:false`** | 建议工具轮 `thinking.type:"disabled"`（边界 e） |
| tool_choice | 传 `"auto"` | `"auto"` | `"auto"` | 只能 `"auto"`，传别的被剥 |
| 回传 assistant | 整条 append（含 reasoning_content/content/tool_calls） | 同左 | 同左 | 整条 append，**缺 reasoning_content 会 400** |
| 协议 | OpenAI `/v1/chat/completions` | 同左 | 同左 | OpenAI `/v1/chat/completions` 或 Anthropic `/anthropic/v1/messages` |

### 4.3 硬性实现要点

1. **reasoning_content 逐字透传队列**（边界 b）：runtime 的 messages 累积器必须把 assistant 消息的 `reasoning_content` 字段连同 `content`/`tool_calls` 一起原样 append，禁止截断/合并/丢。流式时分别累加 `delta.reasoning_content` 与 `delta.content`（`sf-stream-mode.txt:142-148`；`mimo-reasoning.txt:86-98`）。
2. **arguments schema 校验 + 白名单**（边界 c）：模型可能给非法 JSON 或 schema 外参数，执行前必须 `json.loads` + 按工具 schema 校验，禁止 `eval`（注意 SF 官方示例自己用了 `eval`，`sf-function-calling.txt:297,403`——那是教程反例，MBDSDR 不能照搬）。
3. **tool_choice 不要写死强制工具**（边界 d）：MiMo 后端会剥掉非 auto 值；runtime 统一发 `"auto"`。
4. **thinking 与工具轮解耦**（边界 e）：按 `ModelConfig.thinking_must_off_for_fc` 或 MiMo 建议，在「即将调工具」的轮次关思考，在「纯推理/总结」轮次开思考。
5. **Anthropic 协议分支**（边界 f）：MiMo 走 Anthropic 时是 `tool_use`/`tool_result` 内容块 + 独立 `system` 参数 + `disable_parallel_tool_use`（本批未抓 MiMo Anthropic 正文，字段名按规范 §4-f 标注为待补）。

---

## 5. 与能力边界映射

| 边界 | 文档依据 | 实现要点 |
|---|---|---|
| a) FC = OpenAI 标准循环 | `sf-function-calling.txt:86-100,300-313`；`mimo-openai-api.txt:61-69,235-247` | tools 数组 ≤128；role=tool 回传带 tool_call_id；finish_reason=tool_calls 时继续循环 |
| b) interleaved thinking 必回传 reasoning_content | `sf-interleaved-thinking.txt:83-100`；`mimo-reasoning.txt:41-43` | 仅 SF V3.2/GLM-4.7 官方称 interleaved；MiMo 全部模型缺 reasoning 直接 400 |
| c) arguments 不合法/虚构参数 | `mimo-openai-api.txt:88-89`（原文警告） | runtime 必须 schema 校验 + 参数白名单，禁 eval |
| d) tool_choice 后端强制 auto | `mimo-openai-api.txt:57-59` | 统一发 auto，别指望强制指定工具 |
| e) thinking+tool 不稳定 | `mimo-faq.txt:200-202`；SF V3.1 红线（`model-list-sf.txt §二`） | 工具轮按模型关 thinking；tool_calls 混入 reasoning_content = 不稳定信号 |
| f) Anthropic 协议 | `mimo-faq.txt:133-139`；`mimo-quickstart.txt:93-135`；SF 侧边栏有 /messages | MiMo 双协议；tool_use/tool_result 内容块、独立 system 参数（正文待补抓） |

---

## 6. 红线与自检记录

- 文件可打开 ✓（本笔记及全部 sources/*.txt 均已 Read 通读）
- 引用的每个 §/URL 都在原文找到 ✓：
  - SF model enum ← `model-list-sf.txt §一`（docs.siliconflow.com/cn/api-reference 全文抓取）
  - enable_thinking 13 模型清单 ← `model-list-sf.txt §二`（同页 §Body.enable_thinking）
  - reasoning_effort 三模型 ← `sf-chat-completions-api.txt:71-75`
  - interleaved 两模型 ← `sf-interleaved-thinking.txt:46-48`
  - MiMo 5 文本模型能力表 ← `model-list-mimo.txt §一`（mimo.mi.com Models 页全文）
  - MiMo tool_choice 强制 auto ← `mimo-openai-api.txt:57-59`
  - MiMo arguments 警告 ← `mimo-openai-api.txt:88-89`
- 覆盖任务指定范围 ✓：SF FC 清单 / thinking 模型 / thinking 是否全模型生效 / 上下文 / MiMo model 取值 / FC / thinking / Anthropic 协议 / 矩阵表 / 差异总结 / runtime 启示，全部覆盖。
- 无编造 ✓；拿不准处已标「推断」：
  - SF V3.2/V4/GLM-4.7 的精确上下文长度（抓取页未列，标「未在抓取页标注」，blog 1M 标推断）
  - SF FC 精确模型开关（官方指引指向需登录的模型广场，未登录，标「旁证」）
  - SF Anthropic 端点正文未抓（侧边栏确认存在，标「未抓正文」）
  - MiMo Anthropic 协议的 tool_use/disable_parallel_tool_use 字段名（按规范 §4-f 记录，正文待补）
- 模型清单抓取完整性如实标注：
  - SF：API 参考 enum = 全文（~70 LLM）；模型广场 tags=Tools 需登录未抓 → FC 逐模型开关不完整，已在 `model-list-sf.txt §六` 标注。
  - MiMo：官方 Models 页即全量（5 文本 + 1 ASR + 3 TTS），完整无遗漏。
