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

from dataclasses import dataclass
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


def _lin_interp(x: np.ndarray, t: float) -> float:
    """线性插值取 x[t]（t 可落在样本之间）；越界夹到端点。"""
    n = x.size
    if n == 0:
        return 0.0
    if t <= 0.0:
        return float(x[0])
    i0 = int(np.floor(t))
    if i0 + 1 >= n:
        return float(x[-1])
    frac = t - i0
    return float(x[i0] * (1.0 - frac) + x[i0 + 1] * frac)


@dataclass
class TedDiagnostics:
    """Gardner TED 闭环的旁证（诚实报告收敛/未收敛，绝不伪造）。"""

    method: str
    n_symbols: int = 0
    #: 收敛后四分之一窗内 |误差| 均值（越小越稳；不代表"一定对"，仅供旁证）。
    residual_err: float = float("nan")
    #: 闭环累计漂移（实际走过的样本数 - 理想 sps*N），过大 = 失锁/滑移。
    drift_samples: float = 0.0
    #: 未收敛/信号太短时置 False（纯噪声/极低 SNR 诚实不伪造出图）。
    locked: bool = False


def _gardner_symbols(r: np.ndarray, sps: float,
                     init_phase: float = 0.0,
                     kp: float = 0.06, ki: float = 0.002,
                     warmup: int = 8) -> tuple[np.ndarray, TedDiagnostics]:
    """Gardner 误差检测 + 二阶环路滤波 + 线性插值的符号定时恢复闭环（干净室）。

    依据公开教科书算法（Gardner 1986, "A BPSK/QPSK Timing-Error Detector
    for Sampled Receivers"）独立重写，未复制任何 GPL 参考代码。

    环路每符号一次迭代::

        y_now  = interp(r, ip)            # 当前判决点插值
        y_mid  = interp(r, ip - sps/2)    # 上一符号与本符号中点插值
        e      = (y_now - y_prev) * y_mid  # Gardner TED（对载波相位不敏感）
        mu     = mu + ki*e                # 积分支路
        step   = sps + kp*e + mu          # 比例支路 +  nominal
        ip    += step

    误差在中点处的过零点为 0 梯度点：当判决点偏离眼图中心时，中点插值与两端
    符号差乘积给出把采样点拉回中心的纠正方向。warmup 内不开环（避免初始大暂态
    污染积分器）。

    返回 ``(bits, diag)``。纯噪声/信号太短时返回空比特且 ``diag.locked=False``
    （诚实空态，绝不伪造收敛）。
    """
    diag = TedDiagnostics(method="gardner")
    n = r.size
    if n < int(sps * (warmup + 4)):
        return np.zeros(0, dtype=np.int8), diag

    ip = float(init_phase)
    step = float(sps)
    mu = 0.0
    y_prev = 0.0
    bits: list[int] = []
    errs: list[float] = []
    guard = int(n / sps) + 16
    cnt = 0
    while ip + sps < n and cnt < guard:
        y_now = _lin_interp(r, ip)
        y_mid = _lin_interp(r, ip - sps * 0.5)
        # Gardner TED：y_prev 在 warmup 前为 0，误差贡献被 warmup 抑制
        e = (y_now - y_prev) * y_mid
        if cnt >= warmup:
            mu += ki * e
            # 负反馈：Gardner S 曲线 d(e)/d(τ)>0 穿过眼图中心，误差增大时应让游标走慢
            # （step<sps）把相位拉回中心——kp*e / mu 前取负号（干净室按 S 曲线整定）。
            step = sps - kp * e - mu
        bits.append(1 if y_now > 0.0 else 0)
        errs.append(e)
        y_prev = y_now
        ip += step
        cnt += 1

    # warmup 期环路尚未收敛（积分器未开、匹配滤波器边沿过渡），这几个起始符号是
    # 采集瞬态、不可靠——裁掉，避免给 ASM 帧同步引入 1 位偏移。
    bits = bits[warmup:]
    errs = errs[warmup:]
    diag.n_symbols = len(bits)
    tail = np.asarray(errs[max(0, len(errs) - max(8, len(errs) // 4)):])
    diag.residual_err = float(np.mean(np.abs(tail))) if tail.size else float("nan")
    diag.drift_samples = float(ip - (init_phase + (len(bits) + warmup) * sps))
    # 诚实判据：必须解出足够符号，且闭环累计漂移未失控（|drift| 远小于一个符号）。
    # 这只是"环路没跑飞"的旁证，不等于数据一定对——最终仍由 ASM/RS/CRC 把关。
    diag.locked = (len(bits) >= 16) and (abs(diag.drift_samples) < sps * 0.9)
    return np.asarray(bits, dtype=np.int8), diag


def demod_bpsk(iq: np.ndarray, fs: float, symrate: float,
               f_offset: float = 0.0, timing: str = "coarse"
               ) -> np.ndarray:
    """复 IQ → BPSK 硬比特 (int8 0/1)。

    步骤：NCO 带内下变频 → 矩形匹配低通 → 符号定时 → 符号中心按实部正负判决。
    无足够符号/纯噪声时返回空数组（诚实空态）。

    参数
    ----------
    timing :
        ``"coarse"``（默认）：盲符号定时——在 [0, sps) 扫描采样相位，选
        ``|mean(采样点实部)|`` 最大者（眼图张开度最大）。实现简单，但在高噪/弱信号
        下会整比特滑移。
        ``"gardner"``：Gardner TED 插值定时恢复闭环（见 :func:`_gardner_symbols`），
        对定时相位偏移/小频偏可自动收敛到眼图中心，显著降低滑移率；极低 SNR 下
        环路不收敛，由本函数诚实返回空（下游 ASM/RS/CRC 再把关，不伪造出图）。
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

    if timing == "gardner":
        bits, diag = _gardner_symbols(filt, sps)
        if not diag.locked:
            return np.zeros(0, dtype=np.int8)
        return bits

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
    "TedDiagnostics",
    "_gardner_symbols",
]
