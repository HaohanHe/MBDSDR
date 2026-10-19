# phase67 交付报告：模型体验 × UI/UX × 工作流程（参考项目模式落地轮）

> **轮次目标**（承接用户指令）：把 sdroxide / OnAir / DeepSeek-Harness 三个参考项目
> 学到的模式**落地**——优化模型体验（MCP/工具契约）、优化用户体验与 UI、优化工作流程。
> **日期**：2026-10-12　**基线**：a51b624（phase66 收尾：1041 passed / 5 skipped）
> **本轮流件**：Python **1086 passed / 5 skipped**（双解释器同数）；Flutter **389 passed**，
> analyze 零问题。

---

## 1. 模型体验轴（主战场）

### 1.1 差距量化（开工前基线，147 个内置工具）

| 维度 | 基线 | 本轮流件 | 变化 |
|---|---|---|---|
| 前置条件提示 | 6.8%（10/147） | **25.2%**（37） | ×3.7 |
| 失败恢复提示 | 10.2%（15） | **27.2%**（40） | ×2.7 |
| 副作用提示 | 4.1%（6） | **15.0%**（22） | ×3.7 |
| 能力边界提示 | 4.1%（6） | **21.1%**（31） | ×5.2 |
| 任一维度 | 17.0%（25） | **34.7%**（51） | ×2.0 |
| 描述最短 | 9 字 | **40 字** | 地板抬升 |
| 描述总字符 | — | 18,888（中位 100，最长 527） | — |

> 口径说明：以上为**语义族关键词命中率**（见门禁测试 `_PRE/_REC/_SIDE/_BOUND`），
> 是「模型可见指引密度」的粗代理；逐工具权威契约以描述原文为准。

### 1.2 框架层：DSH nextStep 模式落地

- `ToolResult.next_step` 字段 + `to_dict()` 往返（`tool_registry.py`）；
- `ToolRegistry.call()` **四条框架错误路径**自动填充恢复指引：
  tool_not_found（→ list_tools）/ tool_unavailable（→ 先连接/枚举）/
  missing_required_params（→ 补齐必填）/ exception（→ 按类型修参，同参重试≤2 次）；
- handler 返回 dict 时 `next_step` 键透传；
- agent 侧 `_tool_result_to_model_content()` 把非空 next_step 以
  `[下一步建议]` 块注入**模型可见**消息——失败不再让模型从错误码猜恢复路径。

### 1.3 契约层：34 个高频工具四维增强

- **12 个核心**（phase66 末起）：connect/disconnect/list_devices/switch/status/
  set_frequency/set_sample_rate/set_gain/set_demod/set_bandwidth/set_agc/set_squelch/
  spectrum_analyze/record_start/record_stop（含 record_start 补齐 schema 漏暴露的
  gain/decimation 两个真实参数——handler 早就读，schema 没告诉模型）；
- **18 个二梯队**：VFO 家族 4、设备设置 5、数字解码 5、radio_* 3、IQ 回放 1；
- **4 个过短**（<30 字）全部重写。

### 1.4 本轮实证修掉的**三处描述谎言**（比缺指引更伤模型体验）

| 工具 | 描述原声称 | handler 实际行为（读码实证） | 修正 |
|---|---|---|---|
| `sdr_set_bandwidth` | 「返回硬件支持的最近合法值」 | `_guard_backend_set` 失败仅返回「操作失败」 | 改为「用 list_devices 核对范围，换合法值重试」 |
| `sdr_set_squelch` | 「返回不支持提示」 | 同上，仅「操作失败」 | 同上 |
| `sdr_get_frequency` | 「未连接时返回未连接提示」 | `_get_backend(mgr).get_frequency()` → AttributeError 走框架异常路径 | 改为「返回调用错误而非虚构频率值」 |

> 教训：**描述必须对着 handler 逐行核对后再写**。门禁测试只查「有没有四维」，
> 查不了「四维是否属实」——属实性靠本轮这种读码实证，后续增强必须保持。

### 1.5 门禁层：35 项描述质量测试（防回退）

`mbdsdr_ai/tests/test_tool_description_quality.py`：

1. **地板**：全工具面无空描述、无占位符（TODO/待补充/xxx）、≥30 字；
2. **形状**：每个工具 parameters 为合法 JSON Schema（type=object + properties dict）；
3. **状态变更型 18 个**（参数化逐个）：四维**必须齐全**；
4. **查询型 12 个**（参数化逐个）：至少两维；
5. **诚实契约钉死**：sdr_list_devices「不要虚构/空态」、sdr_connect「不会伪造」、
   record_start 磁盘写盘、radio_ptt 实际发射警告、open_iq_file 采样率恢复——删改即红；
6. **棘轮覆盖率**：任一维度 ≥25% / 前置 ≥15% / 恢复 ≥18% / 副作用 ≥6% / 边界 ≥8%
   （实测 34.7/25.2/27.2/15.0/21.1，留 ~10% 波动余量；下一轮目标任一维度 ≥45% 并上调阈值）。

---

## 2. UI/UX 轴（移动端）

| 交付 | 文件 | 参考模式 | 说明 |
|---|---|---|---|
| 应用内帮助页 | `mobile/lib/pages/help_page.dart` | sdroxide F1 手册 | 五分部：快速开始/页面导览/解调模式/FAQ/术语速查；AppBar 帮助图标直达 |
| 首跑引导横幅 | `mobile/lib/widgets/first_run_guide.dart` | OnAir 首跑向导（轻量版） | 三步要点 + 去设置/知道了；不阻断操作；消除即持久化（`firstRunGuideSeen`） |
| 持久化字段 | `settings_service.dart` | sdroxide 配置落盘 | 普通 KV 层，缺失=未看过 |
| 加固：EmptyState 单滚降级 | `empty_state.dart` | sdroxide 优雅降级 | 引导横幅压缩视口时不再 RenderFlex 溢出（本轮实测暴露的 2px 溢出真修复） |
| 加固：AppBar 状态行 Flexible | `home_shell.dart` | — | 加帮助图标后 360px 窄屏 30px 溢出回归修复 |
| Widget 测试 | `test/help_and_onboarding_test.dart`（5 项） | — | 分块齐全/图标可达/引导挂出与永不再现/去设置切页 |

**设计取舍记录**：OnAir 用全屏阻断向导，MBDSDR 取**不阻断横幅**——首次打开 App 的
用户可能只想先看一眼频谱；引导信息密度低、永不重复出现，深度内容留给帮助页。

---

## 3. 工作流程轴

- `docs/learn/phase67/tool-dependency-map.md`：DSH tool-catalog 模式落地——
  ASCII 依赖图 + 32 工具契约速查表 + 7 条典型任务链（Goal→Mechanism）+ 三条铁律
  （写操作先连接 / id 与名称必须来自枚举 / 产物用 recording_path 透传接力）。
- `docs/learn/phase67/reference-projects-playbook.md`：三项目 19 条模式逐一映射
  （sdroxide S1–S7 / OnAir O1–O6 / DSH D1–D6），标注已落地/保持/部分，并列下轮候选 4 条。

---

## 4. 复验矩阵

| 套件 | py3.11 | py3.12 | 备注 |
|---|---|---|---|
| mbdsdr_ai 全量 | 1086 passed / 5 skipped | 1086 passed / 5 skipped | 基线 1041 + 本轮新增 45（框架 10 + 门禁 35） |
| mobile flutter test | 389 passed | — | 基线 384 + 新增 5 |
| mobile flutter analyze | 零问题 | — | 3.47.7 |

**已知边界（未做，列入下轮）**：

1. C++ 侧 VNA 工具（vna_tdr_cable/vna_calibration/vna_device）描述未增强（本轮只覆盖 Python 147）；
2. DSH「pre-execute 审批」未前移（高危动作先问后做）；
3. 移动帮助页与 `docs/mcp_tools.md` 未单源化（人工同步，有漂移风险）；
4. 桌面端 headless 工具描述与移动端 ai_tools 未共用同一描述源。
