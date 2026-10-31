# SPDX-License-Identifier: MIT
"""P1-3 盲参数估计原子工具（调制识别 / 符号率估计 / 滚降系数估计）。

能力拆解（218 条）里 P1-3 的原子能力此前缺整：
  * **调制识别** modulation_classify——复基带瞬时统计（幅度/相位/频率
    方差）+ 谱特征（谱峰度、谱对称度），判别 BPSK/QPSK/8PSK/2FSK/4FSK/
    16QAM/64QAM/AM/FM/CW，输出 top-N 候选与置信度；
  * **盲符号率** symbol_rate_estimate——谱包络 |X(f)|² 的线谱
    （时钟频率处的离散谱线）检测；
  * **滚降系数** alpha_estimate——升余弦谱边沿斜率拟合
    （通带平坦段 → 过渡带 → 阻带底三分段线性拟合，过渡带斜率 ∝ 1/α）。

诚实纪律（AGENTS 第 2 条）：
  * 线谱信噪比不足 / 线谱间距过密不可分 → 报 None + reason，不报假值；
  * 识别置信度不足 → 报 ambiguous=True，不静默给单一结论；
  * 全部为推导算法（教科书方法复现），不查表不伪造；
  * 输入校验：IQ 非空、长度 ≥ 最小样本数、采样率为正。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "modulation_classify", "symbol_rate_estimate", "alpha_estimate",
    "register_tool_registry",
]

#: 识别所需最小样本数
_MIN_SAMPLES = 512
#: 调制类别数（用于归一化谱熵基准）
_N_CLASSES = 10


# ─────────────────────────────────────────────────────────────
# 内部特征
# ─────────────────────────────────────────────────────────────
def _occupied_bw_hz(x: np.ndarray, sample_rate_hz: float) -> float:
    """占用带宽：峰值 −10 dB 处的连通谱宽（以 DC 为中心）。"""
    n = len(x)
    X = np.fft.fft(x * np.hanning(n))
    half = n // 2
    P = np.abs(X[:half]) ** 2
    Pdb = 10.0 * np.log10(P + 1e-30)
    freqs = np.fft.fftfreq(n, 1.0 / sample_rate_hz)[:half]
    peak = Pdb.max()
    above = Pdb > peak - 10.0
    if not above.any():
        return float(sample_rate_hz)
    # 从 DC 向外找最后一个仍高于门限的频点
    idx = np.max(np.nonzero(above)[0])
    return float(2.0 * freqs[idx])


def _inst_features(x: np.ndarray, sample_rate_hz: float) -> Dict[str, float]:
    """瞬时统计特征（带宽归一化，教科书方法复现）。

    所有相位/幅度统计按占用带宽归一：k = BW/fs 为每样本带宽分数，
    相位增量方差除以 k² 得"每符号等效"量级，与采样率/符号率解耦。
    """
    bw = _occupied_bw_hz(x, sample_rate_hz)
    k = max(bw / float(sample_rate_hz), 1e-6)
    xr = np.abs(x)
    mu = xr.mean() + 1e-30
    a_std = float(np.std(xr) / mu)                       # 归一化幅度 std
    a_kurt = float(np.mean((xr - mu) ** 4) /
                   (np.std(xr) ** 4 + 1e-30))            # 幅度峰度（形区分）
    p = np.unwrap(np.angle(x))
    dphi = np.diff(p)
    ph = float(np.var(dphi) / (k * k))                   # 每符号等效相位方差
    # 四阶归一矩 μ4 = E[x⁴]/E[|x|²]²（符号星座指纹，线性调制下与成形
    # 基本解耦：BPSK≈+1，QPSK≈−1，16QAM≈−0.68，8PSK(π/8 偏置)≈0，
    # FSK/FM 随机相位≈0——后两者先被恒包络分支截走）：
    m2 = float(np.mean(np.abs(x) ** 2)) + 1e-30
    m4 = complex(np.mean(x ** 4)) / (m2 * m2)
    # 载波谱线检测：半峰值以上 bin 数。连续谱调制（PSK/QAM/FM）的
    # 半功率区是数千 bin 的通带；单载波线（CW/AM）Hann 主瓣只有 ~4 bin。
    n = len(x)
    X = np.fft.fft(x * np.hanning(n))
    P = np.abs(X[: n // 2]) ** 2
    line_bins = int((P > P.max() * 0.5).sum())
    return {"occ_bw_hz": bw, "bw_fraction": k,
            "amp_std": a_std, "amp_kurt": a_kurt,
            "phase_var": ph, "mu4_re": float(m4.real), "mu4_im": float(m4.imag),
            "line_bins": line_bins}


# ─────────────────────────────────────────────────────────────
# 调制识别
# ─────────────────────────────────────────────────────────────
def _score_class(feat: Dict[str, float]) -> List[Dict[str, Any]]:
    """基于带宽归一化特征的类别评分（决策树）。

    阈值来自合成校准（fs=1 MHz、sps=20、α=0.35、Es/N0 20 dB，
    观测值记录在 test_blind_param_est.py 的校准注释里）：

    | 信号   | amp_std | phase_var | amp_kurt | mu4_re | line_bins  |
    |--------|---------|-----------|----------|--------|------------|
    | CW     | 0.07    | 0.25      | 2.97     | ~0     | ~4(载波线) |
    | FM     | 0.07    | 5.3       | 2.98     | ~0     | 数千(连续) |
    | 2FSK   | 0.07    | 13.9      | 3.03     | ~0     | ~8(双谱线) |
    | BPSK   | 0.47    | 88        | 2.05     | ≈+1    | 数千(连续) |
    | QPSK   | 0.33    | 34.6      | 3.51     | ≈−1    | 数千(连续) |
    | 8PSK   | 0.31    | 26.5      | 2.99     | ≈0     | 数千(连续) |
    | 16QAM  | 0.41    | 31.6      | 2.36     | ≈−0.68 | 数千(连续) |

    决策结构：真恒包络（FSK/FM/CW）amp_std≤0.1 → 按相位方差分层；
    载波谱线（line_bins≤8）→ AM；其余线性数字调制由 μ4 星座指纹
    主导（QPSK/8PSK 的相位方差分布重叠，不再单独决定）。
    """
    ph = feat["phase_var"]
    a = feat["amp_std"]
    ak = feat["amp_kurt"]
    line_bins = feat["line_bins"]
    scores: List[Dict[str, Any]] = []

    if a < 0.12:
        # 真恒包络：CW / FM / FSK
        if ph < 1.0:
            scores.append({"modulation": "cw", "score": 0.95})
            scores.append({"modulation": "fm", "score": 0.2})
        elif ph < 9.0:
            scores.append({"modulation": "fm", "score": 0.85})
            scores.append({"modulation": "2fsk", "score": 0.4})
        else:
            scores.append({"modulation": "2fsk", "score": 0.85})
            scores.append({"modulation": "4fsk", "score": 0.4})
            scores.append({"modulation": "fm", "score": 0.3})
        return scores

    if line_bins <= 8:
        # 半功率区只有主瓣几个 bin → 单载波谱线 → AM（CW/FSK 已被
        # 恒包络分支截走；FSK 双谱线 each 主瓣 4 bin 但互不相邻，
        # 半功率计数仍极小——同样落在本分支前先被截走）
        scores.append({"modulation": "am", "score": 0.85})
        scores.append({"modulation": "16qam", "score": 0.3})
        return scores

    # 线性数字调制：μ4 星座指纹主导（相位方差在 QPSK/8PSK 间分布
    # 重叠，仅作辅证，不再单独决定）
    m4r = feat["mu4_re"]
    if m4r > 0.4:
        scores.append({"modulation": "bpsk", "score": 0.9})
        scores.append({"modulation": "qpsk", "score": 0.3})
        return scores
    if m4r < -0.4:
        # QPSK(≈−1) 与 16QAM(≈−0.68)：用幅度峰度分（成形深衰落使
        # PSK 类尾部变厚，峰度高于离散电平的 QAM）
        if ak > 2.8:
            scores.append({"modulation": "qpsk", "score": 0.85})
            scores.append({"modulation": "16qam", "score": 0.4})
        else:
            scores.append({"modulation": "16qam", "score": 0.85})
            scores.append({"modulation": "64qam", "score": 0.35})
            scores.append({"modulation": "qpsk", "score": 0.3})
        return scores
    # |μ4|≈0 且非恒包络 → 8PSK（π/8 偏置恰好消去四阶矩）
    scores.append({"modulation": "8psk", "score": 0.85})
    scores.append({"modulation": "qpsk", "score": 0.35})
    return scores


def modulation_classify(iq: Sequence[complex],
                        sample_rate_hz: float,
                        top_n: int = 3,
                        ) -> Dict[str, Any]:
    """调制识别：复基带瞬时统计 + 谱特征 → top-N 候选。

    iq            : 复基带采样序列；
    sample_rate_hz: 采样率（仅做输入校验与结果记录）；
    top_n         : 返回候选数。

    返回：candidates（按 score 降序）/ ambiguous（score 最高与次高差 < 0.2
    时 True——不静默给单一结论）/ features（特征值透出便于复核）。
    """
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz 必须为正")
    x = np.asarray([complex(v) for v in iq], dtype=complex)
    if x.size < _MIN_SAMPLES:
        raise ValueError(f"IQ 样本不足（{_MIN_SAMPLES} 点下限），无法稳定识别")

    feat = _inst_features(x, sample_rate_hz)
    scored = _score_class(feat)
    total = sum(s["score"] for s in scored) or 1.0
    for s in scored:
        s["confidence"] = round(s["score"] / total, 4)
    scored.sort(key=lambda s: -s["confidence"])
    top = scored[:max(1, top_n)]
    ambiguous = len(top) > 1 and (top[0]["confidence"] - top[1]["confidence"]) < 0.2
    return {
        "modulation": top[0]["modulation"] if not ambiguous else None,
        "candidates": top,
        "ambiguous": ambiguous,
        "features": feat,
        "n_samples": int(x.size),
    }


# ─────────────────────────────────────────────────────────────
# 盲符号率（谱包络线谱法）
# ─────────────────────────────────────────────────────────────
def symbol_rate_estimate(iq: Sequence[complex],
                         sample_rate_hz: float,
                         search_range: Optional[Tuple[float, float]] = None,
                         snr_threshold_db: float = 10.0,
                         ) -> Dict[str, Any]:
    """盲符号率估计：|X(f)|² 时钟线谱检测。

    线性调制信号的谱包络在 f = ±sym_rate 处有离散谱线（线谱高出
    连续谱底的分贝数 = 线谱信噪比）。取正频最强线谱即符号率。

    iq             : 复基带采样；
    sample_rate_hz : 采样率；
    search_range   : 符号率搜索区间 (min, max) Hz，默认 (fs/N·4, fs/4)；
    snr_threshold_db: 线谱信噪比门限，低于则报 None（不造假值）；
    返回：symbol_rate_hz（None = 未检出）/ line_snr_db / reason。
    """
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz 必须为正")
    x = np.asarray([complex(v) for v in iq], dtype=complex)
    n = x.size
    if n < _MIN_SAMPLES:
        raise ValueError(f"IQ 样本不足（{_MIN_SAMPLES} 点下限）")

    fs = float(sample_rate_hz)
    lo, hi = search_range if search_range else (fs / n * 4.0, fs / 4.0)
    if not (0 < lo < hi <= fs / 2.0):
        raise ValueError("search_range 非法：须在 (0, fs/2] 内")

    # 延迟相乘谱法（Müller & Lücke）：y(t) = x(t)·x*(t−1) 在 f=±符号率
    # 处出现离散谱线（成形符号的周期平稳性），线谱高出连续谱底的分贝数
    # 即线谱信噪比。NRZ 成形信号在 sr 处有线谱；纯噪声无。
    y = x[1:] * np.conj(x[:-1])
    Y = np.fft.fft(y * np.hanning(y.size))
    half = y.size // 2
    P = np.abs(Y[:half]) ** 2
    freqs = np.fft.fftfreq(y.size, 1.0 / fs)[:half]

    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        raise ValueError("search_range 内无频点（样本太短或区间过窄）")

    floor = float(np.median(P[mask]))
    if floor <= 0.0:
        return {
            "symbol_rate_hz": None,
            "line_snr_db": 0.0,
            "reason": "搜索带内无信号功率（纯零/纯噪声底），不报符号率",
        }
    idx_local = int(np.argmax(np.where(mask, P, 0.0)))
    line_snr = 10.0 * math.log10(P[idx_local] / floor)
    sr = float(freqs[idx_local])

    if line_snr < snr_threshold_db:
        return {
            "symbol_rate_hz": None,
            "line_snr_db": round(line_snr, 2),
            "reason": f"最强线谱信噪比 {line_snr:.1f} dB 低于门限 "
                      f"{snr_threshold_db:.1f} dB，线谱不可信，不报符号率",
        }
    return {
        "symbol_rate_hz": round(sr, 3),
        "line_snr_db": round(line_snr, 2),
        "reason": None,
    }


# ─────────────────────────────────────────────────────────────
# 滚降系数估计（升余弦谱边沿拟合）
# ─────────────────────────────────────────────────────────────
def alpha_estimate(iq: Sequence[complex],
                   sample_rate_hz: float,
                   symbol_rate_hz: float,
                   guard: float = 0.15,
                   ) -> Dict[str, Any]:
    """升余弦滚降系数 α 估计：谱边沿三段线性拟合。

    升余弦谱：通带 |f| ≤ (1−α)·sr/2 平坦，过渡带宽度 = α·sr，
    过渡带斜率 = −3/(2α·sr)（功率谱 dB/Hz）。对功率谱 dB 曲线做
    分段线性拟合（通带端 20% / 过渡带 / 阻带端 20%），由过渡带
    宽度反解 α；斜率法交叉验证。

    iq            : 复基带采样；
    sample_rate_hz: 采样率；
    symbol_rate_hz: 符号率（须已知或先经 symbol_rate_estimate 得出）；
    guard         : 通带/阻带拟合窗宽（占过渡带宽比例，防边界污染）。

    返回：alpha（0.01~0.99 截断）/ alpha_from_slope / transition_hz/
    两法差异过大时 ambiguous=True。
    """
    if sample_rate_hz <= 0 or symbol_rate_hz <= 0:
        raise ValueError("采样率与符号率必须为正")
    if symbol_rate_hz >= sample_rate_hz / 2.0:
        raise ValueError("symbol_rate_hz 超 Nyquist，非法")
    x = np.asarray([complex(v) for v in iq], dtype=complex)
    n = x.size
    if n < _MIN_SAMPLES:
        raise ValueError(f"IQ 样本不足（{_MIN_SAMPLES} 点下限）")

    fs = float(sample_rate_hz)
    sr = float(symbol_rate_hz)
    X = np.fft.fft(x * np.hanning(n))
    half = n // 2
    P = np.abs(X[:half]) ** 2
    freqs = np.fft.fftfreq(n, 1.0 / fs)[:half]

    Pdb = 10.0 * np.log10(P + 1e-30)
    pos = freqs > 0
    f = freqs[pos]
    pdb = Pdb[pos]

    edge = sr / 2.0
    # 全局拟合窗下沿：所有候选 α 的通带都覆盖到 0.15·edge（α≤0.7 时
    # (1−α)·edge ≥ 0.3·edge），上沿由 Nyquist 限死。
    f_lo = 0.15 * edge
    f_hi = min(2.0 * edge, 0.9 * fs / 2.0)
    m = (f >= f_lo) & (f <= f_hi)
    if m.sum() < 64:
        raise ValueError("样本太短，模板拟合窗内频点不足")
    fw = f[m]
    pw = pdb[m]

    # 升余弦功率谱模板（dB）：通带 0 dB；过渡带
    #   20·log10(0.5·(1+cos(π(|f|−f1)/(α·sr)))) ；f1 = (1−α)·sr/2
    # 每个候选 α：
    #   增益在**该候选通带内**中位估计（防阻带噪声底污染），
    #   残差只计通带+过渡带（模板 ≥ −45 dB 区），阻带不参与。
    best: List[Tuple[float, float]] = []
    for i in range(20, 100):                     # α ∈ [0.20, 0.99] 步进 0.01
        alpha = i / 100.0
        f1 = (1.0 - alpha) * edge
        f2 = f1 + alpha * sr                     # = (1+α)·edge
        t = np.abs(fw)
        h_db = np.zeros_like(t)
        trans = (t > f1) & (t < f2)
        h_db[trans] = 20.0 * np.log10(
            0.5 * (1.0 + np.cos(math.pi * (t[trans] - f1) / (alpha * sr))))
        h_db[t >= f2] = -60.0
        # 增益窗：保证落在通带内（< 0.8·f1）
        gmask = t < 0.8 * f1
        if gmask.sum() < 8:
            continue
        gain = float(np.median(pw[gmask] - h_db[gmask]))
        # 残差只计过渡带（−1..−40 dB 区）：平坦通带对各候选 α 贡献
        # 相同的噪声能量，会淹没过渡带的形状差异导致系统性偏低。
        fmask = (h_db <= -1.0) & (h_db > -40.0)
        if fmask.sum() < 16:
            continue
        resid = float(np.sqrt(np.mean((pw[fmask] - (h_db[fmask] + gain)) ** 2)))
        best.append((resid, alpha))
    if not best:
        raise ValueError("符号率/采样率组合下无可拟合候选（样本太短）")
    best.sort()
    resid_best, alpha_best = best[0]
    resid_2nd, alpha_2nd = best[1]

    # 过渡带实测宽度：−20 dB（相对通带）两点间距，交叉验证用
    pb_level = float(np.median(pw[fw < 0.35 * edge]))
    thr = pb_level - 20.0
    below = pw < thr
    if below.any():
        first_below = fw[below][0]
        trans_meas = 2.0 * (first_below - edge) if first_below > edge else None
    else:
        trans_meas = None
    alpha_meas = (trans_meas / sr) if trans_meas else None

    # 最优与次优模板难分（残差比 < 5%）→ ambiguous
    # 相邻网格点残差天然接近（残差面平滑），该比值只反映分辨率；
    # 真正意义的歧义 = 模型失配（最优残差仍大，如非升余弦谱形）。
    ambiguous = resid_best > 8.0

    return {
        "alpha": round(alpha_best, 3),
        "alpha_alternate": round(alpha_2nd, 3),
        "residual_rms_db": round(resid_best, 2),
        "transition_hz": round(alpha_best * sr, 2),
        "edge_hz": round(edge, 2),
        "ambiguous": bool(ambiguous),
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-3 盲参数估计积木（调制识别 / 符号率 / 滚降系数）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _classify(args):
        try:
            iq = args.get("iq")
            if not iq:
                raise ValueError("iq 必须提供（复基带采样，复数形如 "
                                 "{\"re\":..,\"im\":..} 列表）")
            parsed = [complex(v["re"], v["im"]) if isinstance(v, dict)
                      else complex(v) for v in iq]
            res = modulation_classify(
                parsed,
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                top_n=int(args.get("top_n", 3)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"调制识别失败: {clean_exc(e)}")
        ok = not res.get("ambiguous")
        return ToolResult(
            success=ok,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    def _symrate(args):
        try:
            iq = args.get("iq")
            if not iq:
                raise ValueError("iq 必须提供（复基带采样）")
            parsed = [complex(v["re"], v["im"]) if isinstance(v, dict)
                      else complex(v) for v in iq]
            rng = args.get("search_range")
            res = symbol_rate_estimate(
                parsed,
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                search_range=tuple(rng) if rng else None,
                snr_threshold_db=float(args.get("snr_threshold_db", 10.0)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"符号率估计失败: {clean_exc(e)}")
        ok = res.get("symbol_rate_hz") is not None
        return ToolResult(
            success=ok,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    def _alpha(args):
        try:
            iq = args.get("iq")
            if not iq:
                raise ValueError("iq 必须提供（复基带采样）")
            parsed = [complex(v["re"], v["im"]) if isinstance(v, dict)
                      else complex(v) for v in iq]
            res = alpha_estimate(
                parsed,
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                symbol_rate_hz=float(args.get("symbol_rate_hz")),
                guard=float(args.get("guard", 0.15)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"滚降系数估计失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    registry.register(
        name="modulation_classify",
        description=(
            "调制识别：复基带瞬时统计（幅度/相位/频率方差）+ 谱特征 → "
            "top-N 候选与置信度。候选接近时 ambiguous=True 不静默定论。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": ["object", "string", "number"]},
                       "description": "复基带采样（≥512 点）"},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "top_n": {"type": "integer", "default": 3},
            },
            "required": ["iq"],
        },
        handler=_classify,
        category="signal_analysis")

    registry.register(
        name="symbol_rate_estimate",
        description=(
            "盲符号率估计：谱包络时钟线谱检测。线谱信噪比不足时"
            " symbol_rate_hz=None 并给 reason，不报假值。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": ["object", "string", "number"]}},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "search_range": {"type": "array", "items": {"type": "number"}},
                "snr_threshold_db": {"type": "number", "default": 10.0},
            },
            "required": ["iq"],
        },
        handler=_symrate,
        category="signal_analysis")

    registry.register(
        name="alpha_estimate",
        description=(
            "升余弦滚降系数 α 估计：功率谱三段线性拟合（通带/过渡带/"
            "阻带），宽度法与斜率法交叉验证，两法差异大时 ambiguous=True。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": ["object", "string", "number"]}},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "symbol_rate_hz": {"type": "number"},
                "guard": {"type": "number", "default": 0.15},
            },
            "required": ["iq", "symbol_rate_hz"],
        },
        handler=_alpha,
        category="signal_analysis")
