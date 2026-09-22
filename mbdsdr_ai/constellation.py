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
