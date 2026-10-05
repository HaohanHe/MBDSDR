# 第五十一阶段实现规格：项目本体尽善尽美（能发表的完成度）

> 基线：HEAD = 31d7b34（已推送）。四块全部真实可测、诚实空态、不 mock。
> 能力盘点：接收链=盲 CFO（平方环 ±1Hz 实测）+ Gardner（盲扫眼心 seed，0/30 滑移）+ CCSDS 级联（Viterbi+RS，BER 1.66% 全图恢复）+ 实时多普勒补偿（四态限幅状态机）+ onboard 一条命令（fsphil/ccsds 两路径）；工具层=35 工具 × ControlHub/HTTP/Agent 三通道；实验层=固定种子/Wilson CI/口径标注，pytest 164/7 skip、flutter 384、ctest 129 注册。

## 块 1（Python 域）：接收链路全场景健壮性测试（核心）
合成 IQ 注入全链四场景：(a) 弱信号 SNR 梯度找诚实失效阈值；(b) 突发（前导 0 信号 + 中途加噪段）；(c) 多普勒扫频（持续变化 + 丢失/恢复）；(d) 窄带 CW 干扰叠加。每场景真实 BER/解码结果/失效点，诚实标注不保证场景与原因；沉淀 mbdsdr_ai 测试套件或实验脚本。

## 块 2（C++ 域）：35 工具三通道行为对齐
逐一核对 ControlHub（本地控制）/HTTP JSON 端点/Agent function-calling 三通道对同一工具的行为一致性（参数/返回值/错误码/手动门控）；差异低成本修、架构性如实记录（file:line）；补 HTTP 端点测试。

## 块 3（experiments 域）：实验可复现性复核
experiments/ 全部脚本重跑，核对固定种子/Wilson CI/样本数/manifest 口径标注与实际输出一致；发现覆盖性/一致性缺口修；输出「实验↔论文结论↔CSV 文件」可回溯清单（数字可复算）；LLM 需 key 项诚实 PENDING 不 mock。

## 块 4（C++ 域）：稳定性长跑
桌面或纯 DSP 长时间值守压力测试（持续 IQ 流 10 分钟级，监控内存/丢包/重连/解调不崩）；发现泄漏/漂移修；给真实数据。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE；诚实空态；
- 8GB OOM（-j2，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- pytest 164/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）；
- 如实报告 file:line、各场景真实数字、未解决项。
