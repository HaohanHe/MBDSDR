# 参考项目学习 Playbook（phase67 · sdroxide / OnAir / DeepSeek-Harness）

> **目的**：把三个参考项目里**可迁移的工程模式**提炼成 MBDSDR 的改造依据，
> 并记录 phase67 已落地 / 未落地的映射，防止「学完就忘、下轮重学」。
> **材料**：`/workspace/reference/{sdroxide,OnAir-src,deepseek-harness}`（本地精读件；
> sdroxide = README 97KB + USER_MANUAL 988KB；OnAir = README 65KB + ARCHITECTURE/DEVICES；
> DSH = README×2 + AGENTS.md + docs/{architecture,tool-catalog,tool-execution-pipeline,
> session-format-status,agent-lifecycle,cordis-primer,glossary}.md）。
> **方法**：只提炼「与 MBDSDR 当前形态（Qt/C++ 桌面 + Flutter 移动 + Python AI 内核）
> 相关」的模式；每条给出**落地动作**与**验证方式**。

---

## 1. sdroxide（Rust/egui/wgpu SDR 应用）

| # | 模式 | 原文事实 | MBDSDR 落地（phase67） | 状态 |
|---|---|---|---|---|
| S1 | **一二进制三模态**：同一二进制=原生 GUI / 服务器+WASM 客户端 / 远程瘦客户端 | README 架构章 | 移动端 rtl_tcp 瘦客户端 + 桌面全功能 = 天然同构；不强制统一二进制 | 已有架构，保持 |
| S2 | **57 个 crate 可插拔后端**：设备/解调/解码全部 trait 化 | README | `SDRBackendManager` 已是同一思路；Python 侧后端缝保持窄接口 | 已有，保持 |
| S3 | **诚实能力标注**：「RX verified / TX unmeasured」——文档不吹未测能力 | README 多处 | **全面吸收**：所有工具描述改「不支持即如实说不」；两处虚假恢复指引（bandwidth/squelch 声称返回合法值，实际只回「操作失败」）本轮实证修正 | ✅ 本轮 |
| S4 | **F1 应用内手册**（988KB USER_MANUAL 随发行） | docs/USER_MANUAL.md | **移动端帮助页** `lib/pages/help_page.dart`：快速开始/页面导览/解调模式/FAQ/术语速查，AppBar 帮助图标可达 | ✅ 本轮 |
| S5 | **优雅降级**：缺库时置灰并说明原因，绝不假接 | README | 移动端 `_maybeRead` 可选注入 + EmptyState 诚实空态 + sounddevice 缺库降级提示；本轮补 EmptyState 单滚降级（引导横幅压缩视口不再溢出） | ✅ 本轮加固 |
| S6 | **`~/.config/sdroxide/` 持久化**：配置/书签/VFO 全部落盘 | README | SettingsService 双层 KV（普通/安全）已有；首跑引导标记 `firstRunGuideSeen` 同模式落盘 | ✅ 本轮 |
| S7 | **PROVENANCE.md 许可证卫生** | README | 仓库 SPDX 头已在做（py/dart 文件均带 MIT 头） | 已有，保持 |

## 2. OnAir（C++ 模拟电视/数字电视地面站）

| # | 模式 | 原文事实 | MBDSDR 落地（phase67） | 状态 |
|---|---|---|---|---|
| O1 | **core/ 接收库 + app/ GUI 两层分离** | ARCHITECTURE.md | C++ 侧已是 core/app 分层；Python 内核 `mbdsdr_ai` 与 mobile/desktop UI 分层同构 | 已有，保持 |
| O2 | **每模式配测试信号发生器**：测试无需真实电台 | DEVICES.md / 测试章 | mobile 测试用 FakeWS/内存 KvStore 替身即同一思想；`_MemKv/_MemSecure` 模式本轮复用 | 已有，保持 |
| O3 | **协议层 Rust 重写 + 金标向量测试**：digest 记录自被替换的 C++ | ARCHITECTURE.md | 数字解码器测试（POCSAG 汉明同步/DTMF/ZVEI）已是金标向量思路 | 已有，保持 |
| O4 | **results/ 遥测 JSON 契约**：Android 桥只读 JSON | ARCHITECTURE.md | mobile_server telemetry fix 局部更新契约已有；`recording_path` 透传即同型数据接力 | 已有，保持 |
| O5 | **首跑向导**：第一次打开引导配置 | README 使用章 | **移动端首跑引导横幅** `lib/widgets/first_run_guide.dart`：三步要点 + 去设置/知道了，消除即持久化，不阻断操作（OnAir 是全屏向导，MBDSDR 取轻量版——首启用户可能只想先看一眼频谱） | ✅ 本轮 |
| O6 | **REAL_WORLD_CHECKLIST.md**：真实环境验收清单文化 | REAL_WORLD_CHECKLIST.md | phase66/67 报告中的双解释器 + 真设备空态复验矩阵同型 | 已有，保持 |

## 3. DeepSeek-Harness（agent harness）

| # | 模式 | 原文事实 | MBDSDR 落地（phase67） | 状态 |
|---|---|---|---|---|
| D1 | **nextStep 模式**：失败/部分成功结果携带「该做什么/告诉用户什么」 | docs/tool-execution-pipeline.md | `ToolResult.next_step` 字段 + 四条框架错误路径自动填充 + agent 注入模型可见消息（`_tool_result_to_model_content`）+ 10 项行为测试 | ✅ 本轮 |
| D2 | **工具执行管线**：pre-execute 审批 → guards → execute → post-execute → 单一冻结结果 | 同上 | ToolRegistry.call 已具雏形（guards=可用性/必填校验）；「单一冻结结果」= to_dict 快照语义保持 | 部分，保持 |
| D3 | **「Model-visible means logged」**：模型可见即必须落调用日志 | docs/architecture.md | call_log 已有（200 条环形）；next_step 也随 to_dict 落日志 | 已有，保持 |
| D4 | **能力缝三方**：Service Definition / Provider / Consumer 分离 | docs/architecture.md | 移动端 `_maybeRead` 缝 + ai_tools 注入 passesService/station 即 Consumer 侧解耦 | 已有，保持 |
| D5 | **工具描述四维标准**：用途 + 前置条件 + 权限/后果 + 恢复指引 | docs/tool-catalog.md | **本轮主战场**：34 个高频工具四维增强（12 核心 + 18 二梯队 + 4 过短）+ 35 项质量门禁测试（地板/形状/状态变更型四维齐全/诚实契约钉死/棘轮覆盖率） | ✅ 本轮 |
| D6 | **工具目录映射表**（tool→需要/影响） | docs/tool-catalog.md | `tool-dependency-map.md`：依赖图 + 32 工具契约速查 + 7 条典型任务链（Goal→Mechanism） | ✅ 本轮 |

---

## 4. 下一轮候选（学有余力时的深化项）

1. **DSH 审批缝前移**：把「pre-execute 审批」做成 AI 动作前的显式确认（移动端 AI 接管模式下高危动作——radio_ptt、record_start 长录制——先问后做）。
2. **sdroxide 模态同构深化**：桌面端 headless API 与移动端 rtl_tcp 路径共用同一套工具描述（现在两边各写一份，有漂移风险）。
3. **OnAir 金标扩展**：把 C++ 侧 VNA 工具（vna_tdr_cable / vna_calibration）也纳入四维描述与门禁（本轮只做了 Python 侧 147 个内置工具）。
4. **F1 手册桌面同构**：帮助页内容与 `docs/mcp_tools.md` 单源化（现在移动帮助页是人工同步）。

## 5. 复盘：本轮「学习→落地」闭环的证据

- 框架层：`mbdsdr_ai/tool_registry.py`（next_step）、`mbdsdr_ai/agent.py`（注入）
- 契约层：`mbdsdr_ai/sdr_tools.py`、`mbdsdr_ai/multimon_decoders.py`（34 工具描述）
- 门禁层：`mbdsdr_ai/tests/test_tool_description_quality.py`（35 项）
- UI 层：`mobile/lib/pages/help_page.dart`、`mobile/lib/widgets/first_run_guide.dart`、
  `mobile/test/help_and_onboarding_test.dart`（5 项）、`empty_state.dart` 加固
- 流程层：本文档 + `tool-dependency-map.md`
- 量化：Python 1086 passed / 5 skipped（双解释器）；Flutter 389 passed / analyze 零问题
