# 第二十阶段实现规格：SDR++ 剩余 decoder 精读对标 + 落地决策

> 基线：HEAD = debe7b2，ctest 112/112（e2e_smoke 云 VM 无音频环境性红项不计）。
>
> 已对标：pager/m17/vor（落地）、meteor/weather_sat（深研+YAGNI）。本轮覆盖 atv/dab/falcon9/kg_sstv/ryfi。

## 步骤一：精读（已完成）
- 合并笔记 `docs/learn/phase20/sdrpp-decoder-remaining.md`（351 行，31KB），每模块 file:line 四要素 + 去重对照。
- 真读文件：atv main/linesync/amplitude（filters 为死代码表）；dab main/dab_dsp（dab_phase_sym 为 2048 相位参考常量表）；falcon9 main/falcon_fec/falcon_packet；kg_sstv main/kg_sstv_dsp；ryfi main + ryfi/ 11 文件（transmitter 发射侧未读，与解码决策无关）。

## 步骤二：落地决策（全部 YAGNI，经主控独立抽查坐实）

| 模块 | 决策 | 核心理由 | 回补条件 |
|---|---|---|---|
| **DAB** | YAGNI（机制记档） | SDR++ 仅 OFDM 同步前端（CP 自相关切 2048 + 相位参考符号 FFT 找频偏），**数据符号路径空**（dab_dsp.h:257-262 仅 `sym++`，已抽查）；MBDSDR Python 已有 FIC/FIG/ETI 元数据层（mbdsdr_ai/dab_plus_lite.py 21KB，已抽查存在）；缺「2.048Msps→ETI」整段 OFDM 信道解码且 SDR++ 未给齐；国内无现役 DAB、云内无金标准 | (a) 拿 DAB IQ 录制样本；(b) MBDSDR 新增任意 OFDM 模式时把 CP 自相关+相位参考 FFO 抽成通用件；(c) 用户明确 P0 要听 DAB |
| **ATV** | YAGNI | NTSC 色副载波通路全注释（main.cpp:171-217,222-228）只出灰度；行同步 PLL 与 apt_decoder.h:114-132 同型；需 7MHz 带宽（MBDSDR-Mini ~2MHz 不够） | 硬件带宽 ≥10MHz + 业余 ATV 需求 |
| **Falcon9** | YAGNI | SpaceX 私有协议、pktId 硬编码、仅发射窗口+S 波段抛物面站可听；机制（FM→MM→32bit sync→CCSDS RS×5→PRBS）MBDSDR m17/ccsds_rx 全有 | 固定卫星地面站 + 公开遥测文档 |
| **RYFI** | YAGNI | 作者私有 QPSK 实验模式、无部署网络；QPSK+K=7 r=1/2 Viterbi+RS(255,223)×4+PRBS 全是已有件；SDR++ 帧长 8168 硬编码带 TODO、收包只 debug 日志 | 出现公开 RYFI 网络或点对点实验需求 |
| **KG-SSTV** | YAGNI | SDR++ 每帧只落 7 字节 kgsstv_out.bin（kg_sstv_dsp.h:245，已抽查）、**无图像重建**；4FSK 1200baud+Viterbi 机制已有；国内无活动 | 用户明确要 KG-SSTV + 录制样本回归 |

## 质量门
- 无落地代码（决策 YAGNI）；112 基线不破（本批零生产改动）。
- 笔记 file:line 可查、GPL 不入库；如实报告（MainAgent 亲自复核）。

## 后续
Phase21 或用户触发回补条件后，DAB 落地路径：读 ETSI EN 300 401 标准原文 → 2.048Msps 复基带→OFDM 均衡/解映射→解交织→Viterbi+RS→ETI→复用 dab_plus_lite 元数据层 → 合成 OFDM ctest + 接入模式/ControlHub/Agent/UI（沿用 P18 三面板模式，tool_schema 与 test_ai_real_link 清单同步）。
