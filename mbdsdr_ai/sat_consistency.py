# SPDX-License-Identifier: MIT
"""同一时刻/同一卫星的三条 az/el 路径一致性守卫（P11-4）。

三条独立实现路径：

1. **过境预测**：``sat_passes.predict_passes``（skyfield ``_alt_az``，粗扫+二分）；
2. **天空视图**：``sat_passes._range_and_rate``（skyfield 相对位置/速度 + altaz，
   卫星跟踪器同源）；
3. **多普勒**：``orbit._state_from_satrec``（sgp4 TEME→ECEF→ENU，纯 sgp4 链）。

同一 UTC 时刻、同一 TLE，三者 az/el 应在容差内一致（默认 0.5°）。不一致时
返回逐项 delta 与 ``issues``，调用方必须对外报出，而不是各算各的。

本模块只读、不修轨道；无 TLE/无过境时返回 ``checked=False`` 并说明原因。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "DEFAULT_TOL_DEG",
    "check_pass_object",
    "check_next_pass_consistency",
    "check_time_consistency",
]

DEFAULT_TOL_DEG = 0.5


def _az_delta(a: float, b: float) -> float:
    """方位角环形差（-180..180）。"""
    d = (float(a) - float(b) + 180.0) % 360.0 - 180.0
    return abs(d)


def _unix_of_skyfield_time(t: Any) -> float:
    return float(t.utc_datetime().timestamp())


def _compare(paths: Dict[str, Dict[str, float]], tol_deg: float) -> Dict[str, Any]:
    """三路径两两比较 az/el，返回 deltas 与 issues。"""
    pairs = (("pass", "sky_view"), ("pass", "doppler"), ("sky_view", "doppler"))
    deltas: Dict[str, Dict[str, float]] = {}
    issues: List[str] = []
    for a, b in pairs:
        pa, pb = paths[a], paths[b]
        daz = _az_delta(pa["az"], pb["az"])
        del_ = abs(float(pa["el"]) - float(pb["el"]))
        key = f"{a}_vs_{b}"
        deltas[key] = {"daz_deg": round(daz, 4), "del_deg": round(del_, 4)}
        if daz > tol_deg:
            issues.append(f"{a} 与 {b} 方位差 {daz:.3f}° > {tol_deg}°")
        if del_ > tol_deg:
            issues.append(f"{a} 与 {b} 仰角差 {del_:.3f}° > {tol_deg}°")
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
    sky_path = {"az": float(rr[0]), "el": float(rr[1])}

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
    dop_path = {"az": float(st["azimuth"]), "el": float(st["elevation"])}

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
    min_alt: float = 5.0,
    tol_deg: float = DEFAULT_TOL_DEG,
    start_unix: Optional[float] = None,
) -> Dict[str, Any]:
    """预测下一次过境并立即做三路径对拍。无过境时 checked=False。

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
    """任意时刻只用两条独立链（天空视图 vs sgp4）对拍 az/el。

    过境路径需要 AOS，这里不伪造过境；返回 ``checked`` 与两路径 delta。
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
        "sky_view": {"az": float(rr[0]), "el": float(rr[1])},
        "doppler": {"az": float(st["azimuth"]), "el": float(st["elevation"])},
    }
    daz = _az_delta(paths["sky_view"]["az"], paths["doppler"]["az"])
    del_ = abs(paths["sky_view"]["el"] - paths["doppler"]["el"])
    issues = []
    if daz > tol_deg:
        issues.append(f"sky_view 与 doppler 方位差 {daz:.3f}° > {tol_deg}°")
    if del_ > tol_deg:
        issues.append(f"sky_view 与 doppler 仰角差 {del_:.3f}° > {tol_deg}°")
    return {
        "checked": True, "consistent": not issues,
        "satellite": name, "at_unix": float(at_unix),
        "tolerance_deg": float(tol_deg), "paths": paths,
        "deltas": {"sky_view_vs_doppler": {
            "daz_deg": round(daz, 4), "del_deg": round(del_, 4)}},
        "issues": issues, "warning": "；".join(issues),
    }


def _utc_dt(unix: float):
    from datetime import timezone
    from datetime import datetime
    return datetime.fromtimestamp(float(unix), tz=timezone.utc)
