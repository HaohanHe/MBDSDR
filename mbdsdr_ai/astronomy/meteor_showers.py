# SPDX-License-Identifier: MIT
"""
主要流星雨目录（辐射点 / 活动期 / 峰值 ZHR）
================================================

用途：星图在**活动期内**画出流星雨辐射点，让「今晚有什么可看」在天空里
直接可见；不做流星通量建模、不预测小时流星数（那需要太阳黄经/质量分布
模型与实时观测反演，本模块不装懂）。

数据口径
--------
- 12 场主要流星雨，峰值日期 / 辐射点 J2000 赤经赤纬 / 峰值 ZHR /
  活动起止 / 母体，取 IMO（国际流星组织）公开年历的常用值；
- 峰值日期逐年有 ±1 天漂移，峰值 ZHR 也有年际变化，这里存的是**典型值**，
  界面上写「峰值 ZHR」而不是「今晚每小时 N 颗」；
- 活动期用**三角包络**近似（峰值为 1、活动期边界为 0），只用于决定
  「现在算不算活动期 / 离峰值多远」，不是 IMO 的 ZHR 剖面。

与 Stellarium 的关系：Stellarium 的 MeteorShowers 插件同样持有
「辐射点 + 活动窗口 + ZHR」静态表，按日期查活动项；本模块是同一思路的
独立实现（数据为公开星历常识，不复制其代码/数据文件）。
2026-11-17 用本机 Stellarium 克隆的 `MeteorShowers.json` 做独立对照，
修正三处赤纬笔误：GEM 32→33、STA 13→15、URS 75→76；其余 9 场一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple


@dataclass(frozen=True)
class MeteorShower:
    """一场流星雨的静态星历（典型值，非某一年实测）。"""

    code: str                 # IMO 三字母代号
    name_zh: str
    name_en: str
    ra_deg: float             # 辐射点 J2000 赤经（度）
    dec_deg: float            # 辐射点 J2000 赤纬（度）
    peak_month: int
    peak_day: int
    zhr_peak: int             # 峰值 ZHR（典型值）
    start_month: int
    start_day: int
    end_month: int
    end_day: int
    velocity_kms: int         # 典型地心速度
    parent: str               # 母体


#: 12 场主要流星雨（IMO 公开年历常用值；峰值逐年 ±1 天漂移）。
SHOWERS: Tuple[MeteorShower, ...] = (
    MeteorShower("QUA", "象限仪座流星雨", "Quadrantids",
                 230.0, 49.0, 1, 3, 110, 12, 28, 1, 12, 41, "2003 EH1"),
    MeteorShower("LYR", "天琴座流星雨", "Lyrids",
                 271.0, 34.0, 4, 22, 18, 4, 16, 4, 25, 49,
                 "C/1861 G1 (Thatcher)"),
    MeteorShower("ETA", "宝瓶座η流星雨", "Eta Aquariids",
                 338.0, -1.0, 5, 6, 50, 4, 19, 5, 28, 66, "1P/Halley"),
    MeteorShower("SDA", "南宝瓶座δ流星雨", "Southern Delta Aquariids",
                 340.0, -16.0, 7, 30, 25, 7, 12, 8, 23, 41,
                 "96P/Machholz"),
    MeteorShower("PER", "英仙座流星雨", "Perseids",
                 48.0, 58.0, 8, 12, 100, 7, 17, 8, 24, 59,
                 "109P/Swift-Tuttle"),
    MeteorShower("DRA", "天龙座流星雨", "Draconids",
                 262.0, 54.0, 10, 8, 10, 10, 6, 10, 10, 20,
                 "21P/Giacobini-Zinner"),
    MeteorShower("ORI", "猎户座流星雨", "Orionids",
                 95.0, 16.0, 10, 21, 20, 10, 2, 11, 7, 66, "1P/Halley"),
    MeteorShower("STA", "南金牛座流星雨", "Southern Taurids",
                 52.0, 15.0, 11, 5, 5, 9, 10, 11, 20, 27, "2P/Encke"),
    MeteorShower("NTA", "北金牛座流星雨", "Northern Taurids",
                 58.0, 22.0, 11, 12, 5, 10, 20, 12, 10, 29, "2P/Encke"),
    MeteorShower("LEO", "狮子座流星雨", "Leonids",
                 152.0, 22.0, 11, 17, 15, 11, 6, 11, 30, 71,
                 "55P/Tempel-Tuttle"),
    MeteorShower("GEM", "双子座流星雨", "Geminids",
                 112.0, 33.0, 12, 14, 150, 12, 4, 12, 17, 35,
                 "3200 Phaethon"),
    MeteorShower("URS", "小熊座流星雨", "Ursids",
                 217.0, 76.0, 12, 22, 10, 12, 17, 12, 26, 33, "8P/Tuttle"),
)

#: 低于这个活动强度（离峰值太远）就不在星图上画辐射点，避免整年满屏标记。
DRAW_MIN_ACTIVITY = 0.15

_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)


def day_of_year(month: int, day: int) -> int:
    """月日 -> 年积日（1 起，按非闰年 365 天）。"""
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(f"非法月日: {month}-{day}")
    return _CUM_DAYS[month - 1] + day


def _circular_delta(a: int, b: int, year_days: int = 365) -> int:
    """a-b 在年周期上的最短有符号差（跨年取小）。"""
    d = (a - b) % year_days
    return d - year_days if d > year_days // 2 else d


def activity_fraction(shower: MeteorShower, month: int, day: int) -> float:
    """活动包络：峰值 1.0，活动期边界 0.0，期外 0.0（三角近似）。

    跨年流星雨（象限仪座：12-28 → 1-12）按年周期取最短距离，不会在
    12-31/1-1 之间断成两段。
    """
    peak = day_of_year(shower.peak_month, shower.peak_day)
    start = day_of_year(shower.start_month, shower.start_day)
    end = day_of_year(shower.end_month, shower.end_day)
    today = day_of_year(month, day)

    d = _circular_delta(today, peak)
    # 半宽按「峰值到该侧边界」取；跨年时 start 在数值上更大，先归一到峰值左侧
    before = _circular_delta(peak, start)
    after = _circular_delta(end, peak)
    if before <= 0:
        before += 365
    if after <= 0:
        after += 365
    half = before if d < 0 else after
    if half <= 0:
        return 0.0
    frac = 1.0 - abs(d) / float(half)
    return max(0.0, min(1.0, frac))


def active_showers(at: datetime,
                   min_fraction: float = 0.0) -> List[Tuple[MeteorShower, float]]:
    """返回 `at` 当天活动的流星雨 [(雨, 活动强度)]，按强度降序。

    `at` 支持 aware/naive；naive 按 UTC 解释（本模块只关心日期）。
    """
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc)
    out = []
    for s in SHOWERS:
        f = activity_fraction(s, at.month, at.day)
        if f > 0.0 and f >= float(min_fraction):
            out.append((s, f))
    out.sort(key=lambda it: it[1], reverse=True)
    return out


def describe(shower: MeteorShower, fraction: float) -> str:
    """一句话文案：活动强度用文字档位，不把三角包络包装成精确流量。"""
    if fraction >= 0.85:
        band = "接近峰值"
    elif fraction >= 0.5:
        band = "活动较强"
    elif fraction >= DRAW_MIN_ACTIVITY:
        band = "活动期"
    else:
        band = "活动边缘"
    return f"{shower.name_zh} · {band} · 峰值 ZHR {shower.zhr_peak}"
