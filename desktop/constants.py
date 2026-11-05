# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端共享常量
======================
集中存放 desktop 各面板共用的默认值，避免到处散落裸字面量。

采样率常量优先复用内核 mbdsdr_ai.sdr_backend 里的项目统一首选值
``DEFAULT_SAMPLE_RATE_HZ``（单一事实来源，当前 2.4 MS/s；实际生效值由设备
能力回落决定，以状态回报为准）。万一 mbdsdr_ai 缺失（例如桌面端单独打包、
未装内核依赖），退回字面量 2_400_000 保证 desktop 仍可导入运行——
这是安全回退值，与内核基准一致，不引入新的"假数字"。
"""

import math

try:
    from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ  # noqa: F401
except Exception:  # noqa: BLE001 - mbdsdr_ai 不可用时的安全回退
    # 与 mbdsdr_ai.sdr_backend 模块级 DEFAULT_SAMPLE_RATE_HZ 保持一致。
    DEFAULT_SAMPLE_RATE_HZ = 2_400_000

# 项目级可调频率范围（Hz）：10 kHz ~ 6 GHz（SDR 全频段）。
# 原先只在控制面板定义，模块面板又写 1 kHz~7.2 GHz（后者是 SoapySDR
# 无范围信息时的兜底值，超过所有在列后端能力），两个面板对
# "能调多少"说法不一。收口到此，各面板共用；实际可调范围仍以设备为准。
FREQ_MIN_HZ = 10_000.0
FREQ_MAX_HZ = 6.0e9


def format_sample_rate(hz) -> str:
    """采样率唯一显示口径（U-03）：一律 ``MS/s``、固定 3 位小数、空格分隔。

    采样率不是频率，不得写成 MHz/kHz；``<1 MS/s`` 也写 ``0.250 MS/s``。
    数值来源必须是设备实际生效值或 ``DEFAULT_SAMPLE_RATE_HZ``，由调用方传入；
    缺失/NaN 显「—」，不拿 0 或默认值冒充读数。
    """
    if hz is None:
        return "—"
    try:
        v = float(hz)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v / 1e6:.3f} MS/s"
