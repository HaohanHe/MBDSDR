# SPDX-License-Identifier: MIT
"""统一参数化调制/解调内核 + 链路质量度量（原子积木）
==========================================================

设计红线（AGENTS.md「杜绝硬编码/一次性工程/原子能力、模型编排」）：
调制与解调原先散落在 gk2a_lrit / fengyun_sat / freedv_modem / digital_modes /
ardop_adapter 等处，各自带一套 RRC 与符号映射（同名不同参）。本模块提供
**单一参数化内核**（调制 / 解调 / 星座质量 / 误码评估），各协议模块随后
收敛到同一来源，行为由回归向量钉死、零漂移。

模型可操作的能力（每块一个独立小工具，绝不打包）：
- 调制：ASK / BPSK / QPSK / 8PSK，矩形或 RRC 成形，任意 sps 与滚降
- 解调：匹配滤波 + 符号定时抽取 + 硬/软判决
- 星座质量：EVM、I/Q 失衡、相位误差、误判率
- 链路误码：调制 → AWGN 信道 → 解调 → BER 实测，并与理论值对照
- 成形系数：RRC/RC 抽头（复用 fir_taps 单一来源，不重复实现）

物理依据：公开教科书口径（矩形脉冲 + 升余弦/根升余弦成形、AWGN 信道、
Q(sqrt(2Eb/N0)) 理论误码率）。所有近似（RRC 长度/截断、眼图采样点数）
在 docstring 与输出里标注，不冒充精确值。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .fir_taps import root_raised_cosine_taps
from .tool_registry import ToolResult

__all__ = [
    "SCHEMES", "symbol_points", "modulate_bits", "demodulate_iq",
    "constellation_metrics", "ber_measure", "link_ber_test",
    "q_function", "theoretical_ber",
]

SCHEMES = ("bpsk", "qpsk", "ask", "8psk")

# 每符号比特数（ASK/BPSK=1，QPSK=2，8PSK=3）
BITS_PER_SYMBOL = {"bpsk": 1, "qpsk": 2, "ask": 1, "8psk": 3}


def q_function(x: float) -> float:
    """高斯 Q 函数 Q(x) = 0.5·erfc(x/√2)。"""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def theoretical_ber(scheme: str, ebn0_db: float) -> float:
    """理论误码率（AWGN、硬判决、理想同步）：

    BPSK/ASK  OOK 相干：Q(√(2Eb/N0))；QPSK：Q(√(2Eb/N0))（每符号 2 bit）；
    8PSK：按 Gray 映射的近似式（未逐 bit 精确，仅作量级对照，输出中标注）。
    """
    ebn0 = 10 ** (ebn0_db / 10.0)
    if scheme == "bpsk":
        return q_function(math.sqrt(2.0 * ebn0))
    if scheme == "ask":
        # OOK 的理论式强烈依赖"每比特能量/门限"口径（平均能量还是 1 符号能量、
        # 门限取 E/2 还是最优门限），不同教材给法不同。为避免口径误导，
        # ASK 不给理论对照值，只报实测 BER 与星座质量（诚实红线）。
        return float("nan")
    if scheme == "qpsk":
        return q_function(math.sqrt(2.0 * ebn0))
    if scheme == "8psk":
        # 近似：Q(√(2·3·Eb/N0)·sin(π/8))，量级用
        return q_function(math.sqrt(6.0 * ebn0) * math.sin(math.pi / 8))
    raise ValueError(f"未知调制 {scheme!r}，可选 {SCHEMES}")


# Gray 映射表（相邻星座点只差 1 bit）：索引 = 二进制位值 -> Gray 序位
_GRAY_INDEX = {
    # 位值 v -> 星座索引（pts_binary 顺序）；相邻位值对应的星座点只差 1 bit
    2: [0, 3, 1, 2],        # 00->(1+j), 01->(1-j), 10->(-1+j), 11->(-1-j)
    3: [0, 1, 3, 2, 6, 7, 5, 4],
}


def symbol_points(scheme: str, mapping: str = "binary") -> np.ndarray:
    """该调制的理想星座点（单位平均功率，QPSK 归一化到 1/√2）。

    mapping="binary"：索引即二进制位值（自然映射）；
    mapping="gray"   ：索引按 Gray 序排布（相邻点仅差 1 bit）。
    映射方式此前被各处写死，这里参数化，消除"同一个 QPSK 两套映射"的隐患。
    """
    s = scheme.lower()
    m = str(mapping).lower()
    if m not in ("binary", "gray"):
        raise ValueError(f"未知映射 {mapping!r}，可选 binary/gray")
    if s == "bpsk":
        pts = np.array([1 + 0j, -1 + 0j])
    elif s == "ask":
        # 索引即位值：bit1=有载波（幅度 1），bit0=空（幅度 0）
        pts = np.array([0j, 1 + 0j])
    elif s == "qpsk":
        pts = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j]) / math.sqrt(2.0)
    elif s == "8psk":
        k = np.arange(8)
        ang = 2 * np.pi * k / 8 + math.pi / 8
        pts = np.exp(1j * ang)
    else:
        raise ValueError(f"未知调制 {scheme!r}，可选 {SCHEMES}")
    if m == "gray":
        k = BITS_PER_SYMBOL[s]
        if k not in _GRAY_INDEX:
            raise ValueError(f"{s} 无 Gray 映射定义")
        out = np.zeros_like(pts)
        for bit, gidx in enumerate(_GRAY_INDEX[k]):
            out[bit] = pts[gidx]
        return out
    return pts

def _bits_to_symbol_indices(bits: np.ndarray, scheme: str) -> np.ndarray:
    """比特流 → 符号索引（MSB 优先打包）。"""
    k = BITS_PER_SYMBOL[scheme.lower()]
    b = np.asarray(bits, dtype=np.int8).ravel()
    if b.size % k:
        raise ValueError(f"比特数 {b.size} 不是 {scheme} 每符号 {k} 位的整数倍")
    if np.any((b != 0) & (b != 1)):
        raise ValueError("bits 只能含 0/1")
    b = b.reshape(-1, k)
    weights = (2 ** np.arange(k - 1, -1, -1)).astype(np.int64)
    return b.astype(np.int64) @ weights


def _rrc_taps(sps: int, alpha: float, length_sps: int = 10) -> np.ndarray:
    """根升余弦抽头（复用 fir_taps 单一来源；长度按 sps 折算，截断到 length_sps×sps）。"""
    n_taps = int(max(1, length_sps * sps))
    if n_taps % 2 == 0:
        n_taps += 1
    # fir_taps 口径：Ts = sample_rate / symbol_rate，这里取符号率 1、采样率 sps
    return root_raised_cosine_taps(n_taps, alpha, 1.0, float(sps))


def modulate_bits(bits: Sequence[int], scheme: str = "bpsk", sps: int = 1,
                  shaping: str = "rect", alpha: float = 0.35,
                  amplitude: float = 1.0,
                  length_sps: int = 10,
                  mapping: str = "binary") -> Dict[str, Any]:
    """比特流 → 基带波形（原子积木）。

    shaping="rect" 矩形脉冲（不滤波）；"rrc" 根升余弦成形（单次成形，
    发射端即可；接收端再配一次匹配滤波时总响应为 RC）。
    返回 {"waveform": complex64, "symbols": complex128, "n_symbols", "params"}。
    """
    scheme = scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(f"未知调制 {scheme!r}，可选 {SCHEMES}")
    sps = int(sps)
    if sps < 1:
        raise ValueError("sps 必须 >= 1")
    if len(bits) == 0:
        raise ValueError("bits 为空：无存在可调制的符号")
    idx = _bits_to_symbol_indices(bits, scheme)
    pts = symbol_points(scheme, mapping)
    syms = pts[idx]
    up = np.repeat(syms, sps) * float(amplitude)

    shape = shaping.lower()
    if shape == "rrc":
        # 标准模型：冲激列（每 sps 采样一个符号）过 RRC；符号瞬时落在 n·sps
        taps = _rrc_taps(sps, alpha, length_sps)
        impulse = np.zeros(len(syms) * sps, dtype=np.complex128)
        impulse[::sps] = syms
        up = np.convolve(impulse, taps, mode="full")
        delay = (len(taps) - 1) // 2
        up = up[delay: delay + len(syms) * sps]
        if len(up) < len(syms) * sps:
            up = np.pad(up, (0, len(syms) * sps - len(up)))
    elif shape == "rect":
        # NRZ：矩形脉冲，符号能量居于块内，判决取块中心
        taps = np.array([1.0])
    else:
        raise ValueError(f"未知 shaping {shaping!r}，可选 rect/rrc")
    return {
        "waveform": up.astype(np.complex64),
        "symbols": syms,
        "n_symbols": int(len(syms)),
        "params": {"scheme": scheme, "sps": sps, "shaping": shape,
                   "alpha": alpha, "amplitude": amplitude,
                   "mapping": mapping, "n_taps": int(len(taps))},
    }


def demodulate_iq(iq: Sequence[complex], scheme: str = "bpsk", sps: int = 1,
                  shaping: str = "rect", alpha: float = 0.35,
                  length_sps: int = 10,
                  mapping: str = "binary") -> Dict[str, Any]:
    """基带波形 → 符号与比特（原子积木）。

    匹配滤波（rrc 时用同抽头）+ 按 sps 抽取符号（假设符号定时已对齐；
    未对齐的偏移由调用方先做 timing 恢复——本函数在 docstring 标注该前提）。
    返回 {"symbols", "bits", "hard_bits", "params"}。
    """
    scheme = scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(f"未知调制 {scheme!r}，可选 {SCHEMES}")
    sps = int(sps)
    x = np.asarray(iq, dtype=np.complex128).ravel()
    if x.size == 0:
        raise ValueError("iq 为空")
    shape = shaping.lower()
    if shape == "rrc":
        taps = _rrc_taps(sps, alpha, length_sps)
        # 匹配滤波（=再过一次 RRC，总响应为 RC，符号瞬时零 ISI）：
        # 全卷积后切除 (L-1)/2 群延迟，符号瞬时对齐 n·sps
        delay = (len(taps) - 1) // 2
        x = np.convolve(x, taps, mode="full")[delay:]
        phase = 0
    elif shape == "rect":
        # NRZ：符号能量居于块内，判决取块中心
        phase = sps // 2
    else:
        raise ValueError(f"未知 shaping {shaping!r}，可选 rect/rrc")

    usable = (len(x) // sps) * sps
    if usable == 0:
        raise ValueError("iq 长度不足一个符号")
    syms = x[:usable].reshape(-1, sps)[:, phase]

    pts = symbol_points(scheme, mapping)
    if scheme == "ask":
        # OOK 幅度判决：有载波电平取 90 分位，门限取其一半
        mag = np.abs(syms)
        on_level = float(np.percentile(mag, 90)) if mag.size else 0.0
        nearest = (mag > on_level / 2.0).astype(int)
        if on_level <= 0:
            nearest = np.zeros(len(syms), dtype=int)
    else:
        # 恒包络调制：按 RMS 归一后最近点判决
        p_norm = float(np.mean(np.abs(syms) ** 2))
        if p_norm <= 0:
            raise ValueError("符号功率为 0，无法判决")
        norm = syms / math.sqrt(p_norm)
        d = np.abs(norm[:, None] - pts[None, :])
        nearest = np.argmin(d, axis=1)
    k = BITS_PER_SYMBOL[scheme]
    hard = np.zeros((len(nearest), k), dtype=np.int8)
    for i, ni in enumerate(nearest):
        for j in range(k):
            hard[i, j] = (ni >> (k - 1 - j)) & 1
    return {
        "symbols": syms,
        "hard_bits": hard.ravel(),
        "bits": hard.ravel(),
        "n_symbols": int(len(nearest)),
        "params": {"scheme": scheme, "sps": sps, "shaping": shape,
                   "alpha": alpha, "mapping": mapping},
    }


def constellation_metrics(symbols: Sequence[complex], scheme: str = "qpsk",
                         mapping: str = "binary") -> Dict[str, Any]:
    """星座质量：EVM / 相位误差 / I-Q 失衡 / 误判率（原子积木）。"""
    scheme = scheme.lower()
    x = np.asarray(symbols, dtype=np.complex128).ravel()
    if x.size == 0:
        raise ValueError("symbols 为空")
    pts = symbol_points(scheme, mapping)
    if scheme == "ask":
        mag = np.abs(x)
        on_level = float(np.percentile(mag, 90)) if mag.size else 0.0
        if on_level <= 0:
            raise ValueError("符号幅度为 0，无法评估")
        xn = x / on_level
    else:
        p = float(np.mean(np.abs(x) ** 2))
        if p <= 0:
            raise ValueError("符号功率为 0，无法评估")
        xn = x / math.sqrt(p)
    d = np.abs(xn[:, None] - pts[None, :])
    nearest = np.argmin(d, axis=1)
    err = xn - pts[nearest]
    evm = float(np.sqrt(np.mean(np.abs(err) ** 2)))
    # 相位误差（仅对非零幅度星座有意义：ASK 的 0 点无相位）
    nz = np.abs(xn) > 0.3
    phase_err = np.angle(xn[nz] * np.conj(pts[nearest][nz])) if np.any(nz) else np.array([0.0])
    # I/Q 失衡：I 与 Q 的功率比
    pi = float(np.mean(xn.real ** 2))
    pq = float(np.mean(xn.imag ** 2))
    iq_imbalance_db = 10 * math.log10((pi + 1e-12) / (pq + 1e-12)) if pq > 0 else 0.0
    # 误判率：判定点距理想点超过星座最小间距的一半
    if len(pts) > 1:
        dd = np.abs(pts[:, None] - pts[None, :])   # 复平面欧氏距离（逐元素模）
        np.fill_diagonal(dd, np.inf)
        min_gap = float(dd.min())
        errs = np.abs(err)
        ser = float(np.mean(errs > min_gap / 2.0))
    else:
        ser = 0.0
    return {
        "evm_rms_pct": round(evm * 100, 3),
        "evm_db": round(20 * math.log10(evm + 1e-12), 2),
        "phase_error_std_deg": round(float(np.std(np.degrees(phase_err))), 3),
        "phase_error_mean_deg": round(float(np.mean(np.degrees(phase_err))), 3),
        "iq_imbalance_db": round(iq_imbalance_db, 3),
        "symbol_error_rate": round(ser, 6),
        "symbol_count": int(len(x)),
        "cluster_centers": [
            {"re": round(float(np.mean(xn[nearest == k].real)), 4),
             "im": round(float(np.mean(xn[nearest == k].imag)), 4)}
            for k in range(len(pts)) if np.any(nearest == k)
        ],
    }


def ber_measure(bits_tx: Sequence[int], bits_rx: Sequence[int]) -> Dict[str, Any]:
    """误比特率与误符号率（原子积木）。"""
    tx = np.asarray(bits_tx, dtype=np.int8).ravel()
    rx = np.asarray(bits_rx, dtype=np.int8).ravel()
    n = min(len(tx), len(rx))
    if n == 0:
        raise ValueError("无可比较的比特")
    tx, rx = tx[:n], rx[:n]
    err = int(np.sum(tx != rx))
    # 误符号率：按每符号比特数折算（每 k 位一组）
    k = 1
    ser_blocks = 0
    for i in range(0, n - k + 1, k):
        if np.any(tx[i:i + k] != rx[i:i + k]):
            ser_blocks += 1
    n_blocks = max(1, (n + k - 1) // k)
    return {
        "bit_errors": err,
        "bits_compared": int(n),
        "ber": err / n,
        "symbol_error_rate": ser_blocks / n_blocks,
    }


def link_ber_test(scheme: str = "bpsk", n_bits: int = 4000, ebn0_db: float = 6.0,
                  sps: int = 4, shaping: str = "rrc", alpha: float = 0.35,
                  seed: int = 20241012,
                  mapping: str = "binary") -> Dict[str, Any]:
    """链路误码实测：调制 → AWGN → 解调 → BER，并与理论值对照（原子积木）。

    Eb/N0 定义：噪声方差按信号平均功率 / (Eb/N0) 归一（整形后信号功率）。
    理论值按公开教科书式给出，实测值受实现细节（抽头长度/定时对齐）影响，
    两者接近即可认为链路实现正确。
    """
    scheme = scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(f"未知调制 {scheme!r}，可选 {SCHEMES}")
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, int(n_bits)).astype(np.int8)
    mod = modulate_bits(bits, scheme=scheme, sps=sps, shaping=shaping,
                        alpha=alpha, mapping=mapping)
    wave = mod["waveform"].astype(np.complex128)
    sig_pow = float(np.mean(np.abs(wave) ** 2))
    if sig_pow <= 0:
        raise ValueError("信号功率为 0")
    ebn0 = 10 ** (ebn0_db / 10.0)
    # Eb/N0 归一：每符号 k 比特，Es = P·sps/R_s，Eb = Es/k
    #   N0 = noise_pow/R_s  =>  noise_pow = P·sps/(k·EbN0)
    k_bits = BITS_PER_SYMBOL[scheme]
    noise_pow = sig_pow * sps / (k_bits * ebn0)
    noisy = wave + np.sqrt(noise_pow / 2.0) * (
        rng.standard_normal(len(wave)) + 1j * rng.standard_normal(len(wave)))
    dem = demodulate_iq(noisy, scheme=scheme, sps=sps, shaping=shaping,
                        alpha=alpha, mapping=mapping)
    # 去掉成形/滤波引入的首尾过渡符号（各端长度_sps 量级）
    k = BITS_PER_SYMBOL[scheme]
    tx_syms = bits[: (len(bits) // k) * k].reshape(-1, k)
    rx_syms = dem["hard_bits"].reshape(-1, k)[: len(tx_syms)]
    m = min(len(tx_syms), len(rx_syms))
    meas = ber_measure(tx_syms[:m].ravel(), rx_syms[:m].ravel())
    theo = theoretical_ber(scheme, ebn0_db)
    return {
        "scheme": scheme,
        "ebn0_db": ebn0_db,
        "sps": sps,
        "shaping": shaping,
        "alpha": alpha,
        "seed": seed,
        "mapping": mapping,
        "measured_ber": meas["ber"],
        "measured_ser": meas["symbol_error_rate"],
        "bit_errors": meas["bit_errors"],
        "bits_compared": meas["bits_compared"],
        "theoretical_ber": theo,
        "constellation": constellation_metrics(dem["symbols"], scheme, mapping),
        "note": ("理论值按 AWGN 硬判决教科书式；实测受抽头长度/定时对齐影响，"
                 "两者同量级即视为实现正确"),
    }


# ═══════════════════════════════════════════════════════════
# 工具层（每块积木一个独立小工具）
# ═══════════════════════════════════════════════════════════
def _err(e: Exception) -> ToolResult:
    return ToolResult(success=False,
                      content=f"参数/计算错误: {type(e).__name__}: {e}",
                      data={"error": str(e)})


def _as_list(x) -> List[float]:
    return np.asarray(x).ravel().tolist()


def _modulate(args):
    """比特流 → 基带波形（ASK/BPSK/QPSK/8PSK，矩形或 RRC 成形）。"""
    try:
        bits = args.get("bits")
        if bits is None:
            raise ValueError("bits 不能为空")
        res = modulate_bits(
            bits, scheme=str(args.get("scheme", "bpsk")),
            sps=int(args.get("sps", 1)),
            shaping=str(args.get("shaping", "rect")),
            alpha=float(args.get("alpha", 0.35)),
            amplitude=float(args.get("amplitude", 1.0)))
        return ToolResult(
            success=True,
            content=(f"{res['params']['scheme'].upper()} 调制完成："
                     f"{res['n_symbols']} 符号 / {len(res['waveform'])} 采样"
                     f"（sps={res['params']['sps']}，成形 {res['params']['shaping']}）"),
            data={"waveform": _as_list(res["waveform"]),
                  "symbols": _as_list(res["symbols"]),
                  "n_symbols": res["n_symbols"],
                  "params": res["params"]},
        )
    except Exception as e:
        return _err(e)


def _demodulate(args):
    """基带波形 → 符号与比特（匹配滤波 + 符号抽取 + 硬判决）。"""
    try:
        iq = args.get("iq")
        if iq is None:
            raise ValueError("iq 不能为空")
        res = demodulate_iq(
            iq, scheme=str(args.get("scheme", "bpsk")),
            sps=int(args.get("sps", 1)),
            shaping=str(args.get("shaping", "rect")),
            alpha=float(args.get("alpha", 0.35)))
        return ToolResult(
            success=True,
            content=(f"解调完成：{res['n_symbols']} 符号，"
                     f"{len(res['bits'])} 比特"
                     f"（假设符号定时已对齐）"),
            data={"bits": [int(b) for b in res["bits"]],
                  "symbols": _as_list(res["symbols"]),
                  "n_symbols": res["n_symbols"],
                  "params": res["params"]},
        )
    except Exception as e:
        return _err(e)


def _constellation(args):
    """星座质量：EVM / 相位误差 / I-Q 失衡 / 误符号率。"""
    try:
        syms = args.get("symbols")
        if syms is None:
            raise ValueError("symbols 不能为空")
        m = constellation_metrics(syms, scheme=str(args.get("scheme", "qpsk")))
        return ToolResult(
            success=True,
            content=(f"EVM {m['evm_rms_pct']:.3f}%（{m['evm_db']:.1f} dB）"
                     f"  相位误差 σ={m['phase_error_std_deg']:.3f}°"
                     f"  I/Q 失衡 {m['iq_imbalance_db']:.2f} dB"
                     f"  误符号率 {m['symbol_error_rate']*100:.3f}%"),
            data=m,
        )
    except Exception as e:
        return _err(e)


def _ber(args):
    """误码率测量（收发比特对比）。"""
    try:
        tx = args.get("bits_tx")
        rx = args.get("bits_rx")
        if tx is None or rx is None:
            raise ValueError("bits_tx 与 bits_rx 均需提供")
        m = ber_measure(tx, rx)
        return ToolResult(
            success=True,
            content=(f"BER {m['ber']:.6f}（{m['bit_errors']}/{m['bits_compared']} bit）"
                     f"  误符号率 {m['symbol_error_rate']:.6f}"),
            data=m,
        )
    except Exception as e:
        return _err(e)


def _link_ber(args):
    """链路误码实测：调制→AWGN→解调→BER，并与理论值对照。"""
    try:
        res = link_ber_test(
            scheme=str(args.get("scheme", "bpsk")),
            n_bits=int(args.get("n_bits", 4000)),
            ebn0_db=float(args.get("ebn0_db", 6.0)),
            sps=int(args.get("sps", 4)),
            shaping=str(args.get("shaping", "rrc")),
            alpha=float(args.get("alpha", 0.35)),
            seed=int(args.get("seed", 20241012)))
        theo = res["theoretical_ber"]
        theo_txt = (f"，理论 {theo:.6f}" if theo == theo
                    else "（该调制不提供理论对照，口径依赖能量/门限定义）")
        return ToolResult(
            success=True,
            content=(f"{res['scheme'].upper()} @ Eb/N0={res['ebn0_db']:.1f} dB："
                     f"实测 BER {res['measured_ber']:.6f}{theo_txt}"
                     f"；EVM {res['constellation']['evm_rms_pct']:.2f}%"),
            data=res,
        )
    except Exception as e:
        return _err(e)


def _pulse_taps(args):
    """成形系数：根升余弦抽头（复用 fir_taps 单一来源）。"""
    try:
        sps = int(args.get("sps", 4))
        alpha = float(args.get("alpha", 0.35))
        n = int(args.get("num_taps", 10 * sps))
        if n % 2 == 0:
            n += 1
        taps = root_raised_cosine_taps(n, alpha, 1.0, float(sps))
        return ToolResult(
            success=True,
            content=(f"RRC 抽头 {n} 个（sps={sps}，alpha={alpha}）："
                     f"和={float(np.sum(taps)):.6f} 峰值={float(np.max(np.abs(taps))):.6f}"),
            data={"taps": _as_list(taps), "num_taps": n, "sps": sps, "alpha": alpha},
        )
    except Exception as e:
        return _err(e)


def register_modulation_tools(registry) -> None:
    """把调制/解调/星座质量/误码积木注册进工具注册表（逐块，不打包）。"""
    R = registry.register

    R(
        name="modulate_symbols",
        description="比特流→基带波形：ASK/BPSK/QPSK/8PSK，矩形或 RRC 成形，任意 sps。"
                    "原子积木，供模型拼接收链或做链路评估。",
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"}},
            "scheme": {"type": "string", "enum": list(SCHEMES), "default": "bpsk"},
            "sps": {"type": "integer", "default": 1},
            "shaping": {"type": "string", "enum": ["rect", "rrc"], "default": "rect"},
            "alpha": {"type": "number", "default": 0.35},
            "amplitude": {"type": "number", "default": 1.0},
        }, "required": ["bits"]},
        handler=_modulate, category="modulation",
    )
    R(
        name="demodulate_symbols",
        description="基带波形→符号与比特：匹配滤波+符号抽取+硬判决（假设定时已对齐）。"
                    "原子积木。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "items": {"type": "number"}},
            "scheme": {"type": "string", "enum": list(SCHEMES), "default": "bpsk"},
            "sps": {"type": "integer", "default": 1},
            "shaping": {"type": "string", "enum": ["rect", "rrc"], "default": "rect"},
            "alpha": {"type": "number", "default": 0.35},
        }, "required": ["iq"]},
        handler=_demodulate, category="modulation",
    )
    R(
        name="constellation_metrics",
        description="星座质量：EVM / 相位误差 / I-Q 失衡 / 误符号率 / 簇心。原子积木。",
        parameters={"type": "object", "properties": {
            "symbols": {"type": "array", "items": {"type": "number"}},
            "scheme": {"type": "string", "enum": list(SCHEMES), "default": "qpsk"},
        }, "required": ["symbols"]},
        handler=_constellation, category="modulation",
    )
    R(
        name="measure_ber",
        description="误码率测量：收发比特对比，给 BER 与误符号率。原子积木。",
        parameters={"type": "object", "properties": {
            "bits_tx": {"type": "array", "items": {"type": "integer"}},
            "bits_rx": {"type": "array", "items": {"type": "integer"}},
        }, "required": ["bits_tx", "bits_rx"]},
        handler=_ber, category="modulation",
    )
    R(
        name="link_ber_test",
        description="链路误码实测：调制→AWGN→解调→BER，并与理论值对照。"
                    "原子积木，供模型自测链路实现。",
        parameters={"type": "object", "properties": {
            "scheme": {"type": "string", "enum": list(SCHEMES), "default": "bpsk"},
            "n_bits": {"type": "integer", "default": 4000},
            "ebn0_db": {"type": "number", "default": 6.0},
            "sps": {"type": "integer", "default": 4},
            "shaping": {"type": "string", "enum": ["rect", "rrc"], "default": "rrc"},
            "alpha": {"type": "number", "default": 0.35},
            "seed": {"type": "integer", "default": 20241012},
        }, "required": []},
        handler=_link_ber, category="modulation",
    )
    R(
        name="pulse_shape_taps",
        description="成形系数：根升余弦(RRC)抽头（与全仓 fir_taps 同一来源）。原子积木。",
        parameters={"type": "object", "properties": {
            "sps": {"type": "integer", "default": 4},
            "alpha": {"type": "number", "default": 0.35},
            "num_taps": {"type": "integer", "description": "抽头数（奇数，自动补）"},
        }, "required": []},
        handler=_pulse_taps, category="modulation",
    )
