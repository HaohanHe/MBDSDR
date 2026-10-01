# A2 · mbdsdr_ai 假闭环工具审计（只读）

> 范围：`mbdsdr_ai/`（Python 原型）全部工具注册点。
> 方法：以当前代码为准逐行精读 + 全仓 grep 交叉验证；audit_r2 结论已逐条复核，行号漂移处已重新标定。
> 边界：只读，未改代码、未动 git。拿不准标「推断」。
> 审查日期：2026-10-01。基线 HEAD=408e739。

---

## 0. TL;DR

- 全仓工具名注册总数（去重前）：**agent.py 128 + sdr_tools.py 137 + tool_registry.py 内置 63 = 328 个 `name=`**；跨文件重名仅 `fft_filter`（tool_registry.py:2128 与 :2257 重复注册）。
- **真正"假闭环/空转/编造"集中在 9 个 category、约 45 个工具**（agent.py 内注册），与 spec §0"假闭环工具全部在 Python 原型"判断一致。
- 其中 **3 个 category 属于"整机从未真正工作"**：`workflow_recorder`（录制从未落盘）、`hook`（无事件源）、`learning`（只写日志不影响行为）。
- `evolution` 工具**默认不注册**（`config.enable_self_evolution` 门控，agent.py:193/211），但一旦开启，LLM 仍走不到 `apply/confirm`——高风险提案卡死。
- 测试侧"实验室绿"：`tests/test_full_integration*.py` 对 hooks/subagents/orchestrator/workflow_engine/self_learning **只测 CRUD/存在性，从不调执行路径**；12 个模块仅有 import/hasattr 断言。

---

## 1. 注册体系（先讲清机制，再判工具）

### 1.1 注册表与分发
- `tool_registry.py:72` `self.tools: Dict[str, Dict]`，每条含 `{definition, handler, available, category}`。
- `register()` 在 `tool_registry.py:80`，签名 `register(name, description, parameters, handler, category="general")`。
- 分发：
  - LLM 自由调用 → `tool_registry.call_from_model()`（tool_registry.py:390）→ `call()`（:235）。
  - 工作流/调度器调用 → `agent._workflow_tool_executor()`（agent.py:1841）→ `tool_registry.call()`。
- 内置工具由 `tool_registry.register_builtin_tools(agent_ref)`（agent.py:196 → tool_registry.py:483）注册 63 个 meta/voice/broadcast/digital_modes/satellite/gnss/设备参数/dsp 工具。
- SDR 主体由 `register_sdr_tools(self)`（agent.py:215 → sdr_tools.py:84）注册 137 个。

### 1.2 注册入口（agent.py `__init__`，195–328）
- 无条件注册：memory/guardian/workflow/scheduler（199–208）→ sdr_tools（215）→ hooks/subagents/pose/workflow_recorder/file_tracker/plugin/judge/learning/orchestrator/code_editor/astronomy/amr（221–254）→ 各解码 DSP category（255–278）→ 一堆外部适配器 register_*（282–323）。
- **门控**：`if self.evolution: self._register_evolution_tools()`（agent.py:211）；`self.evolution = SelfEvolutionEngine() if config.enable_self_evolution else None`（agent.py:193）。**默认关闭。**

### 1.3 全量工具清单（仅列"假闭环/存在性"相关 category；真实 SDR/DSP/解码工具见 §5 摘要，不逐行列）

> 判定取值：**真实** / **假闭环**（空转·静默失败·编造·仅注册无实现）/ **半真**（有后端但承诺能力未兑现）/ **仅存在性**（测试当存在断言、无实际能力）。

| 工具名 | category | 注册位置 | 判定 | 判定依据 file:line |
|---|---|---|---|---|
| `workflow_record_start` | workflow_recorder | agent.py:2269 | **假闭环** | 调用 `wr.start_recording` 本身能建空 Recording，但唯一喂数据的 `record_tool_call` 从不被真调（见下）；`recordings` 永远空 |
| `workflow_record_stop` | workflow_recorder | agent.py:2285 | **假闭环** | 停一个从未录到步骤的 Recording，返回 steps=0 |
| `workflow_template_create` | workflow_recorder | agent.py:2293 | **假闭环** | 源 recording 无 steps；`_parameterize` 用"参数描述"比"录制值"字符串相等，永不命中（workflow_recorder.py:261-277，R2-35 B7 仍在） |
| `workflow_template_list` | workflow_recorder | agent.py:2310 | **假闭环** | 列的是 `recordings`（永远空）+ 磁盘 templates；启动只 `_load_templates` 不 `_load_recordings`（workflow_recorder.py:414） |
| `workflow_recorder_status` | workflow_recorder | agent.py:2318 | **假闭环** | total_recordings 恒 0 |
| ↳ 根因 | — | agent.py:1846 | — | `self.wr.record_tool_call(tool_name, params, result.success)`：①`self.wr` 全文件从未赋值（只有 `self.workflow_recorder`@164）→ AttributeError；②签名要求 4 必填位置参（workflow_recorder.py:187-194），只传 3 个 → TypeError；且把 bool 当 result 传。两层异常都被 agent.py:1847 `except Exception: pass` 吞 |
| ↳ 主循环也不录 | — | tool_registry.py:390 call_from_model | **假闭环** | LLM 自由调用路径从不调 `record_tool_call`；grep 全仓 `record_tool_call` 生产调用点仅 agent.py:1846 一处（且坏） |
| `hook_list` | hook | agent.py:2076 | **假闭环** | HookManager 生产路径零注册内置钩子（grep `hook_manager.register(` 仅测试/工厂），list 恒空或只含 LLM 手动加的 |
| `hook_trigger` | hook | agent.py:2084 | **假闭环** | 全仓唯一生产 `trigger` 点就是它自己（agent.py:2095）；LLM 手动 fire 任意 event_type，但监听器数恒 0 |
| `hook_history` | hook | agent.py:2100 | **假闭环** | 历史只来自 `hook_trigger` 手动 fire；SDR/硬件/agent 主循环从不发 `sdr.signal_detected`/`agent.tool_call` 等事件（grep 事件发射点 0） |
| `hook_stats` | hook | agent.py:2115 | **假闭环** | 同上，触发计数只累加手动 fire |
| ↳ 死工厂 | — | hooks.py:383-429 | **假闭环** | `create_signal_alert_hook`/`create_auto_record_hook` 监听 `sdr.signal_detected/lost`，但该事件从无人发；`create_logging_hook`(367) 生产也无人注册 |
| `learning_record` | learning | agent.py:2524 | **半真** | 真写经验记录（持久化），但 score 由 LLM 自己手填；引擎不评判（judge 注入未用） |
| `learning_learn` | learning | agent.py:2543 | **假闭环** | `learn_batch` 只是循环 `learn_from_experience`，无聚合/无合并/无置信度更新（self_learning.py:200-264，R2-32 §2.1/2.4 仍在） |
| `learning_suggestion` | learning | agent.py:2557 | **假闭环** | 词重叠匹配中文失效；且"应用"环节不存在——`pattern.applied` 恒 False，不注入主循环（self_learning.py:88，R2-32 §2.3） |
| `learning_experiences` | learning | agent.py:2571 | **半真** | 只读列表，真 |
| `learning_patterns` | learning | agent.py:2586 | **半真** | 只读列表，真但内容空/无意义 |
| `learning_stats` | learning | agent.py:2601 | **半真** | 只读统计，真 |
| `orchestrator_add_task` | orchestrator | agent.py:2613 | **半真** | 真入任务图；但依赖 ID 不校验存在性、环依赖静默丢弃（orchestrator.py R2-34 O3/O4） |
| `orchestrator_plan` | orchestrator | agent.py:2634 | **半真** | Kahn 拓扑排序真；环依赖静默缺任务不报错 |
| `orchestrator_execute` | orchestrator | agent.py:2642 | **半真** | 顺序真调 tool_registry.call，但 `max_parallel/timeout_s` 均不生效、无回滚、无幂等（R2-34 O1/O2/O5/O6） |
| `orchestrator_list` | orchestrator | agent.py:2656 | **真实** | 只读 |
| `orchestrator_stats` | orchestrator | agent.py:2664 | **真实** | 只读回显 |
| `orchestrator_pipeline` | orchestrator | agent.py:2672 | **半真** | 生成 `create_sdr_pipeline` 任务清单真；失败后设备频率/录制残留无逆操作 |
| `evolution_propose` | evolution | agent.py:1974 | **半真**（默认不注册） | 真建 Proposal 入内存/version_store；但 schema 无 `real_path`（agent.py:1978-1985），真落盘分支不可达 |
| `evolution_evaluate` | evolution | agent.py:1995 | **半真**（默认不注册） | 已修：无测试用例默认 reject 而非 accept（self_evolution.py:265-269）；code+real_path 跑 py_compile（:295-298）。但 LLM 途径 real_path 永远空，只走"无证据"路径 |
| `evolution_commit` | evolution | agent.py:2012 | **半真**（默认不注册） | 真快照入 version_store |
| `evolution_rollback` | evolution | agent.py:2033 | **半真**（默认不注册） | 真回退 version_store；`_disk_backups` 内存字典，崩溃即失（self_evolution.py:112） |
| `evolution_history` | evolution | agent.py:2050 | **真实**（默认不注册） | 只读 |
| `evolution_status` | evolution | agent.py:2061 | **真实**（默认不注册） | 只读 |
| ↳ 缺失工具 | — | — | **假闭环** | 无 `evolution_apply` / `evolution_confirm` / `evolve` 一键工具；grep agent.py 零命中。`SelfEvolutionEngine.apply()`(:415)/`confirm()`(:360)/`evolve()`(:483) public 但 LLM 不可达；apply 非 code 分支返回"虚拟生效"（self_evolution.py:452） |
| `workflow_list` | workflow | agent.py:1883 | **真实** | 列预设 workflow JSON 真 |
| `workflow_execute` | workflow | agent.py:1890 | **假闭环** | 引擎线性跑步真，但**跨步 `{{var}}` 回填断**：executor 返回 `result.content` 散文 str（agent.py:1850），引擎期望 `.content`/dict（workflow_engine.py:575-586），`result_dict` 恒 None → 后续步拿到字面量 `"{{recording_path}}"` 等（R2-35 B1/B2 仍在） |
| `workflow_trigger_match` | workflow | agent.py:1904 | **半真** | `match_trigger` 函数真，但主聊天循环从不自动调它（描述却承诺"说短语自动执行"） |
| `subagent_create` | subagent | agent.py:2127 | **真实** | 真建 Subagent；但描述宣传 7 类"有自己工具集和上下文"，实际无 LLM 循环 |
| `subagent_list` | subagent | agent.py:2142 | **真实** | 只读 |
| `subagent_execute` | subagent | agent.py:2150 | **半真** | 已重构：3 类(spectrum_analyzer/satellite_tracker/baseband_recorder)真调 sdr_* 工具（subagents.py:341-352）；通用路径可传 `input_data.tool_name` 真调任意工具(:332-338)；其余 4 类(signal_decoder/interference_hunter/hardware_controller/code_evolver)抛 NotImplementedError(:355)。但**不是 AI 子代理**——无 LLM 推理、无多步、system_prompt/context/model_manager 存而不读(:227-233)；`timeout_s` 不生效（R2-34 F1） |
| `subagent_stats` | subagent | agent.py:2167 | **真实** | 只读 |
| `pose_get` | pose | agent.py:2180 | **半真** | 回显 pf 状态真，但状态全靠 LLM 手填 |
| `pose_update_imu` | pose | agent.py:2188 | **假闭环** | 9 个 IMU 数值全部来自 LLM JSON；pose.py 无任何 I2C/SPI/serial 读取；描述却称"来自 BMI260+TMAG5273"（R2-36 S1 仍在） |
| `pose_update_gps` | pose | agent.py:2210 | **假闭环** | 经纬度/卫星数同样 LLM 手填，描述称"来自 ATGM336H" |
| `ar_project_satellite` | pose | agent.py:2231 | **半真** | 投影数学真，但建立在虚构位姿上 |
| `ar_pointing_guidance` | pose | agent.py:2250 | **半真** | 方向引导数学真；同 `astro_pointing_guidance` 的 `if False` 已在 astronomy category 修了吗？未修——见下 |
| `scheduler_add/enable/disable/status/list/tick` | scheduler | agent.py:1915-1962 | **半真** | CRUD 真、后台线程 tick 真；但 `schedule_type="cron"` 无分支、`cron_expression` 从不解析（scheduler.py:265-272，R2-58 #6/#7）；once 任务 enable 时改写 next_run |
| `judge_evaluate` | judge | agent.py:2481 | **半真** | 规则评分真；第 4 位置参恒传 `""`（agent.py:2493，R2-36 S3）；`use_llm=True` 路径依赖外部 key |
| `judge_history/stats` | judge | agent.py:2498/2512 | **真实** | 只读 |
| `file_change_track/history/stats/changelog` | file_tracker | agent.py:2330/2349/2378/2386 | **真实** | 真记录变更、真持久化 |
| `file_change_revert` | file_tracker | agent.py:2364 | **真 bug 非假闭环** | 条件表达式双执行 revert_to + 硬编码 success=True（agent.py:2373，R2-36 B1 仍在）——是 bug，不是空转，列出供修复 |
| `plugin_list` | plugin | agent.py:2404 | **假闭环** | `plugins/` 目录为空（实测 `ls mbdsdr_ai/plugins/` 无文件），discover 恒空 |
| `plugin_load/enable/disable/stats` | plugin | agent.py:2412/2426/2440/2454 | **假闭环** | 在空插件集上操作；无插件可加 |
| `plugin_install` | plugin | agent.py:2462 | **半真** | 从 source_path 复制文件真，但无插件生态配合 |
| `memory_write/search` | memory | agent.py:1800/1820 | **真实** | memory.py 真持久化/检索 |
| `guardian_status/rollback/list` | guardian | agent.py:1858/1865/1872 | **真实** | Guardian 快照/回滚真（测试 507-540 有文件内容级断言） |
| `astro_pointing_guidance` | astronomy | agent.py:2955 | **真 bug** | `AntennaParams(beamwidth_deg=...) if False else None`（agent.py:2968），LLM 传的 beamwidth_deg 被丢弃，描述却承诺"输出是否在波束内"（R2-36 B2 仍在） |
| `amr_classify` / `amr_extract_features` | amr | agent.py:2985/3000 | **假闭环** | LLM 不传 iq_samples 时静默改用内置旋转正弦波做"分类"并返回 success（R2-36 S2，amr.py） |
| `amr_add_sample/stats` | amr | agent.py:3015/3031 | **半真** | 样本库写入真 |

---

## 2. 「摘除 or 接真实」决策表

> 原则：**不建议直接删真实模块**；只摘"注册给 LLM 的工具面"，保留后端类供未来接线或测试。连锁影响必须列出。

| # | 工具/category | 建议 | 缺什么（接真实）/ 连锁影响（摘除） |
|---|---|---|---|
| D1 | `workflow_record_*` 5 个 | **接真实（小修）或摘除** | 接真实：agent.py:1846 改 `self.workflow_recorder.record_tool_call(tool_name, params, result.content, result.success, duration_ms)`；并在 `call_from_model` 后补一次录制钩子。摘除连锁：删 agent.py:2264-2323 整段；测试侧 test_full_integration_v2.py 对 `workflow_recorder` 的 hasattr 断言（:102/:109）要同步改。**推荐先接真实**（一处笔误+一个参数），性价比最高 |
| D2 | `hook_*` 4 个 | **摘除 LLM 工具面，保留 HookManager 类** | 接真实成本高：需要 sdr_backend/agent 主循环在信号检测、工具调用前后真发事件。当前零事件源，hook_trigger 是"让 LLM 对着空气喊话"。摘除连锁：删 agent.py:2071-2120；测试 test_hooks_module（:396-419）删除或改为纯单元测 HookManager；hooks.py 可留作内部事件总线（不删模块） |
| D3 | `learning_*` 6 个 | **降级为"经验记录"或摘除 suggestion/learn** | `learning_record/experiences/patterns/stats` 是真日志，可留；`learning_learn`(无聚合) 与 `learning_suggestion`(中文分词失效+不注入) 是假闭环。摘除连锁：test_self_learning_module（:637-671）删 :659-664 两条断言；不影响其他模块 |
| D4 | `orchestrator_*` 6 个 | **摘除，统一到 workflow_engine** | R2-34 X3 已指出：orchestrator 是 workflow_engine 的劣化重写（无 timeout/条件/模板），LLM 面对两个多步执行框架不知道用哪个。摘除连锁：删 agent.py:2608-2688；test_orchestrator_module（:674-701）删除；orchestrator.py 模块保留不 import。注意 scheduler 不依赖它（grep 零命中） |
| D5 | `evolution_*` 6 个 | **保持默认关闭 + 补 apply/confirm 工具，或摘除 LLM 面** | 现状：门控默认 off，风险可控。但一旦开，缺 evolution_apply/confirm → 高风险提案卡死；apply 虚拟生效误导。二选一：(a) 补 `evolution_apply`/`evolution_confirm` 两个工具并让 version_store 真回灌 system_prompt；(b) 在描述里如实标注"提案记录模式，不实际改运行时"。摘除连锁：test_self_evolution_module（v2:739-784）直连引擎测，不依赖 agent 注册，摘除工具面不破坏该测试 |
| D6 | `workflow_execute` 跨步变量 | **接真实（修 executor 契约）** | 修 agent._workflow_tool_executor：除返回 content 外，让 sdr_tools 关键工具在散文旁吐 JSON sidecar；workflow_engine.py:575-586 才能回填。不修则 `interference_localization`/`noaa_apt_receive_decode` 等预设工作流真机必失败（调谐到 `"{{interference_freq_hz}}"`）。摘除则这些"一键工作流"宣传落空 |
| D7 | `pose_update_imu/gps` | **摘除或改名为 pose_simulate_*** | 无硬件驱动线程推数据，LLM 手填 = 让 AI 幻想传感器。建议：(a) 接真实需 hal/gnss_monitor 侧补推数据流（工作量大）；(b) 短期把工具描述改为"手动喂入仿真位姿"，别再写"来自 BMI260/ATGM336H"。摘除连锁：test_pose_module（:454-505）删除或改为纯算法单测 |
| D8 | `plugin_*` 6 个 | **摘除（空生态）** | plugins/ 目录为空，无任何插件可加载/启用。保留 plugin_manager 类（test_plugin_manager.py 单测在用），但从 LLM 工具面摘除。摘除连锁：test_full_integration_v2.py:102/:107 对 plugin_system/plugin_manager 的 hasattr 断言保留（类还在），只删 LLM 工具注册段 agent.py:2399-2475 |
| D9 | `subagent_execute` 4/7 类 | **收窄描述，不摘除** | 3 类已真调、4 类已诚实抛 NotImplementedError。把 agent.py:2128 描述里宣传的 7 类改成实际支持的 3 类 + "通用路径传 tool_name"。无需摘工具 |
| D10 | `scheduler` cron | **接真实（小修）或摘 cron 分支** | scheduler.py:265-272 补 cron 解析，或在 schema 里删掉 cron 选项（schema 当前只暴露 interval/once，agent.py:1938，所以 cron 实际不可达——降级为文档 bug）。摘除优先级低 |

---

## 3. 摘除执行顺序建议（最小破坏面）

> 原则：先摘"纯空转、零真实后端、测试只断言存在性"的；后动"半真、有真实副作用"的；每步后跑 `tests/test_full_integration*.py` 确认绿。

1. **第一批（零风险纯摘除，只删 LLM 工具注册段，不动后端模块）**
   - `plugin_*`（D8）：plugins/ 空目录，删 agent.py:2399-2475。后端 plugin_manager.py 与单测保留。
   - `orchestrator_*`（D4）：删 agent.py:2608-2688 + 删/改 test_orchestrator_module。orchestrator.py 保留文件但不被 agent import。
   - `hook_*`（D2）：删 agent.py:2071-2120 + 删 test_hooks_module 中对"生产触发"的隐含预期。hooks.py 保留。
2. **第二批（接真实小修，不摘）**
   - D1 `self.wr` 笔误（agent.py:1846）：改属性名 + 补全 4 参数。
   - D6 executor 契约（workflow_engine.py:575-586 + sdr_tools JSON sidecar）。
   - D10 cron schema 文案。
3. **第三批（收窄描述 / 降级）**
   - D3 删 `learning_learn`/`learning_suggestion` 两个假闭环工具，保留 record/list/stats。
   - D7 pose_update_* 描述改"仿真喂入"，或摘 imu/gps 两个 update 工具。
   - D9 subagent_create 描述收窄到 3 类。
4. **第四批（决策待定，影响最大）**
   - D5 evolution：默认保持 off；决定补 apply/confirm 工具还是如实降级描述。涉及 self_evolution.py 回灌逻辑，需独立 wave。

> 禁止操作：不要删 hooks.py/subagents.py/orchestrator.py/workflow_recorder.py/self_evolution.py 模块文件本身（单测与未来接线在用）；不要直接删 file_change_revert 等有 bug 但真实工作的工具——那是 W2b 修 bug，不是 A2 摘除。

---

## 4. 禁止把"测试存在性断言"误当成"真实能力"

`tests/test_full_integration.py:94-128` 对以下对象只做 `__import__` / `hasattr(agent, x)`，**无任何行为验证**（R2-49 §4.1 列了 12 个，复核如下）：

| 模块/属性 | 断言位置 | 真实能力？ |
|---|---|---|
| `self_evolution` | test:99 / hasattr:104 | 半真（见 D5）；且默认 None，:117 v2 已标注"可选" |
| `workflow_recorder` | test:102 / hasattr:103 | **假闭环**（D1），hasattr 过了但录制从不工作 |
| `plugin_system` | test:107 | **假闭环**（D8），plugins/ 空 |
| `orchestrator` | test:104 / hasattr:125 | 半真（D4），测试从不 execute |
| `subagent_manager` | hasattr:123 | 半真；测试 :422-451 从不 execute_task |
| `hook_manager` | hasattr:122 | **假闭环**（D2） |
| `pose_fusion` / `workflow_engine` / `file_tracker` / `llm_judge` / `self_learning` / `guardian` / `code_editor` / `amr_classifier` | hasattr:121-128 | 各类半真/真，见 §1 |
| `config`/`context_manager`/`model_manager`/`memory`/`version_store`/`sandbox` | test:99-100 | 仅 import，无行为测 |

硬编码 True / 恒真式断言（必须修，否则给人"测过了"的错觉）：
- test_full_integration.py:412 `result.record("触发事件", True)`
- test_full_integration.py:448 `result.record("销毁子代理", True)`
- test_full_integration.py:664 `suggestion is None or suggestion is not None`（恒真）

---

## 5. 真实实现摘要（不在假闭环范围，仅计数不逐列）

- `sdr_tools.py` 137 个 `sdr_*`/`vfo_*`/`gimbal_*`/`satellite_*`/`gnss_*`/`gis_*`/`pnt_*`/`openapi_*`/`morse_*`/`meteor_*`/`lro_*`/`satdump_*`/`radio_*`/`instrument_*`/`digital_mode_*`/`platform_info`：绝大多数真调 `sdr_backend` / dsp 模块 / 真实解码库。例外：`satdump_live/process` 依赖外部 SatDump 二进制，常态恒"未安装"（R2-58 #1-#5）；`meteor_*` LRO EKF 量纲错误（R2-58 #10-#12）——这些是 W2d 真链路审计（A4）范围，本报告不展开。
- `tool_registry.py:483-2289` 内置 63 个：meta(list_tools/context_status/model_status/list_models/switch_model/tool_log)、codec2/freedv、hdradio、fsk/baudot/minimodem、satellite image/lrpt/tle/sgp4/doppler、gnss(rinex/spp/ntrip)、kismet、dmr/p25/dsd/dab、rtlsdr/hackrf/limesdr/bladerf/osmosdr 参数、dsp_engine(fft/fir/agc/resample)。均真调用对应模块（device 参数类依赖硬件存在性，无硬件时返回诚实空态——A4 核实）。
- agent.py 内解码 DSP category（ft8/fst4/cw/adsb/rds/apt/aprs/ax25/acars/wfm/analog/demod 等约 50 个）：真调对应 decoder 模块。
- `code_editor_*` 10 个：真文件读写/importlib reload/git（高危但真实，P2 安全闸口另案）。
- `astronomy_*` 9 个：真球面天文数学（除 astro_pointing_guidance 的 `if False` bug）。
- `memory_*`/`guardian_*`/`file_change_*`(除 revert 双执行 bug)：真实持久化。

---

## 6. 自检记录

- [x] 通读 _PHASE3_SPEC.md（A2 范围、§3 红线）。
- [x] 全量 grep 注册点：agent.py 128 + sdr_tools.py 137 + tool_registry 63，跨文件重名仅 `fft_filter`。
- [x] 复核 audit_r2 关键断言（以当前代码为准，行号已重标）：
  - `self.wr` 笔误：仍在 agent.py:1846，属性名 `self.workflow_recorder`@164。✓
  - record_tool_call 签名 4 必填参：workflow_recorder.py:187-194。✓
  - hook 唯一生产 trigger 点 = hook_trigger 工具：grep 确认。✓
  - subagents 已重构：3 类真调、4 类 NotImplementedError（比 R2-34 报告的"4 类假成功"已改善）。✓
  - self_evolution 无测试默认 accept 已修为默认 reject（self_evolution.py:265-269）；apply/confirm 工具仍未注册。✓
  - astro_pointing_guidance `if False` 仍在 agent.py:2968。✓
  - file_change_revert 双执行仍在 agent.py:2373。✓
- [x] 门控确认：evolution 工具默认不注册（agent.py:193/211）。
- [x] 测试存在性断言逐条定位（test_full_integration.py:94-128 / 396-451 / 548-701；v2:739-784）。
- [x] 未改任何代码、未动 git；所有判定带 file:line；拿不准处标注「推断」（本报告多为实读，极少推断）。
- 遗留（不在 A2 范围，转 A4/W2d）：satdump_live 假成功、meteor LRO EKF 量纲、sdr_backend 无硬件空态、device 参数类工具诚实性。
