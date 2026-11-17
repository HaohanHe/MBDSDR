# SPDX-License-Identifier: MIT
"""
模式默认接收带宽表（modes defaults）
====================================

对标 openwebrx/owrx/modes.py:118-209 各解调模式的默认中频带宽：
WFM ±75k、NFM ±4k、USB 300-3000、CW 700-900 等。

返回的 low_hz/high_hz 是相对解调中心（dial）的偏移量（Hz），用于配置
信道滤波器通带。未知模式回退到 FM 默认 ±6250Hz，避免无带宽信息时误配。
"""

from __future__ import annotations

import math

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


# 用户带宽与模式默认通带宽度的匹配容差（相对默认宽度的倍数区间）。
# 工程经验：滤波器实际通带与表值不必逐赫兹相同。下限 0.5× 避免过窄
# （宽带信号被削掉边带 / 语音可懂度差），上限 2× 避免过宽（把邻道干扰与
# 多余噪声一起收进来）；落在 [0.5×, 2×] 内视为「与模式一致」。
TOLERANCE_LOW_RATIO = 0.5
TOLERANCE_HIGH_RATIO = 2.0


def check_bandwidth(mode: str, bandwidth_hz: float) -> dict:
    """检查用户设定带宽是否与解调模式的默认通带一致。

    默认通带来自 :func:`get_mode_bandwidth`，以 ``high_hz - low_hz`` 作为
    默认宽度。匹配口径按工程经验取 ``0.5 × 默认宽 ≤ 用户带宽 ≤ 2 × 默认宽``：
    滤波器通带不必与表值逐赫兹相同，但过窄会削掉边带、过宽会收进邻道干扰
    与多余噪声，故只在该区间内判定为一致。

    参数
    ----
    mode :
        解调模式（大小写/首尾空白不敏感）；不在 ``MODE_BANDWIDTH`` 表中即
        视为未知模式。
    bandwidth_hz :
        用户请求的信道带宽（总宽，Hz），必须是有限正数。

    返回
    ----
    dict
        不匹配或输入非法::

            {"ok": False, "reason": "...", "suggested_hz": 默认宽,
             "default_low_hz": ..., "default_high_hz": ...}

        输入非法（未知模式/非数值/非有限/非正）时只保证 ``ok`` 与 ``reason``，
        没有可用默认通带时不会编造建议值。匹配时 ``{"ok": True, ...}``，
        ``reason`` 为空串，建议值字段照常给出，便于调用方统一取值。
    """
    key = str(mode).upper().strip() if mode is not None else ""
    if key not in MODE_BANDWIDTH:
        return {
            "ok": False,
            "reason": f"未知解调模式 {mode!r}，无法校验带宽一致性",
        }
    try:
        bw = float(bandwidth_hz)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "reason": f"带宽必须是数值，收到 {bandwidth_hz!r}",
        }
    if not math.isfinite(bw):
        return {
            "ok": False,
            "reason": f"带宽必须是有限数值，收到 {bandwidth_hz!r}",
        }
    if bw <= 0:
        return {
            "ok": False,
            "reason": f"带宽必须为正数，收到 {bw:g} Hz",
        }

    info = MODE_BANDWIDTH[key]
    default_low = float(info["low_hz"])
    default_high = float(info["high_hz"])
    default_width = default_high - default_low
    suggested = default_width
    low = default_width * TOLERANCE_LOW_RATIO
    high = default_width * TOLERANCE_HIGH_RATIO
    result = {
        "suggested_hz": suggested,
        "default_low_hz": default_low,
        "default_high_hz": default_high,
        "tolerance_low_hz": low,
        "tolerance_high_hz": high,
    }
    if low <= bw <= high:
        return {"ok": True, "reason": "", **result}
    return {
        "ok": False,
        "reason": (
            f"带宽 {bw:g} Hz 与解调模式 {key} 的默认通带 {suggested:g} Hz "
            f"不一致（容差 {low:g}–{high:g} Hz，建议 {suggested:g} Hz）"
        ),
        **result,
    }
