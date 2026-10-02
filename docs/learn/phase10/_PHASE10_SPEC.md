# 第十阶段实现规格：真机验收冲刺 + 论文投递打磨 + 时空产品化 + 稳定性收尾

> 基线：远端 main = d709e68（本地已同步）。第九阶段已交付：诊断向导（diag_wizard 18 测试）、论文 v0.4 + main.tex 骨架、桌面时空视图 tab + demo_spacetime、自动化稳定（run_all_tests.sh/ci.yml/test_solar_system skip）。验证基线：cpp 94/94、flutter 298、python 168+7skip。
>
> 环境事实：云 VM 无硬件/无凭据（推送由用户外部）；paper/experiments 被忽略；diag_wizard 已有 `--json` 结构化建议输出（tools/diag_wizard.py:25）；main.tex 已有 8 结构块；时空视图已接 GNSS 串口/遥测（main_window.cpp:696-705、spacetime_format.h）；Flutter 有 sky_page 但无时空状态页；tests/ 大套件顺序相关问题未文档化；CI workflow 已标"结构就绪待 runner"。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 真机验收手册与回归（高优先）** | ①"真机 15 分钟验收脚本"docs/learn/phase10/P1-acceptance-runbook.md：从插设备到出成果逐步清单（diag_wizard → onboard 三类信号 adsb/apt/cw → exp_ota_run → 桌面时空视图），每步预期输出 + 常见失败对照表 + 耗时预算；②diag_wizard 输出做成**可直接粘贴回传格式**：加 `--paste` 模式（人类可读 + 一段机器可解析 JSON 的紧凑回传块），parse_hw_report 兼容解析；③测试（回传块格式/解析往返/坏输入）。 | 验收手册 + 回传格式 + 测试 |
| **P2 论文投稿冲刺** | ①WCL checklist 逐项核对（docs/learn/phase10/P2-submission-check.md）：标题/摘要指标/图表自洽/基线对比/related work/篇幅；②LaTeX 可编译性检查（云内如有 pdflatex/pandoc 则试编译 main.tex 或 body 段，无则静态检查 + 标注）；③占位符/图引用/作者占位/IEEE 版权声明位置核对；④"投稿包"清单与打包脚本（tools/paper_package.sh 或文档：main.md/main.tex/图/CSV/manifest 收集与命名）；⑤PENDING 项（LLM 在线、OTA recorded）诚实标注。 | 核对清单 + 打包脚本/说明 |
| **P3 时空产品化** | ①桌面时空视图与真实链路接线复查与补齐：真实 SDR/GNSS 时四格实时更新（信号格 RSSI/SNR、GNSS 格真实 fix）、时间源真实切换（串口 NMEA→gnss）、多普勒补偿实际生效并显示补偿值（engine/doppler 值接入 spacetime 行）；无硬件诚实空态保持；②Flutter 端对应页面：检查并补"时空状态"卡片页结构（时间源/目标/多普勒/GNSS 四格，复用 gnss 服务层，无硬件空态）；③测试（C++ 纯逻辑 + Flutter 新页测试，两基线不破）。 | 桌面接线 + Flutter 页面 + 测试 + 文档 |
| **P4 稳定性与体验收尾** | ①tests/ 大套件顺序相关 9 failed 根因排查或至少文档化（docs/learn/phase10/P4-stability.md：现象/复现/定位尝试/结论/后续建议——能修则修，不能修如实记录，不得扩大改动面）；②CI workflow 注释补 runner 要求（Qt 6.8 安装、Flutter SDK、Python 版本清单）；③第八/九阶段改动的回归复查（快捷键目录/增益档/多 VFO/热插拔/时空视图：对照改动文件 + ctest 关键项复跑，产出复查记录）。 | 稳定性文档/修复 + CI 注释 + 复查记录 |

## 2. 验收（每批必过）
- cpp：ctest **94/94 基线不破** + 新增全绿（P3/P4）；全量构建 0 错误。
- Flutter：**298 基线不破** + 新增全绿；analyze 0（P3 涉 Flutter）。
- Python：pytest 新增全绿；回传格式解析往返测试。
- 论文：checklist 逐项结论、打包说明可用、PENDING 诚实。
- git：只暂存相关文件（禁 add -A）；paper/ 根继续忽略；无"比赛/competition"、无密钥、无敏感信息。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁虚构数据/文献；无硬件诚实空态；MIT 干净室；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
