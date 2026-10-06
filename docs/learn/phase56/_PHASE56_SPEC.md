# 第五十六阶段实现规格：弱信号同步容忍 + 软路径联合精恢复

> 基线：HEAD = 64f7e7a（已推送）。Phase55 定位弱信号新瓶颈=ASM 严格 32-bit 匹配先失同步（非 Viterbi）；把同步改容忍、软路径接精定时/载波，下推弱信号边界。
> 遗留：sd≥0.8 ASM 严格匹配先失同步两路径 0 帧（需 hamming 容忍）；软路径未联合 gardner/PLL；隔离 14× 增益、全链 sd=0.8 硬0/软36 已兑现。
> 文件：ASM=ccsds_rx.py（AsmFramer）；全链=ccsds_ssdv.py；gardner/PLL=ssdv_phy.py；onboard=tools/onboarding/onboard.py。

## 块 1（最高优先）：ASM hamming 容忍同步
- ASM 检测从严格 32-bit 匹配改为 **hamming 距离容忍**（阈值具名常量，如允许 1/2/3 bit 差异），硬/软两路径都接；
- 合成弱信号（sd 0.8→1.3 梯度）实测：**容忍前后 ASM 同步率/MCU 恢复真实对比**，找新诚实失效阈值；
- **误同步风险测试（必须诚实量化）**：纯噪声/随机数据在容忍阈值下的假同步概率，多 trial 真实计数；阈值不能放到假同步泛滥（理论参照：32-bit ASM，t=1 ≈33/2³²、t=2 ≈529/2³²、t=3 ≈5489/2³²，以实测为准）。

## 块 2：软路径联合精恢复
- 软全链接 **gardner 定时（盲扫眼心 seed）+ PLL 载波（二阶）**；
- 合成弱信号 + 频偏/扫频场景实测：**联合 vs 仅软 vs 仅硬 MCU 恢复真实对比**；仍失效区间诚实标 FAIL。

## 块 3：onboard/工具化
- 新能力（ASM 容忍阈值、软路径联合开关）接 onboard CLI；若落在可工具化层则接 Agent 工具/参数（真实计数）；无 IQ 诚实空态。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2，网格分批，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- pytest 188/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）；
- 如实报告 file:line、容忍前后真实数字、假同步率、联合对比、未解决项。
