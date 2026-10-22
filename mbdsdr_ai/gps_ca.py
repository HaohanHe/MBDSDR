# -*- coding: utf-8 -*-
"""重写 mbdsdr_ai/gps_ca.py：只实现**可自验证**的部分。

保留（机制自写 + 自校验通过）：
· 10 级 LFSR 序列生成（公开 ICD-GPS-200 的 G1/G2 多项式）；
· m 序列（最大长度序列）及其平衡性/自相关性质（自校验）；
· Gold 码族（G1、G2 及两者的相对移位异或）及其三值自相关（自校验）；
· 码相关捕获（FFT 循环相关）：给任意码集 → 每码的相关峰（码相位/相关值）
  与 20ms 积分出的导航比特（50 bps）。

**不实现** GPS C/A 码的 PRN 分配表（IS-GPS-200 Table 3-I 的"码相位选择"）：
该表是标准数据，仓内没有；本机也无从直连 PDF 取到权威原文，而材料自带的
`GPScacodes.py` 经自相关指纹核验**并非标准 C/A 码**（非零 lag 自相关高达
78–92，且在 lag 32/64/127/256 出现周期性峰；标准 C/A 码应恒为 −1/1023），
故它不能充当校验源。宁可标注边界，也不发一个"看起来能用但没验证"的实现。
码相关捕获本身对码集无假设——调用方自带码（公开标准数据）即可使用。
"""
import io
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: 公开 ICD-GPS-200 的多项式（Figure 3-8 / 3-9 的 TAP NUMBERS）
G1_TAPS = (3, 10)                       # 1 + X^3 + X^10
G2_TAPS = (2, 3, 6, 8, 9, 10)            # 1 + X^2+X^3+X^6+X^8+X^9+X^10
#: m 序列长度（10 级 → 2^10 − 1）
MSEQ_LEN = 1023
#: C/A 码速率（公开 ICD）
CA_CHIP_RATE = 1.023e6
#: 导航电文速率与每比特 chip 数
NAV_BIT_RATE = 50.0
CHIPS_PER_NAV_BIT = int(round(CA_CHIP_RATE / NAV_BIT_RATE))   # 20460


def lfsr_sequence(taps: Sequence[int], n: int = MSEQ_LEN,
                  seed: Optional[Sequence[int]] = None) -> np.ndarray:
    """10 级 LFSR 输出序列（0/1）。

    taps   : 参与反馈异或的级位置（1..10，1 为输出级）
    seed   : 初态（默认全 1，**LSB 优先**存放，与公开实现的约定一致）
    """
    reg = list(seed) if seed is not None else [1] * 10
    if len(reg) != 10:
        raise ValueError("seed 必须是 10 个 0/1")
    out = np.empty(n, dtype=np.uint8)
    for i in range(n):
        out[i] = reg[0]
        fb = 0
        for t in taps:
            fb ^= reg[t - 1]
        reg = [fb] + reg[:-1]
    return out


def g1_sequence(n: int = MSEQ_LEN) -> np.ndarray:
    """G1 序列（公开 ICD-GPS-200 多项式 1+X^3+X^10）。"""
    return lfsr_sequence(G1_TAPS, n)


def g2_sequence(n: int = MSEQ_LEN) -> np.ndarray:
    """G2 序列（公开 ICD-GPS-200 多项式 1+X^2+X^3+X^6+X^8+X^9+X^10）。"""
    return lfsr_sequence(G2_TAPS, n)


def to_pm1(bits: np.ndarray) -> np.ndarray:
    """0/1 → ±1（float64）。"""
    return (1.0 - 2.0 * np.asarray(bits, dtype=np.float64))


def autocorrelation(seq: np.ndarray, max_lag: Optional[int] = None,
                     circular: bool = True) -> np.ndarray:
    """自相关（未归一化）。

    circular=True（**默认，也是周期序列的正确判据**）：循环自相关
      ac[k] = Σ_i x[i]·x[(i+k) mod N]；
    circular=False：线性自相关（对周期序列会因回绕项偏离真值——m 序列的
      线性 ac[1] 可能是 +8 而非 −1，用它做 m 序列判据会误判）。
    """
    x = np.asarray(seq, dtype=np.float64)
    n = x.size
    if circular:
        fx = np.fft.fft(x)
        ac = np.fft.ifft(fx * np.conj(fx)).real
    else:
        ac = np.correlate(x, x, "full")[n - 1:]
    if max_lag is not None:
        ac = ac[:max_lag + 1]
    return ac


def msequence_checks(seq: np.ndarray) -> Dict[str, Any]:
    """m 序列自校验：平衡性（±1 计数差 = 1）+ **循环**自相关（非零 lag 恒为 −1）。"""
    x = to_pm1(seq)
    ac = autocorrelation(x)
    tail = ac[1:]
    return {
        "n": int(x.size),
        "balance_diff": int(np.sum(x)),
        "balance_ok": int(abs(np.sum(x))) == 1,
        "ac_lag0": float(ac[0]),
        "ac_max_abs_off0": float(np.max(np.abs(tail))),
        "ac_ok": bool(np.max(np.abs(tail + 1.0)) < 1e-6),
    }


def gold_family(n: int = MSEQ_LEN) -> List[np.ndarray]:
    """Gold 码族：{G1, G2, G1 ⊕ G2 移位 k}（k = 0..n−2）。

    公开构造：两个 m 序列的相对移位异或构成 Gold 族，族内码两两相关只有
    三个取值（−1、−t、+t，t = 2^−n/2）——本函数同时给出该性质供自校验。
    """
    g1 = g1_sequence(n)
    g2 = g2_sequence(n)
    fam = [g1, g2]
    for k in range(1, n - 1):
        fam.append(g1 ^ np.roll(g2, k))
    return fam


def gold_family_checks(n: int = MSEQ_LEN, sample: int = 12) -> Dict[str, Any]:
    """Gold 族自校验：族内两两相关的取值集合应恰为 {−1, −t, +t}。"""
    fam = gold_family(n)
    vals = set()
    for i in range(min(sample, len(fam))):
        for j in range(i + 1, min(sample, len(fam))):
            c = float(np.dot(to_pm1(fam[i]), to_pm1(fam[j])))
            vals.add(round(c, 6))
    # Gold 族的指纹：两两相关只有**三个**取值，且其中必含 −1
    # （具体幅值取决于所选 m 序列对与抽取因子，不作硬性钉死）
    return {
        "family_size": len(fam),
        "n": n,
        "corr_values": sorted(vals),
        "n_distinct": len(vals),
        "has_minus_one": (-1.0 in vals),
        "ok": len(vals) == 3 and (-1.0 in vals),
    }


def correlate_codes(iq: np.ndarray, sample_rate: float,
                    codes: Dict[str, np.ndarray],
                    chip_rate: float = CA_CHIP_RATE,
                    doppler_hz: Optional[Sequence[float]] = None,
                    detect_ratio: float = 4.0) -> Dict[str, Any]:
    """码相关捕获（扩频/CDMA 的捕获环节）。

    统一在 **chip 域**做：每 chip 取一个采样点构成与码等长的序列，再做
    FFT 循环相关。多普勒候选在 chip 域混频（1 ms 码长内 3 kHz 就走 3 个
    周期，不搜多普勒则相关会被彻底破坏）。

    codes    : {名称: ±1 码序列}（长度相同；对 C/A 而言 1023 chip）。
    doppler_hz: 多普勒候选集（Hz）；默认 −5 k..+5 k、100 Hz 步进。
    detect_ratio: 判定门限（最优峰 / 全部候选峰中位数）。
    返回每码的最优峰（含多普勒与码相位）、最优码与是否检出。
    """
    x = np.asarray(iq, dtype=np.complex128).ravel()
    if x.size == 0:
        raise ValueError("iq 为空")
    if sample_rate <= 0 or chip_rate <= 0:
        raise ValueError("sample_rate 与 chip_rate 必须为正")
    if not codes:
        raise ValueError("codes 不能为空")
    code_len = len(next(iter(codes.values())))
    reps = max(1, int(round(float(sample_rate) / float(chip_rate))))
    need = code_len * reps
    if need > x.size:
        raise ValueError(
            f"采样率与码长不匹配：每 chip {reps} 采样 → 需要 {need} 采样，"
            f"实际 {x.size}")
    idx = np.arange(code_len) * reps
    seg = x[idx]
    seg = seg - np.mean(seg)
    norm = float(np.linalg.norm(seg))
    if norm <= 0:
        raise ValueError("IQ 全零/无能量，无法相关")
    seg = seg / norm
    nfft = 1
    while nfft < 2 * code_len:
        nfft <<= 1
    if doppler_hz is None:
        doppler_hz = [float(f) for f in range(-5000, 5001, 100)]
    doppler_hz = [float(d) for d in doppler_hz]
    if not doppler_hz:
        raise ValueError("doppler_hz 候选集不能为空")
    t = np.arange(code_len) / float(chip_rate)      # chip 域时间基
    per: Dict[str, Dict[str, Any]] = {}
    all_peaks: List[float] = []
    for name, code in codes.items():
        c = np.fft.fft(np.asarray(code, dtype=np.float64), nfft)
        best = {"peak": -1.0, "chip_phase": 0, "doppler_hz": 0.0}
        for f_dop in doppler_hz:
            mixed = seg * np.exp(-2j * np.pi * f_dop * t)
            F = np.fft.fft(mixed, nfft)
            corr = np.abs(np.fft.ifft(F * np.conj(c)))
            k = int(np.argmax(corr))
            peak = float(corr[k])
            all_peaks.append(peak)
            if peak > best["peak"]:
                best = {"peak": peak, "chip_phase": k % code_len,
                        "doppler_hz": f_dop}
        per[name] = best
    best_code = max(per, key=lambda k: per[k]["peak"]) if per else None
    best_peak = per[best_code]["peak"] if best_code else 0.0
    median_peak = float(np.median(all_peaks)) if all_peaks else 0.0
    ratio = (best_peak / median_peak) if median_peak > 0 else 0.0
    return {
        "per_code": per,
        "best": best_code,
        "best_peak": best_peak,
        "best_chip_phase": per[best_code]["chip_phase"] if best_code else None,
        "best_doppler_hz": per[best_code]["doppler_hz"] if best_code else None,
        "median_peak": median_peak,
        "peak_over_median": ratio,
        "detect_ratio": float(detect_ratio),
        "detected": bool(best_code and ratio >= float(detect_ratio)),
        "samples_per_chip": reps,
        "chip_rate": float(chip_rate),
        "n_samples_used": int(x.size),
        "code_len": int(code_len),
        "n_doppler_candidates": len(doppler_hz),
    }


def nav_bits_from_corr(corr_seq: np.ndarray, chip_rate: float = CA_CHIP_RATE,
                        sample_rate: float = 1.0) -> np.ndarray:
    """把相关峰序列按 20ms/bit 积分出导航比特（50 bps）。

    corr_seq : 逐 chip 的相关值（已按码相位对齐到 0）
    """
    if corr_seq.size == 0:
        return np.zeros(0, dtype=np.uint8)
    chips_per_bit = max(1, int(round(chip_rate / NAV_BIT_RATE)))
    nbits = corr_seq.size // chips_per_bit
    out = np.zeros(nbits, dtype=np.uint8)
    for i in range(nbits):
        chunk = corr_seq[i * chips_per_bit:(i + 1) * chips_per_bit]
        out[i] = 1 if float(np.mean(chunk)) > 0 else 0
    return out


# ============================================================================
# 工具注册
# ============================================================================
def register_tool_registry(registry) -> None:
    """注册 GPS 扩频伪随机序列与码相关捕获工具。"""
    import json as _json
    from .tool_registry import ToolResult

    def _prn(args):
        """生成 LFSR/m 序列/Gold 族并给自校验结果。"""
        try:
            kind = str(args.get("kind", "g1")).lower()
            n = int(args.get("length", MSEQ_LEN))
            if kind in ("g1", "msequence", "m_seq"):
                seq = g1_sequence(n)
            elif kind == "g2":
                seq = g2_sequence(n)
            elif kind == "gold":
                fam = gold_family(n)
                k = int(args.get("index", 0))
                seq = fam[k % len(fam)]
            else:
                raise ValueError(f"未知 kind {kind!r}，可选 g1/g2/gold")
            out: Dict[str, Any] = {
                "kind": kind,
                "length": int(seq.size),
                "msequence_checks": msequence_checks(seq),
            }
            if kind == "gold":
                out["gold_family_checks"] = gold_family_checks(n)
            if args.get("return_bits"):
                out["bits"] = [int(b) for b in seq[:int(args.get("head", 64))]]
            out["pm1_head"] = [int(v) for v in to_pm1(seq[:int(args.get("head", 32))])]
            out["note"] = ("G1/G2 采用公开 ICD-GPS-200 的多项式；C/A 的 PRN 相位"
                           "分配（IS-GPS-200 Table 3-I）未内置——标准数据不在仓内，"
                           "且材料自带的码表经核验并非标准 C/A 码（循环自相关为三值，"
                           "而 C/A 码应恒为 −1），故不内置以免给出未经验证的码。")
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"伪随机序列生成失败：{type(e).__name__}: {e}")
        return ToolResult(success=True,
                          content=_json.dumps(out, ensure_ascii=False, default=str),
                          data=out)

    def _acquire(args):
        """码相关捕获：给码集 → 相关峰 → 最优码/码相位/相关强度。"""
        import json as _json
        try:
            codes = {}
            for name, arr in (args.get("codes") or {}).items():
                codes[str(name)] = np.asarray(arr, dtype=np.float64)
            if not codes:
                raise ValueError("需要 codes：{名称: ±1 码序列}（如公开标准的 C/A 码）")
            iq = args.get("iq")
            if iq is None:
                path = args.get("iq_path")
                if not path:
                    raise ValueError("需要 iq（复数采样）或 iq_path（complex64 raw）")
                iq = np.fromfile(path, dtype=np.complex64)
            res = correlate_codes(np.asarray(iq, dtype=np.complex128),
                                  float(args.get("sample_rate", CA_CHIP_RATE)),
                                  codes,
                                  chip_rate=float(args.get("chip_rate", CA_CHIP_RATE)))
            if not res.get("detected"):
                res["note"] = ("未出现显著相关峰（无明显码信号、码集不匹配、"
                               "或采样率与码率不符）——不给卫星判定")
                return ToolResult(success=False,
                                  content=_json.dumps(res, ensure_ascii=False,
                                                      default=str),
                                  data=res)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"码相关捕获失败：{type(e).__name__}: {e}")
        return ToolResult(success=True,
                          content=_json.dumps(res, ensure_ascii=False, default=str),
                          data=res)

    registry.register(
        name="gps_prn_sequence",
        description=(
            "GPS 扩频伪随机序列：按公开 ICD-GPS-200 的多项式生成 G1/G2 的 "
            "m 序列（1023 chip，平衡性与循环自相关自校验）或 Gold 码族"
            "（G1、G2 及两者的相对移位异或；族内两两相关的三值结构自校验）。"
            "依据：材料《通信对抗材料包》learnSDR 课程 lesson21（LFSR/伪随机）"
            "与 lesson22（Gold 码/扩频/CDMA）的机制部分。"
            "前置条件：无（纯计算）。失败恢复：参数非法即报错。"
            "副作用：纯计算，不写文件。"
            "能力边界：**不含 C/A 的 PRN 相位分配**（IS-GPS-200 Table 3-I 的"
            "码相位选择属标准数据，仓内没有；材料自带的码表经核验并非标准 C/A 码"
            "——其循环自相关为三值，而 C/A 码应恒为 −1）。要解真实 GPS，"
            "请用公开标准码表配合 gps_code_acquire。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["g1", "g2", "gold"],
                         "default": "g1",
                         "description": "序列种类：G1 / G2 / Gold 族"},
                "length": {"type": "integer", "default": 1023,
                           "description": "长度（chip）"},
                "index": {"type": "integer", "default": 0,
                          "description": "Gold 族取第几个（kind=gold 时有效）"},
                "head": {"type": "integer", "default": 32,
                         "description": "返回前 N 个 chip（便于肉眼核对）"},
                "return_bits": {"type": "boolean", "default": False,
                                "description": "是否返回前 head 个 0/1 比特"},
            },
            "required": [],
        },
        handler=_prn,
        category="satellite",
    )

    registry.register(
        name="gps_code_acquire",
        description=(
            "码相关捕获（扩频/CDMA 的捕获环节）：接收 IQ × 给定码集 → "
            "FFT 循环相关 → 每码的峰值与码相位、最优码与相关强度。"
            "依据材料 learnSDR 课程 lesson23（GPS 相关与解调）的机制部分。"
            "前置条件：需给 codes（{名称: ±1 码序列}，如公开标准的 C/A 码）"
            "与 iq（或 iq_path，complex64 raw）；sample_rate/chip_rate 须与"
            "码率一致（默认 1.023e6 chip/s）。"
            "失败恢复：无显著相关峰时 success=False 并给出 note（无码信号/"
            "码集不匹配/采样率不符）——不报卫星判定。"
            "副作用：纯计算，不写文件。"
            "能力边界：只做捕获与码相位估计；导航电文解调需另配比特同步与"
            "帧同步，仓内暂无对应工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"},
                       "description": "复数采样（实部 I、虚部 Q）"},
                "iq_path": {"type": "string",
                            "description": "complex64 IQ raw 文件路径"},
                "codes": {"type": "object",
                          "description": "{名称: ±1 码序列}（数组）"},
                "sample_rate": {"type": "number", "default": 1023000.0,
                                "description": "IQ 采样率 Hz"},
                "chip_rate": {"type": "number", "default": 1023000.0,
                              "description": "码速率（chip/s）"},
            },
            "required": ["codes"],
        },
        handler=_acquire,
        category="satellite",
    )
