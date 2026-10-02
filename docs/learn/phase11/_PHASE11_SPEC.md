# 第十一阶段实现规格：真机端到端演练 + 论文投递包 + 产品体验深化 + 移动端对齐

> 基线：远端 main = 469e48e（本地已同步）。第十阶段已交付：--paste 回传块（45 测试）、15 分钟验收手册、WCL checklist + paper_package.sh（36 文件）、时空产品化（ctest 94/flutter 304）、稳定性收尾（AMR 特征提取根因文档化）。验证基线：cpp 94/94、flutter 304、python 197+7skip。
>
> 环境事实：云 VM 无硬件/无凭据（推送由用户外部）；paper/experiments 被忽略；refs 待核实清单在 main.md:311-321（6 类，refs.bib 未建）；spectrum_display 已有 mousePress/Grab/wheel 交互（826-858 行附近）；Flutter AndroidManifest 已有 USB host（required=false）+ Info.plist 定位权限（无 RECORD_AUDIO 有意）；桌面增益空表回退 25.4dB（_UNKNOWN_TUNER_FALLBACK_GAIN_DB）。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 真机端到端演练（高优先）** | ①一键演练脚本 tools/acceptance_run.sh（或扩展 diag_wizard：插设备→diag_wizard→onboard 三类→exp_ota_run→提示时空视图，逐步执行并输出检查表 JSON 供回传）；②"贴回 --paste 块后的应答模板"docs/learn/phase11/P1-paste-response.md（我方收到用户贴回输出后的标准应答结构：结论/下一步命令/常见故障对照/需用户补跑的命令）；③测试（脚本分步逻辑/检查表生成/坏输入）。 | 演练脚本 + 应答模板 + 测试 |
| **P2 论文收尾与投递包** | ①refs.bib 推进：用公开检索（general_search/web_fetch）核验 main.md:311-321 清单的真实文献（Wilson 1927、SGP4、SigMF、OpenAI function-calling、GNU Radio/SDR++/SatDump 等），产出 `docs/learn/phase7/paper/refs.bib`（BibTeX 条目 + 每条的核实状态/检索日期注释；**禁虚构——查不到确切出处就标 [未核验] 不伪造卷期页码**）；②作者/致谢/基金诚实占位（main.tex \thanks 与 main.md 元信息，标 PENDING 不编造）；③paper_package.sh 升级：打包输出 zip + 自检（图/表/CSV/manifest/tex 引用链一致性校验：正文引用 Fig.X/Tab.Y ↔ 打包图清单 ↔ CSV 列名）；④PENDING 项保持标注。 | refs.bib + 占位更新 + 打包自检 + 测试 |
| **P3 产品体验深化** | ①用户视角走查 docs/learn/phase11/P3-ux-walkthrough.md：模拟"新人打开软件 5 分钟能做什么"逐屏走查（启动→连接/空态→调谐→解码→卫星/时空视图），产出可执行体验改进清单；②落地能落地项（频谱交互手感收尾：点击调谐/滚轮步进与 snap 对齐核对、状态栏密度、面板弹性），纯逻辑可测部分补测试；③对照第八~十阶段全部改动做体验一致性复查。 | 走查报告 + 改进清单 + 落地项 + 测试 |
| **P4 移动端对齐** | ①Flutter 对齐桌面第十阶段时空卡片语义（时间源/多普勒门控/空态文案一致，该移植移植）；②增益回退语义对齐（桌面空表回退 25.4dB vs Flutter 增益行为——架构性差异诚实记录或对齐）；③快捷键语义（移动端无快捷键，记录架构性）；④Flutter 真机编译依赖完备性核对（AndroidManifest/Info.plist 权限声明、平台 API 版本）；⑤测试（flutter 304 基线不破+新增；analyze 0）。 | 对齐清单 + 必要修复 + 测试 + 文档 |

## 2. 验收（每批必过）
- cpp：ctest **94/94 基线不破** + 新增全绿（P3 涉 C++）；全量构建 0 错误。
- Flutter：**304 基线不破** + 新增全绿；analyze 0（P4）。
- Python：pytest 新增全绿。
- 论文：refs.bib 每条件目真实可核验或诚实标 [未核验]；无虚构卷期页码；打包自检通过。
- git：只暂存相关文件（禁 add -A）；paper/ 根继续忽略；无"比赛/competition"、无密钥、无敏感信息。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁虚构数据/文献（refs.bib 宁缺毋滥）；无硬件诚实空态；MIT 干净室；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
