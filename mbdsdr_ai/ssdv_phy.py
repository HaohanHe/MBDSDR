# SPDX-License-Identifier: MIT
"""SSDV 物理层最小真实解调前端：复 IQ → 带内下变频 → BPSK 解调 → 硬比特/字节。

用途
----
把 ``onboard.py --mode ssdv`` 从"只吃解调后字节流"前伸到"吃复 IQ"：
云内用合成 IQ 跑通 录 IQ → 带内下变频 → BPSK 解调 → 字节流 → ``SsdvDecoder`` → JPEG
的一条命令闭环；真机拿到官方链路参数后只需调符号率/中频偏移，不改本层与 SSDV 核心。

边界（红线，勿越界）
------------------
- **不硬编码任何活动频率/符号率**：中频偏移、符号率一律由参数传入；默认值仅为通用演示，
  不对应任何具体卫星（真机参数见 docs/learn/phase14/P3-event-params.md）。
- 本层只到"硬字节"：BPSK 载波相位/精定时在合成信号里由收发两端对齐而确定；
  真机的载波恢复、Gardner/M&M 精定时、卷积/Viterbi/CCSDS 解扰/信道 RS 不在本层
  （分别属射频端与 ``ccsds_rx``），本层对纯噪声/无信号诚实返回空，绝不伪造字节。
- BPSK 调制极性固定：bit=1 → +1∠0，bit=0 → -1∠π；解调按实部正负判决。
  真实链路若反相，由上游差分编码/ASync 自同步兜底（fsphil 0x55 自同步对固定反相不免疫，
  故真机需配套差分译码或 ASM）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

# 通用演示默认符号率（**非活动参数**）：常见业余卫星低速 BPSK 量级。
# 真机链路符号率以 docs 活动参数为准，由 CLI/参数显式传入。
DEFAULT_SYMRATE_HZ: float = 4800.0


def bpsk_modulate_bits(bits, fs: float, symrate: float,
                       f_if: float = 0.0, amp: float = 1.0) -> np.ndarray:
    """比特序列 → BPSK 复 IQ（矩形脉冲，最近邻上采样；f_if 为带内中频偏移 Hz）。

    仅作云内合成/测试用：收发两端对齐同一 fs/symrate/f_if 即可确定往返。
    """
    x = np.asarray(bits)
    if x.size == 0:
        return np.zeros(0, dtype=np.complex64)
    sps = fs / symrate
    reps = max(1, int(round(sps)))
    symbols = np.where(x > 0.5, 1.0, -1.0).astype(np.float64)
    up = np.repeat(symbols, reps)
    t = np.arange(up.size) / fs
    iq = amp * up * np.exp(1j * 2.0 * np.pi * f_if * t)
    return iq.astype(np.complex64)


def demod_bpsk(iq: np.ndarray, fs: float, symrate: float,
               f_offset: float = 0.0) -> np.ndarray:
    """复 IQ → BPSK 硬比特 (int8 0/1)。

    步骤：NCO 带内下变频 → 矩形匹配低通 → 盲符号定时（眼图张开度最大相位扫描）
    → 符号中心按实部正负判决。无足够符号/纯噪声时返回空数组（诚实空态）。
    """
    z = np.asarray(iq, dtype=np.complex64)
    if z.size == 0 or symrate <= 0:
        return np.zeros(0, dtype=np.int8)

    t = np.arange(z.size) / fs
    # 1) 带内下变频：把落在 f_offset 处的信号搬到零中频
    base = z * np.exp(-1j * 2.0 * np.pi * f_offset * t)

    sps = fs / symrate
    k = max(1, int(round(sps)))
    # 2) 矩形匹配低通（一个符号周期的滑动平均），抑制带外/噪声
    kernel = np.ones(k, dtype=np.float64) / k
    filt = np.convolve(base.real, kernel, mode="same")

    n_sym = int(filt.size / sps)
    if n_sym < 8:
        return np.zeros(0, dtype=np.int8)

    # 3) 盲符号定时：在 [0, sps) 扫描采样相位，选 |mean(采样点实部)| 最大者
    #    （符号中心离过零点最远、眼图张开最开）。
    n_ph = max(8, int(sps))
    best_off, best_score = 0.0, -np.inf
    for off in np.linspace(0.0, sps, n_ph, endpoint=False):
        idx = np.round(off + np.arange(n_sym) * sps).astype(int)
        idx = idx[idx < filt.size]
        if idx.size < 8:
            continue
        score = float(np.abs(np.mean(filt[idx])))
        if score > best_score:
            best_score, best_off = score, off

    # 4) 符号中心采样 → 实部正负判决
    idx = np.round(best_off + np.arange(n_sym) * sps).astype(int)
    idx = idx[idx < filt.size]
    bits = (filt[idx] > 0.0).astype(np.int8)
    return bits


def iq_to_ssdv_bytes(iq: np.ndarray, fs: float, symrate: float,
                     f_offset: float = 0.0) -> bytes:
    """复 IQ → BPSK 硬比特 → MSB-first 打包字节（交给 SsdvDecoder 自同步）。"""
    from .ccsds_rx import bits_to_bytes_msb
    bits = demod_bpsk(iq, fs, symrate, f_offset)
    if bits.size == 0:
        return b""
    return bits_to_bytes_msb(bits.tolist())


__all__ = [
    "DEFAULT_SYMRATE_HZ",
    "bpsk_modulate_bits",
    "demod_bpsk",
    "iq_to_ssdv_bytes",
]
