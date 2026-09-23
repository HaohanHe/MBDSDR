# 第二轮深度代码审查 — subagents.py & orchestrator.py

- 审查范围：`mbdsdr_ai/subagents.py`（567 行）、`mbdsdr_ai/orchestrator.py`（405 行）
- 关联文件：`mbdsdr_ai/agent.py`、`mbdsdr_ai/self_evolution.py`、`mbdsdr_ai/tool_registry.py`、`mbdsdr_ai/workflow_engine.py`、`tests/test_full_integration.py`
- 审查方式：只读，逐行阅读真实代码 + 跨文件引用追踪

---

## 一、结论速览

| 维度 | 结论 |
|---|---|
| 子 agent 是否真能创建/管理 | **能创建、能同步跑一个硬编码工具**，但不是"AI 子 agent"——无 LLM 循环、无上下文使用、无工具权限隔离 |
| 隔离机制 | **名义上的**：有 `allowed_tools` 字段和 `can_use_tool()`，但运行时从不调用；进程/线程隔离只有 daemon 线程，且无线程安全保护 |
| 通信机制 | 仅同步返回 `SubagentResult`；异步路径（`submit_task`）**未暴露为工具**，LLM 走不到 |
| 生命周期 | create/destroy 可用；但 destroy 不等待运行中任务、不中断线程 |
| 编排器能否执行多步骤 | **能顺序执行**依赖图拓扑序任务，但**不并行**、不超时、不回滚 |
| 任务依赖图 | Kahn 拓扑排序可用，但**不检测环**、不校验依赖 ID 是否存在 |
| 错误处理 | 有重试（指数退避），但**无回滚/补偿**；依赖失败静默 SKIPPED |
| 与 agent.py 关系 | 都是 agent 单例持有（`agent.py:112`、`agent.py:141`），通过工具暴露给 LLM；**独立运行，互不调用** |
| 与 self_evolution 关系 | **零引用**。self_evolution 完全不经过 orchestrator，也不通过 code_evolver 子 agent |
| "实验室绿、真机红" | 测试只覆盖 CRUD（create/list/stats/destroy），**从不调用 execute**；真机上 `sdr_connect` 失败后整个流水线无补偿，设备状态残留 |
| 空壳/占位比例 | 高：7 个宣传子 agent 类型中 4 个无执行后端；orchestrator 文档承诺的"并行/动态调整/错误恢复（回滚）"均未实现 |

---

## 二、subagents.py 发现

### [真bug] F1. `timeout_s` 完全不生效
- 位置：`subagents.py:76`（字段定义）、`subagents.py:483`/`521`（参数透传）
- 现象：`SubagentTask.timeout_s` 被存入 dataclass，但整个文件**没有任何一处读取它**。`submit_task` 起 daemon 线程后不 `join(timeout=...)`，`execute_task` 同步路径也不看门狗。
- 后果：SDR 工具卡住（设备未插、USB 挂死）时，子 agent 永远停在 RUNNING，daemon 线程泄漏。`SubagentStatus.TIMEOUT`（`:40`）枚举值定义了但**全代码库从未被赋值**。

### [真bug] F2. `max_concurrent` 完全不生效
- 位置：`subagents.py:423`（构造参数）、`:426`（存储）、`:565`（stats 输出）
- 现象：`submit_task`/`execute_task` 都不检查当前 RUNNING 子 agent 数，不检查线程池占用。`max_concurrent=4` 只在 `get_stats()` 里回显给看。
- 后果：LLM 循环里连续 `subagent_execute` N 次会起 N 个线程同时打同一个 SDR 设备（`tool_registry.call` 共享同一个 `sdr_backend`），设备竞争未定义行为。

### [真bug] F3. `allowed_tools` 权限隔离是假的
- 位置：`subagents.py:240`（`can_use_tool` 定义）、`:300`（`_execute` 实际执行）
- 现象：`can_use_tool()` 定义了但**全文件无调用点**。`_execute` 根据 `self.agent_type` 硬编码调哪个工具（`:315-326`），根本不查 `allowed_tools`。
- 后果：`DEFAULT_ALLOWED_TOOLS`（`:388-421`）列了一堆工具白名单，但子 agent 实际行为由 `agent_type` 字符串决定，白名单形同虚设。例如把 `allowed_tools=[]` 传给 spectrum_analyzer，它照样调 `sdr_spectrum_analyze`。

### [真bug] F4. 7 个宣传子 agent 类型中 4 个无执行后端
- 位置：`subagents.py:315-329`
- 现象：`_execute` 的 if/elif 只覆盖 `spectrum_analyzer`（`:315`）、`satellite_tracker`（`:319`）、`baseband_recorder`（`:323`）；`signal_decoder`、`interference_hunter`、`hardware_controller`、`code_evolver` 全部落到 `else`（`:327`），返回字符串 `"子代理 {type} 不支持的任务类型: {goal}"`。
- 后果：
  - `signal_decoder` 宣传支持 FT8/NOAA/ADS-B/LoRa/M17（`:106-119`），实际一句话不执行；
  - `hardware_controller` 宣传设备连接/增益/固件 OTA（`:166-179`），实际不执行；
  - `code_evolver` 宣传自编程/代码审查（`:181-194`），实际不执行——**self_evolution 的 propose/evaluate/commit 工具根本没被任何子 agent 包装**。
- 这是"实验室绿"的典型：`subagent_create` + `subagent_list` 测试通过，`subagent_execute` 一个真任务就暴露。

### [空壳] F5. 没有 LLM 循环——"子 agent"只是工具调用包装器
- 位置：`subagents.py:246-252` 自承："这是简化版执行：直接调用工具完成任务，不做完整的 LLM 循环"
- 现象：`self.system_prompt`（`:228`）、`self.context`（`:232`）、`self.model_manager`（`:226`）全部存了但**从未读取**。`model_manager` 从 `agent.py:114` 传入后就被挂着。
- 后果：白皮书 4.5 章承诺的"子 agent 有自己的系统提示、上下文、多步推理"在代码里只是 dataclass 字段，没有任何 LLM 推理发生。所谓"专家子代理"实际就是一个 if/elif 路由到一个工具。

### [空壳] F6. 三个旧占位方法成为死代码
- 位置：`subagents.py:333` `_execute_spectrum_analysis`、`:341` `_execute_satellite_tracking`、`:349` `_execute_recording`
- 现象：这三个方法返回 `"（简化执行，实际需要连接 SDR 设备读取 IQ 样本）"` 之类的占位字符串，但**没有任何地方调用它们**——`_execute`（`:300`）已经改成真调 `tool_registry.call`。
- 后果：残留的死代码，容易误导读者以为这是真实执行路径。

### [真bug] F7. 线程安全：锁定义了但从不加
- 位置：`subagents.py:238`（`self._lock = threading.Lock()`）
- 现象：`execute_task`（`:246-298`）修改 `self.status`、`self.current_task`、`self.task_history`、`self.total_tasks_completed`、`self.total_tasks_failed` 时**从不 `with self._lock`**。`submit_task`（`:507`）可能让两个线程同时进同一个 subagent 的 `execute_task`。
- 后果：`task_history.append` 交错、计数错乱、`current_task` 被后一个任务覆盖。叠加 F2（无并发上限），这是真实数据竞争。

### [占位] F8. 异步 `submit_task` 路径未对 LLM 暴露
- 位置：`subagents.py:478`（`submit_task` 定义）；`agent.py:1691-1741`（`_register_subagent_tools`）
- 现象：agent 只注册了 `subagent_create`/`subagent_list`/`subagent_execute`/`subagent_stats`，**没有** `subagent_submit`（异步）和 `subagent_get_result`（轮询）。grep `agent.py` 对 `submit_task|get_task_result` 零命中。
- 后果：`submit_task` 里的 daemon 线程逻辑（`:507-512`）从 LLM 不可达，只能被 Python 代码直接调用。异步框架等于写了但没接线。

### [建议] F9. 回调异常被静默吞掉
- 位置：`subagents.py:292-296`
- ```python
  if task.callback:
      try:
          task.callback(result)
      except Exception:
          pass
  ```
- 后果：回调里的 bug 完全无声。建议至少 `traceback.print_exc()` 或记到 `self.task_history`。

### [建议] F10. `destroy` 不中断运行中任务
- 位置：`subagents.py:464-468`
- 现象：`destroy` 直接 `self.subagents.pop(subagent_id)`，不检查 `status == RUNNING`，不发取消信号，不 join 线程。
- 后果：被 pop 的 subagent 仍在后台跑，其持有的 `tool_registry` 引用还在继续打设备，但已从 manager 字典消失——资源泄漏 + 悬挂操作。

---

## 三、orchestrator.py 发现

### [真bug] O1. 不并行——`max_parallel` 是假参数
- 位置：`orchestrator.py:138`（构造参数）、`:142`（存储）、`:250`（`execute` 主循环）、`:359`（stats 回显）
- 现象：模块 docstring（`:16`）承诺"并行执行：无依赖的任务并行执行"，`execute()` 是一个简单的 `for task_id in order:` 顺序循环（`:250`）。`import threading`（`:29`）和 `self._lock`（`:146`）定义了但 `execute` 里没用。
- 后果：`create_sdr_pipeline` 里 t2/t3/t4（设频率/采样率/增益）相互独立，按文档应并行，实际顺序跑。`max_parallel=4` 只在 stats 里好看。

### [真bug] O2. `timeout_s` 完全不生效
- 位置：`orchestrator.py:70`（字段）、`:177`（赋值）、`:295`（`_execute_task`）
- 现象：`Task.timeout_s` 存了，但 `_execute_task` 同步调 `tool_registry.call`（`:308`）或 `handler`（`:306`），没有任何超时包装、没有 `concurrent.futures`、没有 watchdog。
- 后果：和 F1 同病——SDR 工具挂住则整个编排器挂住。`default_timeout_s=60` 参数纯展示。

### [真bug] O3. 不检测循环依赖
- 位置：`orchestrator.py:186-227`（`plan`）
- 现象：Kahn 拓扑排序标准实现，但循环依赖存在时，环上的任务永远 in_degree > 0，最终 `order` 里**静默缺失**这些任务，不抛异常、不告警。
- 后果：用户写 `A depends on B` + `B depends on A`，`plan()` 返回的 order 里没有 A/B，`execute()` 直接跳过，用户以为跑完了。

### [真bug] O4. 依赖 ID 不校验存在性
- 位置：`orchestrator.py:148`（`add_task`）、`:256-268`（`execute` 里的 deps_ok 检查）
- 现象：`add_task` 接受任意 `dependencies=[...]` 字符串列表，不查这些 task_id 是否真的存在。`execute` 里的检查是：
  ```python
  deps_ok = all(
      (dep_id in self.tasks and self.tasks[dep_id].status == TaskStatus.COMPLETED)
      for dep_id in task.dependencies
  )
  ```
  若 `dep_id` 不存在，`and` 短路为 False → `deps_ok=False` → 任务被 SKIPPED，错误信息 `"任务 X 因依赖失败而跳过"`（`:265`）——误导用户以为依赖任务失败了，实际是 ID 拼错。

### [空壳] O5. 无回滚/补偿——"错误恢复"只有重试
- 位置：`orchestrator.py:300-329`（`_execute_task` 重试逻辑）
- 现象：docstring（`:18`）承诺"错误恢复：任务失败时自动重试或降级"，实现只有 `max_retries+1` 次重试 + 指数退避（`:322-323`）。**没有任何 compensation/undo 机制**。
- 后果：`create_sdr_pipeline`（`:368-405`）顺序执行 `sdr_connect → set_frequency → set_sample_rate → set_gain → spectrum → find_signals → set_demod → demodulate → record_start`。若 `record_start` 失败，前面已经在设备上设好的频率/采样率/增益/解调模式**全部残留**，没有逆操作把设备恢复。这是典型的"实验室绿、真机红"：合成测试里工具都 mock 成功，真机上失败一次设备就留在中间态。

### [空壳] O6. 无幂等性——重复 execute 重跑所有任务
- 位置：`orchestrator.py:250`（for 循环）
- 现象：`execute()` 不检查 `task.status == COMPLETED`，第二次调 `execute()` 会把已完成任务再跑一遍。没有 `reset` 也没有 `resume`。
- 后果：LLM 多轮对话里若先 `orchestrator_execute` 再 `orchestrator_execute` 一次，设备被重复设置/重复录制。

### [占位] O7. "动态调整：根据中间结果调整后续任务"未实现
- 位置：`orchestrator.py:18`（docstring 承诺）
- 现象：`execute()` 跑完一个任务只把 `task.result` 塞进 `results` dict（`:272`），后续任务的 `tool_params` 是写死的，**不会读取前序任务结果**。没有参数模板（对比 `workflow_engine.py:9` 的 `{{variable}}` 模板——那才是真实现）。
- 后果：任务 B 不知道任务 A 测出的频率/信号带宽，流水线是"脚本"不是"规划"。

### [建议] O8. handler 调用签名不一致
- 位置：`orchestrator.py:306` `task.result = task.handler(task.tool_params)`
- 现象：handler 被当作 `Callable[[Dict], Any]` 调用，但用户传 lambda 时可能写成 `lambda: ...` 或 `lambda params, extra: ...`，立刻 TypeError。没有文档化签名约定。

### [建议] O9. `clear()` 不取消运行中任务
- 位置：`orchestrator.py:363-366`
- 现象：`self.tasks.clear()` 直接清空 dict，若有任务正在 `_execute_task` 里跑，task 对象的引用仍被栈帧持有，状态写完后 dict 里已没有它——统计失真。

---

## 四、跨模块关系发现

### [真bug] X1. Orchestrator 与 SubagentManager 完全不互通
- 证据：`orchestrator.py` 全文 grep `subagent|Subagent` 零命中；`subagents.py` 全文 grep `orchestrator|Orchestrator` 零命中。
- 后果：白皮书把 4.5（Subagents）和 4.6.3（Orchestrator）描述成协同框架，实际是两个平行系统。编排器任务要么调 `tool_registry.call`，要么调用户传入的 `handler`，**永远不会派发给子 agent**。所谓"code_evolver 子 agent"和"自进化编排"是两个独立世界。

### [真bug] X2. self_evolution 完全不经过编排器
- 证据：`self_evolution.py` 全文 grep `orchestrator|subagent|Subagent` 零命中。
- 现象：`SelfEvolutionEngine.evolve()`（`self_evolution.py:396`）自己走 propose → validate → run_in_sandbox → evaluate → commit → apply 直链，不构造 `Task`、不进 `TaskGraph`、不利用 `max_retries`/依赖图/错误恢复。
- 后果：白皮书暗示的"自进化由编排器调度、code_evolver 子 agent 执行"在代码里不存在。自进化的失败恢复靠它自己内部的 try/except，与 orchestrator 的重试/回滚无关。

### [建议] X3. 与 workflow_engine.py 功能重叠
- 证据：`workflow_engine.py:1-17` 描述的 JSON 工作流（多步工具调用、timeout、retry_on_failure、condition、`{{variable}}` 模板）是 orchestrator 承诺但未实现的超集。
- 后果：项目里有**两套多步骤执行框架**：workflow_engine 真有 timeout/条件/模板，orchestrator 只有拓扑排序 + 顺序执行 + 重试。LLM 面对 `workflow_execute` 和 `orchestrator_execute` 两个工具不知道用哪个，orchestrator 实际是 workflow_engine 的劣化重写。

### [建议] X4. 测试只测 CRUD，从不测执行
- 位置：`tests/test_full_integration.py:405-434`（subagent 测试）、`:657-684`（orchestrator 测试）
- 现象：
  - subagent 测试只跑 `create/list/get/stats/destroy`，**从不调 `execute_task`**；
  - orchestrator 测试只跑 `add_task/plan/create_sdr_pipeline/get_stats`，**从不调 `execute()`**。
- 后果：所有执行路径（F1-F4、O1-O7）都没被自动化覆盖。这是"实验室绿"的根源——CRUD 全绿，真机执行全红。

---

## 五、"实验室绿、真机红"归因

| 层 | 实验室（测试） | 真机（SDR 设备） |
|---|---|---|
| 子 agent 创建 | `sm.create()` 返回 id ✅ | 同左 |
| 子 agent 执行 | 测试不覆盖 | `sdr_spectrum_analyze` 真打设备；4/7 类型直接返回"不支持" |
| 流水线规划 | `plan()` 拓扑序正确 ✅ | 同左 |
| 流水线执行 | 测试不覆盖 | 顺序执行、无超时、无回滚；`record_start` 失败则设备频率残留 |
| 并发 | 测试单线程 | LLM 连调 N 次 → N 线程抢同一 SDR backend |
| 自进化 | `evolve()` 内部自测 | 不经过编排器，失败无统一恢复 |

---

## 六、修复优先级建议

| 优先级 | 项 | 动作 |
|---|---|---|
| P0 | O5 回滚缺失 | `create_sdr_pipeline` 为每个副作用任务注册 inverse（如 set_frequency 失败后恢复上一次频率）；或在 Orchestrator 引入 compensation action |
| P0 | F1/O2 超时不生效 | 用 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=task.timeout_s)` 包装所有工具调用 |
| P0 | F2 max_concurrent 不生效 | `submit_task` 前检查 RUNNING 数，超限则排队或拒绝 |
| P1 | F4 4 类子 agent 无后端 | 要么补 `_execute_*` 路由，要么从 `SUBAGENT_PROMPTS` 和工具描述里删除这 4 类，避免 LLM 幻觉 |
| P1 | O3 环依赖检测 | `plan()` 结束后若 `len(order) < len(self.tasks)`，抛 `CircularDependencyError` |
| P1 | O4 依赖 ID 校验 | `add_task` 时校验依赖 ID 已存在或延迟到 `plan()` 前统一校验 |
| P1 | F7 线程安全 | `execute_task` 全程 `with self._lock`，或改成每个 subagent 单线程串行队列 |
| P2 | F3 allowed_tools 落实 | `_execute` 里每次调工具前 `if not self.can_use_tool(name): raise` |
| P2 | O7 动态参数 | 支持 `tool_params` 里 `{{task_id.field}}` 模板，从 `task.result` 取值（对齐 workflow_engine） |
| P2 | X3 框架合并 | 决策：orchestrator 退役，统一到 workflow_engine；或反向。不要长期并存 |
| P3 | F6 死代码清理 | 删除 `_execute_spectrum_analysis` 等三个占位方法 |
| P3 | X4 测试补全 | 至少加一条 `orchestrator.execute()` 端到端用例（mock 工具），覆盖依赖失败跳过路径 |

---

## 七、关键行号索引

- subagents.py 字段/锁：`:76`（timeout_s）、`:238`（_lock）、`:426`（max_concurrent）
- subagents.py 执行路由：`:246`（execute_task）、`:300`（_execute）、`:315-329`（if/else 三类）
- subagents.py 异步线程：`:507-512`
- subagents.py 死代码：`:333`、`:341`、`:349`
- orchestrator.py 规划：`:186`（plan/Kahn）
- orchestrator.py 执行：`:229`（execute）、`:250`（顺序 for）、`:295`（_execute_task）、`:300-329`（重试）
- orchestrator.py 流水线：`:368`（create_sdr_pipeline）
- agent.py 集成点：`:112`（SubagentManager）、`:141`（Orchestrator）、`:1691`（_register_subagent_tools）、`:2177`（_register_orchestrator_tools）
- self_evolution.py：`evolve()` 在 `:396`，全文无 orchestrator/subagent 引用
- 测试盲区：`tests/test_full_integration.py:405`、`:657`
