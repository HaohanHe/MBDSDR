# 第八阶段实现规格：真机出成果 + 论文可投 + 体验超越参考软件

> 基线：远端 main = b6c1002（本地已同步）。第七阶段已交付：论文可投稿初稿（docs/learn/phase7/paper/main.md，309 行，10 图 Tab.I-VI）、新时空融合（GNSS→SigMF/TLE 三态/NCO 多普勒补偿）、开源学做（3 笔记 + image_enhance 21 测试）、端侧收尾（get_status 状态机，flutter 293）。验证基线：cpp 91/91、flutter 293、python 93。
>
> 环境事实：云 VM 无硬件/无凭据（推送由用户外部）；paper/experiments 被忽略（数据落点）；exp_ota_handoff.py 已支持 --recording/--recordings-dir 空态（n_samples=0 诚实输出）；parse_hw_report.py 就绪（11 测试）；桌面 ui/ 已有 shortcuts_dialog/spectrum_widget 等；Flutter ai_tools get_status 已带完整状态机。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 真机回填与 OTA 准备（高优先）** | "真机 JSON 贴回 → parse_hw_report 解析 → 录制 SigMF 目录 → recorded 口径指标入图"一键闭环：扩展/新写 exp_ota_run.py（接收 onboard 产出的 SigMF/CSV 目录，算解码成功率/AMR 准确率等指标，出 recorded 口径图+CSV+manifest，口径/样本数/日期标注；无录制时诚实空态 N=0 明确提示）；配套"回填操作说明"文档（真机三步：跑 selfcheck/onboard --json → 贴回 → 跑回填脚本）；onboarding README/experiments README 同步；确定性测试（注入固定 SigMF 录制目录验证指标计算与图产物；空态断言）。 | 回填脚本 + 文档 + 测试 |
| **P2 论文初稿打磨** | 对 docs/learn/phase7/paper/main.md 系统级自查并出修订稿：①结构完整性（Abstract→Intro→System Design→Experiments→Discussion→Related Work→Conclusion 各节是否齐、篇幅对标 IEEE WCL）；②图表编号与正文引用一致性（Fig.1-10/Tab.I-VI 全部有定义且有正文引用，数字与 CSV 一致）；③Discussion 覆盖实验局限（合成口径、单站观测性、理想多普勒上界、LLM PENDING）；④Related Work 文献真实性（逐条核对是否真实存在；无真实支撑的条目删除或改"待补"，**禁虚构文献**，宁缺毋滥）；产出修订稿 + 变更记录 docs/learn/phase7/paper/CHANGES.md（每处改动的理由）。 | 修订稿 + CHANGES.md |
| **P3 桌面体验打磨** | 对照"超越 SDR++/GNU Radio/SatDump"验收，先学后做补真实体验改进（不铺空壳，选 2-3 项价值最高的）：频谱交互手感（拖拽调频/缩放/余晖）、瀑布滚动流畅性、快捷键覆盖与一致性（已有 shortcuts_dialog，核对覆盖与实际接线）、状态栏信息密度、多窗口/面板布局；每项：现状 file:line → 上游做法（可参考 repos/，GPL 只学机制）→ 真实现 → 确定性测试（可测部分：快捷键映射、缩放/调频逻辑、状态栏格式化纯逻辑）；真机项（声卡、VFO 实战）诚实标注；产出 docs/learn/phase7/P3-desktop-polish.md。 | 实现 + 测试 + 文档 |
| **P4 移动端一致性** | Flutter 与桌面行为/状态/工具集一致性抽检补齐（get_status 状态机已加，检查其余：连接状态语义、录音/回放、增益控制、设置项、工具调用结果字段），逐项给"差异/影响/是否该修"；修确有必要且云内可验证项（带测试，flutter 293 基线不破、analyze 0）；无硬件诚实空态；产出 docs/learn/phase7/P4-mobile-consistency.md。 | 差异清单 + 必要修复 + 文档 |

## 2. 验收（每批必过）
- cpp：ctest **91/91 基线不破** + 新增全绿（P3 涉 C++ 才需要）；全量构建 0 错误。
- Flutter：**293 基线不破** + 新增全绿；analyze 0（P4 需要）。
- Python：pytest 新增全绿；回填/OTA 脚本云内实跑（空态路径）。
- 论文：修订稿引用一致、文献真实或诚实待补、无虚构。
- git：只暂存相关文件（禁 add -A）；paper/ 根继续忽略；无"比赛/competition"、无密钥、无敏感信息。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁虚构数据/文献；无硬件诚实空态；MIT 干净室（GPL 只学机制）；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
