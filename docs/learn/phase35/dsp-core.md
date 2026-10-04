# Phase35 L2：SDR++ DSP 管线核心精读

> 精读对象：`repos/sdrpp/core/src/dsp/`（真读源码，干净室学机制不抄代码）
> 对照：我方 `cpp/src/dsp/`（channelizer / audio_resampler / fft.h / agc.h / anr.h / rational_resampler.h）
> 上游基线 8c9f5ee。只学机制，GPL 代码不落地。

---

## 1. 机制总结

SDR++ 的 dsp 目录是一套「模板 Processor + stream 缓冲」的流式块库：每个块继承
`Processor<D,T>`，`run()` 从上游 stream `read()` → `process(count,in,out)` → `flush()` →
`out.swap()`。配置热更新统一走 `recursive_mutex` + `tempStop()/tempStart()`
（见 filter/decimating_fir.h:20-25），保证改参数时不撕裂历史缓冲。

五条核心机制线：

1. **抽取 FIR**：不是「先滤波后抽 采」，而是把卷积定位在「只需要输出的那几个时刻」，
   用 VOLK 点积直接算出抽取点的输出，其余样本不算（filter/decimating_fir.h:51-61）。
2. **AGC 平滑**：包络跟随用非对称 attack/decay 单极点，外加「预测削波→回看块内峰值」
   的前瞻校正（loop/agc.h:83,91-104）。FastAGC 是其极简反馈版（loop/fast_agc.h:75）。
3. **多速率链**：两级——先 **2 的幂多级预抽取**（PowerDecimator，吃预生成 tap plan），
   再 **GCD 约分后的多相有理数重采样**（RationalResampler + PolyphaseResampler）。
4. **噪声抑制**：只有很轻的两路——IQ 冲激噪声归一（NoiseBlanker）和整块能量静噪
   （PowerSquelch）；没有谱减法/维纳这类重型 ANR。
5. **FFT**：**SDR++ 不自实现 FFT**，直接包 FFTW3f 单精度，窗函数 + 抽稀 keep/skip 控
   速率，VOLK 出功率谱。

---

## 2. 关键算法（file:line）

### 2.1 抽取 FIR — filter/decimating_fir.h
- 历史缓冲前置：`bufStart = &buffer[taps.size-1]`，输入 memcpy 到工作区
  （filter/fir.h:24-26；decimating_fir.h:47）。
- **只在抽取点做卷积**：`for(; offset<count; offset+=_decimation)` 内才调
  `volk_32fc_32f_dot_prod_32fc`（decimating_fir.h:51-56）。`offset` 跨块保留
  （:86），改 taps/decimation/reset 时清零（:22,:32,:40）。
- 类型分发用 `if constexpr` 在编译期展开三种数据/tap 组合
  （real×real / complex×real / complex×complex，:52-60），零运行期分支。
- 块尾 `memmove` 保留 taps-1 个未用样本当历史（:65）。

### 2.2 AGC — loop/agc.h
- 非对称包络单极点：
  `amp = (inAmp>amp) ? amp*(1-attack)+inAmp*attack : amp*(1-decay)+inAmp*decay`（:83），
  系数 `invAttack/invDecay` 预存（:16-18）。
- `gain = min(setPoint/amp, maxGain)`（:84）；`inAmp==0` 时 gain=1（:87）。
- **前瞻防削波**：若 `inAmp*gain > maxOutputAmp`，扫本块剩余样本取峰值 maxAmp，
  直接把 amp 钉到峰值再定 gain（:91-104）——先压低本块包络再放大，避免瞬态削波。

### 2.3 FastAGC — loop/fast_agc.h
- 输出反馈式：`out=in*gain`，再 `gain += (setPoint - |out|)*rate`，只对上界 maxGain
  做钳位（:63-76）。无 attack/decay 非对称、无下界钳——是 AGC 的退化简化版。

### 2.4 多速率 — multirate/
- **PowerDecimator**（power_decimator.h:91-108）：ratio 必须 2 的幂
  （`ratio&(ratio-1)==0`，:110-113），`planId=log2(ratio)-1` 查表（:97），
  每级一个 DecimatingFIR，多级串在同一 out 缓冲里就地跑（:61-65）。
- **预生成 tap plan**（decim/plans.h:36-140）：如 plan_1024 = {64,8,2} 三级
  （:37-41），由 "magic optimized FIR script"（Youssef Touil plan 生成）离线产出，
  多级分担滤波量、总抽数远少于单级。
- **RationalResampler**（rational_resampler.h:120-165）：
  `predecPower = min(floor(log2(in/out)), maxRatio)`（:122）做幂预抽取；
  剩余 intSR/outSR 用 `std::gcd` 约成 interp/decim（:136-138）；
  误差 >0.01% 打警告（:141-145）；tap 用 `lowPass(min(in,out)/2, bw*0.1, intSR*interp)`
  并 ×interp 补插增益（:154-159）。
- **PolyphaseResampler**（polyphase_resampler.h:75-92）：commutator 分支less前进
  `phase+=decim; offset+=phase/interp; phase%=interp`（:85-91），每输出一次与
  `phases[phase]` 做点积。
- **多相 bank 拆分**（polyphase_bank.h:31-33）：原型 h 按相位交错切分，相位索引做了
  反向 `(phaseCount-1)-(i%phaseCount)`。

### 2.5 噪声抑制 — noise_reduction/
- **NoiseBlanker**（noise_blanker.h:44-51）：单极点平均 amp，`excess=inAmp/amp`，
  超阈值则 `gain=1/excess`——把冲激脉冲压回运行均值。逐样点、复数 IQ。
- **PowerSquelch**（power_squelch.h:33-49）：volk 求幅 + 累加器得整块均值，
  dB 过阈整块透传否则整块清零。作者自己标 `// TODO: Rewrite better!!!!!`（:4）。

### 2.6 FFT — 不自实现，包 FFTW3f
- 计划：`fftwf_plan_dft_1d` + `FFTW_ESTIMATE`（signal_path/iq_frontend.cpp:60-62）。
- 流程：volk 加窗（:252）→ `fftwf_execute`（:255）→
  `volk_32fc_s32f_power_spectrum_32f` 按 fftSize 归一（:262）。
- FFT 速率与采样率解耦：reshape keep/skip（iq_frontend.h:60-62 `genReshapeParams`）。
- 窗：rectangular/blackman/nuttall（iq_frontend.cpp:50-57）。

### 2.7 math 小工具 — math/fast_atan2.h
- 经典有理逼近 atan2：`r=(x-|y|)/(x+|y|)` + 象限翻转（:13-24），替 libm atan2f。

---

## 3. 可借鉴点（机制层）

1. **抽取点处卷积**：多相/抽取本质是「只算需要的输出时刻」，避免每样点全卷积——
   我方 rational_resampler 已是这个 commutator 形态（rational_resampler.h:88-102），
   方向一致。
2. **AGC 前瞻防削波**（loop/agc.h:91-104）：块级先扫峰值、先压包络再放大，比单纯
   maxGain 上限更能挡住 AGC 突调时的瞬态削波。机制便宜、收益直接。
3. **多级幂抽取 tap plan**：高抽取比（如 MHz→几十 kHz）下，多级分担滤波器总抽数，
   比单级大 FIR 省算力；离线生成、运行期只读表。
4. **GCD 约分 + 误差告警**：有理数重采样先约分 interp/decim，再校验相对误差——
   防止把失配采样率默默当成「差不多」。
5. **配置热更新范式**：mutex + tempStop/tempStart，改 taps/比率时先停流、迁历史、
   再启流，做到无缝过渡（filter/fir.h:43-49 历史迁移是亮点）。

---

## 4. 我方差距判定（补齐 vs YAGNI）

| 点 | SDR++ 机制 | 我方现状（cpp/src/dsp/） | 裁决 | 理由 |
|---|---|---|---|---|
| AGC 前瞻防削波 | loop/agc.h:91-104 | agc.h 有 maxGain 上限（DefaultMaxGain=12，:24）但无块内峰值前瞻 | **补齐** | 机制小（块级一次峰值扫描），能挡瞬态削波；Agc 本就是 block 处理，易加 |
| 多级幂抽取 tap plan | decim/plans.h 多级 {64,8,2} | channelizer 单级抽取 FIR + rational 残差 | **YAGNI（先 profile）** | 现 RTL~2.4MHz→信道单级尚可；只有实测 channelizer 成为热点、且大抽取比时才值得多级化 |
| VOLK/SIMD 点积 | volk_32fc_32f_dot_prod | rational_resampler.h:94-95 纯标量 `acc+=ph[k]*data[..]` | **YAGNI** | 引 VOLK 是重依赖+编译链负担；标量对 48k 音频/窄带足够，等 profiling 证明热点再说 |
| FFTW | iq_frontend.cpp:60 包 FFTW3f | fft.cpp 手写 radix-2 Cooley-Tukey，std::complex，无外部依赖 | **YAGNI** | ANR 帧 512/1024、谱分析用自写 FFT 足够；FFTW(LGPL) 重且非必要，保持纯 std |
| FastAGC 单率反馈 | loop/fast_agc.h:75 | agc.h 已有更优 attack/decay 非对称 + carrier AGC | **YAGNI** | FastAGC 是退化简化，不是升级；我方已覆盖且更细 |
| NoiseBlanker 冲激归一 | noise_blanker.h:44-51 | 已有 noise_blanker.{h,cpp}（anr.h:13 引用） | **无差距** | 机制已在 |
| PowerSquelch 整块能量静噪 | power_squelch.h:33-49 | 已有 squelch.cpp | **无差距/勿抄** | SDR++ 自己标 TODO 重写，整块均值无迟滞，不借鉴 |
| 谱减/维纳 ANR | SDR++ 无（仅冲激+静噪） | anr.h：STFT-Wiener、CV-VAD、decision-directed | **我方领先** | 我方 ANR 比 SDR++ 重型得多，无需补 |
| 多相 commutator | polyphase_resampler.h:85-91 | rational_resampler.h:98-101 同款分支less推进 | **无差距** | 机制已对齐，仅实现语言不同 |
| fastAtan2 有理逼近 | math/fast_atan2.h:13-24 | demod/相位相关处按需用 libm | **YAGNI** | 非热点；需要时再加查表/逼近，现在加是过度优化 |

---

## 5. 反例核查（我方「以为有/以为不同」的纠正）

1. **「SDR++ 有自己的 FFT」——错**。math/ 下没有 fft 模块，FFT 完全外包 FFTW3f
   （iq_frontend.cpp:60-62）。我方手写 radix-2 并非「落后于 SDR++ 的自研 FFT」，
   而是与 SDR++ 走了不同依赖路线；不要为「对齐 SDR++」而引入 FFTW。
2. **PowerSquelch 不是范本**：SDR++ 源码自带 `// TODO: Rewrite better!!!!!`
   （power_squelch.h:4）。整块均值能量门、无迟滞/去抖，会在门限附近 chatter。
   我方 squelch 勿向它看齐。
3. **AGC 前瞻并非免费**：loop/agc.h:93-101 是「每样点预测 + 命中才内层扫整块」，
   最坏 O(n²)。我方若补齐，应按块级一次性 `max_element` 峰值扫描，而不是逐样点嵌套。
4. **生产路径留调试打印**：rational_resampler.h:144 `fprintf(stderr, ...)`、:162
   `printf("[Resamp]...")` 在每次 reconfigure 都打。我方落地时必须走日志通道，
   不许 stdout 直打——这是上游可诟病处，不照抄。
5. **多相相位反向索引**（polyphase_bank.h:32 `(phaseCount-1)-(i%phaseCount)`）与我方
   正向切分 `e_p[n]=h[n*L+p]`（rational_resampler.h:145-151）都正确，只是相位排布
   约定不同；我方注释已写明标准形式，非 bug，勿「对齐」成上游写法。

---

## 6. 结论
SDR++ dsp 核心真正值得拿的是 **「抽取点处卷积 + 块级峰值前瞻 AGC + GCD 约分有理
重采样」** 三件机制；我方在 rational_resampler / channelizer / anr 上已基本对齐甚至
（ANR）领先。唯一建议本轮补齐的小项是 **AGC 块级峰值前瞻防削波**；多级 tap plan、
VOLK、FFTW 均判 YAGNI，等 profiling/依赖权衡再动。
