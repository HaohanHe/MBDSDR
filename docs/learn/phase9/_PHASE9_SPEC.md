# 第九阶段实现规格：真机即战力 + 论文可投终稿 + 新时空可演示 + 自动化稳定

> 基线：远端 main = d82247f（本地已同步）。第八阶段已交付：真机回填闭环（exp_ota_run）、论文 v0.3、桌面/移动端打磨。验证基线：cpp 93/93、flutter 298、python 全绿（mbdsdr_ai/test_solar_system 7 项为历表不可用环境性失败，本阶段修复）。
>
> 环境事实：云 VM 无硬件/无凭据（推送由用户外部）；paper/experiments 被忽略；桌面 main_window.cpp 已有 centerTabs_（QTabWidget，频谱/世界/气象）可加新 tab；test_solar_system.py:26-28 硬断言历表可用；无 .github/workflows、无统一测试脚本。

## 1. Wave1（四个并行批次；P1 不动 C++，P3 独占 C++ UI 改动，避免 main_window 冲突）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 真机首跑完备性（高优先，tools/ 侧）** | ①依赖完备性核对：onboard/selfcheck/exp_ota_run 真机依赖清单（rtl_sdr 二进制、pyrtlsdr/SoapySDR、udev 规则、plugdev/dialout 组、常见失败路径表）；②交互式诊断向导：新 tools/diag_wizard.py（读 selfcheck --json 输出，逐项 FAIL/WARN 直接给出可复制的下一步命令与修复建议，坏输入退出码 2）；③文档 docs/learn/phase9/P1-first-run.md（真机首次运行手册：插设备→组权限→udev→自检→onboard 一条命令）；测试（向导解析/建议映射/坏输入）。 | 诊断向导 + 依赖清单 + 手册 + 测试 |
| **P2 论文终稿化** | 在 v0.3 基础上按投稿审稿视角终稿（v0.4）：标题是否传达贡献、摘要是否含指标数字、图表编号/引用自洽、实验含 baseline 对比（经典 vs AI vs Agent）、Related Work 充分性；修订 main.md + CHANGES.md 追加；产出 LaTeX 骨架 `docs/learn/phase7/paper/main.tex`（IEEEtran 结构占位，图表引用注释式标注迁移点）或 IEEEtran 迁移指引（main.md 保持单一事实源，README 说明同步机制）；PENDING 项清单更新。禁虚构文献/数据。 | 终稿 + LaTeX 骨架/指引 + PENDING 更新 |
| **P3 新时空演示（C++ 独占）** | ①桌面"时空视图"tab（centerTabs_ 追加）：当前接收目标 + 时间源（gnss/system，含 utc 与来源标注）+ 多普勒补偿状态 + 四格总览（设备/信号/解码/GNSS，无硬件诚实空态）；②Python 演示脚本 demo_spacetime.py（云内确定性演示路径：注入 GNSS 时间 → SigMF 时间戳 → 多普勒补偿 → 解码 → 图上时空标注，一条命令跑通）；测试（UI 纯逻辑/状态映射 + demo 确定性断言）。 | 时空视图 tab + 演示脚本 + 测试 + 文档 |
| **P4 自动化与稳定** | ①本地一键测试脚本 `scripts/run_all_tests.sh`（cpp ctest offscreen + flutter test/analyze + python pytest 全量，逐项退出码汇总，缺依赖项标注 SKIP）；②.github/workflows/ci.yml（结构完整、诚实标注：runner 需 Qt/Flutter 环境配置、无硬件项 skip，可先建脚本+workflow 骨架待配置）；③修 test_solar_system.py：历表不可用时**优雅 skip 带原因**（保留可用时的完整断言），消除环境性失败；④测试脚本自验证。 | 一键测试脚本 + workflow + 历表修复 |

## 2. 验收（每批必过）
- cpp：ctest **93/93 基线不破** + 新增全绿（P3）；全量构建 0 错误。
- Flutter：**298 基线不破**（P4 跑脚本验证）。
- Python：pytest 新增全绿；test_solar_system 不再硬失败（skip 带原因）。
- 论文：v0.4 图表自洽、baseline 对比明确、PENDING 诚实；LaTeX 骨架可用/指引清晰。
- git：只暂存相关文件（禁 add -A）；paper/ 根继续忽略；无"比赛/competition"、无密钥、无敏感信息。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁虚构数据/文献；无硬件诚实空态（时间源=system、GNSS=无 fix、多普勒=未补偿，不伪装）；MIT 干净室；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
