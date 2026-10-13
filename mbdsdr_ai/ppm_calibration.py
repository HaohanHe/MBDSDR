# SPDX-License-Identifier: MIT
"""
晶体频偏（ppm）校准纯函数
=========================

RTL2832U + FC0012 这类廉价棒的晶振有 20~50ppm 误差（435MHz 上约
±22kHz），绝对频率读数不可信。唯一可靠的办法是对照一个已知频率参考
（FM 整点载波 / 授时台 / 卫星信标）测出误差，再把校正值下发给设备——
即 kalibrate 的做法（docs/learn/kalibrate.md）。

本模块只做两件可单测的纯函数，供 sdr_tools 的两个工具共用：

- :func:`estimate_ppm` —— 已知参考频率 + 实测谱峰频率 → 原始 ppm 误差；
- :func:`clamp_ppm` —— 把估计值钳到 ±limit，并如实报告"是否被钳过"。

红线（诚实文化）：
- 没有已知参考就无从校准：``expected_hz <= 0``（或非有限值）时
  :func:`estimate_ppm` 抛 ``ValueError``，绝不返回 0.0 冒充"已校准"；
- 钳位不是造假：超出 ±limit 的值仍按 limit 应用，但第二元素 ``True``
  告诉调用方"这个值可疑"（多半是看错了峰或拿错了参考），由调用方
  如实转告 agent。
"""
from __future__ import annotations

import math

# 钳位上限默认 200ppm：廉价棒晶振典型 20~50ppm（sdr_backend.py RTLSDRBackend
# 文档），kalibrate 的经验门限 ~40kHz@270833Hz ≈ 148ppm
# （docs/learn/kalibrate.md §6.4-4）。±200ppm 之外基本可判定不是晶体问题，
# 但仍按 limit 钳位应用（不拒收），只是标记可疑。
DEFAULT_PPM_LIMIT = 200.0


def estimate_ppm(expected_hz: float, measured_hz: float) -> float:
    """由「已知参考频率 + 实测谱峰频率」估计晶振 ppm 误差。

    ``ppm = (measured_hz - expected_hz) / expected_hz * 1e6``

    正值 = 实测频率高于参考（本地晶振偏慢、读数偏高）；
    负值 = 实测频率低于参考。这与 kalibrate 的误差口径一致
    （``offset.cc:125`` 的 ``avg_offset/center_freq`` 项）；
    按同源约定，**建议下发的校正值 = 本函数的相反数**
    （``corr_new = corr_old - 误差ppm``），符号换算由调用方负责，
    本函数只如实计算相对误差。

    ``expected_hz <= 0`` 或非有限（NaN/inf）时诚实抛 ``ValueError``：
    没有已知频率参考就无法校准，拒绝假装校准。
    """
    expected = float(expected_hz)
    measured = float(measured_hz)
    if not math.isfinite(expected) or not math.isfinite(measured):
        raise ValueError(
            f"expected_hz/measured_hz 必须是有限数值（得到 {expected_hz!r}/{measured_hz!r}）："
            "NaN/inf 参考不可信，拒绝假装校准")
    if expected <= 0.0:
        raise ValueError(
            f"expected_hz 必须 > 0（得到 {expected_hz!r}）："
            "已知频率参考是校准的唯一基准，没有参考就无法估计 ppm")
    return (measured - expected) / expected * 1e6


def clamp_ppm(ppm: float, limit: float = DEFAULT_PPM_LIMIT) -> tuple[float, bool]:
    """把 ppm 估计钳到 ±limit，返回 ``(钳位后值, 是否被钳位)``。

    第二元素 ``True`` = 估计值超出合理范围（参考/读数可疑），调用方应
    把"可疑"如实告知 agent，而不是静默吞掉超范围值。被钳的值照样返回
    （仍按 limit 应用），不返回 None 之类的空值让上层猜。
    """
    value = float(ppm)
    bound = abs(float(limit))
    if value > bound:
        return bound, True
    if value < -bound:
        return -bound, True
    return value, False
