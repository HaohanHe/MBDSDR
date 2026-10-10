# 气象卫星数字云图机制学习笔记（LRPT / HRPT / LRIT-HRIT，干净室）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 仓库：MBDSDR（MIT）
> 学习源（均为 GPL/AGPL，只读机制，不抄代码）：
>   - `repos/SatDump`（GPLv3，主：LRPT/HRPT/GOES 全链）
>   - `repos/meteor_demod`（GPL，LRPT QPSK/OQPSK 解调侧）
>   - `repos/goestools`（BSD，GOES LRIT/HRIT 解调 + 解链）

---

## 0. 干净室声明（Clean-Room Notice）

上述三个学习源均为开源许可项目（SatDump/meteor_demod 为 GPL，goestools 为 BSD）。
本笔记遵循干净室规则：

- **只读机制，不抄代码**。下列所有 file:line 引用仅作"机制证据"，回答
  "这条数字下传在物理层/链路层做了什么、为什么这么做"。
- 一切转述、参数表、流程描述均为本轮阅读后**自述重写**，不复制 C/C++ 源码
  片段进 MBDSDR（MIT 仓库）。MBDSDR 后续实现须由另一轮在不参考 GPL 源码
  文本的前提下，仅依据本笔记的机制描述自写。
- 笔记中的常量（72 ksym/s、0x1ACFFC1D、RS(255,223)、K=7 r=1/2 等）属于
  **公开协议事实**（CCSDS Blue Book 101.x 与卫星公开下传标准），不构成 GPL
  代码表达；但具体的 C++ 循环、内存布局、命名常量值仍属上游表达，MBDSDR
  不得照搬。
- 与 `docs/learn/noaa_apt_meteor.md`、`docs/learn/satdump.md`、
  `docs/learn/goestools.md`（2026-09-24 早期调研）互补：那三份偏"工具能不能用"，
  本笔记偏"机制怎么跑通 + MBDSDR 怎么自写落地"。

---

## 1. 三种数字下传的全景对比（先给结论）

| 维度 | LRPT（Meteor-M2/2-3） | HRPT（NOAA POES） | LRIT/HRIT（GOES） |
|------|----------------------|-------------------|-------------------|
| 频段 | ~170 MHz VHF | ~170 MHz VHF | ~1.7 GHz L 波段 |
| 调制 | QPSK / OQPSK | BPSK | BPSK |
| 符号率 | 72 ksym/s | 665.4 kbps（公开） | LRIT 293883 / HRIT 927000 sym/s |
| 内码 | CCSDS 卷积 r=1/2 K=7 + Viterbi | **无 FEC** | CCSDS 卷积 r=1/2 K=7 + Viterbi |
| 外码 | RS(255,223) ×4 交织 | **无** | RS(255,223) ×4 交织 |
| 帧同步 | 64-bit（编码后 ASM）相关 | 60-bit 巴克式 ASM | 64-bit（编码后 ASM）相关 |
| 差分 | NRZ-M（可选） | 无 | HRIT 用 NRZ-M；LRIT 用 NRZ-L |
| 数据形态 | CADU 流 → 虚拟信道 → MSU-MR 图像 | 10-bit 字流 → AVHRR 行 | VCDU → xRIT 文件分发 |
| 链路哲学 | CCSDS 全套（抗弱信号） | 模拟时代裸帧（功率换带宽） | CCSDS 全套（静止轨道弱信号） |

**一句话**：LRPT 与 GOES LRIT/HRIT 走的是**同一条 CCSDS 抗噪链**（BPSK/QPSK
→ Viterbi → 解扰 → RS → 帧/包重组），只是调制方式、符号率、上层文件协议不同；
HRPT 是**没有纠错的裸 BPSK 帧**，靠高功率站收，是"模拟时代数字下传"。

---

## 2. LRPT 数字下传机制（Meteor-M2）

### 2.1 解调侧（QPSK/OQPSK）—— 证据：meteor_demod

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `meteor_demod/src/main.c` | 19 | `SYM_RATE = 72000` | LRPT 符号率 72 ksym/s。QPSK 下比特率 = 144 kbps（2 bit/符号）。 |
| `meteor_demod/src/main.c` | 26-30 | RRC alpha=0.6, FIR order=64, interp factor=4 | 根升余弦成形匹配滤波，过采样 4×。alpha=0.6 偏宽，容忍定时抖动。 |
| `meteor_demod/src/demod.c` | 192-200 | Gardner TED：`resync_error=(imag(cur)-imag(before))*imag(mid)` | 基于中间样本的早迟门（Gardner）定时误差检测，NCO 微调符号相位。只用 I/Q 一路的虚部，免乘载波。 |
| `meteor_demod/src/demod.c` | 203-204 | `costas_mix` → `costas_correct_phase` | Costas 环做载波频偏/相位跟踪，每符号迭代一次。 |
| `meteor_demod/src/pll.c` | 117-118 | `err = tanh(I)*Q - tanh(Q)*I`（QPSK/OQPSK） | 面向 QPSK 的切正切误差检测器：象限判向 + 限幅，抗大频偏过载。 |
| `meteor_demod/src/pll.c` | 62-78 | 锁定判据：QPSK `moving_avg(\|err\|)<0.5` 收窄带宽到 1/3 | Costas 失锁时宽带捕获，锁定后 BW/3 精跟踪；误差变大再失锁。二阶环。 |
| `meteor_demod/src/demod.c` | 242-260 | OQPSK 分支：I 路延迟半符号（`prev_i`） | OQPSK 把 Q 路相对 I 路错半符号跳变，避免过原点的相位跳。Meteor-M 实际用 OQPSK。 |
| `meteor_demod/src/demod.c` | 157-158 | 输出 `clamp(cre/sym/2), clamp(cim/sym/2)` 为 int8 | 解调输出是**软 I/Q 符号**（-127..+127），不是硬 bit。这是 Viterbi 软判决的输入。 |

**一句话**：LRPT 解调链 = AGC → RRC 匹配滤波（4×过采样）→ Gardner 定时恢复
→ Costas 载波恢复（tanh 误差检测，锁定收窄带宽）→ 每符号抽一个软 I/Q 样点。

### 2.2 解链侧（Viterbi → 同步 → 解扰 → RS）—— 证据：SatDump

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `SatDump/plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp` | 14-15 | `FRAME_SIZE=1024`, `ENCODED_FRAME_SIZE=1024*8*2=16384` | 一个解码后 CADU 帧 1024 字节；对应卷积编码前 2048 字节软符号窗（r=1/2 → 4096 符号，按 int8 I/Q 交错即 16384 样本）。 |
| `.../module_meteor_lrpt_decoder.cpp` | 201 | `Correlator(QPSK, diff_decode ? 0xfc4ef4fd0cc2df89 : 0xfca2b63db00d9794)` | **QPSK 64-bit 同步字**（在卷积编码后的软 bit 流上相关）。非差分用 0xfca2…；NRZ-M 差分支用 0xfc4e…。 |
| `SatDump/src-core/common/codings/correlator.cpp` | 54-63 | QPSK 生成 8 个候选同步字（4 相位 × I/Q swap） | QPSK 有 4 重相位模糊 + I/Q 交换模糊，相关器一次试 8 个，命中即解模糊。 |
| `SatDump/src-core/common/codings/correlator.cpp` | 140-149 | 硬判决后 64-bit 滑窗，`corr>45`（64 位容 19 错）即同步 | 相关阈值 45/64（容忍 ~19 bit 错），这是弱信号门限的来源之一。 |
| `.../module_meteor_lrpt_decoder.cpp` | 232 | `rotate_soft(buffer, len, phase, swap)` | 根据相关器报出的 phase/swap，把软 I/Q 旋转到正确象限。 |
| `.../module_meteor_lrpt_decoder.cpp` | 46 | `Viterbi27(..., CCSDS_R2_K7_POLYS)` | K=7、r=1/2 卷积内码 Viterbi 软判决解码。 |
| `SatDump/src-core/common/codings/viterbi/viterbi27.h` | 8 | `CCSDS_R2_K7_POLYS = {79, 109}` | 八进制即 {117, 155}，CCSDS 标准生成多项式 g1=1+x^3+x^4+x^5+x^6, g2=1+x+x^2+x^3+x^6。 |
| `.../module_meteor_lrpt_decoder.cpp` | 238 | `diff.decode(frameBuffer, FRAME_SIZE)` | NRZ-M 差分解码（可选）：相邻 bit 异或，解相位极性模糊。 |
| `SatDump/src-core/common/codings/differential/nrzm.cpp` | 13-22 | `mask=(data>>1&0x7F)\|(lastBit<<7); data^=mask` | 字节级 NRZ-M：本字节右移 7 位 + 上一字节最低位拼出掩码，异或还原。 |
| `.../module_meteor_lrpt_decoder.cpp` | 241 | `derand_ccsds(&frameBuffer[4], FRAME_SIZE-4)` | CCSDS 解扰：跳过前 4 字节同步字，对 1020 字节 PN 异或。 |
| `.../module_meteor_lrpt_decoder.cpp` | 251 | `rs.decode_interlaved(&frameBuffer[4], false, 4, errors)` | **RS 外码，4 路交织**：1020 字节 = 4×255 字节 RS(255,223) 块。 |
| `SatDump/src-core/common/codings/reedsolomon/reedsolomon.cpp` | 34-36 | `correct_rs_primitive_polynomial_ccsds, first_root=112, index=11, roots=32` | CCSDS RS(255,223)：223 信息字节 + 32 校验字节，t=16 字节纠错。本原多项式 CCSDS 规定，首根 α^112。 |
| `.../reedsolomon.cpp` | 145-155 | `deinterleave: out[ii]=data[ii*i+pos]` | 4 路块交织：每隔 4 字节取一字节组成一个 RS 块（抗突发误码）。 |
| `.../module_meteor_lrpt_decoder.cpp` | 254-258 | 4 个 RS 块全部 `errors>=0` 才写出，补 `sync={0x1d,0xcf,0xfc,0x1d}` | **帧门限**：4 个 RS 块都可纠正才算有效 CADU，否则整帧丢弃。输出 CADU 同步字 1ACFFC1D。 |

**一句话**：LRPT 解链 = QPSK 软 I/Q → 64-bit 编码后同步（8 重相位模糊搜索）→
Viterbi K7 r1/2 软判决 → （可选 NRZ-M）→ CCSDS 解扰 → 4 路交织 RS(255,223) →
有效才输出 1024 字节 CADU。

### 2.3 CADU → 虚拟信道 → 图像包重组（上层）

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `.../module_meteor_lrpt_decoder.cpp` | 256 | 输出 `sync={0x1d,0xcf,0xfc,0x1d}` + 1020 字节 | CADU = 4 字节 ASM(1ACFFC1D) + 1020 字节数据。这就是 CCSDS CADU。 |
| `SatDump/plugins/meteor_support/meteor/instruments/msumr/lrpt_msumr_reader.cpp` | —（上层） | CADU 流按虚拟信道 ID 拆分，重组 MSU-MR 图像段 | CADU 的 1020 字节里含 VCDU 头（虚拟信道 ID），不同 VC 承载不同仪器/数据；图像段按段号拼回行。本轮未逐行读码，标注为"上层包重组"，落地时再补。 |

> **诚实边界**：本轮把深度读码放在物理层/链路层（解调→Viterbi→RS）。
> CADU 之后的 VCDU 解复用、MSU-MR 段重组、Huffman/IDCT 解压属于"图像格式层"，
> 与 MBDSDR 现有 `image_enhance.py` 的接合点见 §6，落地路径里列为第 3 轮以后。

---

## 3. HRPT 机制（NOAA POES，模拟时代数字下传）

### 3.1 解调与帧结构 —— 证据：SatDump noaa_metop_support

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `SatDump/plugins/noaa_metop_support/noaa/module_noaa_hrpt_decoder.cpp` | 29-31 | 上游已给 int8 软符号，喂 `NOAADeframer::work` | HRPT 模块本身**不做 BPSK 解调**——BPSK Costas 解调在上游 DSP 源块完成，这里只做位同步 + 成帧。 |
| `.../module_noaa_hrpt_decoder.cpp` | 49 | `frame_count / 11090` | 每 11090 个 10-bit 字 = 一个 minor frame。 |
| `SatDump/plugins/noaa_metop_support/noaa/noaa_deframer.cpp` | 13 | `HRPT_MINOR_FRAME_SYNC = 0x0A116FD719D83C95` | **60-bit 行同步字**（不是 CCSDS 的 32-bit ASM）。 |
| `.../noaa_deframer.cpp` | 6-11 | `HRPT_SYNC1..6 = 0x0284,0x016F,0x035C,0x019D,0x020F,0x0095` | 同步字拆成 6 个 10-bit 字，作为行首写入输出流。 |
| `.../noaa_deframer.cpp` | 16-17 | `HRPT_MINOR_FRAME_WORDS=11090`, `HRPT_BITS_PER_WORD=10` | minor frame = 11090 个 10-bit 字 = 110900 bit/行。 |
| `.../noaa_deframer.cpp` | 71-97 | IDLE 态滑窗找 60-bit 同步（容阈值误差），命中进 SYNCED | 状态机：IDLE（找同步）→ SYNCED（按 10-bit 字切数据）→ 满一帧回 IDLE。 |
| `.../noaa_deframer.cpp` | 86 | 同时试同步字取反（`^0x0FFF…`） | BPSK 180° 相位模糊：同步字或其反码任一命中即可。 |
| `.../noaa_deframer.cpp` | 99-110 | SYNCED 态逐 bit 拼 10-bit 字，存满即输出 | **无 Viterbi、无 RS、无解扰**——纯裸帧。误码直接进图像行。 |

**一句话**：HRPT = BPSK 软符号（上游解调）→ 60-bit 同步相关 → 按 10-bit 字切出
11090 字/行，**全程无纠错**。这是它与 LRPT/GOES 最本质的区别：靠高发射功率和
高增益站弥补没有 FEC 的弱点。

> **对任务描述的诚实校正**：任务写"11090 bps 域"。源码事实是
> `HRPT_MINOR_FRAME_WORDS=11090`（10-bit 字/行），不是 11090 bps；
> NOAA HRPT 标称比特率 665.4 kbps 为公开参数。落地时以"11090 字/行"为准。

---

## 4. LRIT / HRIT 机制（GOES 静止轨道）

### 4.1 解调侧（BPSK）—— 证据：goestools

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `goestools/src/goesrecv/demodulator.cc` | 13,16 | LRIT `symbolRate_=293883`, HRIT `=927000` | LRIT 293.883 ksym/s，HRIT 927 ksym/s。BPSK 即 1 bit/符号。 |
| `.../demodulator.cc` | 95-99 | 流水线：Source→AGC→Costas→RRC→ClockRecovery→Quantize→软 bit | 与 LRPT 解调链同构：AGC、Costas、RRC、定时恢复、量化出软 bit。 |
| `goestools/src/goesrecv/costas.cc` | 124-125 | `err = 0.5*(\|I*Q+1\| - \|I*Q-1\|)` | **BPSK Costas 误差检测器**：I·Q 乘积限幅（BPSK 专用，比 QPSK 简单）。 |
| `.../costas.cc` | 18-19 | 二阶环 `alpha=4ζB/(…)`, `beta=4B²/(…)`，ζ=√2/2, B=0.005 | 标准二阶 PLL 系数（阻尼 0.707，归一化带宽 0.005 rad/sample）。 |

### 4.2 解链侧 —— 证据：goestools decoder/

| 文件 | 行 | 机制 | 自述 |
|------|----|------|------|
| `goestools/src/decoder/compute_sync_words.cc` | 37-40 | `syncWord = {0x1A,0xCF,0xFC,0x1D}` | **底层帧同步字就是 CCSDS ASM 0x1ACFFC1D**。这是公开 CCSDS 事实。 |
| `.../compute_sync_words.cc` | 46-63 | LRIT：直接对 ASM 做 Viterbi 编码得 64-bit 同步；取反得 180° 对偶 | LRIT 用 NRZ-L（无差分），相关器同步字 = Viterbi(0x1ACFFC1D)。 |
| `.../compute_sync_words.cc` | 65-86 | HRIT：先 NRZ-M 编码 ASM（两种初值）再 Viterbi 编码 | HRIT 用 NRZ-M，同步字多两种初值变体。 |
| `goestools/src/decoder/correlator.cc` | 10-17 | 4 个同步字：LRIT 0°/180° = 0x035d49c2…/0xfca2b63d…；HRIT 0°/180° = 0x03b10b02…/0xdafef4fd… | 相关器**同时**试 LRIT 与 HRIT 两种流，命中哪个就是哪个流（无需运行时开关）。 |
| `.../correlator.cc` | 56 | `v = 64 - popcount(tmp ^ syncword)` | 64-bit 汉明相关（popcount 数不同位），取最大。 |
| `goestools/src/decoder/viterbi.h` | 27 | `poly[2] = {0x4f, 0x6d}` | = {79,109}，与 SatDump CCSDS_R2_K7_POLYS 完全一致。**交叉证实** CCSDS K7 r1/2。 |
| `goestools/src/decoder/packetizer.cc` | 131 | `viterbi_.decodeSoft(buf, bits, packet)` | 软 bit 流喂 Viterbi，输出硬字节包。 |
| `.../packetizer.cc` | 149-153 | LRIT 180° 命中 → 整包 `^=0xff` | Viterbi 对取反信号也能解（输出取反），事后整包翻转回正。 |
| `.../packetizer.cc` | 157-168 | HRIT 命中 → NRZ-M 差分解码：`in[i]=o[i+1]^o[i]` | HRIT 特有的差分解。 |
| `goestools/src/decoder/derandomizer.cc` | 13-30 | PN 多项式 `x^8+x^7+x^5+x^3+1`，LFSR 初值 0xff，生成 1020 字节表 | CCSDS 标准加扰序列，对解扰后 1020 字节异或。 |
| `goestools/src/decoder/reed_solomon.cc` | 47-51 | `correct_rs_primitive_polynomial_ccsds, first_root=112, index=11, roots=32` | 与 SatDump RS223 **完全一致**：RS(255,223)，4 路交织（`len==1020`）。 |
| `.../reed_solomon.cc` | 11-20 | 对偶基变换 tal[] 矩阵（CCSDS 101.0-B-6 Annex A） | CCSDS RS 用对偶基表示，解码前/后做基变换（与 SatDump ToDualBasis 同机制）。 |
| `.../packetizer.cc` | 182,191 | RS 纠错返回 ≥0 才算 `lock_=true` | **帧锁定判据 = RS 可纠正**。这是比"相关峰"更严格的门限。 |

**一句话**：GOES LRIT/HRIT 解链 = BPSK 软 bit → 64-bit 编码后同步（4 候选：
LRIT/HRIT × 0°/180°）→ Viterbi K7 r1/2 → LRIT 取反/HRIT NRZ-M → 解扰 →
4 路交织 RS(255,223) → RS 可纠正才锁帧。与 LRPT 物理层**同宗同源**。

### 4.3 文件分发协议（xRIT）—— 了解级

| 文件 | 位置 | 机制 | 自述 |
|------|------|------|------|
| `SatDump/plugins/xrit_support/xrit/transport/xrit_demux.cpp` | — | VCDU 按 VC ID 解复用 | 上层把 CADU/VCDU 流拆成虚拟信道。 |
| `SatDump/plugins/xrit_support/xrit/xrit_file.cpp` | — | xRIT 帧头 + 分段文件重组 | LRIT/HRIT 把图像/产品切成 xRIT 文件段，带文件头（产品类型、投影、段号），接收端按段号拼回完整产品。 |

> **诚实边界**：xRIT 文件头格式、WT 压缩（JPEG 小波）属于"应用层"，本轮只确认
> "有文件分发协议"这一事实，未逐字段读码。落地路径里文件重组列为后期轮。

---

## 5. 与现有 APT 模拟链路的差距分析

### 5.1 现状（MBDSDR 已有）

- `mbdsdr_ai/noaa_apt_lite.py`：**模拟 APT 链路**。AM 包络解调（两采样鉴别器）
  → 低通 → 4160 Hz → 行同步（38 样本 guard 方波互相关）→ 切 A/B 两通道
  909 像素/行。全程模拟/裸帧，无 FEC。
- `mbdsdr_ai/image_enhance.py`：**纯 numpy 图像增强后处理**（auto_stretch /
  brightness_contrast / histogram_equalize / LUT），输入是 2D 灰度 ndarray，
  输出增强图。它**不做解调**，是"拿到图像之后"的管线。

### 5.2 差距

| 能力 | APT（已有） | 数字 LRPT/HRPT/LRIT（缺口） |
|------|------------|----------------------------|
| 前端解调 | AM 包络（模拟） | QPSK/OQPSK/BPSK Costas + Gardner |
| 位/帧同步 | 行同步方波（模拟） | 64/60-bit 相关 + 相位模糊搜索 |
| 纠错 | 无（靠模拟信号质量） | Viterbi K7 + RS(255,223)×4 |
| 数据形态 | 直接像素行 | CADU→VCDU→包重组→图像段 |
| 弱信号门限 | ~SNR 0 dB 量级 | LRPT/GOES 可达 -5..-10 dB（FEC 增益） |

### 5.3 关系：互补，不替代

- **数字模式与 APT 互补不替代**：APT 是模拟广覆盖、设备最简单（一根 RTL-SDR
  即可），数字 LRPT 质量高但要稍好天线；HRPT 要高增益。MBDSDR 三条都做。
- **接合点**：数字解码最终也产出 2D 灰度/彩色 ndarray，**直接喂进
  `image_enhance.py` 的 `enhance_apt()`** 做统一后处理（auto_stretch + LUT）。
  这是 `image_enhance.py` 设计为"纯函数、吃 ndarray"的红利——APT 和数字云图
  共用同一套增强。

---

## 6. 诚实边界：RS / 卷积码干净室自写路径

- **卷积内码（K7 r1/2）**：生成多项式 {79,109} 是 CCSDS 公开事实。干净室自写
  路径：按公开 trellis（64 状态）自写 Viterbi ACS（add-compare-select）+ 回溯，
  不抄 volk_k7 表。参数证据：`viterbi27.h:8` / `viterbi.h:27`。
- **RS(255,223)**：本原多项式、首根 112、index 11、32 校验字节是 CCSDS 公开
  事实。干净室自写路径：用 GF(256)（CCSDS 本原多项式 x^8+x^7+x^2+x+1）自写
  Berlekamp-Massey + Chien 搜索，或引入 MIT/Apache 许可的小型 RS 库（如
  `libcorrect` 是 MIT——见 reedsolomon.cpp:6 引用，可评估直接用 MIT 库而非自写）。
  对偶基变换是 CCSDS 规定的线性变换，可按 tal 矩阵自述重写。
- **PN 解扰**：LFSR x^8+x^7+x^5+x^3+1 初值 0xff，1020 字节表，按公开 LFSR
  反馈式自写 20 行代码即可，无专利风险。
- **不抄**：上游 C++ 的整段循环、内存布局、错误处理结构不进 MBDSDR。

---

## 7. 关键参数速查表

| 参数 | LRPT(Meteor-M) | HRPT(NOAA) | LRIT/HRIT(GOES) | 证据 |
|------|----------------|-----------|-----------------|------|
| 调制 | QPSK/OQPSK | BPSK | BPSK | demod.c:67-70; noaa_deframer; costas.cc:124 |
| 符号率 | 72 ksym/s | 665.4 kbps(公开) | LRIT 293883 / HRIT 927000 | main.c:19; demodulator.cc:13,16 |
| RRC alpha | 0.6 | — | （RRC 同） | main.c:26 |
| 内码 | 卷积 r=1/2 K=7 | 无 | 卷积 r=1/2 K=7 | viterbi27.h:8; viterbi.h:27 |
| 生成多项式 | {79,109} | — | {0x4f,0x6d}={79,109} | 同上（交叉一致） |
| 外码 | RS(255,223)×4 | 无 | RS(255,223)×4 | reedsolomon.cpp:34; reed_solomon.cc:47 |
| RS 首根/index/校验 | 112/11/32 | — | 112/11/32 | 同上 |
| 帧同步字 | 0x1ACFFC1D（编码后 64-bit） | 0x0A116FD719D83C95（60-bit） | 0x1ACFFC1D（编码后 64-bit） | lrpt_decoder:256; noaa_deframer:13; compute_sync_words:37 |
| 差分 | NRZ-M 可选 | 无 | HRIT NRZ-M / LRIT NRZ-L | nrzm.cpp:13; packetizer:157 |
| 解扰 PN | x^8+x^7+x^5+x^3+1 init 0xff | 无 | 同左 | derandomizer.cc:13-30 |
| CADU/帧长 | 1024 B（4 ASM+1020 数据） | 11090×10bit 字/行 | 1020 B/块 | lrpt_decoder:14; noaa_deframer:16 |
| 锁帧判据 | RS 4 块全可纠 | 60-bit 同步命中 | RS 可纠 | lrpt_decoder:254; packetizer:191 |
