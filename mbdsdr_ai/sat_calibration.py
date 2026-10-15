# SPDX-License-Identifier: MIT
"""卫星可见光通道物理定标链（SatDump meteor_m2_lrpt C0/C1/C2/C3 口径）。

链条（公开文献公式，非任何 GPL 源码；Cite EUMETSAT/Meteosat 可见光定标
传统，与 SatDump meteor_m2_lrpt cal 结构同构）：

    计数 C --(offset, scale)--> 辐射亮度 L [W/(m2*sr)]
    L --(esun, cos(theta), d^2)--> 反射率 rho (无量纲)

第二步即 EUMETSAT/NASA 通用的窄波段反射率归一：
    rho = pi * L * d^2 / (esun * cos(theta_solar))
（d 以 AU 计，theta 为太阳天顶角）。

诚实边界：
- 本模块只做**公式层**；cal 参数（各通道 offset/scale、esun、日照角、日地
  距离）必须由调用方显式给——没有内置"猜一个"的 cal 表（LRPT/METEOR 的
  定标系数本身依赖在轨定标流程，本项目无法凭空生成）。
- 输入计数越界/超范围只 clip 到 [0,1] 反射率物理域且如实报告，不 wrap、
  不取模。
- 红外通道（10.5-11.5um）的亮温/Planck 反演不在本模块范围（登记未做）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

__all__ = [
    "ChannelCal",
    "counts_to_radiance",
    "radiance_to_reflectance",
    "counts_to_reflectance",
    "reflectance_to_brightness",
    "CalibrationTable",
    "DEFAULT_ESUN",
    "EARTH_SUN_MEAN_AU",
]


#: 典型太阳常数（ESun）缺省参考值（窄波段过滤太阳辐照度 W/(m2*nm) 量级）。
#: 权威值应随卫星/通道定标文件给——此处仅为无资料时的保守参考，调用方
#: 显式覆盖优先；不确定时宁可报错也不静默用错光谱档。
DEFAULT_ESUN: Dict[int, float] = {
    1: 1400.0,   # 可见 ~0.5-0.9um（METEOR MSU-MR ch2 区间量级）
    2: 600.0,    # 近红外 ~0.7-1.1um（METEOR ch3 区间量级）
    3: 500.0,    # 中红外 ~1.6-1.8um（METEOR ch4 区间量级）
    4: 150.0,    # 热红外 ~3.5-4.1um（METEOR ch5 区间量级）
}

#: 日地平均距离（AU）。
EARTH_SUN_MEAN_AU = 1.0


@dataclass(frozen=True)
class ChannelCal:
    """单通道线性定标参数：C = offset + scale * count（count in [0,255]）。

    Cite EUMETSAT 定标传统（见模块 docstring 的公式出处说明）。
    """
    offset: float
    scale: float

    def counts_to_radiance(self, counts) -> np.ndarray:
        return self.offset + self.scale * np.asarray(counts, dtype=np.float64)


def counts_to_radiance(counts, cal: ChannelCal) -> np.ndarray:
    """原始计数 -> 辐射亮度 L [W/(m2*sr)]。"""
    return cal.counts_to_radiance(counts)


def radiance_to_reflectance(radiance, esun: float,
                            solar_zenith_deg: float,
                            earth_sun_distance_au: float = EARTH_SUN_MEAN_AU
                            ) -> np.ndarray:
    """辐射亮度 -> 反射率 rho（窄波段归一）。

    参数
    ----
    radiance : 辐射亮度阵列 [W/(m2*sr)]。
    esun : 窄波段过滤太阳辐照度（见 DEFAULT_ESUN；调用方应显式给权威值）。
    solar_zenith_deg : 太阳天顶角（度）。>=90（夜间/地平线下）-> 无有效
        日照反射 -> 诚实返回 0 而非 NaN/负值。
    earth_sun_distance_au : 日地距离（AU），轨道偏心订正用。

    返回反射率（无量纲，未裁切；物理越界值由调用方裁切或报告）。
    """
    if esun <= 0:
        raise ValueError(f"esun 须为正，收到 {esun}")
    d = float(earth_sun_distance_au)
    if d <= 0:
        raise ValueError(f"日地距离须为正，收到 {d}")
    if solar_zenith_deg >= 90.0:
        return np.zeros_like(np.asarray(radiance, dtype=np.float64))
    cos_z = float(np.cos(np.radians(solar_zenith_deg)))
    L = np.asarray(radiance, dtype=np.float64)
    rho = np.pi * L * d * d / (esun * cos_z)
    return rho


def counts_to_reflectance(counts, cal: ChannelCal, esun: float,
                          solar_zenith_deg: float,
                          earth_sun_distance_au: float = EARTH_SUN_MEAN_AU
                          ) -> np.ndarray:
    """计数 -> 反射率一步链（counts -> L -> rho）。"""
    return radiance_to_reflectance(
        counts_to_radiance(counts, cal), esun, solar_zenith_deg,
        earth_sun_distance_au)


def reflectance_to_brightness(rho, white: Optional[float] = None) -> np.ndarray:
    """反射率 -> 显示亮度（uint8）。

    默认按/100 或给定白色反射率参考归一化并裁剪到 [0,255]。
    rho<=0 -> 0；上限裁切实时报告不上溢。
    """
    ref = 100.0 if white is None or white <= 0 else float(white)
    r = np.asarray(rho, dtype=np.float64)
    return np.clip(np.round(r / ref * 255.0), 0, 255).astype(np.uint8)


@dataclass
class CalibrationTable:
    """通道 cal 表（分通道 offset/scale + 可选 esun 覆盖）。

    honest 原则：缺通道/缺 esun 时调用显式报错；不提供"万能默认 cal"。
    """

    channels: Dict[int, ChannelCal]
    esun: Dict[int, float]

    def __post_init__(self) -> None:
        merged = dict(DEFAULT_ESUN)
        merged.update({k: float(v) for k, v in (self.esun or {}).items()})
        self.esun = merged

    def get(self, channel: int) -> ChannelCal:
        cal = self.channels.get(channel)
        if cal is None:
            raise KeyError(f"通道 {channel} 无定标参数（CalibrationTable.channels）")
        return cal

    def calibrate(self, channel: int, counts, solar_zenith_deg: float,
                  earth_sun_distance_au: float = EARTH_SUN_MEAN_AU
                  ) -> Tuple[np.ndarray, np.ndarray]:
        """返回 (radiance, reflectance)。缺 esun 诚实报错。"""
        if channel not in self.esun:
            raise KeyError(f"通道 {channel} 无 esun（应在 CalibrationTable.esun）")
        rad = counts_to_radiance(counts, self.get(channel))
        rho = radiance_to_reflectance(rad, self.esun[channel],
                                      solar_zenith_deg, earth_sun_distance_au)
        return rad, rho
