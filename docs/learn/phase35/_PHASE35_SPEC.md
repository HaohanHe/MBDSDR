# 第三十五阶段实现规格：SDR++ 精髓深挖
> **交付状态（Phase41 收官复核，2026-10-05）：已交付。** 本阶段规格各项已落地、并通过当时记录的测试基线（见下方基线行）；跨阶段未决项统一登记在 [../phase41/open-items.md](../phase41/open-items.md)。本文件保留为历史规划快照。
>

> 基线：HEAD = b20bfb7（已推送），ctest 124/124、flutter 342、pytest 42/42。
> 学习材料：repos/sdrpp（上游源码 8c9f5ee，gitignore 不入库，只作学习材料；干净室学机制不抄代码）。

## Wave 1：模块深读（5 子 agent 并行，每篇笔记 docs/learn/phase35/<模块>.md，120-200 行，file:line 留痕）
| # | 模块 | 精读对象（repos/sdrpp） |
|---|---|---|
| L1 | radio 前端 | decoder_modules/radio（NBFM 立体声/AGC/静噪/解调链实现细节） |
| L2 | dsp 管线核心 | core/src/dsp（decimating_fir/agc/fast_agc/fft/噪声抑制/多速率） |
| L3 | scheduler + modem 组织 | misc_modules/scheduler + decoder_modules 组织（处理链/调度） |
| L4 | 频谱/瀑布渲染 | core/src/gui（fft 窗口/色板/历史/渲染管线） |
| L5 | 文件/录制 + 反例核查 | sink_modules（file_sink 等）+ 各模块反例核查 |

每篇笔记结构：机制总结 / 关键算法（file:line）/ 可借鉴点 / 我方差距判定（补齐 vs YAGNI+理由，对照 cpp/src/dsp/ 55 个头文件）/ 反例核查（SDR++ 里我方"以为有但实际没有或不同"的点）。

## Wave 2：差距表 + 落地
- 差距表：docs/learn/phase35/gap-table.md 逐项裁决表（模块 | SDR++ 机制 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作）；
- 高价值点落地（选 1-2 项真差距且有产品价值，干净室机制重写，带 ctest）：候选 NB FM 立体声解调链 / AGC 平滑机制 / 频谱渲染细节；
- 反例核查汇总（docs/learn/phase35/counterexamples.md）。

## 质量门 / 红线
- 干净室不复制 GPL（学机制不抄代码）；通用非专用；无假数据；无比赛字样；活动参数禁入；
- ctest 124/124 不回归（全量输出）、flutter 342/analyze 0（若动 mobile）、pytest 42 不回归；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试全量输出、YAGNI 裁决与理由、未解决项。
