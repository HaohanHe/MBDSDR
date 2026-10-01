# MBDSDR 第三阶段实现规格：工具化做全做实 + 真机/真链路收敛（v1，2026-10-01）

> 前置：第二阶段已推送上线（408e739，本地与远端 main 同步，未推送提交数 0）。
> 依据：docs/learn/model-tool-calling-boundaries.md、docs/learn/model-tool-calling/_IMPL_SPEC.md、docs/audit_r2/（历史审查）。
> 纪律：先学后做、真读代码、禁假数据、禁 mock 冒充真实执行、诚实标注未完成项；每批确定性测试通过后独立验证再推下一批。

## 0. 勘察结论（主控已核实，子 agent 不必重查；深度细节由 Wave1 审计补）

- git：HEAD=408e739、`git log origin/main..HEAD` 为空（已同步）、工作区仅 untracked 构建/scratch 目录。
- 工具注册点普查（全仓 grep，无遗漏）：
  - C++ 仅 `cpp/src/ai/agent_tools.cpp`（toolDefs() 消费 M1 registeredToolSpecs()）→ 7 工具：tune_frequency/set_mode/start_recording/stop_recording/scan_band/set_bandwidth/get_status，executeTool 全部真实调用 dsp::SpectrumEngine。
  - Flutter 仅 `mobile/lib/app/ai_tools.dart`（buildRadioTools）→ 5 工具：set_frequency/set_mode/set_gain/set_sample_rate/get_status，execute 全部真实走 RadioApi（manualMode 时动作 gate 返回 gated JSON）。
  - **假闭环工具（evolution_evaluate/subagent_execute/workflow_record_*/hook 等）全部在 Python 原型 `mbdsdr_ai/`**（agent.py 注册、workflow_recorder.py/subagents.py/self_evolution.py/hooks 等），docs/audit_r2 已指出其中多处静默失败/空转。
- 双渲染器：
  - 桌面 `cpp/src/ui/spectrum_widget.{h,cpp}`（容器：控件条+画布+峰值表）+ `cpp/src/ui/spectrum_display.{h,cpp}`（统一 trace+频率条+瀑布 QPainter 画布，同帧驱动、构造对齐、可拖 divider）。
  - Flutter `mobile/lib/widgets/spectrum_display.dart`（CustomPainter，Column flex:5/4、RGBA 像素缓冲瀑布、余晖 SpectrumPersistence、fixedMarksHz 标记）。
  - 已知缺陷（用户点名，待 Wave1 逐项定位）：频谱图与瀑布图对不齐、频谱占位过大、时间轴/余晖/标记在两种渲染器下不一致。
- UI 设计语言：Figma 解压在 `cpp/scratch/figma_unzip/preview/`（4 张 jpg 设计稿预览）。气质要求：Apple/小米车机（克制、4pt 栅格、低饱和蓝灰 #919cac、弹性布局、禁硬编码像素）。
- 卫星追踪：Flutter `mobile/lib/astro/`（sgp4/tle/passes/nav_satellites）；C++ `src/ai/sat_task_planner.cpp` + dsp::TleClient + task 执行链（task_runner/task_orchestrator）。LLM 工具循环目前不含卫星追踪工具（Wave1 需确认是否要新增/接入真实动作链）。
- 无硬件诚实空态：Flutter 已有 empty_state；C++ 侧未连接硬件时 executeTool/engine 行为需 Wave1 核实（禁假峰值/假执行）。

## 1. 第三阶段目标（用户五点，映射到交付）

| 用户方向 | 交付 |
|---|---|
| 1. 工具 Schema 全覆盖 + 总数审计 | 工具注册点已普查无遗漏（7+5 全 Schema 化）→ 落地「注册数 = Schema 数」确定性审计测试（C++ 单测 + Flutter 测试）；如 Wave1 发现遗漏注册点则补 Schema |
| 2. 真链路闭环 | LLM 循环 → 校验 → 真实动作（调谐/解调/录制/扫描/卫星追踪）全路径核实；无硬件诚实空态（不假装成功）；两端行为一致 |
| 3. 已知缺陷修复 | a) 渲染器对齐/占位/时间轴/余晖/标记不一致（按 Wave1 差异清单修）；b) mbdsdr_ai 假闭环工具：接真实实现或从注册表摘除，不留假工具 |
| 4. UI 收敛 | 按 Figma 设计语言收敛两端；触屏/窄窗/多窗口适配复查 |
| 5. 每批交付 | 确定性测试（cpp 83/83 + flutter 226 基线不破坏 + 新增）、git 核对后只暂存相关文件增量推送 |

## 2. 波次与文件所有权（互斥，禁越界）

### Wave 1（审计，全部只读，产出报告落盘 docs/learn/phase3/audits/）
- A1 渲染器差异审计：读两端渲染器全部代码（cpp/src/ui/spectrum_widget.{h,cpp}、spectrum_display.{h,cpp}、mobile/lib/widgets/spectrum_display.dart + 相关调用方），逐项对比：几何对齐（trace/频率条/瀑布 x 域）、占位比例、频率时间轴刻度算法、余晖（persistence）算法与档位、标记（fixed/VFO）交互与绘制，产出「差异清单 + 修复建议（含确定性测试建议）」。
- A2 mbdsdr_ai 假工具审计：审计 mbdsdr_ai 全部工具注册（agent.py/tool_registry + 各 category：hooks/subagents/self_evolution/workflow_recorder/orchestrator/pose 等），逐工具判定：真实实现 / 假闭环（空转/静默失败/编造结果）/ 仅存在性；产出「摘除 or 接真实」清单 + 连锁影响（引用点、tests/test_full_integration*.py 存在性断言）+ 建议执行顺序。
- A3 UI 设计语言审计：读 figma preview 4 张 jpg + 两端 UI 骨架（desktop src/ui/ 主要页面、mobile/lib/pages/ + widgets/），产出「设计语言收敛清单」（色彩/栅格/间距/控件/排版，含与 #919cac 低饱和蓝灰的差距）+「触屏/窄窗/多窗口适配复查清单」（每项现状→问题→建议）。
- A4 真链路闭环审计：读两端 LLM→工具→真实动作全路径（C++ llm_worker/agent_tools/dispatch→SpectrumEngine→dsp 全链；Flutter ai_client→AiTool.execute→RadioApi→dsp），逐工具确认「真实执行 or 空态 or 假执行」；卫星追踪现状（mobile/lib/astro 能力、C++ sat_task_planner/task_runner）是否可作工具接入、缺什么；无硬件时各工具诚实空态行为；产出「闭环差距清单 + 修复建议」。

### Wave 2（实现，按 Wave1 清单派发，每批自带确定性测试）
- W2a 渲染器对齐修复（desktop + Flutter 各自或同一 agent，视清单范围）
- W2b 假工具清理落地（mbdsdr_ai：摘除/接真实，跑通 mbdsdr_ai 测试）
- W2c UI 收敛 + 触屏/窄窗/多窗口适配（两端）
- W2d 真链路闭环补齐（两端，含卫星追踪工具决策）+ 工具总数审计测试（cpp + flutter）

### Wave 3（收尾）
- 独立验证（复用独立验证 agent 复核：循环/校验/保留逻辑、禁 eval、无 key 不真调、基线不破坏）
- git status/log 核对、只暂存相关文件、增量 push origin/main（禁 add -A、禁敏感文件）

## 3. 红线（全部 agent）

- 禁假数据/假峰值/假执行；无硬件一律诚实空态（error/空态 UI，绝不假装成功）；测试用 mock 只用于确定性验证，不得冒充真实执行结果进生产路径。
- 禁硬编码魔法数（像素/颜色/数值走 tokens.h / AppTokens / 设计 token）；文档与代码不得出现"比赛/competition"。
- 禁逐字复制 GPL 源码（干净室 MIT）；mbdsdr_ai 摘除工具时不得破坏其余真实功能。
- 每批交付：实现 + 确定性测试全绿 + 自检记录；审计报告必须带 file:line 出处，拿不准标「推断」，原文没有的标「原文未给」。
- 不动 git（推送由主控统一）；只写自己所有权内的文件。
