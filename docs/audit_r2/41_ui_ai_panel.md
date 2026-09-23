# 第二轮深度审查：desktop/ai_panel.py（642行）

审查人：UI/AI 面板子 agent
审查日期：2026-09-24
审查范围：`desktop/ai_panel.py`，关联 `mbdsdr_ai/agent.py`、`desktop/main_window.py`

---

## 一、UI 控件 → 后端接线状态对照表

| 控件 | 位置 | 信号/触发 | 连接到 | 状态 | 备注 |
|---|---|---|---|---|---|
| `input_field` (QLineEdit) | :190 | `returnPressed` | `_on_submit` (:202) | ✅ 已接线 | |
| `send_btn` (QPushButton) | :205 | `clicked` | `_on_submit` (:220) | ✅ 已接线 | |
| `config_btn` (QPushButton) | :136 | `clicked` | `_show_config` (:139) | ✅ 已接线 | 打开 API Key 配置对话框 |
| 快捷指令按钮 ×4 | :166-181 | `clicked` | `_send_quick_command` (:180) | ✅ 已接线 | 仅显示 `QUICK_COMMANDS[:4]`，后4个未创建按钮 |
| `toggle_log_btn` (QPushButton) | :250 | `clicked` | `_toggle_log` (:253) | ✅ 已接线 | |
| `conversation_view` (QTextEdit) | :145 | — | 只读显示 | ✅ 只读 | |
| `tool_log_view` (QTextEdit) | :230 | — | 只读显示 | ✅ 只读 | 默认隐藏 (:246) |
| **模型下拉框 (QComboBox)** | — | — | — | ❌ **不存在** | QComboBox 已 import (:18) 但从未实例化；模型只能通过配置对话框里的 QLineEdit 手输 |
| `ai_status_label` (QLabel) | :130 | — | 状态显示 | ✅ 仅显示 | 显示模型名/上下文 token |

---

## 二、发现清单

### [真bug] 1. AI 被重复调用两次（双重触发）

**位置**: `ai_panel.py:460` + `main_window.py:308, 521-526`

`_on_submit` 在 :460 发射 `command_submitted.emit(text)`，该信号连接到 `main_window._on_ai_command`（:521），后者直接调用 `self.ai_panel._call_ai(text)`。随后 `_on_submit` 在 :463-464 **再次**调用 `self._call_ai(text)`。

```
_on_submit()
  ├─ command_submitted.emit(text)  → main_window._on_ai_command → ai_panel._call_ai(text)  [第一次]
  └─ if self.agent: self._call_ai(text)                                                        [第二次]
```

后果：
- 每次用户发送消息，AI agent 被调用两次，产生两个独立的 QThread + AIWorker。
- 第二次 `_call_ai` 覆盖 `self._worker_thread` / `self._worker`，第一个 worker 的 Python 对象仍被 Qt 信号连接引用而存活，造成线程泄漏。
- 用户在界面上看到**两份 AI 回复**。
- `_is_processing` 标志在第一次调用时已设为 True，但 `_call_ai` 本身不检查该标志，无法阻止第二次调用。

**修复方向**：删除 `main_window.py:308` 的 `command_submitted.connect(self._on_ai_command)` 连接，或删除 `_on_submit:460` 的 `emit`。二选一。

---

### [真bug] 2. 流式回复与最终回复重复显示

**位置**: `ai_panel.py:490-495`（流式开场）+ `:552`（最终追加）

`_call_ai` 在 :492-495 插入一个**未闭合**的 HTML 开场标签：
```html
<div style="..."><span ...>AI</span><br>
```
然后 `_on_stream_delta`（:500-509）通过 `cursor.insertText(piece)` 逐字追加流式文本。

但 `_on_ai_finished` 在 :552 又调用 `self._add_ai_message(content)`，这会插入一个**完整的新 HTML 块**（包含 "AI" 标签和全部内容）。

后果：用户看到：
1. 流式输出的文本（在未闭合的 div 里）
2. 紧接着 `_add_ai_message` 追加的**完整重复内容**

即 AI 回复显示两遍。且开场 `<div>` 未闭合，Qt 的 QTextEdit 会自动补全标签，导致 HTML 结构混乱。

另外 `self._stream_html = ""`（:491）赋值后从未被读取——死代码。

---

### [真bug] 3. MCP 硬件工具从未注册到 Agent

**位置**: `ai_panel.py:372-376`（`register_mcp_tools` 定义）；`main_window.py` 全文搜索无调用

`AIPanel.register_mcp_tools()` 方法存在，但在 `main_window.py` 中**从未被调用**。整个项目中只有 `agent.py:59` 的文档示例提到它。

后果：
- `self.agent.tool_registry` 中没有 `tune_fm`、`scan_fm`、`get_gps` 等硬件工具。
- Agent 内部的工具调用循环（`agent.py:2755` `call_from_model`）会因"工具不存在"而失败。
- Agent 把失败结果喂回模型，浪费多轮 LLM 调用（`max_tool_rounds=10`），最终模型只能输出文本说"我无法调用工具"。
- 实际硬件执行靠 `ai_panel.py:544` 在 `_on_ai_finished` 里事后发射 `tool_call_requested` 信号——但此时 agent.chat() 已经返回，工具结果**不会**回传给模型。

这是一个架构断裂：Agent 以为工具调用失败了，桌面端却在事后异步执行了工具，两者互不知晓。

---

### [真bug] 4. 用户输入 / AI 输出直接拼入 HTML，存在 HTML 注入

**位置**: `ai_panel.py:391`（用户消息）、`:397`（AI 消息）、`:417`（工具日志）

```python
# :391 — 用户原始文本直接插入 HTML
self._append_to_view(f'...<span ...>{text}</span>...')

# :397 — AI 输出仅替换换行，未转义 HTML
safe_text = text.replace("\n", "<br>")
self._append_to_view(f'...<span ...>{safe_text}</span>...')

# :417 — args JSON 直接插入
f'<b>{tool_name}</b>({json.dumps(args, ensure_ascii=False)[:80]}) '
```

QTextEdit 不执行 JavaScript（非浏览器），所以不是 XSS，但：
- 用户输入 `</span></div><div style="background:red">恶意内容` 可以突破 HTML 结构，伪造消息样式。
- AI 返回的 HTML（如 `<script>`、`<iframe>`）会被 QTextEdit 解析渲染，破坏布局。
- 工具参数中若含 `<`、`>`、`&`，会破坏 tool_log_view 的 HTML 结构。

**修复方向**：用 `html.escape()` 转义所有动态文本后再拼入 HTML。

---

### [真bug] 5. 斜杠命令在 UI 线程同步执行，可能阻塞

**位置**: `ai_panel.py:448-456`

```python
if text.startswith("/"):
    ...
    response = self.agent.run_command(text)  # 同步调用，在 UI 线程
    self._add_ai_message(response)
```

`run_command` 中 `/models` 会调用 `model_manager.fetch_models()`（`agent.py:2921`），这是网络请求。在 UI 线程执行会导致界面冻结。`/switch` 也可能触发网络调用。

---

### [空壳] 6. QComboBox 导入但从未使用

**位置**: `ai_panel.py:18`

`QComboBox` 在 import 列表中，但整个文件没有实例化任何下拉框。审查重点第4条"模型下拉框是否真的能切换LLM模型"——**答案是没有模型下拉框**。模型选择只能通过：
1. 配置对话框里的 QLineEdit 手输模型 ID（:311-316）
2. 聊天框输入 `/switch <model>` 斜杠命令

配置对话框中的模型字段是纯文本输入，无自动补全、无可用模型列表。

---

### [空壳] 7. `on_tool_result` 槽参数丢失

**位置**: `ai_panel.py:421-428`；`main_window.py:440`

`main_window.py:440` 将 worker 的 `tool_result` 信号连接到 `ai_panel.on_tool_result`。但 `on_tool_result` 在 :428 调用 `_add_tool_log(tool_name, {}, success, result_str, 0.0)`——**args 传了空字典 `{}`，latency 传了 0.0**。

这意味着从桌面 worker 回传的工具结果在日志里看不到实际参数和耗时。参数信息被丢弃。

---

### [占位] 8. 快捷指令只创建了前4个按钮

**位置**: `ai_panel.py:166`

```python
for cmd in QUICK_COMMANDS[:4]:
```

`QUICK_COMMANDS` 定义了 8 条（:32-41），但只渲染前 4 个按钮。后 4 条（"当前信号质量怎么样"、"查看9轴姿态"、"自动找干扰源方向"、"识别当前信号制式"）没有对应按钮。

---

### [建议] 9. 主题颜色全面硬编码，未接入主题系统

**位置**: 遍布全文

硬编码颜色包括：
- `#3D3D3D`（:125, :154, :198, :391, :397）
- `#888`（:131, :162, :227, :416）
- `#FAFAF8`（:149）
- `#E0DDD8`（:150, :236）
- `#8B7355`（:210, :391, :400）
- `#5A8A5A`（:274, :397, :413）
- `#F0EDE8`（:173）
- `#C05050`（:280, :413）
- 等等

`main_window.py:352` 有 `_apply_theme` 机制（`get_theme`），但 ai_panel 的所有样式表都是内联硬编码，切换主题时这些颜色不会变。配置对话框（:288-321）甚至没有任何样式表，使用系统默认样式。

---

### [建议] 10. 上下文状态不自动刷新

**位置**: `ai_panel.py:256-280`

`_update_ai_status()` 只在 `_show_config`（:337）和 `init_agent`（:368）时被调用。AI 对话过程中，`ai_status_label` 显示的 token 用量/消息数不会更新——用户在连续对话中看不到上下文是否快满了。

应在 `_on_ai_finished` 后调用 `self._update_ai_status()`。

---

### [建议] 11. 流式插入未转义特殊字符

**位置**: `ai_panel.py:505`

`cursor.insertText(piece)` 插入纯文本，不经过 HTML 转义。虽然 `insertText` 本身是纯文本插入（不会解析 HTML），但如果流式增量中包含大量格式字符（如 Markdown 标记 `**bold**`、`` `code` ``），它们会以原始字符显示，不会被渲染为富文本。这与最终 `_add_ai_message`（也不支持 Markdown）一致，但体验上所有 AI 回复都是纯文本+HTML包装，无 Markdown 渲染。

---

## 三、与 agent.py 接口对照

| agent.chat() 返回字段 | ai_panel 读取 | 位置 | 一致性 |
|---|---|---|---|
| `content: str` | `result.get("content", "")` | :518 | ✅ |
| `tool_calls: list[dict]` | `result.get("tool_calls", [])` | :519 | ✅ |
| `tool_results: list[dict]` | `result.get("tool_results", [])` | :520 | ✅ |
| `usage: dict` | **未读取** | — | ⚠️ token 用量未显示（只在 status label 显示上下文，不显示本次消耗） |
| `latency_ms: float` | `result.get("latency_ms", 0)` | :522 | ✅ |
| `compacted: bool` | `result.get("compacted", False)` | :521 | ✅ |
| `error: str` | `result.get("error")` | :523 | ✅ |
| `rounds: int` | **未读取** | — | ⚠️ |

`agent.chat(on_delta=...)` 流式回调：✅ 已接线（:60），通过 `delta` 信号 → `_on_stream_delta`。

`agent.run_command()`：✅ 已接线（:451），但在 UI 线程同步调用（见真bug #5）。

`agent.register_mcp_tools()`：❌ 从未被 main_window 调用（见真bug #3）。

---

## 四、与 main_window.py 集成对照

| 集成点 | main_window 位置 | ai_panel 位置 | 状态 |
|---|---|---|---|
| `AIPanel()` 实例化 | :306 | :79 | ✅ |
| `tool_call_requested` 信号连接 | :307 | :75 | ✅ |
| `command_submitted` 信号连接 | :308 | :77 | ⚠️ **导致双重调用**（真bug #1） |
| 加入 Tab | :309 | — | ✅ |
| `init_agent` 从配置文件初始化 | :562 | :340 | ✅ |
| `tool_result` → `on_tool_result` | :440 | :422 | ⚠️ 参数丢失（空壳 #7） |
| `register_mcp_tools` 调用 | **无** | :372 | ❌ 从未调用（真bug #3） |
| `_start_sweep` 填入输入框 | :618 | :190 | ✅ |

**配置 key 不一致**：`ai_panel._show_config` 保存时用 `"api_base"`（:330），但 `main_window._init_ai_agent_from_config` 读取时用 `cfg.get("base_url", "")`（:564）。由于 `init_agent` 内部会 fallback 到 `self._saved_config.get("api_base")`（:357），实际仍能工作，但 key 命名不一致，属代码异味。

---

## 五、无限递归检查

逐一排查所有信号连接：

| 信号 | 连接到 | 是否回写同一控件 | 递归风险 |
|---|---|---|---|
| `input_field.returnPressed` | `_on_submit` | `_on_submit` 调用 `input_field.clear()`（:458），不触发 returnPressed | ✅ 无 |
| `send_btn.clicked` | `_on_submit` | 同上 | ✅ 无 |
| `btn.clicked`（快捷指令） | `_send_quick_command` | 先 `setText(cmd)`（:438）再 `_on_submit()`（:439）。`setText` 不触发 `returnPressed` | ✅ 无 |
| `toggle_log_btn.clicked` | `_toggle_log` | 切换 `tool_log_group` 可见性 | ✅ 无 |
| `delta`（worker） | `_on_stream_delta` | `insertText` 到 conversation_view，无 `textChanged` 连接 | ✅ 无 |
| `config_btn.clicked` | `_show_config` | 打开模态对话框 | ✅ 无 |

**未发现 textChanged→setText→textChanged 循环。** 整个 ai_panel.py 没有连接任何 `textChanged`/`textEdited` 信号。

---

## 六、性能评估

| 项目 | 评估 |
|---|---|
| AI 调用是否阻塞 UI | ✅ 使用 QThread + AIWorker（:477-497），不在 UI 线程调用 LLM |
| 流式显示 | ✅ `on_delta` 回调 → `delta` 信号 → `_on_stream_delta`（:499-509）实时追加 |
| 斜杠命令阻塞 | ❌ `run_command` 在 UI 线程同步执行（:451），`/models` 会发网络请求 |
| 大段回复渲染 | ⚠️ 每个 delta 都调用 `cursor.insertText` + `ensureCursorVisible`（:505-507），长回复时可能频繁重绘；未做节流/批量合并 |
| 线程清理 | ⚠️ 双重调用导致线程泄漏（真bug #1）；单次调用时的清理链（:485-488）正确 |

---

## 七、问题汇总统计

| 级别 | 数量 | 编号 |
|---|---|---|
| 真bug | 5 | #1 双重调用, #2 重复显示, #3 MCP工具未注册, #4 HTML注入, #5 斜杠命令阻塞 |
| 空壳 | 2 | #6 QComboBox未使用, #7 on_tool_result参数丢失 |
| 占位 | 1 | #8 快捷指令只渲染4/8 |
| 建议 | 3 | #9 主题硬编码, #10 状态不刷新, #11 Markdown不渲染 |

**最严重的三个问题**：
1. **#1 双重 AI 调用** — 每次发消息都跑两次 LLM，浪费 token + 界面重复回复
2. **#3 MCP 工具未注册** — Agent 核心能力（调用硬件工具）在桌面端完全断裂
3. **#2 流式+最终回复重复** — 用户每次看到两份相同的 AI 回复
