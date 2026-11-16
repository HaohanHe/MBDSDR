# SPDX-License-Identifier: MIT
"""同一时刻/同一卫星的三条 az/el + range 路径一致性守卫（P11-4）。

三条独立实现路径：

1. **过境预测**：``sat_passes.predict_passes``（skyfield ``_alt_az``，粗扫+二分）；
2. **天空视图**：``sat_passes._range_and_rate``（skyfield 相对位置/速度 + altaz，
   卫星跟踪器同源）；
3. **多普勒**：``orbit._state_from_satrec``（sgp4 TEME→ECEF→ENU，纯 sgp4 链）。

同一 UTC 时刻、同一 TLE，三者 az/el 应在容差内一致（默认 0.5°）。不一致时
返回逐项 delta 与 ``issues``，调用方必须对外报出，而不是各算各的。

**range 跨口径对拍**：az/el 是角度（比值），对距离的整体尺度/单位误差天然免疫——
两条链可能 az/el 完全一致却把 range 算错 1000×（m/km）或一个因子。天空视图与
多普勒两条链都独立给出 slant range（km），故对这两条额外对拍 range。
过境路径只有 AOS 方位 + 门限仰角、无独立距离，不参与 range 对拍。range 容差是
**独立口径**（相对 + 绝对下限，见 ``DEFAULT_TOL_RANGE_*``）：skyfield ICRS/WGS-84
与 sgp4 TEME/WGS-72 的合法差实测 ~0.001%（30 m 量级），容差取 0.1% 留 ~100× 裕度，
专抓尺度/单位错误而非合法模型差。

本模块只读、不修轨道；无 TLE/无过境时返回 ``checked=False`` 并说明原因。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .sat_passes import PASS_MIN_ALT_DEG

__all__ = [
    "DEFAULT_TOL_DEG",
    "DEFAULT_TOL_RANGE_REL",
    "DEFAULT_TOL_RANGE_ABS_KM",
    "check_pass_object",
    "check_next_pass_consistency",
    "check_time_consistency",
]

DEFAULT_TOL_DEG = 0.5

#: range 跨口径相对容差：skyfield ICRS/WGS-84 与 sgp4 TEME/WGS-72 的合法差
#: 实测 ~0.001%（30 m 量级）；取 0.1% 留 ~100× 裕度，专抓 az/el 抓不到的
#: 距离尺度/单位误差（如 m/km 1000×、因子错），而非合法模型差。
DEFAULT_TOL_RANGE_REL = 1e-3
#: range 绝对容差下限（km）：近距时兜底数值噪声，与相对容差取大者。
DEFAULT_TOL_RANGE_ABS_KM = 0.5


def _az_delta(a: float, b: float) -> float:
    """方位角环形差（-180..180）。"""
    d = (float(a) - float(b) + 180.0) % 360.0 - 180.0
    return abs(d)


def _unix_of_skyfield_time(t: Any) -> float:
    return float(t.utc_datetime().timestamp())


def _compare(
    paths: Dict[str, Dict[str, float]],
    tol_deg: float,
    tol_range_rel: float = DEFAULT_TOL_RANGE_REL,
    tol_range_abs_km: float = DEFAULT_TOL_RANGE_ABS_KM,
) -> Dict[str, Any]:
    """两两比较 az/el（所有路径）与 range（两路径都给出 range_km 时）。

    只比较 ``paths`` 中实际存在的路径对：``check_time_consistency`` 只喂
    sky_view/doppler 两条，过境对自动跳过。range 用独立口径容差（相对 +
    绝对下限取大者），因为 az/el 是角度、对距离整体尺度/单位误差免疫。
    """
    pairs = (("pass", "sky_view"), ("pass", "doppler"), ("sky_view", "doppler"))
    deltas: Dict[str, Dict[str, float]] = {}
    issues: List[str] = []
    for a, b in pairs:
        if a not in paths or b not in paths:
            continue
        pa, pb = paths[a], paths[b]
        daz = _az_delta(pa["az"], pb["az"])
        del_ = abs(float(pa["el"]) - float(pb["el"]))
        key = f"{a}_vs_{b}"
        d: Dict[str, float] = {"daz_deg": round(daz, 4), "del_deg": round(del_, 4)}
        if daz > tol_deg:
            issues.append(f"{a} 与 {b} 方位差 {daz:.3f}° > {tol_deg}°")
        if del_ > tol_deg:
            issues.append(f"{a} 与 {b} 仰角差 {del_:.3f}° > {tol_deg}°")
        # range：仅当两路径都独立给出距离时对拍（pass 路径无 range，跳过）。
        if "range_km" in pa and "range_km" in pb:
            ra, rb = float(pa["range_km"]), float(pb["range_km"])
            drange = abs(ra - rb)
            tol_km = max(tol_range_abs_km, tol_range_rel * max(abs(ra), abs(rb)))
            d["drange_km"] = round(drange, 4)
            d["range_tol_km"] = round(tol_km, 4)
            if drange > tol_km:
                issues.append(
                    f"{a} 与 {b} 距离差 {drange:.3f} km > {tol_km:.3f} km")
        deltas[key] = d
    return {"deltas": deltas, "issues": issues}


def check_pass_object(
    pass_: Any,
    tle_lines: Sequence[str],
    ground_station: Any,
    tol_deg: float = DEFAULT_TOL_DEG,
    threshold_alt_deg: float = 5.0,
) -> Dict[str, Any]:
    """对一个已算出的过境对象，在 AOS（升起）时刻做三路径对拍。

    ``pass_`` 需具备 ``rise_time``（skyfield Time）、``rise_az`` 与用于
    升起门限的仰角（由调用方给出，缺省用 0）。实际比较的是该时刻的
    az/el；三路径都按显式 UTC 计算，不受时间倍率影响。
    """
    from .sat_passes import _parse_tle, _range_and_rate, make_timescale
    from .orbit import _state_from_satrec

    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return {"checked": False, "consistent": None,
                "reason": "TLE 无法解析"}
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        from skyfield.api import EarthSatellite, Topos
        sat = EarthSatellite(line1, line2, name, ts)
        topo = Topos(latitude_degrees=ground_station.lat_deg,
                     longitude_degrees=ground_station.lon_deg,
                     elevation_m=ground_station.alt_m)
        diff = sat - topo
    except Exception as exc:  # skyfield 缺失/坏 TLE
        return {"checked": False, "consistent": None,
                "reason": f"skyfield 路径不可用: {exc}"}

    t_rise = pass_.rise_time
    at_unix = _unix_of_skyfield_time(t_rise)

    # 路径 1：过境预测（AOS 方位 + 过境门限仰角）
    pass_path = {"az": float(pass_.rise_az),
                 "el": float(threshold_alt_deg)}

    # 路径 2：天空视图（sat_tracker 同源 _range_and_rate）
    rr = _range_and_rate(diff, t_rise)
    if rr is None:
        return {"checked": False, "consistent": None,
                "reason": "天空视图路径传播失败"}
    sky_path = {"az": float(rr[0]), "el": float(rr[1]), "range_km": float(rr[2])}

    # 路径 3：多普勒/跟踪链（sgp4 TEME→ECEF→ENU）
    try:
        from sgp4.api import Satrec
        satrec = Satrec.twoline2rv(line1, line2)
    except Exception as exc:
        return {"checked": False, "consistent": None,
                "reason": f"sgp4 路径不可用: {exc}"}
    st = _state_from_satrec(satrec, name, at_unix / 86400.0 + 2440587.5,
                            ground_station.lat_deg, ground_station.lon_deg,
                            ground_station.alt_m, epoch=line1[18:32].strip())
    if st is None:
        return {"checked": False, "consistent": None,
                "reason": "sgp4 路径传播失败"}
    dop_path = {"az": float(st["azimuth"]), "el": float(st["elevation"]),
                "range_km": float(st["range_km"])}

    paths = {"pass": pass_path, "sky_view": sky_path, "doppler": dop_path}
    cmp_ = _compare(paths, tol_deg)
    return {
        "checked": True,
        "consistent": not cmp_["issues"],
        "satellite": name,
        "at_utc": t_rise.utc_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "at_unix": at_unix,
        "tolerance_deg": float(tol_deg),
        "paths": paths,
        "deltas": cmp_["deltas"],
        "issues": cmp_["issues"],
        "warning": "；".join(cmp_["issues"]),
    }


def check_next_pass_consistency(
    tle_lines: Sequence[str],
    ground_station: Any,
    hours: float = 24.0,
    min_alt: float = PASS_MIN_ALT_DEG,
    tol_deg: float = DEFAULT_TOL_DEG,
    start_unix: Optional[float] = None,
) -> Dict[str, Any]:
    """预测下一次过境并立即做三路径对拍。无过境时 checked=False。

    默认门与 ``sat_passes.PASS_MIN_ALT_DEG``（10°）同源：诊断工具若自带
    5° 的「可见门」，它对「下一次过境」的对拍结论与 ``next_pass()``
    不是同一次过境，对拍本身失真。
    ``start_unix`` 显式钉死预测起点（Unix UTC 秒）；缺省走 TimeEngine。
    """
    from .sat_passes import predict_passes

    anomalies: List[Dict[str, Any]] = []
    passes = predict_passes(tle_lines, ground_station, hours=hours,
                            min_alt=min_alt, anomalies=anomalies,
                            start_unix=start_unix)
    if not passes:
        return {"checked": False, "consistent": None,
                "reason": "未来窗口内无过境", "anomalies": anomalies}
    rep = check_pass_object(passes[0], tle_lines, ground_station,
                            tol_deg=tol_deg, threshold_alt_deg=min_alt)
    rep["anomalies"] = anomalies
    return rep


def check_time_consistency(
    tle_lines: Sequence[str],
    ground_station: Any,
    at_unix: float,
    tol_deg: float = DEFAULT_TOL_DEG,
) -> Dict[str, Any]:
    """任意时刻只用两条独立链（天空视图 vs sgp4）对拍 az/el + range。

    过境路径需要 AOS，这里不伪造过境；返回 ``checked`` 与两路径 delta。
    比较逻辑与 ``check_pass_object`` 共用 ``_compare``（单一比较口径）。
    """
    from .sat_passes import _parse_tle, _range_and_rate, make_timescale
    from .orbit import _state_from_satrec

    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return {"checked": False, "consistent": None, "reason": "TLE 无法解析"}
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        from skyfield.api import EarthSatellite, Topos
        sat = EarthSatellite(line1, line2, name, ts)
        topo = Topos(latitude_degrees=ground_station.lat_deg,
                     longitude_degrees=ground_station.lon_deg,
                     elevation_m=ground_station.alt_m)
        t = ts.from_datetime(_utc_dt(at_unix))
        rr = _range_and_rate(sat - topo, t)
    except Exception as exc:
        return {"checked": False, "consistent": None,
                "reason": f"skyfield 路径不可用: {exc}"}
    if rr is None:
        return {"checked": False, "consistent": None,
                "reason": "天空视图路径传播失败"}
    try:
        from sgp4.api import Satrec
        satrec = Satrec.twoline2rv(line1, line2)
    except Exception as exc:
        return {"checked": False, "consistent": None,
                "reason": f"sgp4 路径不可用: {exc}"}
    st = _state_from_satrec(satrec, name, at_unix / 86400.0 + 2440587.5,
                            ground_station.lat_deg, ground_station.lon_deg,
                            ground_station.alt_m, epoch=line1[18:32].strip())
    if st is None:
        return {"checked": False, "consistent": None,
                "reason": "sgp4 路径传播失败"}
    paths = {
        "sky_view": {"az": float(rr[0]), "el": float(rr[1]), "range_km": float(rr[2])},
        "doppler": {"az": float(st["azimuth"]), "el": float(st["elevation"]),
                    "range_km": float(st["range_km"])},
    }
    cmp_ = _compare(paths, tol_deg)
    return {
        "checked": True, "consistent": not cmp_["issues"],
        "satellite": name, "at_unix": float(at_unix),
        "tolerance_deg": float(tol_deg), "paths": paths,
        "deltas": cmp_["deltas"],
        "issues": cmp_["issues"], "warning": "；".join(cmp_["issues"]),
    }


def _utc_dt(unix: float):
    from datetime import timezone
    from datetime import datetime
    return datetime.fromtimestamp(float(unix), tz=timezone.utc)
