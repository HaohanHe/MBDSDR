# 第四十一阶段实现规格：验收总检收官
> **交付状态（Phase41 收官复核，2026-10-05）：已交付。** 本阶段规格各项已落地、并通过当时记录的测试基线（见下方基线行）；跨阶段未决项统一登记在 [../phase41/open-items.md](../phase41/open-items.md)。本文件保留为历史规划快照。
>

> 基线：HEAD = 58d9f62（已推送），ctest 127 全过+e2e_smoke 诚实 SKIP、flutter 351、pytest 42/42。
> 注意：Phase40 遗留——qt_audio_sink.h 改动触发 ~30 目标重编被预算截断，全量构建未跑完；本轮先补构建再全量验证。

## 块 1（A：全链路对账）
- 先补全量构建（-j2 防 OOM）→ ctest 全量（127 基线 + startup_robustness 新增 + e2e_smoke Skipped）、flutter 351+analyze 0、pytest 42（experiments/onboarding/hw_selfcheck/diag_wizard/acceptance_run）、APK 编译状态；
- 对照各 Phase 交付声明逐项核"已做 vs 实际存在"（代码/测试/文档），出不实项清单 → docs/learn/phase41/full-chain-audit.md。

## 块 2+4（B：README 定稿 + 总账）
- 根 README 更新：五组件表（35 工具数核对）、快速开始、真机状态诚实声明、LLM PENDING 更新（ControlHub/HTTP 50732、Chromebook 节、测试数字 127+1/351/42）；
- docs/learn 状态横幅 phase31-41 全部标已交付（若存在横幅机制）；
- 未解决项总账 docs/learn/phase41/open-items.md（项 | 状态 | 卡点 | 解锁条件：真机验收/LLM 在线列/OTA recorded/作者名单/429-503 transport 遗留/窄窗 3 处/compass 画布字面量等）。

## 块 3（C：活动前最终核对）
- event-checklist（docs/learn/phase34/）逐项复核：设备/天线/预测/链路预演/邮件模板；真机未验证项如实汇总清单；接收链路结论（Python 端）与 runbook 最新对齐 → docs/learn/phase41/event-final-check.md。

## 块 5（红线终扫）
- 全仓扫：比赛/competition 字样（仅规则文档元引用允许——如 _PHASEX_SPEC.md 红线条款，逐一确认是条款本身而非内容）、活动频率/参数进代码、假数据/mock/demo、密钥/token 入库、.figma.zip 入库；零命中才可交付。

## 质量门 / 红线
- 数字全量输出实际值；不实项不掩盖；防虚构；无比赛字样；活动参数禁入；不改功能行为（纯验收/文档，若发现真 bug 顺手修则带测试）；OOM 约束；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、未解决项总表、红线扫描结果。
