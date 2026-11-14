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
import json
import os
import pathlib
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
#: skyfield 独立参考的粗扫步长（秒）。LEO 中天附近仰角变化快，10s 会带来
#: ~1° 采样误差；写进证据快照，复算必须用同一个值。
SKYFIELD_REF_STEP_S = 5.0

# ---------------------------------------------------------------- 证据快照
# 判据不可复跑的根因：每次实时取 TLE、窗口起点取 time.time()、结果不落盘。
# 这里把「这次实跑到底用了什么」原样钉进 docs/hw-acceptance/evidence/<UTC>/，
# 复算脚本只吃这份快照，不再联网、不再取新 TLE。
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EVIDENCE_ENV = "MBDSDR_HW_EVIDENCE_DIR"
EVIDENCE_SCHEMA = 1
EVIDENCE_FILES = ("manifest.json", "passes.json", "tle.txt")


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
    step = SKYFIELD_REF_STEP_S  # 5s 粗扫；LEO 中天附近仰角变化快，10s 会带来 ~1° 采样误差
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


def _utc_iso(unix: float) -> str:
    return datetime.datetime.fromtimestamp(
        unix, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pass_unix(p: dict, now: float) -> float:
    """过境的 UTC 升起秒：优先权威字段 ``rise_epoch_unix``。

    旧路径用本地 ``rise_time`` 反推（:func:`_pass_epoch`），换时区就复算不出
    同一时刻；证据快照复算必须与实跑同一口径，因此优先读 UTC 字段。
    """
    v = p.get("rise_epoch_unix")
    return float(v) if v is not None else _pass_epoch(p["rise_time"], now)


def _build_findings(target_recs: dict, passes: list, ref: dict,
                    now: float) -> tuple[list, list]:
    """把「我方每颗星最近一场 vs skyfield 参考」配成 findings + checks。"""
    findings: list = []
    checks: list = []
    for norad, rec in target_recs.items():
        key = _norm(rec.get("name", ""))
        mb = [p for p in passes if _norm(p.get("satellite", "")) == key]
        rf = ref.get(key)
        item = {"norad": norad, "name": rec.get("name"), "tle_source": rec.get("source")}
        if not mb:
            item["result"] = "24h 内无过境（或低于门限）"
        elif not rf:
            item["result"] = "独立参考无过境"
        else:
            mb_epochs = [(_pass_unix(p, now), p) for p in mb]
            # 配**同一场**过境再比数值：两边「第一场」的定义未必一致
            # （5° 门限附近的掠射场一方可能漏/多），按升序取第一条会拿
            # A 场去比 B 场，误差直接飙到小时级（2026-11-09 实测 31989 s）。
            rise_unix, first = min(mb_epochs,
                                   key=lambda t: abs(t[0] - rf["rise_unix"]))
            rise_err = abs(rise_unix - rf["rise_unix"])
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
                "matched_rise_epoch_unix": round(rise_unix, 3),
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
    return findings, checks


# ---------------------------------------------------------------- 证据快照
def evidence_root() -> pathlib.Path:
    """证据根目录；测试/复跑用 ``MBDSDR_HW_EVIDENCE_DIR`` 重定向。"""
    override = (os.environ.get(EVIDENCE_ENV) or "").strip()
    if override:
        return pathlib.Path(override)
    return REPO_ROOT / "docs" / "hw-acceptance" / "evidence"


def _pass_rows(passes: list, findings: list) -> list:
    """每条过境的 AOS/LOS/峰值，并挂上该场的误差（没有配对则 None）。"""
    errs = {}
    for f in findings:
        if "matched_rise_epoch_unix" in f:
            errs[round(float(f["matched_rise_epoch_unix"]), 3)] = (
                f.get("elevation_error_deg"), f.get("rise_error_s"))
    rows = []
    for p in passes:
        el_err, rise_err = errs.get(round(float(p["rise_epoch_unix"]), 3), (None, None))
        rows.append({
            "satellite": p.get("satellite"),
            "rise_utc": p.get("rise_utc"),
            "rise_epoch_unix": p.get("rise_epoch_unix"),
            "culmination_utc": p.get("culmination_utc"),
            "culmination_epoch_unix": p.get("culmination_epoch_unix"),
            "set_utc": p.get("set_utc"),
            "set_epoch_unix": p.get("set_epoch_unix"),
            "max_elevation": p.get("max_elevation"),
            "elevation_error_deg": el_err,
            "rise_error_s": rise_err,
        })
    return rows


def write_evidence(run_unix: float, station: tuple, tle_recs: dict,
                   tle_errors: dict, passes: list, ref: dict, findings: list,
                   checks: list, status: str) -> pathlib.Path:
    """把本次实跑的「用了什么 + 算出什么」原子落盘。

    实时取 TLE 也必须把 line1/line2、来源、epoch 一起记下来；缺任何一项
    直接抛错（写半个快照比不写更坏）。TLE 完全不可得的 SKIP 运行允许空 TLE，
    但 manifest 会标 ``status=SKIP``，复算/校验据此区分「没数据」与「没记录」。
    """
    from mbdsdr_ai.orbit import (PASS_BISECT_TOL_S, PASS_COARSE_STEP_S,
                                 PASS_FINE_STEP_S)
    from mbdsdr_ai.time_basis import tle_epoch_utc

    if not tle_recs and status != common.SKIP:
        raise ValueError("非 SKIP 运行必须记录 TLE 原文与来源；实时取数也要落盘")

    tle_meta: dict = {}
    for catnr, rec in tle_recs.items():
        line1, line2 = rec.get("line1"), rec.get("line2")
        source = rec.get("source")
        if not line1 or not line2:
            raise ValueError(f"catnr={catnr} 缺 TLE 原文：实时取数也必须记录 line1/line2")
        if not source:
            raise ValueError(f"catnr={catnr} 缺 TLE 来源（source）：无法证明数据来历")
        epoch_dt = tle_epoch_utc(line1)
        if epoch_dt is None:
            raise ValueError(f"catnr={catnr} TLE 无 epoch，无法固定轨道时刻")
        epoch = _utc_iso(epoch_dt.replace(tzinfo=datetime.timezone.utc).timestamp()
                         if epoch_dt.tzinfo is None else epoch_dt.timestamp())
        fetched = rec.get("fetched_at")
        fetched = float(fetched) if fetched is not None else None
        tle_meta[str(catnr)] = {
            "name": rec.get("name"),
            "epoch_utc": epoch,
            "source": source,
            "fetched_at_unix": fetched,
            "fetched_at_utc": _utc_iso(fetched) if fetched is not None else None,
            "refresh_status": rec.get("refresh_status"),
            "refresh_error": rec.get("refresh_error", ""),
            "line1": line1,
            "line2": line2,
        }

    root = evidence_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(
        run_unix, datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / stamp
    n = 2
    while path.exists():
        path = root / f"{stamp}-{n}"
        n += 1
    path.mkdir(parents=True)

    lines = []
    for catnr in sorted(tle_meta, key=int):
        meta = tle_meta[catnr]
        lines.append(f"# {meta['name']} catnr={catnr} epoch={meta['epoch_utc']} "
                     f"source={meta['source']}")
        lines.append(meta["line1"])
        lines.append(meta["line2"])
    (path / "tle.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = {
        "schema": EVIDENCE_SCHEMA,
        "ring": "R5",
        "run_utc": _utc_iso(run_unix),
        "run_unix": run_unix,
        "station": {"lat": float(station[0]), "lon": float(station[1]),
                    "alt_m": float(station[2]), "alt_km": float(station[2]) / 1000.0},
        "window_hours": WINDOW_H,
        "min_elevation_deg": MIN_EL,
        "params": {
            "coarse_step_s": PASS_COARSE_STEP_S,
            "fine_step_s": PASS_FINE_STEP_S,
            "bisect_tol_s": PASS_BISECT_TOL_S,
            "skyfield_ref_step_s": SKYFIELD_REF_STEP_S,
            "pair_max_gap_s": PAIR_MAX_GAP_S,
        },
        "targets": list(TARGETS),
        "tle": tle_meta,
        "tle_errors": {str(k): v for k, v in tle_errors.items()},
        "status": status,
        "pass_count": len(passes),
        "checks": checks,
    }
    (path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {
        "passes": _pass_rows(passes, findings),
        "reference": ref,
        "findings": findings,
    }
    (path / "passes.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def verify_evidence(path) -> list[str]:
    """快照完整性判据：缺文件/缺字段/空 TLE/门限越界都返回问题列表。

    空列表 = 齐全；非空 = 红。只认快照本身，不联网、不读当前 TLE 缓存。
    """
    path = pathlib.Path(path)
    problems: list = []
    for name in EVIDENCE_FILES:
        if not (path / name).is_file():
            problems.append(f"缺文件 {name}")
    if problems:
        return problems
    try:
        man = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        payload = json.loads((path / "passes.json").read_text(encoding="utf-8"))
        tle_txt = (path / "tle.txt").read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 坏快照如实报红
        return [f"快照不可解析：{type(e).__name__}: {e}"]

    iso = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"
    if man.get("schema") != EVIDENCE_SCHEMA:
        problems.append(f"schema={man.get('schema')!r} != {EVIDENCE_SCHEMA}")
    if not re.match(iso, str(man.get("run_utc", ""))):
        problems.append("manifest.run_utc 不是 UTC ISO 时刻")
    if not isinstance(man.get("run_unix"), (int, float)) or man["run_unix"] <= 0:
        problems.append("manifest.run_unix 缺失/非正")
    st = man.get("station") or {}
    for k in ("lat", "lon", "alt_m", "alt_km"):
        if not isinstance(st.get(k), (int, float)):
            problems.append(f"manifest.station.{k} 缺失/非数值")
    for k in ("window_hours", "min_elevation_deg"):
        v = man.get(k)
        if not isinstance(v, (int, float)) or v <= 0:
            problems.append(f"manifest.{k} 缺失/非正")
    params = man.get("params") or {}
    for k in ("coarse_step_s", "fine_step_s", "bisect_tol_s",
              "skyfield_ref_step_s", "pair_max_gap_s"):
        v = params.get(k)
        if not isinstance(v, (int, float)) or v <= 0:
            problems.append(f"manifest.params.{k} 缺失/非正")

    tle = man.get("tle") or {}
    if not tle:
        if man.get("status") != common.SKIP:
            problems.append("非 SKIP 运行却没有 TLE 记录（实时取数未落盘）")
    for catnr, meta in tle.items():
        for k in ("line1", "line2", "source", "epoch_utc", "name"):
            if not meta.get(k):
                problems.append(f"tle[{catnr}].{k} 缺失")
        for line_key in ("line1", "line2"):
            line = str(meta.get(line_key, ""))
            if line and not _tle_line_ok(line):
                problems.append(f"tle[{catnr}].{line_key} 校验和不符")
            if line and line not in tle_txt:
                problems.append(f"tle[{catnr}].{line_key} 未出现在 tle.txt 原文里")
        if not re.match(iso, str(meta.get("epoch_utc", ""))):
            problems.append(f"tle[{catnr}].epoch_utc 不是 UTC ISO 时刻")

    gate = man.get("min_elevation_deg")
    rows = payload.get("passes")
    if not isinstance(rows, list):
        problems.append("passes.json.passes 不是列表")
    else:
        if len(rows) != man.get("pass_count"):
            problems.append(
                f"pass_count={man.get('pass_count')} 与 passes 条数 {len(rows)} 不符")
        need = ("satellite", "rise_utc", "rise_epoch_unix", "culmination_utc",
                "culmination_epoch_unix", "set_utc", "set_epoch_unix",
                "max_elevation", "elevation_error_deg", "rise_error_s")
        for i, row in enumerate(rows):
            for k in need:
                if k not in row:
                    problems.append(f"passes[{i}].{k} 缺失")
            r, s = row.get("rise_epoch_unix"), row.get("set_epoch_unix")
            if isinstance(r, (int, float)) and isinstance(s, (int, float)) and r >= s:
                problems.append(f"passes[{i}] 升起时刻不早于落下时刻")
            el = row.get("max_elevation")
            if isinstance(el, (int, float)) and isinstance(gate, (int, float)) \
                    and el < gate - 1e-9:
                problems.append(f"passes[{i}].max_elevation={el} < 门限 {gate}")
    if not isinstance(payload.get("reference"), dict):
        problems.append("passes.json.reference 不是对象")
    if not isinstance(payload.get("findings"), list):
        problems.append("passes.json.findings 不是列表")
    return problems


def _run_core(lat: float, lon: float, alt_m: float, ctx: dict) -> dict:
    out = common.artifact_dir("r5_satellite_passes")
    from mbdsdr_ai.orbit import (BUILTIN_SATS, builtin_satellite_names,
                                 fetch_tle_record, predict_passes)
    now = time.time()
    ctx["now"] = now
    # 一次取全内置目录：产品 predict_passes 与 skyfield 参考、证据快照都用
    # **同一批** TLE 记录（原实现里产品自己再取一次，快照记的未必是它用的）。
    tle_recs: dict[int, dict] = {}
    tle_errors: dict[int, str] = {}
    for norad in [BUILTIN_SATS[n] for n in builtin_satellite_names("all")]:
        try:
            rec = _with_timeout(lambda n=norad: fetch_tle_record(n, max_age_hours=24.0), 20.0)
            tle_recs[norad] = rec
        except Exception as e:
            tle_errors[norad] = f"{type(e).__name__}: {e}"
    ctx["tle_recs"] = tle_recs
    ctx["tle_errors"] = tle_errors

    if not tle_recs:
        res = common.skip_result(
            "R5", "卫星追踪与过境", "NO_DATA",
            "全部目标 TLE 不可用（离线无缓存/下载失败）；不使用内置近似值冒充实测过境，"
            "本环不产出过境数字。",
            {"tle_errors": tle_errors,
             "observer": {"lat": lat, "lon": lon, "alt_m": alt_m}},
            [common.check("tle_available", False, "全部目标 TLE 不可用", skipped=True)],
            "TLE 数据不可用：请联网刷新，或确认 orbit TLE 缓存存在后重试。",
            [], False)
        ctx["status"] = res["status"]
        return res

    try:
        passes = _with_timeout(
            # 两条链路的单位不同，别混：产品 predict_passes 收**公里**
            # （内部 geodetic_to_ecef 是 WGS-72 km），skyfield 收**米**
            # （_skyfield_pass 的 elevation_m）。这里 alt_m 是米，产品侧
            # 必须 /1000；2026-11-09 真机复核发现旧代码直接传米，
            # 站址被抬高 1000 倍（200 m → 200 km），仰角错到几十度。
            lambda: predict_passes(lat, lon, alt_m / 1000.0, WINDOW_H, MIN_EL,
                                   satellite_type="all", start_unix=now,
                                   tle_records=tle_recs), 60.0)
    except Exception as e:
        ctx["status"] = common.FAIL
        return common.result("R5", "卫星追踪与过境", common.FAIL,
                             {"error": str(e)},
                             [common.check("predict_passes", False, str(e))],
                             "过境预报计算失败，请查看日志或重试。", [], False)
    ctx["passes"] = passes

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
        res = common.skip_result(
            "R5", "卫星追踪与过境", "NO_DATA",
            f"缺 skyfield 独立递推参考（{e}），无法量化过境误差；"
            "过境列表仍可由面板复看，但本环不判 PASS。",
            {"tle_source": {n: r.get("source") for n, r in tle_recs.items()},
             "error": str(e)},
            [common.check("skyfield_reference", False, f"独立参考不可用：{e}", skipped=True)],
            "缺少 skyfield 独立递推参考，无法量化误差。", [], False)
        ctx["status"] = res["status"]
        return res
    ctx["ref"] = ref

    epochs = [_pass_unix(p, now) for p in passes]
    chrono = all(epochs[i] <= epochs[i + 1] for i in range(len(epochs) - 1))
    # TLE 校验和是**外部可验证**的完整性证据（NORAD 标准算法），不是自家口径。
    cs_ok = all(_tle_line_ok(rec["line1"]) and _tle_line_ok(rec["line2"])
                for rec in tle_recs.values())
    checks = [common.check("tle_available", True,
                           f"{len(tle_recs)}/{len(BUILTIN_SATS)} 颗 TLE 可用"),
              common.check("tle_checksums_valid", cs_ok,
                           "全部 TLE 行校验和符合 NORAD 标准" if cs_ok
                           else "有 TLE 行校验和不符（数据被截断/篡改）"),
              common.check("passes_sorted", chrono,
                           f"{len(passes)} 条过境按补全日期后的升起时刻"
                           f"{'升序' if chrono else '非升序：跨午夜顺序失真（按 HH:MM:SS 字符串排序的缺陷）'}"),
              common.check("elevation_ge_gate",
                           all(float(p["max_elevation"]) >= MIN_EL for p in passes),
                           f"全部 >= {MIN_EL}°")]

    target_recs = {n: tle_recs[n] for n in TARGETS if n in tle_recs}
    findings, pair_checks = _build_findings(target_recs, passes, ref, now)
    checks.extend(pair_checks)
    ctx["findings"] = findings
    ctx["checks"] = checks

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
    ctx["status"] = status
    metrics["offline_substitute"] = "PASS" if status == common.PASS else "FAIL"
    return common.result(
        "R5", "卫星追踪与过境", status, metrics, checks,
        "过境预报与独立参考不一致：先刷新 TLE（过期轨道会放大误差），再复跑。",
        [], False, [],
        "离线可复跑（不依赖射频硬件，只依赖 TLE+skyfield）。独立参考使用 skyfield "
        "对同一份 TLE 递推；误差=实现一致性，不是外部星历真值。")


def run(lat: float, lon: float, alt_m: float) -> dict:
    """执行 R5 并把证据快照落盘（路径写进 artifacts/metrics 并打印）。"""
    ctx: dict = {"tle_recs": {}, "tle_errors": {}, "passes": [], "ref": {},
                 "findings": [], "checks": [], "status": common.SKIP}
    res = _run_core(lat, lon, alt_m, ctx)
    path = write_evidence(ctx["now"], (lat, lon, alt_m), ctx["tle_recs"],
                          ctx["tle_errors"], ctx["passes"], ctx["ref"],
                          ctx["findings"], ctx["checks"], res["status"])
    res["artifacts"] = list(res.get("artifacts") or []) + [str(path)]
    res.setdefault("metrics", {})["evidence_dir"] = str(path)
    print(f"[R5] 证据快照：{path}")
    return res


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
