# 32 — 自进化 / 自学习模块深度审查（R2）

审查范围：
- `mbdsdr_ai/self_evolution.py`（572 行）
- `mbdsdr_ai/self_learning.py`（399 行）
- 依赖：`mbdsdr_ai/sandbox.py`、`mbdsdr_ai/version_store.py`
- 关联：`mbdsdr_ai/agent.py` 中的接线
- `hermes-self-evolution/` 独立框架：`evolution/core/{config,constraints,fitness,dataset_builder,external_importers}.py`、`evolution/skills/{evolve_skill,skill_module}.py`

---

## 0. 总体结论（TL;DR）

| 模块 |  verdict |
|---|---|
| `self_evolution.py` | **半真半假的流程壳**：生命周期状态机完整、版本存储和原子写盘代码存在，但**绝大多数"应用"是虚拟的**——没有任何代码把 version_store 里的内容回灌到运行中的 Agent（系统提示词、工具描述、信号处理流水线）。LLM 可调用的工具链**缺 `apply` 和 `confirm` 两个关键环节**，高风险提案永远卡死。 |
| `self_learning.py` | **纯日志记录器**：没有强化学习、没有参数调优、没有提示词优化、没有跨经验聚合。`judge` 注入了但从未使用；`applied` 字段永远是 False；每条高分经验独立生成一条 frequency=1 的模式，不做合并。 |
| `hermes-self-evolution/` | **第三方参考框架原样克隆，未接入 MBDSDR**。GEPA 优化循环本身可跑（需 dspy + LLM API），但：① 进化结果**只写到 `output/` 目录，从不回写 skill 文件、不建分支、不建 PR**；② `run_test_suite` 约束定义了但**从未被调用**；③ GEPA 用的 fitness 是**关键词重叠启发式**（`skill_fitness_metric`），不是 LLM-as-judge；④ 优化的"agent"只是 DSPy ChainOfThought 把 skill 文本当 instructions 跑一遍，**根本不是真实 hermes-agent，更不是 SDR 任务**。 |
| 安全防线 | 沙箱**文档声称文件系统隔离/网络拦截/危险操作拦截，实际全部失效或可绕过**；回滚备份只在内存里，进程退出即丢失；`real_path` 落盘路径**不在工具参数 schema 中**，LLM 永远传不进来。 |

---

## 1. `self_evolution.py` 逐条发现

### 1.1 [空壳] "应用到主系统"是假的——version_store 从不回灌

- **位置**：`self_evolution.py:328-365`（`apply()`）
- **现象**：`apply()` 对非 code 类型只做 `proposal.status = "applied"`，然后返回 `"已应用 {target_type}: {target_name}（虚拟生效，未指定 real_path）"`。
- **证据**：全仓库 grep `get_all_files` / `get_file` 的调用方——除了 `commit()` 内部把快照存进去，**没有任何代码从 version_store 读文件并更新 Agent 的 system_prompt、tool_descriptions、config 或 pipeline**。
- **结论**：所谓"修改系统提示词、工具描述、配置参数"的进化，commit 后只存在于 `~/.mbdsdr/evolution/objects/<sha256>` 里的纯文本文件，运行中的 LLM 看到的提示词永远不变。**这是记录"建议"，不是执行进化。**

### 1.2 [空壳] LLM 工具链缺 `apply` 和 `confirm` 两个关键环节

- **位置**：`agent.py:1538-1638`（`_register_evolution_tools`）
- **现象**：注册的工具只有 6 个：`evolution_propose` / `evolution_evaluate` / `evolution_commit` / `evolution_rollback` / `evolution_history` / `evolution_status`。
- **缺失**：
  - **没有 `evolution_apply` 工具**——`SelfEvolutionEngine.apply()` 是 public 方法但 LLM 无法触发。
  - **没有 `evolution_confirm` 工具**——`SelfEvolutionEngine.confirm()` 是 public 方法但 LLM 无法触发。
  - **没有 `evolve` 一键循环工具**——`evolve()` 方法存在但 agent.py 里没有任何地方调用它（grep `.evolve(` 无结果）。
- **后果**：
  - `evolve()` 在 `needs_confirmation()` 返回 True（medium/high 风险）时会停在 `"awaiting_confirmation"` 状态（`self_evolution.py:462-466`），但因为没有 confirm 工具，提案永远停在 `evaluated` 状态，无法继续。
  - 即使 LLM 手动走完 propose→evaluate→commit，也没有工具把修改真正 apply 出去。

### 1.3 [真bug] `evolution_propose` 工具 schema 不接受 `real_path`——真落盘路径永远为空

- **位置**：`agent.py:1545-1555`（propose 工具 parameters）+ `self_evolution.py:346-365`
- **现象**：工具 schema 只暴露 `target_type/target_name/description/proposed_change/risk_level`，**没有 `real_path` 字段**。handler 里 `evo.propose(**args)` 传入的 args 里永远不含 `real_path`。
- **后果**：`apply()` 中 `if proposal.real_path and proposal.target_type == "code"` 分支永远走不到（`real_path=""`），所有 code 类型进化也走"虚拟生效"分支。文档和代码注释承诺的"原子写盘+备份+回滚"路径**在实际运行时不可达**。

### 1.4 [真bug] 无测试用例时评估默认通过——空提案自动 accept

- **位置**：`self_evolution.py:248`
  ```python
  pass_rate = passed / max(1, len(tests)) if tests else 1.0  # 无测试用例默认通过
  ```
- **后果**：一个没有任何 test_cases 的提案，`evaluate()` 返回 `pass_rate=1.0`、`recommendation="accept"`。在 `evolve()` 流程里（line 454-459），只要 `test_cases` 为空列表，评估步骤直接跳过 reject 判断。LLM 可以提交一个完全没有验证的修改并自动 accept。
- **对照文档承诺**：docstring 说"评估维度：正确性/性能/兼容性/改进度"，实际无测试时全部默认满分。

### 1.5 [真bug] 非 code 类型修改跳过所有安全检查

- **位置**：`self_evolution.py:179-182`
  ```python
  else:
      # 非代码修改（提示词、描述、配置）默认安全
      proposal.status = "validated"
      return True, {"safe": True, ...}
  ```
- **后果**：`config` 类型（系统配置）、`pipeline` 类型（处理流水线）的修改不经过 `sandbox.validate_code`，也不经过任何静态检查。一个恶意或错误的配置修改（比如把采样率改成 0、把中心频率改成卫星频段外）直接进入 commit/apply。虽然 apply 本身是虚拟的，但一旦 1.3 被修复（real_path 暴露），这里就是漏洞。

### 1.6 [真bug] 沙箱"文件系统隔离"名不副实

- **位置**：`sandbox.py:1-13`（docstring 声称"文件系统隔离（只能访问沙箱目录）"）+ `sandbox.py:201-212`（实际 subprocess.run）
- **现象**：子进程只设置了 `cwd=self.work_dir` 和裁剪过的 `env`，**没有 chroot、没有 mount namespace、没有 seccomp、没有目录白名单**。子进程是完整 Python 解释器，可以 `open('/etc/passwd')`、可以读项目源码、可以 `os.listdir('/')`。
- **更严重**：`SAFE_EXEC_TEMPLATE`（line 61-117）用 `{"__builtins__": safe_builtins}` 限制内置函数，但这是经典的 Python 沙箱逃逸模式——用户代码可通过 `().__class__.__bases__[0].__subclasses__()` 找到 `subprocess.Popen`、`os._exit` 等已加载类，进而执行任意系统调用。预扫描正则（line 142-162）只匹配行首的 `import os` / `eval(` 等，对反射调用完全无效。
- **缓解因素**：沙箱只用于"执行 proposed_change 作为测试"，且超时 10 秒。但 docstring 承诺的安全边界是假的。

### 1.7 [真bug] `_disk_backups` 只在内存——进程重启后回滚丢失真文件

- **位置**：`self_evolution.py:108`、`self_evolution.py:377-384`
- **现象**：磁盘备份存于 `self._disk_backups: Dict[str, tuple]`，是进程内字典。回滚时遍历这个字典写回旧内容。
- **后果**：如果 `apply()` 真落盘后进程崩溃/退出，重启后 `_disk_backups` 为空，`rollback()` 只能回退 version_store 里的虚拟文本，**已经被覆盖的真实 .py 文件无法恢复**。防幻觉变砖承诺在崩溃场景下失效。
- **对照**：version_store 本身是持久化的（JSON + objects 目录），但它存的是虚拟文件名→内容哈希，不是真实磁盘文件路径。

### 1.8 [建议] `rollback()` 一次性恢复所有磁盘备份，不按 proposal_id 选择

- **位置**：`self_evolution.py:377-384`
- **现象**：`for pid, (rp, old) in list(self._disk_backups.items())`——不管调用方想回滚哪个版本，所有备份文件都被恢复。
- **后果**：如果连续 apply 了多个不同文件的修改，rollback 会把所有文件都恢复到各自最早的备份，而不是"回到上一个版本"。语义错误。

### 1.9 [占位] "用户投稿/专家委员会"流程无队列、无审查工具

- **位置**：`self_evolution.py:495-530`
- **现象**：`submit_user_contribution()` 和 `review_contribution()` 存在，但：
  - 没有持久化的待审查队列（proposals 字典在内存）。
  - 没有对应 LLM 工具（agent.py 未注册）。
  - "专家委员会"只是 `reviewer: str = "expert_committee"` 默认字符串，没有实际的多专家投票/评审逻辑。
- **判定**：接口占位。

### 1.10 [建议] `evaluate()` 无 baseline 对比——"改进度"维度缺失

- **位置**：`self_evolution.py:205-260`
- **现象**：评估只跑提案自己的 test_cases，不跑 original_content 的对照测试。docstring 承诺"改进度：相比原始版本的提升"，实际从未计算。无法判断是进化了还是只是没退化。

---

## 2. `self_learning.py` 逐条发现

### 2.1 [空壳] "学习"只是字符串拼接——无统计、无聚合、无更新

- **位置**：`self_learning.py:217-264`（`_analyze_tool_sequence`）
- **现象**：对每条高分经验，把工具名用 ` -> ` 拼成字符串，生成一条 `LearnedPattern(frequency=1, confidence=score/10)`。
- **问题**：
  - **不做模式合并**：1000 条高分经验都用了 `search -> read -> write`，会生成 1000 条独立 pattern，每条 frequency=1，而不是合并成一条 frequency=1000 的强模式。
  - **不做置信度更新**：后续同类经验出现时，不更新已有 pattern 的 confidence/avg_score。
  - **不区分好坏**：只学高分（≥7.0）经验，低分经验里的错误模式被丢弃，不学"不要做什么"。
  - **trigger 只是 question[:100]**：不是真正的触发条件/特征提取。

### 2.2 [空壳] `judge` 注入了但从未使用

- **位置**：`self_learning.py:124`（`self.judge = judge`）
- **现象**：`SelfLearningEngine.__init__` 接收 `judge` 参数（agent.py 传入 `self.llm_judge`，见 `agent.py:138`），但全文件 grep `self.judge`——**除了赋值那一行，再无任何调用**。
- **后果**：docstring 承诺的"LLM-as-Judge 评分→获取反馈"环节，实际评分由调用方（LLM 自己调用 `learning_record` 工具时手动传 score 字段）填写，引擎内部不做任何评判。

### 2.3 [空壳] `applied` 字段永远是 False——学到的模式从不影响行为

- **位置**：`self_learning.py:88`（`applied: bool = False`）+ 全文件 grep
- **现象**：没有任何代码把 `pattern.applied` 设为 True。也没有代码在 Agent 处理新任务时**主动查询并注入**匹配的 pattern 到系统提示词或工具选择中。
- **唯一出口**：`learning_suggestion` 工具（`agent.py:2125-2135`）让 LLM **手动**调用并传入 question，返回一个 JSON。LLM 不调用就什么都不发生。
- **结论**：学习闭环的"应用"环节不存在。这是个只读的经验/模式查询接口，不是闭环。

### 2.4 [真bug] `learn_batch` 标记 `learned=True` 但不产生跨经验洞察

- **位置**：`self_learning.py:200-215`
- **现象**：批量学习就是循环调用 `learn_from_experience`，每条经验独立分析。没有 n-gram 统计、没有频繁序列挖掘、没有错误聚类。所谓"批量学习"只是循环单条学习。

### 2.5 [建议] `get_suggestion` 匹配逻辑过于简陋

- **位置**：`self_learning.py:266-287`
- **现象**：用 `set(question.lower().split()) & set(pattern.trigger.lower().split())` 做词重叠。中文 question 按空格分词基本失效（中文无空格），且 trigger 本身只是 question[:100] 的切片，匹配会退化成"找一条历史上和你问题有共同词的记录"。

### 2.6 [建议] 持久化加载丢弃 answer/tool_calls

- **位置**：`self_learning.py:359-367`（`_load_experiences`）
- **现象**：重启后只恢复 question/score/feedback/timestamp，不恢复 answer 和 tool_calls。这意味着重启后无法对历史经验做重新分析（比如改进学习算法后重跑），只能用已保存的 pattern。

---

## 3. `hermes-self-evolution/` 框架逐条发现

### 3.1 [空壳] 进化结果从不回写 skill 文件——只写报告到 output/

- **位置**：`evolve_skill.py:254-285`
- **现象**：进化完成后，`evolved_skill.md` 写到 `output/<skill>/<timestamp>/`，`baseline_skill.md` 也写到那，`metrics.json` 也写到那。
- **缺失**：
  - **不修改** `skill_path`（原 SKILL.md）。
  - **不创建 git branch、不 git add/commit/push**（config.py:47 `create_pr: bool = True` 但全代码库无任何 git 操作——grep `git ` 只命中 README/PLAN 文档，无 subprocess git 调用）。
  - **不创建 PR**。
- **对照 PLAN.md:208**："writes evolved versions to git branches, creating PRs for human review"——这是规划，代码未实现。
- **判定**：优化循环跑完后，输出一个对比报告就结束了。进化不会真正部署。

### 3.2 [空壳] `run_test_suite` 约束定义了但从未被调用

- **位置**：`constraints.py:55-93`（定义）+ `evolve_skill.py:118-131`（baseline 约束验证）+ `evolve_skill.py:186-204`（evolved 约束验证）
- **现象**：`evolve_skill.py` 中 baseline 和 evolved 的约束验证只调用了 `validator.validate_all()`（检查 size/growth/non_empty/skill_structure），**从未调用 `validator.run_test_suite(hermes_repo)`**。
- **后果**：docstring 承诺的"MUST pass 100% test suite"硬约束在实际进化循环中不执行。一个把 hermes-agent 测试搞挂的 skill 变体，只要 size/frontmatter 合规就能通过。

### 3.3 [真bug] GEPA 优化用的 fitness 是关键词重叠启发式，不是 LLM-as-judge

- **位置**：`fitness.py:107-136`（`skill_fitness_metric`）+ `evolve_skill.py:156-159`
  ```python
  optimizer = dspy.GEPA(metric=skill_fitness_metric, ...)
  ```
- **现象**：`skill_fitness_metric` 是：
  ```python
  expected_words = set(expected_lower.split())
  output_words = set(output_lower.split())
  overlap = len(expected_words & output_words) / len(expected_words)
  score = 0.3 + (0.7 * overlap)
  ```
  即按空格分词后做词重叠率。这是一个**词袋匹配**，根本不评判正确性、不评判过程遵循。
- **而 `LLMJudge` 类（`fitness.py:34-104`）虽然实现了多维度打分（correctness/procedure_following/conciseness + length_penalty），但在 `evolve_skill.py` 里从未被实例化或调用**。grep `LLMJudge` 只在 import 和 class 定义处出现。
- **后果**：GEPA 会优化 skill 文本，让 ChainOfThought 生成的输出和 expected_behavior 的词表重叠率更高——这可能导致 skill 文本变成"把 expected_behavior 的关键词塞进输出"的绕路提示词，而不是真正更好的 skill。**这是典型的"实验室绿、真机红"**：在词重叠代理指标上涨点，在真实任务上可能退化。

### 3.4 [空壳] 进化循环中的"agent"不是真实 hermes-agent

- **位置**：`skill_module.py:84-114`（`SkillModule`）
- **现象**：DSPy 的 `SkillModule` 只是把 skill_text 作为 `TaskWithSkill` signature 的 input field，然后调 `dspy.ChainOfThought` 生成输出。它**不加载 hermes-agent 的工具、不连接 SDR 硬件、不跑真实信号处理**。
- **后果**：整个进化循环评估的是"LLM 读了这段 skill 文本后能不能生成一段和 expected_behavior 词表相近的文本"。这和 MBDSDR 的 SDR 任务（解调、解码、卫星跟踪）毫无关系。hermes-self-evolution 框架本身是给通用 LLM agent skill 做优化的，不是给 SDR 做的。

### 3.5 [空壳] hermes-self-evolution 与 mbdsdr_ai 零接线

- **位置**：全仓库 grep `hermes|evolve_skill|GEPA` 在 `mbdsdr_ai/` 下无任何匹配。
- **现象**：`hermes-self-evolution/` 是一个独立目录（看起来是从 NousResearch 仓库 clone 的参考实现），`mbdsdr_ai/` 不 import 它、不调用它、不依赖它。
- **后果**：它既不是 MBDSDR 自进化能力的一部分，也没有被编排器调用。它是一个放在仓库里的"参考框架"。

### 3.6 [建议] `_discover_hermes_agent_path` 在本项目必然返回 None

- **位置**：`config.py:50-60`
- **现象**：查找 `HERMES_AGENT_REPO` 环境变量、`~/.hermes/hermes-agent`、`../hermes-agent`。MBDSDR 项目里没有 hermes-agent 仓库。
- **后果**：在本项目中直接跑 `evolve_skill.py` 会在 `find_skill()` 处退出（`evolve_skill.py:62-64`），因为 `config.hermes_agent_path / "skills"` 不存在。dry-run 模式可以跑，但实际优化跑不起来。

### 3.7 [建议] 约束检查只看 body 不看 frontmatter——growth 计算有歧义

- **位置**：`evolve_skill.py:121`（`validator.validate_all(skill["body"], "skill")`）+ `constraints.py:119-134`
- **现象**：growth limit 比较的是 body 长度。但 GEPA 修改的是整个 SkillModule 的 skill_text（即 body），frontmatter 保持不变。这本身合理，但 `reassemble_skill()`（`skill_module.py:117-123`）直接拼接 frontmatter + evolved_body，如果 GEPA 在 evolved_body 里生成了额外的 `---` 分隔线，会破坏文件结构。`_check_skill_structure` 只检查开头 500 字符，不检查 body 内的 `---`。

---

## 4. 安全防线专项评估

| 防线 | 文档承诺 | 实际实现 | verdict |
|---|---|---|---|
| 沙箱子进程隔离 | 子进程隔离 | `subprocess.run` 独立进程 | ✅ 真有 |
| 超时控制 | 超时终止 | `timeout=10` 秒 | ✅ 真有 |
| 文件系统隔离 | 只能访问沙箱目录 | 仅 `cwd` 切换，无 chroot | ❌ **假** |
| 网络拦截 | 禁止网络 | 预扫描字符串匹配 + 裁剪 env；但子进程仍可建 socket（`socket` 不在 safe_builtins，但反射可绕过） | ⚠️ 半真 |
| 危险模块黑名单 | 禁止 os/subprocess 等 | 预扫描正则 + safe_builtins；但 Python 沙箱经典逃逸（`__subclasses__`）未防 | ❌ **可绕过** |
| 内存/CPU 限制 | 资源限制 | `max_memory_mb=256` 定义了但**从未使用**（grep 确认无 resource.setrlimit） | ❌ **空壳** |
| 版本快照回滚 | git-like 快照 | VersionStore 内容寻址存储，持久化到磁盘 | ✅ 真有（但只跟踪虚拟文件） |
| 真落盘备份 | 原子写+备份 | `tempfile.mkstemp` + `os.replace` 原子写 | ✅ 真有代码 |
| 崩溃后回滚 | 任何修改可回滚 | `_disk_backups` 内存字典，进程退出即失 | ❌ **不可靠** |
| 高风险用户确认 | 高风险需人工确认 | `needs_confirmation()` 逻辑存在，但**无 confirm 工具暴露给 LLM/用户** | ❌ **卡死** |
| 测试门禁 | 修改后跑测试 | `evolve()` 里 test_cases 为空时默认通过；hermes 框架 run_test_suite 从不调用 | ❌ **失效** |

---

## 5. "实验室绿、真机红"风险点

1. **self_evolution 的评估是自说自话**：test_cases 由 LLM 自己 propose 时附带，evaluate 也是 LLM 自己写的测试代码在沙箱里跑。没有真实 SDR 回归测试集（如真实 IQ 数据解调成功率、FT8 解码正确率、卫星跟踪误差）作为门禁。
2. **self_learning 的"模式"是词表匹配**：在 SDR 这种需要精确数值/时序/信号处理知识的领域，词重叠学习没有意义。
3. **hermes 框架的 fitness 是词袋启发式**：GEPA 会在代理指标上过拟合，真实 SDR 任务上不会有任何提升。
4. **没有真实 SDR 任务的 eval dataset**：dataset_builder 生成的是通用 LLM 测试用例（task_input/expected_behavior 文本对），不是 SDR 信号处理 bench。

---

## 6. 与 orchestrator.py 的关系

- `agent.py:141` 创建 `self.orchestrator = Orchestrator(tool_registry=self.tool_registry)`。
- grep orchestrator.py 中是否引用 self_evolution / self_learning：
  - **结论：orchestrator.py 不感知自进化/自学习**。自进化引擎是 Agent 的一个独立属性，工具注册到 tool_registry，由 LLM 在对话中手动调用。编排器不自动触发进化循环、不在任务前自动查询学习建议、不在任务后自动记录经验。
- 自进化不是编排流程的一环，而是一组被动工具。

---

## 7. 发现汇总表

| # | 类型 | 位置 | 简述 |
|---|---|---|---|
| 1.1 | 空壳 | self_evolution.py:328-365 | apply 对非 code 类型是虚拟生效，version_store 从不回灌运行中 Agent |
| 1.2 | 空壳 | agent.py:1538-1638 | 缺 evolution_apply / evolution_confirm 工具，高风险提案卡死 |
| 1.3 | 真bug | agent.py:1545-1555 | propose 工具 schema 无 real_path，真落盘分支不可达 |
| 1.4 | 真bug | self_evolution.py:248 | 无测试用例时 pass_rate 默认 1.0，空提案自动 accept |
| 1.5 | 真bug | self_evolution.py:179-182 | config/pipeline 类型修改跳过所有安全检查 |
| 1.6 | 真bug | sandbox.py:1-13,201-212 | 文件系统隔离名不副实；safe_builtins 可被 __subclasses__ 逃逸 |
| 1.7 | 真bug | self_evolution.py:108,377-384 | _disk_backups 内存存储，崩溃后真文件无法回滚 |
| 1.8 | 建议 | self_evolution.py:377 | rollback 恢复所有备份而非指定版本，语义错误 |
| 1.9 | 占位 | self_evolution.py:495-530 | 用户投稿/专家委员会无队列无工具 |
| 1.10 | 建议 | self_evolution.py:205-260 | 评估无 baseline 对照，"改进度"维度缺失 |
| 2.1 | 空壳 | self_learning.py:217-264 | 模式不合并不更新，纯字符串拼接 |
| 2.2 | 空壳 | self_learning.py:124 | judge 注入后从未调用 |
| 2.3 | 空壳 | self_learning.py:88 | applied 永远 False，学习结果不影响行为 |
| 2.4 | 真bug | self_learning.py:200-215 | learn_batch 只是循环单条学习，无批量统计 |
| 2.5 | 建议 | self_learning.py:266-287 | get_suggestion 中文分词失效 |
| 2.6 | 建议 | self_learning.py:359-367 | 重启后丢失 answer/tool_calls |
| 3.1 | 空壳 | evolve_skill.py:254-285 | 进化结果只写 output/ 报告，不回写 skill 文件、不建 PR |
| 3.2 | 空壳 | constraints.py:55-93 | run_test_suite 定义了但 evolve_skill.py 从不调用 |
| 3.3 | 真bug | fitness.py:107-136 | GEPA 用关键词重叠 fitness，LLMJudge 从未使用 |
| 3.4 | 空壳 | skill_module.py:84-114 | 进化循环中的"agent"只是 CoT 文本生成，不跑真实 SDR |
| 3.5 | 空壳 | mbdsdr_ai/ 全局 | hermes-self-evolution 与 mbdsdr_ai 零接线 |
| 3.6 | 建议 | config.py:50-60 | hermes_agent_path 在本项目必然为 None |
| 3.7 | 建议 | skill_module.py:117-123 | reassemble 不检查 body 内 `---` 分隔线 |

---

## 8. 关键建议（优先级排序）

1. **P0**：要么补 `evolution_apply` / `evolution_confirm` 工具并让 version_store 回灌运行中 Agent 的 system_prompt，要么在 docstring/UI 里明确标注"当前为提案记录模式，不实际修改运行时行为"，避免误导。
2. **P0**：`evaluate()` 无测试用例时应默认 reject 而非 accept（self_evolution.py:248）。
3. **P1**：`_disk_backups` 持久化到磁盘（或直接走 version_store + 真实路径映射表），否则崩溃即变砖。
4. **P1**：self_learning 需要真正的聚合逻辑（按工具序列相似度合并 pattern、更新 confidence），以及在 Agent 循环前自动注入 suggestion 的钩子，否则就是日志系统。
5. **P1**：hermes-self-evolution 如果要作为 MBDSDR 的进化框架，必须替换 fitness 为真实 SDR 指标（解调正确率、误码率等），并实现 deploy/PR 环节；否则应从主仓库移到 `reference/` 目录并标注"未接入"。
6. **P2**：沙箱要么用真正的隔离机制（Docker/bubblewrap/seccomp），要么在 docstring 里降级承诺为"尽力而为"。
