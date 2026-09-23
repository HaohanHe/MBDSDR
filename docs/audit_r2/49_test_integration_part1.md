# 第二轮深度代码审查 — test_full_integration.py 第1-700行

**审查范围**: `tests/test_full_integration.py` 第1–700行（共1421行，本轮审查前半部分）
**审查日期**: 2026-09-24
**审查人**: 子Agent #49
**项目**: MBDSDR AI 内核

---

## 一、总体评价

该文件是一个**自写的简易测试运行器**（非 pytest），用 `TestResult` 类手动收集通过/失败数。前700行覆盖了17个测试函数，声称验证了30个模块和128个工具。

**核心结论**: 测试数量看起来很多（228项），但大量断言是"能跑通就过"的弱断言，**真正验证数值正确性的测试不超过10%**。最严重的问题是：
1. 子代理和编排器的核心行为（execute/execute_task）完全未被测试——只测CRUD
2. 存在硬编码 `True` 的占位断言和恒真式（tautology）
3. DSP/解码器测试只检查"返回了dict/非空"，不验证计算结果是否正确

---

## 二、严重发现（[空壳] / [真bug]）

### 2.1 [空壳] 子代理系统：只测CRUD，从不调用 execute_task

**位置**: `tests/test_full_integration.py:405-434`

测试只做了：create → list → get → stats → destroy。**从未调用 `SubagentManager.execute_task()`**（定义于 `mbdsdr_ai/subagents.py:246`）。

这意味着：
- 子代理的 `_execute()` 方法（`subagents.py:300`）——真正根据任务类型调用工具的逻辑——完全没有被覆盖
- SpectrumAnalyzer/SignalDecoder/SatelliteTracker 等7种内置子代理的行为路径零覆盖
- 任务失败处理、异常捕获、回调执行（`subagents.py:276-296`）全部未测试

**这正是审查重点第5条指出的"只测CRUD不测行为"问题，在此确认存在。**

### 2.2 [空壳] 编排器：只测 add_task/plan，从不调用 execute

**位置**: `tests/test_full_integration.py:657-684`

测试添加了3个带依赖的任务，调用了 `plan()` 验证拓扑排序，然后就结束了。**从未调用 `Orchestrator.execute()`**（定义于 `mbdsdr_ai/orchestrator.py:229`）。

未覆盖的关键行为：
- 依赖检查逻辑（`orchestrator.py:256-268`）：依赖失败时任务是否被正确跳过
- 任务重试机制（`orchestrator.py:300` 循环 `max_retries`）
- handler vs tool_name 两种执行路径（`orchestrator.py:305-308`）
- `stop_on_failure` 参数行为
- 错误恢复和降级逻辑

`create_sdr_pipeline`（line 676）只检查 `len(pipeline) >= 5`，不验证流水线内容是否合理。

### 2.3 [占位] Hook 触发事件：硬编码 True

**位置**: `tests/test_full_integration.py:395`

```python
hm.trigger(event)
result.record("触发事件", True)   # ← 硬编码 True，永远通过
```

无论 `hm.trigger(event)` 是否真的调用了hook回调，这行都记录通过。虽然下一行（line 399）通过 `total_triggers >= 1` 间接验证了触发计数，但 line 395 本身是一个无意义的占位断言，给人"测试了触发"的假象。

### 2.4 [占位] 自学习建议：恒真式断言

**位置**: `tests/test_full_integration.py:647`

```python
suggestion = sl.get_suggestion("调谐到 FM 98.5")
result.record("获取学习建议", suggestion is None or suggestion is not None)
```

`suggestion is None or suggestion is not None` 是**逻辑恒真式**，对任何Python对象都成立。这等价于 `result.record("获取学习建议", True)`。

查看源码（`self_learning.py:272-287`），`get_suggestion` 在 patterns 为空时返回 None，有匹配时返回 LearnedPattern。测试应该验证：
- 刚 record 一条经验后 get_suggestion 是否返回非None
- 返回的 suggestion 是否与问题相关

当前写法完全跳过了这两个验证。

### 2.5 [占位] 销毁子代理：硬编码 True

**位置**: `tests/test_full_integration.py:431`

```python
sm.destroy(sub_id)
result.record("销毁子代理", True)
```

不验证 destroy 后 `list_subagents()` 是否真的少了一个，也不验证 `get(sub_id)` 是否返回 None/异常。destroy 可能什么都没做，测试仍然通过。

---

## 三、弱断言/凑数测试清单

以下测试**只验证"函数能调用且返回了某种类型"，不验证输出正确性**：

| # | 行号 | 测试名 | 断言内容 | 问题 |
|---|------|--------|----------|------|
| 1 | 212 | DCBlocker | `len(out) == len(fm_signal)` | 只检查长度，不检查DC分量是否真的被去除 |
| 2 | 221 | IQCalibrator | `out is not None` | 不检查I/Q失衡是否被校正 |
| 3 | 229 | AGC | `len(out) == len(fm_signal)` | 不检查增益是否真的归一化 |
| 4 | 237 | FM/AM/CW 解调 | `len(audio) > 0` | 只检查非空。注入的是已知FM信号，应验证解调音频中包含调制频率分量 |
| 5 | 244 | SSB USB 解调 | `len(audio) > 0` | 同上 |
| 6 | 263 | compute_snr | `isinstance(snr, dict) and "snr_db" in snr` | 注入的是无噪复指数信号（SNR→∞），应验证 snr_db 为较大正值（如>20dB），当前不验证数值 |
| 7 | 270 | estimate_bandwidth | `isinstance(bw, dict) and "bandwidth_hz" in bw` | 注入的是单频信号（理论带宽≈0），应验证带宽估计值合理 |
| 8 | 277 | front_end | `out is not None and len(out) == len(fm_signal)` | 只检查长度 |
| 9 | 297 | compute_spectrum | `spectrum is not None` | 不验证频谱峰值位置是否在注入频率处 |
| 10 | 301 | find_signals | `isinstance(signals, list)` | 注入了明确信号，应检测到至少1个信号且频率位置正确 |
| 11 | 305-306 | estimate_center_offset | `isinstance(center, dict) and "estimated_center_freq" in center` | 注入信号频率为0.1*fs，应验证估计值接近该频率 |
| 12 | 310 | extract_modulation_features | `isinstance(features, dict) and len(features) >= 5` | 只检查字典大小≥5，不检查特征值 |
| 13 | 334 | list_visible_satellites | `isinstance(sats, list)` | 不验证卫星数量、坐标、过境时间是否合理 |
| 14 | 345 | compute_doppler_correction | `isinstance(doppler, dict) or isinstance(doppler, float)` | 接受任何类型，不验证多普勒频偏数值是否在合理范围（kHz级） |
| 15 | 362 | detect_fhss | `isinstance(out, dict)` | **注入了10个跳频**（line 357-360），应验证 `num_hops_detected ≈ 10` 且跳频序列匹配。当前只检查返回了dict |
| 16 | 374 | decode_digital_mode (FT8) | `out is not None` | 输入是**1秒静音**，FT8一帧12.6秒，这个输入永远不可能解码出有意义结果。测试只确认函数不崩溃 |
| 17 | 415 | 创建子代理 | `sub_id is not None` | 弱，但可接受 |
| 18 | 423 | 获取子代理 | `sub is not None` | 不验证sub的属性是否正确 |
| 19 | 458 | ComplementaryFilter | `cf.roll is not None` | 注入了纯陀螺仪X轴旋转，应验证 roll 角随时间增长 |
| 20 | 463 | TiltCompensatedCompass | `isinstance(heading, float)` | 不验证航向角数值是否正确 |
| 21 | 470 | MadgwickFilter | `isinstance(euler, tuple) and len(euler) == 3` | 不验证欧拉角数值 |
| 22 | 478 | PoseFusion | `pose is not None` | 不验证位姿内容 |
| 23 | 484 | ARProjector | `marker is not None` | 不验证投影坐标是否在屏幕范围内 |
| 24 | 520 | 回滚快照 | `success`（bool） | 不验证回滚后文件内容是否真的被恢复。写入了`print('hello')`，回滚后应读回比对 |
| 25 | 605 | LLMJudge规则评分 | `result_data.overall_score > 0` | 不验证评分是否接近预期（应接近满分，因为答案完全正确） |
| 26 | 643 | learn_batch | `patterns is not None` | 不验证是否真的学到了模式 |

---

## 四、测试覆盖盲区

### 4.1 完全没有行为测试的模块（仅导入或仅存在性检查）

以下模块在 `test_module_imports`（line 77-94）中仅验证能import，在 `test_agent_init`（line 97-129）中仅验证 `hasattr(agent, sub)`，**没有任何行为测试**：

| 模块 | 位置 | 说明 |
|------|------|------|
| `config` | line 81 | 只import，不测试配置加载/校验 |
| `context_manager` | line 81, 103 | 只检查存在，不测试上下文压缩/窗口管理 |
| `model_manager` | line 81, 103 | 只检查存在，不测试模型加载/切换/降级 |
| `memory` | line 81, 103 | 只检查存在，不测试记忆存储/检索/遗忘 |
| `version_store` | line 82 | 只import，不测试版本历史/回滚 |
| `sandbox` | line 82 | 只import，不测试代码沙箱执行安全 |
| `self_evolution` | line 82, 104 | 只检查存在（`evolution`属性），不测试自进化逻辑 |
| `workflow_recorder` | line 85, 106 | 只检查存在，不测试工作流录制/回放 |
| `file_tracker` | line 85, 107 | 只检查存在，不测试文件变更追踪 |
| `plugin_system` | line 86, 107 | 只检查存在，不测试插件加载/隔离 |
| `astronomy` | line 87 | 只import，完全无行为测试 |
| `amr` | line 87, 108 | 只检查存在（`amr_classifier`），不测试调制识别 |

### 4.2 测试了行为但只测了"不崩溃"的模块

| 模块 | 测试函数 | 问题 |
|------|----------|------|
| SDR Tools (line 151-191) | test_sdr_tools | 所有SDR命令只检查 `r.success`。set_frequency(98.5MHz)后调用get_frequency但**不对比数值**——如果set和get犯同样错误（如都返回默认值100MHz），测试仍然通过 |
| Workflow Engine (line 530-551) | test_workflow_module | 只list_workflows检查数量≥3和特定名称存在，**从不执行任何工作流** |
| Scheduler (line 554-587) | test_scheduler_module | 只测add/list/disable/enable/remove的CRUD。任务设为`time.time()+3600`（1小时后），**从不触发执行**。调度触发逻辑、错过任务处理、重试策略全部未测 |

---

## 五、"实验室绿、真机红"风险分析

### 5.1 SDR工具测试全部基于 MockSDRBackend

**位置**: `tests/test_full_integration.py:151-191`，配合 `mbdsdr_ai/sdr_backend.py:324`

测试通过 `agent.tool_registry.call("sdr_connect", {})` 连接SDR。根据源码（`sdr_backend.py:1094-1129`），SDRBackendManager默认实例化 MockSDRBackend。这意味着：

- `sdr_set_frequency` / `sdr_get_frequency` 在mock后端上是**直接读写一个float变量**，没有硬件寄存器交互
- `sdr_spectrum_analyze` 在mock上可能返回合成数据，不测试真实FFT流水线
- `sdr_demodulate`（line 186-191）测试了5种模式，但mock后端的解调实现可能是占位返回
- **真机上**：频率精度、采样率兼容性、增益范围限制、信号缺失时的静噪行为、解调失真——全部未覆盖

**建议**: 至少在mock后端上验证set/get往返一致性（set 98.5MHz后get应返回98.5MHz ± 容差）。

### 5.2 DSP测试使用理想无噪信号

**位置**: `tests/test_full_integration.py:205-206`

```python
t = np.linspace(0, 1, 10000, endpoint=False)
fm_signal = np.exp(1j * 2 * np.pi * (0.05 * t + 0.01 * np.sin(...)))
```

这是一个**纯解析信号**（无噪声、无量化误差、无直流偏移、无I/Q失衡）。真实SDR信号有：
- 直流偏移（DC offset）——DCBlocker在纯信号上测试意义有限
- I/Q增益/相位失衡——IQCalibrator在校准目标上自校准再应用于自身，是"用钥匙锁自己再用钥匙开"
- 噪声——AGC在无噪信号上的行为与真实信号不同
- 有限字长效应

### 5.3 FT8解码测试使用静音输入

**位置**: `tests/test_full_integration.py:368-376`

生成1秒全零WAV文件送入FT8解码器。FT8标准帧长12.6秒、15Hz频偏。这个输入：
- 长度不对（1秒 vs 12.6秒）
- 内容是静音
- 测试只验证 `out is not None`，即"函数不抛异常"

这既不是正向测试（编码→解码验证），也不是负向测试（验证静音时返回空结果）。它是一个"烟雾测试"，被包装成了功能测试。

---

## 六、测试隔离与状态泄漏

### 6.1 [建议] 子代理管理器使用 agent.tool_registry

**位置**: `tests/test_full_integration.py:411`

```python
sm = SubagentManager(agent.tool_registry)
```

SubagentManager 持有主agent的tool_registry引用。如果测试间有状态泄漏，subagent的工具调用可能影响全局tool_registry状态。不过由于测试只做了create/destroy，未实际执行工具调用，当前无实际泄漏风险。

### 6.2 [建议] Orchestrator 也持有 agent.tool_registry

**位置**: `tests/test_full_integration.py:663`

```python
oc = Orchestrator(tool_registry=agent.tool_registry)
```

同上，由于未调用execute，当前安全。但一旦补充execute测试，需注意tool_registry中的工具是否有全局状态。

### 6.3 正面：使用了 TemporaryDirectory

Guardian（line 497）、Scheduler（line 561）、SelfLearning（line 627）、CodeEditor（line 694）都正确使用了 `tempfile.TemporaryDirectory()`，测试间文件隔离良好。

### 6.4 [建议] detect_fhss 测试使用了固定随机种子

**位置**: `tests/test_full_integration.py:356`

```python
rng = np.random.default_rng(0)
```

这是好的实践，测试可复现。但种子固定也意味着可能在某些特定随机噪声下偶然通过，换种子可能暴露问题。

---

## 七、测试速度

- **无 sleep 调用**：前700行中未发现任何 `time.sleep()` 或类似等待。
- **无大文件处理**：FT8测试只生成1秒静音WAV，FHSS测试只用了20480个样本。
- **潜在慢点**：`sdr_demodulate`（line 188）对5种模式各调用一次，duration_s=0.1，在mock后端应该很快。如果切换到真实后端会慢，但当前是mock。

---

## 八、Mock 使用评估

- **SDR后端**: 默认使用 MockSDRBackend（`sdr_backend.py:1094`）。这是合理的——CI环境没有硬件。但测试没有明确标注"这些测试在mock后端上运行，不验证真实硬件行为"，给人全覆盖的错觉。
- **LLMJudge**: `use_llm=False`（line 603），使用规则评分而非真实LLM。这是合理的（避免外部API依赖），但只测了规则路径，LLM评分路径未测。
- **无过度mock**: 与一些项目不同，这里没有用mock替换被测对象本身。DSP、SpectrumProcessor、Decoders等都是直接实例化真实类。mock仅限于硬件层。

---

## 九、汇总：弱断言/凑数测试清单

### 硬编码 True / 恒真式（必须修复）

1. **line 395**: `result.record("触发事件", True)` — 硬编码True
2. **line 431**: `result.record("销毁子代理", True)` — 硬编码True
3. **line 647**: `result.record("获取学习建议", suggestion is None or suggestion is not None)` — 恒真式

### 只测类型不测值（建议增强）

4. **line 221**: IQCalibrator — `out is not None`
5. **line 237**: FM/AM/CW解调 — `len(audio) > 0`
6. **line 244**: SSB解调 — `len(audio) > 0`
7. **line 263**: compute_snr — 只检查dict有key，不检查数值
8. **line 270**: estimate_bandwidth — 只检查dict有key，不检查数值
9. **line 297**: compute_spectrum — `spectrum is not None`
10. **line 301**: find_signals — `isinstance(signals, list)`，注入了信号但不验证检测结果
11. **line 305**: estimate_center_offset — 不验证估计频率
12. **line 334**: list_visible_satellites — `isinstance(sats, list)`
13. **line 345**: compute_doppler_correction — 接受dict或float任何类型
14. **line 362**: detect_fhss — `isinstance(out, dict)`，注入了10跳但不验证检测数
15. **line 374**: decode_digital_mode — `out is not None`，静音输入无意义
16. **line 458-484**: Pose/AR全部6项 — 只检查类型/非None，不检查数值
17. **line 520**: rollback — 只检查success bool，不验证文件内容恢复

### 只测CRUD不测行为（严重缺失）

18. **line 405-434**: SubagentManager — 从不调用 execute_task
19. **line 657-684**: Orchestrator — 从不调用 execute
20. **line 554-587**: Scheduler — 从不触发任务执行
21. **line 530-551**: WorkflowEngine — 从不执行工作流

### 完全无行为测试的模块

22. `config`, `context_manager`, `model_manager`, `memory`, `version_store`, `sandbox`, `self_evolution`, `workflow_recorder`, `file_tracker`, `plugin_system`, `astronomy`, `amr` — 共12个模块仅有import/存在性检查。
