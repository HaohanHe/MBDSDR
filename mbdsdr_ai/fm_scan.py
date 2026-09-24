"""
MBDSDR AI 内核 - FM 广播自动搜台
==================================
纯 numpy 实现，扫 87-108MHz，步长 100kHz，每个频点采一小段 IQ，
算 FM 载波存在性（中心 bin 功率 vs 噪声底）+ 19kHz 导频存在性（立体声指示），
输出按强度排序的电台列表。

设计要点：
- scan_fm_band() 接受 acquire 回调（同 sweep_scan 模式），因此可对合成信号自测，
  不依赖真硬件。acquire(center_hz, sample_rate, n_samples) -> complex128 ndarray。
- 载波存在性：对采集到的 IQ 做 FFT，取中心 bin（±1 bin）功率与全带噪声底比较。
- 19kHz 导频：先正交鉴频得到复合基带 mpx，再在 19kHz ±500Hz 带通内算能量，
  与 mpx 整体能量比较，超过门限即判立体声。
- 输出 [{freq_mhz, strength_db, stereo, active}]，按 strength_db 降序。

无 scipy 时降级：带通改用 numpy FFT 掩码实现，保证纯 numpy 也能跑。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Callable, List, Optional, Dict, Any

import numpy as np


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class FMStation:
    """一个检出的 FM 广播电台。"""
    freq_mhz: float
    strength_db: float
    stereo: bool
    active: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FMScanResult:
    """一次 FM 扫频的完整结果。"""
    f_start_mhz: float
    f_stop_mhz: float
    step_khz: float
    sample_rate_hz: float
    dwell_samples: int
    noise_floor_db: float
    stations: List[FMStation]
    scan_time_s: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stations"] = [s.to_dict() for s in self.stations]
        return d


# ═══════════════════════════════════════════════════════════════
# 核心 DSP：单频点载波 + 导频检测
# ═══════════════════════════════════════════════════════════════

def _carrier_strength(iq: np.ndarray, sample_rate: float,
                       channel_bw_hz: float = 150000.0) -> tuple:
    """计算 FM 信道功率与带外噪声底，返回 (strength_db, noise_floor_db)。

    FM 是恒包络角调制信号，功率分散在 ~150kHz 信道内，中心 bin 不突出。
    因此取中心 ±channel_bw/2 内的**平均功率谱密度**作为信道功率，
    取信道外（±1.5*channel_bw 以外）的中位数功率谱密度作为噪声底，
    两者之差即为信号强度（高于噪声底多少 dB）。

    要求 sample_rate > 3 * channel_bw，否则带外区域不足，退化为全谱中位数。
    """
    n = len(iq)
    if n < 16:
        return -120.0, -120.0
    x = iq - np.mean(iq)
    window = np.hanning(n)
    spec = np.fft.fftshift(np.fft.fft(x * window))
    power = np.abs(spec) ** 2 / n
    center = n // 2
    bin_hz = sample_rate / n

    # 信道半宽（bin 数）
    half_ch = max(1, int(round(channel_bw_hz / 2 / bin_hz)))
    # 带外起始：信道外 50% 余量
    half_guard = max(half_ch + 1, int(round(channel_bw_hz * 0.75 / bin_hz)))

    ch_lo = max(0, center - half_ch)
    ch_hi = min(n, center + half_ch + 1)
    channel_psd = float(np.mean(power[ch_lo:ch_hi]))

    # 带外：左右两侧 beyond half_guard
    out_mask = np.ones(n, dtype=bool)
    out_mask[max(0, center - half_guard):min(n, center + half_guard + 1)] = False
    if np.any(out_mask) and sample_rate > 2.5 * channel_bw_hz:
        noise_psd = float(np.median(power[out_mask]))
    else:
        # 采样率不够宽，退化为全谱中位数（排除极窄中心区）
        fallback_mask = np.ones(n, dtype=bool)
        fallback_mask[max(0, center - 3):min(n, center + 4)] = False
        noise_psd = float(np.median(power[fallback_mask]))

    noise_db = 10.0 * np.log10(noise_psd + 1e-18)
    strength_db = 10.0 * np.log10(channel_psd + 1e-18) - noise_db
    return round(strength_db, 1), round(noise_db, 1)


def _detect_pilot_19k(iq: np.ndarray, sample_rate: float,
                       threshold_db: float = -18.0) -> tuple:
    """检测 FM 复合基带中的 19kHz 立体声导频。

    返回 (stereo: bool, pilot_snr_db: float)。
    流程：正交鉴频 -> mpx -> 19kHz ±500Hz 带通能量 vs mpx 总能量。
    无 scipy 时用 FFT 掩码做带通。
    """
    n = len(iq)
    if n < 64 or sample_rate < 60000:
        return False, None
    x = np.asarray(iq, dtype=np.complex128)
    x = x - np.mean(x)
    # 正交鉴频 -> 复合基带 mpx（频偏归一化到 75kHz）
    phase = np.angle(x[1:] * np.conj(x[:-1]))
    mpx = phase * (sample_rate / (2.0 * np.pi * 75000.0))
    mpx = mpx - np.mean(mpx)
    m = len(mpx)
    if m < 64:
        return False, None

    # FFT 带通：19kHz ±500Hz
    freqs = np.fft.rfftfreq(m, 1.0 / sample_rate)
    spec = np.fft.rfft(mpx * np.hanning(m))
    power = np.abs(spec) ** 2
    pilot_mask = (freqs >= 18500.0) & (freqs <= 19500.0)
    if not np.any(pilot_mask):
        return False, None
    pilot_pwr = float(np.sum(power[pilot_mask]))
    total_pwr = float(np.sum(power)) + 1e-18
    pilot_snr = 10.0 * np.log10(pilot_pwr / total_pwr + 1e-18)
    stereo = pilot_snr > threshold_db
    return stereo, round(pilot_snr, 1)


def analyze_fm_frequency(iq: np.ndarray, sample_rate: float,
                         carrier_threshold_db: float = 3.0) -> Dict[str, Any]:
    """对单个频点的 IQ 做完整分析：载波强度 + 立体声导频。

    返回 {strength_db, noise_floor_db, stereo, pilot_snr_db, active}。
    active = 载波强度超过门限。
    """
    strength_db, noise_db = _carrier_strength(iq, sample_rate)
    stereo, pilot_snr = _detect_pilot_19k(iq, sample_rate)
    active = strength_db >= carrier_threshold_db
    return {
        "strength_db": strength_db,
        "noise_floor_db": noise_db,
        "stereo": bool(stereo),
        "pilot_snr_db": pilot_snr,
        "active": bool(active),
    }


# ═══════════════════════════════════════════════════════════════
# 扫频主函数
# ═══════════════════════════════════════════════════════════════

def scan_fm_band(
    acquire: Callable[[float, float, int], Optional[np.ndarray]],
    f_start_mhz: float = 87.0,
    f_stop_mhz: float = 108.0,
    step_khz: float = 100.0,
    sample_rate_hz: float = 1000000.0,
    dwell_samples: int = 16384,
    carrier_threshold_db: float = 2.0,
) -> FMScanResult:
    """扫 FM 广播频段，返回按强度排序的电台列表。

    Parameters
    ----------
    acquire : callable
        acquire(center_hz, sample_rate, n_samples) -> complex ndarray 或 None。
        由调用方提供（真硬件后端 / 合成信号生成器），使本函数不依赖硬件。
    f_start_mhz, f_stop_mhz : float
        扫描范围，默认 87-108MHz。
    step_khz : float
        步进，默认 100kHz。
    sample_rate_hz : float
        每个频点的 IQ 采样率，默认 1MHz（FM 信道 150kHz，需 >3x 才有带外噪声底）。
    dwell_samples : int
        每个频点采集样本数，默认 16384（≈16ms @1M）。
    carrier_threshold_db : float
        载波存在门限（信道功率谱密度 - 带外噪声底），默认 2dB。

    Returns
    -------
    FMScanResult
    """
    t0 = time.time()
    f_start = f_start_mhz * 1e6
    f_stop = f_stop_mhz * 1e6
    step = step_khz * 1e3
    freqs = np.arange(f_start, f_stop + step / 2, step)

    stations: List[FMStation] = []
    noise_readings: List[float] = []

    for center in freqs:
        try:
            iq = acquire(float(center), sample_rate_hz, dwell_samples)
        except Exception:
            iq = None
        if iq is None or len(iq) < 16:
            continue
        iq = np.asarray(iq, dtype=np.complex128)
        info = analyze_fm_frequency(iq, sample_rate_hz, carrier_threshold_db)
        noise_readings.append(info["noise_floor_db"])
        if info["active"]:
            stations.append(FMStation(
                freq_mhz=round(center / 1e6, 3),
                strength_db=info["strength_db"],
                stereo=info["stereo"],
                active=True,
            ))

    # 按强度降序
    stations.sort(key=lambda s: s.strength_db, reverse=True)
    noise_floor = float(np.median(noise_readings)) if noise_readings else -120.0

    return FMScanResult(
        f_start_mhz=f_start_mhz,
        f_stop_mhz=f_stop_mhz,
        step_khz=step_khz,
        sample_rate_hz=sample_rate_hz,
        dwell_samples=dwell_samples,
        noise_floor_db=round(noise_floor, 1),
        stations=stations,
        scan_time_s=round(time.time() - t0, 2),
    )


# ═══════════════════════════════════════════════════════════════
# 合成信号自测（不依赖硬件）
# ═══════════════════════════════════════════════════════════════

def synthesize_fm_station(
    freq_offset_hz: float = 0.0,
    sample_rate: float = 240000.0,
    n_samples: int = 16384,
    stereo: bool = True,
    audio_tone_hz: float = 1000.0,
    deviation: float = 75000.0,
    noise_level: float = 0.05,
    pilot_amp: float = 0.10,
) -> np.ndarray:
    """合成一个 FM 广播电台的复基带 IQ（用于自测）。

    生成包含：单声道/立体声复合基带 -> FM 调制 -> 加噪声。
    立体声时含 19kHz 导频和 38kHz 差信号副载波。

    Parameters
    ----------
    freq_offset_hz : float
        信号相对中心频率的偏移（模拟未精确调谐），默认 0。
    sample_rate : float
        IQ 采样率。
    n_samples : int
        样本数。
    stereo : bool
        是否生成立体声复合信号（含 19k pilot）。
    audio_tone_hz : float
        调制音频音调频率。
    deviation : float
        FM 最大频偏，广播标准 75kHz。
    noise_level : float
        加性高斯噪声标准差。
    pilot_amp : float
        19kHz 导频幅度（相对复合基带）。
    """
    t = np.arange(n_samples) / sample_rate
    # 音频调制信号
    audio = 0.5 * np.sin(2 * np.pi * audio_tone_hz * t)

    if stereo:
        # 立体声复合基带：(L+R)/2 + pilot + (L-R)/2 * cos(38k)
        # 这里 L=R=audio，所以差信号为 0，但 pilot 仍存在
        pilot = pilot_amp * np.cos(2 * np.pi * 19000.0 * t)
        # 给一点差信号（L 比 R 多一点），让立体声更真实
        diff = 0.1 * np.sin(2 * np.pi * audio_tone_hz * t)
        carrier_38k = np.cos(2 * np.pi * 38000.0 * t)
        mpx = audio + pilot + diff * carrier_38k
    else:
        mpx = audio

    # FM 调制：瞬时相位 = 2π * deviation * ∫mpx dt
    phase = 2 * np.pi * deviation * np.cumsum(mpx) / sample_rate
    iq = np.exp(1j * phase)

    # 频率偏移（模拟未精确调谐到台）
    if abs(freq_offset_hz) > 1e-6:
        iq = iq * np.exp(1j * 2 * np.pi * freq_offset_hz * t)

    # 加噪声
    noise = noise_level * (np.random.randn(n_samples) + 1j * np.random.randn(n_samples))
    return (iq + noise).astype(np.complex128)


def self_test() -> Dict[str, Any]:
    """纯软件自测：合成一个已知 FM 台，验证能检出。

    返回 {success, detected_freq, expected_freq, stereo_detected, strength_db}。
    """
    # 合成一个在 98.0MHz 的立体声 FM 台（相对扫描中心偏移 0）
    # 扫描时 acquire 回调在 98.0MHz 返回这个信号，其他频点返回噪声
    station_freq = 98.0e6

    def acquire(center_hz, sr, n):
        if abs(center_hz - station_freq) < 50000:
            return synthesize_fm_station(
                freq_offset_hz=0.0, sample_rate=sr, n_samples=n,
                stereo=True, noise_level=0.05,
            )
        # 其他频点只有噪声
        return 0.05 * (np.random.randn(n) + 1j * np.random.randn(n))

    # 只扫 97.5-98.5MHz 加速自测
    result = scan_fm_band(
        acquire, f_start_mhz=97.5, f_stop_mhz=98.5,
        step_khz=100.0, sample_rate_hz=1000000.0, dwell_samples=16384,
    )
    if not result.stations:
        return {"success": False, "error": "未检出任何电台", "stations": []}
    top = result.stations[0]
    return {
        "success": True,
        "detected_freq_mhz": top.freq_mhz,
        "expected_freq_mhz": 98.0,
        "stereo_detected": top.stereo,
        "strength_db": top.strength_db,
        "total_stations": len(result.stations),
        "noise_floor_db": result.noise_floor_db,
    }


# ═══════════════════════════════════════════════════════════════
# 预设持久化
# ═══════════════════════════════════════════════════════════════

PRESETS_DIR = os.path.expanduser("~/.mbdsdr")
PRESETS_FILE = os.path.join(PRESETS_DIR, "fm_presets.json")


def load_presets() -> Dict[str, Any]:
    """加载 ~/.mbdsdr/fm_presets.json，不存在则返回空结构。"""
    if not os.path.exists(PRESETS_FILE):
        return {"stations": [], "last_scan": None, "updated_at": None}
    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return {"stations": [], "last_scan": None, "updated_at": None}


def save_presets(stations: List[Dict[str, Any]], scan_meta: Optional[Dict] = None) -> str:
    """保存电台列表到 ~/.mbdsdr/fm_presets.json。

    stations: [{freq_mhz, strength_db, stereo, active, name?}]
    scan_meta: 可选的扫描元信息（范围、时间等）。
    返回保存的文件路径。
    """
    os.makedirs(PRESETS_DIR, exist_ok=True)
    data = {
        "stations": stations,
        "last_scan": scan_meta or {},
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return PRESETS_FILE


def compare_with_last_scan(current_stations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对比当前扫描结果与上次保存的预设，返回新增/消失/持续的电台。

    匹配容差 ±50kHz（同一台可能因频偏落在不同 bin）。
    """
    old = load_presets()
    old_stations = old.get("stations", [])
    old_freqs = {s["freq_mhz"] for s in old_stations if s.get("active", True)}
    cur_freqs = {s["freq_mhz"] for s in current_stations if s.get("active", True)}

    def _match(freq, freq_set, tol=0.05):
        return any(abs(freq - f) <= tol for f in freq_set)

    new_stations = [s for s in current_stations if not _match(s["freq_mhz"], old_freqs)]
    gone_stations = [s for s in old_stations if not _match(s["freq_mhz"], cur_freqs) and s.get("active", True)]
    persistent = [s for s in current_stations if _match(s["freq_mhz"], old_freqs)]

    return {
        "new": new_stations,
        "gone": gone_stations,
        "persistent": persistent,
        "last_updated": old.get("updated_at"),
    }


if __name__ == "__main__":
    print("=== FM Scan 自测 ===")
    r = self_test()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("success"):
        print(f"\n✓ 自测通过：检出 {r['detected_freq_mhz']}MHz "
              f"(期望 98.0), 立体声={r['stereo_detected']}, "
              f"强度={r['strength_db']}dB")
    else:
        print("\n✗ 自测失败")
