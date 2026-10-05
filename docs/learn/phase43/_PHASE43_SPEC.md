# 第四十三阶段实现规格：活动解码冲刺

> 基线：HEAD = 8797bfb（已推送），ctest 128 全过+e2e_smoke 诚实 SKIP、flutter 362、pytest 145。
> 背景：10-08/09/10 活动临近；SSTV Robot72 已闭环（real_sstv.wav 239/240 行）、Robot36 两行组是遗留；SSDV DSLWP 方言已在 mbdsdr_ai/ssdv_decoder.py（218B/9头/CRC32 魔数 0x4EE4FDE1 大端，pytest 26 passed）但物理层未串一条命令（onboard --mode ssdv 只吃解调后包字节流）；活动出图全链走 Python 端（C++ YAGNI 不变）。
> 文件位置：onboard.py = tools/onboarding/onboard.py；ssdv_decoder = mbdsdr_ai/ssdv_decoder.py；sstv 解码 = mbdsdr_ai/（sstv_decoder.py / decoders.py:531 decode_sstv）。

## 块 1+2（A：Python 域）
- **SSTV**：mbdsdr_ai/sstv 模块补 Robot36（120×240 两行组）解码 + 自动制式识别（Robot36/72 特征区分：同步头/行数/尺寸）；合成信号 + real_sstv.wav 离线测试；诚实标注哪些制式无测试样本；
- **SSDV 物理层串一条命令**：onboard.py `--mode ssdv` 链路打通——录 IQ（rtl_sdr）→ 解调（FSK/BPSK 带内下变频）→ 产包字节流 → ssdv decode → JPEG；至少云内合成信号全链测试（确定性）；真机频率/符号率参数走 docs 活动参数（禁进代码）；
- **pytest 全量 145 不回归**（全量输出实际计数）。

## 块 3（B：Flutter 域）
- **扫频增强 Wave2**（mobile radio_scan）：方向切换/暂停恢复/命中停留时长配置（弹性 token，禁裸数）+ 测试；桌面扫频如已具备同能力则说明对齐方式，不硬造。

## 块 4（核对更新）
- event-final-check.md 更新（新能力就绪状态、TLE 重拉时机提醒、真机步骤）。

## 质量门 / 红线
- 活动参数（JAMX01/ASRTU-1 频率等）禁进通用代码只进 docs；不预置 TLE；诚实空态不 mock；无比赛字样；
- pytest 145 不回归（全量输出实际计数）、flutter 362 + analyze 0、ctest 128 不回归（若动 C++）；
- OOM 约束增量构建；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、无测试样本的制式如实标注、未解决项。
