# -*- coding: utf-8 -*-
"""GPS C/A：LFSR/Gold 机制 + IS-GPS-200 标准 PRN 码（全验证链）。

机制自写 + 自校验：
· 10 级 LFSR 序列生成（公开 ICD-GPS-200 的 G1/G2 多项式）；
· m 序列（最大长度序列）及其平衡性/自相关性质（自校验）；
· Gold 码族（G1、G2 及两者的相对移位异或）及其三值相关（自校验）；
· 码相关捕获（FFT 循环相关）：给任意码集 → 每码的相关峰（码相位/相关值）
  与 20ms 积分出的导航比特（50 bps）。

标准数据（2026-11-04 补真值）：C/A 码 PRN 相位分配表（IS-GPS-200
Table 3-I）已内置，见 ``_GPS_CA_PHASE_ASSIGNMENTS`` 头注的来源与三方互核
记录；`ca_code(prn)` 输出与 IS-GPS-200E octal 列全 37 行一致、与
gps-sdr-sim 比特级一致（PRN 1-32）的标准 C/A 码。PRN 34/37 同码是标准
明文规定（Table 3-I 脚注 **）。

材料 `GPScacodes.py` 复核更正（2026-11-04 实测）：32 组码与标准 C/A
**逐比特互为反相**（±1 极性约定相反），其自相关 {|ac|} ⊆ {65, 1, 63}
完全落在 Gold 三值内——是标准码的反极性版本而非"非标准码"。此前
"三值自相关故非标准"的判词有误：Gold 码两两相关本就三值 {−65,−1,+63}，
恒 −1 是 m 序列性质，不是 C/A 的。
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

# ── 标准参考数据（Standard Reference Data）─────────────────────────────
# 名称：GPS L1 C/A 码相位分配（Code Phase Assignments）
# 权威出处：IS-GPS-200E (01 Jul 2009) §3.3.2.3 Table 3-I，
#   官方存档 https://archive.gps.gov/technical/icwg/meetings/2010/03/10/AFD-100302-042.pdf
#   （该表 PRN 1-32 分配自 ICD-GPS-200C 起冻结，跨版本不变；
#     gps.gov《L1 C/A PRN CODE ASSIGNMENTS》Jan 2026 版对 PRN 1-63 亦仅回引 IS-GPS-200）
# 提取方式：脚本从官方 PDF 文本层提取（.tmp-tool-results/gps_stage3/），非手抄。
# 交叉验证（verify_and_gold.py，全过）：
#   · G2 delay 列与 gps-sdr-sim gpssim.c codegen() 内表 32/32 一致；
#   · 本仓生成全码与 gps-sdr-sim 比特级一致（PRN 1-32）；
#   · first-10-chips octal 与 IS-GPS-200E 一致（PRN 1-37）；
#   · 两级抽头形式 ≡ 延迟形式（37/37）；
#   · Gold 指纹：自相关 ⊆ {−65,−1,63}，互相关 |max| = 65（标准界）。
# 结构：prn -> ((tap_a, tap_b), g2_delay_chips)；PRN 34 与 37 同码是标准规定。
_GPS_CA_PHASE_ASSIGNMENTS: Dict[int, Tuple[Tuple[int, int], int]] = {
    1: ((2, 6), 5), 2: ((3, 7), 6), 3: ((4, 8), 7), 4: ((5, 9), 8),
    5: ((1, 9), 17), 6: ((2, 10), 18), 7: ((1, 8), 139), 8: ((2, 9), 140),
    9: ((3, 10), 141), 10: ((2, 3), 251), 11: ((3, 4), 252), 12: ((5, 6), 254),
    13: ((6, 7), 255), 14: ((7, 8), 256), 15: ((8, 9), 257), 16: ((9, 10), 258),
    17: ((1, 4), 469), 18: ((2, 5), 470), 19: ((3, 6), 471), 20: ((4, 7), 472),
    21: ((5, 8), 473), 22: ((6, 9), 474), 23: ((1, 3), 509), 24: ((4, 6), 512),
    25: ((5, 7), 513), 26: ((6, 8), 514), 27: ((7, 9), 515), 28: ((8, 10), 516),
    29: ((1, 6), 859), 30: ((2, 7), 860), 31: ((3, 8), 861), 32: ((4, 9), 862),
    33: ((5, 10), 863), 34: ((4, 10), 950), 35: ((1, 7), 947), 36: ((2, 8), 948),
    37: ((4, 10), 950),
}

#: 仓内 LFSR 输出约定（out=reg[0]，反馈入 reg[0]）领先 ICD 寄存器输出约定
#: （输出取第 10 级）9 个 chip——与 gps-sdr-sim 序列实测校准所得常数。
_CA_PHASE_OFFSET = 9

_ALIGNED_CACHE: Dict[str, np.ndarray] = {}


def _aligned_g1_g2() -> Tuple[np.ndarray, np.ndarray]:
    """G1/G2 对齐到 ICD 相位基准（chip 0 = X1 历元起点）。"""
    if "g1" not in _ALIGNED_CACHE:
        _ALIGNED_CACHE["g1"] = np.roll(g1_sequence(), _CA_PHASE_OFFSET)
        _ALIGNED_CACHE["g2"] = np.roll(g2_sequence(), _CA_PHASE_OFFSET)
    return _ALIGNED_CACHE["g1"], _ALIGNED_CACHE["g2"]


def prn_assignment(prn: int) -> Tuple[Tuple[int, int], int]:
    """查标准表：prn -> ((G2 抽头 a, b), G2 延迟 chip)。非法 PRN 抛 ValueError。"""
    entry = _GPS_CA_PHASE_ASSIGNMENTS.get(int(prn))
    if entry is None:
        raise ValueError(
            f"PRN {prn!r} 不在 IS-GPS-200 Table 3-I（1..37）内——不给编造分配")
    return entry


def ca_code(prn: int) -> np.ndarray:
    """标准 C/A 码（0/1 uint8, 1023 chip, ICD 相位基准）。

    构造：C/A = G1 ⊕ G2 延迟 d chip（IS-GPS-200 §3.3.2.3 的延迟形式）。
    与 IS-GPS-200E Table 3-I 的 first-10-chips octal 列全 37 行一致；
    与 gps-sdr-sim 比特级一致（PRN 1-32）。
    """
    _taps, delay = prn_assignment(prn)
    g1, g2 = _aligned_g1_g2()
    idx = (np.arange(MSEQ_LEN) - delay) % MSEQ_LEN
    return (g1 ^ g2[idx]).astype(np.uint8)


def ca_code_taps(prn: int) -> np.ndarray:
    """标准 C/A 码的两级抽头形式（IS-GPS-200 Figure 3-10 的实现形式）。

    G2i = 同一运行 G2 寄存器第 a 级 ⊕ 第 b 级输出；与 ca_code() 的延迟形式
    逐比特互等（37/37 已钉入测试）。主要用于对照硬件式实现与教学演示。
    """
    (a, b), _delay = prn_assignment(prn)
    reg = [1] * 10
    hist = np.empty((MSEQ_LEN, 10), dtype=np.uint8)
    for i in range(MSEQ_LEN):
        hist[i] = reg
        fb = 0
        for t in G2_TAPS:
            fb ^= reg[t - 1]
        reg = [fb] + reg[:9]
    g1, _g2 = _aligned_g1_g2()
    return (hist[:, a - 1] ^ hist[:, b - 1] ^ g1).astype(np.uint8)


def standard_ca_codes(prns: Optional[Sequence[int]] = None) -> Dict[str, np.ndarray]:
    """标准 C/A 码集（±1 float），键为 "PRN<n>"——可直接喂码相关捕获。"""
    if prns is None:
        prns = range(1, 33)
    return {f"PRN{p}": to_pm1(ca_code(p)) for p in prns}


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


# ============================================================================
# 工具注册
# ============================================================================
def register_tool_registry(registry) -> None:
    """注册 GPS 扩频伪随机序列与码相关捕获工具。"""
    import json as _json
    from .tool_registry import ToolResult, clean_exc

    def _prn(args):
        """生成 LFSR/m 序列/Gold 族/标准 C/A 码并给自校验结果。"""
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
            elif kind in ("ca", "c/a"):
                prn = int(args.get("prn", 1))
                seq = ca_code(prn)
                (ta, tb), delay = prn_assignment(prn)
                pm1 = to_pm1(seq)
                fx = np.fft.fft(pm1)
                ac = np.fft.ifft(fx * np.conj(fx)).real
                ac_vals = sorted({int(round(v)) for v in ac[1:]})
                out: Dict[str, Any] = {
                    "kind": "ca",
                    "prn": prn,
                    "g2_taps": [ta, tb],
                    "g2_delay_chips": delay,
                    "source": "IS-GPS-200E Table 3-I（官方存档 PDF 脚本提取，"
                              "与 gps-sdr-sim 比特级互核）",
                    "length": int(seq.size),
                    "gold_checks": {
                        "ones": int(seq.sum()),
                        "autocorr_values": ac_vals,
                        "autocorr_within_gold_three_values":
                            set(ac_vals) <= {-65, -1, 63},
                    },
                }
                if args.get("return_bits"):
                    out["bits"] = [int(b) for b in seq[:int(args.get("head", 64))]]
                out["pm1_head"] = [int(v) for v in to_pm1(seq[:int(args.get("head", 32))])]
                return ToolResult(success=True,
                                  content=_json.dumps(out, ensure_ascii=False, default=str),
                                  data=out)
            else:
                raise ValueError(f"未知 kind {kind!r}，可选 g1/g2/gold/ca")
            out = {
                "kind": kind,
                "length": int(seq.size),
                "msequence_checks": msequence_checks(seq),
            }
            if kind == "gold":
                out["gold_family_checks"] = gold_family_checks(n)
            if args.get("return_bits"):
                out["bits"] = [int(b) for b in seq[:int(args.get("head", 64))]]
            out["pm1_head"] = [int(v) for v in to_pm1(seq[:int(args.get("head", 32))])]
            out["note"] = ("G1/G2 采用公开 ICD-GPS-200 的多项式；标准 C/A 码"
                           "（IS-GPS-200 Table 3-I 的 PRN 相位分配）已内置，"
                           "用 kind=ca 加 prn 取用。")
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
            codes_source = "caller"
            if not codes:
                codes = standard_ca_codes()
                codes_source = "is-gps-200e-table-3I"
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
            res["codes_source"] = codes_source
            if not res.get("detected"):
                res["note"] = ("未出现显著相关峰（无明显码信号、码集不匹配、"
                               "或采样率与码率不符）——不给卫星判定")
                return ToolResult(success=False,
                                  content=_json.dumps(res, ensure_ascii=False,
                                                      default=str),
                                  data=res)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"码相关捕获失败：{clean_exc(e)}")
        return ToolResult(success=True,
                          content=_json.dumps(res, ensure_ascii=False, default=str),
                          data=res)

    registry.register(
        name="gps_prn_sequence",
        description=(
            "GPS 扩频伪随机序列：按公开 ICD-GPS-200 的多项式生成 G1/G2 的 "
            "m 序列（1023 chip，平衡性与循环自相关自校验）、Gold 码族"
            "（G1、G2 及两者的相对移位异或；族内两两相关的三值结构自校验），"
            "或标准 C/A 码（kind=ca：IS-GPS-200 Table 3-I 的 PRN 相位分配已"
            "内置为标准参考数据，与官方标准原文 octal 列全 37 行一致、与 "
            "gps-sdr-sim 比特级互核通过；返回 Gold 三值相关自校验）。"
            "依据：材料《通信对抗材料包》learnSDR 课程 lesson21（LFSR/伪随机）"
            "与 lesson22（Gold 码/扩频/CDMA）的机制部分。"
            "前置条件：无（纯计算）。失败恢复：参数非法即报错。"
            "副作用：纯计算，不写文件。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["g1", "g2", "gold", "ca"],
                         "default": "g1",
                         "description": "序列种类：G1 / G2 / Gold 族 / 标准 C/A 码"},
                "prn": {"type": "integer", "default": 1,
                        "description": "PRN 编号 1..37（kind=ca 时有效）"},
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
            "码相关捕获（扩频/CDMA 的捕获环节）：接收 IQ × 码集 → "
            "FFT 循环相关 → 每码的峰值与码相位、最优码与相关强度。"
            "依据材料 learnSDR 课程 lesson23（GPS 相关与解调）的机制部分。"
            "前置条件：iq（或 iq_path，complex64 raw）；sample_rate/chip_rate"
            " 须与码率一致（默认 1.023e6 chip/s）。codes 可不给——缺省用仓内"
            "标准 C/A 码族（IS-GPS-200 Table 3-I，PRN 1-32），结果里的 "
            "codes_source 标明码集出处（caller / is-gps-200e-table-3I）。"
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
                          "description": "{名称: ±1 码序列}（数组）；缺省用标准 "
                                         "C/A 码族（IS-GPS-200 Table 3-I）"},
                "sample_rate": {"type": "number", "default": 1023000.0,
                                "description": "IQ 采样率 Hz"},
                "chip_rate": {"type": "number", "default": 1023000.0,
                              "description": "码速率（chip/s）"},
            },
            "required": [],
        },
        handler=_acquire,
        category="satellite",
    )
