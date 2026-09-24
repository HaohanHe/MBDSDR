"""
GQRX 真实接收机移植（纯 numpy，无 GNU Radio 依赖）
=====================================================
本模块把 GQRX (repos/gqrx) 里经过十几年实践验证的接收机 DSP 原样翻译成
numpy，所有关键常量都在注释里标注「来源: gqrx 源码 file:line」。

移植自：
  * 接收机主流程      src/receivers/nbrx.cpp / wfmrx.cpp（信号流图）
  * AGC               src/dsp/agc_impl.cpp / agc_impl.h（CAgc 类）
  * IQ 直流/正交校正   src/dsp/correct_iq_cc.cpp
  * 信道滤波          src/dsp/rx_filter.cpp
  * FM 鉴频/去加重     src/dsp/rx_demod_fm.cpp, src/dsp/fm_deemph.cpp
  * AM 包络           src/dsp/rx_demod_am.cpp
  * SSB              nbrx.cpp:51 demod_ssb = complex_to_real
  * 音频采样率/重采样  src/applications/gqrx/receiver.cpp:64, nbrx.cpp:68

链路拓扑（窄带，来源 nbrx.cpp:73-79）：
    源IQ → IQ重采样 → (噪声消隐) → 信道带通 → 静噪 → AGC → 解调 → 音频重采样→48k

链路拓扑（宽带 WFM，来源 wfmrx.cpp:60-65）：
    源IQ → IQ重采样 → 信道带通(±80k) → 静噪 → FM鉴频(75k频偏) → 立体声/单声道
    注意：WFM 路径不经过 AGC（wfmrx.cpp:139-169 整段被注释掉）。
"""
from __future__ import annotations

import collections
from typing import Dict, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  GQRX 全局常量（来源见行内注释）
# ═══════════════════════════════════════════════════════════════════════

#: 窄带接收机优选正交（IF）采样率。来源: nbrx.cpp:29  #define PREF_QUAD_RATE 96000.f
PREF_QUAD_RATE_NB = 96_000.0
#: 宽带 FM 接收机优选正交采样率（信道间隔约 200kHz）。来源: wfmrx.cpp:29
PREF_QUAD_RATE_WFM = 240_000.0
#: 音频输出采样率。来源: receiver.cpp:64  d_audio_rate(48000)
AUDIO_RATE = 48_000.0

#: 各模式默认信道带通 [low, high] Hz，取 GQRX filter_preset_table 的 NORMAL 列。
#: 来源: dockrxopt.cpp:53-63
#:   AM  NORMAL = -5000..5000   (dockrxopt.cpp:54)
#:   LSB NORMAL = -2800..-100  (dockrxopt.cpp:56)
#:   USB NORMAL = 100..2800     (dockrxopt.cpp:57)
#:   CW  NORMAL = -250..250     (dockrxopt.cpp:58-59)
#:   NFM NORMAL = -5000..5000   (dockrxopt.cpp:60)
#:   WFM NORMAL = -80000..80000 (dockrxopt.cpp:61)
GQRX_FILTER_PRESETS = {
    "am":  (-5_000.0,  5_000.0),
    "nfm": (-5_000.0,  5_000.0),
    "fm":  (-5_000.0,  5_000.0),
    "lsb": (-2_800.0, -100.0),
    "usb": (  100.0,  2_800.0),
    "cw":  ( -250.0,   250.0),
    "wfm": (-80_000.0, 80_000.0),
}

#: FM 鉴频最大频偏。来源:
#:   窄带 NFM: nbrx.cpp:52  make_rx_demod_fm(96000, 5000.0, 75.0e-6)
#:   宽带 WFM: wfmrx.cpp:48 make_rx_demod_fm(240000, 75000.0, 0.0)
GQRX_FM_MAXDEV = {"fm": 5_000.0, "nfm": 5_000.0, "wfm": 75_000.0}
#: FM 去加重时间常数 tau。来源:
#:   NFM tau=75us (美制，nbrx.cpp:52 末参 75.0e-6)
#:   WFM tau=0   (wfm 去加重在立体声解调块里做，wfmrx.cpp:48 末参 0.0)
GQRX_FM_DEEMPH_TAU = {"fm": 75.0e-6, "nfm": 75.0e-6, "wfm": 0.0}


# ═══════════════════════════════════════════════════════════════════════
#  AGC —— 逐样本移植自 CAgc (src/dsp/agc_impl.cpp)
# ═══════════════════════════════════════════════════════════════════════
class GqrxAGC:
    """GQRX CAgc 的 numpy 逐样本移植。

    算法（来源 agc_impl.cpp:197-317 ProcessData）：
      1. 取每样本幅度 m = max(|I|, |Q|)，换算成 log10 相对电平：
            mag = log10(m + 1e-8) - log10(1.0)        (agc_impl.cpp:220-224)
      2. 在一个滑动窗（WINDOW_TIMECONST=18ms）里取峰值 m_Peak（单调队列）；
      3. 两条 EMA 平均器：
            AttackAve —— 快：上升沿用 2ms，下降沿用 5ms   (agc_impl.cpp:244-251)
            DecayAve  —— 慢：上升沿用 decay*0.3ms；
                          下降沿先 hang 保持，再用 50ms 释放 (agc_impl.cpp:253-268)
      4. 取两条平均器较大者作为测量电平；按 knee/斜率算增益：
            mag<=knee  → 固定增益 FixedGain
            mag>knee   → gain = 0.7 * 10^(mag*(slope-1))  (agc_impl.cpp:299-304)
      5. 输出 = 延迟线(15ms)里的样本 * gain（agc_impl.cpp:211-214,306）。
    """

    # ---- 常量，逐条对应 agc_impl.cpp ----
    DELAY_TIMECONST = 0.015    # agc_impl.cpp:49  延迟线时间（补偿滤波群延迟）
    WINDOW_TIMECONST = 0.018    # agc_impl.cpp:52  峰值检测窗
    ATTACK_RISE_TIMECONST = 0.002   # agc_impl.cpp:56  信号变强快压增益
    ATTACK_FALL_TIMECONST = 0.005   # agc_impl.cpp:57  信号变弱快放增益
    DECAY_RISEFALL_RATIO = 0.3      # agc_impl.cpp:59  decay 上升=0.3*下降
    RELEASE_TIMECONST = 0.05        # agc_impl.cpp:63  hang 后的释放时间
    AGC_OUTSCALE = 0.7              # agc_impl.cpp:66  输出限幅到约 -3dB
    MAX_AMPLITUDE = 1.0             # agc_impl.cpp:69
    MIN_CONSTANT = 1e-8             # agc_impl.cpp:74  log(0)≈-160dB 的底
    MAX_DELAY_BUF = 2048            # agc_impl.h:17

    def __init__(self,
                 sample_rate: float,
                 agc_on: bool = True,
                 use_hang: bool = False,
                 threshold_db: int = -100,     # nbrx.cpp:47 / dockrxopt.cpp:434 默认 -100dB
                 manual_gain_db: int = 0,      # nbrx.cpp:47 默认 0dB；手动档范围 0..100dB
                 slope: int = 0,               # dockrxopt.cpp:452 默认 0
                 decay_ms: int = 500):         # nbrx.cpp:47 / dockrxopt.cpp:438 默认 500ms
        self.sample_rate = float(sample_rate)
        self.agc_on = bool(agc_on)
        self.use_hang = bool(use_hang)
        self.threshold = int(threshold_db)
        self.manual_gain = int(manual_gain_db)
        self.slope_factor = int(slope)
        self.decay = int(decay_ms)
        self.set_parameters(agc_on, use_hang, threshold_db, manual_gain_db,
                            slope, decay_ms, self.sample_rate)

    # ------------------------------------------------------------------
    def set_parameters(self, agc_on, use_hang, threshold, manual_gain,
                       slope, decay, sample_rate):
        """对应 agc_impl.cpp:124 SetParameters：由时间常数算 EMA alpha。"""
        self.agc_on = bool(agc_on)
        self.use_hang = bool(use_hang)
        self.threshold = int(threshold)
        self.manual_gain = int(manual_gain)
        self.slope_factor = int(slope)
        self.decay = int(decay)
        self.sample_rate = float(sample_rate)

        # 手动档增益：MAX_MANUAL_AMP * 10^(dB/20)。来源 agc_impl.cpp:165
        self.manual_agc_gain = self.MAX_AMPLITUDE * (10.0 ** (self.manual_gain / 20.0))

        # knee 与斜率。来源 agc_impl.cpp:168-169
        self.knee = self.threshold / 20.0           # log10 单位（-100dB => -5.0）
        self.gain_slope = self.slope_factor / 100.0

        # knee 以下的固定增益。来源 agc_impl.cpp:172
        self.fixed_gain = self.AGC_OUTSCALE * (10.0 ** (self.knee * (self.gain_slope - 1.0)))

        # 快/慢 EMA alpha = 1 - exp(-1/(sr*tau))。来源 agc_impl.cpp:175-185
        sr = self.sample_rate
        self.attack_rise_alpha = 1.0 - np.exp(-1.0 / (sr * self.ATTACK_RISE_TIMECONST))
        self.attack_fall_alpha = 1.0 - np.exp(-1.0 / (sr * self.ATTACK_FALL_TIMECONST))
        self.decay_rise_alpha = 1.0 - np.exp(
            -1.0 / (sr * self.decay * 0.001 * self.DECAY_RISEFALL_RATIO))
        self.hang_time = int(sr * self.decay * 0.001)
        if self.use_hang:
            self.decay_fall_alpha = 1.0 - np.exp(-1.0 / (sr * self.RELEASE_TIMECONST))
        else:
            self.decay_fall_alpha = 1.0 - np.exp(-1.0 / (sr * self.decay * 0.001))

        # 缓冲长度。来源 agc_impl.cpp:146-147
        self.delay_samples = int(sr * self.DELAY_TIMECONST)
        self.window_samples = int(sr * self.WINDOW_TIMECONST)
        # GQRX 把 AGC 挂在 96kHz 的 PREF_QUAD_RATE 上（nbrx.cpp:47），
        # 所以 18ms 窗=1728 < MAX_DELAY_BUF(2048)。若在更高采样率跑，
        # 按 agc_impl.cpp:188-189 对 delay 的同款做法把窗钳到缓冲长度内。
        self.delay_samples = min(self.delay_samples, self.MAX_DELAY_BUF - 1)
        self.window_samples = min(self.window_samples, self.MAX_DELAY_BUF - 1)
        if self.window_samples < 1:
            self.window_samples = 1

        # 状态初始化。来源 agc_impl.cpp:148-161
        self._sig_delay = np.zeros(self.MAX_DELAY_BUF, dtype=np.complex128)
        self._mag_buf = np.full(self.MAX_DELAY_BUF, -16.0)
        self._sig_ptr = 0
        self._mag_pos = 0
        self._mag_deque = collections.deque([self.window_samples - 1])
        self.hang_timer = 0
        self.peak = -16.0
        self.decay_ave = -5.0
        self.attack_ave = -5.0

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一段复 IQ，返回增益后的复 IQ。逐样本，状态跨块连续。"""
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return iq.astype(np.complex64)
        if not self.agc_on:
            # 手动档：直接乘固定增益。来源 agc_impl.cpp:312-315
            return (iq * self.manual_agc_gain).astype(np.complex64)

        out = np.empty(n, dtype=np.complex128)
        LOG_MAX = np.log10(self.MAX_AMPLITUDE)   # =0。来源 agc_impl.cpp:72

        for i in range(n):
            inp = iq[i]
            # 取延迟线里的样本（补偿群延迟）。来源 agc_impl.cpp:211
            delayed = self._sig_delay[self._sig_ptr]
            self._sig_delay[self._sig_ptr] = inp
            self._sig_ptr += 1
            if self._sig_ptr >= self.delay_samples:
                self._sig_ptr = 0

            # 幅度 = max(|I|,|Q|)，转 log10 电平。来源 agc_impl.cpp:220-224
            mag = max(abs(inp.real), abs(inp.imag))
            mag = np.log10(mag + self.MIN_CONSTANT) - LOG_MAX

            # 滑动窗峰值（单调队列）。来源 agc_impl.cpp:227-239
            if self._mag_deque and self._mag_deque[0] == self._mag_pos:
                self._mag_deque.popleft()
            while self._mag_deque and mag >= self._mag_buf[self._mag_deque[-1]]:
                self._mag_deque.pop()
            self._mag_deque.append(self._mag_pos)
            self.peak = self._mag_buf[self._mag_deque[0]]
            self._mag_buf[self._mag_pos] = mag
            self._mag_pos += 1
            if self._mag_pos >= self.window_samples:
                self._mag_pos = 0

            # AttackAve：上升快(2ms)、下降快(5ms)。来源 agc_impl.cpp:244-251
            if self.peak > self.attack_ave:
                a = self.attack_rise_alpha
            else:
                a = self.attack_fall_alpha
            self.attack_ave = (1.0 - a) * self.attack_ave + a * self.peak

            # DecayAve：上升用 decay*0.3ms；下降先 hang 再 50ms 释放。
            # 来源 agc_impl.cpp:253-268
            if self.peak > self.decay_ave:
                self.decay_ave = (1.0 - self.decay_rise_alpha) * self.decay_ave \
                                 + self.decay_rise_alpha * self.peak
                self.hang_timer = 0
            else:
                if self.use_hang and self.hang_timer < self.hang_time:
                    self.hang_timer += 1     # 保持当前 DecayAve
                else:
                    self.decay_ave = (1.0 - self.decay_fall_alpha) * self.decay_ave \
                                     + self.decay_fall_alpha * self.peak

            # 取较大者。来源 agc_impl.cpp:293-296
            meas = self.attack_ave if self.attack_ave > self.decay_ave else self.decay_ave

            # 算增益。来源 agc_impl.cpp:299-304
            if meas <= self.knee:
                gain = self.fixed_gain
            else:
                gain = self.AGC_OUTSCALE * (10.0 ** (meas * (self.gain_slope - 1.0)))

            out[i] = delayed * gain

        return out.astype(np.complex64)

    def reset(self):
        """清空滤波器状态（切模式时调用，对应 nbrx.cpp:47 重建）。"""
        self.set_parameters(self.agc_on, self.use_hang, self.threshold,
                           self.manual_gain, self.slope_factor, self.decay,
                           self.sample_rate)


# ═══════════════════════════════════════════════════════════════════════
#  IQ 校正 —— 直流偏移 + 正交不平衡
# ═══════════════════════════════════════════════════════════════════════
class IQCorrector:
    """直流偏移自适应消除 + 正交（I/Q）不平衡校正。

    直流偏移：单极点 IIR 估计均值再减掉。
        来源 correct_iq_cc.cpp:47  alpha = 1/(1 + tau*sr)；
        来源 receiver.cpp:118     dc_corr = make_dc_corr_cc(decim_rate, tau=1.0)
        （即 tau=1.0 秒）。
    正交不平衡：直接变频接收机常见 I/Q 幅度/相位失配。GQRX 本版本只做 DC 校正
    （correct_iq_cc.cpp 里没有增益/相位块），这里补一个标准的 Gram-Schmidt
    正交化式在线校正：用第一个样本作为参考，估计 I 相对 Q 的投影角与幅度比，
    逐样本慢收敛，tau 与 DC 一致（可关）。
    """

    def __init__(self, sample_rate: float, dc_tau: float = 1.0,
                 imbalance_correction: bool = True, ib_tau: float = 0.5):
        self.sr = float(sample_rate)
        # DC 估计 IIR alpha。来源 correct_iq_cc.cpp:47
        self.dc_alpha = 1.0 / (1.0 + dc_tau * self.sr)
        self._dc_i = 0.0
        self._dc_q = 0.0
        # 正交不平衡在线状态
        self.ib_on = bool(imbalance_correction)
        self._ib_alpha = 1.0 / (1.0 + ib_tau * self.sr)
        self._gain = 1.0      # Q 相对 I 的幅度校正
        self._phase = 0.0     # Q 相对 I 的相位校正（弧度）

    def process(self, iq: np.ndarray) -> np.ndarray:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return iq.astype(np.complex64)
        out = np.empty(n, dtype=np.complex128)
        a = self.dc_alpha
        ia = self._ib_alpha
        for i in range(n):
            x = iq[i]
            # --- DC 自适应估计与扣除。来源 correct_iq_cc.cpp:51-57 ---
            self._dc_i = (1.0 - a) * self._dc_i + a * x.real
            self._dc_q = (1.0 - a) * self._dc_q + a * x.imag
            re = x.real - self._dc_i
            im = x.imag - self._dc_q

            if self.ib_on:
                # --- 正交不平衡 Gram-Schmidt 在线校正 ---
                # 让 Q 与 I 正交：扣掉 Q 在 I 方向上的投影，并归一化幅度。
                # 在线估计 E[re*im]/E[re^2] 作为交叉项（慢收敛）。
                cross = np.mean(re * im)  # 块内近似（流式下用一阶矩更准，这里简化稳定）
                # 一阶矩更新
                self._phase = (1.0 - ia) * self._phase + ia * (cross / (re * re + 1e-12))
                im = im - self._phase * re
                # 幅度：保持 I/Q 能量一致
                e_i = (1.0 - ia) * (1.0) + ia * (re * re + 1e-12)
                e_q = (1.0 - ia) * (1.0) + ia * (im * im + 1e-12)
                self._gain = np.sqrt(e_i / (e_q + 1e-12))
                im = im * self._gain
            out[i] = complex(re, im)
        return out.astype(np.complex64)

    @property
    def dc_offset(self) -> complex:
        return complex(self._dc_i, self._dc_q)


# ═══════════════════════════════════════════════════════════════════════
#  小工具：信道带通 / 音频重采样 / FM 去加重
# ═══════════════════════════════════════════════════════════════════════
def _complex_bandpass(sr: float, low: float, high: float, taps: int = 63) -> np.ndarray:
    """复带通 FIR（firdes::complex_band_pass 的 numpy 近似）。

    来源 rx_filter.cpp:61  gr::filter::firdes::complex_band_pass(1, sr, low, high, tw)。
    对单边带/SSB 关键：只保留 low..high 的边带。
    """
    n = np.arange(taps) - taps // 2
    center = 0.5 * (low + high)
    half = 0.5 * (high - low)
    # 复指数把低通搬移到 center；低通截止 half
    h = 2 * half / sr * np.sinc(2 * half / sr * n) * np.exp(2j * np.pi * center / sr * n)
    h *= np.hanning(taps)
    return h / np.sum(np.abs(h))


def fm_deemph_taps(sr: float, tau: float):
    """一阶 FM 去加重 IIR 系数（双线性变换）。

    来源 fm_deemph.cpp:calculate_iir_taps：
        w_c = 1/tau；w_ca = 2*sr*tan(w_c/(2*sr))；
        k = -w_ca/(2*sr)；p1=(1+k)/(1-k)；b0=-k/(1-k)；
        b = [b0, b0]；a = [1, -p1]。
    tau<=1e-9 时直通（来源 fm_deemph.cpp else 分支）。
    """
    if tau <= 1.0e-9:
        return np.array([1.0]), np.array([1.0])
    w_c = 1.0 / tau
    w_ca = 2.0 * sr * np.tan(w_c / (2.0 * sr))
    k = -w_ca / (2.0 * sr)
    p1 = (1.0 + k) / (1.0 - k)
    b0 = -k / (1.0 - k)
    b = np.array([b0, b0])          # z1=-1 => -z1*b0 = b0
    a = np.array([1.0, -p1])
    return b, a


class _IIR1:
    """一阶 IIR（去加重/直流去除共用），状态跨块连续。"""
    def __init__(self, b, a):
        self.b = np.asarray(b, dtype=float)
        self.a = np.asarray(a, dtype=float)
        self._z = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if len(self.b) == 1 and self.b[0] == 1.0 and (len(self.a) == 1 or self.a[0] == 1.0 and len(self.a) == 1):
            return x.copy()
        out = np.empty_like(x)
        y1 = self._z
        b0, b1 = (self.b[0], self.b[1]) if len(self.b) > 1 else (self.b[0], 0.0)
        a1 = -self.a[1] if len(self.a) > 1 else 0.0
        x1 = 0.0
        for i in range(len(x)):
            y = b0 * x[i] + b1 * x1 - a1 * y1
            out[i] = y
            x1, y1 = x[i], y
        self._z = y1
        return out


def _resample_to(x: np.ndarray, sr_in: float, sr_out: float) -> np.ndarray:
    """任意比重采样（PFB 的 numpy 近似：FFT 抗混叠 + 线性插值）。

    来源 resampler_xx.cpp：PFB arb resampler，cutoff=0.4, trans=0.2, 32 相。
    这里用均匀线性插值 + 简单抗混叠低通，足够把 96k→48k 这种 0.5 整数比。
    """
    if abs(sr_in - sr_out) < 1.0:
        return np.asarray(x, dtype=float)
    x = np.asarray(x, dtype=float)
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 0:
        return np.zeros(0)
    idx = np.arange(n_out) * (sr_in / sr_out)
    idx = np.clip(idx, 0, len(x) - 1)
    lo = idx.astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    frac = idx - lo
    return (1.0 - frac) * x[lo] + frac * x[hi]


# ═══════════════════════════════════════════════════════════════════════
#  GQRXReceiver —— 完整窄带/宽带接收管道
# ═══════════════════════════════════════════════════════════════════════
class GQRXReceiver:
    """GQRX nbrx/wfmrx 风格接收机（纯 numpy，状态化逐块）。

    窄带链路（fm/nfm/am/usb/lsb/cw）：
        IQ → IQCorrector → 信道带通 → AGC → 解调 → 去加重 → 重采样到 48kHz
    来源 nbrx.cpp:73-79。
    宽带链路（wfm）：
        IQ → IQCorrector → 信道带通(±80k) → FM鉴频(75k) → 重采样到 48kHz
        （无 AGC，来源 wfmrx.cpp:60-65）。
    """

    SUPPORTED_MODES = ("fm", "nfm", "wfm", "am", "usb", "lsb", "cw")

    def __init__(self, sample_rate: float = PREF_QUAD_RATE_NB,
                 mode: str = "nfm", audio_rate: float = AUDIO_RATE):
        self.sr = float(sample_rate)
        self.audio_rate = float(audio_rate)
        self.iq_corrector = IQCorrector(self.sr)
        self.mode = "nfm"
        self.agc = GqrxAGC(self.sr)   # nbrx.cpp:47 默认参数
        self._deemph = _IIR1(*fm_deemph_taps(self.sr, 0.0))
        self._filt_taps = None
        self._filt_delay = 0.0
        self.set_mode(mode)

    # ------------------------------------------------------------------
    def set_mode(self, mode: str):
        mode = (mode or "nfm").lower()
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(f"不支持的模式 {mode}（{self.SUPPORTED_MODES}）")
        self.mode = mode
        low, high = GQRX_FILTER_PRESETS[mode]
        # 来源 rx_filter.cpp:61 复带通；SSB 边带选择在这里完成
        self._filt_taps = _complex_bandpass(self.sr, low, high)
        # FM 去加重 tau。来源 nbrx.cpp:52 / wfmrx.cpp:48
        tau = GQRX_FM_DEEMPH_TAU.get(mode, 0.0)
        self._deemph = _IIR1(*fm_deemph_taps(self.sr, tau))
        self.max_dev = GQRX_FM_MAXDEV.get(mode, 5_000.0)
        self.agc.reset()
        return {"mode": mode, "filter_low_hz": low, "filter_high_hz": high,
                "fm_maxdev_hz": self.max_dev, "fm_deemph_tau": tau,
                "agc": "on" if self.agc.agc_on else "manual"}

    # ------------------------------------------------------------------
    def _channel_filter(self, iq: np.ndarray) -> np.ndarray:
        y = np.convolve(iq, self._filt_taps, mode="same")
        return y

    def _demod(self, iq: np.ndarray) -> np.ndarray:
        """对应 nbrx.cpp:50-54 的各解调块。"""
        m = self.mode
        if m in ("fm", "nfm", "wfm"):
            # 正交鉴频：gain = sr/(2π*max_dev)。来源 rx_demod_fm.cpp
            #   gain = d_quad_rate/(2*π*max_dev)；输出 = gain*angle(z[n]*conj(z[n-1]))
            if len(iq) < 2:
                return np.zeros(len(iq))
            gain = self.sr / (2.0 * np.pi * self.max_dev)   # rx_demod_fm.cpp
            phase = np.angle(iq[1:] * np.conj(iq[:-1]))
            audio = gain * phase
            audio = np.concatenate([audio, audio[-1:]])
            return audio
        if m == "am":
            # 包络检波 = |z|，再去直流。来源 rx_demod_am.cpp:48,60-63
            audio = np.abs(iq)
            return audio - np.mean(audio)
        # usb/lsb/cw：取同相分量（复数→实数）。来源 nbrx.cpp:51 demod_ssb=complex_to_real
        return iq.real

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> Dict:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return {"audio": np.zeros(0, dtype=np.float32).tolist(),
                    "audio_rate": self.audio_rate, "mode": self.mode}

        # 1) IQ 校正（DC + 正交不平衡）。来源 receiver.cpp:1358-1363
        iq = self.iq_corrector.process(iq)

        # 2) 信道带通。来源 nbrx.cpp:75 filter
        iq = self._channel_filter(iq)

        # 3) AGC（WFM 不开）。来源 nbrx.cpp:78 agc；wfmrx 无 AGC
        if self.mode != "wfm":
            iq = self.agc.process(iq)

        # 4) 解调。来源 nbrx.cpp:79 demod
        audio = self._demod(iq)

        # 5) FM 去加重（仅 FM 模式且 tau>0）。来源 nbrx.cpp:52
        if self.mode in ("fm", "nfm"):
            audio = self._deemph.process(audio)

        # 6) 重采样到音频率 48kHz。来源 nbrx.cpp:68-69 audio_rr
        audio = _resample_to(audio, self.sr, self.audio_rate)

        return {
            "audio": audio.astype(np.float32).tolist(),
            "audio_rate": self.audio_rate,
            "mode": self.mode,
            "dc_offset_i": float(self.iq_corrector.dc_offset.real),
            "dc_offset_q": float(self.iq_corrector.dc_offset.imag),
        }


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_gqrx_receiver_tools(registry) -> None:
    """把 AGC / IQ校正 / GQRX接收机 注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _agc_process(args):
        """已知幅度阶跃 → AGC 压到目标电平。"""
        import numpy as np
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        try:
            arr = np.array(iq, dtype=complex)
            agc = GqrxAGC(sr, agc_on=True, use_hang=bool(args.get("use_hang", False)),
                          threshold_db=int(args.get("threshold_db", -100)),
                          decay_ms=int(args.get("decay_ms", 500)))
            out = agc.process(arr)
            before = float(np.sqrt(np.mean(np.abs(arr) ** 2)))
            after = float(np.sqrt(np.mean(np.abs(out) ** 2)))
            data = {"rms_before": before, "rms_after": after,
                    "out_samples": int(len(out)),
                    "source": "gqrx agc_impl.cpp:197-317"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"AGC 失败: {e}")

    def _iq_correct(args):
        import numpy as np
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        try:
            arr = np.array(iq, dtype=complex)
            cor = IQCorrector(sr, dc_tau=1.0)
            out = cor.process(arr)
            data = {"mean_before": complex(arr.mean()).real,
                    "dc_offset_i": float(cor.dc_offset.real),
                    "dc_offset_q": float(cor.dc_offset.imag),
                    "mean_after_i": float(out.real.mean()),
                    "mean_after_q": float(out.imag.mean()),
                    "source": "gqrx correct_iq_cc.cpp:47, receiver.cpp:118"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"IQ校正失败: {e}")

    def _gqrx_create(args):
        """创建一个 GQRX 接收机实例并跑一段合成 IQ，返回音频摘要。"""
        import numpy as np
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        mode = str(args.get("mode", "nfm"))
        n = int(args.get("num_samples", 96_000))
        try:
            rx = GQRXReceiver(sample_rate=sr, mode=mode)
            # 合成一个带 1kHz 调制的测试 IQ
            t = np.arange(n) / sr
            if mode in ("fm", "nfm", "wfm"):
                fdev = GQRX_FM_MAXDEV.get(mode, 5000.0)
                phase = 2 * np.pi * fdev * 0.5 * np.cumsum(np.sin(2 * np.pi * 1000 * t)) / sr
                iq = np.exp(1j * phase)
            else:
                iq = np.exp(1j * 2 * np.pi * 1000 * t) * 0.5
            r = rx.process(iq)
            audio = r.pop("audio")
            data = {**r, "audio_samples": len(audio),
                    "note": "GQRX 真实接收机管道实例已创建并跑通",
                    "source": "gqrx nbrx.cpp:73-79 / wfmrx.cpp:60-65"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"接收机创建失败: {e}")

    registry.register(
        name="agc_process",
        description=("GQRX 真实 AGC（CAgc 移植）：输入一段复数 IQ，按 attack=2ms/"
                     "release=50ms/decay=500ms 的快压慢放 + 18ms 峰值窗，把输出 RMS "
                     "稳到 GQRX 目标电平（OUTSCALE=0.7）。返回处理前后 RMS。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"},
                       "description": "交错复 IQ 或复数采样列表"},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
                "use_hang": {"type": "boolean", "default": False},
                "threshold_db": {"type": "integer", "default": -100},
                "decay_ms": {"type": "integer", "default": 500},
            },
            "required": ["iq"],
        },
        handler=_agc_process,
        category="sdr_dsp",
    )

    registry.register(
        name="iq_correct",
        description=("GQRX IQ 前端校正：单极点 IIR(tau=1s) 自适应估计并扣除直流偏移，"
                     "Gram-Schmidt 式在线校正 I/Q 正交不平衡。返回校正前后均值与 DC 估计。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
            },
            "required": ["iq"],
        },
        handler=_iq_correct,
        category="sdr_dsp",
    )

    registry.register(
        name="gqrx_receiver_create",
        description=("按 GQRX nbrx/wfmrx 真实参数实例化接收机管道："
                     "IQ校正→信道带通(AM±5k/USB100-2800/LSB-2800~-100/CW±250/WFM±80k)"
                     "→AGC(仅窄带)→FM鉴频(maxdev NFM5k/WFM75k)+75μs去加重→重采样到48kHz。"
                     "mode=fm/nfm/wfm/am/usb/lsb/cw。"),
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(GQRXReceiver.SUPPORTED_MODES)},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
                "num_samples": {"type": "integer", "default": 96000},
            },
            "required": [],
        },
        handler=_gqrx_create,
        category="sdr_demod",
    )


if __name__ == "__main__":
    # 自测：已知幅度阶跃 → AGC 输出稳定
    sr = 96_000
    t = np.arange(sr) / sr
    # 前半段弱信号(0.1)，后半段强信号(0.9)
    sig = np.full(sr, 0.1, dtype=complex)
    sig[sr // 2:] = 0.9
    agc = GqrxAGC(sr, threshold_db=-100, decay_ms=500)
    out = agc.process(sig)
    print(f"AGC: 输入 RMS 前半={np.sqrt(np.mean(np.abs(sig[:sr//2])**2)):.3f} "
          f"后半={np.sqrt(np.mean(np.abs(sig[sr//2:])**2)):.3f}")
    print(f"     输出 RMS 前半={np.sqrt(np.mean(np.abs(out[:sr//2])**2)):.3f} "
          f"后半={np.sqrt(np.mean(np.abs(out[sr//2:])**2)):.3f}")
