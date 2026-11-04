# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine — 天球渲染辅助子包（供 desktop/rf_sky_view 使用）
=====================================================================

子模块:
  - stars:          Hipparcos 亮星子集 + 星等渲染（Pogson 照度定律 / B-V 色指数）
  - star_catalog:   全天 HYG 星表 + 视场驱动的星等分层 (LOD)
  - constellations: IAU 88 星座数据（官方名/缩写/中文名/示意连线）
  - xingguan:       中国星官独立体系（三垣二十八宿，与 IAU 分开存储/标注）
  - satellites:     卫星目录索引 / COSPAR 解析 / 升落预报
  - jtime:          儒略日 / GMST / 时间流速内核（IAU 1982 公式）

坐标链复用 mbdsdr_ai.celestial_geometry (ICRF->当前赤道->地平),
投影复用 celestial_geometry 中的透视投影类。
本包名 skyengine 是为了不与已有扁平模块 mbdsdr_ai/astronomy.py 冲突。

Stellarium / Stellarium Web Engine 仅作为技术参考与致谢，本包未包含其源代码。
"""

from .stars import (BrightStar, BRIGHT_STARS, mag_to_lux, star_draw, bv_to_rgb)
from .star_catalog import (StarCatalog, StarSelection, get_catalog,
                           load_catalog, limit_magnitude, select_for_view,
                           extinction_delta_mag_array)
from .constellations import (
    SYSTEM_IAU, SYSTEM_CHINESE, ConstellationStar, IAUConstellation,
    IAUConstellationSet, ConstellationLayer, load_iau_constellations,
    get_iau_constellations, iau_constellation_segments,
    iau_constellation_label, iau_constellation_anchor, system_info,
    constellation_layer, overlay_layers, payload_sha256, validate_data_payload,
)
from .xingguan import (
    XingguanStar, Xingguan, XingguanRegion, XingguanSet, XingguanAnchor,
    load_xingguan, get_xingguan, xingguan_regions, xingguan_region,
    xingguan_segments, xingguan_all_segments, xingguan_label,
    xingguan_culture, xingguan_anchor, xingguan_anchor_reason,
    xingguan_layer,
)
from .satellites import (SatelliteMeta, SATELLITE_INDEX, cospar_from_tle,
                         norad_from_tle, next_rise_set)
from . import jtime

__all__ = [
    "BrightStar", "BRIGHT_STARS", "mag_to_lux", "star_draw", "bv_to_rgb",
    "StarCatalog", "StarSelection", "get_catalog", "load_catalog",
    "limit_magnitude", "select_for_view", "extinction_delta_mag_array",
    "SYSTEM_IAU", "SYSTEM_CHINESE", "ConstellationStar", "IAUConstellation",
    "IAUConstellationSet", "ConstellationLayer", "load_iau_constellations",
    "get_iau_constellations", "iau_constellation_segments",
    "iau_constellation_label", "iau_constellation_anchor", "system_info",
    "constellation_layer", "overlay_layers", "payload_sha256",
    "validate_data_payload",
    "XingguanStar", "Xingguan", "XingguanRegion", "XingguanSet",
    "XingguanAnchor",
    "load_xingguan", "get_xingguan", "xingguan_regions", "xingguan_region",
    "xingguan_segments", "xingguan_all_segments", "xingguan_label",
    "xingguan_culture", "xingguan_anchor", "xingguan_anchor_reason",
    "xingguan_layer",
    "SatelliteMeta", "SATELLITE_INDEX", "cospar_from_tle", "norad_from_tle",
    "next_rise_set", "jtime",
]
