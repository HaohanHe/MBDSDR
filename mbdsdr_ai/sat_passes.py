"""
MBDSDR AI 内核 - 真实卫星过境(pass)预测模块
============================================
算法参考 Stellarium Satellites plugin (GPL-3.0), 独立重实现
------------------------------------------------------------
本模块独立重实现 Stellarium ``plugins/Satellites`` 的核心几何与预测思想，
使用 Python ``sgp4==2.27`` 做轨道传播、``skyfield==1.55`` 做时间系统与
ECI→站心地平坐标(az/alt)变换。不链接 Stellarium/gpredict 任何二进制。

与 Stellarium 的对应关系（源码引用见各函数注释，路径相对
``repos/stellarium/plugins/Satellites/src/``）：

1. TLE 解析 + SGP4 传播
   - gSatTEME.cpp:66  twoline2rv(line1, line2, ...) 解析两行元素并初始化 satrec
   - gSatTEME.cpp:80  sgp4(...) 按距历元分钟数传播得 TEME 位置/速度
   本模块用 skyfield.EarthSatellite(内部即 sgp4.api.Satrec.twoline2rv + sgp4())。

2. ECI(TEME) → 地面站地平坐标(az/alt)
   - gSatWrapper.cpp:106-131  calcObserverECIPosition：站大地坐标→ECI 位置/速度
   - gSatWrapper.cpp:133-165  getAltAz：slantRange = satECI - obsECI，再旋转到
     站心 topocentric (S/E/Z)。topo[2]=Up>0 即仰角方向。
   本模块用 skyfield ``(sat - topo).at(t).altaz()`` 完成等价变换
   （Topos 即站心，altaz() 返回高度/方位/距离）。

3. 可见性 / 地影
   - gSatWrapper.cpp:205-249 getVisibilityPredict：先判 topo[2]>0（在地平线上，:209），
     再判太阳高度(:213)，最后按 Vallado 锥几何(:227-244)判本影/半影/食。
   本模块默认只做几何可见性(仰角>min_alt)；可选 ``require_sunlit`` 走几何地影判断。

4. 过境扫描（rise/set/中天）
   Stellarium 本身是实时显示，不做未来过境扫描；它在
   Satellite.cpp:1285-1305 computeOrbitPoints 里用固定时间步长
   (orbitLineSegmentDuration=20s, Satellite.cpp:52) 在“当前时刻前后”采样轨道点。
   本模块沿用“时间轴步长采样仰角曲线”这一思想，叠加 gpredict 式粗扫+二分过零：
     a) 粗扫 60s 步长定位仰角跨越 min_alt 的区间；
     b) 二分法精确定位 rise(AOS)/set(LOS) 时刻；
     c) pass 内 2s 细采样找最大仰角(中天)时刻与方位。

5. trail 轨迹点采样：见 :func:`sample_trail`，对应
   Satellite.cpp:1298-1305 的“从起始时刻起按固定间隔逐 setEpoch+getAltAz 采样”。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from skyfield.api import EarthSatellite, Topos, Time, load as _sky_load

__all__ = [
    "GroundStation",
    "SatellitePass",
    "predict_passes",
    "sample_trail",
    "make_timescale",
    "DopplerPoint",
    "compute_doppler_curve",
    "predict_upcoming_passes",
]

# 光速 m/s（与 Stellarium StelUtils.hpp:41 SPEED_OF_LIGHT=299792.458 km/s 一致，
# 本模块统一用 m/s：c = 299792.458 * 1000 = 299792458 m/s）。
C_LIGHT_MPS = 299792458.0

# AU/km 换算（skyfield position.au 为 AU，需转 km 做点积）。
_AU_KM = 149597870.700

# 与 Stellarium gSatTEME.cpp:46-49 一致的传播模式：WGS-84 引力常数、'c'=current 模式。
# Python sgp4 库默认即 WGS-72 legacy；skyfield EarthSatellite 默认 legacy mode，
# 与 Stellarium 的 WGS-84 常数差异对 LEO 过境时刻影响在秒级以内，满足 SDR 预报需求。

# 粗扫/细扫步长（秒）。粗扫量级对照 gpredict predict-tools.c 与
# Stellarium computeOrbitPoints 的固定间隔采样(Satellite.cpp:1287)。
_COARSE_STEP_S = 60.0     # 粗扫：60s 定位过境区间
_FINE_STEP_S = 2.0        # pass 内细采样：找中天/最大仰角
_BISECT_TOL_S = 0.5       # 二分收敛容差（秒），LEO 仰角变化 ~0.5°/s ⇒ ~0.25° 精度


def make_timescale():
    """返回 skyfield 时间系统（缓存，避免重复加载 ephemeris/UT1 表）。"""
    if not hasattr(make_timescale, "_ts"):
        make_timescale._ts = _sky_load.timescale()
    return make_timescale._ts


@dataclass
class GroundStation:
    """地面站（观测者）位置。

    对应 Stellarium StelLocation（纬度/经度/海拔），见
    gSatWrapper.cpp:110-124 calcObserverECIPosition 中 loc.getLatitude()/getLongitude()。
    """
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0


@dataclass
class SatellitePass:
    """一次卫星过境事件（AOS→中天→LOS）。

    时间字段均为 skyfield.api.Time（UTC），与任务约束一致。
    """
    rise_time: Time          # 仰角升至 min_alt 时刻 (AOS)
    set_time: Time           # 仰角回落至 min_alt 时刻 (LOS)
    max_alt_time: Time       # 过境期间最大仰角(中天)时刻
    max_alt: float           # 最大仰角（度）
    rise_az: float           # 升起方位角（度，北=0，东=90）
    set_az: float            # 落下方位角（度）
    duration: float          # 过境总时长（秒）
    sat_name: str = ""       # 卫星名（TLE 标题行，若有）
    sunlit: Optional[bool] = None   # 过境中点卫星是否被太阳照亮（None=未计算）

    def summary(self) -> str:
        dt_r = self.rise_time.utc_datetime()
        dt_s = self.set_time.utc_datetime()
        dt_m = self.max_alt_time.utc_datetime()
        return (f"{self.sat_name or 'sat'}: rise {dt_r:%H:%M:%S}Z az={self.rise_az:.0f}° | "
                f"max {dt_m:%H:%M:%S}Z alt={self.max_alt:.1f}° | "
                f"set {dt_s:%H:%M:%S}Z az={self.set_az:.0f}° | "
                f"dur={self.duration/60:.1f}min sunlit={self.sunlit}")


def _parse_tle(tle_lines: Sequence[str]) -> Optional[Tuple[str, str, str]]:
    """从 TLE 文本中提取 (name, line1, line2)。

    接受：
      - 3 行（标题行 + 1 + 2）：celestrak 标准格式
      - 2 行（1 + 2）：无标题，name 从 line2 编号推断
      - 列表里混有空行/注释；自动跳过。
    解析失败（无有效 1/2 行）返回 None —— 调用方据此返回空列表，不造假。

    对应 Stellarium gSatWrapper.cpp:43-58 构造函数：拷贝并截断 t1/t2 后交给
    gSatTEME → twoline2rv（gSatTEME.cpp:66）。
    """
    lines = [l.strip() for l in tle_lines if l and l.strip()]
    if len(lines) < 2:
        return None
    i1 = next((i for i, l in enumerate(lines) if l.startswith("1 ")), None)
    i2 = next((i for i, l in enumerate(lines) if l.startswith("2 ")), None)
    if i1 is None or i2 is None or i2 <= i1:
        return None
    line1, line2 = lines[i1], lines[i2]
    if len(line1) < 69 or len(line2) < 69:
        return None
    # 标题行 = line1 之前的非数字行
    name = ""
    if i1 > 0:
        name = lines[i1 - 1].strip()
    if not name:
        # 用 NORAD 编号兜底
        name = "NORAD " + line2[2:7].strip()
    return name, line1, line2


def _alt_az(diff, t: Time) -> Optional[Tuple[float, float]]:
    """在时刻 t 计算 (仰角°, 方位角°)；传播失败返回 None。

    对应 gSatWrapper.cpp:133-165 getAltAz：slantRange = satECI - obsECI，
    再旋转到站心 Up/East/North，Up 分量给出仰角。
    skyfield 的 ``(sat - topo).at(t).altaz()`` 是同一变换的官方实现。
    """
    try:
        topoc = diff.at(t)
        alt, az, _dist = topoc.altaz()
        return float(alt.degrees), float(az.degrees)
    except Exception:
        return None


def _bisect_cross(el_fn, lo: float, hi: float, target: float,
                  tol: float = _BISECT_TOL_S) -> float:
    """在 [lo, hi]（秒，相对 t0）之间二分求仰角=target 的时刻。

    调用方保证两端仰角在 target 异侧。对照 gpredict predict-tools.c 的过零细化；
    Stellarium 不做未来扫描，但其 Satellite.cpp:1298-1305 逐时刻 setEpoch+getAltAz
    的采样思想是本函数的基础。
    """
    flo = el_fn(lo)
    fhi = el_fn(hi)
    if flo is None or fhi is None:
        return 0.5 * (lo + hi)
    if (flo - target) * (fhi - target) > 0:
        return 0.5 * (lo + hi)
    for _ in range(40):
        if hi - lo < tol:
            break
        mid = 0.5 * (lo + hi)
        fm = el_fn(mid)
        if fm is None:
            return mid
        if (fm - target) * (flo - target) <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def predict_passes(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    start_time: Optional[Time] = None,
    hours: float = 24.0,
    min_alt: float = 10.0,
    require_sunlit: bool = False,
) -> List[SatellitePass]:
    """预测未来 ``hours`` 小时内卫星从 ``start_time`` 起的过境事件。

    参数
    ----
    tle_lines:
        TLE 文本（标题行可选）。空/无效 → 返回空列表，不造假。
    ground_station:
        :class:`GroundStation` 观测者位置。
    start_time:
        skyfield Time（UTC）。None 取当前时刻。
    hours:
        向前预测时长（小时）。
    min_alt:
        过境最低仰角阈值（度），默认 10°。
    require_sunlit:
        若 True，仅保留过境中点卫星被太阳照亮的事件（几何地影，
        对应 gSatWrapper.cpp:227-244 的 Vallado 锥判断）。默认 False。

    返回
    ----
    List[SatellitePass]，按 rise_time 升序。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed

    ts = make_timescale()
    if start_time is None:
        start_time = ts.now()

    # gSatTEME.cpp:66 twoline2rv + gSatTEME.cpp:80 sgp4 传播器
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []

    # 站心：gSatWrapper.cpp:106-131 calcObserverECIPosition 的 skyfield 等价
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo

    def el_at_s(sec: float) -> Optional[float]:
        t = start_time + sec / 86400.0   # skyfield Time 支持 +天数
        r = _alt_az(diff, t)
        return r[0] if r else None

    def el_az_at_s(sec: float) -> Optional[Tuple[float, float]]:
        t = start_time + sec / 86400.0
        return _alt_az(diff, t)

    total_s = hours * 3600.0
    n_steps = int(total_s / _COARSE_STEP_S) + 1

    # a) 粗扫：60s 步长记录 (sec, el)。
    #    对应 Satellite.cpp:1298-1305 固定间隔采样轨道点的思想。
    coarse: List[Tuple[float, Optional[float]]] = []
    for k in range(n_steps + 1):
        sec = min(k * _COARSE_STEP_S, total_s)
        coarse.append((sec, el_at_s(sec)))
    if all(e is None for _, e in coarse):
        return []

    passes: List[SatellitePass] = []
    n = len(coarse)
    i = 0
    while i < n:
        sec_i, el_i = coarse[i]
        if el_i is None or el_i < min_alt:
            i += 1
            continue
        # 找到一段连续 >= min_alt 的粗采样 coarse[i:j]
        j = i
        while j < n and coarse[j][1] is not None and coarse[j][1] >= min_alt:
            j += 1

        # b) rise/set 二分精确定位（过零检测）
        if i > 0 and coarse[i - 1][1] is not None:
            t_rise_s = _bisect_cross(el_at_s, coarse[i - 1][0], sec_i, min_alt)
        else:
            t_rise_s = sec_i
        if j < n and coarse[j][1] is not None:
            t_set_s = _bisect_cross(el_at_s, coarse[j - 1][0], coarse[j][0], min_alt)
        else:
            t_set_s = coarse[j - 1][0]

        # c) pass 内 2s 细采样，找最大仰角时刻
        best_sec, best_el = t_rise_s, -999.0
        s = t_rise_s
        while s <= t_set_s + 1e-6:
            r = el_az_at_s(s)
            if r is not None and r[0] > best_el:
                best_el, best_sec = r[0], s
            s += _FINE_STEP_S

        rise_r = el_az_at_s(t_rise_s) or (None, None)
        set_r = el_az_at_s(t_set_s) or (None, None)
        rise_az = rise_r[1] if rise_r[1] is not None else float("nan")
        set_az = set_r[1] if set_r[1] is not None else float("nan")

        mid_s = 0.5 * (t_rise_s + t_set_s)
        sunlit = None
        if require_sunlit:
            sunlit = _is_sunlit_geometric(sat, start_time + mid_s / 86400.0)

        p = SatellitePass(
            rise_time=start_time + t_rise_s / 86400.0,
            set_time=start_time + t_set_s / 86400.0,
            max_alt_time=start_time + best_sec / 86400.0,
            max_alt=float(best_el),
            rise_az=float(rise_az),
            set_az=float(set_az),
            duration=float(t_set_s - t_rise_s),
            sat_name=name,
            sunlit=sunlit,
        )
        if (not require_sunlit) or sunlit:
            passes.append(p)
        i = j

    passes.sort(key=lambda p: p.rise_time.utc)
    return passes


def sample_trail(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    t_center: Time,
    before_s: float = 600.0,
    after_s: float = 600.0,
    step_s: float = 20.0,
) -> List[Tuple[Time, float, float]]:
    """采样过去/未来轨迹点（trail），返回 [(Time, az_deg, alt_deg), ...]。

    对应 Stellarium Satellite.cpp:1285-1305 computeOrbitPoints：
    以当前时刻为中心，从 ``t_center - before_s`` 起按固定 ``step_s`` 逐
    setEpoch + getAltAz 采样（Stellarium 默认 orbitLineSegmentDuration=20s，
    见 Satellite.cpp:52）。无效点跳过。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo
    out: List[Tuple[Time, float, float]] = []
    n = int((before_s + after_s) / step_s)
    for k in range(n + 1):
        sec = -before_s + k * step_s
        t = t_center + sec / 86400.0
        r = _alt_az(diff, t)
        if r is not None:
            out.append((t, r[1], r[0]))
    return out


# ---------------------------------------------------------------------------
# 几何地影判断（可选）—— 对照 gSatWrapper.cpp:227-244
# ---------------------------------------------------------------------------
def _sun_eci_unit(t: Time) -> Tuple[float, float, float]:
    """低精度太阳方向单位矢量（TEME/ECI，无量纲）。

    Stellarium 用 SolarSystem 模块取太阳 ECI 位置(gSatWrapper.cpp:181-191)；
    这里用 NOAA/Spherical Astronomy 低精度太阳黄经→春分点赤道坐标，
    误差 ~0.1°，对 LEO 地影边界判定足够。
    """
    jd = t.tt
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    # 太阳方向单位矢量（春分点赤道系）
    x = math.cos(lam)
    y = math.cos(eps) * math.sin(lam)
    z = math.sin(eps) * math.sin(lam)
    return (x, y, z)


def _is_sunlit_geometric(sat: EarthSatellite, t: Time) -> bool:
    """几何地影判断：卫星是否被太阳照亮（不在地球本影内）。

    对照 gSatWrapper.cpp:227-244 的 Vallado 锥几何：
      theta_e = asin(R_earth / r_sat)   地球视半径角
      theta_s = asin(R_sun  / r_sun)    太阳视半径角
      theta   = 卫星-地心-太阳 夹角
    若 theta < theta_e - theta_s → 完全本影(RADAR_NIGHT) → 未被照亮。
    这里简化：仅判是否在本影锥内（半影近似为照亮），足够 SDR 观测预报。
    """
    try:
        au = sat.at(t).position.au   # 地心 ECI，长度-3 数组（AU）
    except Exception:
        return True
    rx, ry, rz = float(au[0]) * 149597870.700, float(au[1]) * 149597870.700, float(au[2]) * 149597870.700  # km
    r_sat = math.sqrt(rx * rx + ry * ry + rz * rz)
    if r_sat <= 0:
        return True
    sx, sy, sz = _sun_eci_unit(t)
    # 太阳方向（地心指向太阳）；卫星-太阳矢量 = r_sun - r_sat，方向近似 -sun_unit 反向
    # 夹角 theta：卫星矢量与“地心→太阳”方向的夹角
    dot = (rx * sx + ry * sy + rz * sz) / r_sat
    dot = max(-1.0, min(1.0, dot))
    theta = math.acos(dot)
    R_EARTH = 6378.137   # km
    R_SUN = 696000.0     # km
    # 日地距离 ~1 AU
    r_sun = 149597870.700
    theta_e = math.asin(R_EARTH / r_sat)
    theta_s = math.asin(R_SUN / r_sun)
    # 本影：卫星落在地球背阳面且 theta < theta_e - theta_s
    if theta_e > theta_s and theta < (theta_e - theta_s):
        return False  # 在本影，未被照亮
    return True


# ===========================================================================
# 多普勒频移预测（SDR 接收用）
# ===========================================================================
#
# Stellarium 中的多普勒参考：
#   - plugins/Satellites/src/gSatWrapper.cpp:167-177  getSlantRange()
#       slantRange       = satECIPos - observerECIPos      (位置差向量, km)
#       slantRangeVel    = satECIVel - observerECIVel      (速度差向量, km/s)
#       ao_slantRange    = slantRange.norm()               (斜距, km)
#       ao_slantRangeRate= slantRange.dot(slantRangeVel)/slantRange.norm()
#         ^^^ 径向速度（km/s），远离为正。这就是多普勒计算的核心。
#   - plugins/Satellites/src/Satellite.cpp:850  update() 中调用
#       pSatWrapper->getSlantRange(range, rangeRate) 把 range/rangeRate 存为成员。
#   - plugins/Satellites/src/Satellite.cpp:940-943  getDoppler(double freq) const
#       return -freq*((rangeRate*1000.0)/SPEED_OF_LIGHT);
#       即 f_d = -f_0 * v_r / c。Stellarium 中 freq 单位 MHz、返回值 kHz
#       （见 Satellite.cpp:496,505 显示为 kHz）；本模块统一用 Hz，c 用 m/s。
#   - src/core/StelUtils.hpp:41  #define SPEED_OF_LIGHT 299792.458 (km/s)
#
# 物理符号约定（与 gSatWrapper.cpp:177 rangeRate 一致）：
#   range_rate > 0 → 卫星远离地面站 → 接收频率降低 → f_d < 0
#   range_rate < 0 → 卫星接近地面站 → 接收频率升高 → f_d > 0
#   AOS（升起）时卫星朝向观测者飞来 ⇒ v_r<0 ⇒ f_d>0（正偏移）
#   中天（最大仰角）时运动方向与视线垂直 ⇒ v_r≈0 ⇒ f_d≈0
#   LOS（落下）时卫星远离观测者 ⇒ v_r>0 ⇒ f_d<0（负偏移）
# ===========================================================================


@dataclass
class DopplerPoint:
    """过境过程中某一时刻的多普勒状态采样点。

    字段对应 Stellarium Satellite.cpp:361-363 信息行中的
    Range / Range rate / Altitude，外加 az/el/多普勒频率与变化率。
    """
    time: Time                # 采样时刻（UTC, skyfield Time）
    azimuth: float            # 方位角（度，北=0，东=90）
    elevation: float          # 仰角（度）
    range_km: float           # 斜距（km）
    range_rate_km_s: float    # 径向速度（km/s），远离为正（gSatWrapper.cpp:177）
    doppler_hz: float         # 多普勒频移（Hz），f_d = -v_r/c * f_0
    doppler_rate_hz_s: float  # 多普勒变化率（Hz/s），SDR AFC 跟踪用


def _range_and_rate(diff, t: Time) -> Optional[Tuple[float, float, float, float, float]]:
    """在时刻 t 计算 (az_deg, el_deg, range_km, range_rate_km_s, unused)。

    算法对照 gSatWrapper.cpp:167-177 getSlantRange：
      slantRange       = satECIPos - observerECIPos
      slantRangeVel   = satECIVel - observerECIVel
      range           = |slantRange|
      rangeRate       = dot(slantRange, slantRangeVel) / |slantRange|
    skyfield 的 ``(sat - topo).at(t)`` 直接给出相对位置/速度向量
    （ICRS/地球中心惯性系，与 TEME 在 LEO 多普勒量级下差异可忽略）。
    """
    try:
        geom = diff.at(t)
        alt, az, dist = geom.altaz()
        # position 单位 AU → km；velocity 单位 AU/day → km/s
        pos_km = [float(x) * _AU_KM for x in geom.position.au]
        vel_kms = [float(x) * _AU_KM / 86400.0 for x in geom.velocity.au_per_d]
        rng = math.sqrt(sum(x * x for x in pos_km))
        if rng <= 0:
            return None
        # gSatWrapper.cpp:177: rangeRate = dot(slantRange, slantRangeVel)/|slantRange|
        rr = sum(p * v for p, v in zip(pos_km, vel_kms)) / rng
        return float(az.degrees), float(alt.degrees), float(rng), float(rr), 0.0
    except Exception:
        return None


def compute_doppler_curve(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    pass_: SatellitePass,
    freq_hz: float,
    num_points: int = 60,
) -> List[DopplerPoint]:
    """对一次过境事件采样完整多普勒曲线。

    参数
    ----------
    tle_lines:
        TLE 文本（标题行可选），与 ``predict_passes`` 同格式。
    ground_station:
        地面站位置。
    pass_:
        :class:`SatellitePass` 过境事件（由 predict_passes 产生）。
    freq_hz:
        下行/上行标称频率（Hz）。例：ISS 中继 437 MHz = 437000000。
    num_points:
        在 [rise_time, set_time] 区间内均匀采样点数，默认 60。

    返回
    ----
    List[DopplerPoint]，按时间升序。无 TLE/传播失败返回空列表。

    算法
    ----
    对每个采样时刻调用 :func:`_range_and_rate`（等价于
    gSatWrapper.cpp:167-177 getSlantRange），然后：
        f_d = -range_rate_mps / c * freq_hz
    其中 c = 299792458 m/s（Stellarium StelUtils.hpp:41，单位换算后）。
    多普勒变化率 doppler_rate_hz_s 用相邻点中心差分：
        df_d/dt ≈ (f_d[i+1] - f_d[i-1]) / (t[i+1] - t[i-1])
    端点用前向/后向差分。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo

    t0 = pass_.rise_time
    t1 = pass_.set_time
    dt_days = t1.tt - t0.tt
    if dt_days <= 0 or num_points < 2:
        return []

    raw: List[Tuple[Time, float, float, float, float]] = []
    for k in range(num_points):
        frac = k / (num_points - 1)
        t = ts.tt_jd(t0.tt + frac * dt_days)
        r = _range_and_rate(diff, t)
        if r is None:
            continue
        az, el, rng, rr, _ = r
        # Satellite.cpp:942: f_d = -freq * (rangeRate_mps / SPEED_OF_LIGHT_mps)
        # range_rate_km_s → m/s 乘 1000
        dop = -rr * 1000.0 / C_LIGHT_MPS * freq_hz
        raw.append((t, az, el, rng, rr))

    if len(raw) < 2:
        return []

    # 计算多普勒变化率（Hz/s）：中心差分
    dop_vals = [-rr * 1000.0 / C_LIGHT_MPS * freq_hz for (_, _, _, _, rr) in raw]
    t_days = [t.tt for (t, _, _, _, _) in raw]
    dop_rate = [0.0] * len(raw)
    for i in range(len(raw)):
        if i == 0:
            dt_s = (t_days[1] - t_days[0]) * 86400.0
            dop_rate[i] = (dop_vals[1] - dop_vals[0]) / dt_s if dt_s > 0 else 0.0
        elif i == len(raw) - 1:
            dt_s = (t_days[-1] - t_days[-2]) * 86400.0
            dop_rate[i] = (dop_vals[-1] - dop_vals[-2]) / dt_s if dt_s > 0 else 0.0
        else:
            dt_s = (t_days[i + 1] - t_days[i - 1]) * 86400.0
            dop_rate[i] = (dop_vals[i + 1] - dop_vals[i - 1]) / dt_s if dt_s > 0 else 0.0

    out: List[DopplerPoint] = []
    for i, (t, az, el, rng, rr) in enumerate(raw):
        out.append(DopplerPoint(
            time=t,
            azimuth=az,
            elevation=el,
            range_km=rng,
            range_rate_km_s=rr,
            doppler_hz=dop_vals[i],
            doppler_rate_hz_s=dop_rate[i],
        ))
    return out


def predict_upcoming_passes(
    tle_list: Sequence[Sequence[str]],
    ground_station: GroundStation,
    hours: float = 24.0,
    min_alt: float = 10.0,
    max_sats: Optional[int] = None,
    freq_hz: Optional[float] = None,
) -> List[Dict]:
    """多颗卫星的未来过境事件列表，按 AOS 时间排序。

    对应 Stellarium Satellites 插件的"未来过境"概念：Stellarium 本身是
    实时天球显示，不做未来过境扫描；但 Satellite.cpp:1298-1305 的固定步长
    采样轨道点思想，叠加 gpredict 式粗扫+二分过零（见本模块 predict_passes
    注释），构成多星事件列表。

    参数
    ----------
    tle_list:
        多颗卫星的 TLE 列表，每项是一个 TLE 文本序列（标题行可选）。
        例：[["ISS (ZARYA)", "1 ...", "2 ..."], ["NOAA 15", "1 ...", "2 ..."]]
        空/无效 TLE 自动跳过（返回空结果，不造假）。
    ground_station:
        地面站位置。
    hours:
        向前预测时长（小时），默认 24。
    min_alt:
        过境最低仰角阈值（度），默认 10°。
    max_sats:
        最多返回多少颗卫星的过境（取仰角峰值最高的前 N 颗）；None 不限制。
    freq_hz:
        若提供，对每条过境计算峰值多普勒（Hz）；否则不计算。

    返回
    ----
    List[Dict]，按 rise_time 升序，每项：
      - pass: SatellitePass 对象
      - tle_lines: 原始 TLE 文本（便于后续 compute_doppler_curve 使用）
      - peak_doppler_hz: 最大 |f_d|（Hz，若 freq_hz 提供）
      - doppler_aos_hz: AOS 时刻多普勒（正=接近，若 freq_hz 提供）
      - doppler_los_hz: LOS 时刻多普勒（负=远离，若 freq_hz 提供）
    """
    # 收集 (max_alt, rise_time_tt, SatellitePass, tle_lines)
    collected: List[Tuple[float, float, SatellitePass, Sequence[str]]] = []
    for tle_lines in tle_list:
        try:
            passes = predict_passes(tle_lines, ground_station, hours=hours,
                                    min_alt=min_alt)
        except Exception:
            continue
        for p in passes:
            collected.append((p.max_alt, p.rise_time.tt, p, tle_lines))

    if not collected:
        return []

    # 若 max_sats 限制，按 max_alt 降序取前 N 颗卫星的过境
    if max_sats is not None and max_sats > 0 and max_sats < len(collected):
        collected.sort(key=lambda x: x[0], reverse=True)
        collected = collected[:max_sats]

    # 按 rise_time 升序排序
    collected.sort(key=lambda x: x[1])

    result: List[Dict] = []
    for _, _, p, tle_lines in collected:
        entry: Dict = {"pass": p, "tle_lines": list(tle_lines)}
        if freq_hz is not None:
            try:
                curve = compute_doppler_curve(
                    tle_lines, ground_station, p, freq_hz, num_points=30,
                )
            except Exception:
                curve = []
            if curve:
                entry["peak_doppler_hz"] = max(abs(pt.doppler_hz) for pt in curve)
                entry["doppler_aos_hz"] = curve[0].doppler_hz
                entry["doppler_los_hz"] = curve[-1].doppler_hz
            else:
                entry["peak_doppler_hz"] = None
                entry["doppler_aos_hz"] = None
                entry["doppler_los_hz"] = None
        result.append(entry)
    return result
