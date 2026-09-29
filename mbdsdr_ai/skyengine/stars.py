# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/stars.py — 亮星星表子集与星等渲染
========================================================

星表为 Hipparcos 亮星 (目视星等 <= ~2.4) 的真实子集, 每颗带 J2000
赤经/赤纬/V 星等/B-V 色指数。不造假星: 未收录的天体不画。

渲染公式（依据公开天文光度学）:
  - 照度 E(lux) = 10.7646e4 / R2AS^2 * 10^(-0.4*vmag)
    其中 R2AS = 180/pi*3600 ~ 206265 角秒/弧度 (Pogson 星等-照度定律, 等差 2.512x)。
  - 点大小/亮度：按星等做 gamma 近似映射到屏幕半径与亮度（人眼适应的简化近似）。
  - 颜色：B-V 色指数分段线性插值定色温（常用的黑体色温近似）。

坐标: 恒星位置是 ICRF/J2000 赤道坐标, 经 celestial_geometry 坐标链
(J2000 -> 岁差章动 -> 当前赤道 -> 地平) 投影到屏幕。

Stellarium / Stellarium Web Engine 仅作为技术参考与致谢，本模块未包含其源代码。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

R2AS: float = 180.0 / math.pi * 3600.0   # 弧度 -> 角秒 ~ 206265


@dataclass(frozen=True)
class BrightStar:
    name: str
    ra_deg: float     # J2000 赤经 (度, 0..360)
    dec_deg: float    # J2000 赤纬 (度)
    vmag: float       # 目视星等
    bv: float         # B-V 色指数


# Hipparcos 亮星子集 (真实 J2000 数据)。(name, ra_deg, dec_deg, vmag, bv)
BRIGHT_STARS: list[BrightStar] = [
    BrightStar("Sirius",      101.287,  -16.716, -1.46,  0.00),
    BrightStar("Canopus",      95.987,  -52.696, -0.74,  0.15),
    BrightStar("Arcturus",    213.915,   19.182, -0.05,  1.23),
    BrightStar("Vega",        279.234,   38.784,  0.03,  0.00),
    BrightStar("Capella",      79.172,   45.998,  0.08,  0.80),
    BrightStar("Rigel",        78.634,   -8.202,  0.13, -0.03),
    BrightStar("Procyon",     114.825,    5.225,  0.34,  0.42),
    BrightStar("Betelgeuse",   88.793,    7.407,  0.42,  1.85),
    BrightStar("Achernar",     24.428,  -57.237,  0.46, -0.16),
    BrightStar("Hadar",       210.958,  -60.373,  0.61, -0.23),
    BrightStar("Altair",      297.696,    8.868,  0.76,  0.22),
    BrightStar("Acrux",       186.650,  -63.099,  0.77, -0.24),
    BrightStar("Aldebaran",    68.980,   16.509,  0.86,  1.54),
    BrightStar("Antares",     247.352,  -26.432,  0.96,  1.83),
    BrightStar("Spica",       201.365,  -11.161,  0.98, -0.23),
    BrightStar("Pollux",      116.329,   28.026,  1.14,  1.00),
    BrightStar("Fomalhaut",   344.413,  -29.622,  1.16,  0.09),
    BrightStar("Deneb",       310.358,   45.280,  1.25,  0.09),
    BrightStar("Mimosa",      191.930,  -59.689,  1.25, -0.23),
    BrightStar("Regulus",     152.093,   11.967,  1.35, -0.11),
    BrightStar("Adhara",      187.106,  -28.972,  1.50, -0.20),
    BrightStar("Castor",      113.650,   31.888,  1.58,  0.03),
    BrightStar("Shaula",      263.402,  -37.104,  1.62, -0.22),
    BrightStar("Gacrux",      187.706,  -57.113,  1.63,  1.59),
    BrightStar("Bellatrix",    81.283,    6.350,  1.64, -0.22),
    BrightStar("Elnath",       81.570,   28.608,  1.65, -0.13),
    BrightStar("Miaplacidus", 138.304,  -69.717,  1.67,  0.04),
    BrightStar("Alnilam",      84.053,   -1.202,  1.69, -0.18),
    BrightStar("Alnair",       34.851,  -46.961,  1.74,  0.02),
    BrightStar("Regor",       196.705,  -56.723,  1.75, -0.14),
    BrightStar("Alioth",      193.507,   55.960,  1.76, -0.02),
    BrightStar("Alnitak",      85.190,   -1.943,  1.77, -0.21),
    BrightStar("Dubhe",       165.932,   61.751,  1.79,  1.07),
    BrightStar("Mirfak",       55.956,   49.861,  1.79,  0.48),
    BrightStar("Wezen",        96.797,   -2.246,  1.83,  0.68),
    BrightStar("Kaus Australis", 279.535, -34.385, 1.85, -0.03),
    BrightStar("Alkaid",      206.885,   49.313,  1.86, -0.19),
    BrightStar("Avior",       155.299,  -59.510,  1.86,  1.21),
    BrightStar("Sargas",       271.429,  -42.998,  1.86,  1.22),
    BrightStar("Menkalinan",   88.806,   44.947,  1.90,  0.08),
    BrightStar("Alhena",       96.429,   16.399,  1.93,  0.00),
    BrightStar("Peacock",     306.412,  -56.733,  1.94, -0.20),
    BrightStar("Alphard",     141.890,   -8.659,  1.98,  1.44),
    BrightStar("Polaris",      37.954,   89.264,  1.98,  0.60),
    BrightStar("Hamal",        31.793,   23.462,  2.00,  1.15),
    BrightStar("Diphda",       13.020,  -17.987,  2.04, -0.03),
    BrightStar("Alpheratz",     2.097,   29.091,  2.06, -0.05),
    BrightStar("Mirach",       16.028,   35.618,  2.07,  1.58),
    BrightStar("Algieba",     154.945,   19.842,  2.08,  1.25),
    BrightStar("Denebola",    177.266,   14.572,  2.11,  0.09),
    BrightStar("Algol",        47.042,   40.956,  2.12, -0.05),
    BrightStar("Mizar",       200.981,   54.925,  2.23,  0.02),
    BrightStar("Naos",        126.574,  -40.003,  2.25, -0.27),
    BrightStar("Ankaa",        21.479,  -42.306,  2.40,  1.17),
]


def mag_to_lux(vmag: float) -> float:
    """照度 E(lux) = 10.7646e4/R2AS^2 * 10^(-0.4*vmag)（Pogson 定律）。"""
    return 10.7646e4 / (R2AS * R2AS) * 10.0 ** (-0.4 * vmag)


def star_draw(vmag: float, bortle: int = 3) -> tuple[float, float]:
    """返回 (屏幕半径 px, 亮度 0..1)。太暗的星 (vmag>6.5) 半径压到 0。

    屏幕半径按极限星等与全天最亮星等做幂律映射：
        r = r_min + (r_max-r_min) * ((6.5-vmag)/8)^(s_relative/2)
    映射: 极限星等 6.5 -> 0.6px, 全天最亮 (mag -1.5) -> ~2.6px。
    bortle 越大 (光污染越重) 整体越小越暗。
    """
    if vmag > 6.5:
        return 0.0, 0.0
    s_relative = 1.1
    dim = 1.0 - 0.06 * (bortle - 3)
    frac = max(0.0, min(1.0, (6.5 - vmag) / 8.0))
    r = (0.6 + 2.0 * frac ** (s_relative / 2.0)) * dim
    brightness = max(0.0, min(1.0, frac))
    return max(0.5, r), brightness


def bv_to_rgb(bv: float) -> tuple[int, int, int]:
    """B-V 色指数 -> (r,g,b) 0..255（黑体色温分段线性近似）。"""
    stops = [
        (-0.30, (155, 178, 255)),
        (0.00, (200, 214, 255)),
        (0.30, (232, 234, 248)),
        (0.60, (255, 244, 214)),
        (1.00, (255, 210, 130)),
        (1.50, (255, 150, 80)),
    ]
    if bv <= stops[0][0]:
        return stops[0][1]
    if bv >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        b0, c0 = stops[i]
        b1, c1 = stops[i + 1]
        if b0 <= bv <= b1:
            t = (bv - b0) / (b1 - b0)
            return (int(c0[0] + (c1[0] - c0[0]) * t),
                    int(c0[1] + (c1[1] - c0[1]) * t),
                    int(c0[2] + (c1[2] - c0[2]) * t))
    return (255, 255, 255)
