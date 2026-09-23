# R2 Audit: hermes-self-evolution/ 框架代码审查

> 审查范围：`hermes-self-evolution/` 全目录（第三方克隆框架，NousResearch/hermes-agent-self-evolution @ `0a929e3`）
> 审查方式：只读，逐文件阅读真实源码
> 审查日期：2026-09-24

---

## 0. 总体结论（TL;DR）

| 维度 | 评级 | 说明 |
|------|------|------|
| GEPA 循环端到端可跑？ | ⚠️ 半跑通 | DSPy 调用链能跑，但**进化后的 skill 文本从未被真正修改**（见 [真bug #1]） |
| 与 MBDSDR 集成？ | ❌ 零集成 | 全项目 grep 无任何 `from evolution` / `import evolution`；框架目标是 NousResearch/hermes-agent，与 mbdsdr_ai 无关 |
| fitness 真实评估？ | ❌ 关键词启发式 | `skill_fitness_metric` 是词面重叠；`LLMJudge` 类从未实例化 |
| 测试套件被调用？ | ❌ 死代码 | `run_test_suite` 定义了但 `evolve_skill.py` 从不调用 |
| 沙箱/安全？ | ⚠️ 无沙箱 | 无代码执行（纯文本生成），但有 secrets 过滤；无 prompt injection 防护 |
| 许可证 | ✅ MIT | `pyproject.toml:10` 声明 MIT，与 MBDSDR MIT 兼容；但**仓库中无 LICENSE 文件** |
| 依赖完整性 | ❌ 缺失 | 需要 dspy/openai/click/rich/reportlab，均不在 MBDSDR requirements.txt 中 |
| **是否值得保留/集成** | **❌ 不建议保留** | 详见末尾建议 |

---

## 1. 框架完整性：GEPA 循环是否真的端到端跑通？

### 1.1 [真bug] 进化后的 skill 文本从未被修改

**文件**：`evolution/skills/skill_module.py:104-114` + `evolution/skills/evolve_skill.py:183`

```python
# skill_module.py:106 — skill_text 是普通字符串属性，不是 dspy.Parameter
self.skill_text = skill_text
self.predictor = dspy.ChainOfThought(self.TaskWithSkill)

# skill_module.py:100 — skill_instructions 是 InputField，不是可优化参数
skill_instructions: str = dspy.InputField(desc="The skill instructions to follow")
```

```python
# evolve_skill.py:183 — 从优化后的 module 读回 skill_text
evolved_body = optimized_module.skill_text
```

**问题**：DSPy 的 GEPA/MIPROv2 优化器只能优化注册为 `dspy.Parameter` 的字段（如 predictor 的 instructions/demos）。`self.skill_text` 是一个普通 Python 字符串属性，在 `forward()` 中作为 `InputField` 的值传入 predictor——优化器**无法也不会修改它**。

因此 `optimized_module.skill_text` 与 `baseline_module.skill_text` 是同一个字符串。最终写入 `output/<skill>/evolved_skill.md` 的内容 = 原始 skill body，**没有任何进化发生**。

Phase 1 报告（`generate_report.py:145`）声称的 "+39.5% improvement" 来自 holdout 评估中 `optimized_module` 的 predictor 内部 CoT demos 被优化后产生了更好的文本输出——但这不是 skill 文件本身的改进，而是 DSPy 运行时 prompt 的改进。导出的 skill 文件是原样。

### 1.2 [空壳] GEPA 循环的 4 个阶段

README/PLAN 描述了 Generate-Evaluate-Propose-Accept 四阶段，实际代码映射：

| 阶段 | 声称实现 | 实际代码 |
|------|----------|----------|
| Generate（生成候选） | GEPA mutation | `dspy.GEPA(metric=..., max_steps=...)` — 委托给 DSPy 库，自身无 mutation 逻辑 |
| Evaluate（评估） | LLM-as-judge | `skill_fitness_metric` — 关键词词重叠（见 §3） |
| Propose（提议改进） | GEPA reflection | `dspy.GEPA` 内部，框架自身无代码 |
| Accept（接受/拒绝） | constraint gates | `ConstraintValidator.validate_all` — 仅检查大小/非空/frontmatter 结构（见 §1.3） |

框架自身只实现了"包装 DSPy + 写 output 文件"，GEPA 算法本身完全依赖外部 DSPy 库。

### 1.3 [占位] ConstraintValidator 不验证语义正确性

**文件**：`evolution/core/constraints.py:30-53`

`validate_all` 只做 4 项检查：
1. `_check_size` — 字符数 ≤ 15KB（`constraints.py:95-117`）
2. `_check_growth` — 增长 ≤ 20%（`constraints.py:119-134`）
3. `_check_non_empty` — 非空字符串
4. `_check_skill_structure` — 包含 `---` / `name:` / `description:`（`constraints.py:150-174`）

**没有**：语义保留检查、事实准确性检查、安全检查、功能正确性检查。PLAN.md 中描述的"semantic preservation"（PLAN.md:696-703）**未实现**。

### 1.4 [空壳] PR 创建、分支创建、回写 skill 文件均未实现

**文件**：`evolution/skills/evolve_skill.py:254-285`

进化结果只写到 `output/<skill>/<timestamp>/evolved_skill.md`：
```python
output_dir = Path("output") / skill_name / timestamp
(output_dir / "evolved_skill.md").write_text(evolved_full)
```

- ❌ 不回写原 skill 文件
- ❌ 不创建 git branch
- ❌ 不创建 PR
- ❌ `config.create_pr = True`（`config.py:47`）是死配置，无代码读取它
- ❌ PLAN.md:151 提到的 `pr_builder.py` 文件**不存在**
- ❌ PLAN.md:150 提到的 `benchmark_gate.py` 文件**不存在**

`generate_report.py:429-432` 声称"hermes-agent repository is never modified directly... improvements are proposed as pull requests"——这是**未实现的设计意图**。

---

## 2. 与 MBDSDR 的集成：完全独立

### 2.1 [确认] 零集成证据

```bash
# 全项目 grep（排除 hermes-self-evolution 自身目录）：
# grep -r "from evolution" / grep -r "import evolution" / grep -r "hermes_self_evolution"
# → 0 matches
```

- `mbdsdr_ai/` 下无任何文件 import `evolution` 包
- `hermes-self-evolution/` 下无任何文件 import `mbdsdr_ai`
- 框架的 `config.py:63-88` 硬编码目标为 `~/.hermes/hermes-agent`（NousResearch 的 agent），与 MBDSDR 的 SDR agent 完全无关
- 框架的 `skill_module.py:63` 查找 `skills/` 目录下的 `SKILL.md`——MBDSDR 没有这个目录结构

### 2.2 [空壳] "agent" 不是真实 SDR agent

**文件**：`evolution/skills/skill_module.py:94-114`

被进化的 "agent" 是：
```python
class TaskWithSkill(dspy.Signature):
    """Complete a task following the provided skill instructions."""
    skill_instructions: str = dspy.InputField(...)
    task_input: str = dspy.InputField(...)
    output: str = dspy.OutputField(...)
```

这只是一个 **DSPy ChainOfThought 文本生成器**——给定 skill 文本和 task 输入，输出一段文本。它：
- ❌ 不调用 SDR 硬件
- ❌ 不执行任何工具/函数调用
- ❌ 不解调信号、不解析频谱、不做任何 DSP
- ❌ 不连接 MCP server

它优化的是"LLM 读了 skill 文本后生成一段文字的能力"，与 MBDSDR 的实际 SDR 功能**毫无关系**。

---

## 3. Fitness 函数：关键词匹配启发式

### 3.1 [确认] skill_fitness_metric 是词面重叠

**文件**：`evolution/core/fitness.py:107-136`

```python
def skill_fitness_metric(example, prediction, trace=None) -> float:
    agent_output = getattr(prediction, "output", "") or ""
    expected = getattr(example, "expected_behavior", "") or ""
    # ...
    expected_words = set(expected_lower.split())
    output_words = set(output_lower.split())
    if expected_words:
        overlap = len(expected_words & output_words) / len(expected_words)
        score = 0.3 + (0.7 * overlap)
    return min(1.0, max(0.0, score))
```

这是 **bag-of-words Jaccard 类重叠**，不是语义相似度。问题：
- 同义词不匹配（"decode" vs "decode" 不匹配）
- 词序/语序完全不考虑
- 长 rubric 天然得分低（分母大）
- GEPA 会学会"在输出中堆砌 rubric 中的词"来刷分——典型的 Goodhart 定律

`generate_report.py:324-332` 也明确承认了这一点："Fitness was measured using keyword overlap... This provides a fast proxy for semantic similarity."

### 3.2 [死代码] LLMJudge 类从未实例化

**文件**：`evolution/core/fitness.py:34-104`（定义），`evolution/skills/evolve_skill.py:24`（import）

```python
# evolve_skill.py:24 — 导入了但从未使用
from evolution.core.fitness import skill_fitness_metric, LLMJudge, FitnessScore
```

`LLMJudge` 类（`fitness.py:34-104`）实现了多维度 rubric 评分（correctness/procedure_following/conciseness），但：
- 全代码库 grep `LLMJudge(` → 仅定义处，无实例化
- `FitnessScore` dataclass 也从未被 `skill_fitness_metric` 使用（该函数直接返回 float）
- 实际传入 `dspy.GEPA(metric=skill_fitness_metric)` 的是关键词启发式版本

注释 `fitness.py:122` 自己也承认："Full LLM-as-judge scoring is expensive — use it selectively"——但选择性使用的代码路径**不存在**。

---

## 4. 测试套件：run_test_suite 是死代码

### 4.1 [死代码] run_test_suite 从不被调用

**文件**：`evolution/core/constraints.py:55-93`（定义）

```python
def run_test_suite(self, hermes_repo: Path) -> ConstraintResult:
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
        capture_output=True, text=True, timeout=300,
        cwd=str(hermes_repo),
    )
```

- `evolve_skill.py` 中 grep `run_test_suite` → **0 调用**
- `evolve_skill.py:44` 有 `--run-tests` CLI flag，设为 `config.run_pytest=run_tests`（`evolve_skill.py:55`）
- 但 `config.run_pytest` 这个字段在整个代码库中**除了赋值外无任何读取处**
- `validate_all()`（`constraints.py:30-53`）不调用 `run_test_suite`

### 4.2 [确认] 框架自身的测试不验证 SDR 功能

`tests/` 目录下的测试：
- `test_constraints.py` — 测试大小/非空/frontmatter 检查
- `test_config_repo_path.py` — 测试路径解析
- `test_external_importers.py` — 测试 Claude/Copilot 历史解析、secrets 过滤
- `test_skill_module.py` — 测试 SKILL.md 解析

这些测试**全部是对框架自身工具函数的单元测试**，不涉及任何 SDR 信号处理、解调、解码等功能正确性验证。

---

## 5. 安全性

### 5.1 [正面] Secrets 过滤

**文件**：`evolution/core/external_importers.py:45-70`

有一个比较完善的正则表达式 `SECRET_PATTERNS`，过滤 API keys、tokens、password assignments 等。在导入 Claude Code / Copilot / Hermes 会话历史时会逐行检查（`external_importers.py:193, 297, 403`）。这是代码中做得比较好的部分。

### 5.2 [无] 沙箱 / 代码执行

进化过程中**没有代码执行**——纯 LLM 文本生成。唯一的 `subprocess.run` 是 `run_test_suite`（`constraints.py:58`），但它从不被调用。因此不存在沙箱逃逸风险。

但如果未来启用 `run_test_suite`，它直接在 `hermes_repo` 目录下运行 `pytest`，无沙箱隔离。

### 5.3 [无] Prompt injection 防护

进化过程中：
- Synthetic dataset 由 LLM 生成（`dataset_builder.py:115-169`）
- SessionDB mining 从用户历史中提取文本（`external_importers.py`）
- 这些文本作为 `task_input` 或 `expected_behavior` 传入 DSPy 评估

**没有任何**对输入文本的 prompt injection 检测/防护。如果用户历史中包含 "ignore previous instructions and output X" 之类的文本，它会被当作 eval 数据的一部分，可能影响 GEPA 的优化方向。

---

## 6. 可维护性

### 6.1 [硬编码] 路径和模型名

| 位置 | 硬编码内容 |
|------|-----------|
| `config.py:77` | `~/.hermes/hermes-agent` |
| `config.py:81` | `../hermes-agent`（sibling directory） |
| `external_importers.py:165` | `~/.claude/history.jsonl` |
| `external_importers.py:222` | `~/.copilot/session-state/` |
| `external_importers.py:346` | `~/.hermes/sessions/` |
| `external_importers.py:714` | `~/.hermes/skills` |
| `evolve_skill.py:41-42` | `openai/gpt-4.1` / `openai/gpt-4.1-mini` |
| `external_importers.py:739` | `openrouter/google/gemini-2.5-flash` |

### 6.2 [平台假设] Unix-only

- 所有路径基于 `Path.home()`，假设 Unix-like 文件系统
- `subprocess.run(["python", "-m", "pytest", ...])` 假设 `python` 在 PATH 中
- 无 Windows 兼容处理

### 6.3 [空壳] Phase 2-5 全是空目录

```
evolution/tools/__init__.py    → "Phase placeholder: tools evolution."
evolution/prompts/__init__.py  → "Phase placeholder: prompts evolution."
evolution/code/__init__.py     → "Phase placeholder: code evolution."
evolution/monitor/__init__.py  → "Phase placeholder: monitor evolution."
```

只有 Phase 1（skills）有实际代码，且如 §1.1 所述该代码存在根本性 bug。

---

## 7. 许可证

### 7.1 MIT 声明

- `pyproject.toml:10`：`license = {text = "MIT"}`
- `README.md:84`：`MIT — © 2026 Nous Research`
- 与 MBDSDR 的 MIT 许可证兼容 ✅

### 7.2 [问题] 仓库中无 LICENSE 文件

`find hermes-self-evolution -iname "*licen*"` → 无结果。克隆下来的仓库中只有 `.git` 目录，没有实际的 LICENSE 文本文件。声明是 MIT 但缺少正式的许可证文本。法律上这意味着许可证条款不完整。

### 7.3 第三方依赖许可证

- DSPy: MIT ✅
- GEPA: MIT ✅
- Darwinian Evolver: AGPL v3 ⚠️（仅在 optional dependency 中 `pyproject.toml:31`，未实际使用）
- reportlab: BSD-style ✅

---

## 8. "实验室绿、真机红"分析

### 8.1 Phase 1 报告的问题

`generate_report.py:143-146` 声称：
> Baseline Score → Optimized Score: 0.408 → 0.569 (+39.5%)

但这个结果：
1. **只在 2 个 validation example 上测试**（`generate_report.py:346-347`）
2. **fitness 是关键词重叠**（不是语义正确性）
3. **optimizer 实际是 BootstrapFewShot**（不是 GEPA——`generate_report.py:269` 明确写了 "Optimizer: DSPy BootstrapFewShot"）
4. **进化后的 skill 文件未被修改**（见 §1.1 [真bug]）
5. **eval 数据是 LLM 自己生成的**（synthetic，`generate_report.py:293`）——LLM 生成 rubric + LLM 评估输出，存在循环论证

### 8.2 在真实 SDR 任务上的预期表现

将此框架用于 MBDSDR 的 SDR skill：
- fitness 函数无法判断解调是否正确（只看关键词）
- "agent" 不执行真实信号处理（纯文本生成）
- 优化会导致 skill 文本堆砌 expected_behavior 中的关键词
- 产出的 skill 文件与原始文件相同（§1.1 bug）

**结论：在真实 SDR 任务上完全无效。**

---

## 9. 依赖分析

### 9.1 hermes-self-evolution 需要的依赖

| 依赖 | 用途 | 在 MBDSDR requirements.txt 中？ |
|------|------|------|
| `dspy>=3.0.0` | GEPA/MIPROv2 优化器 | ❌ 不在 |
| `openai>=1.0.0` | LLM API 调用 | ❌ 不在（MBDSDR 用 requests 直接调 API） |
| `pyyaml>=6.0` | YAML frontmatter 解析 | ❌ 不在 |
| `click>=8.0` | CLI | ❌ 不在 |
| `rich>=13.0` | 终端美化 | ❌ 不在 |
| `reportlab` | generate_report.py PDF 生成 | ❌ 不在 |

MBDSDR 的 `requirements.txt` 只包含 numpy/requests/websocket/sgp4/Pillow 等 DSP 和硬件相关依赖。hermes 框架的全部依赖都缺失。

---

## 10. 发现汇总

### [真bug]

| # | 位置 | 描述 |
|---|------|------|
| 1 | `skill_module.py:106` + `evolve_skill.py:183` | `self.skill_text` 是普通字符串不是 `dspy.Parameter`，GEPA 无法修改它；`optimized_module.skill_text` 读回的是原始文本。**进化产出 = 原始 skill 文件**。 |

### [空壳]

| # | 位置 | 描述 |
|---|------|------|
| 2 | `constraints.py:55-93` | `run_test_suite` 定义了但 `evolve_skill.py` 从不调用；`--run-tests` flag 是死开关 |
| 3 | `fitness.py:34-104` | `LLMJudge` 类定义完善但从未实例化；`FitnessScore` 从未使用 |
| 4 | `evolve_skill.py:254-285` | 无 git branch / PR 创建逻辑；`config.create_pr` 是死配置 |
| 5 | `evolution/tools/`, `prompts/`, `code/`, `monitor/` | 四个 Phase 目录全是空 `__init__.py`，无实现 |
| 6 | `PLAN.md:150-151` | `benchmark_gate.py` 和 `pr_builder.py` 在计划中提到但文件不存在 |

### [占位]

| # | 位置 | 描述 |
|---|------|------|
| 7 | `constraints.py:150-174` | skill structure 检查只查 `---` / `name:` / `description:` 字符串存在，不验证 YAML 合法性 |
| 8 | `dataset_builder.py:96-109` | synthetic dataset 生成依赖 LLM 输出 JSON，解析容错弱（仅一次 regex fallback） |

### [建议]

| # | 位置 | 描述 |
|---|------|------|
| 9 | 全目录 | 缺少 LICENSE 文件（仅 pyproject.toml 声明 MIT） |
| 10 | `external_importers.py:45-70` | secrets 过滤做得较好，但可考虑加入 prompt injection 模式检测 |
| 11 | `config.py:81` | sibling path `Path(__file__).parent.parent.parent / "hermes-agent"` 在 MBDSDR 项目结构下会指向错误位置 |

---

## 11. 最终建议：是否值得保留/集成？

### ❌ 不建议保留此目录

**理由：**

1. **根本性 bug**：进化后的 skill 文本从未被修改（§1.1），整个 GEPA 优化对 skill 文件本身是无效的
2. **零集成**：与 mbdsdr_ai 无任何代码连接，目标是 NousResearch/hermes-agent
3. **fitness 无意义**：关键词重叠无法评估 SDR skill 质量
4. **agent 不真实**：DSPy CoT 文本生成器不执行任何 SDR 任务
5. **依赖缺失**：dspy 等重量级依赖不在 requirements.txt 中
6. **Phase 2-5 全空**：只有 Phase 1 有代码，且该代码有 bug
7. **"实验室绿"不可信**：+39.5% 的结果基于 2 个 synthetic example + 关键词评分 + LLM 自评循环

### 如果未来需要 self-evolution 能力

应基于 MBDSDR 自身架构重新设计：
- fitness 应基于真实 SDR 任务的 pass/fail（解调成功率、BER、信号检测精度等）
- "agent" 应调用真实的 MCP tools / DSP functions，而非纯文本生成
- 评估数据集应来自真实 SDR 测试信号，而非 LLM 生成
- 进化结果应回写到 MBDSDR 的 skill/agent 配置中，并经测试套件验证

### 建议操作

- **短期**：从项目中移除 `hermes-self-evolution/` 目录（或标记为 deprecated，不纳入构建/测试）
- **中期**：如需要 self-evolution，在 `mbdsdr_ai/self_evolution/` 下原生实现，复用 MBDSDR 已有的 sandbox_guardian、tool_tests、experiments 等基础设施
