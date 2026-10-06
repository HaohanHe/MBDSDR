# 第五十四阶段实现规格：接收链能力包络量化 + 级联软判决 + 接收能力工具化

> 基线：HEAD = 704e035（已推送）。把接收能力从"单点验证"升级为"完整包络可量化、可被 Agent 调用"。
> 现有接收能力：盲 CFO（平方环）、Gardner 定时、CCSDS 级联（Viterbi 硬判决+RS）、实时多普勒补偿、分段 AFC、CW notch、多缓冲 resync、二阶 PLL；单点失效边界已实测（弱信号 sd≈1.3-1.6、CW amp≈0.5-0.8、陡扫频低 SNR 失锁）。
> **架构事实（防虚构）**：PLL/AFC/notch/resync/软判决实现位于 **Python（mbdsdr_ai，onboard 链）**；桌面 C++ 有 DopplerStepLimiter（多普勒补偿）但无 PLL/notch/软 Viterbi。工具化必须落在能力真实所在，不虚构 C++↔Python 桥接。

## 块 1（Python，核心）：完整能力包络网格
- 多维参数网格：SNR（sd 0.3→2.0）× CFO（0→1000Hz）× 扫频斜率（0→1000Hz/帧）× CW 强度（0→20）× 遮挡长度（0→长），每格真实解码结果（ASM/RS/MCU 恢复数）；
- 形成**能力包络图/表**（可恢复组合、失效面位置）；网格确定性（固定种子）、真实样本数、分批跑；
- 诚实标注失效面与理论解释（PLL 带宽-噪声权衡等）；沉淀实验脚本 + 数据图。

## 块 2（Python）：Viterbi 软判决 LLR
- 硬判决 Viterbi 升级为软判决（BPSK 软信息/LLR 输入，公开教科书算法干净室重写）；
- 弱信号（多 SNR 点）BER/解码成功率 vs 硬判决对比，真实改善数字；与 RS 串级联全链实测；仍不改善区间诚实标 FAIL。

## 块 3：接收能力工具化
- **Python 侧（深链 agent）**：把 PLL/AFC/notch/resync/软判决开关注册进 Python Agent 工具注册表（sdr_tools.py/registry），给真实新工具计数，带工具测试；无设备/无 IQ 诚实空态（开关禁用说明）；
- **桌面 C++ 侧（P51 agent）**：审计 C++ 真实存在的接收控制（多普勒补偿等），缺三通道暴露则补（ControlHub/HTTP/Agent + 手动门控对齐 + 测试）；Python-only 能力如实标注架构边界（不虚构 C++ 工具/桥接）。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2，网格分批，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- pytest 179/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）；
- 如实报告 file:line、包络真实数字、软判决改善、新工具计数、未解决项。
