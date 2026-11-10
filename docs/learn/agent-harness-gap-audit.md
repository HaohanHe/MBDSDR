# Agent Harness 对照审计（2026-11-08）

审计参照：`D:\mbdsdr\deepseek-harness-master`（DeepSeek Harness，Cordis 插件化，
文档见其 `docs/architecture.zh.md` 与 `docs/subsystems/*.zh.md`）。
被审对象：`mbdsdr_ai/agent.py`、`tool_registry.py`、`context_manager.py`、
`orchestrator.py`、`subagents.py`、`desktop/local_agent_bridge.py`。

**一句话结论：我们不是缺功能，是缺边界。** 496 个工具、循环能跑、错误信封
有 `error` + `next_step` 结构，这部分比多数自研 agent 强；但"谁能执行、何时停、
失败算什么、上下文怎么活过崩溃"这四件事，目前主要靠调用方自觉。

---

## 一、DSH 的 8 条硬机制（学到的）

| # | 机制 | 解决的失败模式 | 我们的现状 |
|---|---|---|---|
| 1 | turn enclosure + `TurnEndReason` | 停不下来；分不清"取消/失败/截断" | 半有：`chat()` 是一轮，但结局靠字符串 `last_error` |
| 2 | 仅追加会话日志 + `deriveMessages()` | 历史双写漂移；崩溃丢最后一段 | 没有：内存 history + 全量快照 |
| 3 | `agent/pre-step` 拒/纳闸 + `request-error` 重试闸 | 任何插件偷改请求；重试重复副作用 | 没有 |
| 4 | 工具批次契约（call/result 一一配对、并发上限） | 漏结果 → 下一轮请求非法 → 模型胡编 | 有配对，无并发上限（串行） |
| 5 | compaction 锁对 + `shadowedSeqs` + 工具配对边界 | 压到一半崩溃；摘要与历史不一致 | 有配对修正，无锁对/留痕；摘要失败仍裁剪 |
| 6 | approval `allowed-once` + 全量 fail-closed | "没人回答"被当同意 | **有机制，但只覆盖 6 处**（本轮补发射类） |
| 7 | sandbox 每调用策略 + `partial` 必须暴露 | 全局开关撑不住并发；静默降级变假 | 没有统一沙箱（靠工具自身 + 桌面 gate） |
| 8 | 会话句柄（单写者 + flush + 崩溃修复） | 长任务/审计全失效 | 没有：手动 `save_conversation` |

---

## 二、问题清单（带证据）

### P0 — 模型对话通道没有写入门（本轮已修发射类）

**证据**：`tool_registry.py:776` 的 `call()` 全段不出现 write/gated/confirm；
`WRITE_TOOL_NAME_PATTERNS`（`tool_registry.py:470` 起）列了几十个写/发射工具，
但全仓 `resolve_write_authorization` 只有 **6 处**调用（`agent.py:3128` 的
code_* 包装、`orbit_*_plot`、`sdr_transmit_cw/iq`、`set_ppm_correction`）。

**后果**：同一个模型，走 MCP 通道（`mbdsdr_ai/core/mcp_server.py:2059`，
PreToolUse → write 门 → token）会被拦；走 `agent.chat()` 直接
`call_from_model → call()` 不会。`radio_ptt`、`radio_send_cw`、`gimbal_move`
裸奔——**发射违规是执照问题，不是 bug 问题**。

**本轮已修**：`tool_registry.py:524` 新增 `MODEL_HARD_GATE_PATTERNS`，
`call_from_model`（`tool_registry.py:1069`）对写工具走 `tx_guard`
（allowed-once + fail-closed），未授权返回 `manual_gate_required` + `gated`
信封，handler 一次都不执行。真实验证：`radio_ptt` → `gate_not_armed`。

**仍未修**：可恢复的调谐/云台/工作流（`sdr_set_*`、`vfo_*`、`workflow_execute`）
不在本轮硬门内。P1 要做"分级确认 + 会话免问"，否则日常对话会全是弹窗。

### P1 — 工具重试靠中文关键字，不用结构化 `error`

**证据**：`agent.py:4079-4082` 的 `RETRY_BAD` / `RETRY_OK` 是中文子串表；
而 `ToolResult.error` 早就是稳定 code（`tool_not_found` /
`tool_unavailable` / `missing_required_params`…）。

**后果**：工具返回"找不到设备"会命中 `RETRY_BAD` 的"找不到"→ **该重试的不重试**；
反过来"暂不支持"含"不支持"→ 不重试（恰好对）。分类靠文案，改一句提示语就漂。

**修法**：重试只读 `result.error`：`tool_unavailable` / `device_busy` /
`timeout` 可重试；`invalid_*` / `missing_*` / `unknown_tool` 直接喂模型。

### P1 — 会话不是追加日志，崩溃丢最后一段

**证据**：`context_manager.py` 的 history 是内存 list；`agent.py:4270`
`save_conversation()` 是手动全量 JSON 快照。

**后果**：进程被杀 / 断电，最后一轮（包括已执行的写操作回执）全丢；
无法回放"模型当时到底看到了什么"，也无法 fork 会话对比两条策略。
DSH 的 `model-visible ⇒ logged` 我们完全没有。

**修法（最小）**：每轮 `turn/step/tool/result` 追加写 JSONL（`seq` 单调），
history 从日志投影；先不做格式迁移，先保证"崩溃能读到已发生的"。

### P1 — 子代理没有深度上限

**证据**：`subagents.py:685` `create()` 只有 `max_steps`，无 `maxDepth` /
父级归属；`_register_subagent_tools` 允许子代理再调 `subagent_create`。

**后果**：A 建 B、B 建 C… 每层都有步数上限，但层数无上限，成本指数爆炸；
父级取消后子代理成孤儿。

**修法**：`create(depth)` + 全局 `MAX_SUBAGENT_DEPTH=2`；子代理注册表继承
父级取消事件；父销毁时先子后父释放。

### P1 — 编排不可取消

**证据**：`orchestrator.py:230` `execute()` 是同步 `for task_id in order`，
无取消检查；`_execute_task` 只按 `max_retries` 重试。

**后果**：点了"执行工作流"后用户无法停；长任务把 UI 线程/worker 占到底。

**修法**：`execute(cancel_event=...)`，每任务前检查；`_execute_task` 里
handler 调用前再查一次。

### P1 — 大输出只截断，不落盘

**证据**：`tool_registry.py:983` 超过 `tool_output_max_chars=4000` 就
head 60% + tail 40%，`truncated=True`；但**没有落盘路径**。
同一仓库的 `core/mcp_server.py` 有 `_finalize_tool_result` spill 信封。

**后果**：解码日志/星表查询被砍半，模型知道"截断了"但拿不到全文，
只能重跑或猜；agent 通道与 MCP 通道行为不一致。

**修法**：截断前把全文写 `~/.mbdsdr/spill/<ts>_<tool>.txt`，回执里给路径 +
"用 read_file 分段读"提示（DSH 的 `SpillLocator` 最小版）。

### P2 — `rounds` 双口径

**证据**：`agent.py:4161` 的 metrics 用 `len(round_metrics)`（真实模型步数），
但 `agent.py:4191` 返回给调用方的 `rounds` 是
`len(all_tool_calls) + (1 if final_content else 0)`（工具调用数）。

**后果**：UI 若显示"轮次"，数字与真实模型请求数不符；同一字段两个含义。

**修法**：返回 `rounds=len(round_metrics)`，另加 `tool_calls_count`。

### P2 — compaction 摘要失败仍裁剪（fail-open）

**证据**：`context_manager.py:494` `compact()`，`on_compaction` 抛异常时
摘要变成 `[历史摘要生成失败: ...] 已裁剪 N 条消息` 并继续重建历史。

**后果**：模型丢失早期上下文但以为还在；用户看不到"这次压缩是失败的"。
DSH 是 compaction 锁对 + 崩溃证据 + 明确 `TurnEndReason`。

**修法**：摘要失败 → 不裁剪（返回 False）并把原因交给调用方；成功才替换，
并记 `shadowed` 条数。

### P2 — 无 pre-step 闸 / 无 approval 配对审计

**证据**：`_chat_impl` 直接组装 system prompt + tools 发请求，没有统一
"准入/改写/拒绝"的钩子；`HookManager.veto_for()` 只在 `local_agent_bridge`
用。`tx_guard` 有 `armed/allowed/refused` 审计，但**没有"问了谁、谁答的"**
配对记录。

**后果**：以后加"按会话切模型/切工具集"只能继续往 `_chat_impl` 里塞 if；
审计只能证明"放行了"，不能证明"用户确实点过"。

---

## 三、本轮已改（可复现）

- `mbdsdr_ai/tool_registry.py`：`MODEL_HARD_GATE_PATTERNS` + `_model_hard_gate`
  + `call_from_model` 硬门（复用 `tx_guard`，不新增第二套门语义）。
- `tests/test_model_write_gate.py` 7 条：
  无 token 拦截 / 有效 token 放行 / token 一次性 / `arm_manual` 无效 /
  只读放行 / 门坏 fail-closed / **可恢复调谐"还没修"如实钉住**。
- 真实验证：`radio_ptt` → `success=False, error=manual_gate_required,
  reason=gate_not_armed`；`sdr_set_frequency` 仍走原路径。
- 回归：9 文件 **148 passed**。

## 四、没修的（诚实清单）

> **2026-11-10 复核**：第 2 项里的「会话日志」**已销账**（见第六节，带实测），
> 其余四项仍在。原始结论保留不删，便于对照。

1. 调谐/云台/工作流类写工具仍可直接执行（P1，需分级确认 + 会话免问）。
2. ~~会话日志~~、spill、子代理深度、编排取消、重试词表：除会话日志外仍未做。
3. 统一沙箱没有；C++ 侧写开关 `writeEnabled_` 与 Python gate 仍是两套。
4. 本审计只读代码与文档，**没有在真机上跑过发射路径**（设备不在位）。

## 五、建议顺序

1. **P0 收尾**：给 `sdr_set_*` / `vfo_*` / `gimbal_*` 做分级（首次确认 →
   会话免问），UI 上加"本会话已授权"状态；否则门太硬会被绕过（用户会去点
   MCP 通道）。
2. **P1 会话 JSONL**：先只写不读，跑一周确认无回归，再把 history 改成投影。
3. **P1 子代理深度 + 编排取消**：两个都是"加 20 行防灾难"的改动，优先做。
4. **P1 spill**：直接抄 `core/mcp_server.py` 的 `_finalize_tool_result`，
   agent 通道与 MCP 通道共用。
5. P2 逐条：`rounds`、compaction fail-open、pre-step 闸。

---

## 六、2026-11-10 复核（销账 + 仍欠）

对照物仍是 DeepSeek Harness（`docs/architecture.zh.md`：
「模型可见即已记录」「seam 三角色」「副作用可逆」）。本次只复核、不改架构。

### 已销账：仅追加会话日志（原 P1 第 3 条）

- 实现：`mbdsdr_ai/session_log.py` —— 按 `conversation_id` 分文件，
  一行一事件（`ts` / `seq` / `conv` / `type` + 负载），只追加不改写，
  每次 emit 即 flush；任何异常吞掉，不拖 chat 主循环。
- 接入：`agent.py:_slog()` 懒创建，覆盖 8 类事件实测写入点 ——
  `turn_start`(4093) / `user_message`(4095) / `assistant_message`(4281,4291) /
  `tool_call`(4006,4325) / `tool_result`(3969,4344) / `compaction`(4154,4379) /
  `error`(4414) / `turn_end`(4418)，另有 `subagent.*`(3836)。
- 实测（2026-11-10，隔离 HOME，非读代码推断）：

  ```
  ~/.mbdsdr/sessions/verify_20261110.jsonl   5 条事件，seq 递增
    turn_start → user_message → tool_call → tool_result → turn_end
  ```
- 边界：这是「先只写不读」的阶段——模型历史仍来自 `context_manager`，
  尚未改为从事件流投影（DSH 的 `deriveMessages()` 那一步没做）。
  所以**审计/重放已经可用，崩溃恢复仍未闭环**，不能声称"P1 已解决"。

### 仍欠（本次未动，逐条可查）

1. **大输出只截断不落盘**：`tool_registry.py:1101` 仍是 head 60% / tail 40%
   字符截断到 4000；MCP 侧有 spill，两条通道不一致。
2. **子代理无深度上限**：`mbdsdr_ai/subagents.py` 无 `maxDepth`/`max_depth`。
3. **编排不可取消**：`orchestrator.py` 只有 `CANCELLED` 枚举，无 cancel 路径。
4. **重试判定靠字符串/正则**：`tool_registry.py` 的中文映射表与
   `timed out` 正则，不是 `ToolResult.error` 结构化 code。
5. **调谐/云台分级确认**、**统一沙箱**、**C++ 写开关与 Python gate 两套**：
   与 2026-11-08 结论一致，未变。
6. **未做 DSH 的 `tools/pre-execute|execute|post-execute` 三段钩子**：
   我们的门是硬编码在 `call_from_model` / `mcp_server.call_tool` 里，
   新增策略必须改代码、无法由插件挂载。这是与 DSH 最大的结构差，
   但因为两条通道已共用 `tx_guard`，安全语义至少是一致的（这点比 11-08 好）。
