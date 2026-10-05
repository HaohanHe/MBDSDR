# 第五十阶段实现规格：Agent 层最后收尾

> 基线：HEAD = 113a7a8（已推送）。把 Phase39 G1 已实现但缺入口的上下文压缩接到 UI，并核对两端 Agent 会话管理一致性。
> 现状侦察：G1 `ContextCompaction compactHistory(...)` 已实现（mobile/lib/services/ai_client.dart:436；send 流程 :559 自动调用、只压上送层不删落盘原文）；预算 kAiContextBudgetChars=6000（tokens.dart:195）；ChatPage 会话栏/抽屉 CRUD 完整（新建/切换/删除，重命名 G7）；桌面等价 cpp/src/ai/ai_context.cpp `compactContext`。
> **缺：生产 UI 无手动「压缩上下文」入口。**

## 块 1：移动端压缩入口（mobile）
- ChatPage/AI 面板加「压缩上下文」按钮（未达门槛/无历史 → 禁用 + tooltip 说明，触控≥44）；
- 点击**真实调用 compactHistory G1 折叠逻辑**，显示折叠了多少条（**真实计数，不伪造**）；占位标注「已压缩 N 条」。

## 块 2：两端会话一致性核对
- 逐项核对桌面/移动会话 CRUD（新建/重命名/删除/切换）、历史 JSON 格式、incomplete/角标状态是否对称；
- 差异项判定：低成本该修 / 架构性不修（如实记录原因）；桌面 cpp 只读核对（不重编）。

## 块 3：压缩触发可观测
- 自动压缩（阈值触发）与手动压缩状态文案统一纯函数；给真实 token/条数估算口径；阈值走具名常量不硬编码。

## 块 4：回归
- flutter 377（全量输出实际计数 + analyze 0）；pytest 164/7skip、ctest 129（不动则不触发，如实说明）。

## 质量门 / 红线
- 干净室 MIT；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock（无历史/未达门槛诚实禁用，不渲染假动作）；
- 8GB OOM（不重编 cpp，纯 Dart；如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- 如实报告 file:line、折叠真实计数、未解决项。
