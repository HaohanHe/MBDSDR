# SPDX-License-Identifier: MIT
"""FT4 独立复基带前端：GFSK 合成、软能量与 Costas 时频同步。

协议依据为 2020 QEX《FT4 and FT8 Protocols》PDF 第 3 页：FT4 的
``T=0.048 s``、``h=1``、``BT=1``、四个脉冲幅度 0..3，以及式 (1)--(3)
的连续相位 GFSK。帧布局复用 :mod:`mbdsdr_ai.ft4_codec`，不在这里复制。

默认 12000 S/s、576 样本/符号是便于本实现验证的采样选择，不是新增协议
常量。接收搜索范围、粗/细步长、脉冲截断和同步门限同样属于实现策略。
本模块不承诺 WSJT-X 波形逐样本一致、特定弱信号门限或真实 OTA 性能。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.special import erf

from mbdsdr_ai.ft4_codec import (
    DATA_POSITIONS,
    FRAME_SYMBOLS,
    SYNC_SEQUENCES,
    build_frame,
    decode_ft4_payload,
    encode_ft4_tones,
)

FS_HZ = 12_000
NSPS = 576
SYMBOL_SEC = 0.048
MODULATION_INDEX = 1.0
BT = 1.0
N_TONES = 4
TONE_SPACING_HZ = 1.0 / SYMBOL_SEC
FRAME_SAMPLES = FRAME_SYMBOLS * NSPS
_PULSE_SPAN_SYMBOLS = 4
_GAUSSIAN_K = math.pi * math.sqrt(2.0 / math.log(2.0))

SYNC_TAPS = tuple(
    (block_start + index, tone)
    for block_start, sequence in zip((1, 34, 67, 100), SYNC_SEQUENCES)
    for index, tone in enumerate(sequence)
)

__all__ = [
    "FS_HZ",
    "NSPS",
    "SYMBOL_SEC",
    "MODULATION_INDEX",
    "BT",
    "N_TONES",
    "TONE_SPACING_HZ",
    "FRAME_SAMPLES",
    "SYNC_TAPS",
    "gaussian_frequency_pulse",
    "Ft4Modulator",
    "aligned_tone_energies",
    "Ft4SyncResult",
    "Ft4Receiver",
]


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} 必须是正整数")
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return int(value)


def _finite_float(value, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限数值")
    return result


def _validate_rates(fs: int, nsps: int) -> tuple[int, int]:
    fs = _positive_int(fs, "fs")
    nsps = _positive_int(nsps, "nsps")
    if not math.isclose(nsps / fs, SYMBOL_SEC, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("nsps/fs 必须等于 FT4 的 0.048 s 符号时长")
    return fs, nsps


def _validate_base_frequency(base_freq_hz: float, fs: int) -> float:
    base = _finite_float(base_freq_hz, "base_freq_hz")
    if base <= -fs / 2.0 or base + 3.0 * TONE_SPACING_HZ >= fs / 2.0:
        raise ValueError("四个标称音调必须位于采样奈奎斯特区间内")
    return base


def _samples_iq(samples) -> np.ndarray:
    try:
        iq = np.asarray(samples, dtype=np.complex128)
    except (TypeError, ValueError) as exc:
        raise ValueError("samples 必须是一维有限数值音频") from exc
    if iq.ndim != 1:
        raise ValueError("samples 必须是一维实音频或复基带")
    if not bool(np.isfinite(iq).all()):
        raise ValueError("samples 不得包含 NaN 或 Inf")
    return iq


def _frame_tones(frame: Sequence[int]) -> np.ndarray:
    try:
        values = np.asarray(frame, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("frame 必须是 105 个整数音调") from exc
    if values.shape != (FRAME_SYMBOLS,):
        raise ValueError(f"frame 形状必须为 ({FRAME_SYMBOLS},)，实际为 {values.shape}")
    if not bool(np.isfinite(values).all()):
        raise ValueError("frame 不得包含 NaN 或 Inf")
    if not bool((values == np.floor(values)).all()) or not bool(
        ((values >= 0) & (values < N_TONES)).all()
    ):
        raise ValueError("frame 只能包含整数音调 0、1、2、3")
    return values.astype(np.int8)


def gaussian_frequency_pulse(times, symbol_sec: float = SYMBOL_SEC,
                             bt: float = BT) -> np.ndarray:
    """计算 QEX 式 (3) 的单位面积高斯平滑矩形频偏脉冲。

    ``p(t) = [erf(kBT(t/T+1/2))-erf(kBT(t/T-1/2))]/(2T)``，
    其中 ``k=π√(2/ln2)``。FT4 调用必须使用 ``BT=1``。
    """
    T = _finite_float(symbol_sec, "symbol_sec")
    bandwidth_time = _finite_float(bt, "bt")
    if T <= 0.0 or bandwidth_time <= 0.0:
        raise ValueError("symbol_sec 和 bt 必须为正数")
    try:
        t = np.asarray(times, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("times 必须是有限数值") from exc
    if not bool(np.isfinite(t).all()):
        raise ValueError("times 不得包含 NaN 或 Inf")
    scaled = _GAUSSIAN_K * bandwidth_time * (t / T)
    edge = 0.5 * _GAUSSIAN_K * bandwidth_time
    return (erf(scaled + edge) - erf(scaled - edge)) / (2.0 * T)


class Ft4Modulator:
    """按 QEX 频偏脉冲生成 FT4 复基带，tone 0 的标称频率为 0 Hz。"""

    def __init__(self, fs: int = FS_HZ, nsps: int = NSPS) -> None:
        self.fs, self.nsps = _validate_rates(fs, nsps)
        self.frame_samples = FRAME_SYMBOLS * self.nsps

    def _frequency_deviation(self, frame: np.ndarray) -> np.ndarray:
        # p(t) 无限延伸；±4T 截断仅是可复现的数值合成策略。样本位于各
        # 采样区间中心，符号 n 的矩形脉冲中心为 (n+1/2)T。
        local_t = (np.arange(self.nsps, dtype=np.float64) + 0.5) / self.fs \
            - SYMBOL_SEC / 2.0
        deviation = np.zeros(self.frame_samples, dtype=np.float64)
        for offset in range(-_PULSE_SPAN_SYMBOLS, _PULSE_SPAN_SYMBOLS + 1):
            weights = np.zeros(FRAME_SYMBOLS, dtype=np.float64)
            if offset >= 0:
                weights[:FRAME_SYMBOLS - offset] = frame[offset:]
            else:
                weights[-offset:] = frame[:FRAME_SYMBOLS + offset]
            pulse = gaussian_frequency_pulse(local_t - offset * SYMBOL_SEC)
            deviation += np.repeat(weights, self.nsps) * np.tile(
                pulse, FRAME_SYMBOLS
            )
        return MODULATION_INDEX * deviation

    def modulate(self, frame: Sequence[int], *, freq_offset_hz: float = 0.0,
                 time_offset_samples: int = 0, awgn_snr_db: float | None = None,
                 noise_seed: int = 0) -> np.ndarray:
        """合成 105 符号连续相位 GFSK，并可注入时偏、频偏和固定种子 AWGN。

        首尾 ramp 符号使用升余弦幅度包络；具体包络形状是本合成器选择，
        QEX 只规定其缓慢升降用途。返回值包含前置 ``time_offset_samples``
        个零样本，AWGN 则加到包含前置零的整个返回捕获上。
        """
        tones = _frame_tones(frame)
        base = _validate_base_frequency(freq_offset_hz, self.fs)
        if isinstance(time_offset_samples, bool) or not isinstance(
            time_offset_samples, (int, np.integer)
        ) or time_offset_samples < 0:
            raise ValueError("time_offset_samples 必须是非负整数")
        delay = int(time_offset_samples)

        deviation = self._frequency_deviation(tones)
        increments = (deviation[:-1] + deviation[1:]) * (0.5 / self.fs)
        phase = 2.0 * math.pi * np.concatenate(([0.0], np.cumsum(increments)))
        iq = np.exp(1j * phase)

        # 只给协议规定的首尾 R 符号加幅度渐变，中段保持单位幅度。
        ramp_position = np.arange(self.nsps, dtype=np.float64) / (self.nsps - 1)
        ramp = 0.5 - 0.5 * np.cos(math.pi * ramp_position)
        iq[:self.nsps] *= ramp
        iq[-self.nsps:] *= ramp[::-1]

        t = np.arange(self.frame_samples, dtype=np.float64) / self.fs
        iq *= np.exp(2j * math.pi * base * t)
        signal_power = float(np.mean(np.abs(iq) ** 2))
        if delay:
            iq = np.concatenate((np.zeros(delay, dtype=np.complex128), iq))

        if awgn_snr_db is not None:
            snr_db = _finite_float(awgn_snr_db, "awgn_snr_db")
            rng = np.random.default_rng(noise_seed)
            noise_power = signal_power / (10.0 ** (snr_db / 10.0))
            sigma = math.sqrt(noise_power / 2.0)
            iq = iq + sigma * (
                rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)
            )
        return iq


def aligned_tone_energies(samples, start_sample: int, base_freq_hz: float = 0.0,
                          fs: int = FS_HZ, nsps: int = NSPS) -> np.ndarray:
    """从已知起点提取已对齐 105×4 非相干逐音能量。

    每符号与四个标称音做矩形窗复相关，返回 ``|相关|²/nsps²``；理想恒定
    单位幅度音因此得到约 1 的能量。此函数本身不做搜索或符号判决。
    """
    fs, nsps = _validate_rates(fs, nsps)
    base = _validate_base_frequency(base_freq_hz, fs)
    if isinstance(start_sample, bool) or not isinstance(
        start_sample, (int, np.integer)
    ) or start_sample < 0:
        raise ValueError("start_sample 必须是非负整数")
    start = int(start_sample)
    iq = _samples_iq(samples)
    required = FRAME_SYMBOLS * nsps
    if start + required > iq.size:
        raise ValueError("从 start_sample 开始不足以提取完整 105 符号帧")

    local_t = (np.arange(nsps, dtype=np.float64) + 0.5) / fs
    tone_freqs = base + np.arange(N_TONES, dtype=np.float64) * TONE_SPACING_HZ
    oscillators = np.exp(-2j * math.pi * tone_freqs[:, None] * local_t[None, :])
    frame = iq[start:start + required].reshape(FRAME_SYMBOLS, nsps)
    correlations = frame @ oscillators.T
    return (np.abs(correlations) ** 2) / float(nsps * nsps)


@dataclass
class Ft4SyncResult:
    """时频同步结果；失败时软能量和硬符号均为空，不伪造帧。"""

    synced: bool = False
    base_freq_hz: float = 0.0
    time_offset_samples: int = 0
    sync_score: float = 0.0
    sync_ratio: float = 0.0
    sync_contrast: float = 0.0
    reason: str = ""
    n_time_bins: int = 0
    n_freq_bins: int = 0
    symbols: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int8))
    energies: np.ndarray = field(default_factory=lambda: np.empty((0, N_TONES)))


class Ft4Receiver:
    """四组 Costas 的盲时频粗搜索、局部细化与 FT4 软解码。

    搜索遍历捕获中所有合法粗时间格点和配置的频率格点，再在最佳峰附近
    细化；不会使用调用者提供的真实偏移。默认范围和门限是接收器策略，
    并非协议常量。捕获只需能容纳一帧，模块不规定固定 T/R 窗长。
    """

    def __init__(self, fs: int = FS_HZ, nsps: int = NSPS,
                 freq_search_hz: tuple[float, float] = (-80.0, 80.0),
                 coarse_freq_step_hz: float = 4.0,
                 coarse_time_step_samples: int = 72,
                 sync_ratio_threshold: float = 1.20,
                 sync_contrast_threshold: float = 2.5) -> None:
        self.fs, self.nsps = _validate_rates(fs, nsps)
        if not isinstance(freq_search_hz, (tuple, list)) or len(freq_search_hz) != 2:
            raise ValueError("freq_search_hz 必须是 (low, high)")
        self.freq_lo = _finite_float(freq_search_hz[0], "freq_search_hz[0]")
        self.freq_hi = _finite_float(freq_search_hz[1], "freq_search_hz[1]")
        if self.freq_lo >= self.freq_hi:
            raise ValueError("频率搜索下限必须小于上限")
        _validate_base_frequency(self.freq_lo, self.fs)
        _validate_base_frequency(self.freq_hi, self.fs)
        self.coarse_freq_step = _finite_float(
            coarse_freq_step_hz, "coarse_freq_step_hz"
        )
        if self.coarse_freq_step <= 0.0:
            raise ValueError("coarse_freq_step_hz 必须为正数")
        self.coarse_time_step = _positive_int(
            coarse_time_step_samples, "coarse_time_step_samples"
        )
        if self.coarse_time_step > self.nsps:
            raise ValueError("粗时间步长不得超过一个符号")
        self.ratio_threshold = _finite_float(
            sync_ratio_threshold, "sync_ratio_threshold"
        )
        self.contrast_threshold = _finite_float(
            sync_contrast_threshold, "sync_contrast_threshold"
        )
        if self.ratio_threshold < 1.0 or self.contrast_threshold < 1.0:
            raise ValueError("同步峰比和 Costas 对比门限均不得小于 1")
        self.frame_samples = FRAME_SYMBOLS * self.nsps

    def _score_plane(self, iq: np.ndarray, frequencies: np.ndarray,
                     starts: np.ndarray) -> np.ndarray:
        local_t = (np.arange(self.nsps, dtype=np.float64) + 0.5) / self.fs
        sample_offsets = np.arange(self.nsps, dtype=np.int64)
        plane = np.zeros((frequencies.size, starts.size), dtype=np.float64)
        for freq_index, base in enumerate(frequencies):
            tone_freqs = base + np.arange(N_TONES) * TONE_SPACING_HZ
            oscillators = np.exp(
                -2j * math.pi * tone_freqs[:, None] * local_t[None, :]
            )
            score = np.zeros(starts.size, dtype=np.float64)
            for symbol, tone in SYNC_TAPS:
                block_starts = starts + symbol * self.nsps
                blocks = iq[block_starts[:, None] + sample_offsets[None, :]]
                score += np.abs(blocks @ oscillators[tone])
            plane[freq_index, :] = score
        return plane

    @staticmethod
    def _peak_ratio(plane: np.ndarray, peak_f: int, peak_t: int,
                    time_radius: int) -> float:
        peak = float(plane[peak_f, peak_t])
        masked = plane.copy()
        f0, f1 = max(0, peak_f - 2), min(plane.shape[0], peak_f + 3)
        t0 = max(0, peak_t - time_radius)
        t1 = min(plane.shape[1], peak_t + time_radius + 1)
        masked[f0:f1, t0:t1] = -np.inf
        second = float(masked.max()) if masked.size and np.isfinite(masked).any() else 0.0
        if peak <= 0.0:
            return 0.0
        return peak / max(second, peak * 1e-12)

    @staticmethod
    def _costas_contrast(energies: np.ndarray) -> float:
        rows = np.asarray([symbol for symbol, _ in SYNC_TAPS], dtype=np.int64)
        tones = np.asarray([tone for _, tone in SYNC_TAPS], dtype=np.int64)
        sync = energies[rows]
        expected = sync[np.arange(len(SYNC_TAPS)), tones]
        other_sum = float(sync.sum() - expected.sum())
        if other_sum <= np.finfo(np.float64).tiny:
            return float("inf") if float(expected.sum()) > 0.0 else 0.0
        return 3.0 * float(expected.sum()) / other_sum

    def sync(self, samples) -> Ft4SyncResult:
        """在整段捕获内盲搜帧起点与 tone-0 基频，并提取 105×4 软能量。"""
        iq = _samples_iq(samples)
        max_start = int(iq.size - self.frame_samples)
        if max_start < 0:
            return Ft4SyncResult(reason="short_capture")
        if not bool((np.abs(iq) > 0.0).any()):
            return Ft4SyncResult(reason="no_signal")

        starts = np.arange(0, max_start + 1, self.coarse_time_step, dtype=np.int64)
        if starts[-1] != max_start:
            starts = np.append(starts, max_start)
        frequencies = np.arange(
            self.freq_lo, self.freq_hi + self.coarse_freq_step * 0.5,
            self.coarse_freq_step, dtype=np.float64,
        )
        frequencies = frequencies[frequencies <= self.freq_hi]
        if frequencies[-1] < self.freq_hi:
            frequencies = np.append(frequencies, self.freq_hi)

        plane = self._score_plane(iq, frequencies, starts)
        peak_f, peak_t = np.unravel_index(int(np.argmax(plane)), plane.shape)
        best_freq = float(frequencies[peak_f])
        best_start = int(starts[peak_t])
        ratio = self._peak_ratio(
            plane, peak_f, peak_t,
            max(1, int(math.ceil(2 * self.nsps / self.coarse_time_step))),
        )
        diagnostics = {
            "sync_ratio": ratio,
            "n_time_bins": int(starts.size),
            "n_freq_bins": int(frequencies.size),
        }
        if float(plane[peak_f, peak_t]) <= 0.0:
            return Ft4SyncResult(reason="no_signal", **diagnostics)
        if ratio < self.ratio_threshold:
            return Ft4SyncResult(reason="low_sync_ratio", **diagnostics)

        # 两级局部细化：先 0.5 Hz/8 样本，再 0.1 Hz/1 样本。
        freq_radius = self.coarse_freq_step
        time_radius = self.coarse_time_step
        for freq_step, time_step in ((0.5, 8), (0.1, 1)):
            local_freqs = np.arange(
                best_freq - freq_radius,
                best_freq + freq_radius + freq_step * 0.5,
                freq_step,
            )
            local_freqs = local_freqs[
                (local_freqs >= self.freq_lo) & (local_freqs <= self.freq_hi)
            ]
            local_starts = np.arange(
                max(0, best_start - time_radius),
                min(max_start, best_start + time_radius) + 1,
                time_step,
                dtype=np.int64,
            )
            local_plane = self._score_plane(iq, local_freqs, local_starts)
            local_f, local_t = np.unravel_index(
                int(np.argmax(local_plane)), local_plane.shape
            )
            best_freq = float(local_freqs[local_f])
            best_start = int(local_starts[local_t])
            best_score = float(local_plane[local_f, local_t])
            freq_radius = freq_step
            time_radius = time_step

        energies = aligned_tone_energies(
            iq, best_start, best_freq, fs=self.fs, nsps=self.nsps
        )
        contrast = self._costas_contrast(energies)
        if contrast < self.contrast_threshold:
            return Ft4SyncResult(
                base_freq_hz=best_freq,
                time_offset_samples=best_start,
                sync_score=best_score / (len(SYNC_TAPS) * self.nsps),
                sync_contrast=contrast,
                reason="low_sync_contrast",
                **diagnostics,
            )
        return Ft4SyncResult(
            synced=True,
            base_freq_hz=best_freq,
            time_offset_samples=best_start,
            sync_score=best_score / (len(SYNC_TAPS) * self.nsps),
            sync_contrast=contrast,
            symbols=np.argmax(energies, axis=1).astype(np.int8),
            energies=energies,
            **diagnostics,
        )

    def decode(self, samples, max_iter: int = 30) -> dict:
        """盲同步后将 87×4 数据软能量交给既有 FT4 CRC/LDPC 解码链。"""
        if isinstance(max_iter, bool) or not isinstance(
            max_iter, (int, np.integer)
        ) or max_iter < 1:
            raise ValueError("max_iter 必须是正整数")
        sync = self.sync(samples)
        if not sync.synced:
            return {
                "ok": False,
                "rejected": True,
                "reject_reason": sync.reason,
                "data_bits": None,
                "unpacked": None,
                "text": None,
                "parity_ok": False,
                "crc_ok": False,
                "sync": sync,
            }
        result = decode_ft4_payload(
            sync.energies[np.asarray(DATA_POSITIONS, dtype=np.int64)],
            max_iter=max_iter,
        )
        result["sync"] = sync
        return result

    def modulate_message(self, message77: Sequence[int], **modulate_kwargs) -> np.ndarray:
        """便捷闭环入口：既有 FT4 位编码 → 帧 → 本模块 GFSK 合成。"""
        modulator = Ft4Modulator(fs=self.fs, nsps=self.nsps)
        return modulator.modulate(
            build_frame(encode_ft4_tones(message77)), **modulate_kwargs
        )
