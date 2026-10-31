# SPDX-License-Identifier: MIT
"""P1-6 CMA 盲均衡原子工具（Godard 恒模算法，复数符号级，块平均更新）。

能力拆解（218 条）里 P1-6 的原子能力（ASTU 标"可选级"）：
  cma_equalize(symbols, mu, n_taps, constellation) —— 多径/带限信道导致
  的码间串扰（ISI）在不知道信道、不知道训练序列的条件下盲恢复星座。

算法（教科书标准复数 CMA，独立实现）：
  代价  J = E[(|y_k|² − R2)²]，R2 = E[|s|⁴]/E[|s|²]（星座常数，
        PSK 类 R2=1，16QAM(归一) R2=1.32，64QAM(归一) R2=1.62）；
  梯度  ∂J/∂w* = 2(|y|²−R2)·y·x*（Wirtinger 微分，y = w^H x）；
  更新  块平均 N-CMA：每 block 个样本累积梯度后做一次
        w ← w − (μ/blk)·Σ y(|y|²−R2)/‖x‖²·x*。
        块平均把 |y|³ 型误差的增长压在稳定域内（逐个样本更新在
        μ≥0.01 即发散，块平均后 μ 可到 0.05~0.1，收敛显著加快）。

诚实纪律（AGENTS 第 2 条）与已知局限（如实写明）：
  * QAM 的 CMA 代价地板：完美均衡 16QAM 的输出代价 =
    E[(|s|²−1.32)²] ≈ 0.42 ≠ 0，评估改善量必须对照该地板；
  * 符号间隔 CMA 存在局部极小/收敛慢的公开特性（DD 判决引导精调、
    分数间隔均衡不在本工具范围，留后续批次）；
  * 数值溢出（inf/nan）→ diverged=True，equalized 空表如实上报；
  * 收敛判据：cost_final < cost0·(1−min_improvement)，未达即
    converged=False，不产出伪"已均衡"结论；
  * EVM 按已知星座最近栅格计算（evm_before_db/evm_after_db），
    未知星座/显式 R2 数值时 EVM 为 None。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

import numpy as np

__all__ = ["cma_equalize", "cma_cost", "register_tool_registry"]

#: 星座常数 R2 = E|s|⁴ / E|s|²（归一化平均功率 E|s|²=1 的约定下）
_CONSTELLATION_R2 = {
    "bpsk": 1.0,
    "qpsk": 1.0,
    "8psk": 1.0,
    "16qam": 1.32,
    "64qam": 1.62,
}

#: 已知星座栅格（EVM 计算用，归一化平均功率 1）
_GRIDS: Dict[str, np.ndarray] = {}


def _grid(constellation: str) -> Optional[np.ndarray]:
    key = constellation.lower()
    if key in _GRIDS:
        return _GRIDS[key]
    g: Optional[np.ndarray] = None
    if key == "bpsk":
        g = np.array([-1.0, 1.0])
    elif key == "qpsk":
        g = np.array([a + 1j * b for a in (-1, 1) for b in (-1, 1)]) / math.sqrt(2)
    elif key == "8psk":
        g = np.exp(1j * (2 * math.pi * np.arange(8) / 8 + math.pi / 8))
    elif key == "16qam":
        g = np.array([a + 1j * b for a in (-3, -1, 1, 3)
                      for b in (-3, -1, 1, 3)]) / math.sqrt(10)
    elif key == "64qam":
        lev = np.array([-7, -5, -3, -1, 1, 3, 5, 7])
        g = np.array([a + 1j * b for a in lev for b in lev]) / math.sqrt(42)
    if g is not None:
        _GRIDS[key] = g
    return g


def _evm_db(x: np.ndarray, constellation: str) -> Optional[float]:
    g = _grid(constellation)
    if g is None or x.size < 128:
        return None
    edge = max(8, x.size // 10)
    body = x[edge: x.size - edge]
    p = float(np.mean(np.abs(body) ** 2)) + 1e-30
    d2 = np.abs(body[:, None] - g[None, :]) ** 2
    err = float(np.mean(np.min(d2, axis=1)))
    return round(10.0 * math.log10(err / p), 3)


def cma_cost(symbols: np.ndarray, r2: float) -> float:
    """CMA 代价 J = mean((|y|² − R2)²)。"""
    magsq = np.abs(symbols) ** 2
    return float(np.mean((magsq - r2) ** 2))


def cma_equalize(symbols: Sequence[complex],
                 constellation: str = "16qam",
                 mu: float = 0.02,
                 n_taps: int = 21,
                 block: int = 16,
                 max_samples: int = 40000,
                 min_improvement: float = 0.5,
                 ) -> Dict[str, Any]:
    """复数 CMA 盲均衡（符号间隔，中心抽头初始化，块平均更新）。

    symbols        : 接收复符号序列（已定时恢复，含 ISI）；
    constellation  : 目标星座（决定 R2 与 EVM 栅格）：bpsk/qpsk/8psk/
                     16qam/64qam，或显式 R2 数值字符串（如 "1.32"）；
    mu             : 块平均归一化步长（0.01~0.05 常用区间，≥0.1 有发散风险）；
    n_taps         : 均衡器抽头数（奇数，覆盖 ISI 时延扩展）；
    block          : 梯度累积块长（块平均稳定化）；
    max_samples    : 最多使用的样本数（从头截取）；
    min_improvement: 相对初代价必须下降的比例（0.5 = 至少降 50%）。

    返回：equalized / cost_initial / cost_final / evm_before_db /
    evm_after_db / samples_used / converged / diverged / r2 /
    constellation / n_taps / mu / block。
    """
    x = np.asarray([complex(v) for v in symbols], dtype=complex)
    if x.size < 64:
        raise ValueError("符号序列太短（64 点下限），CMA 统计不稳定")
    if mu <= 0.0:
        raise ValueError("mu 必须为正")
    if n_taps < 3 or n_taps % 2 == 0:
        raise ValueError("n_taps 必须为 ≥3 的奇数")
    if block < 1 or max_samples < 1:
        raise ValueError("block/max_samples 必须为正整数")

    key = constellation.lower()
    if key in _CONSTELLATION_R2:
        r2 = _CONSTELLATION_R2[key]
    else:
        try:
            r2 = float(key)
        except ValueError:
            raise ValueError(
                f"未知星座 {constellation!r}：可用 "
                f"{sorted(_CONSTELLATION_R2)} 或显式 R2 数值")
        if r2 <= 0.0:
            raise ValueError("R2 必须为正")

    x = x[:max_samples]
    n = x.size

    # 中心抽头初始化（避免全零驻点）。约定 y = w^H x（vdot 约定），
    # 整段输出 y_full = convolve(x, conj(w))，全文件一致。
    w = np.zeros(n_taps, dtype=complex)
    w[n_taps // 2] = 1.0

    y0 = np.convolve(x, np.conj(w))[:n]
    cost0 = cma_cost(y0, r2)
    evm0 = _evm_db(y0, key)

    diverged = False
    used = 0
    for k0 in range(0, n - n_taps, block):
        grad = np.zeros(n_taps, dtype=complex)
        kend = min(k0 + block, n - n_taps)
        for k in range(k0, kend):
            seg = x[k: k + n_taps]
            yk = np.vdot(w, seg[::-1])          # y = w^H x
            pwr = float(np.vdot(seg, seg).real) + 1e-12
            grad += ((abs(yk) ** 2 - r2) * yk / pwr) * np.conj(seg[::-1])
        w = w - (mu / block) * grad
        used = kend
        if not np.isfinite(float(np.abs(w).max())):
            diverged = True
            break

    if diverged:
        return {
            "equalized": [], "cost_initial": round(cost0, 6),
            "cost_final": None, "evm_before_db": evm0,
            "evm_after_db": None, "samples_used": int(used),
            "converged": False, "diverged": True,
            "r2": r2, "constellation": key, "n_taps": n_taps,
            "mu": mu, "block": block,
        }

    y = np.convolve(x, np.conj(w))[:n]
    cost_final = cma_cost(y, r2)
    converged = cost_final < cost0 * (1.0 - min_improvement)
    return {
        "equalized": y.tolist(),
        "cost_initial": round(cost0, 6),
        "cost_final": round(cost_final, 6),
        "evm_before_db": evm0,
        "evm_after_db": _evm_db(y, key),
        "samples_used": int(used),
        "converged": bool(converged),
        "diverged": False,
        "r2": r2,
        "constellation": key,
        "n_taps": n_taps,
        "mu": mu,
        "block": block,
    }


def register_tool_registry(registry) -> None:
    """注册 P1-6 CMA 盲均衡积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _cma(args):
        try:
            syms = args.get("symbols")
            if not syms:
                raise ValueError("symbols 必须提供（复符号序列）")
            parsed = [complex(v["re"], v["im"]) if isinstance(v, dict)
                      else complex(v) for v in syms]
            res = cma_equalize(
                parsed,
                constellation=str(args.get("constellation", "16qam")),
                mu=float(args.get("mu", 0.02)),
                n_taps=int(args.get("n_taps", 21)),
                block=int(args.get("block", 16)),
                max_samples=int(args.get("max_samples", 40000)),
                min_improvement=float(args.get("min_improvement", 0.5)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"CMA 均衡失败: {clean_exc(e)}")
        ok = bool(res.get("converged"))
        return ToolResult(
            success=ok,
            content=_json.dumps({k: v for k, v in res.items()
                                 if k != "equalized"},
                                ensure_ascii=False, default=str),
            data=res)

    registry.register(
        name="cma_equalize",
        description=(
            "CMA 恒模盲均衡：多径 ISI 信道下无训练序列恢复星座"
            "（Godard，块平均 N-CMA）。QAM 代价地板非零（16QAM≈0.42），"
            "改善量对照地板评估；改善不足或数值溢出时 converged/diverged "
            "如实上报，不产出伪均衡结论。"),
        parameters={
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": ["object", "string", "number"]},
                            "description": "接收复符号序列（≥64 点）"},
                "constellation": {"type": "string", "default": "16qam"},
                "mu": {"type": "number", "default": 0.02},
                "n_taps": {"type": "integer", "default": 21},
                "block": {"type": "integer", "default": 16},
                "max_samples": {"type": "integer", "default": 40000},
                "min_improvement": {"type": "number", "default": 0.5},
            },
            "required": ["symbols"],
        },
        handler=_cma,
        category="signal_analysis")
