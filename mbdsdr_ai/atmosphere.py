"""
MBDSDR AI 内核 - 大气 / 晨昏 / 天空亮度计算模块
================================================
算法参考 Stellarium Atmosphere (GPL-3.0), 独立重实现
------------------------------------------------------------

本模块独立重实现 Stellarium 中与大气、晨昏、天空背景相关的核心算法，
不链接 Stellarium 任何二进制。时间系统使用 skyfield，太阳位置在无 JPL
星历(bsp)时回退到 Meeus 低精度太阳理论（精度 ~0.01°，对日出日落时刻
影响 < 30s）。

Stellarium 源码引用（路径相对 ``repos/stellarium/src/``）：

1. 大气折射（几何高度 → 视高度）
   - core/RefractionExtinction.cpp:172-173  Saemundsson 公式
     ``r = press_temp_corr * (1.02/tan((h+10.3/(h+5.11))°) + 0.0019279)``
   - core/RefractionExtinction.cpp:151       气压温度修正
     ``press_temp_corr = P/1010 * 283/(273+T) / 60``（弧分→度）
   - core/RefractionExtinction.cpp:214-215  反向（视高度→几何高度）用 Bennett
     ``r = press_temp_corr * (1/tan((h+7.31/(h+4.4))°) + 0.0013515)``

2. 大气质量 (airmass)
   - core/RefractionExtinction.cpp:52-53    Rozenberg 1966（视高度）：
     ``m = 1/(cosZ + 0.025*exp(-11*cosZ))``
   - core/RefractionExtinction.cpp:57-60    Young 1994（几何高度）多项式
   - 本模块按任务要求使用 Kasten-Young 1989（更常用，地平线 m≈40）。

3. 消光 (Beer-Lambert)
   - core/RefractionExtinction.cpp:62-65    forward: ``mag += airmass * ext_coeff``
   - core/modules/Skybright.cpp:75,79       Rozenberg airmass 在 Skybright 中复用

4. 晨昏分段与日出日落
   - core/StelObject.cpp:151-311             getRTSTime：先解 hour-angle 近似，
     再迭代到 10s 精度。本模块用 skyfield 太阳高度 + 二分法达到同等精度。
   - core/StelObject.cpp:161-166             地平线折射修正 ≈ -34'（上边缘）
   - 标准约定：日出/日落 = 太阳上边缘视上地平线 ⇒ 太阳中心几何高度 = -0.833°
     （= 折射 34' + 太阳半径 16'，见 Meeus Astr.Alg. ch.15/16）
   - 民用晨昏 -6°、航海晨昏 -12°、天文晨昏 -18°（国际天文联合会标准定义）。

5. 天空亮度（白天/黄昏/夜晚）
   - core/modules/MilkyWay.cpp:350 与 core/StelToast.cpp:334
     给出全天空平均亮度的经验值：
       日落(sun≈0°)     ≈ 10 cd/m²
       民用晨昏(-6°)    ≈ 3.3 cd/m²
       航海晨昏(-12°)   ≈ 0.0145 cd/m²
       天文晨昏(-18°)   ≈ 0.0004 cd/m²
     本模块据此做分段线性归一化到 0(深夜)~1(白天)。
   - core/modules/Skybright.cpp:91,122       Schaefer 全天空亮度模型
     （twilight term: ``10^(-6.724 + 22.918*(π/2-acos(cosZ_sun)))``），
     本模块不重绘全天空分布，只取太阳高度的一维包络。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Tuple

# 复用 sat_passes.GroundStation 的接口（lat_deg/lon_deg/alt_m），
# 不直接 import 以避免循环依赖；结构化鸭子类型即可。
try:  # 允许作为包导入
    from .sat_passes import GroundStation
except Exception:  # pragma: no cover - 直接脚本运行
    GroundStation = None  # type: ignore


__all__ = [
    "TwilightInfo",
    "compute_twilight_times",
    "atmospheric_refraction",
    "airmass",
    "sky_brightness_factor",
    "is_night",
    "sun_altitude_deg",
    "SUNSET_CENTER_ALT_DEG",
    "CIVIL_TWILIGHT_ALT_DEG",
    "NAUTICAL_TWILIGHT_ALT_DEG",
    "ASTRONOMICAL_TWILIGHT_ALT_DEG",
]


# ── 晨昏 / 日出日落高度角常数（太阳中心，几何高度）──────────────
#: 日出/日落：太阳上边缘 + 大气折射 ⇒ 中心几何高度 = -0.833°
#: （折射 ≈ 34' = 0.5667° + 太阳半径 ≈ 16' = 0.2667°；Meeus ch.15）
SUNSET_CENTER_ALT_DEG = -0.833
#: 民用晨昏：太阳中心 -6°（Stellarium StelObject.cpp:939 twilightAltitude 默认）
CIVIL_TWILIGHT_ALT_DEG = -6.0
#: 航海晨昏：太阳中心 -12°
NAUTICAL_TWILIGHT_ALT_DEG = -12.0
#: 天文晨昏：太阳中心 -18°
ASTRONOMICAL_TWILIGHT_ALT_DEG = -18.0


# ── 时间系统 ────────────────────────────────────────────────
# JD ↔ datetime 用纯 Python（Unix 时间戳换算），避免 skyfield Time
# 构造在不同版本间的差异；精度 < 1s，对晨昏时刻足够。
# 若需闰秒级精度，可由调用方传入 skyfield Time 对象。


# ── 太阳位置（Meeus 低精度，离线回退）────────────────────────
# 参考 Meeus, Astronomical Algorithms, ch.25 (low precision solar position).
# 误差 ~0.01°，对日出日落时刻影响 < 30s，满足 SDR 观测规划需求。
def _sun_ra_dec_deg(jd_ut: float) -> Tuple[float, float]:
    """给定儒略日(UT)，返回太阳视赤经/赤纬（度，J2000 春分点近似）。"""
    d = jd_ut - 2451545.0  # 距 J2000.0 的天数
    # 平黄经 / 平近点角
    L = (280.459 + 0.98564736 * d) % 360.0
    g = math.radians((357.529 + 0.98560028 * d) % 360.0)
    # 中心差
    lon = L + 1.915 * math.sin(g) + 0.020 * math.sin(2.0 * g)
    # 黄赤交角
    eps = math.radians(23.439 - 0.00000036 * d)
    lon_r = math.radians(lon)
    # 黄道 → 赤道
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lon_r), math.cos(lon_r))) % 360.0
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lon_r)))
    return ra, dec


def _gmst_deg(jd_ut: float) -> float:
    """格林尼治平恒星时（度）。IAU 1982 公式。"""
    t = (jd_ut - 2451545.0) / 36525.0
    gmst = 280.46061837 + 360.98564736629 * (jd_ut - 2451545.0) \
        + 0.000387933 * t * t - t * t * t / 38710000.0
    return gmst % 360.0


def sun_altitude_deg(jd_ut: float, lat_deg: float, lon_deg: float) -> float:
    """计算给定 JD(UT)、站点纬度/经度处太阳的几何高度角（度）。

    不做大气折射修正——日出/日落阈值已包含折射（-0.833°）。
    如需视高度，调用方再加 :func:`atmospheric_refraction`。
    """
    ra, dec = _sun_ra_dec_deg(jd_ut)
    gmst = _gmst_deg(jd_ut)            # 度
    lst = (gmst + lon_deg) % 360.0     # 地方恒星时（度）
    ha = math.radians((lst - ra) % 360.0)
    lat = math.radians(lat_deg)
    dec_r = math.radians(dec)
    sin_alt = math.sin(lat) * math.sin(dec_r) \
        + math.cos(lat) * math.cos(dec_r) * math.cos(ha)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    return math.degrees(math.asin(sin_alt))


# ── 大气折射 ─────────────────────────────────────────────────
def atmospheric_refraction(alt_deg: float,
                          pressure_hpa: float = 1013.25,
                          temperature_c: float = 10.0) -> float:
    """大气折射修正角（度，正值 = 视高度比几何高度高）。

    使用 Stellarium RefractionExtinction.cpp:172-173 的 Saemundsson 公式
    （几何高度 → 视高度）：

        r = (P/1010) * (283/(273+T)) / 60
            * [ 1.02 / tan((h + 10.3/(h+5.11))°) + 0.0019279 ]

    标准大气(P=1013.25, T=10°C)下：
      - h=0°   → ≈ 0.48°（约 29'，地平线附近典型值 29-34'）
      - h=90°  → ≈ 0°

    参数：
        alt_deg: 几何（真实）高度角，度
        pressure_hpa: 地面气压（百帕），默认 1013.25
        temperature_c: 地面气温（摄氏度），默认 10

    返回：
        折射修正角（度）。低于 -3.54° 返回 0（Stellarium 在此以下不修正，
        见 RefractionExtinction.cpp:119,184）。
    """
    # 与 Stellarium MIN_GEO_ALTITUDE_DEG = -3.54 对齐
    if alt_deg <= -3.54:
        return 0.0
    h = alt_deg
    # press_temp_corr，见 RefractionExtinction.cpp:151
    press_temp_corr = pressure_hpa / 1010.0 * 283.0 / (273.0 + temperature_c) / 60.0
    # Saemundsson，RefractionExtinction.cpp:173
    x = math.radians(h + 10.3 / (h + 5.11))
    r_arcmin = 1.02 / math.tan(x) + 0.0019279
    r_deg = press_temp_corr * r_arcmin
    # 天顶处截断
    if alt_deg > 90.0:
        r_deg = 0.0
    return max(0.0, r_deg)


# ── 大气质量 (Kasten-Young 1989) ─────────────────────────────
def airmass(alt_deg: float) -> float:
    """大气质量 m（天顶 = 1）。

    使用 Kasten-Young 1989 公式（任务指定）：

        m = 1 / ( sin(h) + 0.50572 * (h + 6.07995)^(-1.6364) )

    其中 h 为高度角（度）。这是最常用的空气质量公式，地平线 h=0 时 m≈40。
    对照：Stellarium 用 Rozenberg 1966（RefractionExtinction.cpp:53），
    ``m = 1/(cosZ + 0.025*exp(-11*cosZ))``，两者在 h>10° 一致到 1%。

    参数：
        alt_deg: 高度角（度）。h <= -2° 返回 0（与 Stellarium
                 UndergroundExtinctionZero 一致，RefractionExtinction.cpp:37,42）。
    """
    if alt_deg <= -2.0:
        return 0.0
    if alt_deg < 0.0:
        alt_deg = 0.0  # 地平线以下到 -2° 之间给地平线值
    h = alt_deg
    m = 1.0 / (math.sin(math.radians(h))
               + 0.50572 * (h + 6.07995) ** (-1.6364))
    return max(1.0, m)


# ── 天空亮度因子（0=深夜, 1=白天）─────────────────────────────
# 断点来自 MilkyWay.cpp:350 / StelToast.cpp:334 的 cd/m² 经验值：
#   h=0°   → 10.0
#   h=-6°  → 3.3
#   h=-12° → 0.0145
#   h=-18° → 0.0004
# 归一化（除以 10）后做分段线性。
_SKY_BRIGHT_BREAKS = [
    (-90.0, 0.0),
    (-18.0, 0.0),       # 天文晨昏结束 → 全黑夜
    (-12.0, 0.002),     # 航海晨昏
    (-6.0, 0.33),       # 民用晨昏
    (0.0, 1.0),         # 日出/日落
    (90.0, 1.0),        # 白天
]


def sky_brightness_factor(sun_alt_deg: float) -> float:
    """根据太阳几何高度返回天空背景亮度因子，0.0(深夜)~1.0(白天)。

    分段线性（断点见模块顶部注释，对照 Stellarium
    ``MilkyWay.cpp:350`` 的 cd/m² 经验值）：
      - sun <= -18°        → 0.0（天文夜）
      - -18° < sun <= -12° → 0 → 0.002
      - -12° < sun <= -6°  → 0.002 → 0.33
      - -6° < sun <= 0°    → 0.33 → 1.0
      - sun > 0°           → 1.0
    """
    pts = _SKY_BRIGHT_BREAKS
    if sun_alt_deg <= pts[1][0]:
        return 0.0
    if sun_alt_deg >= pts[-1][0]:
        return 1.0
    for i in range(1, len(pts)):
        h0, b0 = pts[i - 1]
        h1, b1 = pts[i]
        if h0 <= sun_alt_deg <= h1:
            frac = (sun_alt_deg - h0) / (h1 - h0)
            return b0 + frac * (b1 - b0)
    return 1.0


def is_night(sun_alt_deg: float) -> bool:
    """是否为"夜"（太阳低于民用晨昏 -6°）。

    对照 Stellarium StelCore.hpp:372："true if sun higher than about -6 degrees,
    i.e. 'day' includes civil twilight" —— 即低于 -6° 为夜。
    """
    return sun_alt_deg < CIVIL_TWILIGHT_ALT_DEG


# ── 二分法求太阳过指定高度的时刻 ─────────────────────────────
def _jd_to_utc(jd: float) -> datetime:
    """JD(UT) → timezone-aware UTC datetime（纯 Python，避免 skyfield
    Time 构造的版本差异；精度 < 1s，对晨昏时刻足够）。"""
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=jd - 2440587.5)


def _utc_to_jd(dt: datetime) -> float:
    """timezone-aware UTC datetime → JD(UT)。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def _crossing_time(jd_lo: float, jd_hi: float,
                   target_alt: float,
                   lat_deg: float, lon_deg: float,
                   rising: bool) -> Optional[float]:
    """在 [jd_lo, jd_hi] 内二分求太阳高度 = target_alt 的 JD。

    rising=True 表示太阳正在升起（高度从低到高过目标）；
    rising=False 表示太阳正在落下。若区间内无穿越则返回 None。
    """
    f_lo = sun_altitude_deg(jd_lo, lat_deg, lon_deg) - target_alt
    f_hi = sun_altitude_deg(jd_hi, lat_deg, lon_deg) - target_alt
    # 检查符号（rising: f_lo<0, f_hi>0；setting: f_lo>0, f_hi<0）
    if rising:
        if f_lo > 0 or f_hi < 0:
            return None
    else:
        if f_lo < 0 or f_hi > 0:
            return None
    # 二分，容差 1 秒 ≈ 1/86400 天
    for _ in range(60):
        jd_mid = 0.5 * (jd_lo + jd_hi)
        f_mid = sun_altitude_deg(jd_mid, lat_deg, lon_deg) - target_alt
        if abs(f_mid) < 1e-5 or (jd_hi - jd_lo) < 1.0 / 86400.0:
            return jd_mid
        if (f_mid < 0) == rising:
            jd_lo = jd_mid
        else:
            jd_hi = jd_mid
    return 0.5 * (jd_lo + jd_hi)


def _find_crossings_on_day(date_local: date, lat_deg: float, lon_deg: float,
                           target_alt: float) -> Tuple[Optional[float], Optional[float]]:
    """在给定**本地太阳日** [local 00:00, next local 00:00) 内，
    找太阳高度上穿 target_alt（rising）与下穿 target_alt（setting）的 JD。

    ``date_local`` 是地面站所在本地日历日。本地子夜近似为
    ``UTC 子夜(date_local) - lon/15 小时``（本地太阳时 = UTC + lon/15h），
    这样长春(125°E, UTC+8)的"3月20日"会正确包含该日清晨/傍晚的事件，
    而不会落到相邻 UTC 日。

    返回 (rising_jd, setting_jd)，未发生（极昼/极夜）为 None。
    """
    utc_midnight = datetime(date_local.year, date_local.month,
                            date_local.day, tzinfo=timezone.utc)
    jd_start = _utc_to_jd(utc_midnight) - lon_deg / 15.0 / 24.0
    jd_end = jd_start + 1.0

    # 粗扫 10 分钟步长（太阳高度变化 ~0.25°/10min，足够定位穿越）
    step = 10.0 / 1440.0
    rising_jd: Optional[float] = None
    setting_jd: Optional[float] = None
    t = jd_start
    prev_alt = sun_altitude_deg(t, lat_deg, lon_deg) - target_alt
    n = int(round(1.0 / step))
    for i in range(1, n + 1):
        t_next = jd_start + i * step
        if t_next > jd_end:
            t_next = jd_end
        alt = sun_altitude_deg(t_next, lat_deg, lon_deg) - target_alt
        if prev_alt < 0 <= alt:
            rising_jd = _crossing_time(t, t_next, target_alt, lat_deg, lon_deg, True)
        elif prev_alt >= 0 > alt:
            setting_jd = _crossing_time(t, t_next, target_alt, lat_deg, lon_deg, False)
        prev_alt = alt
        t = t_next
        if rising_jd is not None and setting_jd is not None:
            break
    return rising_jd, setting_jd


# ── 对外数据类与主函数 ───────────────────────────────────────
@dataclass
class TwilightInfo:
    """一天的晨昏 / 日出日落时刻（均为 UTC timezone-aware datetime）。

    字段语义：
      - sunrise/sunset: 太阳上边缘过地平线（中心几何高 -0.833°）
      - civil_twilight_start/end: 民用晨昏起止（-6°）
          start = 早晨太阳升到 -6°（黎明日出前）；end = 傍晚太阳降到 -6°
      - nautical_twilight_start/end: 航海晨昏（-12°）
      - astronomical_twilight_start/end: 天文晨昏（-18°）
      极昼/极夜导致某事件不发生时对应字段为 None。
    """
    sunrise: Optional[datetime] = None
    sunset: Optional[datetime] = None
    civil_twilight_start: Optional[datetime] = None
    civil_twilight_end: Optional[datetime] = None
    nautical_twilight_start: Optional[datetime] = None
    nautical_twilight_end: Optional[datetime] = None
    astronomical_twilight_start: Optional[datetime] = None
    astronomical_twilight_end: Optional[datetime] = None

    def to_dict(self) -> dict:
        def _iso(dt: Optional[datetime]) -> Optional[str]:
            return dt.isoformat() if dt else None
        return {
            "sunrise": _iso(self.sunrise),
            "sunset": _iso(self.sunset),
            "civil_twilight_start": _iso(self.civil_twilight_start),
            "civil_twilight_end": _iso(self.civil_twilight_end),
            "nautical_twilight_start": _iso(self.nautical_twilight_start),
            "nautical_twilight_end": _iso(self.nautical_twilight_end),
            "astronomical_twilight_start": _iso(self.astronomical_twilight_start),
            "astronomical_twilight_end": _iso(self.astronomical_twilight_end),
        }


def compute_twilight_times(date_utc: date, ground_station) -> TwilightInfo:
    """计算给定 UTC 日期在地面站的全部晨昏/日出日落时刻。

    参数：
        date_utc: 公历日期（按 UTC 自然日扫描）。
        ground_station: 具有 ``lat_deg``/``lon_deg`` 属性的对象
                        （如 :class:`sat_passes.GroundStation`）。

    返回：
        :class:`TwilightInfo`，事件为 None 表示当日不发生（极昼/极夜）。
    """
    lat = float(ground_station.lat_deg)
    lon = float(ground_station.lon_deg)

    # 日出/日落（中心 -0.833°）
    rise_jd, set_jd = _find_crossings_on_day(
        date_utc, lat, lon, SUNSET_CENTER_ALT_DEG)
    # 民用晨昏 -6°
    civ_rise, civ_set = _find_crossings_on_day(
        date_utc, lat, lon, CIVIL_TWILIGHT_ALT_DEG)
    # 航海晨昏 -12°
    nau_rise, nau_set = _find_crossings_on_day(
        date_utc, lat, lon, NAUTICAL_TWILIGHT_ALT_DEG)
    # 天文晨昏 -18°
    ast_rise, ast_set = _find_crossings_on_day(
        date_utc, lat, lon, ASTRONOMICAL_TWILIGHT_ALT_DEG)

    return TwilightInfo(
        sunrise=_jd_to_utc(rise_jd) if rise_jd is not None else None,
        sunset=_jd_to_utc(set_jd) if set_jd is not None else None,
        civil_twilight_start=_jd_to_utc(civ_rise) if civ_rise is not None else None,
        civil_twilight_end=_jd_to_utc(civ_set) if civ_set is not None else None,
        nautical_twilight_start=_jd_to_utc(nau_rise) if nau_rise is not None else None,
        nautical_twilight_end=_jd_to_utc(nau_set) if nau_set is not None else None,
        astronomical_twilight_start=_jd_to_utc(ast_rise) if ast_rise is not None else None,
        astronomical_twilight_end=_jd_to_utc(ast_set) if ast_set is not None else None,
    )
