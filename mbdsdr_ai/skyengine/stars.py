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
  - 大气消光：等厚大气 Δm = k/sin(alt)（Stellarium RefractionExtinction
    同款机制），地平附近星系统性变暗。

坐标: 恒星位置是 ICRF/J2000 赤道坐标, 经 celestial_geometry 坐标链
(J2000 -> 岁差章动 -> 当前赤道 -> 地平) 投影到屏幕。

Stellarium / Stellarium Web Engine 仅作为技术参考与致谢，本模块未包含其源代码。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

R2AS: float = 180.0 / math.pi * 3600.0   # 弧度 -> 角秒 ~ 206265


@dataclass(frozen=True)
class BrightStar:
    name: str
    ra_deg: float     # J2000 赤经 (度, 0..360)
    dec_deg: float    # J2000 赤纬 (度)
    vmag: float       # 目视星等
    bv: float         # B-V 色指数
    dist_ly: float = 0.0   # 距离 (光年); 0.0 = 未知, 信息卡如实显 "—"
    spect: str = ""        # 光谱型 (HYG); 空串 = 未知


# Hipparcos 亮星子集 (J2000 视位置; 坐标/星等/色指数/距离经 HYG v4.1
# (CC BY-SA 4.0, astronexus) proper 名与 Bayer 名双路对拍吸附,
# 光谱型注记 = HYG spect)。
BRIGHT_STARS: list[BrightStar] = [
    BrightStar("Sirius",   101.2872,   -16.7161, -1.44,  0.01,      8.6, "A0m..."),  # A0m... CMa
    BrightStar("Canopus",    95.9879,   -52.6957, -0.62,  0.16,    309.2, "F0Ib"),  # F0Ib Car
    BrightStar("Arcturus",   213.9154,   +19.1824, -0.05,  1.24,     36.7, "K2IIIp"),  # K2IIIp Boo
    BrightStar("Vega",   279.2346,   +38.7837,  0.03, -0.00,     25.0, "A0Vvar"),  # A0Vvar Lyr
    BrightStar("Capella",    79.1723,   +45.9980,  0.08,  0.80,     42.8, "M1: comp"),  # M1: comp Aur
    BrightStar("Rigel",    78.6345,    -8.2016,  0.18, -0.03,    862.8, "B8Ia"),  # B8Ia Ori
    BrightStar("Procyon",   114.8255,    +5.2250,  0.40,  0.43,     11.5, "F5IV-V"),  # F5IV-V CMi
    BrightStar("Betelgeuse",    88.7929,    +7.4071,  0.45,  1.50,    497.9, "M2Ib"),  # M2Ib Ori
    BrightStar("Achernar",    24.4283,   -57.2368,  0.45, -0.16,    139.4, "B3Vp"),  # B3Vp Eri
    BrightStar("Hadar",   210.9559,   -60.3730,  0.61, -0.23,    392.0, "B1III"),  # B1III Cen
    BrightStar("Altair",   297.6958,    +8.8683,  0.76,  0.22,     16.7, "A7IV-V"),  # A7IV-V Aql
    BrightStar("Acrux",   186.6497,   -63.0991,  0.77, -0.24,    322.0, "B0.5IV"),  # B0.5IV Cru
    BrightStar("Aldebaran",    68.9802,   +16.5093,  0.87,  1.54,     66.6, "K5III"),  # K5III Tau
    BrightStar("Antares",   247.3519,   -26.4320,  1.06,  1.86,    553.7, "M1Ib + B2.5V"),  # M1Ib + B2.5V Sco
    BrightStar("Spica",   201.2982,   -11.1613,  0.98, -0.23,    249.7, "B1V"),  # B1V  Vir
    BrightStar("Pollux",   116.3292,   +28.0262,  1.16,  0.99,     33.8, "K0IIIvar"),  # K0IIIvar Gem
    BrightStar("Fomalhaut",   344.4126,   -29.6222,  1.17,  0.14,     25.1, "A3V"),  # A3V  PsA
    BrightStar("Deneb",   310.3580,   +45.2803,  1.25,  0.09,   1411.9, "A2Ia"),  # A2Ia Cyg
    BrightStar("Mimosa",   191.9304,   -59.6888,  1.25, -0.24,    278.5, "B0.5III"),  # B0.5III Cru
    BrightStar("Regulus",   152.0930,   +11.9672,  1.36, -0.09,     79.3, "B7V"),  # B7V  Leo
    BrightStar("Adhara",   104.6565,   -28.9721,  1.50, -0.21,    405.2, "B2II"),  # B2II CMa
    BrightStar("Castor",   113.6495,   +31.8883,  1.58,  0.03,     50.9, "A2Vm"),  # A2Vm Gem
    BrightStar("Shaula",   263.4022,   -37.1038,  1.62, -0.23,    571.2, "B1.5IV+..."),  # B1.5IV+... Sco
    BrightStar("Gacrux",   187.7914,   -57.1132,  1.59,  1.60,     88.6, "M4III"),  # M4III Cru
    BrightStar("Bellatrix",    81.2828,    +6.3497,  1.64, -0.22,    252.4, "B2III"),  # B2III Ori
    BrightStar("Elnath",    81.5730,   +28.6075,  1.65, -0.13,    133.9, "B7III"),  # B7III Tau
    BrightStar("Miaplacidus",   138.3006,   -69.7172,  1.67,  0.07,    113.2, "A2IV"),  # A2IV Car
    BrightStar("Alnilam",    84.0534,    -1.2019,  1.69, -0.18,   1976.7, "B0Ia"),  # B0Ia Ori
    BrightStar("Alnair",   332.0581,   -46.9610,  1.73, -0.07,    101.0, "B7IV"),  # B7IV Gru
    BrightStar("Regor",   122.3831,   -47.3366,  1.75, -0.14,   1117.0, "WC8 + O9I"),  # WC8 + O9I Vel
    BrightStar("Alioth",   193.5071,   +55.9598,  1.76, -0.02,     82.6, "A0p"),  # A0p  UMa
    BrightStar("Alnitak",    85.1897,    -1.9426,  1.74, -0.20,    736.2, "O9.5Ib SB"),  # O9.5Ib SB Ori
    BrightStar("Dubhe",   165.9323,   +61.7510,  1.81,  1.06,    122.9, "F7V comp"),  # F7V comp UMa
    BrightStar("Mirfak",    51.0807,   +49.8612,  1.79,  0.48,    506.5, "F5Ib"),  # F5Ib Per
    BrightStar("Wezen",   107.0979,   -26.3932,  1.83,  0.67,   1606.7, "F8Ia"),  # F8Ia CMa
    BrightStar("Kaus Australis",   276.0430,   -34.3846,  1.79, -0.03,    143.3, "B9.5III"),  # B9.5III Sgr
    BrightStar("Alkaid",   206.8853,   +49.3133,  1.85, -0.10,    103.9, "B3V SB"),  # B3V SB UMa
    BrightStar("Avior",   125.6285,   -59.5095,  1.86,  1.20,    605.1, "K3III+B2V"),  # K3III+B2V Car
    BrightStar("Sargas",   264.3297,   -42.9978,  1.86,  0.41,    300.3, "F1II"),  # F1II Sco
    BrightStar("Menkalinan",    89.8822,   +44.9474,  1.90,  0.08,     81.1, "A2V"),  # A2V  Aur
    BrightStar("Alhena",    99.4279,   +16.3993,  1.93,  0.00,    109.3, "A0IV"),  # A0IV Gem
    BrightStar("Peacock",   306.4119,   -56.7351,  1.94, -0.12,    178.8, "B2IV"),  # B2IV Pav
    BrightStar("Alphard",   141.8969,    -8.6586,  1.99,  1.44,    180.3, "K3III"),  # K3III Hya
    BrightStar("Polaris",    37.9462,   +89.2641,  1.97,  0.64,    432.6, "F7:Ib-IIv SB"),  # F7:Ib-IIv SB UMi
    BrightStar("Hamal",    31.7933,   +23.4624,  2.01,  1.15,     65.8, "K2III"),  # K2III Ari
    BrightStar("Diphda",    10.8973,   -17.9866,  2.04,  1.02,     96.3, "K0III"),  # K0III Cet
    BrightStar("Alpheratz",     2.0969,   +29.0904,  2.07, -0.04,     97.0, "B9p"),  # B9p  And
    BrightStar("Mirach",    17.4329,   +35.6206,  2.07,  1.58,    197.4, "M0IIIvar"),  # M0IIIvar And
    BrightStar("Algieba",   154.9931,   +19.8415,  2.01,  1.13,    130.1, "K0III"),  # K0III Leo
    BrightStar("Denebola",   177.2649,   +14.5721,  2.14,  0.09,     35.9, "A3Vvar"),  # A3Vvar Leo
    BrightStar("Algol",    47.0422,   +40.9556,  2.09, -0.00,     89.9, "B8V"),  # B8V  Per
    BrightStar("Mizar",   200.9812,   +54.9254,  2.23,  0.06,     85.8, "A2V"),  # A2V  UMa
    BrightStar("Naos",   120.8961,   -40.0031,  2.21, -0.27,   1083.6, "O5IAf"),  # O5IAf Pup
    BrightStar("Ankaa",     6.5708,   -42.3060,  2.40,  1.08,     84.7, "K0III..."),  # K0III... Phe
]


def mag_to_lux(vmag: float) -> float:
    """照度 E(lux) = 10.7646e4/R2AS^2 * 10^(-0.4*vmag)（Pogson 定律）。"""
    return 10.7646e4 / (R2AS * R2AS) * 10.0 ** (-0.4 * vmag)


# ---------------------------------------------------------------------------
# 中国星名/星官对照（文化对位层，"读懂"冲刺）
# ---------------------------------------------------------------------------
# 数据在 data/star_culture.json（皮进配置，源码零中文名字面量在此之外）；
# 三垣四象二十八宿星官制，公开天文常识级对照，非标准文本摘录。
_CULTURE_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def _load_culture() -> Dict[str, Dict[str, str]]:
    global _CULTURE_CACHE
    if _CULTURE_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "star_culture.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw: Dict[str, Any] = json.load(fh)
            _CULTURE_CACHE = {k: v for k, v in raw.items()
                              if not k.startswith("_")}
        except (OSError, ValueError):
            _CULTURE_CACHE = {}
    return _CULTURE_CACHE


def star_culture(name: str) -> Optional[Dict[str, str]]:
    """按 BRIGHT_STARS 的西方名取中国星名/星官注记；无收录返回 None（不编造）。"""
    return _load_culture().get(str(name))


def culture_label(name: str) -> str:
    """星标签显示名：有中文星官用中文（文化对位），否则回退原名。"""
    c = star_culture(name)
    return c["zh"] if c else str(name)


# ---------------------------------------------------------------------------
# 光害可见性口径（**唯一事实源**）
# ---------------------------------------------------------------------------
# 裸眼极限星等基线：**Bortle 3 为 6.5 等**，每档 -0.35 等，上下都不截断
# （Bortle 1 暗空 ≈ 7.2 等，Bortle 9 城市天 ≈ 4.4 等，Stellarium
# StelSkyDrawer 同语义）。Bortle 1/2 当初被 max(0, bortle-3) 夹成与 3 同值，
# 于是把光害从 3 调到 1 画面一颗星都不多（P1-46），此处按同一线性律放开。
# 所有"这颗星画不画/点不点得中"的门都必须走 naked_eye_limit_mag()，
# 不许在别处复制这条公式（历史上 star_catalog.py 曾自带一份同式副本，
# 改一处漏一处就会让绘制门与拾取门各说各话）。
DEFAULT_BORTLE = 3               # 默认光害假设（乡下/近郊暗空）
NAKED_EYE_LIMIT_SCALE = 6.5      # 等：Bortle<=3 的裸眼极限基线
NAKED_EYE_BORTLE_SLOPE = 0.35    # 等/Bortle 档：每高一档的极限下降量
BORTLE_DIM_SLOPE = 0.06          # 亮度压暗系数：每高一档 Bortle 压暗 6%
# 星等→半径映射的参考跨度：从 6.5 等（frac=0）到 -1.5 等（frac=1）。
MAG_SPAN_TO_FULL_BRIGHT = 8.0


def naked_eye_limit_mag(bortle: int = DEFAULT_BORTLE) -> float:
    """光害等级 → 裸眼极限星等（天顶方向）。

    以 Bortle 3 = 6.5 等为基线，每档 -0.35 等，**1..9 全程单调**：
    Bortle 1 = 7.2、2 = 6.85、3 = 6.5、…、9 = 4.4。更暗的天空必须露出
    更暗的恒星，不能把 Bortle 1/2 夹成与 3 同值（否则光害设置在最该
    体现差别的暗空区间完全失效）。这是**唯一**定义，绘制层
    （``star_draw``/``star_draw_many``）与星表 LOD 层（``star_catalog``）
    都调用本函数。
    """
    return NAKED_EYE_LIMIT_SCALE - NAKED_EYE_BORTLE_SLOPE * (int(bortle) - 3)


def bortle_dim_factor(bortle: float) -> float:
    """光害等级 → 星点亮度压暗系数（Bortle 3 为 1.0，每档 -6%）。

    不夹 Bortle 下限：Bortle<3 视为更暗的天空，系数>1（历史口径，保持不变）。
    """
    return 1.0 - BORTLE_DIM_SLOPE * (bortle - 3)


# Stellarium StelSkyDrawer.cpp:420 同款小星补偿常数：半径 <1.2px 的星点
# 按面积比 r³/1.728 补亮度（1.2³ = 1.728），否则亚像素星能量不足、
# "亮星突兀暗星全灭"。大星则走平方根压缩，避免织女星糊屏。
_SMALL_STAR_R = 1.2
_SMALL_STAR_R3 = _SMALL_STAR_R ** 3      # 1.728
# 辉光（bloom）：压缩后半径 ≥ _BLOOM_MIN_R 的亮星给 0..1 强度，由调用方
# 画径向渐光晕；_BLOOM_MAX_R 对应强度 1 的参考半径。
_BLOOM_MIN_R = 2.0
_BLOOM_MAX_R = 8.0


def star_draw(vmag: float, bortle: int = DEFAULT_BORTLE) -> tuple[float, float, float]:
    """返回 (屏幕半径 px, 亮度 0..1, 辉光强度 0..1)。不可见星全 0。

    - 可见 clamp：vmag 超过光害极限星等 6.5-0.35*(bortle-3) 不画
      （Bortle 9 裸眼极限 ~4.4 等，Stellarium 同语义）；
    - 半径：幂律映射（原逻辑）+ 大星平方根压缩（r>1.2 后每 +1px
      实际只增 √，StelSkyDrawer computeRCMag 同款）；
    - 亮度：小星按 r³/1.728 补偿（StelSkyDrawer.cpp:420）；
    - 辉光：压缩后半径 ≥2px 的亮星按半径线性给强度。
    """
    limit = naked_eye_limit_mag(bortle)
    if vmag > limit:
        return 0.0, 0.0, 0.0
    s_relative = 1.1
    dim = bortle_dim_factor(bortle)
    # frac 不设上限 clamp：饱和交给下面 brightness 的 min(1.0, frac)，半径必须
    # 继续单调。恒星永不触发上限（两张星表最亮 Sirius -1.44/-1.46，未过 -1.5），
    # 但行星会——金星 -4.7、木星 -1.9、满月 -12.8，clamp 会把它们画成一样大
    # 一样亮，视觉编码当场说谎（SWE core_get_point_for_mag 要求所有天体共用
    # 一套单调亮度模型）。
    frac = max(0.0, (NAKED_EYE_LIMIT_SCALE - vmag) / MAG_SPAN_TO_FULL_BRIGHT)
    r = (0.6 + 2.0 * frac ** (s_relative / 2.0)) * dim
    if r > _SMALL_STAR_R:
        r = _SMALL_STAR_R + (r - _SMALL_STAR_R) ** 0.5
    brightness = max(0.0, min(1.0, frac))
    if r < _SMALL_STAR_R:
        brightness = min(1.0, brightness * (r ** 3) / _SMALL_STAR_R3)
    bloom = 0.0
    if r >= _BLOOM_MIN_R:
        bloom = min(1.0,
                    0.4 + (r - _BLOOM_MIN_R) / (_BLOOM_MAX_R - _BLOOM_MIN_R))
    return max(0.5, r), brightness, bloom


# 消光参数：k=0.2 对应晴朗海平面大气（Stellarium 默认数量级）。
EXTINCTION_K = 0.2
_EXTINCTION_MAX_DM = 3.5      # Δm 上限：再多就是地平线雾霾，不给无穷
_EXTINCTION_MIN_ALT = 0.5     # 仰角下限（度）：防 sin(alt)→0 奇异


def extinction_delta_mag(alt_deg: float, k: float = EXTINCTION_K) -> float:
    """大气消光星等增量 Δm = k/sin(alt)（等厚大气近似）。

    alt 越高消光越小（天顶 Δm=k）；地平附近星系统性变暗——Stellarium
    截图里地平线星更暗就是此机制。alt ≤ 0.5° 夹到 0.5° 防奇异，
    返回值夹到 3.5 等。
    """
    alt = max(_EXTINCTION_MIN_ALT, min(90.0, float(alt_deg)))
    s = math.sin(math.radians(alt))
    if s <= 1e-9:
        return _EXTINCTION_MAX_DM
    return min(_EXTINCTION_MAX_DM, k / s)


_BV_STOPS = (
    (-0.30, (155, 178, 255)),
    (0.00, (200, 214, 255)),
    (0.30, (232, 234, 248)),
    (0.60, (255, 244, 214)),
    (1.00, (255, 210, 130)),
    (1.50, (255, 150, 80)),
)


def bv_to_rgb(bv: float) -> tuple[int, int, int]:
    """B-V 色指数 -> (r,g,b) 0..255（黑体色温分段线性近似）。"""
    stops = _BV_STOPS
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


def bv_to_rgb_many(bv) -> tuple:
    """bv_to_rgb 的向量化镜像（标量版是事实源，逐点对拍见测试）。"""
    arr = np.asarray(bv, dtype=float).reshape(-1)
    edges = np.array([s[0] for s in _BV_STOPS], dtype=float)
    cols = np.array([s[1] for s in _BV_STOPS], dtype=float)
    # 仅 NaN 走兜底色：标量版对 NaN 所有区间判据为 False → (255,255,255)；
    # 但 ±inf 会命中首/末 stop（`bv >= 1.5` / `bv <= -0.3`），必须保留。
    bad = np.isnan(arr)
    # 标量对 NaN 的走向：所有区间判据为 False → 末尾 return (255,255,255)。
    x = np.where(bad, edges[0], arr)
    idx = np.clip(np.searchsorted(edges, x, side="right") - 1,
                  0, len(edges) - 2)
    lo = edges[idx]
    hi = edges[idx + 1]
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    rgb = cols[idx] + (cols[idx + 1] - cols[idx]) * t[:, None]
    rgb = np.trunc(rgb).astype(np.int64)     # 与 int() 一致（值域全非负）
    rgb[bad] = (255, 255, 255)
    return rgb[:, 0], rgb[:, 1], rgb[:, 2]


def saturate_for_brightness_many(bright) -> tuple:
    """可视亮度 → 星色饱和度系数（0..1，1 = 保留原色，0 = 纯白）。

    为什么需要这一步：星表里有三分之一的恒星 B-V >= 1.0，映射成
    (255,210,130)..(255,150,80) 的满饱和橙色。按满饱和画出来，整片天就是
    "一堆橙红点"—— 这不符合肉眼所见。暗视觉（scotopic）下色觉几乎失效，
    人眼看到的暗星基本是白色或极淡的色调，只有亮星才看得出橙/蓝。
    Stellarium 的 saturation 参数是同一思路。

    口径：0 等左右（bright≈1）保留 100% 颜色，6.5 等（bright≈0）压到 10%，
    中间按 bright 的 0.70 次幂过渡（暗端更平，避免"突然变白"的断层）。
    """
    # nan_to_num 不能省：上游一旦给了 NaN，np.clip 会原样留着，
    # power(NaN) 再传进颜色计算，整帧星色一起变坏（测试钉住）。
    raw = np.nan_to_num(np.asarray(bright, dtype=float).reshape(-1),
                        nan=0.0, posinf=1.0, neginf=0.0)
    b = np.clip(raw, 0.0, 1.0)
    return 0.10 + 0.90 * np.power(b, 0.70)


def desaturate_rgb_many(r, g, b, sat) -> tuple:
    """把颜色按饱和度系数向白插值：``out = 255 - (255 - c) * sat``。"""
    s = np.clip(np.asarray(sat, dtype=float).reshape(-1), 0.0, 1.0)[:, None]
    rgb = np.stack([
        np.asarray(r, dtype=float).reshape(-1),
        np.asarray(g, dtype=float).reshape(-1),
        np.asarray(b, dtype=float).reshape(-1),
    ], axis=1)
    out = 255.0 - (255.0 - rgb) * s
    out = np.clip(np.round(out), 0.0, 255.0).astype(np.int64)
    return out[:, 0], out[:, 1], out[:, 2]


def star_draw_many(vmag, bortle: int = DEFAULT_BORTLE) -> tuple:
    """star_draw 的向量化镜像（同一公式；标量版是事实源，测试逐点对拍）。

    NaN 星等按标量的实际走向处理：``max(0.0, nan)`` 得 0 → 半径按
    ``frac=0`` 计算，故按 6.5 等代入（不可见性判据 ``nan > limit`` 为 False）。
    """
    m = np.asarray(vmag, dtype=float).reshape(-1)
    bad = ~np.isfinite(m)
    m = np.where(bad, NAKED_EYE_LIMIT_SCALE, m)
    limit = naked_eye_limit_mag(bortle)
    dim = bortle_dim_factor(bortle)
    visible = (m <= limit) | bad
    frac = np.maximum(
        0.0, (NAKED_EYE_LIMIT_SCALE - m) / MAG_SPAN_TO_FULL_BRIGHT)
    r = (0.6 + 2.0 * frac ** (1.1 / 2.0)) * dim
    over = r > _SMALL_STAR_R
    # np.where 两支都会求值：负底数开方会发 RuntimeWarning（结果本身不被取用，
    # 但会让日志变噪）。先夹到 0 再开方，两支数值仍只在 over 处生效。
    r = np.where(
        over, _SMALL_STAR_R + np.maximum(0.0, r - _SMALL_STAR_R) ** 0.5, r)
    brightness = np.clip(frac, 0.0, 1.0)
    small = r < _SMALL_STAR_R
    brightness = np.where(
        small, np.minimum(1.0, brightness * (r ** 3) / _SMALL_STAR_R3),
        brightness)
    bloom = np.where(
        r >= _BLOOM_MIN_R,
        np.minimum(1.0, 0.4 + (r - _BLOOM_MIN_R) /
                   (_BLOOM_MAX_R - _BLOOM_MIN_R)),
        0.0)
    r = np.maximum(0.5, r)
    r = np.where(visible, r, 0.0)
    brightness = np.where(visible, brightness, 0.0)
    bloom = np.where(visible, bloom, 0.0)
    return r, brightness, bloom
