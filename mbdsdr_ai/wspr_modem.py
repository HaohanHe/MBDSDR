# SPDX-License-Identifier: MIT
"""经典 WSPR Type 1 的受限音频前端与位级链接。

协议事实来自已归档材料，而非记忆：
- ``wspr_upstream_wsprsim.c`` 31-49 行：375 Hz 采样、256 采样/符号、
  ``df=375/256``，四音为 ``center+(symbol-1.5)*df``，相位跨符号连续累加；
- ``wspr_upstream_wsprd.c`` 208-310 行：四个非相干音相关幅度、以已知 sync
  LSB 加权的归一化同步度量；
- ``wspr_coding_process_g4jnt.txt`` 213-217、233-235 行：12000/8192 Hz
  音距/符号率及约 110.6 s 的 162 符号传输。

本模块独立实现连续相位复基带/实音频合成、整符号四音非相干能量、sync-aware
LLR，以及调用者指定范围内的时频网格搜索。搜索步长、候选数量、同步门限和
译码预算是**实现参数，不是 WSPR 协议常量**。仅处理缓冲区内完整帧，不含
频率漂移、部分帧、宽带盲捕获、多信号相消或真实 OTA/弱信号性能声明。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
import operator
from typing import List, Optional, Sequence, Tuple

import numpy as np

from mbdsdr_ai.wspr_codec import (
    CODE_BITS,
    SYNC_VECTOR,
    WsprMessage,
    decode_convolutional,
    encode_message,
    unpack_message,
)

WSPR_SYMBOL_COUNT = CODE_BITS
WSPR_BAUD = 12000.0 / 8192.0
WSPR_TONE_SPACING_HZ = WSPR_BAUD
WSPR_TRANSMISSION_SECONDS = WSPR_SYMBOL_COUNT / WSPR_BAUD

# 官方参考解调/仿真采样策略；不是空中接口强制采样率。
REFERENCE_SAMPLE_RATE_HZ = 375.0
REFERENCE_SAMPLES_PER_SYMBOL = 256

# 以下默认值均为本实现的工作点，不是协议门限。
DEFAULT_MIN_SYNC_METRIC = 0.35
DEFAULT_MAX_DECODE_CANDIDATES = 3
DEFAULT_MAX_FEC_EXPANSIONS = 100_000


@dataclass(frozen=True)
class WsprDecodeResult:
    """一次完整帧译码及其同步估计。"""

    message: WsprMessage
    bits: Tuple[int, ...]
    frequency_hz: float
    start_sample: int
    time_offset_seconds: float
    sync_metric: float


def _real(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} 必须是有限实数")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限实数")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    try:
        return operator.index(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _samples_per_symbol(sample_rate_hz: object) -> Tuple[float, int]:
    rate = _real(sample_rate_hz, "采样率")
    if rate <= 0:
        raise ValueError("采样率必须为正数")
    exact = rate / WSPR_BAUD
    count = int(round(exact))
    # 整符号相关要求每符号固定整数采样点；375 与 12000 Hz 都满足。
    if count < 4 or not math.isclose(exact, count, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("当前前端要求采样率/ WSPR 波特率为整数，推荐 375 或 12000 Hz")
    return rate, count


def _audio(samples: object) -> np.ndarray:
    array = np.asarray(samples)
    if array.ndim != 1 or array.size == 0:
        raise ValueError("音频必须是一维非空实数或复数序列")
    if not (np.issubdtype(array.dtype, np.number)
            or np.issubdtype(array.dtype, np.complexfloating)):
        raise ValueError("音频必须是数值类型")
    try:
        values = array.astype(np.complex128, copy=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("音频必须是一维数值序列") from exc
    if not bool(np.isfinite(values).all()):
        raise ValueError("音频不能含 NaN 或无穷大")
    return values


def _is_complex_input(samples: object) -> bool:
    return np.iscomplexobj(np.asarray(samples))


def _validate_center(center_frequency_hz: object, rate: float,
                     complex_baseband: bool) -> float:
    center = _real(center_frequency_hz, "中心频率")
    lowest = center - 1.5 * WSPR_TONE_SPACING_HZ
    highest = center + 1.5 * WSPR_TONE_SPACING_HZ
    if complex_baseband:
        if lowest <= -rate / 2 or highest >= rate / 2:
            raise ValueError("四个复基带音必须位于 Nyquist 区间内")
    elif lowest < 0 or highest >= rate / 2:
        raise ValueError("实音频四音必须位于 0 与 Nyquist 频率之间")
    return center


def _silence_samples(seconds: object, rate: float, name: str) -> int:
    value = _real(seconds, name)
    if value < 0:
        raise ValueError(f"{name} 不能为负数")
    return int(round(value * rate))


def synthesize_symbols(symbols: Sequence[int],
                       sample_rate_hz: float = REFERENCE_SAMPLE_RATE_HZ,
                       center_frequency_hz: float = 0.0,
                       complex_baseband: bool = True,
                       amplitude: float = 1.0,
                       leading_silence_seconds: float = 0.0,
                       trailing_silence_seconds: float = 0.0) -> np.ndarray:
    """合成 162 符号连续相位 4FSK；复数输出为 exp(jφ)，实输出为 cos(φ)。

    无填充时，375 Hz 下输出 41472 点，即 110.592 s。首尾静音仅用于构造
    测试/缓冲波形，不改变符号相位；符号起点从首段静音之后开始，初相为零。
    """
    if isinstance(symbols, (str, bytes, bytearray)):
        raise ValueError("信道符号必须是 162 项整数序列")
    values = np.asarray(symbols)
    if values.ndim != 1 or values.size != WSPR_SYMBOL_COUNT:
        raise ValueError("信道符号必须是一维 162 项序列")
    if not np.issubdtype(values.dtype, np.integer):
        raise ValueError("信道符号必须为整数")
    tones = values.astype(np.int64, copy=False)
    if bool(((tones < 0) | (tones > 3)).any()):
        raise ValueError("信道符号必须位于 0..3")
    rate, count = _samples_per_symbol(sample_rate_hz)
    center = _validate_center(center_frequency_hz, rate, bool(complex_baseband))
    level = _real(amplitude, "幅度")
    if level <= 0:
        raise ValueError("幅度必须为正数")
    leading = _silence_samples(leading_silence_seconds, rate, "首段静音")
    trailing = _silence_samples(trailing_silence_seconds, rate, "尾段静音")

    output = np.zeros(leading + WSPR_SYMBOL_COUNT * count + trailing,
                      dtype=np.complex128 if complex_baseband else np.float64)
    phase = 0.0
    cursor = leading
    sample_index = np.arange(count, dtype=np.float64)
    for symbol in tones:
        frequency = center + (float(symbol) - 1.5) * WSPR_TONE_SPACING_HZ
        increment = 2.0 * np.pi * frequency / rate
        phase_samples = phase + increment * sample_index
        phasor = np.exp(1j * phase_samples)
        segment = level * (phasor if complex_baseband else np.cos(phase_samples))
        output[cursor:cursor + count] = segment
        cursor += count
        # 与官方参考一致：最后一个采样输出后仍累加一次增量，供下一符号使用。
        phase = (phase + increment * count) % (2.0 * np.pi)
    return output


def encode_audio(callsign: str, grid4: str, power_dbm: int,
                 **kwargs: object) -> np.ndarray:
    """Type 1 字段到完整 162 符号音频；参数同 :func:`synthesize_symbols`。"""
    return synthesize_symbols(encode_message(callsign, grid4, power_dbm), **kwargs)


def _frame(samples: object, sample_rate_hz: object, start_sample: object
           ) -> Tuple[np.ndarray, float, int, int]:
    audio = _audio(samples)
    rate, count = _samples_per_symbol(sample_rate_hz)
    start = _integer(start_sample, "起始采样点")
    if start < 0:
        raise ValueError("起始采样点不能为负数")
    size = WSPR_SYMBOL_COUNT * count
    if start + size > audio.size:
        raise ValueError("缓冲区不含从指定起点开始的完整 162 符号帧")
    return audio[start:start + size].reshape(WSPR_SYMBOL_COUNT, count), rate, count, start


def symbol_energies(samples: object, sample_rate_hz: float,
                    center_frequency_hz: float,
                    start_sample: int = 0) -> np.ndarray:
    """对完整帧执行四音非相干相关，返回形状 ``(162,4)`` 的能量。

    每行依次为符号值 0、1、2、3 的能量；相位不参与判决，因此适用于 CPFSK。
    实音频先转为解析信号，避免负频率镜像泄漏污染正频率四音相关。
    """
    frame, rate, count, _ = _frame(samples, sample_rate_hz, start_sample)
    complex_input = _is_complex_input(samples)
    center = _validate_center(center_frequency_hz, rate, complex_input)
    if not complex_input:
        # NumPy 实现的 Hilbert 解析信号：保留 DC/Nyquist，正频率加倍，负频率置零。
        real_frame = frame.reshape(-1).real
        multiplier = np.zeros(real_frame.size, dtype=np.float64)
        multiplier[0] = 1.0
        if real_frame.size % 2 == 0:
            multiplier[real_frame.size // 2] = 1.0
            multiplier[1:real_frame.size // 2] = 2.0
        else:
            multiplier[1:(real_frame.size + 1) // 2] = 2.0
        analytic = np.fft.ifft(np.fft.fft(real_frame) * multiplier)
        frame = analytic.reshape(WSPR_SYMBOL_COUNT, count)
    frequencies = center + (np.arange(4, dtype=np.float64) - 1.5) \
        * WSPR_TONE_SPACING_HZ
    sample_index = np.arange(count, dtype=np.float64)
    reference = np.exp(-2j * np.pi
                       * (frequencies[:, None] / rate) * sample_index[None, :])
    correlation = reference @ frame.T
    return (np.abs(correlation) ** 2 / float(count * count)).T


def _energies(values: object) -> np.ndarray:
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("能量必须是实数数组") from exc
    if array.shape != (WSPR_SYMBOL_COUNT, 4):
        raise ValueError("能量数组形状必须为 (162,4)")
    if not bool(np.isfinite(array).all()) or bool((array < 0).any()):
        raise ValueError("能量必须为有限非负数")
    return array


def sync_metric(energies: object) -> float:
    """由 sync LSB 得到归一化相关度量；全零能量返回 0。

    计算方式采用四音幅度（能量的平方根），已知 sync=1 时倾向音 1/3，
    sync=0 时倾向音 0/2。它只评价候选对齐程度，不是消息 CRC。
    """
    power = _energies(energies)
    amplitude = np.sqrt(power)
    total = float(amplitude.sum())
    if total <= 0.0:
        return 0.0
    difference = amplitude[:, 1] + amplitude[:, 3] \
        - amplitude[:, 0] - amplitude[:, 2]
    signs = np.asarray(SYNC_VECTOR, dtype=np.float64) * 2.0 - 1.0
    return float(np.dot(signs, difference) / total)


def llrs_from_energies(energies: object) -> np.ndarray:
    """用已知 sync LSB 选择对应音对，返回 162 个交织域数据 LLR。

    sync=0 比较音 0（数据 0）与音 2（数据 1）；sync=1 比较音 1 与音 3。
    正 LLR 倾向数据 0，负 LLR 倾向数据 1。极小正数仅用于防止 log(0)。
    """
    power = _energies(energies)
    sync = np.asarray(SYNC_VECTOR, dtype=np.int64)
    data_zero = np.where(sync == 0, power[:, 0], power[:, 1])
    data_one = np.where(sync == 0, power[:, 2], power[:, 3])
    floor = np.finfo(np.float64).tiny
    return np.log(data_zero + floor) - np.log(data_one + floor)


def _decode_candidate(samples: object, sample_rate_hz: float,
                      center_frequency_hz: float, start_sample: int,
                      sync: float, energies: np.ndarray,
                      min_sync_metric: float,
                      max_fec_expansions: int) -> Optional[WsprDecodeResult]:
    if sync < min_sync_metric:
        return None
    llr = llrs_from_energies(energies)
    bits = decode_convolutional(llr, max_expansions=max_fec_expansions)
    if bits is None:
        return None
    try:
        message = unpack_message(bits)
    except ValueError:
        return None
    rate, _ = _samples_per_symbol(sample_rate_hz)
    return WsprDecodeResult(
        message=message,
        bits=tuple(bits),
        frequency_hz=float(center_frequency_hz),
        start_sample=int(start_sample),
        time_offset_seconds=float(start_sample / rate),
        sync_metric=float(sync),
    )


def decode_aligned(samples: object, sample_rate_hz: float,
                   center_frequency_hz: float, start_sample: int = 0,
                   min_sync_metric: float = DEFAULT_MIN_SYNC_METRIC,
                   max_fec_expansions: int = DEFAULT_MAX_FEC_EXPANSIONS
                   ) -> Optional[WsprDecodeResult]:
    """已知中心频率与帧起点的对齐译码；sync 或字段校验失败返回 ``None``。"""
    threshold = _real(min_sync_metric, "同步门限")
    if not -1.0 <= threshold <= 1.0:
        raise ValueError("同步门限必须位于 -1..1")
    budget = _integer(max_fec_expansions, "FEC 搜索预算")
    if budget <= 0:
        raise ValueError("FEC 搜索预算必须为正整数")
    energies = symbol_energies(samples, sample_rate_hz, center_frequency_hz, start_sample)
    sync = sync_metric(energies)
    return _decode_candidate(samples, sample_rate_hz, center_frequency_hz,
                             start_sample, sync, energies, threshold, budget)


def _offset_values(values: object, name: str) -> List[float]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{name} 必须是非空实数序列")
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是非空实数序列") from exc
    if array.ndim != 1 or array.size == 0 or not bool(np.isfinite(array).all()):
        raise ValueError(f"{name} 必须是一维非空有限实数序列")
    return sorted(set(float(value) for value in array))


def search_and_decode(samples: object, sample_rate_hz: float,
                      nominal_center_frequency_hz: float = 0.0,
                      frequency_offsets_hz: Sequence[float] = (0.0,),
                      time_offsets_seconds: Sequence[float] = (0.0,),
                      min_sync_metric: float = DEFAULT_MIN_SYNC_METRIC,
                      max_decode_candidates: int = DEFAULT_MAX_DECODE_CANDIDATES,
                      max_fec_expansions: int = DEFAULT_MAX_FEC_EXPANSIONS
                      ) -> Optional[WsprDecodeResult]:
    """在给定时频网格内独立搜索并译码，不向同步器传入真实偏移。

    仅评估缓冲区能容纳的完整帧；按 sync 度量从高到低最多尝试
    ``max_decode_candidates`` 个候选。无过门限候选或 FEC/字段失败返回 ``None``。
    这是一项受限网格同步，不代表任意频偏、任意时延或宽带全盲捕获能力。
    """
    audio = _audio(samples)
    rate, count = _samples_per_symbol(sample_rate_hz)
    complex_baseband = _is_complex_input(samples)
    nominal = _validate_center(nominal_center_frequency_hz, rate, complex_baseband)
    threshold = _real(min_sync_metric, "同步门限")
    if not -1.0 <= threshold <= 1.0:
        raise ValueError("同步门限必须位于 -1..1")
    candidate_limit = _integer(max_decode_candidates, "候选译码数量")
    if candidate_limit <= 0:
        raise ValueError("候选译码数量必须为正整数")
    budget = _integer(max_fec_expansions, "FEC 搜索预算")
    if budget <= 0:
        raise ValueError("FEC 搜索预算必须为正整数")
    frequency_offsets = _offset_values(frequency_offsets_hz, "频偏搜索网格")
    time_offsets = _offset_values(time_offsets_seconds, "时偏搜索网格")
    if any(value < 0 for value in time_offsets):
        raise ValueError("当前完整帧前端仅接受非负时偏搜索点")

    starts = sorted({int(round(value * rate)) for value in time_offsets})
    candidates: List[Tuple[float, float, int, np.ndarray]] = []
    frame_size = WSPR_SYMBOL_COUNT * count
    for frequency_offset in frequency_offsets:
        center = nominal + frequency_offset
        _validate_center(center, rate, complex_baseband)
        for start in starts:
            if start < 0 or start + frame_size > audio.size:
                continue
            energies = symbol_energies(audio, rate, center, start)
            score = sync_metric(energies)
            candidates.append((score, center, start, energies))
    candidates.sort(key=lambda item: item[0], reverse=True)
    attempted = 0
    for score, center, start, energies in candidates:
        if score < threshold:
            break
        attempted += 1
        result = _decode_candidate(audio, rate, center, start, score, energies,
                                   threshold, budget)
        if result is not None:
            return result
        if attempted >= candidate_limit:
            break
    return None


__all__ = [
    "DEFAULT_MAX_DECODE_CANDIDATES", "DEFAULT_MAX_FEC_EXPANSIONS",
    "DEFAULT_MIN_SYNC_METRIC", "REFERENCE_SAMPLES_PER_SYMBOL",
    "REFERENCE_SAMPLE_RATE_HZ", "WSPR_BAUD", "WSPR_SYMBOL_COUNT",
    "WSPR_TONE_SPACING_HZ", "WSPR_TRANSMISSION_SECONDS", "WsprDecodeResult",
    "decode_aligned", "encode_audio", "llrs_from_energies", "search_and_decode",
    "symbol_energies", "sync_metric", "synthesize_symbols",
]
