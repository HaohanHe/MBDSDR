# SPDX-License-Identifier: MIT
"""R5 卫星追踪与过境 —— 过境预报与独立参考（skyfield/SGP4）对拍。

判据（自动断言，无需射频硬件）：
  * TLE 可取得（缓存/在线），记录带来源与历元
  * 24h 内至少 2 颗卫星有过境，列表按升起时刻升序、峰值仰角 >= 门限
  * 与 skyfield 用**同一份 TLE**独立递推：峰值仰角误差 <= 1.0°
  * 升起时刻误差 <= 60 s
  * TLE 完全不可得时如实 UNVERIFIED(no_tle)，不用内置近似值冒充实测
人眼项：无（过境面板视觉属 R6）。
"""
from __future__ import annotations

import argparse
import datetime
import re
import threading
import time

import common

# 观测站是**参数**不是测量值。默认用中性示例站，不写任何真实或近似的
# 站址：这个脚本会被拷到别人机器上跑，坐标必须是中性的。
# `SAMPLE_SITE`（45°N/120°E）在源码里明写「不指向任何真实默认站址」；
# 2026-11-09 真机复核发现旧默认值（43.88/125.32）是某次调试留下的近似
# 真实位置，按仓库铁律改回中性站，不再引入第二份坐标常量。
from mbdsdr_ai.solar_system import SAMPLE_SITE

DEFAULT_OBS = (SAMPLE_SITE.latitude_deg, SAMPLE_SITE.longitude_deg,
               SAMPLE_SITE.height_m)
TARGETS = [25338, 28654, 33591, 25544]  # NOAA-15/18/19 + ISS
WINDOW_H = 24.0
MIN_EL = 5.0
#: 我方过境与 skyfield 参考配对的时间窗（秒）。超过它就不算「同一场」，
#: 不拿错配的场次去算角度误差（那会把配对问题伪装成精度问题）。
PAIR_MAX_GAP_S = 1800.0


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _with_timeout(fn, seconds: float):
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"操作超过 {seconds:.0f}s 未返回（网络/递推阻塞）")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _skyfield_pass(l1: str, l2: str, name: str, lat: float, lon: float,
                   alt_m: float, start_unix: float, hours: float, min_el: float):
    from skyfield.api import EarthSatellite, load, wgs84

    ts = load.timescale()
    sat = EarthSatellite(l1, l2, name, ts)
    obs = wgs84.latlon(lat, lon, elevation_m=alt_m)
    step = 5  # 5s 粗扫；LEO 中天附近仰角变化快，10s 会带来 ~1° 采样误差
    n = int(hours * 3600 / step)
    dts = [datetime.datetime.fromtimestamp(start_unix + i * step, datetime.timezone.utc)
           for i in range(n)]
    t = ts.utc(dts)
    alt_deg = (sat - obs).at(t).altaz()[0].degrees

    above = alt_deg >= min_el
    if not above.any():
        return None
    i_rise = int(next(i for i, v in enumerate(above) if v))
    i_set = i_rise
    while i_set < len(above) and above[i_set]:
        i_set += 1
    seg = range(i_rise, max(i_rise + 1, i_set))
    i_peak = int(max(seg, key=lambda i: alt_deg[i]))
    return {
        "rise_unix": start_unix + i_rise * step,
        "max_el_deg": float(alt_deg[i_peak]),
        "peak_unix": start_unix + i_peak * step,
    }


def _tle_checksum(line: str) -> int:
    """NORAD TLE 校验和（标准算法：数字相加，'-' 记 1，其余记 0，取模 10）。"""
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def _tle_line_ok(line: str) -> bool:
    return len(line) >= 69 and line[68].isdigit() and int(line[68]) == _tle_checksum(line)


def _pass_epoch(hhmmss: str, start_unix: float) -> float:
    """rise_time 只有 HH:MM:SS（本地时），按起始本地日期补全；跨午夜则 +1 天。"""
    y, m, d = time.localtime(start_unix)[:3]
    parts = [int(x) for x in hhmmss.split(":")] + [0, 0]
    hh, mm, ss = parts[0], parts[1], parts[2]
    epoch = time.mktime((y, m, d, hh, mm, ss, 0, 0, -1))
    if epoch < start_unix - 5:
        epoch += 86400
    return epoch


def run(lat: float, lon: float, alt_m: float) -> dict:
    out = common.artifact_dir("r5_satellite_passes")
    from mbdsdr_ai.orbit import fetch_tle_record, predict_passes
    now = time.time()
    tle_recs: dict[int, dict] = {}
    tle_errors: dict[int, str] = {}
    for norad in TARGETS:
        try:
            rec = _with_timeout(lambda n=norad: fetch_tle_record(n, max_age_hours=24.0), 20.0)
            tle_recs[norad] = rec
        except Exception as e:
            tle_errors[norad] = f"{type(e).__name__}: {e}"

    if not tle_recs:
        return common.skip_result(
            "R5", "卫星追踪与过境", "NO_DATA",
            "全部目标 TLE 不可用（离线无缓存/下载失败）；不使用内置近似值冒充实测过境，"
            "本环不产出过境数字。",
            {"tle_errors": tle_errors,
             "observer": {"lat": lat, "lon": lon, "alt_m": alt_m}},
            [common.check("tle_available", False, "全部目标 TLE 不可用", skipped=True)],
            "TLE 数据不可用：请联网刷新，或确认 orbit TLE 缓存存在后重试。",
            [], False)

    try:
        passes = _with_timeout(
            # 两条链路的单位不同，别混：产品 predict_passes 收**公里**
            # （内部 geodetic_to_ecef 是 WGS-72 km），skyfield 收**米**
            # （_skyfield_pass 的 elevation_m）。这里 alt_m 是米，产品侧
            # 必须 /1000；2026-11-09 真机复核发现旧代码直接传米，
            # 站址被抬高 1000 倍（200 m → 200 km），仰角错到几十度。
            lambda: predict_passes(lat, lon, alt_m / 1000.0, WINDOW_H, MIN_EL,
                                   satellite_type="all", start_unix=now), 60.0)
    except Exception as e:
        return common.result("R5", "卫星追踪与过境", common.FAIL,
                             {"error": str(e)},
                             [common.check("predict_passes", False, str(e))],
                             "过境预报计算失败，请查看日志或重试。", [], False)

    try:
        def sky_all():
            res = {}
            for norad, rec in tle_recs.items():
                r = _skyfield_pass(rec["line1"], rec["line2"], str(rec.get("name", norad)),
                                   lat, lon, alt_m, now, WINDOW_H, MIN_EL)
                if r:
                    res[_norm(rec.get("name", ""))] = r
            return res

        ref = _with_timeout(sky_all, 120.0)
    except Exception as e:
        return common.skip_result(
            "R5", "卫星追踪与过境", "NO_DATA",
            f"缺 skyfield 独立递推参考（{e}），无法量化过境误差；"
            "过境列表仍可由面板复看，但本环不判 PASS。",
            {"tle_source": {n: r.get("source") for n, r in tle_recs.items()},
             "error": str(e)},
            [common.check("skyfield_reference", False, f"独立参考不可用：{e}", skipped=True)],
            "缺少 skyfield 独立递推参考，无法量化误差。", [], False)

    epochs = [_pass_epoch(p["rise_time"], now) for p in passes]
    chrono = all(epochs[i] <= epochs[i + 1] for i in range(len(epochs) - 1))
    # TLE 校验和是**外部可验证**的完整性证据（NORAD 标准算法），不是自家口径。
    cs_ok = all(_tle_line_ok(rec["line1"]) and _tle_line_ok(rec["line2"])
                for rec in tle_recs.values())
    checks = [common.check("tle_available", True,
                           f"{len(tle_recs)}/{len(TARGETS)} 颗 TLE 可用"),
              common.check("tle_checksums_valid", cs_ok,
                           "全部 TLE 行校验和符合 NORAD 标准" if cs_ok
                           else "有 TLE 行校验和不符（数据被截断/篡改）"),
              common.check("passes_sorted", chrono,
                           f"{len(passes)} 条过境按补全日期后的升起时刻"
                           f"{'升序' if chrono else '非升序：跨午夜顺序失真（按 HH:MM:SS 字符串排序的缺陷）'}"),
              common.check("elevation_ge_gate",
                           all(float(p["max_elevation"]) >= MIN_EL for p in passes),
                           f"全部 >= {MIN_EL}°")]

    findings = []
    for norad, rec in tle_recs.items():
        key = _norm(rec.get("name", ""))
        mb = [p for p in passes if _norm(p.get("satellite", "")) == key]
        rf = ref.get(key)
        item = {"norad": norad, "name": rec.get("name"), "tle_source": rec.get("source")}
        if not mb:
            item["result"] = "24h 内无过境（或低于门限）"
        elif not rf:
            item["result"] = "独立参考无过境"
        else:
            mb_epochs = [(_pass_epoch(p["rise_time"], now), p) for p in mb]
            # 配**同一场**过境再比数值：两边「第一场」的定义未必一致
            # （5° 门限附近的掠射场一方可能漏/多），按升序取第一条会拿
            # A 场去比 B 场，误差直接飙到小时级（2026-11-09 实测 31989 s）。
            rise_local, first = min(mb_epochs,
                                    key=lambda t: abs(t[0] - rf["rise_unix"]))
            rise_err = abs(rise_local - rf["rise_unix"])
            if rise_err > PAIR_MAX_GAP_S:
                # 最近一场也差半小时以上 = 过境集合本身不一致，
                # 不拿它算角度误差（那是错配，不是精度问题）。
                item["result"] = (f"过境集合不一致：我方最近一场与参考差 "
                                  f"{rise_err / 3600.0:.1f} h")
                item["passes_mbdsdr"] = len(mb)
                findings.append(item)
                checks.append(common.check(
                    f"pass_pair_{norad}", False,
                    f"最近一场差 {rise_err:.0f}s > {PAIR_MAX_GAP_S:.0f}s，"
                    "无法配对同一场过境"))
                continue
            el_err = abs(float(first["max_elevation"]) - rf["max_el_deg"])
            item.update({
                "mbdsdr_pass_max_el_deg": round(float(first["max_elevation"]), 1),
                "skyfield_pass_max_el_deg": round(rf["max_el_deg"], 1),
                "elevation_error_deg": round(el_err, 2),
                "rise_error_s": round(rise_err, 1),
            })
            checks.append(common.check(f"el_err_{norad}", el_err <= 1.0,
                                       f"{el_err:.2f}° <= 1.0°"))
            checks.append(common.check(f"rise_err_{norad}", rise_err <= 60.0,
                                       f"{rise_err:.1f}s <= 60s"))
        findings.append(item)

    metrics = {
        "observer": {"lat": lat, "lon": lon, "alt_m": alt_m, "note": "观测站为参数输入，非实测"},
        "window_hours": WINDOW_H,
        "min_elevation_deg": MIN_EL,
        "pass_count": len(passes),
        "satellites": findings,
        "tle_errors": tle_errors,
        "tle_checksums_valid": cs_ok,
        "reference_note": ("独立参考=skyfield 对**同一份 TLE** 递推；两者共用 sgp4 内核，"
                           "因此误差是「实现一致性」，不是外部星历真值。"),
    }
    measured = [f for f in findings if "elevation_error_deg" in f]
    checks.append(common.check("measured_sats", len(measured) >= 2,
                               f"{len(measured)} 颗完成误差量化"))
    status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
    metrics["offline_substitute"] = "PASS" if status == common.PASS else "FAIL"
    return common.result(
        "R5", "卫星追踪与过境", status, metrics, checks,
        "过境预报与独立参考不一致：先刷新 TLE（过期轨道会放大误差），再复跑。",
        [], False, [],
        "离线可复跑（不依赖射频硬件，只依赖 TLE+skyfield）。独立参考使用 skyfield "
        "对同一份 TLE 递推；误差=实现一致性，不是外部星历真值。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="R5 卫星追踪与过境")
    ap.add_argument("--lat", type=float, default=DEFAULT_OBS[0])
    ap.add_argument("--lon", type=float, default=DEFAULT_OBS[1])
    ap.add_argument("--alt", type=float, default=DEFAULT_OBS[2])
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.out:
        import os
        os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = args.out
    out = common.artifact_dir("r5_satellite_passes")
    res = common.guarded("R5", "卫星追踪与过境",
                         lambda: run(args.lat, args.lon, args.alt), device_required=False)
    raise SystemExit(common.emit(res, out))
