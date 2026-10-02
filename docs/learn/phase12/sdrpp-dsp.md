# SDR++ 信号处理子系统研习（correction / channel / decimation / 平滑）

> 上游：`repos/sdrpp`（master `8c9f5ee`），**GPLv3**。本文只学机制/算法/管线划分，按干净室原则描述思路，
> 不复制源码；所有引用均给 `file:line` 与极短示意（注释性引用），落地时以自有实现重写。
> 对照基线：MBDSDR `cpp/src/dsp/`（MIT）。
> 范围：`correction/{dc_blocker}.h`、`channel/{frequency_xlator,rx_vfo}.h`、
> 多级抽取 `multirate/{power_decimator,rational_resampler,decim/plans}.h`、
> `filter/decimating_fir.h`、瀑布 FFT 平滑 `gui/widgets/waterfall.cpp`。

---

## 0. 上游信号链总览（先看管线，再看单块）

SDR++ 源端前端 `core/src/signal_path/iq_frontend.{h,cpp}` 把宽频 IQ 固定串成：

```
inBuf → PowerDecimator(幂二多级抽取) → DCBlocker(自适应去直流) → Conjugate(可选IQ翻转)
      → Splitter(扇出) → 每路 RxVFO{ NCO频移 → RationalResampler(残差有理重采样) → 可选LPF }
                       → FFT/瀑布路径
```

接线证据 `core/src/signal_path/iq_frontend.cpp:36-39`：
```cpp
preproc.addBlock(&decim,  _decimRatio > 1);   // 幂二抽取
preproc.addBlock(&dcBlock, dcBlocking);        // 去直流
preproc.addBlock(&conjugate, false);           // IQ 翻转
```
块成员声明见 `core/src/signal_path/iq_frontend.h:69-72`。
设计要点：**源端只做最大幂二抽取，残余非整数倍率交给每个 VFO 的 RationalResampler**，
VFO 之间互不影响，改带宽/频率只重建该 VFO。

---

## 1. DC 校正（correction/dc_blocker.h）

### 1.1 上游真实做法（GPLv3，仅机制引用）
`core/src/dsp/correction/dc_blocker.h:54-60`——一个**泄漏积分型自适应去直流**：
```cpp
for (int i = 0; i < count; i++) {
    out[i] = in[i] - offset;        // 减掉估计出的直流分量
    offset += out[i] * _rate;       // 用新输出再轻微更新偏移（LEAKY 均值）
}
```
- `_rate` 是归一化泄漏系数。源端取值 `genDCBlockRate = 50.0/sampleRate`
  （`core/src/signal_path/iq_frontend.h:55-57`，cpp 端 `iq_frontend.cpp:33,86`）。
  即时间常数 ≈ 采样率/50 ≈ 20 ms 量级，随采样率自适应。
- 本质是一阶 IIR 跟踪均值：`offset[n] = offset[n-1] + k·(in[n]-offset[n-1])`，
  稳态把直流/慢漂移抬成 0，又不会因为阶跃产生长拖尾。
- 模板同时支持 `float / complex_t / stereo_t`，complex 时 offset 取复数偏移
  （`dc_blocker.h:20-22`），I/Q 各去各的直流。

### 1.2 MBDSDR 现状
`cpp/src/dsp/iq_frontend.h:14-22` + `iq_frontend.cpp:41-50`——**固定陷波型 DC blocker**：
```cpp
// y[n] = x[n] - x[n-1] + R * y[n-1], R=0.998   (iq_frontend.h:13,20)
yi = xi - xPrevI_ + (float)R_ * yPrevI_;
```
- MBDSDR 用的是经典 "DC blocker"（移动差分 + 极点在 R=0.998 的环路），
  陷波在 0 Hz，R 固定 0.998，**不随采样率改变**。
- 同模块还串了一阶 RC 高通 `HighpassFilter`（`iq_frontend.cpp:29-39`，
  `alpha=RC/(RC+dt)`，cutoff 可配）与一个**协方差白化型 IQ 平衡校正**
  `IQBalanceCorrector`（`iq_frontend.cpp:63-139`，2×2 闭式特征分解做白化）。

### 1.3 差距判定
- **DC 去直流：已实现且真实**。两种都是教科书做法，MBDSDR 陷波型在 0Hz 抑制更深、
  但 R 固定不随采样率自适应；上游泄漏积分型对采样率自适应、对窄带慢漂移更友好。
- **MBDSDR 反而领先**：`IQBalanceCorrector`（`iq_frontend.cpp:97-139`）做 I/Q 正交/增益
  不平衡白化，SDR++ 此路径**没有**对应块（只有 DC + Conjugate）。这一项不用补，保留。

---

## 2. 频移 NCO（channel/frequency_xlator.h）

### 2.1 上游真实做法（GPLv3）
`core/src/dsp/channel/frequency_xlator.h:43-50`——**VOLK 向量化复数旋转**：
```cpp
volk_32fc_s32fc_x2_rotator2_32fc((lv_32fc_t*)out, (lv_32fc_t*)in,
                                 &phaseDelta, &phase, count);
```
- 初始 `phase=(1,0)`，`phaseDelta=(cos(ω), sin(ω))`（`frequency_xlator.h:16-17`），
  ω 由 `hzToRads(offset, samplerate)` 给出（`:21-23`）。
- 关键工程点：**相位状态 `phase` 跨块保持、连续**（reset 才归 1，`:35-41`），
  改 offset 只更新 `phaseDelta`、不重置相位（`:25-29`），因此**调谐无相位跳变**。
- 用 VOLK 的 rotator 内核，省掉每样本 cos/sin 与相位回绕判断。

### 2.2 MBDSDR 现状
`cpp/src/dsp/channelizer.cpp:11-21`——手写 NCO：
```cpp
dphi_ = 2π * shiftHz / sampleRateHz;
std::complex<float> v(std::cos(phase_), -std::sin(phase_)); // e^{-j·phase}
phase_ += dphi_;  // 手动 ±2π 回绕
```
- 同样连续相位、改 offset 只重配 `dphi_`（`channelizer.cpp:57-59`），语义等价。
- 但**每样本现算 cos/sin**，无向量化；这是性能差距，不是正确性差距。

### 2.3 差距判定
- **已实现但缺深度**：功能/连续性等价；缺的是 VOLK 级向量化内核。落地价值偏性能，
  云内可做"参考标量实现 vs 向量化实现输出逐位/逐 dB 对齐"的确定性测试。

---

## 3. RxVFO 一体化（channel/rx_vfo.h）

### 3.1 上游真实做法（GPLv3）
`core/src/dsp/channel/rx_vfo.h:19-33, 89-100, 117-121`——把"选道"拆成三级短路管线：
```cpp
xlator.init(NULL, -_offset, _inSamplerate);          // ① 频移：把 VFO 中心搬到 0
resamp.init(NULL, _inSamplerate, _outSamplerate);   // ② 有理重采样到目标率
filterNeeded = (_bandwidth != _outSamplerate);      // ③ 只有带宽<输出率才低通
...
inline int process(int count, const complex_t* in, complex_t* out) {
    xlator.process(count, in, out);
    if (!filterNeeded) return resamp.process(count, out, out);  // 率=带宽：跳过LPF
    count = resamp.process(count, out, out);
    { lock(filterMtx); filter.process(count, out, out); }       // 带宽受限：再LPF
    return count;
}
void generateTaps() {
    double filterWidth = _bandwidth / 2.0;
    ftaps = taps::lowPass(filterWidth, filterWidth*0.1, _outSamplerate); // 过渡带=带宽*0.1
}
```
要点：
- **频移符号取 `-offset`**（`:27,40,76`），即把目标信道搬到底带。
- **懒滤波短路**：当 `bandwidth == outRate`（整道出），根本不构造/不跑低通
  （`:24,51,65,91-93`），省算力。
- 低通抽头**显式带"过渡带宽度"参数**（`filterWidth*0.1`，`:120`），不是只给一个截止。
- 改采样率/带宽/频率都走 `tempStop/tempStart` 重建，锁 `filterMtx` 保护重抽头
  （`:35-77,96`）。

### 3.2 MBDSDR 现状
`cpp/src/dsp/channelizer.cpp:44-55, 74-106`——NCO + 单级抽取 FIR 揉在一个类里：
```cpp
decimation_ = max(1, (int)round(inRateHz / outRateHz));          // 只取整数抽取
cutoff = min(bw_/2.0, effOut/2.0 * 0.85);                        // 固定 0.85·奈奎斯特
designTaps(cutoff / inSr_);                                     // Hann 窗 sinc，无过渡带参数
...
mixed[i] = in[i] * nco_.next();   // ①频移  ②历史拼接  ③每输出点做一次整段卷积  ④留尾
```
- 非整数倍率被 `round()` 成整数抽取，**残差倍率不在 DDC 内修正**（只在音频链
  `AudioResampler` 用线性插值补，`audio_resampler.cpp:83-104`）。
- 滤波截止写死 `effOut/2*0.85`，**没有"带宽==率则跳过滤波"的短路**，也没有显式过渡带。

### 3.3 差距判定
- **已实现但缺深度**：MBDSDR 一个 Channelizer 干了 RxVFO 的事，但把"频移/重采样/低通"
  耦合在单类里，缺 ①残差有理重采样、②带宽短路、③参数化过渡带。

---

## 4. 多级抽取 / 多速率（核心性能机制）

### 4.1 上游真实做法（GPLv3）

**(a) 预计算多级抽取计划** `core/src/dsp/multirate/decim/plans.h:36-140`。
对每个 2 的幂（2,4,8,…,8192）预置一条"首级重抽头大滤波 + 末级多级 2× 抽取"计划，
如 `plan_1024 = {64,8,2}`（`plans.h:37-41`），由 Youssef Touil 的优化计划算法离线生成
（注释 `plans.h:16-22`）。

**(b) PowerDecimator** `core/src/dsp/multirate/power_decimator.h:51-67, 91-113`：
```cpp
// ratio 必须是 2 的幂
bool checkRatio(unsigned int r){ return ((r&(r-1))==0) && r && r<=getMaxRatio(); }
int planId = log2(_ratio)-1;
decim::plan plan = decim::plans[planId];          // 查表取多级方案
for (i...) decimFirs.push_back(new DecimatingFIR(..., plan.stages[i].decimation));
...
for (i=0;i<stageCount;i++){ count = fir->process(count, data, out); data = out; } // 级联
```
- 思想：**大抽取比拆成"先大因子粗抽+强滤波，再 2× 细抽"**，把每级折叠率压低、
  滤波工作量从 O(N·大M) 降到接近 O(N)。

**(c) DecimatingFIR** `core/src/dsp/filter/decimating_fir.h:45-68`——带历史缓冲的抽取卷积：
```cpp
memcpy(bufStart, in, count*sizeof(D));
for (; offset < count; offset += _decimation)
    volk_32fc_32f_dot_prod_32fc(out++, &buffer[offset], taps, size); // 每 decimation 点取一个
memmove(buffer, &buffer[count], (size-1)*sizeof(D));   // 保留 size-1 点历史
```
- `offset` 跨块推进（`:62`）保证抽取相位连续；VOLK 点积向量化。

**(d) RationalResampler（残差有理重采样）** `core/src/dsp/multirate/rational_resampler.h:120-165`：
```cpp
int predecPower = min(floor(log2(in/out)), maxRatio);   // 先吃掉最大的 2 的幂
int predecRatio = 1<<predecPower;  intSamplerate = inSr/predecRatio;
int gcd = gcd(round(intSr), round(outSr));
interp = outSr/gcd;  decim = intSr/gcd;                  // 残差走多相有理比
if (interp==decim) mode = DECIM_ONLY/NONE;              // 已抽到位就不跑重采样
rtaps = lowPass(min(in,out)/2, (min(in,out)/2)*0.1, intSr*interp);  // 多相原型低通
for(i) rtaps[i]*=interp;                                 // 内插增益归一
if (error>0.01%) fprintf("Warning resampling error over 0.01%");  // 精度自检
```
- 即 **PowerDecimator（整数幂二）+ PolyphaseResampler（有理残差）** 两段式，
  并对重采样误差 >0.01% 打警告（`:143-145`）。

### 4.2 MBDSDR 现状
- `cpp/src/dsp/channelizer.cpp:44-55, 91-99`：**单级**整数抽取 FIR，
  `decimation_=round(in/out)`，无多级折叠、无幂二计划查表。
- `cpp/src/dsp/audio_resampler.cpp:27-44, 83-104`：音频（实信号）侧先 `M=floor(in/out)`
  整数抽取，再用**最邻近线性插值**做分数步（`audio_resampler.cpp:91-98`，
  `(1-f)*list[i]+f*list[i+1]`），不是多相 FIR。
- 无"预抽取 + 残差有理"两段式，无重采样误差自检阈值。

### 4.3 差距判定
- **未实现（深度差距）**：多级幂二抽取计划 + 多相有理残差重采样。这是上游在高采样率
  （MS/s 级）下省算力、控混叠的核心；MBDSDR 单级大 FIR 在高倍率时计算与阻带都吃亏。

---

## 5. 频谱/瀑布平滑

### 5.1 上游真实做法（GPLv3）
`core/src/gui/widgets/waterfall.cpp:913-920`——**一阶 EMA 指数平滑**：
```cpp
volk_32f_s32f_multiply_32f(latestFFT,   latestFFT,   fftSmoothingAlpha, dataWidth);
volk_32f_s32f_multiply_32f(smoothingBuf, smoothingBuf, fftSmoothingBeta, dataWidth);
volk_32f_x2_add_32f(smoothingBuf, latestFFT, smoothingBuf, dataWidth);
memcpy(latestFFT, smoothingBuf, ...);
```
- 即 `y[n] = α·x[n] + (1-α)·y[n-1]`；`α=speed, β=1-speed`
  （`waterfall.cpp:1190-1194`）。开关时把当前 FFT 拷入平滑缓冲做初值
  （`waterfall.cpp:1180-1187`），避免冷启动。
- 注意：**SDR++ 并没有"倍频程/octave 平滑"**；hint 里的"倍频程平滑"在本仓库对应物是
  这个**逐频点一阶 IIR 时间平滑**（瀑布另有 SNR 的 EMA，`waterfall.cpp:924-927`）。

### 5.2 MBDSDR 现状
`cpp/src/dsp/power_spectrum.cpp:125-139`——**定长滑动平均环**：
```cpp
const int depth = (avg_==Slow) ? 16 : 4;     // 快=4帧 / 慢=16帧 硬切
ring_[ringIdx_] = lin; ringIdx_=(ringIdx_+1)%depth;
for (frame: ring) for (i) lin[i] += frame[i];
lin[i] *= 1.0f/depth;
```
- 是帧域移动平均（盒式滤波器），窗口深度两档硬编码；没有 α 连续可调，
  也没有 EMA 的"快捕慢跟"单边系数。
- 窗口 MBDSDR 反而更全：Hann/Blackman/**Flattop**（`power_spectrum.cpp:87-100`），
  上游 IQFrontEnd 只 RECT/BLACKMAN/NUTTALL 三档（`iq_frontend.h:17-21`）。

### 5.3 差距判定
- **已实现且真实**：平滑机制两家都成熟，只是盒式平均 vs EMA 的取向差异。落地优先级低。

---

## 6. 差距判定片段（供 Wave2 整合 `sdrpp-gap-analysis.md`）

| # | 上游 file:line | MBDSDR file:line | 判定 | 落地价值 | 云内可确定性验证 |
|---|---|---|---|---|---|
| G1 | `multirate/power_decimator.h:91-113` + `decim/plans.h:36-140` + `rational_resampler.h:120-165`（多级幂二抽取 + 残差多相有理重采样两段式） | `channelizer.cpp:44-55,91-99`（单级整数抽取 FIR）、`audio_resampler.cpp:83-104`（线性插值分数步） | **未实现（深度差距）** | 高：高采样率下降混叠、省算力；为多 VFO 扇出减负 | ctest：合成多音/带外单音，量化通带波纹与阻带抑制、输出样本数精确等于 round(in/out)；线性插值 vs 多相在过渡带衰减对比 |
| G2 | `channel/frequency_xlator.h:43-50`（VOLK `volk_32fc_s32fc_x2_rotator2`） | `channelizer.cpp:15-21`（每样本 `cos/sin` 手写 NCO） | **已实现但缺深度**（连续相位语义等价，缺向量化） | 中：纯性能，非功能 | ctest：标量参考输出与向量化输出在同一 `dphi_` 下逐样本对齐（容差 1e-6），连续 1e6 样本无相位跳变 |
| G3 | `channel/rx_vfo.h:24,89-100,117-121`（带宽≠率才低通的短路 + 过渡带=带宽·0.1 参数化抽头） | `channelizer.cpp:52-53`（截止写死 0.85·奈奎斯特、无短路、无过渡带参数） | **已实现但缺深度** | 中：正确按信道带宽限带、整道出省滤波 | ctest：给不同 bandwidth 参数，测阻带起始与过渡带宽度随带宽线性缩放；带宽==输出率时跳过滤波路径 |
| G4 | `correction/dc_blocker.h:54-60` + `signal_path/iq_frontend.h:55-57`（泄漏积分，`k=50/sr` 随采样率自适应） | `iq_frontend.h:14-22` + `iq_frontend.cpp:41-50`（陷波型 R=0.998 固定） | **已实现且真实**（两法皆成熟，取向不同） | 低-中：仅在多采样率档切换时受益 | ctest：注入 0Hz 直流音调，测稳态残余与建立时间；不同采样率下抑制深度一致性 |
| G5 | `gui/widgets/waterfall.cpp:913-920,1190-1194`（逐点 EMA，α 连续可调） | `power_spectrum.cpp:125-139`（4/16 帧硬切滑动平均环） | **已实现且真实**（盒式 vs EMA 取向差） | 低 | ctest：固定输入序列下，阶跃响应/建立帧数学可解析比对（EMA 闭式、盒式卷积闭式） |

> 反向提示（MBDSDR 已领先、无需补）：
> - `iq_frontend.cpp:97-139` 的 **IQ 平衡协方差白化**，SDR++ 此链路无对应块；
> - `power_spectrum.cpp:87-100` 的 **Flattop 窗**，上游仅 RECT/BLACKMAN/NUTTALL。

---

## 7. 红线与未决
- 全部为机制性引用，**未复制 GPLv3 代码**；落地需以自有实现重写并保留 MIT。
- 未在本机验证：VOLK 是否在 MBDSDR 构建链可用（G2 落地前置）；多相重采样原型窗
  （`taps/low_pass` 设计参数）需在落地时再读上游 `taps/low_pass.h` 与 `window/nuttall.h`。
- 本轮只读机制，**未改任何 cpp/ 代码，未 commit/push**（遵循云环境无凭据约定）。
