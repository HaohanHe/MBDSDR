# GNU Radio 真实 DSP 块移植笔记

> 仓库：`repos/gnuradio`（GPL-3.0-or-later, Free Software Foundation）
> 移植产物：`mbdsdr_ai/gnuradio_blocks.py`
> 往返验证：`tests/gnuradio_blocks_test.py`（7/7 通过）

本笔记记录**真读 `.cc/.h` 源码**后提取的算法与常量，以及向 MBDSDR 的移植映射。
所有关键常量均标注 `file:line`。

---

## 1. FIR 滤波器

来源：`gr-filter/lib/fir_filter.cc` / `fir_filter_with_buffer.cc`

- `set_taps()` 内部把用户抽头**反转**后存入 `d_taps`
  （fir_filter.cc:34 `std::reverse(d_taps.begin(), d_taps.end());`）。
- `filter()` 对输入滑动窗与反转抽头做点积（fir_filter.cc:91-114，VOLK 点积）。
- 数学上等价于线性卷积 `y[n] = Σ_k taps[k]·x[n-k]`，即 `numpy.convolve(x, taps)`。
- 流式版本 `fir_filter_with_buffer`（fir_filter_with_buffer.cc:69-84）用双份环形缓冲
  避免回绕：写入 `buffer[idx]` 和 `buffer[idx+ntaps]`，再与反转抽头点积。

移植：`gnuradio_blocks.py::FIRFilter`
- `filter(x)` 整段输出 == `np.convolve(x, taps)`（测试断言误差 < 1e-12）。

---

## 2. FFT 快速卷积（overlap-add）

来源：`gr-filter/lib/fft_filter.cc`，头 `gr-filter/include/.../fft_filter.h`

| 量 | 公式 | 来源 |
|---|---|---|
| fftsize | `2·2^ceil(log2(ntaps))` | fft_filter.cc:76 / :207 / :338 |
| nsamples | `fftsize − ntaps + 1` | fft_filter.cc:77 / :208 |
| tailsize | `ntaps − 1` | fft_filter.h:72 |
| 抽头归一化 | `taps · (1/fftsize)` 后再 FFT | fft_filter.cc:52 / :183 / :314 |

处理流程（`filter()` fft_filter.cc:115-149）：
1. 首 `nsamples` 槽放新样本，其余补零；
2. 正向 FFT → 频域乘 `d_xformed_taps` → 逆向 FFT；
3. 前 `tailsize` 个样本叠加 carry `d_tail`（:132-133）；
4. 输出前 `nsamples`，把末 `tailsize` 存为新 tail（:144-148）。

> 注：GNU Radio 用 FFTW（`FFTW_MEASURE` 规划，gr-fft/lib/fft.cc:185）。
> Python 移植用 `numpy.fft`；numpy 的 `ifft` 默认自带 1/N 归一化而 FFTW_BACKWARD
> 不归一化，故实现里乘回 `fftsize` 还原卷积幅值。

移植：`gnuradio_blocks.py::FFTFilter` —— 长信号与直接 FIR 输出误差 < 1e-6（实测 ~1e-12）。

---

## 3. IIR 滤波器

来源：`gr-filter/lib/iir_filter.cc:32-42`

```
acc = fftaps[0]·x[n]
for i=1..n-1: acc += fftaps[i] · x[n-i]      # 前馈
for i=1..m-1: acc += fbtaps[i] · y[n-i]      # 反馈（fbtaps[0] 隐含 =1，不参与）
```

反馈项是**加号**累加，即用户传入的 `fbtaps[i]` 对应 `−scipy.signal.lfilter` 里的 `a[i]`。
双份写入 `prev_output[idx]` 和 `prev_output[idx+m]` 避免环形回绕（:39-42）。

移植：`gnuradio_blocks.py::IIRFilter` —— 与 `scipy.signal.lfilter(b,[1,-fbtaps[1:]],x)` 一致（误差 < 1e-8）。

---

## 4. 多相任意重采样器 PFB arbitrary resampler

来源：`gr-filter/lib/pfb_arb_resampler.cc`

- `int_rate = filter_size`（分支数/内插率）(:41)。
- `set_rate(rate)`（:148-152）：
  `dec_rate = floor(int_rate/rate)`；`flt_rate = int_rate/rate − dec_rate`。
- 多相分解（:96-101）：`branch[i][j] = tmp[i + j·int_rate]`；
  `taps_per_filter = ceil(ntaps/int_rate)`（:83）。
- 微分抽头：导数滤波器 `[−1, +1]`（:112-114），`d[i]=taps[i+1]−taps[i]`，末位补 0。
- 输出主循环（:189-206）：
  ```
  o0 = fir[j](&input[i_in]);  o1 = difffir[j](&input[i_in])
  out = o0 + o1·d_acc                      # 线性插值
  d_acc += flt_rate;  j += dec_rate + floor(d_acc);  d_acc %= 1
  ```

移植：`gnuradio_blocks.py::PFBArbResampler` —— rate=0.75 时输出/输入比 0.749，
单音重采样后主峰落在新基带内、无混叠。

---

## 5. AGC2（attack / decay）

来源：`gr-analog/include/gnuradio/analog/agc2.h:64-85`（kernel::agc2_cc::scale）

逐样本：
```
output = input · gain
tmp    = |output| − reference
rate   = decay_rate;  if tmp > gain: rate = attack_rate   # :70-73
gain  -= tmp · rate                                       # :74
if gain < 0: gain = 10e-5                                 # :78-79
if max_gain>0 and gain>max_gain: gain = max_gain          # :81-83
```

默认参数（agc2.h:41-45）：
`attack_rate=1e-1, decay_rate=1e-2, reference=1.0, gain=1.0, max_gain=0(无限)`。

> 注意：`attack` 触发条件是 `tmp > gain`（信号变大、需快速压增益），安静段增益按
> `decay_rate` 慢爬——这是 GNU Radio 的真实行为，不是 bug。

移植：`gnuradio_blocks.py::AGC2` + `dsp.py::make_gr_agc2()`。阶跃后输出幅度收敛到 reference。

---

## 6. 有理重采样 Rational Resampler

来源：`gr-filter/lib/rational_resampler_impl.cc`

- 自动设计抽头 `design_resampler_filter`（:43-74）：
  Kaiser 窗 `beta=7.0`（:55），`fractional_bw` 默认 `0.4`（:124/:142），
  滤波器 `gain = interpolation`（:68）。
- 多相分解（:197-201）：`xtaps[i%nfilters][i/nfilters] = taps[i]`。
- `general_work`（:248-256）：`out = firs[ctr].filter(in); ctr += decimation;
  while ctr>=interpolation: ctr−=interpolation; in++`。

移植：`gnuradio_blocks.py::RationalResampler`（底层用 `scipy.signal.resample_poly`，
抽头按 GR 的 Kaiser β=7.0/fbw=0.4 设计）。2× 上采样后单音绝对频率不变、镜像被抑制。

---

## 7. Mueller-Mueller 位同步

来源：`gr-digital/lib/clock_recovery_mm_ff_impl.cc:82-94`；
切片 `gr-digital/lib/binary_slicer_fb_impl.cc`（`volk_32f_binary_slicer_8i` → `sign(x)`）。

逐符号（general_work）：
```
out  = interp(&in[ii], mu)
mm   = slice(last)·out − slice(out)·last     # :85 误差检测器
last = out
omega += gain_omega · mm                      # :88
omega = omega_mid + clip(omega−omega_mid, omega_lim)   # :89
mu   += omega + gain_mu · mm                  # :90
ii   += floor(mu);  mu −= floor(mu)           # :92-93
```

> GNU Radio 用 `mmse_fir_interpolator`（8 抽头、32 步，
> `gr-filter/lib/interpolator_taps.h`）做分数延迟；移植版用线性插值近似，
> 环路方程与增益标定完全一致。

移植：`gnuradio_blocks.py::ClockRecoveryMM` —— omega=4 时 800 输入样本恢复出 200 符号，
采样电平 ≈ ±1，omega 收敛回中值。

---

## 8. FFT 规划策略（参考）

来源：`gr-fft/lib/fft.cc`
- 规划策略 `FFTW_MEASURE`（fft.cc:185 / :203 / :221 / :238）。
- wisdom 文件名 `fftw_wisdom`（fft.cc:44），全局规划锁（:51-56）。

---

## 验证矩阵

| 块 | 测试 | 断言 | 结果 |
|---|---|---|---|
| FIRFilter | `test_fir_matches_numpy_convolve` | == np.convolve | PASS (err<1e-12) |
| FFTFilter | `test_fft_filter_matches_fir` | vs FIR | PASS (err~1e-12 < 1e-6) |
| IIRFilter | `test_iir_filter_matches_lfilter` | vs scipy.lfilter | PASS (err<1e-8) |
| AGC2 | `test_agc2_converges_to_reference` | 阶跃后稳定 ref | PASS |
| RationalResampler | `test_rational_resampler_2x_spectrum` | 峰位+镜像抑制 | PASS |
| PFBArbResampler | `test_pfb_resampler_no_aliasing` | 比率+无混叠 | PASS |
| ClockRecoveryMM | `test_clock_recovery_mm_locks` | 符号数+电平 | PASS |

注册到 ToolRegistry：`fir_filter / fft_filter / agc_process / rational_resample / pfb_resample`。
