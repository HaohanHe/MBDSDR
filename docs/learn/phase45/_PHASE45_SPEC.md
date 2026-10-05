# 第四十五阶段实现规格：CCSDS 级联补齐 + UI 最后字面量收尾

> 基线：HEAD = 466e370（已推送）。活动 10-08 临近，SSDV 现场 Viterbi/RS 级联是最大技术风险；软件必须具备解级联能力。
> 现状侦察：`mbdsdr_ai/ccsds_rx.py` **已存在** ConvEncoder（:126）、ViterbiDecoder（:158 硬判决 ACS+回溯）、RS(255,223) 注释（:59 复用 fec.ReedSolomon）、ASM/解扰框架；Phase43 完整级联全链未云内跑。compass_dial 仅 mobile/lib/widgets/（桌面 C++ 无同名文件）。
> 干净室：只按公开 CCSDS 标准（131.0-B RS(255,223)、卷积 (2,1,7) 多项式 171/133）重写，不复制 GPL；项目 MIT。

## 块 1+2（A：Python 域）
- **Viterbi**：核实/补齐 ccsds_rx.py ViterbiDecoder（(2,1,7)，标准多项式），合成确定性往返（卷积编码→加噪→硬判决解码断言）；
- **RS(255,223)**：CCSDS 131.0-B（GF(256)，标准域生成多项式，32 校验/纠 16 符号），合成往返（RS 编码→插符号错误→纠错断言，**含 16 符号纠错边界**）；
- **完整级联链**：IQ→BPSK（复用 ssdv_phy.py）→Viterbi→RS→218B 包→ssdv decode→JPEG，云内合成全链确定性测试（全量输出）；
- pytest 全量不回归（实际计数）。

## 块 3（B：compass sky-token）
- compass_dial.dart（mobile）画布几何裸数（标注字号 8.5/8/10.5、dotR、strokeWidth、标签偏移）全具名化：单开 kSky* token 组（AppTokens，触控/4pt 对齐，不改变渲染行为）；
- **桌面 C++ 核实**：cpp 无 compass 同名——若 sky_view/constellation_view 有对应画布裸数则一并具名化，若无则如实记录"compass 为 mobile 独有"，不硬造两端；
- 三态截图复验无回归（mobile 侧可 widget 测试；若动 cpp 则 offscreen 截图）。

## 块 4（活动文档更新）
- event-final-check / runbook 更新级联解码就绪状态（真机参数仍走 docs，禁进代码）。

## 质量门 / 红线
- 干净室只按公开标准重写不复制 GPL；MIT；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2 单目标，如实记录）；测试真实（合成往返确定性，全量输出实际计数）；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试实际通过数、级联全链真实状态、未解决项。
