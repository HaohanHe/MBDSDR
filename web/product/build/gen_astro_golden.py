# -*- coding: utf-8 -*-
"""天文框架跨语言金标准（事实源 = 仓库 celestial_geometry.py）。

前端 14-astro-frame.js 逐项复刻仓库的 GMST / 岁差 / 章动公式，
本脚本用仓库 Python 实现算出基准矩阵与向量结果，交给
tests/astro_selftest.js 做数值对拍。任一侧公式漂移都会立刻报错。

**为什么不用仓库 GroundStation 的地平矩阵**：其 east 基与标准天文方位
（东正）差一个符号（数值对拍 x/z 一致、y 相反）。这里按前端已验证的
标准 LST 旋转构造 Rf，并在 meta 里如实记录该差异。

输出：build/astro-golden.json
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO  # noqa: E402

sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
from mbdsdr_ai import celestial_geometry as cg  # noqa: E402

D2R = math.pi / 180.0
# v_repo = Q @ v_frontend；前端标准 ICRS (x=春分点, y=6h, z=NCP)
Q = np.array([[0.0, 1.0, 0.0],
              [0.0, 0.0, 1.0],
              [-1.0, 0.0, 0.0]])
QT = Q.T

JDS = (2451545.0, 2460676.5, 2461000.0, 2461500.0, 2470000.0, 2490000.0)
STATIONS = ((0.0, 0.0), (39.9, 116.4), (40.7, -74.0), (45.75, 126.63))
POINTS = ((0.0, 0.0), (100.0, 30.0), (200.0, -20.0), (300.0, 70.0),
          (45.0, 89.0), (270.0, -45.0))


def flat(m: np.ndarray) -> list[float]:
    return [float(x) for x in np.asarray(m).reshape(9)]


def vec_front(ra_deg: float, dec_deg: float) -> np.ndarray:
    """前端标准 ICRS 单位向量（与仓库 vec_from_radec 基不同）。"""
    ra, dec = ra_deg * D2R, dec_deg * D2R
    cd = math.cos(dec)
    return np.array([cd * math.cos(ra), cd * math.sin(ra), math.sin(dec)])


def rf_matrix(jd: float, lat_deg: float, lon_deg: float) -> np.ndarray:
    """标准天文 LST 旋转：of-date 赤道 -> 地平(x=北, y=东, z=天顶)。"""
    L = (cg.gmst(jd) + lon_deg) * D2R
    phi = lat_deg * D2R
    cL, sL, cP, sP = math.cos(L), math.sin(L), math.cos(phi), math.sin(phi)
    return np.array([[-sP * cL, -sP * sL, cP],
                     [-sL, cL, 0.0],
                     [cP * cL, cP * sL, sP]])


cases = []
for jd in JDS:
    P = cg.precession_matrix(jd)
    N = cg.nutation_matrix(jd)
    dpsi, deps = cg.nutation_angles_2000b(jd)
    Mpn = QT @ N @ P @ Q          # 前端基下的 J2000 -> of-date
    Pinv = QT @ P.T @ Q           # 逆岁差（TEME -> J2000 近似）
    for lat, lon in STATIONS:
        Rf = rf_matrix(jd, lat, lon)
        M = Rf @ Mpn               # 前端基下 J2000 -> 地平（总矩阵）
        vectors = []
        for ra, dec in POINTS:
            v = vec_front(ra, dec)
            vectors.append({
                "ra": ra, "dec": dec,
                "v": [float(x) for x in v],
                "horizon": [float(x) for x in (M @ v)],
                "dateHorizon": [float(x) for x in (Rf @ v)],
                "ofDate": [float(x) for x in (Mpn @ v)],
                "temeToJ2000": [float(x) for x in (Pinv @ v)],
            })
        cases.append({
            "jd": jd, "lat": lat, "lon": lon,
            "gmst": cg.gmst(jd),
            "dpsiRad": float(dpsi), "depsRad": float(deps),
            "epsRad": cg.obliquity(jd),
            "P": flat(P), "N": flat(N), "Mpn": flat(Mpn),
            "Rf": flat(Rf), "M": flat(M), "Pinv": flat(Pinv),
            "vectors": vectors,
        })

meta = {
    "source": "mbdsdr_ai/celestial_geometry.py (MIT, repo-owned)",
    "precession": "IAU 2006 / P03 (repo precession_matrix)",
    "nutation": "IAU 2000B abridged 6 terms, arcsec-scaled (unit bug fixed 2026-11-06)",
    "gmst": "Capitaine-Wallace-Chapront 2003 eq.43 (repo gmst)",
    "basis": "frontend ICRS x=equinox/y=RA6h/z=NCP; repo basis via Q=[[0,1,0],[0,0,1],[-1,0,0]]",
    "horizon": "standard LST rotation x=North/y=East/z=Zenith; "
               "repo GroundStation.east has opposite sign, deliberately NOT reused",
    "jdCount": len(JDS), "stationCount": len(STATIONS),
    "pointCount": len(POINTS), "caseCount": len(cases),
}

out = ROOT / "build" / "astro-golden.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"meta": meta, "cases": cases},
                          ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"[astro-golden] {len(cases)} cases -> {out} "
      f"({out.stat().st_size} bytes), from repo {REPO.name}")
