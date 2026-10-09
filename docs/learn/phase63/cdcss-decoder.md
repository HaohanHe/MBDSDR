# Phase63 — CDCSS/DCS 数字亚音解码器落地（纯 DSP 层 + 单测先行）

> 状态：**解码器本体 + 确定性单测已落地**（HEAD=7e0418e 之上）。
> 本轮只做 DSP 解码器 + 单测闭环；引擎集成（set_cdcss 三通道 / mobile 51 / UI / gate 互斥域）留后续轮。
> 接口设计见 `dsb-raw-cdcss-survey.md §4`（本轮按其 DSP 段落地）。

## 1. 落地清单

| 文件 | 作用 |
|---|---|
| `cpp/src/dsp/cdcss.h` | `class CdcssDecoder` 声明（镜像 `CtcssToneDetector` 生命周期风格） |
| `cpp/src/dsp/cdcss.cpp` | Golay(23,12) 编/解码、DCS 104 码表、相干 I 前端、曼彻斯特配对、去抖 latch |
| `cpp/tests/test_squelch_gate.cpp` | 新增 4 个确定性 CDCSS 单测（该文件是本轮唯一允许修改的测试文件） |
| `docs/learn/phase63/cdcss-decoder.md` | 本档 |
| `cpp/CMakeLists.txt` | 把 `src/dsp/cdcss.cpp` 加进 `mbdsdr_core` 源列表（构建系统必要改动，非业务文件） |

## 2. 机制对照（干净室）

输入：post-ANR 48 kHz 单音 float 音频块（与 CTCSS 同一条音频路径）。

| 段 | 做法 |
|---|---|
| 前端混频 | 固定本振 `kCarrierHz=1500 Hz`，相干 I 解调 `I += x·sin(φ)`，按半位周期积分取符号。**本轮固定载波、无 PLL/载波恢复**——留给引擎集成轮。 |
| 半位采样 | 分数相位累加器 `halfBitPeriod_ = 48000/(2·134.4) ≈ 178.57` 样点/半位，吸收非整数位钟漂移。 |
| 曼彻斯特解码 | 两个连续半位配成一位（约定：bit=1 → 高→低，bit=0 → 低→高；位值 = 第一半位电平）。 |
| 字装配 | 23 位 LSB-first 移位进 `word_`；满 23 位送 `onWordComplete`。 |
| Golay 纠错 | 见 §3。 |
| 去抖 | 连续 `kLatchHits=3` 个匹配字 → latch；连续 `kLatchMisses=5` 个非匹配/不可纠正字 → 释放（镜像 CTCSS DetectHits/Misses 诚实去抖）。常量在 `cdcss.cpp` 内部 `constexpr`，**不碰 tokens.h**。 |

API：`configure(sr,code12) / setEnabled / reset / process(const float*,int) / codePresent() / lastCode() / configuredCode()`。

## 3. Golay(23,12) 干净室自述

- **机制来源**：公开代数规范。二元 Golay 码 G23 是 [23,12,7] 线性分组码，生成多项式
  `g(x) = x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1 = 0xC75`（12 位表示，最高项 x^11）。
- **编码**（`cdcss.cpp:51`）：系统码 = `(data<<11) | ((data<<11) mod g)`。多项式除法在副本 `work` 上做，保留原 `cw`，最后 OR 上 11 位余数。
- **校验子**（`cdcss.cpp:39`）：`rcv mod g`，11 位。
- **纠错表**（`cdcss.cpp:60`）：静态一次性建表，覆盖权重 0..2 的错误图样。(23,12,7) 最小距 7，理论 t=3；本轮只表 ≤2（覆盖单测翻转预算与现场裕度），权重 3 留后续。
- **代码自写**：循环移位 + GF(2) XOR 是通用 LFSR 写法；未抄 `m17_decoder.cpp` 的 Golay(24,12) 表构建代码，只参考其"公开多项式 0xC75 + 综合征查表"的机制概念。GPL 中立，MIT。

## 4. DCS 104 码表

`cdcss.cpp:102` `kDcsCodes[104]`：公开 DCS/DPL 三进制地址表（0023/0025/…/0574 风格 C 前导零八进制字面量）。表来源为公开 DCS 码表文档；**不预置任何电台呼号/设备 ID**。`dcsCodeValid(c)` 线性查表拒绝非表内地址（诚实拒绝，不静默调谐）。

## 5. 单测（`test_squelch_gate.cpp`，offscreen 真实计数）

合成器在测试内联（非产品代码）：测试侧独立重写同一公开多项式 `testGolayEncode`，把 12 位地址 → 23 位 Golay 字 → 曼彻斯特 → 1500 Hz BPSK 键控正弦 + 固定种子 LCG 白噪声。半位网格与解码器分数累加器完全对齐。

| 测试槽 | 验证 |
|---|---|
| `cdcssDetectsConfiguredCode` | 合成 023 流 + 噪声 → latch，`lastCode()==023` |
| `cdcssFalseOnWrongCode` | 合成 025 流、解码器调 023 → `codePresent()` 恒 false；`lastCode()==025`（诚实：确实解出 025，只是不匹配） |
| `cdcssCorrectsBitFlips` | 每字翻转 bit5+bit17 → Golay(23,12) 纠正后仍 latch 023 |
| `cdcssDisabledStaysFalse` | disabled → 恒 false；非表内 `configure(0777)` 被拒绝 |

**真实计数：`Totals: 13 passed, 0 failed`**（9 个原有 squelch/CTCSS/gated-recorder 测试 + 4 个新 CDCSS 测试）。

## 6. 剩余工作（后续轮，本轮不做）

- 引擎集成：`SpectrumEngine` 加 `cdcss_` 成员，`setCdcssEnabled/setCdcssCode/setCdcssGateAudio` + `cdcssPresent()` 读回；gate 决策与 CTCSS 的互斥语义（同频段只开一种亚音域）。
- 三通道：`set_cdcss`（写/gated）+ `get_cdcss_status`（读）；mobile catalog 49→51。
- UI：ctcss 区并列 cdcss 开关 + 码字输入；badge 语义同 ctcss。
- 前端增强：载波恢复 PLL（当前固定 1500 Hz 本振）、真正的带通/限幅、位钟早-晚纠错（当前依赖合成端与解码器分数网格对齐）。
- Golay 表扩展到权重 3（t=3 全纠错半径）。

## 7. 红线

- 无 competition/比赛 字样；MIT；无预置呼号/电台 ID。
- 文件集严格限定：`cpp/src/dsp/cdcss.{h,cpp}`（新）、`cpp/tests/test_squelch_gate.cpp`、`docs/learn/phase63/cdcss-decoder.md`、`cpp/CMakeLists.txt`（仅新增一行源文件）。未碰 demod.*、vfo_manager.*、tokens.h、agent_tools.*、control_hub.*、main_window.*、ui_diag_freeze.cpp、scratch/*。
- 全程 offscreen（QT_QPA_PLATFORM=offscreen），未操控 GUI；无 mock（音频全是合成正弦+噪声，真实信号路径过 Goertzel/相干 I 链）。
- 无 git add/commit/push。
