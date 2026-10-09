# Phase63 — DSB / RAW / CDCSS 三候选侦察与接口设计（干净室，未落地）

> 状态：**侦察完成 + 接口设计，本轮不落地**。三候选均为真缺口；DSB/RAW 工作量小、CDCSS 偏大，
> 本轮时间预算优先给了双构型验证与门控快照，完整落地排后续轮。本档只登记现状与设计，不写代码。

## 1. 现状核实（HEAD=fa4949c 之后，工作树 = fa4949c + 无新码）

| 候选 | 现状 | 证据 |
|---|---|---|
| **DSB 解调** | 无 DSB 模式 | `demodCombo_` items（main_window.cpp:596）= AM/NFM/WFM/USB/LSB/CW/BPSK/QPSK/ADS-B/…；demod.h 只有 DemodAM/NFM/WFM/SSB 类（demod.h:57-63 起）。demod.h:116 的 "38 kHz stereo DSB" 是 FM 立体声 MPX 子载波（WBFM 内部），**不是解调模式** |
| **RAW 直听** | 无 RAW 模式 | grep kModeRaw/RawMode 零命中；解调链入口按 mode 分发，无 pass-through 分支 |
| **CDCSS/DCS** | 登记未实现 | ctcss.h:20 注释 + tokens.h:363 注释显式声明 "CDCSS/DCS (digital coded squelch, 134.4 bps 3-of-8 code) is NOT implemented"；全仓无 Golay(23,12) 码检测器 |

参考：SDR++ radio/demodulators 有 dsb.h（平衡调制 DSB 解调）与 raw.h（IQ 直通），本项目干净室只学机制概念。

## 2. DSB 解调接口设计（价值：中等——广播 DSB 收听、调试平衡调制器）

- 引擎解调链新增 `DemodDSB`（demod.h 侧，类同 DemodAM 的包络/同步检波结构）：
  - `DemodDSB(double ifSampleRate, double bandwidth)`，`name() = "DSB"`；
  - 与 AM 同链位置（post-channelizer、pre-ANR），同步检波（载波恢复锁相 + 乘积检波）或简化包络（与 AM 共用通路时需文档注明"无载波恢复的 DSB 与 AM 解调等价，仅带宽/AGC 差异"）；
- UI：demodCombo_ 加 "DSB"（main_window.cpp:596 行 + 模式→带宽联动表 kBwComboPresetsHz 与 demod 特殊化 :2396 核对）；
- tokens：kDemodModeDsb 或复用字符串表；持久化键 rx/demodMode 已有（"DSB" 字符串天然兼容）；
- 三通道：set_mode 白名单（agent_tools.cpp:107-113 复用 kControlHubModes）加 "DSB"；control_hub 模式表同步；
- 测试：合成 DSB（调制信号×载波乘积）解调恢复确定性断言；快照 MBD_MODE=DSB。

## 3. RAW 直听接口设计（价值：中高——IQ 直通调试、频谱/瀑布对照、外部分析仪输入）

- 引擎解调链 pass-through 分支：mode=="RAW" 时 post-channelizer 复数 IQ 直通为 L/R 音频（或经 1:1 重采样到 48k）：
  - `DemodRaw(double ifSampleRate)`，`name() = "RAW"`；
  - 输出 = I/Q 交替或 L=实部/R=虚部（需定稿：**建议 L=I、R=Q**，便于立体声监听与示波器对照）；
  - 与 AGC/squelch/CTCSS 门控的关系：RAW 直通不经 FM/AM 音频路径，squelch 用 RMS 域仍适用，CTCSS 门控对 RAW 无意义（无解调音频）——**文档注明 RAW 下 CTCSS 检测保持运行但不门控**；
- UI：demodCombo_ 加 "RAW"；带宽控件隐藏或禁用（RAW 无滤波器语义）；
- 三通道：set_mode 白名单加 "RAW"；set_bandwidth 对 RAW 返回 ok:false（无滤波器可设）或忽略（定稿：**ok:false 诚实拒绝**）；
- 测试：IQ 直通前后逐采样相等（除缩放）；快照 MBD_MODE=RAW。

## 4. CDCSS/DCS 接口设计（价值：高——中继/公众台数字亚音过滤，CTCSS 自然延伸；工作量偏大）

- DSP：`CdcssDecoder`（新文件 cpp/src/dsp/cdcss.{h,cpp}，镜像 CtcssToneDetector 生命周期）：
  - 输入 post-ANR 48k 单音音频；前置带通（~134.4 bps 码率域），过零率/曼彻斯特解码到 3-of-8 码字流；
  - **Golay(23,12) 纠错**：m17_decoder.cpp:130 已有 Golay(24,12) 公开规范实现（gen 0xC75）——同族但不同码长，**可参考其公开规范实现风格，不抄代码**；DCS 码字表（023/025/…/754 共 104 码，3-of-8 恒权码）入 tokens 或 dsp 常量；
  - 判据：连续 N 个匹配码字才 latch（镜像 CTCSS DetectHits=1/Misses=3 的诚实去抖）；
- 引擎：与 ctcss_ 并列成员 cdcss_{}；`setCdcssEnabled/setCdcssCode/setCdcssGateAudio` + 读回 `cdcssPresent()`；gate 决策扩展（:1577）`ctcssOpen` 与 `cdcssOpen` 的**互斥语义**（同频段同时只启用一种亚音域——文档定稿）；
- 三通道：`set_cdcss`（写/gated，enabled + code）+ `get_cdcss_status`（读，enabled/code/active/gate_audio）；mobile catalog 49→51；
- UI：ctcss 区并列 cdcss 开关 + 码字输入（spin 023-754 或编辑框+校验）；badge 语义同 ctcss；
- 测试：合成 134.4bps 3-of-8 码流（匹配/不匹配/噪声）确定性断言；快照扩展。

## 5. 本轮结论

- 三候选均确认**真缺口**、收益排序 CDCSS > RAW > DSB；
- **本轮未落地**（预算已用于 D88/R88 双构型 136/136 + CTCSS 门控三档快照核查）；
- 下一轮建议按序：CDCSS（若专职整轮）或 RAW（半轮可成）；DSB 可与 RAW 同轮（模式枚举同处改）；
- 红线：本档无 competition/比赛 字样；无 mock；GPL 对照仅机制概念（SDR++ dsb.h/raw.h 与 Golay 公开规范），不抄 GPL。
