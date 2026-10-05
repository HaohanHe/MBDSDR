# Phase36 G2：GNU Radio 采样率转换链深读（gr-filter）

> 精读对象：`repos/gnuradio/gr-filter/`（真读源码，干净室学机制不抄代码）。
> 我方对照：`cpp/src/dsp/{channelizer.h,.cpp,audio_resampler.h,.cpp,rational_resampler.h}`。
> 基线 HEAD 677cb4c；只学机制，不复制 GPL。

## 1. 机制总结

GNU Radio 的采样率转换是一条「原型低通 → 多相分解 → 换向器（commutator）」链，
分三层积木，可任意拼装：

- **有理数重采样 `rational_resampler`**：任意 L/M（先 GCD 约分为最简）。upsample L 倍、
  抗镜像、再 decimate M 倍。内部 = 一个原型低通被切成 L 个多相分支 FIR，换向器每出一个
  输出样本就选一个分支、并按 M 推进输入游标。
- **FIR 内核两种实现二选一**：`kernel::fir_filter`（短抽头，volk 直接点积）与
  `kernel::fft_filter_*`（长抽头，overlap-save 频域卷积）。**不是块内运行时自动切换**，
  而是两个独立块，由 flowgraph 作者在设计期选。
- **抽取链 tap 规划**：`firdes` 用 Harris 经验公式按「阻带衰减 ÷ 过渡带」反推抽头数，
  再用窗函数（Kaiser/Hann）截断 sinc。多级抽取的「多级」是集成方把大 M 拆成幂次/因子
  的组合策略，`rational_resampler` 本身是单级 GCD 最简链。

## 2. 关键算法（file:line）

### 2.1 原型滤波器设计 design_resampler_filter
`gr-filter/lib/rational_resampler_impl.cc:42-74`
- `fractional_bw` 限幅 (0,0.5)，否则 `range_error`（:48-51）。
- 默认 `beta=7.0`（Kaiser），`halfband=0.5`（:55-56）。
- 按速率比分两支算过渡带中点：`rate>=1` 时 `trans_width=halfband-fractional_bw`；
  `rate<1` 时乘 `rate` 缩放（:60-66）——**抽取方向把过渡带随速率收缩**，这是关键。
- 调 `firdes::low_pass(gain=L, Fs=L, mid_transition_band, trans_width, WIN_KAISER, beta)`
  （:68-73）。即设计在「上采样率 L×」上，增益 = L（补偿插零）。

### 2.2 GCD 约分与多相银行
`gr-filter/lib/rational_resampler_impl.cc:127-166`
- `d = gcd(interp, decim)`（:127）；无用户抽头时 `interp/=d; decim/=d`（:147-148）——
  **银行规模 = 最简后的 L**，这是省算的核心。
- 若用户自带抽头但 GCD>1，仅打日志提醒「filterbank 复杂度上升，建议先约分」（:129-137）。
- `set_relative_rate(L, M)`（:156），建 L 个空 FIR（:159-162）。

`set_taps` 把抽头长度补齐到 L 的整数倍（零填充）（:174-183）。
`install_taps` 多相切片：`xtaps[i % nfilters][i / nfilters] = taps[i]`（:200-201），
即 e_p[n]=h[n·L+p]；`set_history(nt)`（:206）。

### 2.3 换向器 general_work
`gr-filter/lib/rational_resampler_impl.cc:229-261`
```
ctr = d_ctr;                       // 跨块持久游标
while (i<nout && count<nin):
    out[i++] = d_firs[ctr].filter(in);   // 选第 ctr 个相位分支
    ctr += decimation;
    while (ctr >= interpolation): { ctr -= interpolation; in++; count++; }
d_ctr = ctr;  consume_each(count);
```
- 游标 `d_ctr` 跨块持久（:258）→ 长流无音调漂移。
- `forecast` 反推输入需求：`(nout+1)*decim/interp + history - 1`（:220-223）。

### 2.4 直接卷积内核（volk 点积）
`gr-filter/lib/fir_filter.cc:30-43`
- `set_taps` 里**翻转抽头**（:34），并预建 `d_naligned` 组对齐偏移副本（:36-42），
  用 `volk_get_alignment()` 适配任意输入对齐（:24-25）。
- `filter()` 特化走 `volk_32fc_32f_dot_prod_32fc_a`（ccf，:109-113）等对齐点积。
- `filterNdec` 每输出一个样本输入游标 += decimate（:78-89）。

### 2.5 FFT 卷积（overlap-save）
`gr-filter/lib/fft_filter.cc:72-92, 115-149`
- `compute_sizes`：`d_fftsize = 2·2^ceil(log2(ntaps))`（:76），
  `nsamples = fftsize - ntaps + 1`（:77）——这是 overlap-save 的可复用输出块长。
- 抽头只做一次前向 FFT 存 `d_xformed_taps`（:54-66），运行期每块：输入填块→前向 FFT
  （:122）→volk 频域逐点乘（:127）→逆 FFT（:129）→**叠加重叠尾部**（:131-133）→
  按 decimation 抽取输出（:136-141）→**暂存尾部**供下块（:144-148）。

### 2.6 抽头数规划 firdes
`gr-filter/lib/firdes.cc:690-714`
- Harris 经验式：`ntaps = 衰减dB · Fs / (22 · 过渡带)`，向上取奇（:697-699, :709-711）。
- `low_pass`：sinc·窗，再按 `fmax`（0频双边和）归一到指定增益（:96-116）。
- `sanity_check` 强制 `0<fa<=Fs/2`、过渡带>0（:745-757）。

## 3. 可借鉴点

1. **GCD 约分先于设计**：银行/分支数取最简 L、M，复杂度不随非约分参数膨胀——
   我方 `rational_resampler.h:47-49` 已做，一致。
2. **抽取方向过渡带随速率收缩**（:60-66）：rate<1 时 trans_width 乘 rate，避免在低频段
   过度设计；我方目前用固定 `tapsPerBranch=31`，没有按过渡带反推抽头。
3. **Harris 抽头公式**（:697）：`ntaps ≈ 衰减·Fs/(22·trans_width)` 可把「固定抽头」
   升级为「指标驱动抽头」，并自然给奇数约束（我方 channelizer.cpp:26 已保奇）。
4. **overlap-save 尾部账本**（:131-148）：频域卷积的块间连续性靠「加尾+存尾」两行，
   与我方 hist_/tail_ 跨块持久是同一思想。
5. **设计期选内核而非运行时**：短抽头直接点积、长抽头 FFT，阈值交给指标，不搞分支预测。

## 4. 我方差距判定（补齐 vs YAGNI）

| 机制 | GNU Radio | 我方现状 | 判定 |
|---|---|---|---|
| GCD 约分多相 | :127,147 | rational_resampler.h:47 | **已对齐** |
| 换向器游标跨块持久 | :258 | rational_resampler.h:99-104 + channelizer.cpp:169 | **已对齐**（offset 式等价 ctr 式） |
| 幂次预抽取+残差有理 | （集成方多级策略） | channelizer.cpp:84-99 取最大 2^k≤floor(ratio) | **已对齐且更清晰** |
| 指标驱动抽头数 | Harris :697 / Kaiser β=7 | 固定 tapsPerBranch=31/32 + Hann | **小差距，倾向补齐**（见下） |
| Kaiser 窗 | β=7.0 :55,72 | 全程 Hann（channelizer.cpp:36, rational_resampler.h:133） | **YAGNI**：语音窄带 Hann 足够 |
| FFT/overlap-save 卷积 | fft_filter.cc | 无（短 FIR 直接循环） | **YAGNI**（见反例） |
| forecast 输入预算 | :220-223 | 我方 owns 块两端，变长 process | **YAGNI** |

**补齐候选（小而有价值）**：把 rational_resampler/channelizer 的固定 `tapsPerBranch`
换成「给定阻带衰减 + 过渡带 → Harris 公式算 ntaps」。理由：当前 WFM 后级（≈2×抽取）与
宽信道下固定 31 抽头可能在过渡带变窄时欠设计；Harris 公式零依赖、一行可算，且能让
`tapsPerBranch` 从魔数变成可解释指标。Hann→Kaiser 暂不补（语音段听感无差异，徒增 β 调参面）。

## 5. 反例核查

1. **任务前提「fir_filter 在 FFT/直接间切换」——证伪**。GNU Radio 里 `kernel::fir_filter`
   只走 volk 直接点积（fir_filter.cc:91-156），`kernel::fft_filter_*` 只走 overlap-save
   （fft_filter.cc），二者是**两个独立块**；`fft_filter_ccf_impl.h` 仅包一层 FFT 内核，
   全树无 `if(ntaps>阈值) 切 FFT` 的运行时分支。「切换」发生在 flowgraph 设计期选块，
   不是块内自适应。我方若加内核选择，应做成配置项/设计期决策，而非运行时猜阈值。
2. **设计率与增益等价性核对**：GNU 用 Fs=L、gain=L（:68-69）；我方 `designRate=inSr*L`、
   `gain=L/sum`（rational_resampler.h:122,139）。数学等价，无 6dB 增益错配。
3. **GCD 警告反例**：GNU 仅在「用户自带抽头却未约分」时告警（:129-137）；我方每次都
   `gcd` 自动约分后再建银行，不存在该陷阱。
4. **奇长度约束一致**：GNU 强制奇数（firdes.cc:698）；我方 designTaps `if(T%2==0)++T`
   （channelizer.cpp:26）。对称线性相位 FIR 要求一致，无误。
5. **audio_resampler 的线性分数级是有意取舍**：我方 audio_resampler.cpp:83-104 用线性
   插值做残差分数重采样（注释明言「窄带近 1、WFM 后级 1~2，语音可闻性内无损」）。
   GNU 侧对应 `mmse_fir_interpolator`（更高阶拉格朗日）——我方此处**有意降级**，非缺陷；
   若未来上数字语音模式（M17/POCSAG 已是整数重采样路径）再评估换 MMSE。

## 6. 结论

我方采样率转换链在「GCD 约分多相 + 换向器持久游标 + 幂次预抽取残差有理 + 跨块历史」
四项核心机制上与 GNU Radio **已对齐**。真实差距仅一处：**抽头数由固定魔数改为
Harris 指标驱动**（小补齐）；FFT 卷积、Kaiser、forecast 预算三项判定 YAGNI 并附理由。
无 GPL 代码复制，全部为机制对照。
