# R2 深度代码审查：hooks.py / workflow_engine.py / workflow_recorder.py

审查范围：
- `mbdsdr_ai/hooks.py`（430 行）
- `mbdsdr_ai/workflow_engine.py`（596 行）
- `mbdsdr_ai/workflow_recorder.py`（438 行）
- 关联：`mbdsdr_ai/agent.py`、`mbdsdr_ai/scheduler.py`、`mbdsdr_ai/sdr_tools.py`、`tests/test_full_integration_v2.py`

总体结论：**三个模块的"类形状"都写出来了，但事件流和数据流在生产路径上几乎全断。** 钩子系统在整个代码库里只有一个调用点（LLM 手动触发的 MCP 工具）；工作流引擎的步骤间数据传递因 executor 契约不匹配而完全失效；工作流录制器因 `self.wr` 笔误 + 参数数量错误而从未真正录过一次工具调用。单元测试只测"类能实例化、预设能列出来"，从不走 `execute()` 真实路径——这就是典型的"实验室绿、真机红"。

---

## 一、hooks.py：钩子系统

### 1.1 钩子是否真的在工具调用前后执行？——**没有**

全局 grep `hook_manager.trigger(` / `.trigger(Event` 的结果：

| 调用点 | 文件:行 | 性质 |
|---|---|---|
| `hm.trigger(Event(...))` | `mbdsdr_ai/agent.py:1664` | LLM 通过 MCP 工具 `hook_trigger_event` **手动** fire 任意 event_type |
| `Hook.trigger`（被 manager 调度） | `mbdsdr_ai/hooks.py:136` | 内部回调，本身没问题 |

也就是说：

- Agent 主聊天循环 `agent.py:2755`（`result = self.tool_registry.call_from_model(tc)`）**前后没有任何 `hook_manager.trigger(AGENT_TOOL_CALL / AGENT_TOOL_RESULT)`**。
- `WorkflowEngine.execute()`（`workflow_engine.py:404-528`）**没有持有 hook_manager 引用**，从不发 `workflow.start/step/complete/error`。
- SDR 后端（`sdr_backend.py`）、信号检测（`signal_analysis.py`）、云台（`gimbal.py`）、调度器（`scheduler.py`）都不发任何 `sdr.*` / `hardware.*` 事件。
- `EventType` 枚举（hooks.py:34-94）声明了 40+ 种事件，但其中**只有 `hook_trigger_event` 这一个入口能产生事件**，且事件类型由 LLM 字符串输入决定。

**[空壳] `mbdsdr_ai/hooks.py:383-429` `create_signal_alert_hook` / `create_auto_record_hook`**
这两个内置工厂函数监听 `sdr.signal_detected` / `sdr.signal_lost`，设计意图是"自动调谐/自动录制"。但：
1. 全代码库没有任何模块调用 `hook_manager.register(...)` 把它们挂上去；
2. 更根本地，`sdr.signal_detected` / `sdr.signal_lost` 事件**从来没人发**。
结果：死代码。真机上信号来了，既不会自动录制也不会告警。

**[空壳] `mbdsdr_ai/hooks.py:367-380` `create_logging_hook`**
同上，没有任何地方注册它；`hook_manager` 实例上默认一个钩子都没有。

### 1.2 注册 / 注销

**[真bug] `mbdsdr_ai/hooks.py:186` hook_id 注销后可碰撞**
```python
hook_id = f"hook_{int(time.time() * 1000)}_{len(self._hook_index)}"
```
后缀用 `len(self._hook_index)`。连续注册 3 个钩子得到 `..._0, ..._1, ..._2`；注销中间一个后长度变回 2，下次注册会再生成 `..._2`，与已存在的钩子 ID 冲突，新钩子**静默覆盖** `self._hook_index[hook_id]`（line 196）。

**[真bug] `mbdsdr_ai/hooks.py:255-265` trigger() 遍历钩子列表时不持锁**
```python
hooks = self._hooks.get(event.event_type, [])
for hook in hooks:
    result = hook.trigger(event)
...
for hook in self._all_hooks:
    result = hook.trigger(event)
```
`register/unregister` 在 `self._lock` 下重建列表（line 216、219-221），但 `trigger` 读取和迭代列表时**不持锁**。多线程场景（异步 worker 线程 `_async_worker` line 293 + 主线程 trigger）下，unregister 与 trigger 并发会触发 `RuntimeError: list changed size during iteration`。

### 1.3 钩子异常是否中断主流程？

`Hook.trigger`（hooks.py:142-147）确实 try/except 包住了 callback，错误只 `print` 一行——**单钩子异常不会中断其它钩子或主流程**。设计正确。

**[建议] `mbdsdr_ai/hooks.py:146`** 用 `print` 而非 `logging`，且只打印 `e` 不打 traceback，线上排障困难。

### 1.4 安全：钩子是否可能被注入恶意代码？

钩子回调是进程内 Python callable，注册动作只在代码层发生；`hook_trigger_event` MCP 工具（agent.py:1664）只让 LLM **触发已有钩子**，不能注入新回调。因此**钩子本身没有远程代码注入面**。

但 `create_logging_hook(log_file=...)`（hooks.py:367-380）每次事件都 `open(log_file, 'a')`（line 373）——高频事件下是不必要的 open/close 开销，且无 rotation。

---

## 二、workflow_engine.py：工作流引擎

### 2.1 真的能执行多步骤工作流吗？——**框架能跑通，但步骤间数据流是断的（核心 bug）**

**[真bug — 实验室绿/真机红的根因] `mbdsdr_ai/workflow_engine.py:496-509` 步骤结果无法回填上下文**

```python
context[f"step_{step.step_id}_result"] = result
result_dict = None
if hasattr(result, 'content') and result.content:
    try:
        result_dict = json.loads(result.content) if isinstance(result.content, str) else result.content
    except (json.JSONDecodeError, TypeError):
        result_dict = None
elif isinstance(result, dict):
    result_dict = result
if isinstance(result_dict, dict):
    for k, v in result_dict.items():
        if isinstance(v, (str, int, float, bool)):
            context[k] = v
```

引擎期望 `tool_executor` 的返回值满足以下之一：
1. 有 `.content` 属性且可 `json.loads` 成 dict；
2. 本身就是 dict。

但生产路径上 `tool_executor` 被绑定为 `agent._workflow_tool_executor`（agent.py:231），其实现（agent.py:1410-1421）是：

```python
def _workflow_tool_executor(self, tool_name, params):
    result = self.tool_registry.call(tool_name, params)
    ...
    if result.success:
        return result.content     # ← 返回的是 str（散文文本）
    else:
        raise Exception(...)
```

返回的是 `ToolResult.content` 字符串。一个 `str`：
- 没有 `.content` 属性 → 分支 1 走不到；
- 不是 `dict` → 分支 2 走不到；
- `result_dict` 永远为 `None`，`for k, v in result_dict.items()` 永远不执行。

**后果：每一步工具的输出都不会进 `context`。** 后续步骤里所有 `{{var}}` 模板：
- 若 `var` 同时在 `workflow.parameters` 里有 default（如 `doppler_freq`、`gimbal_mode`、`frequency`），就用 default 值——表面"能跑"，但实际没用上一步真实输出；
- 若 `var` 不在 parameters 里（如 `interference_freq_hz`、`rssi_by_azimuth`、`interference_az`、`recording_path`），`_resolve_params`（workflow_engine.py:376-378）走 `replace_var` 兜底返回 `match.group(0)`——**字面量 `"{{interference_freq_hz}}"` 被当作参数值传给工具**。

这就是真机失败的直接原因：
- `interference_localization` step 3（workflow_engine.py:201）：`sdr_set_frequency(frequency_hz="{{interference_freq_hz}}")` —— 设备收到字符串 `"{{interference_freq_hz}}"`，要么类型错、要么调到错误频率。
- `noaa_apt_receive_decode` step 5（workflow_engine.py:232）：`sdr_decode_noaa_apt(input_path="{{recording_path}}")` —— 解码工具去打开一个名字叫 `{{recording_path}}` 的文件，必然 FileNotFoundError。
- `sstv_receive_decode` step 4（line 255）、`aprs_monitor` step 4（line 275）：同样问题。

**雪上加霜**：`sdr_tools.py` 里这些工具的 `content` 本身就是**多行散文**，不是 JSON。例如：
- `_signal_detect_interference`（sdr_tools.py:5688-5698）：返回 `"=== 干扰源检测 ===\n中心频率: ... MHz\n检测到 N 个...\n  频率: ... MHz ..."`；
- `_record_start`（sdr_tools.py:2982）：返回 `"开始录制\n格式: wav\n路径: /home/...\n时长: ...秒"`。

即使修了 `.content` 这一层，`json.loads(散文)` 也必然抛 `JSONDecodeError`，`result_dict` 仍是 None。**工具返回格式（散文）和引擎期望（JSON dict）从设计上就不兼容。**

**[真bug] `mbdsdr_ai/workflow_engine.py:205` 引用了不存在的变量**
```python
{"step_id": 7, "tool_name": "gimbal_rssi_sweep",
 "params": {"az_start": "{{peak_az_minus_20}}", "az_end": "{{peak_az_plus_20}}", ...}}
```
`peak_az_minus_20` / `peak_az_plus_20` 在整个 `interference_localization` 工作流里**没有任何步骤产出**，也不在 `parameters` 默认值里。step 7 的粗扫精扫方位区间永远是字面量模板串。

### 2.2 状态机 / 条件分支 / 循环

**[空壳] 没有真正的状态机，也没有循环。** 工作流就是一个线性 `for step in workflow.steps`（line 445）。`condition` 只决定"跳过"还是"执行"，不能回跳、不能迭代。文件头注释（line 10）声称"支持条件执行"，但没有 loop/branch-back 结构。

**[真bug — fail-open] `mbdsdr_ai/workflow_engine.py:392-400` `_check_condition` 异常时返回 True**
```python
try:
    return bool(context.get(condition, False))
except Exception:
    return True
```
条件判断本身抛错时，默认**执行该步骤**而不是跳过。条件表达式只是"变量名存在性检查"（docstring 写的是"条件表达式"），不是真正的表达式求值。任何写错的 condition 都会 fail-open，可能导致真机上不该执行的步骤（如云台转动）被执行。

### 2.3 错误处理与回滚

**[空壳] 没有任何回滚机制。** `execute()` 在某步失败时 `break`（line 485），返回失败结果，但：
- 已经 `sdr_set_frequency` 调谐过的频率不会恢复；
- 已经 `sdr_record_start` 开始的录制（如 NOAA step 4 录 900 秒，line 231）不会停止；
- 已经 `gimbal_point` 转过的云台不会回位。

对 SDR 这种带硬件副作用的工作流，失败后留脏状态是真机红的重要来源。

**[真bug] `mbdsdr_ai/workflow_engine.py:34 / 445-473` `step.timeout` 声明了但从未生效**
每个 step 都有 `timeout` 字段（line 34），预设里也都填了值（10/15/30/60/90/120 秒），`to_dict` 也序列化了（line 108）。但 `execute()` 里调用 `self.tool_executor(...)`（line 467）是**同步裸调用**，没有任何 `signal.alarm` / `threading.Timer` / `concurrent.futures.timeout` 包裹。真机上某个 SDR 调用 hang 死，整个工作流就 hang 死，timeout 形同虚设。

### 2.4 与 agent.py / orchestrator.py 的关系

- `agent.py:103` 创建 `WorkflowEngine()`，`agent.py:231` 注入 `_workflow_tool_executor`，`agent.py:232` 把引擎交给 scheduler。
- `agent.py:1448+` 注册了 `workflow_list` / `workflow_execute` 等 MCP 工具，LLM 可以显式调用。
- `orchestrator.py`：grep `workflow` 关键字 **零命中**——编排器完全不调用工作流引擎。工作流只被 (a) LLM 显式 `workflow_execute`、(b) scheduler 定时任务（scheduler.py:231-232）两条路径触发。
- 触发短语 `match_trigger`（workflow_engine.py:532-537）在 agent 主聊天循环里**没有被调用**——即用户说"找干扰源"并不会自动触发工作流，必须 LLM 自己决定调 `workflow_execute`。文件头注释（line 11）宣称"用户说这些短语自动触发工作流"，**这是空壳**。

### 2.5 安全：工作流定义有沙箱吗？

**[建议/安全] `mbdsdr_ai/workflow_engine.py:330-352`** 用户工作流从 `~/.mbdsdr/workflows/*.json` 加载，**无 schema 校验、无工具名白名单**。JSON 里的 `tool_name` 可以是 tool_registry 里任何已注册工具——包括 `code_editor_*`、shell 类工具。一个被污染的 workflow JSON 等于一个可执行脚本。`execute()` 里也没有对 `params` 做任何过滤。

参数模板解析 `_resolve_params`（line 370-390）用正则替换，**没有 `eval`/`exec`**，所以 JSON 里塞 Python 表达式不会被执行——这一层是安全的。但工具名层面没有沙箱。

---

## 三、workflow_recorder.py：录制与回放

### 3.1 真的能录制吗？——**不能**

**[真bug] `mbdsdr_ai/agent.py:1415` 属性名笔误 `self.wr`**
```python
def _workflow_tool_executor(self, tool_name, params):
    result = self.tool_registry.call(tool_name, params)
    try:
        self.wr.record_tool_call(tool_name, params, result.success)   # ← self.wr 不存在
    except Exception:
        pass
```
grep 全文件：`self.workflow_recorder` 在 agent.py:122 赋值，`self.wr` **从未被赋值**。这一行抛 `AttributeError`，被 line 1416 的 `except Exception: pass` 静默吞掉。

后果：通过工作流引擎执行的每一步工具调用，**都不会写进 recorder**。`workflow_recorder_*` 系列 MCP 工具（agent.py:1833-1891）能列出/创建模板，但 `recordings` 字典永远是空的。

**[真bug] `mbdsdr_ai/workflow_recorder.py:186-193` 方法签名与调用方参数数量不匹配**
```python
def record_tool_call(self, tool_name, parameters, result, success, duration_ms=0.0):
```
4 个必填位置参数。而 agent.py:1415 的调用是：
```python
self.wr.record_tool_call(tool_name, params, result.success)
```
只传了 3 个。即使把 `self.wr` 改成 `self.workflow_recorder`，这一行也会抛 `TypeError: record_tool_call() missing 1 required positional argument: 'success'`——继续被裸 except 吞掉。

另外注意：调用方把 `result.success`（bool）当作 `result` 参数传了，把 `params` 当作 `parameters` 传了，即使签名对齐，`result` 字段存的也是 bool 而不是结果文本——录制记录里的 `result` 列会全是 `True/False`。

**[空壳] Agent 主聊天循环（agent.py:2755 `call_from_model`）也没有调用 `workflow_recorder.record_tool_call`。** 也就是说除了已经坏掉的 `_workflow_tool_executor` 路径，LLM 自由发挥时调的工具也不会被录。recorder 的唯一设计入口（`record_tool_call`）在整个生产代码里只有 agent.py:1415 这一处调用点，且该点是坏的。

### 3.2 参数化模板

**[真bug — 设计错误] `mbdsdr_ai/workflow_recorder.py:261-277` `_parameterize` 匹配逻辑错误**
```python
for param_name, param_desc in parameter_map.items():
    if str(value) == param_desc:   # ← 拿录制到的参数值跟"参数描述"做字符串相等比较
        result[key] = f"{{{{{param_name}}}}}"
```
`parameter_map` 的类型是 `Dict[str, str]`（line 227），文档说"参数名 → 描述"。调用方传入的 value 是人类可读的描述（如 `"接收频率 Hz"`），而录制到的 `value` 是真实数值（如 `100000000` 或 `"manual"`）。`str(100000000) == "接收频率 Hz"` 永远 False。

结果：`create_template` 生成的模板里**一个参数占位符都不会插进去**，录制到的具体值原样保留。回放时 `_substitute_parameters`（line 320-332）找不到 `{{...}}`，参数替换也不会发生。"录制一次、以后用新参数回放"的核心卖点在实现上是失效的。

### 3.3 回放

**[空壳] `mbdsdr_ai/workflow_recorder.py:279-318` `replay_template`**
- 没有重试（对比 WorkflowEngine 有 retry）；
- 没有条件分支；
- 没有 timeout；
- 失败后**继续执行下一步**（line 314-316 注释"这里继续"），不像 WorkflowEngine 那样 break；
- `tool_executor` 签名是 `(tool_name, params) -> (result_text, success)`，与 WorkflowEngine 的 `(tool_name, params) -> content`（失败抛异常）契约**不一致**，两套执行器抽象不统一。

**[空壳] `mbdsdr_ai/workflow_recorder.py:413-437` 启动时只加载 templates，不加载 recordings。**
`_load_templates` 从磁盘恢复 `templates/`，但 `self.recordings` 在构造时是空 dict，磁盘上 `~/.mbdsdr/workflows/recordings/*.json` 的历史录制**不会被读回**。重启后 `list_recordings()` 返回空，`get_recording(recording_id)` 找不到旧录制——`delete_recording` 能删文件，但列不出来。

### 3.4 记录格式是否完整？

`ToolCallRecord.to_dict`（line 48-58）包含 step_index/tool_name/parameters/result/success/timestamp/duration_ms/user_annotation——字段层面完整。但因为 3.1 的 bug，这些字段永远不会被填充。

---

## 四、"实验室绿、真机红"根因汇总

| 层 | 实验室（tests） | 真机（生产） |
|---|---|---|
| 工具执行器 | 测试里 `we.set_tool_executor(lambda n,p: {"fake": "dict"})` 直接返回 dict（test_full_integration_v2.py:463-479 甚至根本没调 execute） | `_workflow_tool_executor` 返回 `result.content` 散文 str |
| 结果提取 | dict 直接命中 `isinstance(result, dict)` 分支 | str 既无 `.content` 也非 dict，提取分支全走不到 |
| JSON 解析 | 测试用 dict 不需要 json.loads | 真工具返回多行散文，json.loads 必抛异常 |
| 录制器 | 不测试 record_tool_call | `self.wr` 笔误 + 参数数量错，被裸 except 吞掉 |
| 钩子 | 不测试事件触发 | 全代码库只有 LLM 手动 fire 一个入口 |
| timeout | 不验证 | step.timeout 字段从未被消费 |
| 回滚 | 不验证 | 失败后硬件状态不恢复 |

---

## 五、发现清单（按严重程度）

### [真bug]

| # | 位置 | 问题 |
|---|---|---|
| B1 | `mbdsdr_ai/workflow_engine.py:496-509` | 步骤结果回填 context 的逻辑因 executor 返回 str 而非 ToolResult/dict 而完全失效；所有 `{{var}}` 跨步骤变量传不下去 |
| B2 | `mbdsdr_ai/agent.py:1419` | `_workflow_tool_executor` 返回 `result.content`（散文 str），与 WorkflowEngine 期望的 `.content` JSON / dict 契约不匹配 |
| B3 | `mbdsdr_ai/sdr_tools.py:5688-5698 / 2982` | 工具 content 是多行散文，即使修了 B1/B2，`json.loads` 也必失败 |
| B4 | `mbdsdr_ai/workflow_engine.py:205` | step 7 引用 `{{peak_az_minus_20}}` / `{{peak_az_plus_20}}`，无任何步骤产出这两个变量 |
| B5 | `mbdsdr_ai/agent.py:1415` | `self.wr` 笔误（应为 `self.workflow_recorder`），AttributeError 被裸 except 吞掉，录制永不发生 |
| B6 | `mbdsdr_ai/workflow_recorder.py:186-193` | `record_tool_call` 需 4 个必填位置参数，调用方只传 3 个；且把 bool 当 result 传 |
| B7 | `mbdsdr_ai/workflow_recorder.py:261-277` | `_parameterize` 用参数描述去匹配录制值，永远匹配不上，模板参数化失效 |
| B8 | `mbdsdr_ai/workflow_engine.py:34 / 467` | `step.timeout` 声明并序列化但从未在 execute 中强制，SDR 调用 hang 死时无保护 |
| B9 | `mbdsdr_ai/workflow_engine.py:392-400` | `_check_condition` 异常时 return True（fail-open），写错的 condition 会导致不该执行的步骤被执行 |
| B10 | `mbdsdr_ai/hooks.py:186` | hook_id 后缀用 `len(_hook_index)`，注销后重新注册会 ID 碰撞并覆盖已有钩子 |
| B11 | `mbdsdr_ai/hooks.py:255-265` | `trigger()` 遍历钩子列表时不持锁，与 register/unregister 并发时可能 `RuntimeError: list changed size` |

### [空壳]

| # | 位置 | 问题 |
|---|---|---|
| S1 | `mbdsdr_ai/hooks.py` 全文 | 整个 HookManager 在生产代码里只有 agent.py:1664 一个调用点（LLM 手动 fire）；`agent.tool_call/result`、`workflow.*`、`sdr.*`、`hardware.*` 事件从未被发射 |
| S2 | `mbdsdr_ai/hooks.py:383-429` | `create_signal_alert_hook` / `create_auto_record_hook` 从未被注册，且监听的事件类型从未被发射——死代码 |
| S3 | `mbdsdr_ai/workflow_engine.py:532-537` | `match_trigger` 触发短语匹配在 agent 聊天循环里没有被调用，"说短语自动触发工作流"未实现 |
| S4 | `mbdsdr_ai/workflow_engine.py:445-528` | 没有循环结构、没有回跳、没有状态机；文件头注释宣称的"条件分支/循环"只有线性 skip |
| S5 | `mbdsdr_ai/workflow_engine.py:485` | 失败即 break，没有回滚已调谐频率/已开始录制/已转动云台的副作用 |
| S6 | `mbdsdr_ai/workflow_recorder.py:279-318` | replay_template 无重试/无 timeout/失败继续，与 WorkflowEngine 执行契约不统一 |
| S7 | `mbdsdr_ai/workflow_recorder.py:413-437` | 启动只加载 templates，不加载 recordings；重启后历史录制不可见 |
| S8 | `mbdsdr_ai/orchestrator.py` | grep 零命中，Orchestrator 完全不集成 WorkflowEngine |

### [占位]

| # | 位置 | 问题 |
|---|---|---|
| P1 | `mbdsdr_ai/workflow_engine.py:37` `condition: str = ""` | docstring 称"条件表达式"，实现只是 `bool(context.get(condition))` 变量存在性检查 |
| P2 | `mbdsdr_ai/workflow_engine.py:105` `param_templates: {}` | `to_dict` 里硬编码空 dict 输出，无实际字段 |
| P3 | `mbdsdr_ai/hooks.py:170-172` | `_async_queue` / `_async_thread` / `_async_stop` 异步路径写了，但 `start_async()` 在生产代码里没人调用 |

### [建议]

| # | 位置 | 问题 |
|---|---|---|
| R1 | `mbdsdr_ai/agent.py:1416` | 裸 `except Exception: pass` 吞掉了 B5/B6 两个真 bug；应至少 log |
| R2 | `mbdsdr_ai/hooks.py:146` | 钩子错误用 `print` 而非 logging，无 traceback |
| R3 | `mbdsdr_ai/hooks.py:373` | `create_logging_hook` 每条事件 open/close 日志文件，无 rotation |
| R4 | `mbdsdr_ai/workflow_engine.py:330-352` | 用户 workflow JSON 无 schema 校验、无工具名白名单，可调用任意已注册工具（含 code_editor） |
| R5 | `mbdsdr_ai/workflow_engine.py:381-387` | `_resolve_params` 对纯数字字符串强制 int/float 转换，可能把本该是字符串的数字 ID（如 `"00123"`）转成 `123` 丢前导零 |
| R6 | `tests/test_full_integration_v2.py:458-479` | WorkflowEngine 测试只 list/stats，从不调 `execute()`；B1/B2/B8 这类真机路径零覆盖 |

---

## 六、修复优先级建议

1. **P0（阻断真机）**：B1+B2+B3 一起修——统一 executor 契约（建议返回结构化 dict 或 ToolResult），并让 `_workflow_tool_executor` 把工具结果里真正需要的字段（频率、路径、方位角）提取成 dict 回填 context；同时让 sdr_tools 的关键工具在散文之外再输出一个 JSON sidecar。
2. **P0**：B5+B6 修 recorder 接线（属性名 + 参数数量 + 把 result 文本传进去）。
3. **P1**：B8 给 `tool_executor` 加 timeout 包裹；B9 把 `_check_condition` 异常改成 return False（fail-closed）。
4. **P1**：S5 给工作流加回滚钩子（失败时调 `sdr_record_stop` / 频率恢复）。
5. **P2**：S1/S2 决定要么把 hook 事件真正接到 SDR 后端和 agent 循环，要么删掉空枚举和死代码；不要留着给人"有这个能力"的错觉。
6. **P2**：S7 启动时加载 recordings；B7 重新设计 `_parameterize`（应该让用户指定"哪个参数名对应哪个录制值"，而不是拿描述做字符串相等）。
