# SDR++ 解调链子系统研习笔记（Phase12 / Wave1-C）

> 上游仓库：`repos/sdrpp`（**GPLv3**，Rygeloria / Alex 等）。本笔记只学机制与算法骨架，
> 所有引用片段均为短摘录并注明 file:line；落地 MBDSDR 时按干净室原则重写，不逐字复制。
>
> 研习范围：
> - `core/src/dsp/demod/{quadrature,fm,am,ssb,cw,broadcast_fm,psk,gfsk}.h`
> - `core/src/dsp/channel/{frequency_xlator,rx_vfo}.h`
> - `core/src/dsp/multirate/rational_resampler.h`、`math/{normalize_phase,fast_atan2}.h`、`taps/low_pass.h`
>
> MBDSDR 对照基线：`cpp/src/dsp/{demod.{h,cpp},channelizer.{h,cpp},vfo_manager.h,
> digital_demod.{h,cpp},wfm_stereo.{h,cpp},agc.{h,cpp},cw_decoder.{h,cpp},audio_resampler.h}`。

---

## 0. SDR++ 解调链架构总图

SDR++ 把"信道选择"与"解调"**严格解耦**：

```
宽频 IQ → FrequencyXlator(信道移到 DC) → RationalResampler(抽/插值到 IF) → 可选 LPF
        → RxVFO (整体封装, channel/rx_vfo.h:6)
        → 各模式 Demod<complex_t, float|stereo_t> (demod/*.h)
        → 音频后端
```

要点：
- **解调器内部不再做信道选择**——`am.h`/`ssb.h`/`fm.h` 假设进来的 IQ 已经是中心在 0、
  采样率合适的窄带复基带。AM 只做包络，SSB 只做 BFO 偏移 + 取实部。
- 每个解调器是一个 `Processor<in_t,out_t>`，`process(count,in,out)` 内联手写循环或
  volk 调用；状态（相位、抽头、滤波器 tail）随块保留。
- 所有可热改参数（带宽/采样率/偏差）走 `setBandwidth()` 等，内部 `tempStop()` →
  重建抽头 → `tempStart()`，不丢流。

---

## 1. FM 鉴频器核心：`demod/quadrature.h`（GPLv3）

**机制**：相邻样本相位差分鉴频。

`quadrature.h:39-46`（短片段，GPLv3）：
```cpp
inline int process(int count, complex_t* in, float* out) {
    for (int i = 0; i < count; i++) {
        float cphase = in[i].phase();
        out[i] = math::normalizePhase(cphase - phase) * _invDeviation;
        phase = cphase;
    }
    return count;
}
```

- 每样本两次 atan2（当前 + 上一拍状态 `phase`），差分后用 `normalize_phase.h:6-10`
  的 `if (diff > pi) diff -= 2pi; else if (diff <= -pi) diff += 2pi;` 折回 (-π, π]。
- 增益：构造时 `_invDeviation = 1/deviation`（`quadrature.h:19`），deviation 由上层
  传入（窄带 FM 传 `bandwidth/2`，广播 FM 传 75 kHz）。
- `reset()`（:48-52）只清 `phase=0`，无其他状态——极简。

**MBDSDR 对照**：`cpp/src/dsp/demod.cpp:71-83`（DemodNFM）与 `:97-121`（DemodWFM）
用的是**等价但更省**的写法——共轭相乘后一次 atan2：
```cpp
std::complex<float> y = iq[i] * std::conj(prev_);   // demod.cpp:74
float angle = std::atan2(y.imag(), y.real());       // demod.cpp:75
float demod = gain_ * angle;
prev_ = iq[i];
```
数学上 `arg(a·conj(b)) = arg(a)−arg(b)`（主值），与 SDR++ 的两次 atan2 差分等价；
MBDSDR 一次复数乘 + 一次 atan2，比 SDR++ 两次 atan2 略省。**此项判定：已实现且真实**，
无差距。

---

## 2. 窄带 FM：`demod/fm.h`（GPLv3）

**机制**：Quadrature 鉴频 → 可选 Nuttall 窗低通。

- 偏差归一：`fm.h:30` `demod.init(NULL, bandwidth / 2.0, _samplerate);`
- 抽头生成：`fm.h:121`
  ```cpp
  filterTaps = dsp::taps::lowPass(_bandwidth / 2.0, (_bandwidth / 2.0) * 0.1, _samplerate);
  ```
  即截止 = bw/2，过渡带 = 0.1·bw，窗函数见 `taps/low_pass.h:7-11` → `windowedSinc` +
  Nuttall 窗（`window/nuttall.h`）。
- 处理链：`fm.h:79-96` 内联 `demod.process(count, in, out); if (_lowPass) fir.process(out,out);`
  （in-place）。
- 带宽热切换：`fm.h:55-62` `setBandwidth()` 改 `_bandwidth` → `demod.setDeviation(bw/2)`
  → `updateFilter()` 释放旧抽头、按新 bw 重设计、`fir.setTaps()`。无流中断。
- 当 `_lowPass=false`：`loadDummyTaps()`（:132-135）塞一个 1-tap 抽头 `[1.0f]`，
  FIR 退化为直通——**避免条件分支散布在 process() 里**。

**MBDSDR 对照**：`demod.cpp:71-83`（DemodNFM）鉴频后**只接了一个 50 µs 去加重单极点**
（:65-66 `deAlpha = dt/(tau+dt)`），没有按带宽匹配的 FIR 低通。`setBandwidth()` 在
`IDemod` 基类是空默认（`demod.h:22`），DemodNFM **没有 override**——即运行时切带宽
滤波器不跟随。详见差距 G3。

---

## 3. AM 包络检波：`demod/am.h`（GPLv3）

**机制**：|·| 包络 → DC 阻断 → 可选音频 AGC → 低通。

- 包络：`am.h:109` `volk_32fc_magnitude_32f(out, (lv_32fc_t*)in, count);`（volk SIMD）。
- **双 AGC**：`am.h:34-35` 同时构造 `carrierAgc`（复数输入，target=1.0, attack/decay 可调,
  max=10e6）和 `audioAgc`（浮点输出端）。`AGCMode::CARRIER` 在 :103-106 把复数 IQ
  **先过 AGC 再取包络**；`AGCMode::AUDIO` 在 :111-113 取包络后再过浮点 AGC。
- DC 阻断：`am.h:36` `dcBlock.init(NULL, dcBlockRate)`，:110 在包络后立即滤除残留载波直流。
- 后置 LPF：`am.h:37` `lpfTaps = taps::lowPass(bandwidth/2.0, (bandwidth/2.0)*0.1, samplerate);`
  与 FM 同一套 Nuttall 窗设计。
- **解调器内不带 BPF**——信道由上游 RxVFO 选好。

**MBDSDR 对照**：`demod.cpp:46-59`
```cpp
float m = std::abs(iq[i]);
float y = m - dcPrev_ + R * dcPrev_;   // 一阶 DC 阻断, R = 1-100/sr
dcPrev_ = y;
```
有包络、有 DC 阻断、有后置 63-tap Hann FIR（:37, :43）；但**完全没有 AGC**。
`cpp/src/dsp/agc.{h,cpp}` 存在，但挂在音频输出后端（`qt_audio_sink`/`audio_output` 链路），
不在 DemodAM 内部。详见差距 G1。

---

## 4. SSB / CW 乘积检波：`demod/ssb.h`、`demod/cw.h`（GPLv3）

**机制**：SDR++ 这里**不用 Hilbert 正交滤波法**，而是"BFO 频移 + 取实部"：

- `ssb.h:106-116` `getTranslation()`：
  ```cpp
  if (_mode == Mode::USB)      return  _bandwidth / 2.0;
  else if (_mode == Mode::LSB) return -_bandwidth / 2.0;
  else                         return 0.0;   // DSB
  ```
- `ssb.h:77-92` 处理：`xlator.process(count, in, ...)` 把期望边带搬到 DC，
  然后 `convert::ComplexToReal::process(...)` 直接取 `.real()`，再过 AGC。
  数学上这就是乘积检波：把载波移到 0 频后，I 路即基带音频。
- CW 完全复用同一架构（`cw.h:21` `xlator.init(NULL, tone, samplerate)`），
  只是把 translation 固定为用户设定的音频拍频（默认 600–800 Hz）。
  `cw.h:57-69` process 与 SSB 一模一样。

**MBDSDR 对照**：`demod.cpp:144-154`（DemodSSB）
```cpp
std::complex<float> osc(std::cos(phase_), std::sin(phase_));
mixed[i] = (iq[i] * osc).real();
phase_ += dPhi_;
```
同样是"BFO 移频 + 取实部"，机制与 SDR++ 一致；但 NCO 用 `std::cos/sin` 每样本现算
（SDR++ `frequency_xlator.h:43-50` 用 `volk_32fc_s32fc_x2_rotator2_32fc` 相量递推，
无三角函数）。SSB 末尾接 63-tap Hann LPF（:126, :152），**无 AGC**（SDR++ :83 有
`agc.process(out,out)`）。CW 在 MBDSDR 里走 `cw_decoder.cpp`，但那是 Morse 译码器
（RBJ biquad 500–900 Hz + 包络 + 自适应阈值），**不是** BFO 乘积检波器——即 MBDSDR
没有独立的 CW 解调模式，CW 音频由 SSB 模式人工拍频收听。

---

## 5. 广播 FM 立体声：`demod/broadcast_fm.h`（GPLv3）

**机制**：
1. 鉴频（:146 `demod.process(count, in, demod.out.writeBuf)`）→ MPX 复合基带。
2. 实数转复（:149 `rtoc`）→ 19 kHz pilot 带通 FIR（:43
   `taps::bandPass<complex_t>(18750.0, 19250.0, 3000.0, samplerate, true)`）。
3. Pilot PLL（:46）：自然频 25000/sr，初始 19000 Hz，频率窗 18750–19250 Hz。
4. **群延时对齐**（:47-48, :156-157）：
   ```cpp
   lprDelay.init(NULL, ((pilotFirTaps.size - 1) / 2) + 1);
   lmrDelay.init(NULL, ((pilotFirTaps.size - 1) / 2) + 1);
   ```
   MPX 主路延迟 pilotFir 的半长，让 M/S 路径与 pilot 路径在 38 kHz 解调时刻对齐。
5. 38 kHz 副载波恢复（:160-162）：PLL 输出**共轭**后连乘两次（×2 倍频）：
   ```cpp
   math::Conjugate::process(count, pilotPLL.out.writeBuf, pilotPLL.out.writeBuf);
   math::Multiply<complex_t>::process(count, lmrDelay.out.writeBuf, pilotPLL.out.writeBuf, ...);
   math::Multiply<complex_t>::process(count, ..., pilotPLL.out.writeBuf, ...);
   ```
   （乘两次 = (e^{jφ})² = e^{j2φ}，恢复 38 kHz 相干副载波。）
6. :177 `volk_32f_s32f_multiply_32f(lmr, lmr, 2.0f, count);` —— L-R 乘 2 归一。
7. :180-181 矩阵：`L = MPX + LMR, R = MPX - LMR`。
8. :49-51 音频 LPF：`lowPass(15000.0, 4000.0, samplerate)`，L/R 各一路独立 FIR。
9. RDS 抽头：:52 `xlator.init(NULL, -57000.0, samplerate)` 把 57 kHz 副载波搬到 DC，
   :53 `rdsResamp.init(NULL, samplerate, 5000.0)` 有理重采样到 5 kHz，
   :165-171 作为**第二输出流** `stream<complex_t> rdsOut`（:232）送出。

**MBDSDR 对照**：`demod.cpp:97-121`（DemodWFM）鉴频后给两个 tap：
- `rawMpxBuf_`（:111）：鉴频器原始输出，**去加重之前**，喂给下游 `WfmStereoDecoder`；
- `mpxBuf_`（:115）：去加重后、15k LPF 之前，57 kHz RDS 子载波仍在，喂给 `RdsDecoder`。

`wfm_stereo.h:12-22` 明确走了**另一条更省的路**：不用 pilot 带通 FIR，而是把 MPX 直接
混到 19 kHz NCO，I/Q 各过 2 ms 单极点低通，atan2(Q,I) 作鉴相，积分器把 I/Q 平滑带来的
静态正交偏差积到 0。注释原话："no pilot band-pass FIR, so no pilot-path group delay"。
M/S 两路走同一个 201-tap FIR（`wfm_stereo.h:42, 125-132`），天然群延时对齐。
**判定：已实现且真实，架构不同但等价**——SDR++ 用带通 FIR + 显式延迟线对齐，
MBDSDR 用窄带 I/Q 低通 + 同 FIR 双路省掉对齐。详见差距 G4（非缺陷，是设计选择对照）。

---

## 6. 数字解调：`demod/psk.h`、`demod/gfsk.h`（GPLv3）

**PSK（`psk.h`）链**：`psk.h:138-143`
```cpp
rrc.process(count, in, out);        // RRC 匹配滤波 (:31 taps::rootRaisedCosine)
agc.process(count, out, out);       // FastAGC (:33)
costas.process(count, out, out);    // Costas<ORDER> 载波恢复 (:34)
return recov.process(count, out, out);  // Mueller-Muller 位恢复 (:35, 抽)
```
ORDER 是模板参数（BPSK/QPSK/8PSK）。所有环参数（rrcBeta/costasBw/omegaGain/muGain）
都在 init 时传入，热切换走 `tempStop()` → 重建 RRC 抽头 → `tempStart()`。

**GFSK（`gfsk.h`）链**：`gfsk.h:131-135`
```cpp
demod.process(count, in, out);   // Quadrature 鉴频 (浮点)
rrc.process(count, out, out);    // 浮点 RRC 匹配
return recov.process(count, out, out);  // Mueller-Muller 位恢复
```
注意 RRC 放在鉴频**之后**的浮点基带，因为 GFSK 是频移键控，鉴频后变成 PAM，
RRC 在 PAM 域做匹配滤波即可。

**MBDSDR 对照**：`digital_demod.h:50-66` 只有 BPSK/QPSK：
- 链：AGC（:118 `agcGain_` 单乘）→ Costas NCO（:122-125）→ 线性插值 → 符号判决。
- **无 RRC 匹配滤波**——依赖矩形脉冲在 SPS≥4 下的自然 SNR；无 FastAGC 快捕。
- 时钟恢复默认 `timingBw = 0`（:64），注释明说"MM TED 在矩形脉冲上有偏置，
  默认关"——即自由跑，靠 channelizer 整数倍抽把 SPS 钉在 nominal。
- **无 GFSK/FSK 解调器**。详见差距 G5。

---

## 7. 信道化与重采样：`channel/rx_vfo.h`、`multirate/rational_resampler.h`（GPLv3）

**RxVFO 链**（`rx_vfo.h:89-100`）：
```cpp
xlator.process(count, in, out);                  // 1) 信道移到 DC
if (!filterNeeded) return resamp.process(...);    // 2a) 无滤波需要, 只重采样
count = resamp.process(count, out, out);          // 2b) 先重采样到 IF 速率
filter.process(count, out, out);                 // 3) 在低速率端做 LPF
```
- `filterNeeded = (_bandwidth != _outSamplerate)`（:24, :51, :65）——当带宽等于
  输出采样率时直接跳过 FIR，省 CPU。
- LPF 设计：`rx_vfo.h:117-121` `filterWidth = bw/2; lowPass(filterWidth, filterWidth*0.1, outSamplerate)`
  ——**在输出速率端设计**，因为已经抽完，抽头短。
- 偏移热切换：`setOffset()`（:72-77）只改 xlator 相位增量，不动滤波器——连续调谐不重置流。

**RationalResampler**（`rational_resampler.h:120-165`）：
- 先做 2 的幂预抽取：`predecPower = min(floor(log2(in/out)), PowerDecimator.getMaxRatio())`
  （:122-123），把高倍率抽抽取拆成 CIC 式 2^N 段。
- 剩余比例用 GCD 约：`gcd = gcd(intSr, outSr); interp = outSr/gcd; decim = intSr/gcd`（:136-138）。
- 误差报警：若实际输出率偏差 > 0.01%，stderr 警告（:142-145）。
- 四模式（:83-94）：BOTH / DECIM_ONLY / RESAMP_ONLY / NONE（= memcpy）。
- 抗混叠 LPF：`bandwidth = min(inSr, outSr)/2`，过渡带 0.1·bandwidth（:155-158）。

**MBDSDR 对照**：`channelizer.cpp:44-55`
```cpp
decimation_ = std::max(1, (int)std::round(inRateHz / outRateHz));
const double effOut = inSr_ / decimation_;
const double cutoff = std::min(bw_/2.0, effOut/2.0 * 0.85);
```
- **只有整数倍抽取**，输出实际速率 = inSr/decimation，与请求的 outSr 有舍入差。
- 无 2 的幂预抽取，无分数内插/重采样；`audio_resampler.h:15-38` 在音频末端才用
  "整数多相 + 线性插值分数级"补一刀。
- NCO：`channelizer.cpp:15-21` 每样本 `cos/sin` 现算 + 手动 2π 回绕；
  SDR++ `frequency_xlator.h:43-50` 用相量递推 `phase *= phaseDelta`，无三角函数。
详见差距 G2。

---

## 8. 差距判定片段（供整合 `sdrpp-gap-analysis.md`）

> 判定分类：**[已真实]** = 机制已落地且与上游等价；
> **[缺深度]** = 有但缺关键环节；**[未实现]** = 上游有、MBDSDR 没有。

### G1 —— 解调链内无 AGC（AM/SSB/CW 三级）
- **上游**：`am.h:34-35,103-106`（carrier AGC 在包络前作用于复数；audio AGC 在包络后）；
  `ssb.h:29,83`；`cw.h:22,61`；`psk.h:33`（FastAGC）。
- **MBDSDR**：`demod.cpp:46-59`（DemodAM 仅 abs+DC 阻断+LPF，无 AGC）；
  `demod.cpp:144-154`（DemodSSB 无 AGC）；`agc.{h,cpp}` 存在但挂在音频输出后端，
  不在解调器内；`digital_demod.h:118` 仅一个 `agcGain_` 单乘，无 attack/decay 包络。
- **判定**：**[缺深度]**。MBDSDR 把 AGC 当作"音频后处理"，SDR++ 把"载波 AGC"
  当作解调链一环——AM 在包络**前**对复数 AGC，避免弱信号下包络检波把噪声底一起抬。
- **落地价值**：AM 台强/弱切换时音量不再跳；SSB 短波信号衰减时输出电平稳定。
- **云内确定性验证**：合成 AM 测试信号 `i(t) = (1 + m(t))·e^{jω0t}`，m(t) 在 0.5–1.0
  阶跃变化，喂 DemodAM，断言输出 RMS 在 attack/decay 时间常数内收敛到 target，
  阶跃响应过冲 < 3 dB；新增 ctest TU `test_demod_agc.cpp`，不依赖硬件。

### G2 —— 信道化只有整数倍抽取，无有理重采样 / 无 2 的幂预抽取
- **上游**：`rx_vfo.h:89-100`（xlator → rational resampler → 可选 LPF）；
  `rational_resampler.h:120-165`（PowerDecimator 2^N 预抽 + GCD 约 interp/decim +
  四模式 BOTH/DECIM_ONLY/RESAMP_ONLY/NONE，误差 >0.01% 告警）。
- **MBDSDR**：`channelizer.cpp:50` `decimation_ = round(in/out)`，输出实际速率
  = inSr/decimation，与请求 outSr 有舍入差；`audio_resampler.h:15-38` 只在音频末端
  用线性插值分数级补刀。
- **判定**：**[缺深度]**。当 in=2.4 MS/s、out=48 kHz 时 decim=50 正好；但
  rtl-sdr 常见 2.048 MS/s → decim=42.67 取 43 → 实际输出 47.62 kHz，比 48 kHz
  慢 0.8%，直接后果：音频音高走调约 8‰，19 kHz pilot 漂到 18.85 kHz，
  57 kHz RDS 子载波漂到 56.6 kHz，下游 RDS 位同步（应在 57 kHz ± 几百 Hz）失锁。
- **落地价值**：rtl_tcp / 任意采样率源下音高不漂、RDS 稳定同步；
  也为将来 96/192 kHz 高保真 IF 铺路。
- **云内确定性验证**：合成 1 kHz 正弦在 2.048 MS/s 输入，经 Channelizer→DemodNFM，
  用 Goertzel 测输出主频，断言 = 1000 ± 1 Hz（当前会偏 ~8 Hz）。
  ctest TU `test_channelizer_rational.cpp`。

### G3 —— NFM/WFM 鉴频后无带宽匹配 FIR（只有单极点去加重）
- **上游**：`fm.h:30`（deviation=bw/2）、`fm.h:121`（LPF cutoff=bw/2, trans=0.1·bw,
  Nuttall 窗）、`fm.h:55-62`（`setBandwidth` 实时重设计抽头，无流中断）；
  `broadcast_fm.h:49`（音频 LPF cutoff=15 kHz, trans=4 kHz）。
- **MBDSDR**：`demod.cpp:65-66`（50 µs 去加重单极点 -20 dB/十倍程）；
  `demod.cpp:101-103`（15 kHz 音频 LPF 也是单极点一阶）；
  `demod.h:22` `IDemod::setBandwidth()` 空默认，DemodNFM 未 override。
- **判定**：**[缺深度]**。NFM 12.5 kHz 带宽时鉴频器输出包含 ±6.25 kHz 邻道能量，
  单极点去加重在 12.5 kHz 处滚降不足，对讲机集群等拥挤频段邻道串音明显；
  切带宽（6.25k/12.5k/25k）滤波器不跟随。
- **落地价值**：拥挤频段邻道抑制从 ~15 dB 提到 ≥ 40 dB；运行时切带宽即时生效。
- **云内确定性验证**：双音测试——主信道 1 kHz 调制 + 偏移 8 kHz 的同幅度邻道 FM，
  切换 bw=6.25k/12.5k/25k，断言邻道泄漏 ≥ 40 dB（FIR）vs 当前 ~15 dB。
  ctest TU `test_nfm_adjacent.cpp`。

### G4 —— WFM 立体声群延时对齐：两种架构对照（非缺陷，记录备查）
- **上游**：`broadcast_fm.h:47-48`（lprDelay/lmrDelay = pilotFir 半长）、
  :156-157（MPX 路径显式延迟对齐 pilot FIR 群延时）、:46（PLL 频率窗 18.75–19.25 kHz）。
- **MBDSDR**：`wfm_stereo.h:12-22`（不用 pilot 带通 FIR，用 I/Q 单极点低通鉴相，
  "no pilot-path group delay"）；:125-132（M/S 共用同一个 201-tap FIR，天然对齐）。
- **判定**：**[已真实]**。架构选择不同：SDR++ 用带通 FIR + 显式延迟线，
  MBDSDR 用窄带 I/Q 低通 + 同 FIR 双路。MBDSDR 路径更省、无群延时匹配 bug 面；
  SDR++ 的带通 FIR 在邻道 19 kHz 假 pilot（音频带内）处拒绝更陡。
- **落地价值**：本轮**不落地重写**；可选小改进——在 WfmStereoDecoder 前加一个
  极窄 18.8–19.2 kHz 带通（IIR 双二阶即可），把音频带内的假 pilot 进一步压掉。
- **云内确定性验证**：注入 19 kHz±500 Hz 假 pilot（幅度等于真 pilot 30%），
  断言 `WfmStereoDecoder::locked()` 不被误触发；现有 2 ms I/Q 平滑（~80 Hz 带宽）
  已能拒大部分，加 IIR 后余量从 ~6 dB 提到 ~20 dB。

### G5 —— GFSK/FSK 数字解调链缺失
- **上游**：`gfsk.h:31-34,131-135`（Quadrature 鉴频 → 浮点 RRC → Mueller-Muller
  位恢复）；`psk.h:31-35`（RRC → FastAGC → Costas → MM）。
- **MBDSDR**：`digital_demod.h:50-66` 仅 BPSK/QPSK（Costas + Gardner 默认关）；
  无 RRC 匹配滤波；`cw_decoder.cpp` 是 Morse 音频译码，不是 FSK 比特同步。
- **判定**：**[未实现]**。APRS 1200/2400 FSK、气象传真之外的窄带数字业务、
  与现有 `rds_decoder.cpp`（也是 FSK 位同步）的共用基建都缺。
- **落地价值**：解锁 APRS、144.39 MHz 报文、未来 LoRa 之前的窄带数字；
  把 RDS 的位恢复抽出来做 `GfskDemod` 基类，RDS 也受益。
- **云内确定性验证**：合成 GFSK 9600 Bd、BT=0.5 信号，断言 MM 钟恢复输出符号数
  = 输入比特数，无噪下 BER < 1e-3。ctest TU `test_gfsk_demod.cpp`。

---

## 9. 未完成 / 未覆盖项

- `loop/{agc,costas,pll,fast_agc,phase_control_loop}.h`、`clock_recovery/{mm,fd}.h`、
  `filter/{fir,decimating_fir}.h`、`taps/{root_raised_cosine,band_pass,high_pass}.h`
  仅按调用点引用，未逐行通读——下一轮（Wave1-D 信号处理子系统）应由 D agent 覆盖。
- `mod/`（调制侧）未读。
- SDR++ 实际模块侧（`root/modules/sdrpp_server/sdrpp_server.cpp` 等）如何把
  `RxVFO + Demod<stereo_t>` 拼起来、VFO 数量上限、多 VFO 扇出在哪做——
  归 Wave1-F 插件架构。
- 未跑任何确定性测试；本笔记是纯阅读笔记。
