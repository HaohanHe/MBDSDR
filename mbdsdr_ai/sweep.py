"""
宽带扫频与活动信号扫描（wideband sweep / activity scanner）
=========================================================

RTL-SDR 一类接收机瞬时带宽有限（典型 2.4 MHz），无法一次看清整个频段。
本模块通过「步进调谐 → 逐段功率谱 → 重叠拼接 → 噪声底门限 → 活动段提取」，
在远大于瞬时带宽的范围内自动找出正在发射的频点，对标 SDR++ 的
Frequency Scanner / GQRX 的扫频记录，也是「找台 / 找干扰源」技能的底层引擎。

设计为可注入采集回调的纯函数，便于离线用合成信号严格验证，
真实设备只需提供 acquire(center_hz, sample_rate_hz, n) -> 复数 IQ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np


@dataclass
class Activity:
    center_hz: float
    bandwidth_hz: float
    peak_db: float
    mean_db: float
    low_hz: float
    high_hz: float

    def to_dict(self) -> dict:
        return {
            "center_hz": float(self.center_hz),
            "center_mhz": round(self.center_hz / 1e6, 4),
            "bandwidth_hz": float(self.bandwidth_hz),
            "peak_db": round(float(self.peak_db), 2),
            "mean_db": round(float(self.mean_db), 2),
            "low_mhz": round(self.low_hz / 1e6, 4),
            "high_mhz": round(self.high_hz / 1e6, 4),
        }


@dataclass
class SweepResult:
    freqs_hz: np.ndarray            # 拼接后频率轴（Hz），单调递增
    power_db: np.ndarray            # 对应功率谱密度（dB，相对值）
    activities: List[Activity] = field(default_factory=list)
    noise_floor_db: float = 0.0
    threshold_db: float = 0.0
    centers_scanned: List[float] = field(default_factory=list)

    def to_dict(self, max_points: int = 0) -> dict:
        n = len(self.freqs_hz)
        idxs = np.arange(n)
        if max_points and n > max_points:
            idxs = np.linspace(0, n - 1, max_points).astype(int)
        return {
            "freqs_mhz": (self.freqs_hz[idxs] / 1e6).round(4).tolist(),
            "power_db": self.power_db[idxs].round(2).tolist(),
            "noise_floor_db": round(float(self.noise_floor_db), 2),
            "threshold_db": round(float(self.threshold_db), 2),
            "activities": [a.to_dict() for a in self.activities],
            "centers_scanned_mhz": [round(c / 1e6, 3) for c in self.centers_scanned],
        }


def _segment_psd(iq: np.ndarray, sample_rate: float, fft_size: int = 2048):
    """对一段 IQ 做 Welch 风格平均周期图，返回 (freqs_offset, psd_db)。"""
    iq = np.asarray(iq, dtype=np.complex128)
    iq = iq - np.mean(iq)
    n_avail = len(iq)
    if n_avail < fft_size:
        fft_size = max(64, 1 << int(np.log2(max(64, n_avail))))
    hop = fft_size // 2

    window = np.hanning(fft_size)
    win_power = np.sum(window ** 2)
    psd_acc = np.zeros(fft_size)
    n_frames = 0
    for start in range(0, n_avail - fft_size + 1, hop):
        frame = iq[start:start + fft_size] * window
        spec = np.fft.fftshift(np.fft.fft(frame))
        psd_acc += np.abs(spec) ** 2
        n_frames += 1
    if n_frames == 0:
        frame = np.concatenate([iq, np.zeros(fft_size - n_avail)]) * window
        spec = np.fft.fftshift(np.fft.fft(frame))
        psd_acc = np.abs(spec) ** 2
        n_frames = 1

    psd = psd_acc / (n_frames * win_power * sample_rate)
    psd_db = 10.0 * np.log10(psd + 1e-20)
    freqs_offset = (np.arange(fft_size) - fft_size / 2) * (sample_rate / fft_size)
    return freqs_offset, psd_db


def sweep_scan(
    acquire: Callable[[float, float, int], np.ndarray],
    f_start_hz: float,
    f_stop_hz: float,
    sample_rate_hz: float,
    step_hz: Optional[float] = None,
    overlap: float = 0.5,
    dwell_samples: int = 16384,
    fft_size: int = 2048,
    margin_db: float = 6.0,
    threshold_db: Optional[float] = None,
    merge_gap_hz: Optional[float] = None,
    min_bw_hz: float = 1e3,
) -> SweepResult:
    """
    在 [f_start_hz, f_stop_hz] 内步进调谐扫描，拼接频谱并提取活动信号。

    acquire(center_hz, sample_rate_hz, n_samples) -> np.ndarray(complex)
        由真实后端（set_frequency + read_samples）或测试合成源提供。
    overlap      : 相邻调谐段重叠比例（0~0.9），默认 0.5，抑制边缘滚降。
    margin_db    : 门限 = 噪声底(中位数) + margin_db（threshold_db 给定时用绝对门限）。
    merge_gap_hz : 活动段之间小于该间隔则合并，默认 2 个 FFT bin。
    min_bw_hz    : 小于该带宽的活动段丢弃（抑制孤立毛刺）。
    """
    if f_stop_hz < f_start_hz:
        f_start_hz, f_stop_hz = f_stop_hz, f_start_hz

    sr = float(sample_rate_hz)
    half = sr / 2.0
    if step_hz is None:
        step_hz = sr * max(0.1, min(0.9, overlap))
    step_hz = float(step_hz)

    # 调谐中心：让第一段左边缘覆盖 f_start，最后一段覆盖 f_stop
    first_center = f_start_hz + half
    last_center = f_stop_hz - half
    if last_center < first_center:
        # 范围小于一个瞬时带宽，只在中央采一次
        centers = [(f_start_hz + f_stop_hz) / 2.0]
    else:
        centers = list(np.arange(first_center, last_center + 1e-6, step_hz))
        if centers[-1] < last_center - 1e-6:
            centers.append(last_center)

    bin_hz = sr / fft_size
    # 全局网格：覆盖 f_start-half .. f_stop+half（含边缘半带宽）
    g_lo = f_start_hz - half
    g_hi = f_stop_hz + half
    n_grid = int(np.ceil((g_hi - g_lo) / bin_hz)) + 1
    grid_freqs = g_lo + np.arange(n_grid) * bin_hz
    power_sum = np.zeros(n_grid)
    weight = np.zeros(n_grid)

    for center in centers:
        iq = acquire(center, sr, dwell_samples)
        if iq is None or len(iq) == 0:
            continue
        foff, pdb = _segment_psd(iq, sr, fft_size)
        abs_freqs = center + foff
        idx = np.round((abs_freqs - g_lo) / bin_hz).astype(int)
        valid = (idx >= 0) & (idx < n_grid)
        idx = idx[valid]
        # dB 转线性累加再回转，正确处理重叠平均
        lin = 10.0 ** (pdb[valid] / 10.0)
        np.add.at(power_sum, idx, lin)
        np.add.at(weight, idx, 1.0)

    covered = weight > 0
    power_lin = np.zeros(n_grid)
    power_lin[covered] = power_sum[covered] / weight[covered]
    power_db = np.full(n_grid, np.nan)
    power_db[covered] = 10.0 * np.log10(power_lin[covered] + 1e-20)

    # 只在用户请求范围内做检测（边缘半带宽仅用于拼接平滑）
    in_range = (grid_freqs >= f_start_hz) & (grid_freqs <= f_stop_hz) & covered
    if not np.any(in_range):
        return SweepResult(grid_freqs, power_db, [], 0.0, 0.0, centers)

    band = power_db[in_range]
    noise = float(np.median(band))
    thr = float(threshold_db) if threshold_db is not None else noise + margin_db

    gap = merge_gap_hz if merge_gap_hz is not None else 2.0 * bin_hz
    activities = _extract_activities(
        grid_freqs[in_range], band, thr, gap, min_bw_hz
    )

    return SweepResult(
        freqs_hz=grid_freqs,
        power_db=power_db,
        activities=activities,
        noise_floor_db=noise,
        threshold_db=thr,
        centers_scanned=centers,
    )


def _extract_activities(freqs: np.ndarray, power_db: np.ndarray,
                        threshold_db: float, merge_gap_hz: float,
                        min_bw_hz: float) -> List[Activity]:
    """在超门限的连续段上提取活动信号，并按频率间隙合并。"""
    above = power_db >= threshold_db
    activities: List[Activity] = []
    i = 0
    n = len(freqs)
    while i < n:
        if not above[i]:
            i += 1
            continue
        j = i
        # 允许小于 merge_gap 的凹陷把相邻段合并
        gap_bins = max(1, int(merge_gap_hz / max(1.0, freqs[1] - freqs[0])))
        gap_count = 0
        k = i
        while k < n:
            if above[k]:
                gap_count = 0
                j = k
            else:
                gap_count += 1
                if gap_count > gap_bins:
                    break
            k += 1
        seg_f = freqs[i:j + 1]
        seg_p = power_db[i:j + 1]
        mask = seg_p >= threshold_db
        low = float(seg_f[mask][0])
        high = float(seg_f[mask][-1])
        bw = high - low
        if bw >= min_bw_hz:
            peak = float(np.max(seg_p))
            activities.append(Activity(
                center_hz=(low + high) / 2.0,
                bandwidth_hz=bw,
                peak_db=peak,
                mean_db=float(np.mean(seg_p[mask])),
                low_hz=low,
                high_hz=high,
            ))
        i = j + 1
    activities.sort(key=lambda a: a.peak_db, reverse=True)
    return activities
