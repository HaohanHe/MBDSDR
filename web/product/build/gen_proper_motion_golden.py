# -*- coding: utf-8 -*-
"""恒星自行跨语言金标准（独立事实源 = skyfield 严格空间运动，无需星历/联网）。

前端 14-astro-frame.js 的 propagateProperMotion 用「线性 RA/Dec 近似」把
HYG J2000 星位推到当前历元。本脚本提供两条独立基准：

  1. linear   —— 与 JS 逐字同构的 Python 线性转录（同一套取整后的 catalog 输入）。
                 tests/proper_motion_selftest.js 以 1e-9° 紧钉扎 JS↔Python，
                 任一侧公式/单位漂移立刻报错。
  2. skyfield —— skyfield.api.Star 的严格空间运动（含视差距离 + 径向速度 +
                 多普勒因子），pos = _position_au + _velocity_au_per_d·(t−epoch)，
                 归一化后得 ICRS RA/Dec。用来量「线性近似」相对严格运动差多少。

两条基准都喂**取整后的 catalog 值**（ra/dec 0.001°、pm 1 mas/yr），与前端
实际消费的数据逐字一致；这样残差只反映「线性 vs 严格」的近似误差，不混入
catalog 量化误差（量化是 star[0..1] 既有口径，与本特性无关）。

视差/径向速度取自 HYG 的 dist(pc)/rv(km/s)：parallax_mas = 1000/dist。
dist 缺测/非正 → 按 HYG 占位 100000 pc（parallax≈0.01 mas，透视加速度可忽略），
rv 缺测 → 0。绝不凭空造视差/速度。

输出：build/proper-motion-golden.json
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT  # noqa: E402

import numpy as np  # noqa: E402
import skyfield  # noqa: E402
from skyfield.api import Star, load  # noqa: E402

D2R = math.pi / 180.0
MAS_PER_DEG = 3600000.0
J2000 = 2451545.0
MAG_CUTOFF = 7.5
HYG_CSV = ROOT / "data/hygdata_v41.csv"
EPOCHS = (2451545.0, 2461000.0, 2461500.0, 2470000.0)
# 证据设定：实测最快渲染星 Lalande 21185 到 2051（jd2470000）残差 ~0.35″，
# Groombridge 1830 / Lacaille 9352 ~0.23″，近极星 ≤0.003″。取 2″ 作显示级
# 上限（任何 FOV 下都亚像素），留足裕度。worstResidualArcsec 由本脚本实测写入。
TOL_ARCSEC = 2.0
LINEAR_PIN_TOL_DEG = 1e-9

_ts = load.timescale()


def _pm(v: str) -> int:
    if v in ("", None):
        return 0
    return round(float(v))


def linear_propagate(ra_deg: float, dec_deg: float, pmra: int, pmdec: int,
                     dt_yr: float) -> tuple[float, float]:
    """与 JS propagateProperMotion 逐字同构（cosδ0 用 J2000 dec，极区守卫）。"""
    dec = dec_deg + (pmdec * dt_yr) / MAS_PER_DEG
    cos_dec0 = math.cos(dec_deg * D2R)
    ra = ra_deg
    if abs(cos_dec0) > 1e-9:
        ra = ra_deg + (pmra * dt_yr) / (MAS_PER_DEG * cos_dec0)
    dec = max(-90.0, min(90.0, dec))
    ra = ((ra % 360.0) + 360.0) % 360.0
    return ra, dec


def rigorous_propagate(ra_deg: float, dec_deg: float, pmra: int, pmdec: int,
                       parallax_mas: float, rv_kms: float,
                       jd: float) -> tuple[float, float]:
    """skyfield 严格空间运动（无星历：直接用 Star 的位置/速度向量）。"""
    s = Star(ra_hours=ra_deg / 15.0, dec_degrees=dec_deg,
             ra_mas_per_year=float(pmra), dec_mas_per_year=float(pmdec),
             parallax_mas=parallax_mas, radial_km_per_s=rv_kms, epoch=J2000)
    t = _ts.tt_jd(jd)
    pos = s._position_au + s._velocity_au_per_d * (t.tdb - s.epoch)
    u = pos / np.linalg.norm(pos)
    dec = math.degrees(math.asin(float(u[2])))
    ra = math.degrees(math.atan2(float(u[1]), float(u[0]))) % 360.0
    return ra, dec


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a1, d1, a2, d2 = (x * D2R for x in (ra1, dec1, ra2, dec2))
    c = (math.sin(d1) * math.sin(d2)
         + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def load_pool() -> list[dict]:
    if not HYG_CSV.is_file():
        raise RuntimeError(f"HYG 星表缺失：{HYG_CSV}（先跑 build/fetch_hyg.py）")
    pool = []
    with HYG_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                mag = float(row["mag"])
            except (ValueError, TypeError):
                continue
            if mag > MAG_CUTOFF:
                continue
            ra_deg = round(float(row["ra"]) * 15.0 * 1000) / 1000.0
            dec_deg = round(float(row["dec"]) * 1000) / 1000.0
            pmra, pmdec = _pm(row.get("pmra", "")), _pm(row.get("pmdec", ""))
            try:
                dist = float(row.get("dist", "") or 0)
            except ValueError:
                dist = 0.0
            if not math.isfinite(dist) or dist <= 0:
                dist = 100000.0          # HYG 占位：缺测距离
            try:
                rv = float(row.get("rv", "") or 0)
            except ValueError:
                rv = 0.0
            if not math.isfinite(rv):
                rv = 0.0
            pool.append({
                "name": row.get("proper") or "",
                "id": row.get("id", ""),
                "ra0": ra_deg, "dec0": dec_deg, "mag": mag,
                "pmra": pmra, "pmdec": pmdec,
                "pmag": math.hypot(pmra, pmdec),
                "distPc": dist, "parallaxMas": 1000.0 / dist, "rvKms": rv,
            })
    return pool


def sample(pool: list[dict]) -> list[dict]:
    """确定性抽样：高自行 / 命名亮星 / 近极 / 零自行 / 等距铺开。无随机、无墙钟。"""
    chosen: dict[tuple, dict] = {}

    def add(s: dict) -> None:
        chosen.setdefault((round(s["ra0"] * 1000), round(s["dec0"] * 1000)), s)

    for s in sorted(pool, key=lambda x: -x["pmag"])[:14]:
        add(s)
    named_want = ["Sirius", "Arcturus", "Vega", "Capella", "Rigel", "Procyon",
                  "Altair", "Aldebaran", "Spica", "Antares", "Pollux",
                  "Fomalhaut", "Deneb", "Regulus", "Polaris", "Betelgeuse",
                  "Achernar", "Hadar", "Acrux", "Mimosa"]
    by_name = {s["name"]: s for s in pool if s["name"]}
    for n in named_want:
        if n in by_name:
            add(by_name[n])
    for s in sorted([x for x in pool if abs(x["dec0"]) >= 80],
                    key=lambda x: (-abs(x["dec0"]), x["ra0"]))[:6]:
        add(s)
    for s in [x for x in pool if x["pmra"] == 0 and x["pmdec"] == 0][:4]:
        add(s)
    stride = max(1, len(pool) // 12)
    for s in pool[::stride][:12]:
        add(s)
    return list(chosen.values())


def main() -> None:
    digest = hashlib.sha256(HYG_CSV.read_bytes()).hexdigest()
    pool = load_pool()
    stars = sample(pool)
    cases = []
    worst = 0.0
    worst_star = ""
    for s in stars:
        samples = []
        for jd in EPOCHS:
            dt_yr = (jd - J2000) / 365.25
            lr, ld = linear_propagate(s["ra0"], s["dec0"], s["pmra"],
                                      s["pmdec"], dt_yr)
            rr, rd = rigorous_propagate(s["ra0"], s["dec0"], s["pmra"],
                                        s["pmdec"], s["parallaxMas"],
                                        s["rvKms"], jd)
            res = sep_arcsec(lr, ld, rr, rd)
            if res > worst:
                worst, worst_star = res, (s["name"] or f"HYG-{s['id']}")
            samples.append({
                "jd": jd, "dtYr": dt_yr,
                "linear": {"ra": lr, "dec": ld},
                "skyfield": {"ra": rr, "dec": rd},
                "residualArcsec": res,
            })
        cases.append({
            "name": s["name"], "hygId": s["id"],
            "ra0": s["ra0"], "dec0": s["dec0"], "mag": s["mag"],
            "pmra": s["pmra"], "pmdec": s["pmdec"],
            "distPc": s["distPc"], "parallaxMas": s["parallaxMas"],
            "rvKms": s["rvKms"], "samples": samples,
        })

    meta = {
        "source": f"skyfield {skyfield.__version__} Star rigorous space motion "
                  f"(position_au + velocity_au_per_d·Δt; no ephemeris, no network)",
        "catalogSource": "HYG v4.1 (data/hygdata_v41.csv)",
        "catalogSha256": digest,
        "convention": "pmra=μα·cosδ, pmdec=μδ, mas/yr (ICRS J2000); "
                      "rounded to 1 mas/yr, ra/dec to 0.001° (same as catalog)",
        "parallax": "parallax_mas = 1000/dist_pc (HYG dist); "
                    "unknown/non-positive dist → 100000 pc placeholder; rv missing → 0",
        "linearFormula": "dec=dec0+pmdec·Δt/3.6e6; "
                         "ra=ra0+pmra·Δt/(3.6e6·cos(dec0)) (|cos dec0|>1e-9); "
                         "Δt=(jd−2451545)/365.25",
        "epochs": list(EPOCHS),
        "toleranceArcsec": TOL_ARCSEC,
        "linearPinTolDeg": LINEAR_PIN_TOL_DEG,
        "worstResidualArcsec": worst,
        "worstStar": worst_star,
        "starCount": len(cases),
        "caseCount": len(cases) * len(EPOCHS),
        "magCutoff": MAG_CUTOFF,
        "notVerified": [
            "linear RA/Dec approximation ignores perspective acceleration "
            "(secular change of parallax/rv) and the cos(dec0)→cos(dec(t)) term; "
            "residual vs skyfield rigorous measured per case, worst recorded in meta",
            "pm rounded to 1 mas/yr and ra/dec to 0.001° (catalog quantization, "
            "common to both anchors so it cancels in the residual)",
            "HYG dist=100000 pc placeholder for unknown distance → parallax≈0.01 mas, "
            "perspective acceleration effectively suppressed for those stars",
            "Barnard's/Kapteyn's (mag>7.5) are outside the rendered catalog and "
            "therefore not sampled; fastest rendered stars are Groombridge 1830 / "
            "Lacaille 9352",
            "not for mas-level astrometry / precise pointing",
        ],
    }
    out = ROOT / "build" / "proper-motion-golden.json"
    out.write_text(json.dumps({"meta": meta, "cases": cases},
                              ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"[pm-golden] {len(cases)} stars × {len(EPOCHS)} epochs = "
          f"{meta['caseCount']} cases -> {out.name} ({out.stat().st_size} bytes)")
    print(f"[pm-golden] worst residual {worst:.4f}\" ({worst_star}) "
          f"vs tolerance {TOL_ARCSEC}\"; skyfield {skyfield.__version__}")
    if worst > TOL_ARCSEC:
        raise SystemExit(f"[pm-golden] FAIL: worst {worst:.4f}\" > tol {TOL_ARCSEC}\"")


if __name__ == "__main__":
    main()