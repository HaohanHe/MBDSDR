"""
mbdsdr_ai/skyengine — Stellarium 天球引擎精髓 (独立重实现, 供 desktop/rf_sky_view)
================================================================================

子模块:
  - stars:      Hipparcos 亮星子集 + 星等渲染 (core.c:697/386)
  - satellites: 卫星目录索引 / COSPAR 解析 / 升落预报
  - jtime:      儒略日 / GMST / 时间流速内核 (navigation.c:52)

坐标链复用 mbdsdr_ai.celestial_geometry (ICRF->当前赤道->地平),
投影复用 celestial_geometry.PerspectiveProjection (StelProjector 透视)。
本包名 skyengine 是为了不与已有扁平模块 mbdsdr_ai/astronomy.py 冲突。
"""

from .stars import (BrightStar, BRIGHT_STARS, mag_to_lux, star_draw, bv_to_rgb)
from .satellites import (SatelliteMeta, SATELLITE_INDEX, cospar_from_tle,
                         norad_from_tle, next_rise_set)
from . import jtime

__all__ = [
    "BrightStar", "BRIGHT_STARS", "mag_to_lux", "star_draw", "bv_to_rgb",
    "SatelliteMeta", "SATELLITE_INDEX", "cospar_from_tle", "norad_from_tle",
    "next_rise_set", "jtime",
]
