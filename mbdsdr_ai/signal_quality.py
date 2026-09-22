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


if __name__ == "__main__":
    # 自测：加 DC 偏移 + I/Q 不平衡，应被检出
    rng = np.random.default_rng(0)
    x = (0.5 + 0.5j) + 0.1 * rng.standard_normal(10000) + 0.2j * rng.standard_normal(10000)
    q = signal_quality(x)
    print("DC I≈", q["dc_offset_i"], "DC Q≈", q["dc_offset_q"])
    print("I/Q不平衡 dB:", q["iq_imbalance_db"], "| PAPR:", q["papr_db"])
