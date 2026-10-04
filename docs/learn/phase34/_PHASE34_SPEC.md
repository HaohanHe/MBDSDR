# 第三十四阶段实现规格：真机链路端到端就绪

> 基线：HEAD = 4ad1814（已推送），ctest 124/124、flutter 342、pytest 全绿。
>
> 勘察：tools/acceptance_run.sh 已有 phase11 P1 版（selfcheck→diag_wizard→onboard adsb/apt/cw→exp_ota_run，exit 0/1/2/3，禁 mock 红线）；onboard.py 已含 sstv(:93)/ssdv(:100) 模式；活动参数只存 docs/learn/phase14/P3-event-params.md；云端无硬件。

## 1. acceptance_run.sh --event（tools/ 域）
- --event/--event-modes/--freq-sstv/--freq-ssdv 参数；模式白名单 {sstv,ssdv} 校验；缺频率→stderr 指向 P3-event-params.md + exit 3；坏模式 exit 3；
- 串联 selfcheck→diag_wizard --paste→onboard（sstv/ssdv，频率用户传值绝不硬编码）→exp_ota_run；每步 PASS/FAIL 明确；
- 无硬件诚实空态：device_present 只读 selfcheck target_hits；无设备→诚实 SKIP+原因+exit 2；
- acceptance_lib.py 加 resolve_event_modes 纯函数 + CLI；test_acceptance_run.py +14 测试；pytest 42/42 全绿。

## 2. 接收端结论（docs/learn/phase34/recv-chain-conclusion.md）
- 结论：活动出图全链走 Python（onboard.py + mbdsdr_ai：sstv_decoder Robot72 320×240 / SsdvDecoder auto 方言含 DSLWP 218B 魔数 CRC）；C++ 端只做外围（ControlHub/recording_library/时空视图），SSTV/SSDV 整图解码明确 YAGNI（移植 55-80 人时>窗口、无 DSP 引擎、C++ ssdv_packet=SP5WWP 与 ASRTU-1 DSLWP 不匹配），不双写。

## 3. 真机手册收口（event-rx-runbook.md）+ 活动前核对清单（event-checklist.md）
- 过境预测命令、出图判断标准（SSTV rows_decoded 接近全帧≈239/240 / SSDV JPEG 可开 FFD8…FFD9 且 missing_mcus==[]）、邮件核对项；
- 清单 A 设备/B 天线/C 时间/D 预测频率/E 链路预演/F 邮件模板/G 待真机确认项；未验证部分如实标注【待真机确认】。

## 质量门 / 红线
- 活动参数禁入通用代码（grep 435.075/436.210/JAMX/ASRTU 零命中）；无 mock（无硬件诚实空态）；无比赛字样；防虚构；
- 不回归：ctest 124/124、flutter 342、pytest 42/42 全量输出；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、未解决项。
