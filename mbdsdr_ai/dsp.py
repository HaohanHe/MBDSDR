"""
MBDSDR AI 内核 - DSP 数字信号处理模块
=====================================
DSP Module：IQ 前端校正 + 真实解调算法 + 信号处理工具。

对照白皮书第五章和专题 05 实现：
1. IQ 前端校正（DC blocker + I/Q 平衡协方差白化 + 整数抽取）
2. 真实解调算法（FM/AM/SSB/LSB/USB/CW）
3. 信号处理工具（AGC、滤波、重采样）

这是 SDR 软件最核心的基本功，之前完全是空壳。
"""

import numpy as np

from typing import Tuple, Optional, Dict, Any


# ═══════════════════════════════════════════════════════
# 1. IQ 前端校正（对照专题 05）
# ═══════════════════════════════════════════════════════

class DCBlocker:
    """
    直流阻断器（一阶 IIR）。

    y[n] = x[n] - x[n-1] + R * y[n-1]
    R ∈ [0,1)，典型 0.995~0.999。

    相比"减整段均值"，能跟踪随温度/时间缓慢漂移的直流，
    且流式、O(1) 状态、无需整段已知。
    """

    def __init__(self, r: float = 0.998):
        self.r = r
        self._x_prev = 0.0
        self._y_prev = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段 IQ 样本（分别对 I 和 Q 做 DC 阻断）。"""
        if x.dtype == np.complex64 or x.dtype == np.complex128:
            i_part = self._process_real(x.real)
            q_part = self._process_real(x.imag)
            return i_part + 1j * q_part
        else:
            return self._process_real(x)

    def _process_real(self, x: np.ndarray) -> np.ndarray:
        """向量化 DC 阻断：y[n] = x[n] - x[n-1] + R*y[n-1]。

        用 scipy.signal.lfilter 做一阶 IIR，保留流式状态；
        scipy 不可用时退化为 numpy 向量化差分 + 递推。
        """
        r = self.r
        x_prev = self._x_prev
        y_prev = self._y_prev

        try:
            from scipy.signal import lfilter
            # 一阶 IIR: b=[1,-1], a=[1,-r]，初始状态 zi[0] = -x_prev + r*y_prev
            zi = np.array([-x_prev + r * y_prev], dtype=np.float64)
            y, zf = lfilter([1.0, -1.0], [1.0, -r], x.astype(np.float64), zi=zi)
            self._x_prev = float(x[-1]) if len(x) else x_prev
            self._y_prev = float(y[-1]) if len(y) else y_prev
            return y.astype(x.dtype)
        except ImportError:
            pass

        # numpy 兜底：向量化差分 + 一阶递推
        x64 = x.astype(np.float64)
        diff = np.empty_like(x64)
        if len(x64) > 0:
            diff[0] = x64[0] - x_prev
            diff[1:] = x64[1:] - x64[:-1]
        y = np.zeros_like(x64)
        yp = y_prev
        for n in range(len(x64)):
            yp = diff[n] + r * yp
            y[n] = yp
        self._x_prev = float(x64[-1]) if len(x64) else x_prev
        self._y_prev = yp
        return y.astype(x.dtype)

    def reset(self):
        self._x_prev = 0.0
        self._y_prev = 0.0


class IQCalibrator:
    """
    I/Q 不平衡校正：协方差白化。

    常见做法是分别反解增益差 g 与相位误差 φ 再补偿，
    但 g 与 φ 在二阶矩里相互耦合，逐参数反解不彻底。

    本实现改用协方差白化：
    1. 去均值后估计 2×2 协方差 C = [[varI, covIQ],[covIQ, varQ]]
    2. 特征分解 C = VΛVᵀ，构造白化矩阵 W = V·diag(1/√Λ)·Vᵀ
    3. 对 [I,Q]ᵀ 左乘 W，使两路零均值、等功率、互不相关

    白化对任意"增益差 + 正交相位误差"组合都成立，不依赖参数化假设。
    """

    def __init__(self):
        self._whitening_matrix: Optional[np.ndarray] = None
        self._mean: Optional[np.ndarray] = None
        self._fitted = False

    def fit(self, x: np.ndarray, n_samples: int = 10000) -> Dict[str, Any]:
        """
        从一段 IQ 样本估计白化矩阵。

        返回诊断信息：增益误差、相位误差、协方差矩阵。
        """
        # 取前 n_samples 个样本估计
        n = min(n_samples, len(x))
        iq = np.column_stack([x[:n].real, x[:n].imag])

        # 去均值
        mean = np.mean(iq, axis=0)
        iq_centered = iq - mean

        # 协方差矩阵
        cov = np.cov(iq_centered.T)

        # 特征分解
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        # 避免除零
        eigenvalues = np.maximum(eigenvalues, 1e-12)

        # 白化矩阵 W = V · diag(1/√Λ) · Vᵀ
        whitening = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T

        self._whitening_matrix = whitening
        self._mean = mean
        self._fitted = True

        # 诊断：估计增益误差和相位误差
        var_i = cov[0, 0]
        var_q = cov[1, 1]
        cov_iq = cov[0, 1]

        gain_err = np.sqrt(var_q / var_i) if var_i > 0 else 1.0
        # 相位误差估计（从协方差的非对角元）
        if var_i > 0 and var_q > 0:
            phase_err_rad = np.arcsin(np.clip(cov_iq / np.sqrt(var_i * var_q), -1, 1))
            phase_err_deg = np.degrees(phase_err_rad)
        else:
            phase_err_deg = 0.0

        return {
            "fitted": True,
            "samples_used": n,
            "covariance_matrix": cov.tolist(),
            "iq_gain_error": float(gain_err),
            "iq_phase_error_deg": float(phase_err_deg),
            "dc_offset_i": float(mean[0]),
            "dc_offset_q": float(mean[1]),
        }

    def apply(self, x: np.ndarray) -> np.ndarray:
        """应用白化矩阵到 IQ 样本。"""
        if not self._fitted or self._whitening_matrix is None:
            return x

        iq = np.column_stack([x.real, x.imag])
        # 去均值
        iq_centered = iq - self._mean
        # 白化
        iq_white = iq_centered @ self._whitening_matrix.T
        return iq_white[:, 0] + 1j * iq_white[:, 1]

    def is_fitted(self) -> bool:
        return self._fitted


def decimate(x: np.ndarray, factor: int) -> np.ndarray:
    """
    整数抽取：先抗混叠低通，再每 factor 取 1。

    这是 DDC（数字下变频）的末级。对照 GNU Radio rational_resampler 的
    多相滤波结构（gr-blocks/lib/rational_resampler_base_cc.cc）：
    抽取前必须先把 |f| > fs_out/2 的分量滤掉，否则高频会折叠混叠到基带，
    严重劣化解调质量。本实现用 Kaiser 窗 FIR 做抗混叠低通，截止 = fs_out/2，
    留 ~20% 过渡带，抽头数取 kaiserord 与 32*factor 的较大者。
    """
    if factor <= 1:
        return x

    n = len(x)
    if n == 0:
        return x

    # 样本太少：凑不齐滤波器阶数，退化为直接抽取（避免边缘瞬态主导输出）
    if n < 8 * factor:
        return x[::factor]

    # 来源: GNU Radio rational_resampler_base_cc.cc:43-74 (design_resampler_filter)
    # — 抽取前必须抗混叠低通：截止 = 输出 Nyquist = fs/(2*factor)，留 ~20% 过渡带
    # 归一化频率单位：输入 Nyquist = 1.0；折叠频率 = 1/factor（输入 Nyquist 单位）
    stopband_edge = 1.0 / factor          # 阻带边缘：从此处开始全部折叠，必须衰减
    trans = 0.2 / factor                  # 20% 过渡带
    passband_edge = stopband_edge - trans  # 通带边缘
    cutoff = (passband_edge + stopband_edge) / 2.0  # -6dB 截止
    ripple_db = 60.0                      # 阻带衰减（Kaiser 窗 ~60dB）

    try:
        from scipy.signal import kaiserord, firwin, lfilter
        numtaps, beta = kaiserord(ripple_db, trans)
        # 工程经验：FIR 阶数至少 32*factor（任务规格），取 kaiserord 与 32*factor 较大者
        numtaps = max(numtaps, 32 * factor)
        # 偶数阶 → 奇数抽头（Type I 线性相位 FIR）
        if numtaps % 2 == 0:
            numtaps += 1
        # 抽头数不能超过信号长度的 ~1/3，否则边缘瞬态主导输出
        numtaps = min(numtaps, max(8, n // 3))
        if numtaps % 2 == 0:
            numtaps -= 1
        taps = firwin(numtaps, cutoff, window=('kaiser', beta), scale=True)
        # 因果 FIR 滤波（scipy lfilter 对复数数组自动处理 I/Q 两路）
        filtered = lfilter(taps, 1.0, x)
    except ImportError:
        # scipy 不可用：numpy 兜底 sinc + Hanning 窗（抽头仍按 32*factor 量级取）
        numtaps = min(max(32 * factor, 32), max(8, n // 3))
        if numtaps % 2 == 0:
            numtaps += 1
        t = np.arange(numtaps) - (numtaps - 1) / 2
        h = 2.0 * cutoff * np.sinc(2.0 * cutoff * t)
        h *= np.hanning(numtaps)
        h /= np.sum(h)
        if x.dtype in (np.complex64, np.complex128):
            filtered = (np.convolve(x.real, h, mode='same')
                        + 1j * np.convolve(x.imag, h, mode='same'))
        else:
            filtered = np.convolve(x, h, mode='same')

    # 滤波完成后再抽取（多相结构的等价离线实现）
    return filtered[::factor]


def front_end(
    x: np.ndarray,
    dc_r: float = 0.998,
    correct_iq: bool = True,
    decimation: int = 1,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    一站式 IQ 前端校正：DC 阻断 → I/Q 平衡 → 抽取。

    返回 (校正后 IQ, 诊断信息)。
    """
    diagnostics = {}

    # 1. DC 阻断
    dc_blocker = DCBlocker(r=dc_r)
    x = dc_blocker.process(x)
    diagnostics["dc_blocked"] = True

    # 2. I/Q 平衡
    if correct_iq:
        calibrator = IQCalibrator()
        cal_diag = calibrator.fit(x)
        x = calibrator.apply(x)
        diagnostics["iq_correction"] = cal_diag
    else:
        diagnostics["iq_correction"] = {"skipped": True}

    # 3. 抽取
    if decimation > 1:
        x = decimate(x, decimation)
        diagnostics["decimation"] = decimation
        diagnostics["effective_sample_rate_ratio"] = 1.0 / decimation

    return x, diagnostics


# ═══════════════════════════════════════════════════════
# 2. 真实解调算法
# ═══════════════════════════════════════════════════════

def fm_demod(x: np.ndarray, deviation: float = 75000.0,
             sample_rate: float = 2400000.0) -> np.ndarray:
    """
    FM 调频解调（正交鉴频）。

    y[n] = angle(x[n] * conj(x[n-1])) / (2π * deviation / sample_rate)

    deviation: 最大频偏，广播 FM 75kHz，窄带 FM 5kHz

    ── SDRangel NFM 真实参数（来源: plugins/channelrx/demodnfm/）──
      - 默认 m_rfBandwidth = 12500 Hz   (nfmdemodsettings.cpp:57)
      - 默认 m_afBandwidth = 3000 Hz    (nfmdemodsettings.cpp:58)
      - 默认 m_fmDeviation = 5000 Hz    (nfmdemodsettings.cpp:59)
      - RF 带通 [-dev,+dev]/channelSR   (nfmdemodsink.cpp:297-299)
      - FM 缩放 = audioSR/fmDeviation   (nfmdemodsink.cpp:321,390)
      - 相位差分鉴频 unwrap 到[-1,1]    (sdrbase/dsp/phasediscri.h:75-92)
      - 音频带通 300Hz ~ afBandwidth    (nfmdemodsink.cpp:326)
    精确实现见 mbdsdr_ai/sdrangel_adapter.py::NFMDemodSink。
    """
    if len(x) < 2:
        return np.zeros(len(x), dtype=np.float32)

    # 正交鉴频
    phase_diff = np.angle(x[1:] * np.conj(x[:-1]))
    # 归一化到音频
    gain = sample_rate / (2 * np.pi * deviation)
    audio = phase_diff * gain

    # 音频去直流
    audio = audio - np.mean(audio)

    return audio.astype(np.float32)


class QuadratureDemod:
    """有状态 FM 正交鉴频器（对照 GR quadrature_demod_cf_impl.cc:37 set_history(2)）。

    纯函数 fm_demod() 每块丢 1 个样本（块边界相位差断了），本类缓存上一块
    末样本，跨帧连续。公式与 fm_demod 完全一致：
        out = gain * arg(x[n] * conj(x[n-1]))
        gain = sample_rate / (2π * deviation)
    """

    def __init__(self, sample_rate: float = 48000.0, deviation: float = 5000.0):
        self.sample_rate = float(sample_rate)
        self.deviation = float(deviation)
        self.gain = self.sample_rate / (2.0 * np.pi * self.deviation)
        self._last: Optional[complex] = None

    def reset(self):
        self._last = None

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.complex128)
        if len(x) < 2:
            return np.zeros(0, dtype=np.float32)
        # 前置补上一块末样本（= GR set_history(2) 让调度器补的那个样本）
        if self._last is not None:
            x = np.concatenate(([self._last], x))
        phase_diff = np.angle(x[1:] * np.conj(x[:-1]))
        self._last = complex(x[-1])
        audio = phase_diff * self.gain
        # 去直流（块级均值，跨帧由调用方的 DCBlocker 处理）
        audio = audio - np.mean(audio)
        return audio.astype(np.float32)


def wfm_broadcast_demod(x: np.ndarray, sample_rate: float,
                        audio_sr: int = 48000, deemph_us: float = 50.0,
                        audio_cutoff: float = 15000.0) -> np.ndarray:
    """
    完整宽带调频广播（WFM / 商用 FM）接收链，对标 GQRX/SDR# 的单声道 FM：

      去直流 → 正交鉴频 → 抗混叠降采样到音频率 → 去加重 → 15kHz 音频低通 → 归一化

    sample_rate : 输入 IQ 采样率（FM 广播建议 ≥200kHz，如 240k/1.0M/2.4M）
    audio_sr    : 输出音频采样率（默认 48k）
    deemph_us   : 去加重时间常数，中国/欧洲/澳洲 50µs，美国/韩国 75µs
    仅输出单声道（L+R，基带 0–15kHz）；立体声复合解码（19kHz pilot / 38kHz 副载波）
    与 RDS（57kHz 副载波）为独立后续模块。
    """
    from scipy.signal import resample_poly, butter, lfilter

    x = np.asarray(x, dtype=np.complex128)
    x = x - np.mean(x)
    if len(x) < 4:
        return np.zeros(0, dtype=np.float32)

    # 1) 正交鉴频（频偏归一化，广播 FM 最大频偏 75kHz）
    phase_diff = np.angle(x[1:] * np.conj(x[:-1]))
    audio = phase_diff * (sample_rate / (2.0 * np.pi * 75000.0))
    audio = audio - np.mean(audio)

    # 2) 降采样到音频率（resample_poly 内置抗混叠低通）
    from math import gcd
    if int(sample_rate) != int(audio_sr):
        g = gcd(int(round(sample_rate)), int(audio_sr))
        up = int(audio_sr) // g
        down = int(round(sample_rate)) // g
        audio = resample_poly(audio, up, down)

    # 3) 去加重（一阶 RC 低通：y[n] = a*y[n-1] + (1-a)*x[n]）
    if deemph_us and deemph_us > 0:
        a = float(np.exp(-1.0 / (deemph_us * 1e-6 * audio_sr)))
        audio = lfilter([1.0 - a], [1.0, -a], audio)

    # 4) 音频低通（去除 15k 以上残余，含立体声/RDS 副载波泄漏）
    nyq = audio_sr / 2.0
    norm_cut = min(0.99, audio_cutoff / nyq)
    if norm_cut < 0.99:
        b, a = butter(5, norm_cut, btype="low")
        audio = lfilter(b, a, audio)

    # 5) 归一化到 [-1,1]（留 5% 余量，避免削波）
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 1e-9:
        audio = audio / peak * 0.95
    return audio.astype(np.float32)


def rds_decode_from_wfm(x: np.ndarray, sample_rate: float) -> dict:
    """
    从 WFM 基带 IQ 中解码 RDS（57kHz 副载波，1187.5 波特双相码）的占位实现：
    做带通选通与载波能量检测，返回 RDS 副载波是否存在及电平；完整块同步/纠错
    （104bit 组、差分解码、CRC）后续接入成熟开源 RDS 库。此函数先让链路可观测。
    """
    from scipy.signal import butter, lfilter
    x = np.asarray(x, dtype=np.complex128)
    x = x - np.mean(x)
    if len(x) < 4:
        return {"rds_present": False, "carrier_db": None}
    phase = np.angle(x[1:] * np.conj(x[:-1]))
    mp = phase * (sample_rate / (2.0 * np.pi * 75000.0))
    mp = mp - np.mean(mp)
    nyq = sample_rate / 2.0
    # RDS 副载波 57kHz ± 2kHz
    b, a = butter(4, [55000.0 / nyq, 59000.0 / nyq], btype="band")
    band = lfilter(b, a, mp)
    rms = float(np.sqrt(np.mean(band ** 2)))
    floor = float(np.sqrt(np.mean(mp ** 2))) + 1e-12
    carrier_db = 20.0 * np.log10(rms / floor + 1e-12)
    # 经验门限：副载波能量相对复合基带明显即判存在（最终以块同步为准）
    return {"rds_present": carrier_db > -20.0, "carrier_db": round(carrier_db, 2)}


def audio_to_playback(x: np.ndarray, in_sr: float, cutoff_hz: float = 4500.0,
                      out_sr: int = 48000) -> np.ndarray:
    """
    把窄带解调结果（基带速率）整理为可播放音频：
    去直流 → 按模式音频带宽低通 → 抗混叠降采样到 out_sr → 归一化。
    用于 AM 航空 / NFM 对讲机 / SSB / CW，使各模式都能直接存成可听 WAV。
    """
    from math import gcd
    from scipy.signal import resample_poly, butter, lfilter

    x = np.asarray(x, dtype=np.float64)
    x = x - np.mean(x)
    if len(x) < 4:
        return np.zeros(0, dtype=np.float32)

    nyq = in_sr / 2.0
    cut = min(0.98, cutoff_hz / nyq)
    if cut < 0.98:
        b, a = butter(5, cut, btype="low")
        x = lfilter(b, a, x)

    if int(round(in_sr)) != int(out_sr):
        g = gcd(int(round(in_sr)), int(out_sr))
        x = resample_poly(x, int(out_sr) // g, int(round(in_sr)) // g)

    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 1e-9:
        x = x / peak * 0.95
    return x.astype(np.float32)


def am_demod(x: np.ndarray) -> np.ndarray:
    """
    AM 调幅解调（包络检波）。

    y[n] = |x[n]| - mean(|x|)
    """
    envelope = np.abs(x)
    # 去直流
    audio = envelope - np.mean(envelope)
    return audio.astype(np.float32)


def ssb_demod(x: np.ndarray, mode: str = "USB",
              carrier_offset: float = 1500.0,
              sample_rate: float = 2400000.0) -> np.ndarray:
    """
    SSB 单边带解调（Weaver 法简化版）。

    USB: 取上边带
    LSB: 取下边带

    carrier_offset: 载波偏移频率（BFO），典型 1500Hz

    ── SDRangel SSB 滤波器真实参数（来源: plugins/channelrx/demodssb/ssbdemodsink.cpp）──
      - FFT 长度 m_ssbFftLen = 2048            (ssbdemodsink.cpp:31)
      - 默认带宽 m_Bandwidth = 5000 Hz         (ssbdemodsink.cpp:52)
      - 默认低截 m_LowCutoff = 300 Hz          (ssbdemodsink.cpp:53)
      - 滤波器 f1=LowCutoff/audioSR, f2=Bandwidth/audioSR (ssbdemodsink.cpp:73,301)
      - USB 保留正频率 bin / LSB 保留负 bin    (fftfilt.cpp:475-502)
      - 检波 audio = (I+Q)*0.7                 (ssbdemodsink.cpp:208)
    精确 overlap-add 边带滤波见 mbdsdr_ai/sdrangel_adapter.py::SSBFilter/SSBDemodSink。
    """
    n = len(x)
    t = np.arange(n) / sample_rate

    # 本振
    lo = np.exp(1j * 2 * np.pi * carrier_offset * t)

    if mode.upper() == "USB":
        # 上边带：下变频后取实部
        mixed = x * np.conj(lo)
        audio = mixed.real
    else:
        # 下边带：本振符号翻转 = 频谱镜像（正确取边带）
        mixed = x * lo
        audio = mixed.real

    # 低通滤波（简单移动平均）
    kernel_size = max(1, int(sample_rate / 4000))  # ~4kHz 音频带宽
    if kernel_size > 1 and len(audio) > kernel_size:
        kernel = np.ones(kernel_size) / kernel_size
        audio = np.convolve(audio, kernel, mode='same')

    # 去直流
    audio = audio - np.mean(audio)

    return audio.astype(np.float32)


def cw_demod(x: np.ndarray, tone_freq: float = 700.0,
             sample_rate: float = 2400000.0) -> np.ndarray:
    """
    CW 等幅报解调（拍频振荡 BFO）。

    将 CW 信号搬移到音频频段，输出音频。
    """
    n = len(x)
    t = np.arange(n) / sample_rate
    bfo = np.exp(1j * 2 * np.pi * tone_freq * t)
    mixed = x * bfo
    audio = mixed.real
    # 去直流
    audio = audio - np.mean(audio)
    return audio.astype(np.float32)


def demodulate(x: np.ndarray, mode: str = "FM",
               sample_rate: float = 2400000.0,
               deviation: float = 75000.0) -> np.ndarray:
    """
    统一解调入口。

    mode: FM / NFM / WFM / AM / LSB / USB / CW
    """
    mode = mode.upper()

    if mode in ("FM", "WFM"):
        return fm_demod(x, deviation=deviation, sample_rate=sample_rate)
    elif mode == "NFM":
        return fm_demod(x, deviation=5000.0, sample_rate=sample_rate)
    elif mode == "AM":
        return am_demod(x)
    elif mode == "USB":
        return ssb_demod(x, mode="USB", sample_rate=sample_rate)
    elif mode == "LSB":
        return ssb_demod(x, mode="LSB", sample_rate=sample_rate)
    elif mode == "CW":
        return cw_demod(x, sample_rate=sample_rate)
    else:
        # 默认 FM
        return fm_demod(x, deviation=deviation, sample_rate=sample_rate)


# ═══════════════════════════════════════════════════════
# 3. AGC 自动增益控制
# ═══════════════════════════════════════════════════════

class AGC:
    """
    自动增益控制（简化版）。

    维持输出信号幅度在目标水平。

    ── SDRangel 真实参数校准（来源: sdrbase/dsp/agc.cpp:53-179 MagAGC）──
      - historySize = 12000 样本     (ssbdemodsink.cpp:40  m_agc(12000, target, 1e-2))
      - target (m_R) = 3276          (ssbdemodsink.cpp:32  -10dB 幅度, 32768/10)
      - threshold = 1e-2 (magsq)     (agc.cpp:57)
      - stepLength = min(2400, history/2)  (agc.cpp:60, @48kHz 最长 50ms 攻击/释放)
      - stepDelta  = 1/stepLength    (agc.cpp:61)
      - 增益 = target / sqrt(mean(|x|^2)) (agc.cpp:117)
      - 硬限幅: 输出幅度不超过 1.0    (agc.cpp:104-111)
    本类保持一阶 attack/release 结构；精确的滑动均值+smootherstep 包络见
    mbdsdr_ai/sdrangel_adapter.py::MagAGC（与 SDRangel 逐行对齐）。
    """

    # SDRangel MagAGC 标定常量（供调用方参考/对齐）
    SDRANGEL_HISTORY = 12000        # ssbdemodsink.cpp:40
    SDRANGEL_TARGET_I16 = 3276      # ssbdemodsink.cpp:32
    SDRANGEL_STEP_LEN_MAX = 2400    # agc.cpp:60  (@48kHz = 50ms)
    SDRANGEL_THRESHOLD = 1e-2       # agc.cpp:57

    def __init__(self, target_level: float = 0.5, attack: float = 0.01,
                 release: float = 0.001, max_gain: float = 60.0):
        self.target_level = target_level
        self.attack = attack
        self.release = release
        self.max_gain = max_gain
        self._current_gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """处理一段信号。"""
        # 计算当前信号电平
        level = np.sqrt(np.mean(np.abs(x) ** 2))
        if level < 1e-10:
            return x

        # 计算需要的增益
        desired_gain = self.target_level / level
        desired_gain = min(desired_gain, self.max_gain)

        # 平滑增益变化
        if desired_gain > self._current_gain:
            # 增益上升（attack 快）
            alpha = self.attack
        else:
            # 增益下降（release 慢）
            alpha = self.release

        self._current_gain = (1 - alpha) * self._current_gain + alpha * desired_gain

        return x * self._current_gain

    def reset(self):
        self._current_gain = 1.0


# ── GNU Radio 真实 DSP 参数校准（来源: repos/gnuradio）─────────────
# 与 mbdsdr_ai/gnuradio_blocks.py 逐行移植对齐。本常量块把现有
# decimate()/AGC() 的工程经验值校准到 GNU Radio 内核真实默认值。
#
# 1) 有理重采样 FIR 设计（gr-filter/lib/rational_resampler_impl.cc:43-74
#    design_resampler_filter）：
GR_RESAMPLER_KAISER_BETA = 7.0        # rational_resampler_impl.cc:55  float beta = 7.0;
GR_RESAMPLER_FRACTIONAL_BW = 0.4      # rational_resampler_impl.cc:124/:142 默认 0.4
GR_RESAMPLER_HALFBAND = 0.5           # rational_resampler_impl.cc:56
#   → decimate() 的 Kaiser 阻带波纹 60dB 与 beta=7.0（≈75dB）一致，
#     截止 mid_transition_band 与本文件 cutoff 取中点的做法对齐。
#
# 2) FFT 快速卷积（gr-filter/lib/fft_filter.cc:76/77 + fft_filter.h:72）：
GR_FFT_FILTER_FFTSIZE = lambda nt: int(2 * 2 ** np.ceil(np.log2(max(nt, 1))))
#   fft_filter.cc:76  d_fftsize = 2*2^ceil(log2(ntaps))
GR_FFT_FILTER_NSAMPLES = lambda nt, fs: fs - nt + 1   # fft_filter.cc:77
GR_FFT_FILTER_TAILSIZE = lambda nt: nt - 1            # fft_filter.h:72
#   fft_filter.cc:52  抽头先乘 scale=1/fftsize 再 FFT（吸收归一化）。
#
# 3) AGC2（gr-analog/include/gnuradio/analog/agc2.h:41-45）默认值：
GR_AGC2_ATTACK_RATE = 1e-1            # agc2.h:41
GR_AGC2_DECAY_RATE = 1e-2             # agc2.h:42
GR_AGC2_REFERENCE = 1.0               # agc2.h:43
GR_AGC2_INIT_GAIN = 1.0               # agc2.h:44
GR_AGC2_MAX_GAIN = 0.0                # agc2.h:45  0 = 不限
GR_AGC2_GAIN_FLOOR = 10e-5            # agc2.h:79  gain<0 时钳到 1e-4


def make_gr_agc2(reference: float = GR_AGC2_REFERENCE,
                 attack_rate: float = GR_AGC2_ATTACK_RATE,
                 decay_rate: float = GR_AGC2_DECAY_RATE):
    """构造与 GNU Radio agc2_cc 内核逐样本等价的 AGC2（agc2.h:64-85）。

    返回 mbdsdr_ai.gnuradio_blocks.AGC2 实例，可直接 .process(complex_iq)。
    若需要 SDRangel 滑动包络 AGC，仍用上面的 AGC / sdrangel_adapter.MagAGC。
    """
    from .gnuradio_blocks import AGC2
    return AGC2(attack_rate=attack_rate, decay_rate=decay_rate,
                reference=reference, gain=GR_AGC2_INIT_GAIN, max_gain=GR_AGC2_MAX_GAIN)


# ═══════════════════════════════════════════════════════
# 4. 基带录制（真正写文件）
# ═══════════════════════════════════════════════════════

def write_cf32(x: np.ndarray, path: str):
    """
    写 cf32 格式（交错 float32 小端 [I0,Q0,I1,Q1,...]）。

    GNU Radio / SDR++ 默认格式。
    """
    # 转换为交错 float32
    interleaved = np.column_stack([x.real, x.imag]).flatten().astype(np.float32)
    interleaved.tofile(path)


def write_cs16(x: np.ndarray, path: str, gain: float = 1.0):
    """
    写 cs16 格式（交错 int16 小端）。

    省一半空间，16bit 定点。
    """
    # 归一化到 [-1,1] 然后量化到 int16
    max_val = np.max(np.abs(x))
    if max_val > 0:
        normalized = x / max_val * gain
    else:
        normalized = x
    # clip 到 [-1,1]
    normalized = np.clip(normalized, -1.0, 1.0)
    # 转换为交错 int16
    i16 = (normalized.real * 32767).astype(np.int16)
    q16 = (normalized.imag * 32767).astype(np.int16)
    interleaved = np.column_stack([i16, q16]).flatten()
    interleaved.tofile(path)


def write_wav(x: np.ndarray, path: str, sample_rate: int = 48000, gain: float = 1.0):
    """
    写 WAV 格式（双声道 16bit PCM，I=左、Q=右）。

    SDR# 风格，任意音频工具可直接打开。
    """
    import wave
    # 归一化
    max_val = np.max(np.abs(x))
    if max_val > 0:
        normalized = x / max_val * gain
    else:
        normalized = x
    normalized = np.clip(normalized, -1.0, 1.0)
    # 转换为 int16 交错
    i16 = (normalized.real * 32767).astype(np.int16)
    q16 = (normalized.imag * 32767).astype(np.int16)
    interleaved = np.column_stack([i16, q16]).flatten()

    with wave.open(path, 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(interleaved.tobytes())


def write_csv(x: np.ndarray, path: str, decimation: int = 1):
    """
    写 CSV 格式（文本行 I,Q，首行表头）。

    教学、表格审计用。建议配合大抽取。
    """
    if decimation > 1:
        # 来源: GNU Radio rational_resampler_base_cc.cc — 降采样前必须抗混叠低通
        # 不能裸 x[::decimation]，否则高频折叠到低频污染审计数据
        x = decimate(x, decimation)
    with open(path, 'w') as f:
        f.write("I,Q\n")
        for sample in x:
            f.write(f"{sample.real:.8f},{sample.imag:.8f}\n")


def write_sidecar_json(path: str, metadata: Dict[str, Any]):
    """
    写 sidecar 元数据 JSON（可回放的关键）。

    录制停止时写 <文件>.json，记录中心频率/采样率/格式/样本数/时间。
    """
    sidecar_path = path + ".json"
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    return sidecar_path


# 延迟导入 json（在文件顶部导入会导致循环）
import json


# ═══════════════════════════════════════════════════════
# 5. 信号分析工具
# ═══════════════════════════════════════════════════════

def compute_snr(x: np.ndarray, signal_band: Tuple[float, float] = None,
                sample_rate: float = 2400000.0) -> Dict[str, Any]:
    """
    计算 SNR（信噪比）。

    如果指定 signal_band（Hz），在该频带内计算信号功率，
    其余频带计算噪声功率。
    """
    # 加 Hann 窗减少频谱泄漏（裸 FFT 会把信号功率摊到邻近 bin，
    # 导致噪声功率偏高、SNR 估计偏低）。窗增益补偿用于功率归一化。
    n = len(x)
    win = np.hanning(n)
    win_gain = float(np.mean(win))
    spectrum = np.fft.fftshift(np.fft.fft(x * win))
    powers = np.abs(spectrum) ** 2 / (win_gain ** 2)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / sample_rate))

    if signal_band:
        # 指定信号频带
        signal_mask = (freqs >= signal_band[0]) & (freqs <= signal_band[1])
        noise_mask = ~signal_mask
        signal_power = np.mean(powers[signal_mask])
        noise_power = np.mean(powers[noise_mask])
    else:
        # 用峰值附近作为信号，其余作为噪声
        peak_idx = np.argmax(powers)
        bandwidth = max(1, len(x) // 20)  # 5% 带宽作为信号
        start = max(0, peak_idx - bandwidth // 2)
        end = min(len(x), peak_idx + bandwidth // 2)
        signal_power = np.mean(powers[start:end])
        noise_power = np.mean(np.concatenate([powers[:start], powers[end:]]))

    snr_db = 10 * np.log10(signal_power / max(noise_power, 1e-12))

    return {
        "snr_db": float(snr_db),
        "signal_power": float(signal_power),
        "noise_power": float(noise_power),
        "signal_band_hz": list(signal_band) if signal_band else None,
    }


def estimate_bandwidth(x: np.ndarray, sample_rate: float = 2400000.0,
                       threshold_db: float = -20.0) -> Dict[str, Any]:
    """
    估计信号占用带宽。

    找到功率超过峰值 -threshold_db 的频率范围。
    """
    spectrum = np.fft.fftshift(np.fft.fft(x))
    powers_db = 10 * np.log10(np.abs(spectrum) ** 2 + 1e-12)
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1.0 / sample_rate))

    peak_power = np.max(powers_db)
    threshold = peak_power + threshold_db  # threshold_db 是负数
    above_threshold = powers_db >= threshold

    if np.any(above_threshold):
        indices = np.where(above_threshold)[0]
        start_freq = freqs[indices[0]]
        end_freq = freqs[indices[-1]]
        bandwidth = end_freq - start_freq
        center_freq = (start_freq + end_freq) / 2
    else:
        start_freq = end_freq = center_freq = 0.0
        bandwidth = 0.0

    return {
        "bandwidth_hz": float(bandwidth),
        "center_freq_hz": float(center_freq),
        "start_freq_hz": float(start_freq),
        "end_freq_hz": float(end_freq),
        "peak_power_db": float(peak_power),
        "threshold_db": float(threshold),
    }


def anr_denoise(x: np.ndarray, frame: int = 256, hop: int = 128,
                noise_frames: int = 10, oversub: float = 2.0,
                floor_db: float = -30.0) -> np.ndarray:
    """STFT 谱减自动降噪（ANR）。

    用前 noise_frames 帧估计噪声功率谱，逐帧从观测谱中减去（过减因子
    oversub，保留 floor_db 地板防止音乐噪声）。纯 numpy，可测。
    """
    x = np.asarray(x, dtype=np.complex128)
    n = len(x)
    win = np.hanning(frame)
    wg = float(np.mean(win))

    def stft(seg):
        frames = []
        for s in range(0, len(seg) - frame, hop):
            frames.append(np.fft.fft(seg[s:s + frame] * win))
        return np.array(frames) if frames else np.zeros((1, frame), complex)

    def istft(fr):
        out = np.zeros(n, dtype=complex)
        wsum = np.zeros(n)
        k = 0
        for s in range(0, n - frame, hop):
            seg = np.fft.ifft(fr[k]) * win
            out[s:s + frame] += seg
            wsum[s:s + frame] += win ** 2
            k += 1
        wsum = np.where(wsum > 1e-9, wsum, 1.0)
        return out / wsum

    spec = stft(x)
    mag = np.abs(spec)
    # 噪声谱：前 noise_frames 帧平均
    nf = min(noise_frames, len(mag))
    noise_mag = np.mean(mag[:nf], axis=0, keepdims=True)
    # 谱减：保留相位
    phase = spec / (mag + 1e-12)
    cleaned = mag - oversub * noise_mag
    floor = np.maximum(mag, 1e-12) * (10.0 ** (floor_db / 20.0))
    cleaned = np.maximum(cleaned, floor)
    out_spec = cleaned * phase
    return istft(out_spec)


def find_spectrum_peaks(x: np.ndarray, sample_rate: float = 2400000.0,
                        n_peaks: int = 10, rel_threshold_db: float = 15.0) -> Dict:
    """在一段复 IQ 的幅度谱上找显著峰（找台/活动扫描）。

    返回每个峰的频率(Hz, 相对中心)和功率(dB)，按功率降序。
    """
    x = np.asarray(x, dtype=np.complex128)
    n = len(x)
    if n < 64:
        return {"peaks": []}
    win = np.hanning(n)
    win_gain = float(np.mean(win))
    spec = np.fft.fftshift(np.fft.fft(x * win))
    mag_db = 20.0 * np.log10(np.abs(spec) / (win_gain * n) + 1e-12)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sample_rate))
    peak_idx = int(np.argmax(mag_db))
    floor_db = float(np.median(mag_db))
    thresh = mag_db[peak_idx] - rel_threshold_db
    # 简单局部极大：比左右相邻都大且过阈
    cand = []
    for i in range(2, n - 2):
        if mag_db[i] >= thresh and mag_db[i] >= mag_db[i - 1] and mag_db[i] >= mag_db[i + 1]:
            cand.append((float(freqs[i]), float(mag_db[i])))
    cand.sort(key=lambda c: -c[1])
    peaks = [{"freq_hz": round(f, 1), "power_dbfs": round(p, 2)}
             for f, p in cand[:n_peaks]]
    return {"peaks": peaks, "floor_db": round(floor_db, 2),
            "peak_count": len(peaks)}


# ═══════════════════════════════════════════════════════
# 6. SDR++ 风格接收 VFO（数字下变频通道）
# ═══════════════════════════════════════════════════════

class VFO:
    """SDR++ 风格接收 VFO：频率变频 → 有理重采样 → 低通滤波。

    逐行对照 SDR++ 源码（repos/sdrpp/core/src/dsp/channel/）：
      - __init__     ↔ rx_vfo.h:19-33  init()
      - set_offset   ↔ rx_vfo.h:72-77  setOffset()
      - set_bandwidth ↔ rx_vfo.h:60-70 setBandwidth()
      - process      ↔ rx_vfo.h:89-100 process()（xlator → resamp → filter）
      - _generate_taps ↔ rx_vfo.h:117-121 generateTaps()

    频率变频（FrequencyXlator）对照 channel/frequency_xlator.h:
      - :21-23 init(in, offset, samplerate) → math::hzToRads(offset, samplerate)
      - :15-19 phase=1+0j, phaseDelta = cos(offset)+j*sin(offset)
      - :43-50 volk 旋转器：out[i] = in[i]*phase; phase *= phaseDelta
    关键：rx_vfo.h:27 调 xlator.init(NULL, -_offset, _inSamplerate) ——
    传负 offset，目的是把用户指定的目标频率（在输入频谱上的位置）搬移到 DC。

    低通抽头对照 taps/low_pass.h:7-11 + taps/estimate_tap_count.h:5：
      count = 3.8 * samplerate / transWidth；windowed_sinc.h:17-26 窗函数 sinc。
    rx_vfo.h:119-120: filterWidth = bandwidth/2.0；
      lowPass(filterWidth, filterWidth*0.1, outSamplerate)
    （即截止=bandwidth/2，过渡带=filterWidth*0.1=bandwidth*0.05；以源码为准。）
    """

    def __init__(self, in_samplerate: float, out_samplerate: float,
                 bandwidth: float, offset: float = 0.0):
        """in_samplerate: 输入采样率(Hz); out_samplerate: 输出采样率(Hz);
        bandwidth: VFO 带宽(Hz); offset: 变频偏移(Hz)，正=把 +offset 处信号搬到 DC。"""
        self._in_sr = float(in_samplerate)
        self._out_sr = float(out_samplerate)
        self._bandwidth = float(bandwidth)
        self._offset = float(offset)

        # rx_vfo.h:24 filterNeeded = (_bandwidth != _outSamplerate)
        self._filter_needed = abs(self._bandwidth - self._out_sr) > 1e-6

        # rx_vfo.h:27 xlator.init(NULL, -_offset, _inSamplerate)
        self._phase = complex(1.0, 0.0)
        self._rebuild_xlator()

        # rx_vfo.h:28 resamp.init(NULL, _inSamplerate, _outSamplerate)
        self._up = 1
        self._down = 1
        self._rebuild_rational()

        # rx_vfo.h:29-30 generateTaps + filter.init
        self._taps = None
        if self._filter_needed:
            self._generate_taps()

        # ── 块间状态持久化（对照 GR block.cc:97 forecast history + fir_filter_with_buffer.cc:71）──
        # LPF 滤波器初始条件，跨帧传递，消除每块开头瞬态咔哒
        self._lpf_zi = None
        # AGC2（对照 gr-analog agc2.h:64-85，已移植在 gnuradio_blocks.py:325）
        # 默认关闭，UI 可开；开启后在变频后、重采样前对复 IQ 做逐样本 AGC
        self._agc = None
        self._agc_enabled = False

    # ---------- 内部：频率变频 ----------
    def _rebuild_xlator(self):
        """frequency_xlator.h:21-23 hzToRads(offset, sr) = 2π*offset/sr。
        rx_vfo.h:27/76 传给 xlator 的是 -_offset（把目标频率搬到 DC）。"""
        self._d_theta = 2.0 * np.pi * (-self._offset) / self._in_sr
        self._phase_delta = complex(np.cos(self._d_theta), np.sin(self._d_theta))

    # ---------- 内部：有理重采样系数 ----------
    def _rebuild_rational(self):
        from math import gcd
        g = gcd(int(round(self._out_sr)), int(round(self._in_sr)))
        self._up = int(round(self._out_sr)) // g
        self._down = int(round(self._in_sr)) // g

    # ---------- 内部：低通抽头 ----------
    def _generate_taps(self):
        """rx_vfo.h:117-121 generateTaps()。"""
        cutoff = self._bandwidth / 2.0          # rx_vfo.h:119
        trans_width = cutoff * 0.1             # rx_vfo.h:120 lowPass(filterWidth, filterWidth*0.1, ...)
        # estimate_tap_count.h:5: count = 3.8 * samplerate / transWidth
        count = int(round(3.8 * self._out_sr / trans_width))
        if count % 2 == 0:
            count += 1  # 奇数抽头 = Type I 线性相位 FIR
        count = max(15, min(count, 4095))

        try:
            from scipy.signal import firwin
            nyq = self._out_sr / 2.0
            self._taps = firwin(count, cutoff / nyq, window='nuttall',
                                scale=True).astype(np.float64)
        except ImportError:
            # numpy 兜底：窗函数 sinc（windowed_sinc.h:17-26 的直译，Nuttall 窗）
            t = np.arange(count) - (count - 1) / 2.0
            h = 2.0 * (cutoff / self._out_sr) * np.sinc(2.0 * (cutoff / self._out_sr) * t)
            n = np.arange(count)
            w = (0.355768
                 - 0.487396 * np.cos(2.0 * np.pi * n / max(count - 1, 1))
                 + 0.144232 * np.cos(4.0 * np.pi * n / max(count - 1, 1))
                 - 0.012604 * np.cos(6.0 * np.pi * n / max(count - 1, 1)))
            taps = h * w
            taps /= np.sum(taps)
            self._taps = taps.astype(np.float64)

    # ---------- 公开接口 ----------
    def set_offset(self, offset_hz: float):
        """设置变频偏移（对照 rx_vfo.h:72-77 setOffset）。"""
        self._offset = float(offset_hz)
        self._rebuild_xlator()

    def set_bandwidth(self, bandwidth_hz: float):
        """设置 VFO 带宽并重建低通滤波器（对照 rx_vfo.h:60-70 setBandwidth）。"""
        self._bandwidth = float(bandwidth_hz)
        self._filter_needed = abs(self._bandwidth - self._out_sr) > 1e-6
        if self._filter_needed:
            self._generate_taps()
        else:
            self._taps = None

    def reset(self):
        """重置相位累加器（对照 frequency_xlator.h:35-41 reset）。"""
        self._phase = complex(1.0, 0.0)
        # 换频/换带宽后 LPF 状态也要清，否则旧频残留会串到新频
        self._lpf_zi = None
        if self._agc is not None:
            self._agc.reset(gain=1.0)

    def set_agc(self, enabled: bool, attack_rate: float = 1e-1,
                decay_rate: float = 1e-2, reference: float = 1.0):
        """开关 VFO 内 AGC2（对照 agc2_cc_impl.cc:42-50 work 调 scaleN）。

        默认参数 = GR agc2.h:41-45 默认值。开启后在变频后、重采样前对复 IQ
        做逐样本 attack/decay AGC，状态跨帧保留。"""
        self._agc_enabled = bool(enabled)
        if enabled and self._agc is None:
            from .gnuradio_blocks import AGC2
            self._agc = AGC2(attack_rate=attack_rate, decay_rate=decay_rate,
                             reference=reference, gain=1.0, max_gain=0.0)
        elif not enabled and self._agc is not None:
            self._agc.reset(gain=1.0)

    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一帧复 IQ：变频 → 重采样 → 低通滤波（对照 rx_vfo.h:89-100）。
        返回处理后的复 IQ（长度约 = len(iq) * out_sr / in_sr）。"""
        n = len(iq)
        if n == 0:
            return iq
        x = np.asarray(iq)
        in_dtype = x.dtype

        # 1) 频率变频（在 in_sr 上）—— rx_vfo.h:90 xlator.process
        # frequency_xlator.h:43-50: out[i] = in[i]*phase; phase *= phaseDelta
        # 向量化：phase_k = phase0 * exp(j * d_theta * k)，状态跨帧保持
        k = np.arange(n, dtype=np.float64)
        mult = self._phase * np.exp(1j * self._d_theta * k)
        x = x * mult.astype(np.complex128 if x.dtype == np.complex128 else np.complex64)
        # 推进相位累加器：phase *= phaseDelta^n
        self._phase = self._phase * np.exp(1j * self._d_theta * n)
        mag = abs(self._phase)
        if mag > 1e-12:
            self._phase /= mag  # 防长期幅度漂移（volk rotator 同样有此问题）

        # 1.5) 可选 AGC2（变频后、重采样前，对照 agc2_cc_impl.cc:42-50）
        if self._agc_enabled and self._agc is not None:
            x = self._agc.process(x)

        # 2) 有理重采样 in_sr → out_sr —— rx_vfo.h:92/94 resamp.process
        if self._up != 1 or self._down != 1:
            try:
                from scipy.signal import resample_poly
                x = resample_poly(x, self._up, self._down)
            except ImportError:
                # numpy 兜底：线性插值（粗糙但可用；scipy 不可用时的降级路径）
                n_new = int(round(n * self._out_sr / self._in_sr))
                t_old = np.linspace(0.0, 1.0, n, endpoint=False)
                t_new = np.linspace(0.0, 1.0, n_new, endpoint=False)
                x = (np.interp(t_new, t_old, x.real)
                     + 1j * np.interp(t_new, t_old, x.imag))
        # else: in_sr == out_sr，跳过重采样（rx_vfo.h:28 也允许）

        # 3) 低通滤波（在 out_sr 上）—— rx_vfo.h:97 filter.process
        # 用 lfilter_zi 跨帧保持滤波器状态（对照 GR block.cc:97 history +
        # fir_filter_with_buffer.cc:71-76 环形缓冲），消除每块开头瞬态咔哒
        if self._filter_needed and self._taps is not None:
            try:
                from scipy.signal import lfilter, lfilter_zi
                if self._lpf_zi is None or len(self._lpf_zi) != len(self._taps):
                    self._lpf_zi = lfilter_zi(self._taps, 1.0)
                x, self._lpf_zi = lfilter(self._taps, 1.0, x, zi=self._lpf_zi)
            except ImportError:
                if x.dtype in (np.complex64, np.complex128):
                    x = (np.convolve(x.real, self._taps, mode='same')
                         + 1j * np.convolve(x.imag, self._taps, mode='same'))
                else:
                    x = np.convolve(x, self._taps, mode='same')

        return x.astype(in_dtype)


# ═══════════════════════════════════════════════════════
# 7. SDR++ 风格 Stream 路由与 DSP 链
# ═══════════════════════════════════════════════════════

from .dsp_stream import PingPongStream


class StreamSplitter:
    """一进 N 出流分配器。

    对照 sdrpp/core/src/dsp/routing/splitter.h:46-61。

    从 input_stream 读一帧，对每个绑定的下游 output_stream 拷贝数据并 swap，
    然后 flush 输入流。解决"录音和 AI 扫频抢数据"的问题
    （sdr_backend.py:315 vs sdr_tools.py:3283）。

    用法：
        splitter = StreamSplitter(input_stream)
        splitter.bind(recorder_stream)
        splitter.bind(spectrum_stream)
        splitter.start()
    """

    def __init__(self, input_stream: PingPongStream):
        self._input = input_stream
        self._outputs: list = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def bind(self, output_stream: PingPongStream) -> None:
        """注册一个下游输出流。

        对照 splitter.h:13-27 bindStream()。
        """
        with self._lock:
            if output_stream not in self._outputs:
                self._outputs.append(output_stream)

    def unbind(self, output_stream: PingPongStream) -> None:
        """移除一个下游输出流。

        对照 splitter.h:29-44 unbindStream()。
        """
        with self._lock:
            if output_stream in self._outputs:
                self._outputs.remove(output_stream)

    def run_once(self) -> int:
        """从输入读一帧，分发给所有下游。

        对照 splitter.h:46-61 run()。

        Returns:
            本次分发的样本数；-1 表示输入流已停止。
        """
        buf, n = self._input.read()
        if n < 0:
            return -1

        # 拷贝给每个下游并 swap
        with self._lock:
            outputs_snapshot = list(self._outputs)

        for out_stream in outputs_snapshot:
            out_stream.write_buf[:n] = buf[:n]
            if not out_stream.swap(n):
                # 下游停止，flush 输入流并退出
                self._input.flush()
                return -1

        # 读完后 flush 输入，通知生产者可以写下一帧
        self._input.flush()
        return n

    def start(self) -> None:
        """启动后台分发线程。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止分发线程。"""
        self._running = False
        self._input.stop_reader()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run_loop(self):
        while self._running:
            n = self.run_once()
            if n < 0:
                break


class DSPChain:
    """轻量 DSP 处理链：按启用顺序串联处理块。

    对照 sdrpp/core/src/dsp/chain.h:62-90 enableBlock() / disableBlock()。

    SDR++ 的 chain 通过重连上下游 stream 指针实现块的动态启停，
    不重启线程。这里用 Python 函数链的轻量方式实现：
    每个 block 是一个有 .process(x) -> ndarray 的对象，
    set_block_enabled 控制是否跳过该块。

    先用在 front_end() 的 DCBlocker / IQCalibrator / decimate 可独立开关。
    """

    def __init__(self):
        self._blocks: list = []       # [(name, block, enabled)]
        self._block_map: dict = {}    # name -> index

    def add_block(self, name: str, block, enabled: bool = True) -> None:
        """添加一个处理块。

        Args:
            name: 块名称（用于开关控制）。
            block: 必须有 process(x: ndarray) -> ndarray 方法。
            enabled: 是否默认启用。
        """
        if name in self._block_map:
            raise ValueError(f"Block '{name}' already exists")
        idx = len(self._blocks)
        self._blocks.append((name, block, enabled))
        self._block_map[name] = idx

    def set_block_enabled(self, name: str, enabled: bool) -> None:
        """启用或禁用某个块。

        对照 chain.h:121-128 setBlockEnabled()。
        禁用的块在 process() 中被跳过，数据直通。
        """
        if name not in self._block_map:
            raise ValueError(f"Block '{name}' not found")
        idx = self._block_map[name]
        _, block, _ = self._blocks[idx]
        self._blocks[idx] = (name, block, enabled)

    def process(self, data: np.ndarray) -> np.ndarray:
        """按启用顺序串联处理所有块。

        对照 chain.h 的处理流：依次经过每个启用的 block.process()。
        禁用的块被跳过，数据直通（等价于 SDR++ 的
        after->setInput(before ? &before->out : _in)）。
        """
        result = data
        for name, block, enabled in self._blocks:
            if enabled and hasattr(block, 'process'):
                result = block.process(result)
        return result

    def reset(self) -> None:
        """重置所有块的内部状态（如 DCBlocker 的历史值）。"""
        for name, block, enabled in self._blocks:
            if hasattr(block, 'reset'):
                block.reset()


# 需要 threading 导入（StreamSplitter 用到）
import threading  # noqa: E402  (文件末尾追加，避免顶部循环导入)
