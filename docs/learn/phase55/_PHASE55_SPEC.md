# 第五十五阶段实现规格：软判决全链闭环 + C++ 接收状态可观测/可控

> 基线：HEAD = cfb5e02（已推送）。Phase54 软判决 14× 增益仅隔离链验证（孤儿风险），串进全链真正提升弱信号出图；补齐 C++ 两个诚实遗留。
> 遗留：软判决全链未串（demod_bpsk_soft→decode_soft→解扰→RS 只到卷积 BER 未到全图）；C++ Costas PLL+AGC+MM（digital_demod.h 经 vfo_manager 接入）锁定状态 carrierLocked/EVM 未透出；多普勒补偿三通道未暴露（执行器只接 SpectrumEngine*，够不到 MainWindow 状态）。

## 块 1（最高优先，Python 域）：软判决全链闭环
- demod_bpsk_soft → decode_soft → 解扰 → RS 串成完整 RX 路径（可复用层）；
- 合成级联 IQ 弱信号（sd=0.8/1.0）实测全链出图：**硬 vs 软 MCU 恢复数/成功率真实对比**（隔离 14× 增益是否在全链兑现）；
- onboard 加软判决开关（如 --ssdv-soft）；纯噪声诚实空态；仍失效区间诚实标 FAIL。

## 块 2（C++ 域）：Costas 状态透出
- engine 加 carrierLocked/EVM（或锁定指示）访问器，真实反映 digital_demod Costas 状态；
- UI（状态面板/遥测）与三通道（ControlHub/HTTP/Agent get_status）透出；无信号/未锁定诚实状态（不伪造锁定）；补测试。

## 块 3（C++ 域）：多普勒补偿三通道桥接
- 显式跨层把 DopplerStepLimiter/多普勒补偿开关接到 ControlHub/HTTP/Agent（新增工具或 get_status 字段，真实计数），手动门控对齐；
- 无 TLE/无目标诚实禁用；补测试。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- pytest 185/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）；
- 如实报告 file:line、全链软硬对比真实数字、状态透出/桥接计数、未解决项。
