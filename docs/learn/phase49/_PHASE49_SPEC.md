# 第四十九阶段实现规格：实时多普勒补偿引擎

> 基线：HEAD = 088fc9b（已推送）。把"只显值"liveFd 升级为可选自动微调闭环（卫星 BPSK 通用能力，非活动专用）。
> 现状侦察：桌面**已有 `core::DopplerStepLimiter`**（main_window.h:304 dopplerLimiter_；advance(target)/disarm/reset(target)；1Hz 循环 main_window.cpp:5028 调 advance；kDopplerMaxStepHz=2000 tokens.h:435）；dopplerCompChk_ 开关（:1207 default off）；vfoSetOffset（engine）。本轮=核实补齐安全闭环工程化。
> 职责边界：TLE 预测补偿大尺度多普勒（慢、可预测）；盲 CFO 平方环收残余频偏（快）；Gardner 管符号定时——三者不重叠。

## 块 1+2+桌面UI（A：cpp 域）
- **引擎核心核实/补齐**（DopplerStepLimiter，纯函数可测）：
  - 每 tick 最大步进限幅（复用 kDopplerMaxStepHz，确认 advance 限幅正确）；
  - **目标丢失/数据无效 → 冻结保持（不瞎扫）**——核实 disarm 语义：若 disarm 是回零，则补 freeze-hold 行为（保持上一有效 offset）；
  - 一键关 → **平滑回零**（非瞬跳）；
  - 合成确定性测试：模拟一段过境多普勒曲线（含目标丢失/重获/关断），断言**跟踪误差真实数字 + 限幅/冻结/回零行为**；
- **全链协同**：TLE 补偿（大尺度）+ 盲 CFO（残余）+ Gardner（定时）职责边界理清不冲突，桌面链路实测协同；**无 TLE/无目标诚实空态**（开关禁用并说明，不预置 TLE）；
- **桌面 UI 状态文案**：补偿开关显示「补偿中·累计 xxx Hz」（补真实状态纯函数，值来自引擎）。

## 块 3 移动侧（B：Flutter 域）
- 若接线成本小则对称（自动补偿开关 + 状态文案，AppTokens 弹性/触控≥44）；否则**如实标注"只显值"+架构理由**，不硬造。

## 块 4：回归
- ctest 128（动 C++ 全量输出实际计数）；pytest 164/7 skip；flutter 366（动则）不回归。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE（无 TLE 诚实空态）；诚实空态不 mock；
- 8GB OOM（-j2 单目标，如实记录构建方式）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- 如实报告 file:line、跟踪误差真实数字、未解决项。
