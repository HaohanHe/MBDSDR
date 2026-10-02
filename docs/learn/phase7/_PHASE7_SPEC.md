# 第七阶段实现规格：论文可投稿冲刺 + 新时空融合 + 开源学做 + 端侧收尾

> 基线：远端 main = 0cf1af2（本地已同步）。第六阶段已交付：包名 mbdsdr.app（APK 真编译）、真机回传解析（parse_hw_report 24 测试）、对标缺口四项（ctest 91/91）、文档同步。
>
> 环境事实（2026-10-02）：云 VM 无硬件；实验产物（paper/experiments/，被 .gitignore 忽略）：9 张 synthetic 图（ebno/doppler/duration/baseline/amr 混淆矩阵/rate_bandwidth/llm）+ 多 CSV；paper/ 根目录整体被忽略（含早期 TCCN-ADR-Draft-v0.1.md 骨架，**不可入库**）；SigMF 录制已有 `captures[]/core:datetime`（recorder.cpp:64-69，playback.py:84-86 可读）；serial_gnss.py 有 NMEA utc_time 字段；全仓无多普勒补偿资产（需新建）；repos/ 有 SatDump/gnuradio/Stellarium 源码。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **Q1 论文冲刺（高优先）** | 整合现有实验结果成完整图表体系（图/表统一编号、口径+样本数+日期标注、synthetic 明确）；补 3 个缺失实验（均接公共模块 runner/manifest/plot、固定种子）：①解码成功率 vs 采样率；②多普勒补偿收益（补偿前后解码成功率对比，确定性仿真）；③Agent 工具调用开销/成功率分析（复用 exp_weak_model_toolcall 基础，LLM 列 PENDING_ONLINE_RUN 诚实）；产出可投稿初稿：docs/learn/phase7/paper/（受版本控制）下 Markdown 或 LaTeX 源 + 编译指引（LaTeX 模板如 IEEE 样式），结构 Abstract→Intro→System Design→Experiments→Discussion→Related Work→Conclusion，篇幅对标 IEEE WCL（约 4-5 页量级），图表与数据一致，不虚构数据；README 说明投稿状态与 PENDING 项。 | 3 实验脚本 + 图表体系 + 论文初稿 + 编译指引 + pytest |
| **Q2 新时空融合** | GNSS 授时接链路：serial_gnss NMEA utc_time → SigMF captures core:datetime 对齐（写入与读取两端，recorder/playback/datasource 对齐）；TLE 新鲜度与过境预测联动（过境预测输入时间源显式化：GNSS 时间可用则用之，否则系统时间并标注；新鲜度阈值统一）；多普勒补偿闭环：新建 doppler 补偿模块（确定性仿真：已知轨道多普勒频移→补偿→解码成功率对比实验），无硬件诚实空态；每项云内确定性测试。 | 模块 + 实验 + 测试 + 文档 |
| **Q3 开源学做** | 对照 repos/SatDump（图像增强管线，如 img 相关 src）、repos/gnuradio（gr-filter 滤波器设计）、repos/Stellarium（天空渲染/交互）真读源码；产出 2-3 份学习笔记 docs/learn/phase7/Q3-*.md（file:line，MIT 干净室）；挑至少 1 个能力落地为真实现（建议 SatDump 式图像增强：对比度/伪彩增强管线，Python 或 C++，带确定性测试；其余按价值落地或标注"学习完成不落地"）。 | 学习笔记 + 真实现 + 测试 |
| **Q4 端侧收尾** | ①parse_hw_report 输出示例接入文档/README（P2 文档 + onboarding README 补"解析输出示例"一节，用真实字段的示例文本）；②桌面四项（热插拔提示/增益档/声卡健康/VFO 出声）收尾复查：对照 P3 交付与 B4 审计逐项核对实现与测试覆盖，产出收尾报告 docs/learn/phase7/Q4-desktop-review.md（含未完成/真机待验项）；③Flutter 与桌面行为一致性抽检：对比两端（录音/回放、连接状态、增益/档位、VFO 出声语义、get_status 字段），产出差异清单 docs/learn/phase7/Q4-cross-platform.md，修复确有必要且云内可验证的差异（带测试）。 | 文档示例 + 复查报告 + 差异清单 + 必要修复 |

## 2. 验收（每批必过）
- cpp：ctest **91/91 基线不破坏** + 新增全绿（Q2/Q3/Q4 涉 C++ 才需要）；全量构建 0 错误。
- Flutter：**288 基线不破坏** + 新增全绿；analyze 0（Q4 涉 Flutter 才需要）。
- Python：pytest 新增全绿；实验脚本云内实跑（synthetic 口径）产物带口径/样本数/日期。
- 论文：所有图/表数据与 CSV 一致、口径标注完整、LLM 列 PENDING 诚实、无虚构数字。
- git：只暂存相关文件（禁 add -A）；paper/ 目录继续忽略（论文初稿在 docs/learn/phase7/paper/ 受控）；无"比赛/competition"、无密钥、无敏感信息。
- 推送：云环境无有效凭据（历次一致）——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁虚构数据（论文数字全部来自真实实验产物）；无硬件诚实空态；MIT 干净室（GPL 只学机制）；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
