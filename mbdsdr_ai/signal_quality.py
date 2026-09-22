"""
MBDSDR 信号质量 / 星座统计（纯 numpy）
=====================================
IQ -> DC 偏移、I/Q 不平衡、峰均比、星座散布、RMS。
GNU Radio 的 constellation/sink 诊断能力的 AI 工具版。
"""
from __future__ import annotations
import numpy as np
from typing import Dict


def signal_quality(iq: np.ndarray) -> Dict:
    """对一段复 IQ 做信号质量统计。"""
    x = np.asarray(iq, dtype=np.complex128)
    I = np.real(x)
    Q = np.imag(x)
    rms_i = float(np.sqrt(np.mean(I ** 2)))
    rms_q = float(np.sqrt(np.mean(Q ** 2)))
    mag = np.abs(x)
    rms = float(np.sqrt(np.mean(mag ** 2)))
    peak = float(np.max(mag))
    # 圆形均值：不能直接平均角度（跨 ±π 边界会错）。
    # 用 atan2(mean(sin), mean(cos)) 求合矢量方向。
    ang = np.angle(x)
    mean_phase = float(np.degrees(np.arctan2(
        np.mean(np.sin(ang)), np.mean(np.cos(ang)))))
    return {
        "dc_offset_i": round(float(np.mean(I)), 6),
        "dc_offset_q": round(float(np.mean(Q)), 6),
        "iq_imbalance_db": round(20 * np.log10((rms_i + 1e-9) / (rms_q + 1e-9)), 2),
        "rms": round(rms, 4),
        "peak": round(peak, 4),
        "papr_db": round(20 * np.log10((peak + 1e-9) / (rms + 1e-9)), 2),
        "mean_phase_deg": round(mean_phase, 2),
        "sample_count": int(len(x)),
    }


def squelch_gate(iq: np.ndarray, threshold_db: float = -50.0,
                 block: int = 1024) -> Dict:
    """静噪门控：按块算 RSSI(dBFS)，判断信号是否超过门控并给占空比。

    dBFS 以满量程 1.0 为参考（IQ 归一化后 |x|<=1）。
    """
    x = np.asarray(iq, dtype=np.complex128)
    n = len(x)
    if n < block:
        block = n
    blocks = n // block
    if blocks == 0:
        blocks = 1
        block = n
    rssi_db = []
    for i in range(blocks):
        seg = x[i * block:(i + 1) * block]
        p = float(np.mean(np.abs(seg) ** 2))
        rssi_db.append(10.0 * np.log10(p + 1e-12))
    rssi_db = np.array(rssi_db)
    open_blocks = int(np.sum(rssi_db > threshold_db))
    return {
        "rssi_dbfs": round(float(np.mean(rssi_db)), 2),
        "peak_rssi_dbfs": round(float(np.max(rssi_db)), 2),
        "threshold_db": threshold_db,
        "open": bool(np.mean(rssi_db) > threshold_db),
        "duty_cycle": round(open_blocks / max(blocks, 1), 3),
        "block_count": int(blocks),
    }


if __name__ == "__main__":
    # 自测：加 DC 偏移 + I/Q 不平衡，应被检出
    rng = np.random.default_rng(0)
    x = (0.5 + 0.5j) + 0.1 * rng.standard_normal(10000) + 0.2j * rng.standard_normal(10000)
    q = signal_quality(x)
    print("DC I≈", q["dc_offset_i"], "DC Q≈", q["dc_offset_q"])
    print("I/Q不平衡 dB:", q["iq_imbalance_db"], "| PAPR:", q["papr_db"])
