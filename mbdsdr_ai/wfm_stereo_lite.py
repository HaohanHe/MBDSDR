"""WFM 广播立体声复合解码（lite，纯 numpy/scipy）。

商用 FM 广播复合基带（mpx，鉴频后、降采样前）标准结构：
    mpx = (L+R)/2  +  A·cos(2π·19k·t)  +  (L-R)/2·cos(2π·38k·t)  (+ 57k RDS)
其中：
  - (L+R)  0~15kHz  主信道（mono 和信号）
  - 19kHz  导频 pilot（锁相参考，固定 cos 相位）
  - (L-R)  DSB-SC 调制在 38kHz ±15kHz（23~53kHz），无载波
  - 57kHz  RDS（见 rds_lite，独立）

立体声解码：
  1. 鉴频得 mpx；
  2. 窄带带通 19kHz + Hilbert 解析信号取 pilot 相位 θ；
  3. 二倍频得 38kHz 相干载波 cos(2θ)；
  4. mpx·cos(2θ) 低通 15kHz 得差信号 (L-R)；
  5. (L+R) 低通 15kHz 得和信号；
  6. L=(和+差)/2，R=(和-差)/2；去加重、降采样、归一化。

往返验证用 synthesize_stereo_iq 生成复合基带并 FM 调制为复基带 IQ。
"""
from __future__ import annotations
import numpy as np
from scipy.signal import butter, sosfiltfilt, hilbert, resample_poly, lfilter
from math import gcd

PILOT_HZ = 19000.0
CARRIER_HZ = 38000.0
AF_CUTOFF = 15000.0


def _resample_to(x: np.ndarray, sr_in: float, sr_out: float) -> np.ndarray:
    if int(round(sr_in)) == int(sr_out):
        return x
    g = gcd(int(round(sr_in)), int(sr_out))
    return resample_poly(x, int(sr_out) // g, int(round(sr_in)) // g)


def synthesize_stereo_iq(
    left: np.ndarray,
    right: np.ndarray,
    audio_sr: float = 48000.0,
    mpx_sr: float = 240000.0,
    deviation: float = 75000.0,
    pilot_amp: float = 0.10,
) -> np.ndarray:
    """把 L/R 音频合成成复合基带并 FM 调制为复基带 IQ（往返验证用）。

    left/right: 音频域波形（任意尺度，内部按峰归一化）。返回 complex64。
    """
    t_a = np.arange(len(left)) / audio_sr
    # 上采样到 mpx 率
    def _up(a):
        return _resample_to(np.asarray(a, dtype=np.float64), audio_sr, mpx_sr)
    L = _up(left).astype(np.float64)
    R = _up(right).astype(np.float64)
    n = min(len(L), len(R))
    L, R = L[:n], R[:n]
    pk = max(np.max(np.abs(L)), np.max(np.abs(R)), 1e-9)
    L, R = L / pk, R / pk
    t = np.arange(n) / mpx_sr
    pilot = pilot_amp * np.cos(2 * np.pi * PILOT_HZ * t)
    carrier = np.cos(2 * np.pi * CARRIER_HZ * t)
    mpx = 0.5 * (L + R) + pilot + 0.5 * (L - R) * carrier
    # FM 调制为复基带瞬时相位
    phase = 2 * np.pi * np.cumsum(mpx * deviation) / mpx_sr
    return np.exp(1j * phase).astype(np.complex64)


def decode_stereo(
    iq: np.ndarray,
    sample_rate: float,
    out_sr: int = 48000,
    deemph_us: float = 50.0,
) -> dict:
    """从 WFM 复基带 IQ 解码出立体声 L/R。

    返回 {l, r, sample_rate, stereo, pilot_snr_db, duration_s}。
    若 19kHz pilot 能量不足（非立体声/信号弱），stereo=False，l==r（mono）。
    """
    x = np.asarray(iq, dtype=np.complex128)
    x = x - np.mean(x)
    if len(x) < int(sample_rate * 0.05):
        return {"l": np.zeros(0), "r": np.zeros(0), "sample_rate": out_sr,
                "stereo": False, "pilot_snr_db": None, "duration_s": 0.0}

    # 1) 正交鉴频 → 复合基带 mpx
    ph = np.angle(x[1:] * np.conj(x[:-1]))
    mpx = ph * (sample_rate / (2.0 * np.pi))  # Hz 单位瞬时频偏
    mpx = mpx - np.mean(mpx)
    nyq = sample_rate / 2.0

    # 2) 窄带提取 19kHz pilot
    b19 = butter(5, [(PILOT_HZ - 800) / nyq, (PILOT_HZ + 800) / nyq], btype="band")
    pilot_band = lfilter(b19[0], b19[1], mpx)
    p_rms = float(np.sqrt(np.mean(pilot_band ** 2)))
    m_rms = float(np.sqrt(np.mean(mpx ** 2))) + 1e-9
    pilot_snr_db = 20.0 * np.log10(p_rms / m_rms + 1e-9)
    is_stereo = p_rms > 0.02 * m_rms

    # 3) 和信号 (L+R)：低通 15kHz
    low = butter(5, AF_CUTOFF / nyq, btype="low")
    S = lfilter(low[0], low[1], mpx)

    if is_stereo:
        # 4) pilot 解析信号相位 θ，二倍频 38kHz 载波
        z = hilbert(pilot_band)
        theta = np.angle(z)
        car = np.cos(2.0 * theta)  # 与合成时 cos(2π38kt) 同相
        D = lfilter(low[0], low[1], mpx * car)  # 差信号 (L-R)
        Lraw = 0.5 * (S + D)
        Rraw = 0.5 * (S - D)
    else:
        Lraw = Rraw = S

    # 5) 降采样到音频率
    Lr = _resample_to(Lraw, sample_rate, out_sr)
    Rr = _resample_to(Rraw, sample_rate, out_sr)

    # 6) 去加重（一阶 RC 低通）
    if deemph_us and deemph_us > 0:
        a = float(np.exp(-1.0 / (deemph_us * 1e-6 * out_sr)))
        Lr = lfilter([1.0 - a], [1.0, -a], Lr)
        Rr = lfilter([1.0 - a], [1.0, -a], Rr)

    # 7) 归一化（L/R 统一幅度，避免削波）
    peak = max(float(np.max(np.abs(Lr))), float(np.max(np.abs(Rr))), 1e-9)
    Lr = Lr / peak * 0.9
    Rr = Rr / peak * 0.9

    return {
        "l": Lr.astype(np.float32),
        "r": Rr.astype(np.float32),
        "sample_rate": out_sr,
        "stereo": bool(is_stereo),
        "pilot_snr_db": round(pilot_snr_db, 1),
        "duration_s": round(len(Lr) / out_sr, 2),
    }
