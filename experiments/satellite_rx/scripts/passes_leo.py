#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
passes_leo.py — 全量过境表（LEO 过滤）
=====================================
把 TLE 库里所有星的未来过境摊开：AOS/LOS/时长/仰角/方位/距离 + 已知下行。
有已知业余下行的排前面（优先目标），其余排后面（需查频率）。

用法：
  python passes_leo.py                     # 默认 8h、>15°
  python passes_leo.py --hours 24 --min-alt 30
  python passes_leo.py --json              # 机器可读 JSON（stdout 纯 JSON）
"""
import argparse, glob, os, re, sys
from datetime import datetime, timedelta, timezone

import numpy as np
from skyfield.api import load, EarthSatellite, Topos

LAT, LON, ALT = 43.774156, 125.397813, 230.8
HERE = os.path.dirname(os.path.abspath(__file__))
# TLE 库按优先级搜：仓库入库目录 data/ → 脚本目录 → 当前工作目录
TLE_DIRS = [os.path.join(HERE, os.pardir, "data"), HERE, os.getcwd()]
MAX_KM = 2500          # 只留 LEO

# 一律 \b 词边界：防子串误伤（SWISSCUBE 里含 "ISS" 片段，不能算 ISS）
KNOWN = [
    (r"\bISS\b|\bZARYA\b", "145.990话音/437.800APRS", "ISS"),
    (r"\bNOAA 15\b",       "137.620 APT", "NOAA"),
    (r"\bNOAA 18\b",       "137.9125 APT", "NOAA"),
    (r"\bNOAA 19\b",       "137.100 APT", "NOAA"),
    (r"\bMETEOR-M2 2\b|\bMETEOR-M 2\b", "137.900 LRPT", "METEOR"),
    (r"\bMETEOR-M2 3\b|\bMETEOR-M2 4\b", "137.900/137.100 LRPT", "METEOR"),
    (r"^CUTE$|\bCUTE 49263\b", "437.250 GFSK9600", "CUTE"),
    (r"\bCUTE-1 \(CO-55\)", "436.8375 FM/CW", "CUTE-1"),
    (r"\bCUTE-1\.7\b",     "437.475 CW/437.360", "CUTE-1.7"),
    (r"\bSO-50\b|\bSAUDISAT\b", "436.795 FM", "SO-50"),
    (r"\bAO-73\b|\bFUNcube\b", "145.935 FSK", "AO-73"),
    (r"\bFO-29\b|\bJAS-2\b", "435.800 SSB/CW", "FO-29"),
    (r"\bRS-44\b",         "435.615 SSB", "RS-44"),
]


def freq_of(name):
    for pat, f, tag in KNOWN:
        if re.search(pat, name, re.I):
            return f, tag
    return "—", ""


def load_all():
    sats, seen = [], set()
    for d in TLE_DIRS:
        d = os.path.abspath(d)
        fns = sorted(glob.glob(os.path.join(d, "tle_*.txt"))) \
            + sorted(glob.glob(os.path.join(d, "cute_49263.tle"))) \
            + [os.path.join(d, "iss.tle")]
        for fn in fns:
            if not os.path.exists(fn):
                continue
            lines = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
            i = 0
            while i + 2 < len(lines):
                n, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
                if l1.startswith("1 ") and l2.startswith("2 "):
                    try:
                        nid = l1[2:7]
                        if nid not in seen:
                            seen.add(nid)
                            sats.append((n.strip(), EarthSatellite(l1, l2, n.strip())))
                    except Exception:
                        pass
                    i += 3
                else:
                    i += 1
    return sats


def compute(sats, ts, gs, hours, min_alt, step=20.0):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    n_pts = int(hours * 3600 / step)
    t0 = ts.utc(now.year, now.month, now.day, now.hour, now.minute,
                np.arange(0, n_pts * step, step))
    res = []
    for name, sat in sats:
        try:
            alt, az, dist = (sat - gs).at(t0).altaz()
            ad = alt.degrees
            dk = dist.km
            idx = np.where((ad > min_alt) & (dk < MAX_KM))[0]
            if len(idx) == 0:
                continue
            for g in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                imax = g[np.argmax(ad[g])]
                aos = t0[g[0]].utc_datetime().replace(tzinfo=None)
                los = t0[g[-1]].utc_datetime().replace(tzinfo=None)
                res.append((aos, los, float(ad[imax]), float(az.degrees[imax]),
                            float(dk[imax]), name))
        except Exception:
            pass
    res.sort(key=lambda r: r[0])
    return now, res


def main():
    ap = argparse.ArgumentParser(description="全量 LEO 过境表（含已知业余下行标注）")
    ap.add_argument("--hours", type=float, default=8, help="展望小时数（默认 8）")
    ap.add_argument("--min-alt", type=float, default=15.0, help="仰角门限（度）")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON（stdout 纯 JSON，无表格）")
    a = ap.parse_args()
    if a.hours <= 0:
        ap.error(f"--hours 需为正，收到 {a.hours}")

    ts = load.timescale()
    gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
    sats = load_all()
    now, res = compute(sats, ts, gs, a.hours, a.min_alt)

    if a.json:
        import json
        out = []
        for aos, los, am, az, dk, name in res:
            f, tag = freq_of(name)
            out.append(dict(name=name, aos_utc=aos.isoformat() + "Z",
                            los_utc=los.isoformat() + "Z",
                            duration_s=int((los - aos).total_seconds()),
                            max_alt_deg=round(am, 2), az_deg=round(az, 1),
                            range_km=round(dk, 1), downlink=f, tag=tag,
                            now=bool(aos <= now <= los)))
        json.dump({"generated_utc": now.isoformat() + "Z",
                   "site": {"lat": LAT, "lon": LON, "alt_m": ALT},
                   "hours": a.hours, "min_alt_deg": a.min_alt,
                   "count": len(out), "passes": out},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    L8 = timedelta(hours=8)
    print(f"载入 {len(sats)} 颗（去重） | 当前本地 "
          f"{(now + L8).strftime('%H:%M:%S')} | 站址 {LAT:.4f}N {LON:.4f}E")
    hams = [r for r in res if freq_of(r[5])[0] != "—"]
    others = [r for r in res if freq_of(r[5])[0] == "—"]

    def show(rows, title, n=16):
        print("\n" + "=" * 108)
        print(title)
        print("=" * 108)
        print(f"{'AOS本地':<9}{'LOS本地':<9}{'时长':<7}{'仰角':<7}{'方位':<7}{'距离':<8}{'卫星':<24}{'下行'}")
        for aos, los, am, az, dk, name in rows[:n]:
            f, tag = freq_of(name)
            nowflag = "★NOW" if aos <= now <= los else "    "
            print(f"{(aos + L8).strftime('%H:%M:%S'):<9}{(los + L8).strftime('%H:%M:%S'):<9}"
                  f"{(los - aos).seconds // 60:>3}min {am:>5.1f}° {az:>5.0f}° {dk:>6.0f}  "
                  f"{name[:23]:<24}{f} {nowflag}")

    show(hams, f"◆ 有已知业余下行（{len(hams)} 次）— 优先目标", 14)
    show(others, f"◇ 其他 LEO 过境（{len(others)} 次）— 需查频率", 12)


if __name__ == "__main__":
    main()
