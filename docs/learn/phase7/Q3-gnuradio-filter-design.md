# Q3 学习笔记：GNU Radio 滤波器设计（gr-filter firdes）

> 上游：[GNU Radio](https://www.gnuradio.org/)（GPLv3）。本笔记只学机制，不照抄代码。
> **落地状态：学习完成，未落地（理由见 ④）。**

## ① 上游真实做法（file:line）

核心文件：`repos/gnuradio/gr-filter/lib/firdes.cc`

### 1.1 窗函数法低通设计
`firdes::low_pass`（:77-118）和 `low_pass_2`（:33-75）：

```cpp
int ntaps = compute_ntaps(sampling_freq, transition_width, window_type, param);
vector<float> w = window(window_type, ntaps, param);   // 选窗
int M = (ntaps - 1) / 2;
double fwT0 = 2*M_PI*cutoff_freq / sampling_freq;
for (n = -M; n <= M; n++) {
    if (n == 0) taps[n+M] = fwT0/M_PI * w[n+M];
    else        taps[n+M] = sin(n*fwT0)/(n*M_PI) * w[n+M];  // sinc × window
}
// 归一化 DC 增益
double fmax = taps[M];
for (n=1; n<=M; n++) fmax += 2*taps[n+M];
gain /= fmax;
```

`low_pass` 与 `low_pass_2` 的区别：`_2` 系列接受显式 `attenuation_dB`，用 Harris 公式算 tap 数；普通系列按窗类型的最大衰减算 tap 数。

### 1.2 Tap 数估算（Harris 公式）
`compute_ntaps_windes`（:690-701）：

```cpp
int ntaps = (int)(attenuation_dB * sampling_freq / (22.0 * transition_width));
if ((ntaps & 1) == 0) ntaps++;   // 强制奇数（Type I）
```

`compute_ntaps`（:703-714）：

```cpp
double a = fft::window::max_attenuation(window_type, param);
int ntaps = (int)(a * sampling_freq / (22.0 * transition_width));
if ((ntaps & 1) == 0) ntaps++;
```

即 **N ≈ (衰减dB × fs) / (22 × 过渡带Hz)**，这是 Fred Harris《Multirate Signal Processing》的经典经验公式。

### 1.3 高通/带通/带阻
- `high_pass`（:170-）：低通原型调制到 ±Nyquist（`h[n] = -lp[n]·(-1)^n`）。
- `band_pass`（:261-）：两个低通原型相减。
- `band_reject`（:538-）：低通 + 高通并联。
- `hilbert`（:587-）：希尔伯特变换器（90° 相移）。
- `gaussian`（:616-）：高斯脉冲成形。
- `root_raised_cosine`（:641-）：RRC 脉冲成形。

### 1.4 窗库
`gr-filter/lib/...` 调用 `gr-fft/.../window_*`：Kaiser、Hann、Hamming、Blackman、Nuttall、Flattop、Gaussian、Tukey 等，每种窗有已知的最大阻带衰减（如 Hamming ~44dB，Kaiser beta 可调，Blackman ~74dB）。

## ② 我方现状（file:line）

| 能力 | 我方位置 | 现状 |
|---|---|---|
| 窗函数法低通 | `mbdsdr_ai/fir_taps.py:75-113`（`windowed_sinc`/`lowpass_taps`） | ✅ 已有 Nuttall/Hann/Hamming 窗 + sinc 截断 |
| Tap 数估算 | `fir_taps.py:64-72`（`estimate_tap_count`） | ⚠️ 硬编码 `3.8·fs/trans_width`（Nuttall ~93dB），不支持按衰减 dB 自适应 |
| 高通 | `fir_taps.py:116-128`（`highpass_taps`） | ✅ 已有（低通×(-1)^n） |
| 带通 | `fir_taps.py:131-139`（`bandpass_taps`） | ✅ 已有（两低通相减） |
| RRC | `fir_taps.py:145-180`（`root_raised_cosine_taps`） | ✅ 已有 |
| Kaiser 窗 | `mbdsdr_ai/noaa_apt_lite.py:85-114`（`_kaiser_lowpass`） | ✅ 已有独立实现（仅 APT 用），未抽到 fir_taps |
| C++ LPF | `cpp/src/dsp/apt_decoder.cpp:57-71`（Hann 窗 127-tap LPF） | ✅ 已有硬编码 Hann LPF |
| 带阻/陷波 | 无 | ❌ 未实现 |
| Hilbert | 无 | ❌ 未实现 |
| 高斯脉冲 | 无 | ❌ 未实现 |
| 按衰减 dB 选窗/tap 数 | 无 | ❌ 不支持 |

## ③ 差距判定

我方 `fir_taps.py` 已覆盖最常用的低通/高通/带通/RRC，与 GNU Radio 的核心设计流程（窗函数法 + sinc 截断 + DC 归一化）一致。差距在"工程完备度"而非"算法正确性"：

1. **Tap 数估算不灵活**：GNU Radio 允许指定目标衰减 dB（如 60dB/80dB）自动算 tap 数和选窗；我方硬编码 Nuttall ~93dB，想省 taps 时没法降。
2. **缺带阻/陷波**：对 DABCO/邻道干扰抑制有用，但当前项目模式（FM/AM/CW/APT/ADS-B）暂不需要。
3. **缺 Hilbert 变换器**：SSB 解调需要，但我方 `demod_ssb.py` 已有独立实现（`mbdsdr_ai/demod_ssb.py`）。
4. **Kaiser 窗散落**：Kaiser 设计在 `noaa_apt_lite.py` 里，没抽到 `fir_taps.py` 公共位置。

## ④ 落地建议

**未落地理由**：
- 我方已有低通/高通/带通/RRC 且测试通过（`tests/test_dsp_frontend_fir_taps.py`），覆盖当前 SDR 模式需求。
- 带阻/Hilbert/高斯脉冲属于"当前模式用不上"的能力，按 YAGNI 原则不预建。
- 真正值得做的改进是"把 Kaiser 窗抽到 fir_taps.py + 按衰减 dB 自适应选 tap 数"，但这是重构而非新能力，收益有限。

**后续若需要**：
- 在 `fir_taps.py` 加 `lowpass_by_atten(atten_db, ...)` 用 Harris 公式 `N = atten·fs/(22·trans)`。
- 把 `noaa_apt_lite._kaiser_lowpass` 提到 `fir_taps.py` 作为公共 `kaiser_lowpass()`。
- 加 `bandstop_taps()`（两低通相减的对偶：低通 + 高通并联）。
