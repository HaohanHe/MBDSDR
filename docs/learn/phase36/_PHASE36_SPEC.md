# 第三十六阶段实现规格：GNU Radio 机制学习落地
> **交付状态（Phase41 收官复核，2026-10-05）：已交付。** 本阶段规格各项已落地、并通过当时记录的测试基线（见下方基线行）；跨阶段未决项统一登记在 [../phase41/open-items.md](../phase41/open-items.md)。本文件保留为历史规划快照。
>

> 基线：HEAD = 677cb4c（已推送），ctest 126/126、flutter 342、pytest 42/42。
> 学习材料：repos/gnuradio（上游，gitignore 不入库；干净室学机制不抄代码）。
> 注意：8GB cgroup OOM 限制（Phase35 已踩，全树并行重编受限→单目标构建验证，如实记录）。

## Wave 1：机制深读（5 子 agent 并行，每篇笔记 docs/learn/phase36/<机制>.md，120-200 行，file:line 留痕）
| # | 机制 | 精读对象（repos/gnuradio） | 我方对照 |
|---|---|---|---|
| G1 | 块式流处理 | gnuradio-runtime（block work 语义/flowgraph/scheduler/buffer 管理） | cpp/src/dsp/ 流链 |
| G2 | 采样率转换链 | gr-filter（rational_resampler/fir decimation 链） | channelizer/audio_resampler/rational_resampler |
| G3 | AGC 与增益控制 | gr-analog（agc/agc2/fast_agc/feedforward） | agc.{h,cpp}（Phase35 已落地块级前瞻） |
| G4 | FFT 窗口/频谱 | gr-fft（window/fft） | fft.h/spectrum_render.h |
| G5 | message passing（PMT） | gr-pmt + block message 机制 | ai/ 事件信号、ControlHub 消息 |

每篇笔记结构：机制总结 / 关键算法（file:line）/ 可借鉴点 / 我方差距判定（补齐 vs YAGNI+理由）/ 反例核查。

## Wave 2：落地 + 差距文档
- 高价值点落地（选 1-2 项真差距且有产品价值，干净室机制重写，带 ctest；候选：流式块调度抽象/多级抽取链优化/PMT 式消息评估）；单目标构建验证（OOM 约束）；
- docs/learn/phase36/gap-table.md 逐项裁决表（机制 | gnuradio 实现 | 我方现状 | 差距判定 | 证据 | 本轮动作）。

## 质量门 / 红线
- 干净室不复制 GPL；通用非专用；无假数据；无比赛字样；活动参数禁入；
- ctest 126/126 不回归（全量输出）、flutter 342/analyze 0（若动 mobile）、pytest 42 不回归；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试全量输出、YAGNI 裁决、未解决项。
