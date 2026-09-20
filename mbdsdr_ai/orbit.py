"""
MBDSDR AI 内核 - 真实轨道计算模块 (SGP4)
==========================================
用 sgp4 + 真实 celestrak TLE，替换手写占位 TLE 与经验多普勒近似。

能力：
- 按 CATNR 在线获取真实 TLE（celestrak），本地缓存避免重复联网；
- sgp4 传播得 TEME 位置/速度向量；
- TEME -> ECEF（GMST 旋转）-> 站心 ENU -> 仰角/方位/距离；
- 多普勒用真视线速度（卫星速度向量投影到站星方向），非经验公式。

参考：SGP4/SDP4 标准（Vallado），WGS84 站坐标。
"""
from __future__ import annotations

import math
import os
import time
import urllib.request
import ssl
from typing import Dict, List, Any, Optional, Tuple

from sgp4.api import Satrec, jday

# WGS84
WGS84_A = 6378.137          # km
WGS84_F = 1.0 / 298.257223563
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
OMEGA_E = 7.292115146706979e-5  # rad/s (WGS84)
C_LIGHT = 299792.458          # km/s

# 内置卫星 -> NORAD CATNR（真实编号，TLE 在线拉取）
BUILTIN_SATS: Dict[str, int] = {
    "NOAA 15": 25338,
    "NOAA 18": 28654,
    "NOAA 19": 33591,
    "ISS (ZARYA)": 25544,
    "METEOR M2": 42025,
    "FENGYUN 3D": 54234,
}

_TLE_CACHE: Dict[str, Tuple[float, Tuple[str, str]]] = {}
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mbdsdr", "tle_cache")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _gmst_days(jd_ut1: float) -> float:
    """Greenwich 平恒星时（弧度），基于儒略日。"""
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    gmst_rad = math.radians((gmst_sec % 86400.0) / 240.0)
    return gmst_rad % (2.0 * math.pi)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> Tuple[float, float, float]:
    """WGS84 大地坐标 -> ECEF (km)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon)
    y = (n + alt_km) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return x, y, z


def fetch_tle(catnr: int, max_age_hours: float = 24.0) -> Tuple[str, str]:
    """按 CATNR 从 celestrak 拉真实 TLE，内存+磁盘缓存。失败抛异常。"""
    key = str(catnr)
    now = time.time()
    if key in _TLE_CACHE and now - _TLE_CACHE[key][0] < max_age_hours * 3600.0:
        return _TLE_CACHE[key][1]
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cf = os.path.join(_CACHE_DIR, f"{catnr}.tle")
    if os.path.exists(cf) and now - os.path.getmtime(cf) < max_age_hours * 3600.0:
        lines = open(cf).read().splitlines()
        if len(lines) >= 3:
            tle = (lines[1].strip(), lines[2].strip())
            _TLE_CACHE[key] = (now, tle)
            return tle
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=tle"
    req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})
    data = urllib.request.urlopen(req, timeout=15, context=_CTX).read().decode()
    lines = [l for l in data.splitlines() if l.strip()]
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        raise RuntimeError(f"celestrak 返回异常: {data[:80]!r}")
    tle = (lines[1].strip(), lines[2].strip())
    with open(cf, "w") as f:
        f.write("\n".join(lines[:3]))
    _TLE_CACHE[key] = (now, tle)
    return tle


def compute_satellite_state(
    satellite_name: str,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    when: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """用 sgp4 真传播，返回站坐标下的仰角/方位/距离/视线速度/多普勒。

    when: unix 秒；默认 now。
    """
    catnr = BUILTIN_SATS.get(satellite_name)
    if catnr is None:
        return None
    try:
        line1, line2 = fetch_tle(catnr)
    except Exception:
        return None
    sat = Satrec.twoline2rv(line1, line2)
    if when is None:
        when = time.time()
    # unix -> UTC 儒略日
    jd_utc = when / 86400.0 + 2440587.5
    jd = int(jd_utc)
    fr = jd_utc - jd
    e, r_teme, v_teme = sat.sgp4(jd, fr)
    if e != 0:
        return None
    # TEME(km) -> ECEF：绕 z 轴转 -GMST
    gmst = _gmst_days(jd_utc)
    cg, sg = math.cos(-gmst), math.sin(-gmst)
    rx = cg * r_teme[0] - sg * r_teme[1]
    ry = sg * r_teme[0] + cg * r_teme[1]
    r_ecef = (rx, ry, r_teme[2])
    # 速度同旋转
    vx = cg * v_teme[0] - sg * v_teme[1]
    vy = sg * v_teme[0] + cg * v_teme[1]
    v_ecef = (vx, vy, v_teme[2])
    # 站 ECEF
    s_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)
    # 站心矢量
    rx_s = r_ecef[0] - s_ecef[0]
    ry_s = r_ecef[1] - s_ecef[1]
    rz_s = r_ecef[2] - s_ecef[2]
    dist = math.sqrt(rx_s * rx_s + ry_s * ry_s + rz_s * rz_s)
    # 站坐标 ENU
    lat = math.radians(observer_lat)
    lon = math.radians(observer_lon)
    sin_l, cos_l = math.sin(lat), math.cos(lat)
    sin_b, cos_b = math.sin(lon), math.cos(lon)
    e_east = -sin_b * rx_s + cos_b * ry_s
    e_north = -sin_l * cos_b * rx_s - sin_l * sin_b * ry_s + cos_l * rz_s
    e_up = cos_l * cos_b * rx_s + cos_l * sin_b * ry_s + sin_l * rz_s
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, e_up / dist)))) if dist > 0 else 0.0
    azimuth = (math.degrees(math.atan2(e_east, e_north)) + 360.0) % 360.0
    # 视线速度：卫星速度投影到站星方向单位矢量
    los = (rx_s / dist, ry_s / dist, rz_s / dist)
    range_rate = (v_ecef[0] * los[0] + v_ecef[1] * los[1] + v_ecef[2] * los[2])  # km/s, 远离为正
    return {
        "satellite": satellite_name,
        "elevation": elevation,
        "azimuth": azimuth,
        "range_km": dist,
        "range_rate_kms": range_rate,
        "altitude_km": math.sqrt(r_ecef[0]**2 + r_ecef[1]**2 + r_ecef[2]**2) - WGS84_A,
        "epoch": line1[20:32].strip(),
    }


def doppler_correction(
    satellite_name: str,
    nominal_freq_hz: float,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
) -> Dict[str, Any]:
    """真视线速度多普勒：f_rx = f_tx * (1 - v_los/c)（远离为正）。"""
    st = compute_satellite_state(satellite_name, observer_lat, observer_lon, observer_alt)
    if st is None:
        return {"error": f"无法计算卫星 {satellite_name} 位置（TLE 不可用或不在内置列表）"}
    v_los = st["range_rate_kms"]
    shift = -nominal_freq_hz * v_los / C_LIGHT  # 远离时频率降低
    return {
        "satellite": satellite_name,
        "nominal_freq_mhz": round(nominal_freq_hz / 1e6, 4),
        "corrected_freq_mhz": round((nominal_freq_hz + shift) / 1e6, 4),
        "doppler_shift_hz": round(shift, 1),
        "range_rate_kms": round(v_los, 3),
        "elevation_deg": round(st["elevation"], 1),
        "azimuth_deg": round(st["azimuth"], 1),
        "range_km": round(st["range_km"], 1),
        "epoch": st["epoch"],
        "method": "sgp4-teme-ecef-real-velocity",
    }


def visible_satellites(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    min_elevation: float = 0.0,
    satellite_type: str = "all",
) -> List[Dict[str, Any]]:
    """列出当前仰角超过阈值的卫星（真 sgp4）。"""
    out = []
    for name in BUILTIN_SATS:
        if satellite_type == "weather" and not any(
                k in name for k in ("NOAA", "METEOR", "FENGYUN")):
            continue
        if satellite_type == "amateur" and "ISS" not in name:
            continue
        st = compute_satellite_state(name, observer_lat, observer_lon, observer_alt)
        if st and st["elevation"] >= min_elevation:
            out.append(st)
    out.sort(key=lambda x: x["elevation"], reverse=True)
    return out
