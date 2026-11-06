# SPDX-License-Identifier: MIT
"""晨昏天空亮度 → 极限星等压制（量级近似模型，显式标注未验证）。

为什么需要
----------
桌面星象仪原先只有一档布尔「白天门」：太阳高度 ≥ −0.833° 整层不画恒星，
一旦低于该值就按**夜空**极限星等（Bortle 3 → 6.5 等）满亮度画出来。
后果是每天晨昏两段各约 1 小时里画出一个物理上不可能的画面：
天空还是白蓝渐变（``atmosphere`` 的背景亮度在 −1° 时 0.89），
星点却已经满屏 6.5 等。真实晨昏是连续过程。

模型（三步，全部可复核）
------------------------
1. 天顶天空亮度 ``L(sun_alt)``：V 波段，单位 mag/arcsec²，按公开晨昏亮度
   曲线的**量级**取值（见 ``_SKY_BRIGHTNESS_TABLE``），−18° 及以下取无月
   夜值 21.6。
2. 背景限定探测：星与背景按 1:1 换算 —— 天空亮 Δm 等，能看见的最暗星也
   亮 Δm 等（固定探测阈值的背景限情形，标准光度学关系）。
3. 于是
       penalty      = max(0, L_night − L(sun_alt))
       limit_twilight = limit_magnitude(fov, bortle) − penalty

诚实口径（不要当成已验证的辐射传输模型）
----------------------------------------
- 表值是文献晨昏曲线的量级近似；气溶胶、臭氧、纬度、方位角（朝太阳一侧
  比背向亮得多）都会带来 ±1 等差异，本仓**没有**实测标定。
- 只用于显示层「画不画、画多暗」，不参与任何指向 / 定轨 / 过境结论。
- 太阳高度未知（历表未就绪）时 penalty 恒为 0，即维持夜空口径，不假装。
- 未做晨昏的**方位依赖**（真实晨昏天顶角处最暗、朝太阳方位最亮），
  本模型给的是天顶等效值。

参考文献口径：Krisciunas & Schaefer 1991, PASP 103, 1033（夜空亮度与月光）；
Patat et al. 2006, A&A 455, 385（天文晨昏天顶亮度序列）。
"""

from __future__ import annotations

import math
from typing import Optional

__all__ = [
    "SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2",
    "sky_brightness_mag_arcsec2",
    "twilight_limit_penalty_mag",
]

# 无月夜天顶亮度（V，mag/arcsec²）。Bortle 3 附近 21.6 是常用量级。
SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2: float = 21.6

# 太阳高度(度) -> 天顶天空亮度(V mag/arcsec²)。按高度降序，供线性插值。
# 量级来源见模块 docstring；不要在别处再复制一份。
_SKY_BRIGHTNESS_TABLE: tuple[tuple[float, float], ...] = (
    (10.0, 4.0),
    (5.0, 4.8),
    (2.0, 6.0),
    (0.0, 8.0),
    (-2.0, 10.5),
    (-4.0, 13.0),
    (-6.0, 15.0),
    (-8.0, 16.5),
    (-10.0, 17.8),
    (-12.0, 19.0),
    (-15.0, 20.5),
    # −18° = 天文晨昏结束，天空即无月夜：从这里起惩罚必须精确为 0，
    # 否则"天文夜"仍会比夜空暗 0.2 等，属于无来由的系统偏差。
    (-18.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2),
    (-90.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2),
)


def sky_brightness_mag_arcsec2(sun_alt_deg: Optional[float]) -> float:
    """太阳高度(度) → 天顶天空亮度(V mag/arcsec²)。未知/非有限 → 夜空值。

    分段线性、单调：太阳越高于地平，数值越小（越亮）。表外夹到端点。
    """
    if sun_alt_deg is None:
        return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2
    try:
        alt = float(sun_alt_deg)
    except (TypeError, ValueError):
        return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2
    if not math.isfinite(alt):
        return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2

    table = _SKY_BRIGHTNESS_TABLE
    if alt >= table[0][0]:
        return table[0][1]
    if alt <= table[-1][0]:
        return table[-1][1]
    for (a_hi, m_hi), (a_lo, m_lo) in zip(table, table[1:]):
        if a_lo <= alt <= a_hi:
            # a_hi > a_lo；m_hi 更小（更亮）
            t = (alt - a_lo) / (a_hi - a_lo)
            return m_lo + t * (m_hi - m_lo)
    return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2      # pragma: no cover


def twilight_limit_penalty_mag(sun_alt_deg: Optional[float]) -> float:
    """天空比无月夜亮多少等 → 极限星等要压低多少等（≥0，单调不减）。

    sun_alt ≥ −18° 之外返回 0；白天（太阳在地平上）会给出十几等的压制，
    足以让整层恒星自然消失，不必再靠布尔开关。
    """
    return max(0.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2
               - sky_brightness_mag_arcsec2(sun_alt_deg))
