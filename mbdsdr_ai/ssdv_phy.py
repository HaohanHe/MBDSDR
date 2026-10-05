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


def estimate_cfo_bpsk(iq: np.ndarray, fs: float,
                      min_prominence: float = 8.0) -> tuple[float, float]:
    """BPSK 盲载波频偏估计（平方环，干净室教科书实现）。

    原理：复基带 BPSK ``z(t)=d(t)·exp(j2π f₀ t)`` 平方后 ``d²=1`` 数据被抹除，
    出现一根在 ``2·f₀`` 的谱线 → FFT 找峰 /2 即频偏估计。对抑制载波 BPSK 适用。

    返回 ``(cfo_hz, prominence)``。``prominence`` = 峰谱线幅度 / 谱中位数，是"信号
    是否真在"的旁证：纯噪声无相干谱线，prominence 很低（实测 ~4）；有信号时即便
    sd=3 仍 ~8+。``prominence < min_prominence`` 视为无可靠信号（诚实返回 0 频偏，
    由下游 ASM/RS/CRC 把关，不伪造估计）。
    """
    z = np.asarray(iq, dtype=np.complex128)
    n = z.size
    if n < 64:
        return 0.0, 0.0
    z2 = z * z
    z2 = z2 * np.hanning(n)
    Z = np.fft.fftshift(np.fft.fft(z2))
    fq = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    mag = np.abs(Z)
    pk = int(np.argmax(mag))
    med = float(np.median(mag)) + 1e-12
    prominence = float(mag[pk] / med)
    # 抛物线插值亚-bin 精峰位（降频率量化误差，供 AFC 小窗跟踪）
    if 1 <= pk <= n - 2:
        y0, y1, y2 = mag[pk - 1], mag[pk], mag[pk + 1]
        denom = (y0 - 2.0 * y1 + y2)
        if denom > 1e-12:
            delta = 0.5 * (y0 - y2) / denom          # [-0.5,0.5] bin
            df = fs / n
            cfo = float((fq[pk] + delta * df) / 2.0)
        else:
            cfo = float(fq[pk] / 2.0)
    else:
        cfo = float(fq[pk] / 2.0)
    if prominence < min_prominence:
        return 0.0, prominence
    return cfo, prominence


# AFC / notch 的具名门限常量（不硬编码活动参数；仅通用演示默认，真机可由参数覆盖）。
AFC_WIN_SAMPLES: int = 8192          # 每 AFC 窗样本数（频率分辨率 fs/win ≈ 5.9Hz）
AFC_MAX_STEP_HZ: float = 40.0        # 相邻窗频偏估计的限幅（Hz/窗），防野点跳变
AFC_MIN_PROMINENCE: float = 8.0      # 窗内信号 prominence 门限，低于则冻结前值
NOTCH_GATE: float = 6.0              # CW 峰 = 该 bin 幅度 / 局部均值 > 此门限
NOTCH_HALF_BINS: int = 1             # 陷波半宽（bin），±half 一并清零
NOTCH_LOCAL_BINS: int = 16           # 局部背景对比半径（bin），需大于 CW 主瓣泄漏宽


def afc_correct(iq: np.ndarray, fs: float, symrate: float,
                win: int = AFC_WIN_SAMPLES,
                max_step: float = AFC_MAX_STEP_HZ,
                min_prom: float = AFC_MIN_PROMINENCE
                ) -> tuple[np.ndarray, np.ndarray]:
    """分段自适应 AFC：滑窗平方环估计 → 限幅/冻结平滑 → 逐窗逆旋。

    对**慢扫频多普勒**（窗内频偏变化 << 窗分辨率量级）可跟踪；对快扫频（单窗内
    扫过量级分辨率）仍失效——这是 AFC 带宽与频率分辨率的固有矛盾，诚实返回
    未补偿信号并由下游 ASM/RS 把关。

    状态机语义（对齐 Phase49 DopplerStepLimiter，纯 Python）：
      - 窗内 prominence 达标 → 估本窗频偏，再与前值限幅（|Δf|<=max_step）；
      - 窗内 prominence 不足（信号弱/遮挡）→ **冻结**前一频偏（不跳 0）；
      - 全程无任何达标窗 → 频偏恒 0（诚实不补偿，下游判空）。

    返回 ``(de-rotated complex64 iq, 每窗中心瞬时频偏估计 Hz 数组)``。
    """
    z = np.asarray(iq, dtype=np.complex128)
    n = z.size
    if n == 0:
        return np.zeros(0, dtype=np.complex64), np.zeros(0)
    if win < 256 or n < win:
        # 样本不足以滑窗：退化为整段单估
        cfo, _ = estimate_cfo_bpsk(z, fs, min_prom)
        t = np.arange(n) / fs
        return (z * np.exp(-1j * 2 * np.pi * cfo * t)).astype(np.complex64), \
            np.array([cfo])

    n_win = int(np.ceil(n / win))
    centers = np.zeros(n_win)
    smooth = np.zeros(n_win)
    prev = 0.0
    have_lock = False
    for k in range(n_win):
        lo, hi = k * win, min((k + 1) * win, n)
        est, prom = estimate_cfo_bpsk(z[lo:hi], fs, min_prom)
        centers[k] = (lo + hi) / 2.0
        if prom < min_prom:
            # 冻结：无新观测时保持前值（不跳 0）
            smooth[k] = prev
            continue
        if not have_lock:
            prev = est
            have_lock = True
        else:
            # 限幅：单次步进不超过 max_step（防野点/谱线二义跳变）
            d = est - prev
            d = max(-max_step, min(max_step, d))
            prev = prev + d
        smooth[k] = prev

    # 由窗中心平滑频偏 → 逐样本瞬时频偏 → 积分成相位 → 逆旋
    f_inst = np.interp(np.arange(n), centers, smooth)
    phase = 2 * np.pi * np.cumsum(f_inst) / fs
    derot = z * np.exp(-1j * phase)
    return derot.astype(np.complex64), smooth


def notch_cw(iq: np.ndarray, fs: float, symrate: float,
             gate: float = NOTCH_GATE, local_bins: int = NOTCH_LOCAL_BINS
             ) -> tuple[np.ndarray, list]:
    """自适应 CW 对消：FFT 检测窄带强单音 → 估计复幅度 → 时域精确相减。

    频域切除会留 sinc 旁瓣（频率未对齐 bin 时尤其严重）；这里改成**参数对消**：
    检测到 CW 峰频 ``f_cw`` 后，用 ``mean(z·exp(-j2π f_cw t))`` 估计复幅度，再从
    时域 ``z(t)`` 减去 ``a·exp(+j2π f_cw t)``——主瓣与旁瓣一并消除。

    判据：某 bin 幅度 > ``gate × 局部（±local_bins 均值）`` 视为窄带 CW 峰。信号主瓣是
    **宽带**的，局部均值同样高，故不会被当 CW 误陷；真正的窄单音才命中。

    返回 ``(对消后 complex64 iq, 检测到的 CW 频偏 Hz 列表)``。
    """
    z = np.asarray(iq, dtype=np.complex128)
    n = z.size
    if n < 64:
        return np.asarray(iq, dtype=np.complex64), []
    Z = np.fft.fft(z)
    mag = np.abs(Z)
    fq = np.fft.fftfreq(n, 1.0 / fs)
    t = np.arange(n) / fs

    # 找 CW 峰：按幅度扫描，命中后把该峰及其邻近 bin 标记为已处理（不重复报）
    order = np.argsort(mag)[::-1]
    removed: set = set()
    cw_freqs: list = []
    df = fs / n
    for pk in order:
        if pk in removed:
            continue
        lo = max(0, pk - local_bins)
        hi = min(n, pk + local_bins + 1)
        local = np.concatenate([mag[lo:pk], mag[pk + 1:hi]])
        loc_mean = float(np.mean(local)) + 1e-12
        if mag[pk] <= gate * loc_mean:
            break  # 已按幅度降序，后面都更低，不必再查
        # 抛物线亚-bin 精峰频（频率误差 over 长窗会让时域对消失配）
        f_cw = float(fq[pk])
        if 1 <= pk <= n - 2:
            y0, y1, y2 = mag[pk - 1], mag[pk], mag[pk + 1]
            den = (y0 - 2.0 * y1 + y2)
            if den > 1e-12:
                f_cw += (0.5 * (y0 - y2) / den) * df
        # 细网格精调：最大化 |mean(z·exp(-j2π f t))|，步长 0.05Hz（长窗下 0.3Hz 偏差
        # 即积累数 rad 相位差，令对消失败；亚-Hz 精度才能干净相消）
        best_f, best_abs = f_cw, -1.0
        for fg in np.arange(f_cw - df, f_cw + df, 0.05):
            aa = float(np.abs(np.mean(z * np.exp(-1j * 2 * np.pi * fg * t))))
            if aa > best_abs:
                best_abs, best_f = aa, float(fg)
        f_cw = best_f
        # 估计该 CW 的复幅度 → 时域相减
        a = np.mean(z * np.exp(-1j * 2 * np.pi * f_cw * t))
        z = z - a * np.exp(1j * 2 * np.pi * f_cw * t)
        cw_freqs.append(f_cw)
        for m in range(pk - 2, pk + 3):
            if 0 <= m < n:
                removed.add(m)

    return z.astype(np.complex64), cw_freqs


def _eye_center_phase(filt: np.ndarray, sps: float) -> float:
    """盲找眼图中心相位：在 [0,sps) 扫相位，选 mean(|采样点|) 最大者。

    与粗定时用 ``|mean|`` 不同——这里取平均**幅度**，眼心 |y|≈1、过渡点 |y|≈0，
    故峰值准确落在眼心，用作 Gardner/M&M 环路的初始相位 seed（避免从过渡点启动
    被不稳定平衡点困住）。
    """
    n_sym = int(filt.size / sps)
    best_off, best_score = 0.0, -1.0
    for off in np.linspace(0.0, sps, max(8, int(sps)), endpoint=False):
        idx = np.round(off + np.arange(n_sym) * sps).astype(int)
        idx = idx[idx < filt.size]
        if idx.size < 8:
            continue
        score = float(np.mean(np.abs(filt[idx])))
        if score > best_score:
            best_score, best_off = score, off
    return best_off


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
               f_offset: Optional[float] = 0.0, timing: str = "coarse"
               ) -> np.ndarray:
    """复 IQ → BPSK 硬比特 (int8 0/1)。

    步骤：盲/显式载波频偏校正 → 矩形匹配低通 → 符号定时 → 符号中心按实部正负判决。
    无足够符号/纯噪声时返回空数组（诚实空态）。

    参数
    ----------
    f_offset :
        显式带内中频偏移 Hz。传 ``None`` = **盲估计**（平方环 :func:`estimate_cfo_bpsk`
        自动估计频偏；prominence 不足时诚实按 0 处理，由下游 ASM/RS/CRC 把关）。
    timing :
        ``"coarse"``（默认）：盲符号定时——在 [0, sps) 扫描采样相位，选
        ``|mean(采样点实部)|`` 最大者（眼图张开度最大）。
        ``"gardner"``：Gardner TED 插值定时恢复闭环（见 :func:`_gardner_symbols`），
        先用平均幅度扫描盲找眼心作为初始相位 seed，再闭环精跟踪；极低 SNR 下
        环路不收敛，由本函数诚实返回空（下游 ASM/RS/CRC 再把关，不伪造出图）。
    """
    z = np.asarray(iq, dtype=np.complex64)
    if z.size == 0 or symrate <= 0:
        return np.zeros(0, dtype=np.int8)

    # 1) 载波频偏：None=盲估计；否则用显式 f_offset。
    if f_offset is None:
        f_offset, _prom = estimate_cfo_bpsk(z, fs)

    t = np.arange(z.size) / fs
    # 带内下变频：把落在 f_offset 处的信号搬到零中频
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
        eye = _eye_center_phase(filt, sps)
        bits, diag = _gardner_symbols(filt, sps, init_phase=eye)
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
    "estimate_cfo_bpsk",
    "_gardner_symbols",
]
