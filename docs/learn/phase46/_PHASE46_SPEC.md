# 第四十六阶段实现规格：活动接收链路最后收口

> 基线：HEAD = 84acb25（已推送）。10-08 活动前，解决 Phase45 两个"最后一公里"，让现场一条命令应对带/不带级联 SSDV。
> 遗留：①onboard.py CLI 只走 fsphil 自同步方言，缺 `--ssdv-mode ccsds`（级联全链已在 test_phase45_cascade.py 跑通，BER 1.66% 全图恢复）；②demod_bpsk 眼图扫描是粗定时，高噪/弱信号下整比特滑移，需 Gardner TED 或 M&M 精定时闭环。
> 文件：onboard.py = tools/onboarding/onboard.py；物理层 = mbdsdr_ai/ssdv_phy.py（demod_bpsk）；级联 = mbdsdr_ai/ccsds_rx.py（Viterbi/RS/ASM/解扰）；SSDV = mbdsdr_ai/ssdv_decoder.py。
> 干净室：Gardner/M&M 为公开标准算法，按标准重写不复制 GPL；MIT。

## 块 1：onboard `--ssdv-mode ccsds`
- CLI 增模式参数（fsphil 默认 / ccsds 级联）；ccsds 路径串：IQ → demod_bpsk（精定时）→ ASM 同步 → Viterbi（终态0）→ 解扰 → RS → 218B → JPEG；
- 云内合成级联 IQ **全链一条命令实测出图**（确定性、全量输出）；fsphil 路径不回归。

## 块 2：精符号定时
- ssdv_phy.py demod_bpsk 加 **Gardner TED（或 M&M）插值定时恢复闭环**；合成测试（定时相位偏移/小频偏下收敛断言），对比改进前后滑移率；
- **诚实标注收敛失败场景**（极低 SNR 给 FAIL 不伪造）。

## 块 3：活动 runbook 更新
- 现场两路径（带级联/不带）操作步骤最终版 + 判据（看哪一步输出判断走哪条）；真机参数仍走 docs 禁进代码。

## 块 4：回归
- pytest 167 不回归（全量输出实际计数）；flutter 366 不回归（若动）；ctest 128 不回归（若动 C++）。

## 质量门 / 红线
- 干净室按公开标准重写不复制 GPL；MIT；无比赛字样；活动参数禁入；不预置 TLE；诚实空态；
- 8GB OOM（-j2，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- 如实报告 file:line、测试实际通过数（全量输出）、全链真实状态、未解决项。
