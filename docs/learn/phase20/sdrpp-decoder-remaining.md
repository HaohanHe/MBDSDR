# Phase20 步骤一+二 — SDR++ 剩余 5 个 decoder 精读笔记（atv / dab / falcon9 / kg_sstv / ryfi）

> 范围：`repos/sdrpp/decoder_modules/{atv_decoder,dab_decoder,falcon9_decoder,kg_sstv_decoder,ryfi_decoder}/src` 全部源码逐文件精读。
>
> **版权声明**：SDR++ 为 GPLv3。下列引用仅为机制学习的注释性短片段（≤3 行，锚定 `file:line`），**不复制实现代码进 MBDSDR**；MBDSDR 落地走干净室（自有命名/结构，MIT）。
>
> **与 phase16 的关系**：`docs/learn/phase16/sdrpp-special-decoders.md` 已精读 m17/pager/vor + dab **前端概览**。本笔记：
> - dab 重新深读（phase16 只到 `CyclicSync/FrameFreqSync` 概览，本轮补全 `main.cpp` 链末端、FFO 控制环、DEBUG 自相关诊断、符号计数重置条件等细节，并对照 MBDSDR 已有的 `mbdsdr_ai/dab_plus_lite.py` 元数据层做差距判定）。
> - atv/falcon9/kg_sstv/ryfi 是 phase16 明确**未读**的 4 个，本轮首读。
>
> **一句话结论**：5 个模块里 **dab 是最有公共标准价值的，但 SDR++ 自己只做到 OFDM 同步前端（未 FFT 解数据符号、未解交织、未 Viterbi、未出 ETI）**；MBDSDR 反而已经把"基带后"的 FIC/ETI/业务表解析层用 Python 干净室写完了。其余 4 个：atv 是模拟快扫电视（色道全注释掉，只出灰度），falcon9 是 SpaceX 私有遥测（事件触发+大口径天线），ryfi 是 SDR++ 作者自家 QPSK 私有数据链，kg_sstv 是 4FSK 数字 SSTV 变体（SDR++ 自己只 dump 二进制不画图）。**5 个全部 YAGNI 本轮**，但理由和回补条件各不相同。

---

## 0. 与 phase16 笔记的去重对照

| 主题 | phase16 已写 | 本轮新增/重写 |
|---|---|---|
| SDR++ 通用五段式骨架（VFO→同步→解调→解码→出口） | `phase16/sdrpp-special-decoders.md` §1.0, §2 已完整归纳 | **不重复**，下文只在每模块四要素里引用 |
| MBDSDR pull 式对照（vfo_manager/fsk_demod/digital_demod/adsb/apt/cw） | phase16 §3 已列表 | 本轮补 `mbdsdr_ai/dab_plus_lite.py`、`mbdsdr_ai/sstv_decoder.py`、`cpp/src/dsp/sstv_vis.h` 三个新对照点 |
| DAB CyclicSync CP 自相关 | phase16 §1.4 已引 `dab_dsp.h:58-104` | 本轮补：递归滑窗 `corr -= *slot; ...; corr += prod` 的实现细节（`dab_dsp.h:66-76`）、符号长=2048 样点（`main.cpp:50` + `dab_dsp.h:18-19`）、平均电平自适应门限 `avgCorr`（`dab_dsp.h:109`） |
| DAB FrameFreqSync | phase16 §1.4 已概览 | 本轮补：NCO rotator 在每符号开头先转（`dab_dsp.h:182-188`）、**空窗检测 = 符号平均电平掉到 avgLvl 的 0.5 倍就重置 sym=1**（`dab_dsp.h:198-208`）、相位参考符号上才做 FFT 找峰（`dab_dsp.h:214-255`）、整数+小数频偏公式（`dab_dsp.h:247-250`）、控制环增益 0.1（`dab_dsp.h:253`）、DEBUG 用相邻子载波差分自相关出星座（`dab_dsp.h:219-227`） |
| DAB 半成品结论 | phase16 §1.4 已说"类名误抄 M17DecoderModule、写 sync4.f32、只到星座图" | 本轮确认：`main.cpp:38` 的 ofstream 还在但 `main.cpp:124` 写盘已注释；链末端 `ns` handler 只 memcpy 1024 复样本到星座图（`main.cpp:122-129`）；**没有任何 FFT 解数据符号、解交织、Viterbi、FIC/MSC 解析** |

---

## 1. ATV — `atv_decoder/src/`（NTSC 模拟快扫电视，灰度半成品）

### 1.1 机制（file:line）

- **基带链**：`main.cpp:43-50`：VFO 7 MHz @ 采样率 `SAMPLE_RATE = 625 * LINE_SIZE * 25`（`main.cpp:36`，代入 `LINE_SIZE=945` = `linesync.h:8` → **14.765625 Msps**）→ `FastAGC` → `Amplitude` 包络检波 → `LineSync` 行同步 → `Handler` sink 逐行回调。
- **Amplitude 检波**：`amplitude.h:39-43` 就是 `volk_32fc_magnitude_32f` 后乘 **-1**（让同步头朝下为负，方便门限判断）。不是真 AM 解调，是"取模取负"。
- **行同步 PLL**（`linesync.h:91-220`）：polyphase 内插器（128 相 × 8 抽头，`linesync.h:49,230-235`）+ NCO 累加器（`phase += period; offset += phase>>30`，`linesync.h:117-119`）；每凑满 945 样点一行，用"同步头左右两侧平均之差"当误差（`linesync.h:125-145`，`error = (left - right)/SYNC_HALF_LEN`），PI 式调 period+phase；period 钳在标称 ±0.01%（`linesync.h:62-63,159`）；lock 计数 0..1000（`linesync.h:28,191-196`），失锁时 `fastLock` 直接把谷值位置拽到 `SYNC_R_START`（`linesync.h:200-203`）。
- **场同步**（`main.cpp:165-280`）：每行行首判定 shortSync/longSync（前均衡脉冲 vs 场同步脉冲），移位寄存器 `syncHistory<<2 | bits`（`main.cpp:169`）攒 16 位；匹配到 `0b0101011010010101` = 奇场、`0b0001011010100101` = 偶场（`main.cpp:242-243`），vlock 一致+1/不一致-1（`main.cpp:248-254`），偶场结束 swap 图像 buffer（`main.cpp:275`）。
- **亮度闭环**（`main.cpp:144-162`）：测前均衡平均=syncLevel、水平消隐平均=blankLevel，用 blankLevel 当误差调 offset（消隐电平平到 0）、用 `blankLevel - syncLevel + SYNC_LEVEL` 调 gain（同步头压到 `SYNC_LEVEL=-0.428`，`linesync.h:23`）。
- **色通道**：`main.cpp:171-217` 一整段 NTSC 色副载波带通（`filters.h:4` 的 123-tap 复数 FIR `CHROMA_BANDPASS`）+ burst 锁相 + 色差旋转 **全部被注释掉**；`main.cpp:222-228` 的 colorMode 渲染分支也注释掉；实际渲染走 `main.cpp:229-234` 的纯灰度（R=G=B=亮度采样×255）。

### 1.2 信号参数

- 带宽 ~7 MHz（VFO 43 行），FM/AM 调电视，行频 15.625 kHz 派生（LINE_SIZE=945 样点/行 @ 14.765625 Msps）。
- 同步头 -0.428（归一化包络），色副载波 NTSC 3.58 MHz 表已写好但未用。
- 输出 768×576 灰度图（`main.cpp:40,220-234`）。

### 1.3 处理链

```
VFO(7MHz) → FastAGC → -|z| 包络 → polyphase 行同步 PLL(Gardner 类) → 逐行回调
   → 测 sync/blank 电平 → 调 offset/gain → 16位移位寄存器匹配奇/偶场 pattern
   → 写灰度行 buffer → 偶场 swap → ImGui ImageDisplay
```

### 1.4 可复用点（与 MBDSDR 差距）

- **"光栅行定时 PLL"** 机制 MBDSDR 已在 `cpp/src/dsp/apt_decoder.h:114-132` 用滑动归一化互相关锁行 + 分数像素累加器实现过同一件事，**不缺**。
- **"场同步用 N 位移位寄存器匹配长 pattern"** 比 m17 的单同步字 memcmp 更鲁棒，但 MBDSDR 的 ADSB/APT 前导检测已覆盖。
- **可复用的增量**：几乎为零。色道全注释 = SDR++ 自己都没做完；MBDSDR 也没有 7 MHz 宽带前端（MBDSDR-Mini 带宽 ~2 MHz）。

---

## 2. DAB — `dab_decoder/src/`（OFDM 同步前端，最有公共标准价值但 SDR++ 半成品）

### 2.1 机制（file:line，重点深读）

#### 2.1.1 链骨架（`main.cpp:30-60`）

```
INPUT_SAMPLE_RATE = 2.048e6, VFO_BANDWIDTH = 1.6e6   (main.cpp:30-31)
VFO(1.6MHz @ 2.048Msps) → CyclicSync → FrameFreqSync → Handler → 星座图
```

- 类名仍叫 `M17DecoderModule`（`main.cpp:33`，phase16 已指出的误抄未改）。
- `main.cpp:38` 还 `std::ofstream("sync4.f32")`，但 `main.cpp:124` 写盘语句已注释——只是调试残留。
- `main.cpp:122-129` handler 只 memcpy 1024 复样本到星座图：**链末端就是调试星座，没有任何业务解码**。

#### 2.1.2 CyclicSync — OFDM 符号定时（`dab_dsp.h:8-140`）

- 参数：`main.cpp:50` 传 `symbolLength=1e-3 s, cyclicPrefixLength=246e-6 s`；`dab_dsp.h:18-19` 算得 `symbolSamps = round(2.048e6 * 1e-3) = 2048`，`prefixSamps = round(2.048e6 * 246e-6) = 504`。这正好是 DAB Mode I 的"有用符号 2048 + CP 504"。
- **CP 自相关**（`dab_dsp.h:58-110`）：滑窗内递推 `corr -= histBuf[histId]; histBuf[histId] = val.conj()*delayBuf[i+symbolSamps]; corr += histBuf[histId]`（`dab_dsp.h:66-76`）——即对 `x[n]·conj(x[n+2048])` 在长度 504 的窗内做滑动求和。
- **峰检测**：`rcorr = corr.amplitude()`；当 `rcorr > avgCorr && rcorr > peakCorr`（`dab_dsp.h:82`）就更新峰位置 `samplesSincePeak = 0`；然后**从峰位置开始，连续输出 symbolSamps=2048 个样点**作为一个 OFDM 符号（`dab_dsp.h:94-103`），凑满就 `out.swap(2048)`。
- 自适应门限 `avgCorr = agcRate*rcorr + (1-agcRate)*avgCorr`（`dab_dsp.h:109`，agcRate=1e-3）。
- 机制总结：**不做 CP 剥离/FFT，只做"找到每符号的起点，然后切 2048 样点出去"**——这是 OFDM 接收机里最简单的粗符号同步。

#### 2.1.3 FrameFreqSync — 帧同步 + 整数/小数频偏（`dab_dsp.h:142-279`）

- 输入是 CyclicSync 切出来的 2048 样点/符号。
- **每符号先做 NCO 旋转**：`volk_32fc_s32fc_x2_rotator2_32fc(readBuf, readBuf, phaseDelta={cos(offset),sin(offset)}, &phase, count)`（`dab_dsp.h:182-188`）——把当前频偏估计 `offset` 反旋上去。
- **空窗/帧边界检测**：`volk_32fc_magnitude_32f(amps,...); volk_32f_accumulator_s32f(&level, amps, 2048)`（`dab_dsp.h:191-195`）；若 `level < avgLvl*0.5`（`dab_dsp.h:198`）就判定帧间空窗，`sym = 1`（`dab_dsp.h:200`）并 return——即 DAB 帧之间的零功率区间用来重置符号计数。
- **相位参考符号（sym==1）上做 FFT 找峰**（`dab_dsp.h:214-255`）：
  1. 先做一段 DEBUG 用的相邻子载波差分自相关（`dab_dsp.h:219-227`）：对 `i in -767..767` 算 `corrOut[i]·conj(corrOut[i-1]) / |corrOut[i-1]|²`，出 1535 个点喂星座图——这是**信道估计/诊断用**，不是业务路径。
  2. 真正的频偏估计：`corrIn = input * conjRef`（`dab_dsp.h:230`，`conjRef` 来自 `dab_phase_sym.h` 的 2048 点 `DAB_PHASE_SYM_CONJ` 常量表，phase16 已确认是纯数据表），FFT 2048 点，`volk_32f_index_max_32u` 找最高功率 bin（`dab_dsp.h:240`）。
  3. **整数频偏**：`offInt = peakId<1024 ? peakId : peakId-2048`（`dab_dsp.h:247`）；**小数频偏**：`off = π·(offInt + (peakR-peakL)/(peakR+peakL)) / 1024`（`dab_dsp.h:250`，相邻 bin 幅度比值插值）。
  4. 控制环：`offset -= 0.1f*off`（`dab_dsp.h:253`）——单环 IIR 锁 NCO。
- **数据符号（sym>=2）上什么都不做**：只 `sym++` 然后 `flush`（`dab_dsp.h:257-262`）。没有 FFT、没有差分解调、没有解交织、没有 Viterbi、没有 FIC/MSC 拆分。

### 2.2 信号参数

- 2.048 Msps 复采样，1.6 MHz 带宽，DAB Mode I：2048 子载波 FFT + 504 样点 CP，符号长 1 ms，每帧 76 个 OFDM 符号（这里没硬编码帧长，只靠空窗重置 sym）。
- QPSK 差分调制（DAB 标准），但 SDR++ 没做到差分解调那一步。

### 2.3 处理链（SDR++ 实际做到的）

```
VFO 1.6MHz → CP 自相关找符号峰(切 2048 样点) → NCO 反旋当前 offset
   → 测符号电平：<0.5×avg 判帧边界(sym=1)；否则 sym++
   → sym==1 时 ×conjRef → FFT → 找峰 → 整/小数频偏 → 更新 NCO offset
   → 星座图（DEBUG）
```

**缺失**（即"要落地 DAB 还得自己写"的部分）：
1. 数据符号的 FFT → 提取有效子载波（DAB Mode I 有 1536 个有效子载波，中心 DC 空）。
2. 差分解调 `X[k]·conj(X_prev[k])`。
3. 频率解交织 + 时间解交织（DAB 标准的 bit 级卷积交织，查表）。
4. Viterbi 解码（DAB 是 r=1/4 卷积 + 删余，不是 r=1/2）。
5. 卷积解扰 + FIC/MSC 拆分。
6. FIC → FIB → FIG 解析（**这一层 MBDSDR 已经有了**，见 §2.5）。
7. MSC → 子载波解复用 → 各 subchannel → AAC 帧（AAC 外部受限，本仓不做）。

### 2.4 与 phase16 的差异

phase16 §1.4 说"只做到同步前端，未真正解码音频/数据"——本轮逐行读完后**确认无误**，并补出具体的"前端只覆盖到哪"：CP 粗定时 + 基于相位参考符号的 FFT 频偏估计。**数据符号路径是空的**。

### 2.5 MBDSDR 已有能力对照（新发现，关键）

- `mbdsdr_ai/dab_plus_lite.py`（575 行，MIT 干净室）**已经实现了基带后的全部元数据层**：
  - `crc16_ccitt()` / `crc_fire_code()`（`:57,:72`）。
  - `DABParams`（`:95`，Mode I 参数：FFT=2048、CP、符号、76 符号/帧、ETI 帧长）。
  - `FICDecoder`（`:178`）：FIB → FIG0/FIG1 → ensemble/service/subchannel 表，EBU 短/长 label 解码（`:353,:364`）。
  - `ETIParser`（`:387`）：ETI 帧同步字搜索 + 帧结构解析。
  - `dab_decode_iq()`（`:558`）——**名不副实**，docstring 自己写"输入假定为已分帧 ETI(NI) 字节；OFDM 解调/Viterbi/解扰由本层之前的信道解码完成"。
  - 即：MBDSDR 缺的是"从 2.048 Msps 复基带 → ETI 字节"这一整段 OFDM 信道解码；SDR++ 的 dab_decoder 只覆盖了这一段的前 2 块（符号定时 + 频偏）。
- FEC 原语：`mbdsdr_ai/fec.py` 已有 `ReedSolomon` / `Scrambler`；`mbdsdr_ai/ccsds_rx.py` 已有干净室 K=7 r=1/2 Viterbi（Phil Karn 机制）。DAB 用的是 r=1/4 + 删余的不同码率，需要另配，但 Viterbi 引擎可复用。

### 2.6 可复用点

- **CP 自相关符号定时 + 相位参考 FFT 频偏环**是任何 OFDM 模式（DAB/DRM/CMMB/5G-narrowband）都要的通用前端。机制干净、可云内用合成 OFDM 信号确定性单测。
- 但 SDR++ 这两块的具体常数（2048/504/conjRef 表）是 DAB 专有的，**不抄表**，机制学下来即可。

---

## 3. Falcon9 — `falcon9_decoder/src/`（SpaceX 私有 S 波段遥测，事件触发）

### 3.1 机制（file:line）

- **基带链**（`main.cpp:44-63`）：VFO 4 MHz @ 6 Msps（`main.cpp:35,44`）→ `FloatFMDemod`（`main.cpp:52`，去调制到 2 MHz 低通）→ `MMClockRecovery`（`main.cpp:53`，ratio = 6e6/3.5714e6 ≈ 1.68 samples/symbol → **符号率 ≈ 3.5714 Mbaud**）→ Splitter 两路：一路 reshaper 1024:198976 喂星座图（`main.cpp:57-58`），一路 `Threshold` 二值切片 → `Deframer`（`main.cpp:60`，帧长 10232 bit，32-bit 同步字）→ `FalconRS` → `FalconPacketSync` → sink。
- **同步字**（`main.cpp:232`）：32 bit 自定义 `{0,0,0,1,1,0,1,0,1,1,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,1,1,1,0,1}`。
- **RS 解 FEC**（`falcon_fec.h:68-128`）：用 libcorrect 建 CCSDS RS(255,223)（`falcon_fec.h:73`，`correct_rs_primitive_polynomial_ccsds, 120, 11, 16`），**5 路并行 RS 块**，先 `fromDB[data[i]]` 做差映射（`falcon_fec.h:88`，256 项查表 `fromDB`/`toDB` `falcon_fec.h:10-36`）再按 `i%5` 解交织，5 块全部 decode 成功才出，否则丢整帧（`falcon_fec.h:93-117`）；重交织后再 `^ randVals[i%255]` 解扰（`falcon_fec.h:121`，255 项 PRBS 表 `falcon_fec.h:38-55`）。
- **包同步**（`falcon_packet.h:28-105`）：每帧 4 字节头 = `counter:19bit + packet:13bit`（`falcon_packet.h:34-35`）；`packet==2047` 表示"帧尾延续包"，否则按 length 字段逐个抽 space-packet；丢帧（`lastCounter+1 != counter`，`falcon_packet.h:42`）就废掉正在拼的半包。
- **业务分发**（`main.cpp:184-202`）：按 64-bit `pktId` 硬编码路由——
  - `0x0117FE0800320303` / `0x0112FA0800320303` → GPS NMEA 字符串，追加到 UI 日志 tab（`main.cpp:190-195`）。
  - `0x01123201042E1403` → 940 字节一帧摄像头视频，`fwrite` 到 `ffplay` stdin 实时播放 + 写 `output.ts` 文件（`main.cpp:196-199`）。

### 3.2 信号参数

- 6 Msps、4 MHz 带宽、FM 解调后 3.57 Mbaud、5 路 CCSDS RS(255,223) 并行交织、每帧 10232 bit。SpaceX 一级箭 S 波段（~2.2 GHz）私有遥测。

### 3.3 处理链

```
VFO 4MHz → FM demod → MM 时钟恢复(1.68 sps) → 二值切片 → 32-bit 同步字 deframe
   → fromDB 差映射 + 5 路解交织 → CCSDS RS(255,223)×5 → 重交织 + randVals 解扰
   → 帧头 counter/packet 指针 → space-packet 抽包 → 按 pktId 分发(GPS / 摄像头)
```

### 3.4 可复用点

- **没有新机制**。MM 时钟恢复 + RS + 解交织 + PRBS 解扰 + 32-bit 同步字，MBDSDR 在 `m17_decoder` / `ccsds_rx` / `pocsag` 里全部已经有同型件。
- 唯一"特别"的是硬编码 pktId 路由——纯 SpaceX 业务，不可复用。

---

## 4. KG-SSTV — `kg_sstv_decoder/src/`（4FSK 数字 SSTV 变体，SDR++ 自己只 dump 二进制）

### 4.1 机制（file:line）

- **基带链**（`main.cpp:35-62`）：`INPUT_SAMPLE_RATE=6000 Hz`（音频速率！`main.cpp:35`），VFO 3 kHz；类名同样误抄 `M17DecoderModule`（`main.cpp:37`）。
- **Hier block**（`kg_sstv_dsp.h:234-257`）：`FloatFMDemod`（`kg_sstv_dsp.h:237`，deviation=300 Hz）→ RRC 滤波（`kg_sstv_dsp.h:238`，31 tap、baud=1200、alpha=0.7）→ FIR → `MMClockRecovery`（`kg_sstv_dsp.h:240`，sampleRate/1200 = 5 sps）→ `StreamDoubler` → `Deframer`。
- **Deframer**（`kg_sstv_dsp.h:141-208`）：
  - 63-bit 同步字（`kg_sstv_dsp.h:30-35`，BPSK 极性序列），滑动匹配允许 ≤4 bit 错误（`kg_sstv_dsp.h:148-155`）。
  - 同步后攒 **108 个软符号**（`kg_sstv_dsp.h:176,179`），把浮点样点 `clamp((x+1)*128, 0, 255)` 转 8-bit 软值。
  - 按 108-bit `KGSSTV_SCRAMBLING` 表（`kg_sstv_dsp.h:37-46`）逐字节取反解扰（`kg_sstv_dsp.h:185-191`）。
  - 喂 libcorrect K=7 r=1/2 Viterbi（多相式 `{0155,0117}`，`kg_sstv_dsp.h:55,123`），解出 **7 字节**（`kg_sstv_dsp.h:194,197`）。
- **出口**：`FileSink<uint8_t> ns2` 写 `kgsstv_out.bin`（`kg_sstv_dsp.h:245`）——**没有图像重建、没有 RGB 映射、没有 PNG 输出**，每帧只出 7 字节落盘。

### 4.2 信号参数

- 音频速率 6 kHz、4FSK（4 电平频移键控）、1200 baud、频偏 ±300 Hz、RRC 0.7、K=7 r=1/2 卷积、63-bit 同步、每帧 108 软符号 → 7 字节硬输出。

### 4.3 处理链

```
音频 IQ → FM 解调 → RRC(α=0.7) → MM 时钟恢复 → 同步字搜索(63bit,≤4 err)
   → 攒 108 软符号 → 108-bit 表解扰 → K=7 r=1/2 软判决 Viterbi → 7 字节/帧 → 落盘
```

### 4.4 可复用点 / 与 MBDSDR 重叠

- MBDSDR 已有 `mbdsdr_ai/sstv_decoder.py`（1083 行，Martin M1/Scottie S1/Robot 36 模拟 SSTV）+ `cpp/src/dsp/sstv_vis.{h,cpp}`（VIS 解码 + 过零测频干净室核心）。
- KG-SSTV 是**另一种**数字 SSTV 模式（4FSK+Viterbi），与模拟 SSTV 不重叠；但 SDR++ 自己只解出 7 字节/帧落盘，**没有图像重建参考**——落地它等于从 7 字节帧格式 spec 反推图像，工程量不小且上游无参考。
- Viterbi K=7 r=1/2 MBDSDR 已经有干净室实现（`ccsds_rx.py` 引用 Phil Karn 机制），不缺。

---

## 5. RYFI — `ryfi_decoder/src/ryfi/`（SDR++ 作者私有 QPSK 数据链）

### 5.1 机制（file:line）

- **基带链**（`main.cpp:26-42`）：VFO 600 kHz @ 1 Msps（`main.cpp:26-27`），符号率 500 kbaud（`main.cpp:28`），即 2 sps。
- **Receiver**（`receiver.h:57-68`, `receiver.cpp:17-25`）：`dsp::demod::PSK<4>`（QPSK，`receiver.cpp:19`，31-tap RRC、alpha 0.6、各种环增益）→ `Doubler` 分星座诊断路 + Deframer → `ConvDecoder` → `RSDecoder`；`onPacket` 回调出包（`receiver.h:54`）。
- **Deframer**（`framing.h:47-86`, `framing.cpp:85-136`）：
  - 64-bit 同步字 `0x341CC540819D8963`（`framing.h:8`），按 2 bit/symbol 映射成 32 个 QPSK 符号。
  - 处理 QPSK 4 重相位模糊：预计算 0°/90°/180°/270° 四个旋转后的 syncRots（`framing.cpp:50-80`），滑窗 64-bit 移位寄存器里做汉明距离 **<6** 就算同步（`framing.cpp:121`，允许 ~3 bit 错），选定 `symRot` 旋转后续符号。
  - 锁相后硬编码接收 **8168 个符号**（`framing.cpp:127`，TODO 注释说别硬编码）。
- **ConvDecoder**（`conv_codec.cpp:33-73`）：QPSK 软值 `(re,im) → uint8 (x*127+128)` 当软比特（`conv_codec.cpp:54-58`），喂 libcorrect K=7 r=1/2（`correct_conv_r12_7_polynomial`，`conv_codec.cpp:35`）。
- **RSDecoder**（`rs_codec.h:12-21`, `rs_codec.cpp:53-101`）：CCSDS RS(255,223)，**4 路并行块**，先与 `RS_SCRAMBLER_SEQ[1020]`（`rs_codec.cpp:103-168`，一大张 PRBS 常量表）异或解扰，再按 `i%4` 解交织，逐块 decode，任何一块失败整帧丢（`rs_codec.cpp:85-86`）。
- **Frame 结构**（`frame.h:25-40`, `frame.cpp:4-36`）：`FRAME_SIZE = RS_BLOCK_DEC_SIZE*4 = 892 字节`，头 6 字节 = counter(2) + firstPacket(2) + lastPacket(2)，剩余 886 字节内容。
- **Packet 重组**（`receiver.cpp:69-193`）：worker 线程读 RS 输出 → 反序列化 Frame → 检查 counter 连续性（丢帧就废半包，`receiver.cpp:97-118`）→ 按 firstPacket/lastPacket 指针从 frame.content 里抽变长 packet（2 字节大端长度前缀，`receiver.cpp:183-188`）→ 拼包完成触发 `onPacket(pkt)`。

### 5.2 信号参数

- 1 Msps / 600 kHz BW / 500 kbaud QPSK / 2 sps / K=7 r=1/2 软 Viterbi / CCSDS RS(255,223)×4 交织 / 1020 字节 PRBS 加扰 / 64-bit 同步字（汉明距<6）/ 帧长 892 字节（4×223）。

### 5.3 处理链

```
VFO 600kHz → QPSK 解调+RRC+载波环 → 星座路 / 位路
   → 64-bit 同步字(4 旋转模糊,汉明<6) → 8168 sym/帧
   → 软值 (re,im)→uint8 → K=7 r=1/2 Viterbi → 字节流
   → 与 1020B PRBS 异或解扰 → 4 路解交织 → RS(255,223)×4 → 892B 帧
   → counter 连续性检查 + first/lastPacket 指针抽包 → onPacket
```

### 5.4 可复用点

- **没有新机制**。QPSK 解调 MBDSDR `digital_demod.h` 已有 Costas NCO；K=7 r=1/2 Viterbi MBDSDR `ccsds_rx.py` 已有干净室；RS(255,223) MBDSDR `fec.py` 已有；同步字+旋转模糊是 QPSK 标配。
- RYFI 是 SDR++ 作者自家做的"实验性点对点数据模式"，没有部署网络、没有标准、没有对端可听。

---

## 6. 逐模块落地决策（YAGNI 诚实版）

> 评估维度：**通用价值 × MBDSDR 已有差距 × 成本**。

### 6.1 DAB —— **YAGNI 本轮（但机制记档，作为未来 OFDM 前端种子）**

- **决策**：本轮**不落地端到端 DAB**；只把"CP 自相关符号定时 + 相位参考 FFT 频偏环"两个通用 OFDM 同步件的机制记入笔记（即本节 §2.1.2/§2.1.3），未来做任何 OFDM 模式时干净室抽。
- **理由**：
  1. **SDR++ 自己没做完**：`dab_decoder` 链末端就是星座图，数据符号路径空着（`dab_dsp.h:257-262`），没有 FFT 解数据符号、解交织、Viterbi、FIC/MSC——它不是一个可参考的完整 DAB 接收机，只参考得到前两块。
  2. **MBDSDR 已经把"基带后"元数据层写完了**：`mbdsdr_ai/dab_plus_lite.py` 575 行 FIC/FIG/ETI/CRC/业务表全部就位（§2.5）。缺的是"2.048 Msps 复基带 → ETI 字节"这段 OFDM 信道解码，SDR++ 没给齐。
  3. **成本高**：要自己写 2048-FFT 数据符号解调 + 频率/时间双层解交织（DAB 标准卷积交织表）+ r=1/4 删余 Viterbi（与现有 r=1/2 Viterbi 码率不同）+ MSC 子信道解复用；AAC 音频按红线外部受限不做，但至少要能解出 FIC 服务列表才算"落地"。
  4. **测试可行性差**：国内 DAB 商用部署基本退役（国标走 CDR），长春本地抓不到现役 DAB 信号；云内确定性测试要先按 ETSI EN 300 401 合成一整段带 FIC 的 DAB 波形，工作量等于先写一个 DAB 发射机。
- **回补条件**（任一触发即重启）：
  - (a) 拿到一段可录制的 DAB IQ 波形（出差欧洲/澳洲/韩国抓包，或找到公开的 DAB 录制样本），有了金标准才能云内迭代；
  - (b) MBDSDR 路线图新增任意 OFDM 模式（DRM 数字广播 / CMMB / 卫星 DVB-S2 窄带 / 5G-narrowband-IoT 物理层），此时把 §2.1.2/§2.1.3 的 CP 自相关 + 相位参考 FFT 频偏环抽成通用 `OfdmSymbolSync`/`OfdmFfoTracker` 两件，DAB 作为首个实例；
  - (c) 有人明确提"要在 MBDSDR 上听 DAB 电台"作为 P0 需求。

### 6.2 ATV —— **YAGNI**

- **决策**：不落地。
- **理由**：
  1. **SDR++ 自己只做了灰度**：NTSC 色副载波整条通路（123-tap `CHROMA_BANDPASS`、burst 锁相、色差旋转）全部注释掉（`main.cpp:171-217,222-228`），`filters.h` 那张 123 抽头复数带通表是死代码。参考价值只到"灰度行扫描"。
  2. **机制重叠**：行同步 PLL（polyphase + NCO + 误差平均）与 MBDSDR `apt_decoder.h:114-132` 的"滑动归一化互相关锁行 + 分数像素累加器"是同一类东西，不缺；AM 包络检波 MBDSDR `demod.h` 已有。
  3. **硬件不匹配**：ATV 要 7 MHz 实时带宽，MBDSDR-Mini 前端带宽 ~2 MHz；场同步 pattern 匹配 + 768×576 像素渲染对桌面端 UI 也是新投入。
  4. **国内无信号源**：业余快扫电视在 10 GHz/1.2 GHz，长春本底没有爱好者活动。
- **回补条件**：
  - (a) MBDSDR-Mini 下一代硬件把射频前端带宽做到 ≥10 MHz；
  - (b) 出现明确的"接收业余 ATV / 火箭航拍模拟视频"需求（通常跟卫星地面站配套）。

### 6.3 Falcon9 —— **YAGNI**

- **决策**：不落地。
- **理由**：
  1. **SpaceX 私有协议 + 事件触发**：pktId 是硬编码的 SpaceX 内部 ID（`main.cpp:190,196`），只在猎鹰 9 发射窗口能听到，平时完全静默。
  2. **硬件门槛远超 MBDSDR-Mini**：S 波段 2.2 GHz + 抛物面天线 + 低噪放，是一个独立的"发射监控站"项目，不是便携 SDR 能做的事。
  3. **机制零新增**：FM→MM 时钟恢复→32-bit 同步字→CCSDS RS(255,223)×5→PRBS 解扰→space-packet 抽包，MBDSDR 在 m17/ccsds_rx/pocsag 里已经把这套全做过一遍。
- **回补条件**：
  - (a) MBDSDR 衍生成固定卫星地面站产品，且有用户要追猎猎鹰 9 / Starship 遥测；
  - (b) 出现公开的 SpaceX 遥测标准文档（目前都是逆向），否则干净室基础不稳。

### 6.4 RYFI —— **YAGNI**

- **决策**：不落地。
- **理由**：
  1. **私有小众模式**：RYFI 是 SDR++ 作者（Ryzerth）自己设计的实验性 QPSK 数据链，不是任何公开标准；没有部署网络、没有对端发射机能听。
  2. **机制零新增**：QPSK 解调（MBDSDR `digital_demod` Costas 已有）+ 64-bit 同步字 4 旋转模糊（QPSK 标配）+ K=7 r=1/2 Viterbi（MBDSDR `ccsds_rx.py` 已有干净室）+ RS(255,223)×4 交织（MBDSDR `fec.py` 已有）+ PRBS 解扰——全是 MBDSDR 已有的件。
  3. **SDR++ 自己也是半成品**：`framing.cpp:127` 帧长 8168 硬编码带 TODO；`main.cpp:88-90` 收到包只 `flog::debug` 打一行日志，没有业务层。
- **回补条件**：
  - (a) 出现一个公开的 RYFI 网络/中继站（目前没有）；
  - (b) 我们要主动和另一个 SDR++ 玩家做点对点 QPSK 实验——届时直接用现有 Costas+Viterbi+RS 件拼，不需要再参考 SDR++。

### 6.5 KG-SSTV —— **YAGNI**

- **决策**：不落地。
- **理由**：
  1. **SDR++ 自己没解完图像**：每帧 Viterbi 后只出 7 字节，`FileSink` 写 `kgsstv_out.bin`（`kg_sstv_dsp.h:245`），没有像素重建、没有 PNG。参考价值只到"4FSK 同步 + Viterbi"，而这两件 MBDSDR 都有。
  2. **与已有 SSTV 重叠但不同**：MBDSDR 已有模拟 SSTV（Martin/Scottie/Robot，`mbdsdr_ai/sstv_decoder.py` + `cpp/src/dsp/sstv_vis.h`）；KG-SSTV 是 4FSK 数字 SSTV 的另一个小众分支（国外个别爱好者在 2 m 波段玩），国内几乎无活动。
  3. **测试无金标准**：没有录制好的 KG-SSTV 波形，云内没法确定性回归。
- **回补条件**：
  - (a) 有用户明确要求加 KG-SSTV 模式（届时复用 fsk_demod + 已有 Viterbi，只补 63-bit 同步字 + 7 字节帧 → 图像映射表）；
  - (b) 找到公开的 KG-SSTV 录制样本做回归基线。

---

## 7. 精读文件清单（实际打开读过的）

**atv_decoder/src/**（4/4）：
- `main.cpp`（314 行，全读）
- `linesync.h`（254 行，全读）
- `amplitude.h`（65 行，全读）
- `filters.h`（130 行，确认是 123-tap NTSC chroma 复数 FIR 常量表，未逐数）

**dab_decoder/src/**（3/3）：
- `main.cpp`（163 行，全读）
- `dab_dsp.h`（279 行，全读）
- `dab_phase_sym.h`（2052 行，**未逐行**，head/tail 确认是 2048 点 `DAB_PHASE_SYM_CONJ` 复数常量表，与 phase16 §5 结论一致）

**falcon9_decoder/src/**（3/3）：
- `main.cpp`（258 行，全读）
- `falcon_fec.h`（139 行，全读；`fromDB`/`toDB`/`randVals` 三张表是协议专有常量表，未逐数）
- `falcon_packet.h`（117 行，全读）

**kg_sstv_decoder/src/**（2/2）：
- `main.cpp`（193 行，全读）
- `kg_sstv_dsp.h`（279 行，全读；`KGSSTV_SYNC_WORD`/`KGSSTV_SCRAMBLING` 是协议专有常量表，未逐数）

**ryfi_decoder/src/**（15/15）：
- `main.cpp`（138 行，全读）
- `ryfi/receiver.{h,cpp}`（68+193 行，全读）
- `ryfi/framing.{h,cpp}`（86+136 行，全读）
- `ryfi/frame.{h,cpp}`（41+36 行，全读）
- `ryfi/conv_codec.{h,cpp}`（70+73 行，全读）
- `ryfi/rs_codec.{h,cpp}`（81+168 行，全读；`RS_SCRAMBLER_SEQ[1020]` 是 PRBS 常量表，未逐数）
- `ryfi/packet.{h,cpp}`（88+125 行，**packet.h 全读接口；packet.cpp 只确认是 byte buffer 容器 + 2 字节长度前缀 serialize，未逐行背 alloc 逻辑**）
- `ryfi/transmitter.{h,cpp}`（68+176 行，**未读**——是发射侧编码，与接收解码决策无关）

**MBDSDR 对照侧**：
- `cpp/src/dsp/sstv_vis.h`（头 50 行，确认是干净室 SSTV VIS + 过零测频核心）
- `mbdsdr_ai/dab_plus_lite.py`（head 40 行 + API grep + `:555-575` 结尾，确认是 ETI/FIC/FIG 元数据层，**未逐行精读每一个 FIG 解码分支**）
- `mbdsdr_ai/sstv_decoder.py`（head 30 行，确认支持 Martin/Scottie/Robot）
- `mbdsdr_ai/ccsds_rx.py`（head 25 行，确认已有干净室 K=7 Viterbi + RS 链路）

---

## 8. 未读透清单（如实）

1. **`dab_phase_sym.h` 2052 行**：纯 2048 点复数常量表（DAB 标准 B.5.1 相位参考序列的共轭），head/tail 已确认结构，但**未逐点核对每个复数的相位**——这是协议专有数据表，MBDSDR 不会抄表，无逐行必要。
2. **`ryfi/transmitter.{h,cpp}` 244 行**：发射侧编码，与"接什么、解什么"的落地决策无关，未读。
3. **`ryfi/packet.cpp` 125 行**：byte buffer 的 alloc/copy/serialize 样板代码，`packet.h` 接口已看清（2 字节长度前缀，最大 64KB），未逐行背。
4. **`mbdsdr_ai/dab_plus_lite.py` 的 FIG0/FIG1 每个分支**（`:204-352`）：只 grep 出类/函数签名 + 抽样读 docstring，没逐行核对每个 FIG 类型的位段偏移。这不影响本轮决策（结论是"元数据层已存在"，不是要改它）。
5. **`filters.h` 的 123-tap NTSC chroma FIR 表**：纯系数表，且整条 chroma 路径在 SDR++ 里是注释掉的死代码，未逐数。
6. **`falcon_fec.h` 的 `fromDB/toDB/randVals`、`kg_sstv_dsp.h` 的 `SYNC_WORD/SCRAMBLING`、`rs_codec.cpp` 的 `RS_SCRAMBLER_SEQ`**：都是协议专有常量表，MBDSDR 不抄，未逐数。
7. **SDR++ 仓库外的 ETSI EN 300 401 标准正文**：本轮没拉标准原文，DAB 缺失段（§2.3 列的 7 项）是基于"SDR++ 代码里没出现 FFT 数据符号路径"反推的，若真要落地 DAB 第一步必须读标准原文。
