"""
MBDSDR 星座图 / EVM 统计（纯 numpy）
====================================
IQ 符号 -> 理想星座点聚类 -> EVM（误差向量幅度）。
GNU Radio constellation sink 的 AI 诊断版。
"""
from __future__ import annotations
import numpy as np
from typing import Dict


def evm_qpsk(symbols: np.ndarray) -> Dict:
    """对一段 QPSK 软符号算 EVM。理想点 (±1±j)/sqrt(2)。"""
    x = np.asarray(symbols, dtype=np.complex128)
    # 归一化功率
    p = np.mean(np.abs(x) ** 2)
    xn = x / (np.sqrt(p) + 1e-9)
    # 理想 QPSK 点
    ideal = (np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j])) / np.sqrt(2)
    # 每符号找最近理想点
    dist = np.abs(xn[:, None] - ideal[None, :])
    nearest = np.argmin(dist, axis=1)
    err = xn - ideal[nearest]
    evm_rms = float(np.sqrt(np.mean(np.abs(err) ** 2)))
    # 误判率（理想点归一化后，越限算错）
    hard = np.sign(np.stack([xn.real, xn.imag], axis=1))
    return {
        "evm_rms_pct": round(evm_rms * 100, 2),
        "evm_db": round(20 * np.log10(evm_rms + 1e-9), 1),
        "symbol_count": int(len(x)),
        "cluster_centers": [
            {"re": round(float(np.mean(xn[nearest == k].real)), 3),
             "im": round(float(np.mean(xn[nearest == k].imag)), 3)}
            for k in range(4) if np.any(nearest == k)
        ],
    }


if __name__ == "__main__":
    # 自测：干净 QPSK -> EVM 小；加噪 -> EVM 大
    rng = np.random.default_rng(0)
    bits = rng.integers(0, 2, 4000)
    sym = (1 - 2 * bits[0::2]) / np.sqrt(2) + 1j * (1 - 2 * bits[1::2]) / np.sqrt(2)
    clean = evm_qpsk(sym)
    noisy = evm_qpsk(sym + 0.1 * (rng.standard_normal(2000) + 1j * rng.standard_normal(2000)))
    print("干净 EVM%:", clean["evm_rms_pct"], "| 加噪 EVM%:", noisy["evm_rms_pct"])


def scatter_points(iq: np.ndarray, n_points: int = 1024) -> Dict:
    """把一段复 IQ 转成星座散点坐标（给 UI 画星座图用）。

    去 DC、自动增益归一化到单位方差，均匀重采样到 n_points 个点。
    不做符号判决/频偏校正（那由解调链路负责），只提供可直接画散点的坐标。
    """
    x = np.asarray(iq, dtype=np.complex128)
    if len(x) == 0:
        return {"points": [], "n_points": 0}
    x = x - np.mean(x)
    p = float(np.mean(np.abs(x) ** 2))
    if p > 1e-12:
        x = x / np.sqrt(p)
    if len(x) >= n_points:
        idx = np.linspace(0, len(x) - 1, n_points).astype(int)
        pts = x[idx]
    else:
        pts = x
    return {
        "points": [[float(p.real), float(p.imag)] for p in pts],
        "n_points": int(len(pts)),
    }
