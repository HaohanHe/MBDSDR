#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sat_radar.py — 机会雷达：未来 N 小时所有可收卫星一览
======================================================
外场作战前的一屏视图：什么时间、哪颗星、什么频率、仰角多少、
朝哪个方向、值得不值得打（按"信号好收"程度排序）。

排序规则（越靠前越值得架天线）：
  1. 仰角越高越好（>60° 顶空，屋里窗外都有戏；<30° 基本没戏）
  2. 常发射型优先（NOAA APT / METEOR LRPT > 需要排期的 ISS 话音）

用法：
  python sat_radar.py                  # 未来 24h，>30°
  python sat_radar.py --hours 12 --min-alt 45
  python sat_radar.py --today           # 只看今天剩余
"""
import argparse, glob, os, re
from datetime import datetime, timedelta

import numpy as np
from skyfield.api import load, EarthSatellite, Topos

LAT, LON, ALT = 43.774156, 125.397813, 230.8
HERE = os.path.dirname(os.path.abspath(__file__))

# 优先级：常发射=2（随时有信号），排期型=1，老星/未知=0
PRIORITY = [
    (r"^NOAA 1[589]$", 2, "137.620/137.9125/137.100", "APT 常发射"),
    (r"METEOR-M2", 2, "137.100/137.900", "LRPT 常发射"),
    (r"^ISS", 1, "145.990/437.800", "需 ARISS 排期"),
    (r"CUTE$", 1, "437.250", "GFSK 遥测"),
    (r"CUTE-1\.7", 0, "437.475", "老星"),
    (r"CUTE-1 \(CO-55\)", 0, "436.8375", "2003 老星，疑停用"),
    (r"SO-50", 1, "436.795", "FM 转发，需上行"),
    (r"AO-73", 1, "145.935", "遥测"),
    (r"RS-44", 1, "435.615", "SSB 转发"),
]


def load_sats():
    sats = []
    seen = set()
    for fn in sorted(glob.glob(os.path.join(HERE, "tle_*.txt"))) + \
              [os.path.join(HERE, "iss.tle")]:
        if not os.path.exists(fn):
            continue
        L = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
        for i in range(len(L) - 2):
            if L[i+1].startswith("1 ") and L[i+2].startswith("2 "):
                nid = L[i+1][2:7]
                if nid in seen:
                    continue
                seen.add(nid)
                try:
                    sats.append((L[i].strip(), EarthSatellite(L[i+1], L[i+2], L[i])))
                except Exception:
                    pass
    return sats


def meta(name):
    for pat, pri, freq, note in PRIORITY:
        if re.search(pat, name, re.I):
            return pri, freq, note
    return 0, "?", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--min-alt", type=float, default=30)
    ap.add_argument("--today", action="store_true")
    a = ap.parse_args()

    ts = load.timescale()
    gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hours = (24 - now.hour - now.minute / 60) if a.today else a.hours

    n = int(hours * 3600 / 30)
    t0 = ts.utc(now.year, now.month, now.day, now.hour, now.minute,
                [float(i * 30) for i in range(n)])
    L8 = timedelta(hours=8)
    MAX_KM = 2500          # 只留 LEO（GEO/HEO 一挂几十分钟，不是接收目标）
    rows = []
    for name, sat in load_sats():
        try:
            alt, az, dist = (sat - gs).at(t0).altaz()
            ad = np.array(alt.degrees)
            idx = np.where((ad > a.min_alt) & (np.array(dist.km) < MAX_KM))[0]
            for g in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                im = g[int(np.argmax(ad[g]))]
                aos = t0[g[0]].utc_datetime().replace(tzinfo=None)
                los = t0[g[-1]].utc_datetime().replace(tzinfo=None)
                if los < now:
                    continue
                pri, freq, note = meta(name)
                rows.append(dict(name=name, aos=aos, los=los, alt=float(ad[im]),
                                 az=float(az.degrees[im]), km=float(dist.km[im]),
                                 pri=pri, freq=freq, note=note))
        except Exception:
            pass
    rows.sort(key=lambda r: (-r["pri"], -r["alt"]))
    print("=" * 96)
    print(f"机会雷达 · 长春站 · 本地 {now + L8:%Y-%m-%d %H:%M} 起 {hours:.0f}h · "
          f"门限 >{a.min_alt}°  共 {len(rows)} 次")
    print("=" * 96)
    print(f"{'#':<3}{'AOS本地':<10}{'时长':<7}{'仰角':<7}{'方位':<7}{'距离':<8}"
          f"{'卫星':<22}{'下行':<22}{'备注'}")
    for i, r in enumerate(rows[:24], 1):
        print(f"{i:<3}{(r['aos']+L8).strftime('%m-%d %H:%M'):<10}"
              f"{(r['los']-r['aos']).seconds//60:>3}min  "
              f"{r['alt']:>4.0f}° {r['az']:>4.0f}° {r['km']:>5.0f}km  "
              f"{r['name'][:21]:<22}{r['freq']:<22}{r['note']}")
    print("=" * 96)
    hot = [r for r in rows if r["pri"] >= 2 and r["alt"] >= 50]
    print(f"★ 高价值目标（常发射+仰角>50°）：{len(hot)} 次")
    for r in hot[:5]:
        print(f"   {(r['aos']+L8).strftime('%m-%d %H:%M')}  {r['name']}  "
              f"{r['alt']:.0f}°  {r['freq']}")
    if not hot:
        print("   （无——等下一个窗口，或降低门限 --min-alt 30 再看）")


if __name__ == "__main__":
    main()
