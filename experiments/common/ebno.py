# SPDX-License-Identifier: MIT
"""
Eb/N0 <-> 带内 SNR 换算（论文实验公共口径）
=============================================

推导（与 docs/learn/phase4/audits/B2-experiment-pipeline.md §2.1 一致）：

  噪声功率谱密度 N0；观测/判决带宽 B；信号功率 Ps；比特率 Rb；每比特能量 Eb=Ps/Rb。

      Eb/N0 = (Ps/Rb) / N0 = (Ps / (N0·B)) · (B / Rb) = SNR_meas · (B / Rb)

  dB:  (Eb/N0)_dB = SNR_meas_dB + 10·log10(B / Rb)

其中 SNR_meas 是"注入噪声、且判决器实际看到"的带内信噪比。本管线噪声铺满整个
采样率 fs、判决器不额外带限，故对复基带脚本取 B=fs，对实音频脚本取 B=音频 fs
（保守、含带外噪声）。这是**实算**换算，不是估算；B/Rb 与模式一一写死在
:data:MODE_TABLE 里，并标注证据来源。

红线：
- CW/OOK、AM/FM、SSTV、FHSS、CFO、频谱感知等无比特率/非比特流口径，**不报 Eb/N0**。
- 任何图/CSV/manifest 都必须同时记录 snr_db 与 ebn0_db 两列，禁止只报其一。
"""
from __future__ import annotations

import math
from typing import Dict


# ---------------------------------------------------------------------------
# 换算（纯函数，可被单测精确断言）
# ---------------------------------------------------------------------------
def snr_db_to_ebn0_db(snr_db: float, rb: float, b: float) -> float:
    """带内 SNR(dB) -> Eb/N0(dB)。(Eb/N0)_dB = SNR_dB + 10·log10(B/Rb)。

    参数:
        snr_db : 带内信噪比（dB），噪声铺满带宽 B。
        rb     : 信息比特率（bit/s）。
        b      : 噪声/判决带宽（Hz）。本管线取复基带 fs 或音频 fs。
    """
    if rb <= 0 or b <= 0:
        raise ValueError(f"Rb 与 B 必须为正：rb={rb}, b={b}")
    return float(snr_db) + 10.0 * math.log10(b / rb)


def ebn0_db_to_snr_db(ebn0_db: float, rb: float, b: float) -> float:
    """Eb/N0(dB) -> 带内 SNR(dB)。上式严格逆运算。"""
    if rb <= 0 or b <= 0:
        raise ValueError(f"Rb 与 B 必须为正：rb={rb}, b={b}")
    return float(ebn0_db) - 10.0 * math.log10(b / rb)


def ebn0_offset_db(rb: float, b: float) -> float:
    """Δ = 10·log10(B/Rb)：把带内 SNR 搬到 Eb/N0 需要加的 dB 数。"""
    if rb <= 0 or b <= 0:
        raise ValueError(f"Rb 与 B 必须为正：rb={rb}, b={b}")
    return 10.0 * math.log10(b / rb)


# ---------------------------------------------------------------------------
# 各模式 (Rb, B) 常量表。b_source 给证据 file:line（仓库内真实出处）。
# 不报 Eb/N0 的模式不在此表（见模块 docstring 红线）。
# ---------------------------------------------------------------------------
MODE_TABLE: Dict[str, Dict[str, object]] = {
    "afsk_ax25_1200": {
        "label": "AX.25/AFSK Bell202 1200 baud",
        "rb": 1200.0,
        "b": 44100.0,           # 音频 fs（44.1k，保守含带外噪声）
        "b_source": "mbdsdr_ai/ax25.py:45 AFSK_BAUD_RATE=1200；exp_ax25_performance.py:31 fs=44100",
        "reports_ebn0": True,
    },
    "adsb_modes_1m": {
        "label": "ADS-B Mode-S 1 Mbps",
        "rb": 1_000_000.0,
        "b": 4_000_000.0,       # fs=4e6，112bit/112us
        "b_source": "mbdsdr_ai/adsb.py:144 区；exp_digital_modes.py:145 fs=4e6",
        "reports_ebn0": True,
    },
    "bpsk_10k": {
        "label": "BPSK 10 kbps (fs=100k, sps=10)",
        "rb": 10_000.0,
        "b": 100_000.0,         # 复基带 fs，噪声铺满
        "b_source": "experiments/exp_ebno_decode.py（自包含相干 BPSK 链）",
        "reports_ebn0": True,
    },
}


def mode_params(mode: str) -> Dict[str, object]:
    """查表取某模式的 (rb, b, label)；未知模式抛错（绝不猜一个 B/Rb）。"""
    if mode not in MODE_TABLE:
        raise KeyError(
            f"未知/不报 Eb/N0 的模式 '{mode}'。可选：{sorted(MODE_TABLE)}；"
            f"CW/SSTV/FHSS/CFO/频谱感知/AM/FM 无比特率口径，不报 Eb/N0。"
        )
    return MODE_TABLE[mode]


def offset_for_mode(mode: str) -> float:
    """该模式的 Δ=10·log10(B/Rb)（dB）。"""
    p = mode_params(mode)
    return ebn0_offset_db(float(p["rb"]), float(p["b"]))
