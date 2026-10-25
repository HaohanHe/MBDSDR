from skyfield.api import load, EarthSatellite, Topos
from datetime import datetime, timedelta, timezone
import numpy as np, glob, os, re

LAT, LON, ALT = 43.774156, 125.397813, 230.8
HOURS, MIN_ALT, STEP = 8, 15.0, 20.0
MAX_KM = 2500          # 只留 LEO

KNOWN = [
    (r"ISS|ZARYA",        "145.990话音/437.800APRS", "ISS"),
    (r"NOAA 15",          "137.620 APT", "NOAA"),
    (r"NOAA 18",          "137.9125 APT", "NOAA"),
    (r"NOAA 19",          "137.100 APT", "NOAA"),
    (r"METEOR-M2 2|METEOR-M 2", "137.900 LRPT", "METEOR"),
    (r"METEOR-M2 3|METEOR-M2 4", "137.900/137.100 LRPT", "METEOR"),
    (r"^CUTE$|CUTE 49263", "437.250 GFSK9600", "CUTE"),
    (r"CUTE-1 \(CO-55\)", "436.8375 FM/CW", "CUTE-1"),
    (r"CUTE-1\.7",        "437.475 CW/437.360", "CUTE-1.7"),
    (r"SO-50|SAUDISAT",   "436.795 FM", "SO-50"),
    (r"AO-73|FUNcube",    "145.935 FSK", "AO-73"),
    (r"FO-29|JAS-2",      "435.800 SSB/CW", "FO-29"),
    (r"RS-44",            "435.615 SSB", "RS-44"),
]
def freq_of(name):
    for pat, f, tag in KNOWN:
        if re.search(pat, name, re.I): return f, tag
    return "—", ""

seen_ids = set()
def load_all():
    sats = []
    for fn in sorted(glob.glob("tle_*.txt")) + glob.glob("cute_49263.tle") + glob.glob("iss.tle"):
        lines = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
        i = 0
        while i + 2 < len(lines):
            n, l1, l2 = lines[i], lines[i+1], lines[i+2]
            if l1.startswith("1 ") and l2.startswith("2 "):
                try:
                    nid = l1[2:7]
                    if nid not in seen_ids:
                        seen_ids.add(nid)
                        sats.append((n.strip(), EarthSatellite(l1, l2, n.strip())))
                except Exception: pass
                i += 3
            else: i += 1
    return sats

ts = load.timescale()
gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
sats = load_all()
now = datetime.now(timezone.utc).replace(tzinfo=None)
print(f"载入 {len(sats)} 颗（去重） | 当前本地 {(now+timedelta(hours=8)).strftime('%H:%M:%S')} | 站址 {LAT:.4f}N {LON:.4f}E")
n_pts = int(HOURS*3600/STEP)
t0 = ts.utc(now.year, now.month, now.day, now.hour, now.minute, np.arange(0, n_pts*STEP, STEP))
res = []
for name, sat in sats:
    try:
        alt, az, dist = (sat - gs).at(t0).altaz()
        ad = alt.degrees; dk = dist.km
        idx = np.where((ad > MIN_ALT) & (dk < MAX_KM))[0]
        if len(idx) == 0: continue
        for g in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
            imax = g[np.argmax(ad[g])]
            a = t0[g[0]].utc_datetime().replace(tzinfo=None)
            m = t0[imax].utc_datetime().replace(tzinfo=None)
            l = t0[g[-1]].utc_datetime().replace(tzinfo=None)
            res.append((a, l, ad[imax], az.degrees[imax], dk[imax], name))
    except Exception: pass
res.sort(key=lambda r: r[0])

# 分两组打印：有已知业余下行的优先
hams = [r for r in res if freq_of(r[5])[0] != "—"]
others = [r for r in res if freq_of(r[5])[0] == "—"]
def show(rows, title, n=16):
    print("\n" + "="*108)
    print(title)
    print("="*108)
    print(f"{'AOS本地':<9}{'LOS本地':<9}{'时长':<7}{'仰角':<7}{'方位':<7}{'距离':<8}{'卫星':<24}{'下行'}")
    for a, l, am, az, dk, name in rows[:n]:
        f, tag = freq_of(name)
        nowflag = "★NOW" if a <= now <= l else "    "
        print(f"{(a+timedelta(hours=8)).strftime('%H:%M:%S'):<9}{(l+timedelta(hours=8)).strftime('%H:%M:%S'):<9}"
              f"{(l-a).seconds//60:>3}min {am:>5.1f}° {az:>5.0f}° {dk:>6.0f}  {name[:23]:<24}{f} {nowflag}")
show(hams, f"◆ 有已知业余下行（{len(hams)} 次）— 优先目标", 14)
show(others, f"◇ 其他 LEO 过境（{len(others)} 次）— 需查频率", 12)
