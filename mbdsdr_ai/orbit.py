"""
MBDSDR AI 内核 - 真实轨道计算模块 (SGP4)
==========================================
用 sgp4 + 真实 celestrak TLE，替换手写占位 TLE 与经验多普勒近似。

能力：
- 按 CATNR 在线获取真实 TLE（celestrak），本地缓存避免重复联网；
- sgp4 传播得 TEME 位置/速度向量；
- TEME -> ECEF（GMST 旋转）-> 站心 ENU -> 仰角/方位/距离；
- 多普勒用真视线速度（卫星速度向量投影到站星方向），非经验公式。

参考：SGP4/SDP4 标准（Vallado）。椭球参数统一使用 WGS-72（与 Python sgp4 库默认
legacy 模式及 gpredict sgp4sdp4.h 硬编码一致），避免轨道 WGS-72 / 站心 WGS-84 混用
引入的米级系统差。

校准：本模块的 WGS-72 常数(6378.135km, f=1/298.26)与 GMST 折叠式(_gmst_days)已与
逐行移植 gpredict C 源码的 mbdsdr_ai/gpredict_adapter.py 交叉验证：同一卫星 TLE+时刻下，
本模块 ECEF-ENU 法与 gpredict sgp_obs.c 站心法的方位/仰角差 <0.02°/0.01°（见
tests/gpredict_test.py::test_coordinate_az_el）。gpredict_adapter 提供无第三方依赖的纯
Python SGP4 参考实现，本模块保留 sgp4 库高性能传播路径。
"""
from __future__ import annotations

import math
import os
import time
import urllib.request
import ssl
from typing import Dict, List, Any, Optional, Tuple

from sgp4.api import Satrec, jday

# 来源: gpredict repos/gpredict/src/sgpsdp/sgp4sdp4.h:211 — xkmper=6378.135 km（WGS-72 赤道半径）
# 来源: gpredict sgp4sdp4.h:216 — __f=3.352779E-3 ≈ 1/298.26（WGS-72 扁率）
# SGP4 内核（xke/xkmper/ck2/ck4）全程 WGS-72，站心椭球必须一致，否则 ECEF 站位置与
# 卫星 TEME→ECEF 旋转后的坐标存在 ~米级系统差（gpredict 全程 WGS-72，见笔记 5.3）。
WGS72_A = 6378.135          # km, WGS-72 赤道半径
WGS72_F = 1.0 / 298.26      # WGS-72 扁率（gpredict __f）
WGS72_B = WGS72_A * (1.0 - WGS72_F)
WGS72_E2 = WGS72_F * (2.0 - WGS72_F)
# 来源: gpredict sgp4sdp4.h:250 — mfactor=7.292115E-5 rad/s（地球自转角速度，WGS-72）
OMEGA_E = 7.292115e-5       # rad/s
C_LIGHT = 299792.458          # km/s

# 内置卫星 -> NORAD CATNR（真实编号，TLE 在线拉取）
BUILTIN_SATS: Dict[str, int] = {
    "NOAA 15": 25338,
    "NOAA 18": 28654,
    "NOAA 19": 33591,
    "ISS (ZARYA)": 25544,
    "METEOR M2": 44016,
    "FENGYUN 3D": 54234,
}

_TLE_CACHE: Dict[str, Tuple[float, Tuple[str, str]]] = {}
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mbdsdr", "tle_cache")
_CTX = ssl.create_default_context()


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
    """WGS-72 大地坐标 -> ECEF (km)。椭球与 SGP4 输出一致（见 sgp4sdp4.h:211,216）。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = WGS72_A / math.sqrt(1.0 - WGS72_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon)
    y = (n + alt_km) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS72_E2) + alt_km) * sin_lat
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


def _state_from_satrec(
    sat: Satrec,
    satellite_name: str,
    jd_utc: float,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    epoch: str = "",
) -> Optional[Dict[str, Any]]:
    """核心坐标变换：TEME -> ECEF(绕 z 转 -GMST) -> ENU -> 仰角/方位/距离/视线速度。

    来源: gpredict repos/gpredict/src/predict-tools.c:82 (Calculate_Obs) 等价路线 (a)：
    gpredict 把地面站转到 TEME（method b），这里把卫星 TEME 转到 ECEF（method a），
    数学等价。GMST 折叠式见 _gmst_days（与 gpredict ThetaG_JD 等价）。
    """
    jd = int(jd_utc)
    fr = jd_utc - jd
    e, r_teme, v_teme = sat.sgp4(jd, fr)
    if e != 0:
        return None
    # TEME(km) -> ECEF：绕 z 轴转 -GMST（gpredict 路线 b 是站位置转 +GMST 到 TEME，互为逆）
    gmst = _gmst_days(jd_utc)
    cg, sg = math.cos(-gmst), math.sin(-gmst)
    rx = cg * r_teme[0] - sg * r_teme[1]
    ry = sg * r_teme[0] + cg * r_teme[1]
    r_ecef = (rx, ry, r_teme[2])
    # 速度：旋转 − Coriolis 项（输运定理 v_ECEF = R·v_TEME − Ω×r）
    # 来源: gpredict sgp_obs.c:33-35 站速 = Ω×r_obs；这里减 Ω×r_ECEF = +(ωy,-ωx,0)
    vx = cg * v_teme[0] - sg * v_teme[1]
    vy = sg * v_teme[0] + cg * v_teme[1]
    vx += OMEGA_E * r_ecef[1]    # - (Ω×r)_x = +ω·y
    vy += -OMEGA_E * r_ecef[0]   # - (Ω×r)_y = -ω·x
    v_ecef = (vx, vy, v_teme[2])
    # 站 ECEF（WGS-72 椭球，与 SGP4 一致）
    s_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)
    # 站心矢量
    rx_s = r_ecef[0] - s_ecef[0]
    ry_s = r_ecef[1] - s_ecef[1]
    rz_s = r_ecef[2] - s_ecef[2]
    dist = math.sqrt(rx_s * rx_s + ry_s * ry_s + rz_s * rz_s)
    if dist <= 0:
        return None
    # 站坐标 ENU（与 gpredict sgp_obs.c:110-114 SEU 旋转等价，仅 S→N 取反）
    lat = math.radians(observer_lat)
    lon = math.radians(observer_lon)
    sin_l, cos_l = math.sin(lat), math.cos(lat)
    sin_b, cos_b = math.sin(lon), math.cos(lon)
    e_east = -sin_b * rx_s + cos_b * ry_s
    e_north = -sin_l * cos_b * rx_s - sin_l * sin_b * ry_s + cos_l * rz_s
    e_up = cos_l * cos_b * rx_s + cos_l * sin_b * ry_s + sin_l * rz_s
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, e_up / dist))))
    azimuth = (math.degrees(math.atan2(e_east, e_north)) + 360.0) % 360.0
    # 视线速度：卫星速度投影到站星方向单位矢量（远离为正）
    los = (rx_s / dist, ry_s / dist, rz_s / dist)
    range_rate = (v_ecef[0] * los[0] + v_ecef[1] * los[1] + v_ecef[2] * los[2])
    return {
        "satellite": satellite_name,
        "elevation": elevation,
        "azimuth": azimuth,
        "range_km": dist,
        "range_rate_kms": range_rate,
        "altitude_km": math.sqrt(r_ecef[0]**2 + r_ecef[1]**2 + r_ecef[2]**2) - WGS72_A,
        "epoch": epoch,
    }


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
    # unix -> UTC 儒略日（与 gpredict Date_Time 互逆）
    jd_utc = when / 86400.0 + 2440587.5
    return _state_from_satrec(sat, satellite_name, jd_utc,
                              observer_lat, observer_lon, observer_alt,
                              epoch=line1[18:32].strip())


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


def _bisect_threshold(el_fn, t_lo: float, t_hi: float, target: float,
                      tol_s: float = 0.25, max_iter: int = 40) -> float:
    """二分法求仰角过零（阈值）时刻。

    来源: gpredict repos/gpredict/src/predict-tools.c:172-174, 263 — 细扫到 |el|<0.005°。
    这里用时间二分，收敛到 tol_s（0.25s），对 LEO 仰角变化率 ~0.5°/s 即 ~0.125° 精度，
    远好于原固定 30s 步长（±15s、~7° 误差）。
    """
    f_lo = el_fn(t_lo)
    f_hi = el_fn(t_hi)
    if f_lo is None or f_hi is None:
        return t_lo
    # 若两端同侧，直接返回中点（不应发生，调用方保证跨越）
    if (f_lo - target) * (f_hi - target) > 0:
        return 0.5 * (t_lo + t_hi)
    for _ in range(max_iter):
        if t_hi - t_lo < tol_s:
            break
        t_mid = 0.5 * (t_lo + t_hi)
        f_mid = el_fn(t_mid)
        if f_mid is None:
            return t_mid
        if (f_mid - target) * (f_lo - target) <= 0:
            t_hi = t_mid
            f_hi = f_mid
        else:
            t_lo = t_mid
            f_lo = f_mid
    return 0.5 * (t_lo + t_hi)


def predict_passes(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    hours: float = 24.0,
    min_elevation: float = 10.0,
    satellite_type: str = "all",
    nominal_freq_hz: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """预测未来 hours 小时内的卫星过境事件。

    自适应粗/细扫描（对照 gpredict predict-tools.c:129-314）：
      a) 粗扫 60s 步长定位仰角跨越 min_elevation 的区间；
      b) 细扫：在跨越区间内二分法收敛到 ~0.25s 精度（对应仰角 ~0.1°，远优于
         原固定 30s 步长的 ±15s / ~7° 误差）；
      c) 上升沿 = AOS，下降沿 = LOS；pass 内 2s 细采样找最大仰角与多普勒范围。
    """
    coarse_step = 60.0   # 来源: gpredict predict-tools.c:156-160 粗扫量级（~0.5min）
    fine_step = 2.0      # pass 内采样步长，找中天/多普勒极值
    t0 = time.time()
    t_end = t0 + hours * 3600.0
    names = [n for n in BUILTIN_SATS
             if not (satellite_type == "weather" and not any(
                 k in n for k in ("NOAA", "METEOR", "FENGYUN")))
             and not (satellite_type == "amateur" and "ISS" not in n)]
    passes = []
    for name in names:
        catnr = BUILTIN_SATS[name]
        try:
            line1, line2 = fetch_tle(catnr)
        except Exception:
            continue
        sat = Satrec.twoline2rv(line1, line2)
        epoch_str = line1[18:32].strip()

        def _state_at(t_unix: float) -> Optional[Dict[str, Any]]:
            jd_utc = t_unix / 86400.0 + 2440587.5
            return _state_from_satrec(sat, name, jd_utc,
                                      observer_lat, observer_lon, observer_alt,
                                      epoch=epoch_str)

        def _el_at(t_unix: float) -> Optional[float]:
            st = _state_at(t_unix)
            return st["elevation"] if st else None

        # a) 粗扫：60s 步长记录 (t, el)
        coarse: List[Tuple[float, Optional[float]]] = []
        t = t0
        while t <= t_end:
            coarse.append((t, _el_at(t)))
            t += coarse_step
        # 跳过 sgp4 失效的段
        if all(e is None for _, e in coarse):
            continue

        # b) 找仰角 >= min_elevation 的粗采样段，细扫边界
        i = 0
        n = len(coarse)
        while i < n:
            ti, ei = coarse[i]
            if ei is None or ei < min_elevation:
                i += 1
                continue
            # 上升沿：找前一个 < min_elevation 的点
            j = i
            while j < n and coarse[j][1] is not None and coarse[j][1] >= min_elevation:
                j += 1
            # 本段为 coarse[i:j]（均 >= threshold）。
            # AOS = 在 coarse[i-1] 与 coarse[i] 之间二分（若 i==0 则 rise=ti）
            if i > 0 and coarse[i - 1][1] is not None:
                t_rise = _bisect_threshold(_el_at, coarse[i - 1][0], ti, min_elevation)
            else:
                t_rise = ti
            # LOS = 在 coarse[j-1] 与 coarse[j] 之间二分（若 j==n 则 set=coarse[j-1]）
            if j < n and coarse[j][1] is not None:
                t_set = _bisect_threshold(_el_at, coarse[j - 1][0], coarse[j][0], min_elevation)
            else:
                t_set = coarse[j - 1][0]

            # c) pass 内细采样找最大仰角、方位、多普勒
            seg_states = []
            ts = t_rise
            while ts <= t_set + 1e-6:
                st = _state_at(ts)
                if st is not None:
                    seg_states.append((ts, st))
                ts += fine_step
            if not seg_states:
                i = j
                continue
            max_idx = max(range(len(seg_states)),
                          key=lambda k: seg_states[k][1]["elevation"])
            t_cul, s_cul = seg_states[max_idx]
            s_rise = _state_at(t_rise) or seg_states[0][1]
            s_set = _state_at(t_set) or seg_states[-1][1]
            dop = None
            if nominal_freq_hz and seg_states:
                shifts = [-nominal_freq_hz * s["range_rate_kms"] / C_LIGHT
                          for _, s in seg_states]
                dop = {"min_hz": round(min(shifts), 1),
                       "max_hz": round(max(shifts), 1)}
            passes.append({
                "satellite": name,
                "rise_time": time.strftime("%H:%M:%S", time.localtime(t_rise)),
                "rise_azimuth": round(s_rise["azimuth"], 1),
                "culmination_time": time.strftime("%H:%M:%S", time.localtime(t_cul)),
                "max_elevation": round(s_cul["elevation"], 1),
                "culmination_azimuth": round(s_cul["azimuth"], 1),
                "set_time": time.strftime("%H:%M:%S", time.localtime(t_set)),
                "set_azimuth": round(s_set["azimuth"], 1),
                "duration_s": round(t_set - t_rise),
                "doppler_hz": dop,
            })
            i = j
    passes.sort(key=lambda p: p["rise_time"])
    return passes
