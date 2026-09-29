# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/astronomy/refraction.py — 大气折射校正
==================================================

依据公开折射公式独立实现：
  - ``press_temp_corr = P/1010 * 283/(273+T)``  (气压/温度修正因子)
  - 正向（几何真高度 → 视高度）Saemundsson 公式，单位角分：
        r' = corr * ( 1.02 / tan((alt + 10.3/(alt+5.11))*π/180) + 0.0019279 )
  - 反向（视高度 → 几何真高度）Bennett 公式：
        r' = corr * ( 1.00 / tan((alt + 7.31/(alt+4.4))*π/180) + 0.0013515 )

参考测试值（P=1010hPa, T=10°C）：
  几何高度 0°  → 折射 28.982'
  几何高度 5°  →  9.674'
  几何高度 45°  →  1.013'
  几何高度 89°  →  0.016' （天顶≈0）
  视高度   0°  → 反向 Bennett ≈ 34.5'（地平线著名的 ~34' 下沉）

我们的增强：
  * ``RefractionModel`` 类可实例化、可开关（enabled=False 时恒等映射）。
  * 同时提供 arcmin / degree / 向量级接口，便于与现有 ``astronomy.Observer`` 集成。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "RefractionModel",
    "saemundsson_arcmin",
    "bennett_arcmin",
    "true_to_apparent",
    "apparent_to_true",
]

# 标准大气
STD_PRESSURE_HPA = 1010.0
STD_TEMPERATURE_C = 10.0


def _pressure_temp_factor(pressure_hpa: float, temperature_c: float) -> float:
    """气压/温度修正因子 corr = P/1010 * 283/(273+T)。"""
    return (pressure_hpa / 1010.0) * (283.0 / (273.0 + temperature_c))


def saemundsson_arcmin(true_alt_deg: float,
                       pressure_hpa: float = STD_PRESSURE_HPA,
                       temperature_c: float = STD_TEMPERATURE_C) -> float:
    """正向折射角（角分）：几何真高度 → 视高度抬升量。

    当 true_alt_deg <= -5° 时返回 0（公式不再可信）。
    """
    if true_alt_deg <= -5.0:
        return 0.0
    corr = _pressure_temp_factor(pressure_hpa, temperature_c)
    x = math.radians(true_alt_deg + 10.3 / (true_alt_deg + 5.11))
    return corr * (1.02 / math.tan(x) + 0.0019279)


def bennett_arcmin(obs_alt_deg: float,
                   pressure_hpa: float = STD_PRESSURE_HPA,
                   temperature_c: float = STD_TEMPERATURE_C) -> float:
    """反向折射角（角分）：视高度 → 几何真高度需要扣除的量。

    Bennett 公式（Meeus, Astr. Algorithms）。
    """
    if obs_alt_deg <= -5.0:
        return 0.0
    corr = _pressure_temp_factor(pressure_hpa, temperature_c)
    x = math.radians(obs_alt_deg + 7.31 / (obs_alt_deg + 4.4))
    return corr * (1.0 / math.tan(x) + 0.0013515)


def true_to_apparent(true_alt_deg: float,
                     pressure_hpa: float = STD_PRESSURE_HPA,
                     temperature_c: float = STD_TEMPERATURE_C) -> float:
    """几何真高度（度）→ 视高度（度）。"""
    return true_alt_deg + saemundsson_arcmin(true_alt_deg, pressure_hpa, temperature_c) / 60.0


def apparent_to_true(obs_alt_deg: float,
                     pressure_hpa: float = STD_PRESSURE_HPA,
                     temperature_c: float = STD_TEMPERATURE_C) -> float:
    """视高度（度）→ 几何真高度（度）。"""
    return obs_alt_deg - bennett_arcmin(obs_alt_deg, pressure_hpa, temperature_c) / 60.0


@dataclass
class RefractionModel:
    """可开关的折射模型。enabled=False 时恒等。"""

    pressure_hpa: float = STD_PRESSURE_HPA
    temperature_c: float = STD_TEMPERATURE_C
    enabled: bool = True

    def true_to_apparent(self, true_alt_deg: float) -> float:
        if not self.enabled:
            return true_alt_deg
        return true_to_apparent(true_alt_deg, self.pressure_hpa, self.temperature_c)

    def apparent_to_true(self, obs_alt_deg: float) -> float:
        if not self.enabled:
            return obs_alt_deg
        return apparent_to_true(obs_alt_deg, self.pressure_hpa, self.temperature_c)
