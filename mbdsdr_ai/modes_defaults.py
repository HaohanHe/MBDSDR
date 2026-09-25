"""
模式默认接收带宽表（modes defaults）
====================================

对标 openwebrx/owrx/modes.py:118-209 各解调模式的默认中频带宽：
WFM ±75k、NFM ±4k、USB 300-3000、CW 700-900 等。

返回的 low_hz/high_hz 是相对解调中心（dial）的偏移量（Hz），用于配置
信道滤波器通带。未知模式回退到 FM 默认 ±6250Hz，避免无带宽信息时误配。
"""

from __future__ import annotations

# 各模式默认通带（相对中心频率的偏移，Hz）
MODE_BANDWIDTH = {
    "WFM": {"low_hz": -75000, "high_hz": 75000,
            "description": "宽带调频广播"},
    "NFM": {"low_hz": -4000, "high_hz": 4000,
            "description": "窄带调频"},
    "FM": {"low_hz": -6250, "high_hz": 6250,
           "description": "调频（默认）"},
    "AM": {"low_hz": -3000, "high_hz": 3000,
           "description": "调幅"},
    "USB": {"low_hz": 300, "high_hz": 3000,
            "description": "上边带"},
    "LSB": {"low_hz": -3000, "high_hz": -300,
            "description": "下边带"},
    "CW": {"low_hz": 700, "high_hz": 900,
           "description": "等幅报"},
    "FT8": {"low_hz": -50, "high_hz": 50,
            "description": "FT8 数字模式"},
}

# 未知模式兜底（FM 标准 2.5k 信道间隔的一半）
_FALLBACK = {"low_hz": -6250, "high_hz": 6250}


def get_mode_bandwidth(mode: str) -> dict:
    """返回模式默认通带 {"low_hz","high_hz","description"(可选)}。

    未知模式回退到 {"low_hz": -6250, "high_hz": 6250}，不带 description。
    """
    key = str(mode).upper().strip()
    if key in MODE_BANDWIDTH:
        return dict(MODE_BANDWIDTH[key])
    return dict(_FALLBACK)
