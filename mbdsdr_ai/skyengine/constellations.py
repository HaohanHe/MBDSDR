# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/constellations.py — 星座数据层 (IAU 88 座 + 星官独立体系)
=============================================================================

本模块负责 **IAU 西方 88 星座**；中国星官（三垣二十八宿）在独立模块
``xingguan.py``，两套体系的数据文件、名称空间、标签与接口互不混用。

两套数据:

1. **EXTENSION_STARS** — BRIGHT_STARS 之外亮星扩员 (3.6 等以内优先),
   供星座连线端点引用。坐标 J2000 (度), 2026-11-02 经 HYG v4.1
   (CC BY-SA 4.0, astronexus) proper 名 + Bayer 名双路对拍吸附,
   并带 HYG 光谱型注记; dist_ly 取 HYG 视差距离 (缺省 0.0 = 未知,
   信息卡如实显 "—", 不编造)。BRIGHT_STARS 同口径吸附, 其中 Adhara/
   Naos/Alnair 等 14 颗历史坐标错误 (度/时混淆) 本次一并修正,
   锚点见 tests/test_sky_constellations.py::TestCatalogRegression。

2. **CONSTELLATION_LINES** — 旧版 27 座亮星骨架连线 (桌面端仍在用,
   保持兼容); **IAU 完整 88 座** 由 ``load_iau_constellations()`` 从
   ``data/constellations_iau.json`` 载入, 带来源/SHA/许可守卫。

星座连线来源与性质
------------------
IAU 只定义 88 个星座的 **名称与边界**，不定义连线图形。本模块的 88 座
连线来自 Stellarium ``modern_iau`` 天空文化（CC BY-SA 4.0；图形由
Stellarium 与 Sky & Telescope 合作制作并发布于 IAU 网站），属于
传统/民间约定的示意图形，**不是 IAU 官方定义**。数据文件 meta 中
``lines_are_iau_official=false`` 明确记录这一点。

中文名来源: 现有 ``data/constellation_culture.json``（27 座文化注记）
+ Stellarium 中文翻译（``po/.../zh_CN.po`` 的 IAU constellation name）。
缺名直接报错，不从记忆补写。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .stars import BRIGHT_STARS, BrightStar

# ---------------------------------------------------------------------------
# 星表扩员 (name, ra_deg, dec_deg, vmag, bv, dist_ly)
# 坐标已经 Hipparcos 子集最近邻校验 (开发工具, 见模块 docstring)
# ---------------------------------------------------------------------------
EXTENSION_STARS: List[BrightStar] = [
    BrightStar("Merak",   165.4602,   +56.3824,  2.34,  0.03,     79.7, "A1V"),  # A1V  UMa
    BrightStar("Phecda",   178.4575,   +53.6948,  2.41,  0.04,     83.2, "A0V SB"),  # A0V SB UMa
    BrightStar("Megrez",   183.8563,   +57.0326,  3.32,  0.08,     80.5, "A3Vvar"),  # A3Vvar UMa
    BrightStar("Kochab",   222.6766,   +74.1555,  2.07,  1.47,    130.9, "K4IIIvar"),  # K4IIIvar UMi
    BrightStar("Pherkad",   230.1822,   +71.8340,  3.00,  0.06,    486.8, "A3II-III"),  # A3II-III UMi
    BrightStar("Caph",     2.2933,   +59.1498,  2.28,  0.38,     54.7, "F2III-IV"),  # F2III-IV Cas
    BrightStar("Schedar",    10.1267,   +56.5373,  2.24,  1.17,    228.2, "K0II-IIIvar"),  # K0II-IIIvar Cas
    BrightStar("Gamma Cas",    14.1771,   +60.7167,  2.15, -0.05,    549.1, "B0IV:evar"),  # B0IV:evar Cas
    BrightStar("Ruchbah",    21.4532,   +60.2353,  2.66,  0.16,     99.4, "A5Vv SB"),  # A5Vv SB Cas
    BrightStar("Alderamin",   319.6445,   +62.5856,  2.45,  0.26,     49.0, "A7IV-V"),  # A7IV-V Cep
    BrightStar("Alfirk",   322.1649,   +70.5607,  3.23, -0.20,    685.2, "B2IIIv SB"),  # B2IIIv SB Cep
    BrightStar("Gamma Cep",   354.8373,   +77.6323,  3.21,  1.03,     46.0, "K1IV"),  # K1IV Cep
    BrightStar("Almach",    30.9748,   +42.3297,  2.10,  1.37,    393.0, "B8V"),  # B8V  And
    BrightStar("Gamma Per",    46.1991,   +53.5064,  2.91,  0.72,    243.2, "G8III+..."),  # G8III+... Per
    BrightStar("Epsilon Per",    59.4635,   +40.0102,  2.90, -0.20,    638.3, "B0.5V"),  # B0.5V Per
    BrightStar("Theta Aur",    89.9303,   +37.2126,  2.65, -0.08,    165.6, "A0p Si"),  # A0p Si Aur
    BrightStar("Iota Aur",    74.2484,   +33.1661,  2.69,  1.49,    493.4, "K3IIvar"),  # K3IIvar Aur
    BrightStar("Epsilon Aur",    75.4922,   +43.8233,  3.03,  0.54,   2131.7, "F0Ia"),  # F0Ia Aur
    BrightStar("Zeta Tau",    84.4112,   +21.1425,  2.97, -0.15,    445.0, "B4IIIp"),  # B4IIIp Tau
    BrightStar("Alcyone",    56.8712,   +24.1051,  2.85, -0.09,    403.2, "B7III"),  # B7III Tau
    BrightStar("Mintaka",    83.0017,    -0.2991,  2.25, -0.17,    692.5, "O9.5II"),  # O9.5II Ori
    BrightStar("Saiph",    86.9391,    -9.6696,  2.07, -0.17,    647.1, "B0.5Iavar"),  # B0.5Iavar Ori
    BrightStar("Mebsuta",   100.9830,   +25.1311,  3.06,  1.38,    845.0, "A3mA6-A9"),  # A3mA6-A9 Gem
    BrightStar("Mu Gem",    95.7401,   +22.5136,  2.87,  1.62,    231.6, "M3IIIvar"),  # M3IIIvar Gem
    BrightStar("Wasat",   110.0307,   +21.9823,  3.50,  0.37,     60.5, "F0IV..."),  # F0IV... Gem
    BrightStar("Gomeisa",   111.7877,    +8.2893,  2.89, -0.10,    161.7, "B8Vvar"),  # B8Vvar CMi
    BrightStar("Mirzam",    95.6749,   -17.9559,  1.98, -0.24,    492.7, "B1II/III"),  # B1II/III CMa
    BrightStar("Adhafera",   154.1726,   +23.4173,  3.43,  0.31,    274.1, "F0III"),  # F0III Leo
    BrightStar("Epsilon Leo",   146.4628,   +23.7743,  2.97,  0.81,    246.7, "G0II"),  # G0II Leo
    BrightStar("Zosma",   168.5271,   +20.5237,  2.56,  0.13,     58.4, "A4V"),  # A4V  Leo
    BrightStar("Chertan",   168.5600,   +15.4296,  3.33, -0.00,    165.1, "A2V"),  # A2V  Leo
    BrightStar("Vindemiatrix",   195.5442,   +10.9591,  2.85,  0.93,    109.6, "G8IIIvar"),  # G8IIIvar Vir
    BrightStar("Porrima",   190.4152,    -1.4494,  2.74,  0.37,     38.1, "F0V+..."),  # F0V+... Vir
    BrightStar("Minelauva",   193.9009,    +3.3975,  3.39,  1.57,    198.4, "M3III"),  # M3III Vir
    BrightStar("Zeta Vir",   203.6733,    -0.5958,  3.38,  0.11,     74.1, "A3V"),  # A3V  Vir
    BrightStar("Zubenelgenubi",   222.7197,   -16.0418,  2.75,  0.15,     75.8, "A3IV"),  # A3IV Lib
    BrightStar("Zubeneschamali",   229.2517,    -9.3829,  2.61, -0.07,    185.1, "B8V"),  # B8V  Lib
    BrightStar("Graffias",   241.0916,   -11.3746,  4.84,  0.45,     79.4, "F6 IV-V"),  # F6 IV-V Sco
    BrightStar("Dschubba",   240.0834,   -22.6217,  2.29, -0.12,    491.2, "B0.2IV"),  # B0.2IV Sco
    BrightStar("Pi Sco",   239.7130,   -26.1141,  2.89, -0.18,    585.6, "B1V + B2V"),  # B1V + B2V Sco
    BrightStar("Sigma Sco",   245.2971,   -25.5928,  2.90,  0.30,    696.9, "B1III"),  # B1III Sco
    BrightStar("Tau Sco",   248.9706,   -28.2160,  2.82, -0.21,    474.1, "B0V"),  # B0V  Sco
    BrightStar("Epsilon Sco",   252.5412,   -34.2932,  2.29,  1.14,     63.7, "K2IIIb"),  # K2IIIb Sco
    BrightStar("Mu1 Sco",   252.9676,   -38.0474,  3.00, -0.20,    501.0, "B1.5IV + B"),  # B1.5IV + B Sco
    BrightStar("Lesath",   262.6910,   -37.2958,  2.70, -0.18,    576.2, "B2IV"),  # B2IV Sco
    BrightStar("Nunki",   283.8163,   -26.2967,  2.05, -0.13,    227.8, "B2.5V"),  # B2.5V Sgr
    BrightStar("Kaus Media",   275.2485,   -29.8281,  2.72,  1.38,    347.7, "K3III"),  # K3III Sgr
    BrightStar("Kaus Borealis",   276.9927,   -25.4217,  2.82,  1.02,     78.2, "K1IIIb"),  # K1IIIb Sgr
    BrightStar("Ascella",   285.6530,   -29.8801,  2.60,  0.06,     88.2, "A3IV"),  # A3IV Sgr
    BrightStar("Albaldah",   287.4410,   -21.0236,  2.88,  0.38,    509.6, "F2II/III"),  # F2II/III Sgr
    BrightStar("Phi Sgr",   281.4141,   -26.9908,  3.17, -0.11,    239.3, "B8.5III"),  # B8.5III Sgr
    BrightStar("Sheliak",   282.5200,   +33.3627,  3.52,  0.00,    962.1, "A8:V comp SB"),  # A8:V comp SB Lyr
    BrightStar("Sulafat",   284.7359,   +32.6896,  3.25, -0.05,    620.1, "B9III"),  # B9III Lyr
    BrightStar("Sadr",   305.5571,   +40.2567,  2.23,  0.67,   1832.3, "F8Ib"),  # F8Ib Cyg
    BrightStar("Gienah Cyg",   311.5527,   +33.9703,  2.48,  1.02,     72.7, "K0III"),  # K0III Cyg
    BrightStar("Fawaris",   296.2436,   +45.1308,  2.86, -0.00,    165.0, "B9.5III"),  # B9.5III Cyg
    BrightStar("Albireo",   292.6803,   +27.9597,  3.05,  1.09,    434.3, "K3II+..."),  # K3II+... Cyg
    BrightStar("Sadalmelik",   331.4460,    -0.3199,  2.95,  0.97,    523.5, "G2Ib"),  # G2Ib Aqr
    BrightStar("Sadalsuud",   322.8897,    -5.5712,  2.90,  0.83,    537.3, "G0Ib"),  # G0Ib Aqr
    BrightStar("Skat",   343.6626,   -15.8208,  3.27,  0.07,    160.6, "A3V"),  # A3V  Aqr
    BrightStar("Deneb Algedi",   326.7602,   -16.1273,  2.85,  0.18,     38.7, "A5mF2 (IV)"),  # A5mF2 (IV) Cap
    BrightStar("Dabih",   305.2528,   -14.7814,  3.05,  0.79,    326.8, "A5:n"),  # A5:n Cap
    BrightStar("Markab",   346.1902,   +15.2053,  2.49, -0.00,    133.3, "B9.5III"),  # B9.5III Peg
    BrightStar("Scheat",   345.9435,   +28.0828,  2.44,  1.66,    196.0, "M2II-IIIvar"),  # M2II-IIIvar Peg
    BrightStar("Algenib",     3.3090,   +15.1836,  2.83, -0.19,    391.5, "B2IV"),  # B2IV Peg
    BrightStar("Enif",   326.0465,    +9.8750,  2.38,  1.52,    689.5, "K2Ibvar"),  # K2Ibvar Peg
    BrightStar("Homam",   340.3655,   +10.8314,  3.41, -0.09,    204.4, "B8.5V"),  # B8.5V Peg
    BrightStar("Kornephoros",   247.5550,   +21.4896,  2.78,  0.95,    139.1, "G8III"),  # G8III Her
    BrightStar("Zeta Her",   250.3217,   +31.6027,  2.81,  0.65,     35.0, "F9IV"),  # F9IV Her
    BrightStar("Eta Her",   250.7240,   +38.9223,  3.48,  0.92,    108.6, "G8III-IV"),  # G8III-IV Her
    BrightStar("Pi Her",   258.7618,   +36.8092,  3.16,  1.44,    376.6, "K3IIvar"),  # K3IIvar Her
    BrightStar("Rasalgethi",   258.6619,   +14.3903,  2.78,  1.16,    359.6, "M5IIvar"),  # M5IIvar Her
    BrightStar("Rasalhague",   263.7336,   +12.5600,  2.08,  0.15,     48.6, "A5III"),  # A5III Oph
    BrightStar("Cebalrai",   265.8681,    +4.5673,  2.76,  1.17,     81.8, "K2III"),  # K2III Oph
    BrightStar("Sabik",   257.5945,   -15.7249,  2.43,  0.06,     88.4, "A2.5Va"),  # A2.5Va Oph
    BrightStar("Delta Oph",   243.5864,    -3.6943,  2.73,  1.58,    171.1, "M1III"),  # M1III Oph
    BrightStar("Epsilon Oph",   244.5804,    -4.6925,  3.23,  0.97,    106.4, "G8III"),  # G8III Oph
    BrightStar("Zeta Oph",   249.2897,   -10.5671,  2.54,  0.04,    366.1, "O9.5V"),  # O9.5V Oph
    BrightStar("Izar",   221.2468,   +27.0742,  2.35,  0.97,    202.6, "A0"),  # A0   Boo
    BrightStar("Muphrid",   208.6712,   +18.3977,  2.68,  0.58,     37.2, "G0IV"),  # G0IV Boo
    BrightStar("Seginus",   218.0195,   +38.3083,  3.04,  0.19,     86.8, "A7IIIvar"),  # A7IIIvar Boo
    BrightStar("Nekkar",   225.4865,   +40.3906,  3.49,  0.96,    225.2, "G8III"),  # G8III Boo
    BrightStar("Rho Boo",   217.9575,   +30.3714,  3.57,  1.30,    160.1, "K3III"),  # K3III Boo
    BrightStar("Delta Boo",   228.8756,   +33.3148,  3.46,  0.96,    121.8, "G8III"),  # G8III Boo
    BrightStar("Delta Cru",   183.7864,   -58.7489,  2.79, -0.19,    345.1, "B2IV"),  # B2IV Cru
    BrightStar("Rigil Kent",   219.9115,   -60.8340, -0.01,  0.71,      4.3, "G2V"),  # G2V  Cen
    BrightStar("Menkent",   211.6709,   -36.3700,  2.06,  1.01,     58.8, "K0IIIb"),  # K0IIIb Cen
]

# ---------------------------------------------------------------------------
# 传统星座连线 (骨架示意, 端点 = 星名). 键 = IAU 三字母缩写.
# ---------------------------------------------------------------------------
CONSTELLATION_LINES: Dict[str, List[Tuple[str, str]]] = {
    "Ori": [("Betelgeuse", "Bellatrix"), ("Betelgeuse", "Alnitak"),
            ("Bellatrix", "Mintaka"), ("Alnitak", "Alnilam"),
            ("Alnilam", "Mintaka"), ("Alnitak", "Saiph"),
            ("Mintaka", "Rigel")],
    "Gem": [("Castor", "Pollux"), ("Castor", "Mebsuta"),
            ("Mebsuta", "Mu Gem"), ("Pollux", "Wasat"),
            ("Wasat", "Alhena")],
    "Tau": [("Elnath", "Zeta Tau"), ("Aldebaran", "Elnath"),
            ("Aldebaran", "Zeta Tau")],
    "CMa": [("Sirius", "Mirzam"), ("Sirius", "Wezen"), ("Wezen", "Adhara")],
    "CMi": [("Procyon", "Gomeisa")],
    "UMa": [("Dubhe", "Merak"), ("Merak", "Phecda"), ("Phecda", "Megrez"),
            ("Megrez", "Alioth"), ("Alioth", "Mizar"), ("Mizar", "Alkaid")],
    "UMi": [("Polaris", "Kochab"), ("Kochab", "Pherkad"),
            ("Pherkad", "Polaris")],
    "Cas": [("Caph", "Schedar"), ("Schedar", "Gamma Cas"),
            ("Gamma Cas", "Ruchbah"), ("Ruchbah", "Caph")],
    "Cep": [("Alderamin", "Alfirk"), ("Alfirk", "Gamma Cep"),
            ("Gamma Cep", "Alderamin")],
    "And": [("Alpheratz", "Mirach"), ("Mirach", "Almach")],
    "Per": [("Mirfak", "Gamma Per"), ("Gamma Per", "Epsilon Per"),
            ("Epsilon Per", "Algol")],
    "Aur": [("Capella", "Epsilon Aur"), ("Epsilon Aur", "Menkalinan"),
            ("Menkalinan", "Theta Aur"), ("Theta Aur", "Iota Aur"),
            ("Iota Aur", "Capella")],
    "Leo": [("Regulus", "Algieba"), ("Algieba", "Adhafera"),
            ("Adhafera", "Epsilon Leo"), ("Epsilon Leo", "Regulus"),
            ("Algieba", "Zosma"), ("Zosma", "Chertan"),
            ("Chertan", "Denebola"), ("Zosma", "Denebola")],
    "Vir": [("Porrima", "Vindemiatrix"), ("Vindemiatrix", "Minelauva"),
            ("Minelauva", "Spica"), ("Porrima", "Zeta Vir"),
            ("Zeta Vir", "Spica")],
    "Lib": [("Zubenelgenubi", "Zubeneschamali")],
    "Sco": [("Graffias", "Dschubba"), ("Dschubba", "Pi Sco"),
            ("Pi Sco", "Sigma Sco"), ("Dschubba", "Antares"),
            ("Antares", "Sigma Sco"), ("Sigma Sco", "Tau Sco"),
            ("Tau Sco", "Epsilon Sco"), ("Epsilon Sco", "Mu1 Sco"),
            ("Mu1 Sco", "Shaula"), ("Shaula", "Lesath")],
    "Sgr": [("Nunki", "Kaus Borealis"), ("Kaus Borealis", "Kaus Media"),
            ("Kaus Media", "Kaus Australis"), ("Kaus Media", "Ascella"),
            ("Ascella", "Nunki"), ("Kaus Borealis", "Albaldah"),
            ("Albaldah", "Phi Sgr")],
    "Lyr": [("Vega", "Sheliak"), ("Sheliak", "Sulafat"),
            ("Sulafat", "Vega")],
    "Cyg": [("Sadr", "Deneb"), ("Sadr", "Albireo"), ("Sadr", "Fawaris"),
            ("Sadr", "Gienah Cyg")],
    "Aqr": [("Sadalmelik", "Sadalsuud"), ("Sadalsuud", "Skat")],
    "Cap": [("Deneb Algedi", "Dabih")],
    "Peg": [("Markab", "Scheat"), ("Scheat", "Alpheratz"),
            ("Alpheratz", "Algenib"), ("Algenib", "Markab"),
            ("Markab", "Enif")],
    "Her": [("Eta Her", "Pi Her"), ("Pi Her", "Zeta Her"),
            ("Zeta Her", "Kornephoros"), ("Kornephoros", "Rasalgethi")],
    "Oph": [("Rasalhague", "Sabik"), ("Rasalhague", "Cebalrai"),
            ("Cebalrai", "Epsilon Oph"), ("Epsilon Oph", "Delta Oph"),
            ("Delta Oph", "Sabik"), ("Sabik", "Zeta Oph")],
    "Boo": [("Arcturus", "Muphrid"), ("Arcturus", "Izar"),
            ("Izar", "Seginus"), ("Seginus", "Nekkar"),
            ("Nekkar", "Rho Boo"), ("Rho Boo", "Delta Boo"),
            ("Delta Boo", "Arcturus")],
    "Cru": [("Gacrux", "Acrux"), ("Acrux", "Mimosa"),
            ("Mimosa", "Delta Cru"), ("Delta Cru", "Gacrux")],
    "Cen": [("Menkent", "Rigil Kent"), ("Rigil Kent", "Hadar"),
            ("Hadar", "Acrux")],
}

# IAU 缩写 -> 通用英文全名 (facts; 中文名在 data/constellation_culture.json)
CONSTELLATION_NAMES: Dict[str, str] = {
    "Ori": "Orion", "Gem": "Gemini", "Tau": "Taurus",
    "CMa": "Canis Major", "CMi": "Canis Minor",
    "UMa": "Ursa Major", "UMi": "Ursa Minor",
    "Cas": "Cassiopeia", "Cep": "Cepheus", "And": "Andromeda",
    "Per": "Perseus", "Aur": "Auriga", "Leo": "Leo", "Vir": "Virgo",
    "Lib": "Libra", "Sco": "Scorpius", "Sgr": "Sagittarius",
    "Lyr": "Lyra", "Cyg": "Cygnus", "Aqr": "Aquarius", "Cap": "Capricornus",
    "Peg": "Pegasus", "Her": "Hercules", "Oph": "Ophiuchus",
    "Boo": "Bootes", "Cru": "Crux", "Cen": "Centaurus",
}


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------
def merged_star_index() -> Dict[str, BrightStar]:
    """BRIGHT_STARS + EXTENSION_STARS 按名合并索引 (重名以后者报错为妥)。"""
    idx: Dict[str, BrightStar] = {}
    for s in BRIGHT_STARS:
        idx[s.name] = s
    for s in EXTENSION_STARS:
        if s.name in idx:
            raise ValueError(f"duplicate star in extension: {s.name}")
        idx[s.name] = s
    return idx


def constellation_segments() -> Dict[str, List[Tuple[BrightStar, BrightStar]]]:
    """缩写 -> [(starA, starB)] 连线端点星表条目。"""
    idx = merged_star_index()
    out: Dict[str, List[Tuple[BrightStar, BrightStar]]] = {}
    for abbr, pairs in CONSTELLATION_LINES.items():
        segs: List[Tuple[BrightStar, BrightStar]] = []
        for a, b in pairs:
            sa, sb = idx.get(a), idx.get(b)
            if sa is None or sb is None:
                raise KeyError(f"{abbr}: unknown star {a!r} or {b!r}")
            segs.append((sa, sb))
        out[abbr] = segs
    return out


# ---------------------------------------------------------------------------
# 中文名/文化注记 (皮进配置, data/constellation_culture.json; 缺键回退拉丁名)
# ---------------------------------------------------------------------------
_CULTURE_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def _load_constellation_culture() -> Dict[str, Dict[str, str]]:
    global _CULTURE_CACHE
    if _CULTURE_CACHE is None:
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))),
            "data", "constellation_culture.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            _CULTURE_CACHE = {k: v for k, v in raw.items()
                              if not k.startswith("_")}
        except (OSError, ValueError):
            _CULTURE_CACHE = {}
    return _CULTURE_CACHE


def constellation_culture(abbr: str) -> Optional[Dict[str, str]]:
    """取星座中文名/注记; 无收录返回 None（不编造）。

    旧版 27 座继续用 ``constellation_culture.json`` 的人工注记；其余 IAU
    星座从 88 座数据文件取中文名（注记留空，不合并两套体系）。
    """
    c = _load_constellation_culture().get(str(abbr))
    if c:
        return c
    con = load_iau_constellations().by_abbr.get(str(abbr))
    if con is None:
        return None
    return {"zh": con.zh_name}


def constellation_label(abbr: str) -> str:
    """星座标签: 有中文用 '中文 Latin'，否则回退拉丁全名/缩写。"""
    c = constellation_culture(abbr)
    if abbr in CONSTELLATION_NAMES:
        latin = CONSTELLATION_NAMES[abbr]
    else:
        con = load_iau_constellations().by_abbr.get(str(abbr))
        latin = con.latin_name if con else abbr
    return f"{c['zh']} {latin}" if c else latin


def constellation_anchor(abbr: str) -> Optional[BrightStar]:
    """星座名标签锚点: 该座最亮星 (常识级位置, 不精确到几何中心)。"""
    idx = merged_star_index()
    stars = {name for a, b in CONSTELLATION_LINES.get(abbr, []) for name in (a, b)}
    best: Optional[BrightStar] = None
    for name in stars:
        s = idx[name]
        if best is None or s.vmag < best.vmag:
            best = s
    return best


# ===========================================================================
# IAU 88 座完整数据（独立于旧版 27 座骨架）
# ===========================================================================
SYSTEM_IAU = "iau_88"
SYSTEM_CHINESE = "chinese_xingguan"

_IAU_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "constellations_iau.json")


class ConstellationMetadataError(ValueError):
    """星座/星官数据 meta 缺字段或格式不合法（来源、许可、SHA 不完整）。"""


class ConstellationIntegrityError(ValueError):
    """数据内容与 meta.data_sha256 不一致（疑似手改数据未改 meta）。"""


_REQUIRED_META_KEYS = (
    "schema", "system_id", "source", "source_url", "source_license",
    "source_license_url", "attribution", "source_file_sha256",
    "data_sha256",
)


def _sha256_hex_ok(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def payload_sha256(payload: dict) -> str:
    """规范化 JSON SHA-256；与生成脚本完全同一口径。"""
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(clone.get("meta"), dict):
        clone["meta"].pop("data_sha256", None)
    blob = json.dumps(clone, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validate_data_payload(payload: dict, *, expected_system: str,
                          required: Optional[Tuple[str, ...]] = None) -> dict:
    """校验 meta 来源/许可/SHA 与数据 SHA；失败抛错，不静默加载。"""
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ConstellationMetadataError("数据 meta 不是 JSON 对象")
    keys = tuple(required or _REQUIRED_META_KEYS)
    missing = [k for k in keys if not meta.get(k)]
    if missing:
        raise ConstellationMetadataError(f"数据 meta 缺少必需字段: {missing}")
    if str(meta["system_id"]) != expected_system:
        raise ConstellationMetadataError(
            f"数据体系不匹配: {meta['system_id']!r} != {expected_system!r}")
    for key in ("source_url", "source_license_url"):
        if not str(meta[key]).startswith(("https://", "http://")):
            raise ConstellationMetadataError(f"meta.{key} 不是有效 URL")
    if "source_file_sha256" in keys and not _sha256_hex_ok(
            meta.get("source_file_sha256")):
        raise ConstellationMetadataError(
            "meta.source_file_sha256 不是 64 位 SHA-256")
    for name, info in (meta.get("source_files") or {}).items():
        if not isinstance(info, dict) or not _sha256_hex_ok(info.get("sha256")):
            raise ConstellationMetadataError(
                f"meta.source_files.{name}.sha256 不是 64 位 SHA-256")
    if not _sha256_hex_ok(meta.get("data_sha256")):
        raise ConstellationMetadataError("meta.data_sha256 不是 64 位 SHA-256")
    if payload_sha256(payload) != meta["data_sha256"]:
        raise ConstellationIntegrityError(
            "数据 SHA-256 与 meta.data_sha256 不一致（疑似手改数据）")
    return meta


def _load_json(path: str, *, expected_system: str,
               required: Optional[Tuple[str, ...]] = None) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        raise ConstellationMetadataError(f"数据文件不是合法 JSON: {path}") from exc
    validate_data_payload(payload, expected_system=expected_system,
                          required=required)
    return payload


@dataclass(frozen=True)
class ConstellationStar:
    """IAU 星座连线端点（来自 HYG v4.1，J2000 度）。"""
    hip: int
    hyg_id: int
    proper: str
    bayer: str
    ra_deg: float
    dec_deg: float
    vmag: float
    con: str
    in_catalog: bool

    @property
    def label(self) -> str:
        return self.proper or self.bayer or f"HIP {self.hip}"


@dataclass(frozen=True)
class IAUConstellation:
    """一个 IAU 星座：官方拉丁名/缩写/中文名 + 示意连线。"""
    abbr: str
    latin_name: str
    english_name: str
    zh_name: str
    zh_source: str
    lines: Tuple[Tuple[ConstellationStar, ...], ...]
    stars: Tuple[ConstellationStar, ...]
    star_count: int
    segment_count: int
    original_segment_count: int
    dropped_segment_count: int
    unresolved_hips: Tuple[int, ...]
    anchor_hyg_id: Optional[int]

    def segments(self) -> Tuple[Tuple[ConstellationStar, ConstellationStar], ...]:
        out: List[Tuple[ConstellationStar, ConstellationStar]] = []
        for poly in self.lines:
            for a, b in zip(poly, poly[1:]):
                out.append((a, b))
        return tuple(out)

    @property
    def label(self) -> str:
        return f"{self.zh_name} {self.latin_name}"

    def anchor(self) -> Optional[ConstellationStar]:
        if self.anchor_hyg_id is None:
            return None
        for s in self.stars:
            if s.hyg_id == self.anchor_hyg_id:
                return s
        return None


class IAUConstellationSet:
    """88 座只读索引；键为 IAU 三字母缩写。"""

    def __init__(self, meta: Dict[str, Any],
                 constellations: Tuple[IAUConstellation, ...]):
        self.meta = meta
        self.constellations = constellations
        self.by_abbr: Dict[str, IAUConstellation] = {
            c.abbr: c for c in constellations}

    def __len__(self) -> int:
        return len(self.constellations)

    def __iter__(self):
        return iter(self.constellations)

    def __getitem__(self, abbr: str) -> IAUConstellation:
        return self.by_abbr[abbr]

    def get(self, abbr: str) -> Optional[IAUConstellation]:
        return self.by_abbr.get(abbr)


def _parse_star_ref(raw: dict) -> ConstellationStar:
    return ConstellationStar(
        hip=int(raw["hip"]), hyg_id=int(raw["hyg_id"]),
        proper=str(raw.get("proper") or ""), bayer=str(raw.get("bayer") or ""),
        ra_deg=float(raw["ra_deg"]), dec_deg=float(raw["dec_deg"]),
        vmag=float(raw["vmag"]), con=str(raw.get("con") or ""),
        in_catalog=bool(raw.get("in_catalog", False)),
    )


@lru_cache(maxsize=1)
def load_iau_constellations(path: Optional[str] = None) -> IAUConstellationSet:
    """加载 IAU 88 座数据；meta/SHA 不合法时抛错（不静默降级）。"""
    payload = _load_json(path or _IAU_DATA_PATH, expected_system=SYSTEM_IAU)
    meta = payload["meta"]
    if bool(meta.get("lines_are_iau_official")):
        raise ConstellationMetadataError(
            "meta.lines_are_iau_official 必须为 false：星座连线不是 IAU 官方定义")
    rows = payload.get("constellations") or []
    if len(rows) != 88:
        raise ConstellationIntegrityError(f"IAU 星座数不是 88: {len(rows)}")
    abbrs = [r["abbr"] for r in rows]
    if len(set(abbrs)) != 88:
        raise ConstellationIntegrityError("IAU 缩写存在重复")
    if int(meta["constellation_count"]) != 88:
        raise ConstellationIntegrityError("meta.constellation_count 与数据不一致")

    out: List[IAUConstellation] = []
    for row in rows:
        lines = tuple(tuple(_parse_star_ref(s) for s in poly)
                      for poly in row.get("lines") or ())
        for poly in lines:
            if len(poly) < 2:
                raise ConstellationIntegrityError(
                    f"{row['abbr']}: 连线折线段少于 2 个端点")
        stars = tuple(_parse_star_ref(s) for s in row.get("stars") or ())
        if not row.get("zh_name"):
            raise ConstellationIntegrityError(f"{row['abbr']}: 缺中文名")
        out.append(IAUConstellation(
            abbr=str(row["abbr"]), latin_name=str(row["latin_name"]),
            english_name=str(row.get("english_name") or row["latin_name"]),
            zh_name=str(row["zh_name"]), zh_source=str(row.get("zh_source") or ""),
            lines=lines, stars=stars,
            star_count=int(row.get("star_count", len(stars))),
            segment_count=int(row.get("segment_count", 0)),
            original_segment_count=int(row.get("original_segment_count", 0)),
            dropped_segment_count=int(row.get("dropped_segment_count", 0)),
            unresolved_hips=tuple(int(h) for h in row.get("unresolved_hips") or ()),
            anchor_hyg_id=(int(row["anchor_hyg_id"])
                           if row.get("anchor_hyg_id") is not None else None),
        ))
    return IAUConstellationSet(meta, tuple(out))


def get_iau_constellations() -> IAUConstellationSet:
    return load_iau_constellations()


def iau_constellation_segments(
        abbr: str) -> Tuple[Tuple[ConstellationStar, ConstellationStar], ...]:
    con = load_iau_constellations().by_abbr.get(str(abbr))
    if con is None:
        raise KeyError(f"未知 IAU 星座缩写: {abbr!r}")
    return con.segments()


def iau_constellation_label(abbr: str, *, with_system_tag: bool = False) -> str:
    con = load_iau_constellations().by_abbr.get(str(abbr))
    if con is None:
        raise KeyError(f"未知 IAU 星座缩写: {abbr!r}")
    label = con.label
    return f"[IAU] {label}" if with_system_tag else label


def iau_constellation_anchor(abbr: str) -> Optional[ConstellationStar]:
    """88 座中任一座的最亮连线端点；未知缩写返回 None（不编造）。"""
    con = load_iau_constellations().by_abbr.get(str(abbr))
    return con.anchor() if con else None


# ---------------------------------------------------------------------------
# 体系标识 / 并列图层（同时打开时每层都带 system_id + 显示名）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConstellationLayer:
    """一套天空文化体系的只读视图；渲染方必须显示 ``display_name``/``tag``。"""
    system_id: str
    display_name: str
    tag: str
    source: str
    source_license: str
    items: Tuple[Any, ...]

    def keys(self) -> Tuple[str, ...]:
        return tuple(
            getattr(i, "abbr", None) or getattr(i, "key", "") for i in self.items)


def system_ids() -> Tuple[str, str]:
    return (SYSTEM_IAU, SYSTEM_CHINESE)


def system_info(system_id: str) -> Dict[str, Any]:
    """返回体系元数据；两套体系各自读自己的数据文件，互不污染。"""
    if system_id == SYSTEM_IAU:
        ds = load_iau_constellations()
        return {
            "system_id": SYSTEM_IAU,
            "display_name_zh": ds.meta.get("system_name_zh", "IAU 88 星座"),
            "display_name_en": ds.meta.get("system_name_en", "IAU 88 Constellations"),
            "tag": "IAU",
            "source": ds.meta.get("source", ""),
            "source_url": ds.meta.get("source_url", ""),
            "source_license": ds.meta.get("source_license", ""),
            "lines_are_iau_official": False,
            "count": len(ds),
        }
    if system_id == SYSTEM_CHINESE:
        from . import xingguan  # 延迟导入，避免两套体系循环依赖
        return xingguan.system_info()
    raise KeyError(f"未知天空文化体系: {system_id!r}")


def constellation_layer(system_id: str) -> ConstellationLayer:
    if system_id == SYSTEM_IAU:
        ds = load_iau_constellations()
        return ConstellationLayer(
            system_id=SYSTEM_IAU,
            display_name=ds.meta.get("system_name_zh", "IAU 88 星座"),
            tag="IAU",
            source=ds.meta.get("source", ""),
            source_license=ds.meta.get("source_license", ""),
            items=ds.constellations,
        )
    if system_id == SYSTEM_CHINESE:
        from . import xingguan
        return xingguan.xingguan_layer()
    raise KeyError(f"未知天空文化体系: {system_id!r}")


def overlay_layers(
        systems: Optional[Tuple[str, ...]] = None) -> List[ConstellationLayer]:
    """同时打开多套体系；每层保留独立 ``system_id`` 与显示名，禁止合并名称。

    界面层必须为每层显示 ``display_name``（或 ``tag``）徽标；本函数只是
    并列返回，不做任何跨体系同名合并。
    """
    ids = systems or system_ids()
    seen = set()
    layers: List[ConstellationLayer] = []
    for sid in ids:
        if sid in seen:
            continue
        if sid not in system_ids():
            raise KeyError(f"未知天空文化体系: {sid!r}")
        seen.add(sid)
        layers.append(constellation_layer(sid))
    return layers

