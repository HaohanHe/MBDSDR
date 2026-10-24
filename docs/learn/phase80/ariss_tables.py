#!/usr/bin/env python3
# ARISS 全链路保障 -- 明早（2026-10-25）过境数据表生成
import json
import sys
from datetime import timedelta, timezone

import numpy as np

sys.path.insert(0, "/workspace/MBDSDR")
from mbdsdr_ai.sat_passes import GroundStation, predict_passes, compute_doppler_curve

CST = timezone(timedelta(hours=8))
gs = GroundStation(43.8267, 125.4189, 220.0)
tle_json = json.loads(open(
    "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d4d-3cb31d5a8000/iss_tle.txt"
).read())
TLE = [tle_json["header"], tle_json["line1"], tle_json["line2"]]
epoch = __import__("datetime").datetime.fromtimestamp(tle_json["tle_timestamp"], timezone.utc)

passes = predict_passes(TLE, gs, start_time=None, hours=10.0, min_alt=5.0)
print(f"TLE epoch {epoch:%Y-%m-%d %H:%M:%S}Z")
print(f"\n=== 明早（10-25 CST）全部过境 ===")
for p in passes:
    r = p.rise_time.utc_datetime().astimezone(CST)
    m = p.max_alt_time.utc_datetime().astimezone(CST)
    s = p.set_time.utc_datetime().astimezone(CST)
    print(f"  AOS {r:%H:%M:%S} az={p.rise_az:5.1f}° | MAX {m:%H:%M:%S} alt={p.max_alt:4.1f}° "
          f"| LOS {s:%H:%M:%S} az={p.set_az:5.1f}° | dur={p.duration/60:.1f}min")

for p in passes:
    r = p.rise_time.utc_datetime().astimezone(CST)
    if r.hour != 6:
        continue
    print(f"\n=== 主过境 {r:%H:%M:%S} CST 详细表（30 s 步进）===")
    for freq, tag in ((145_800_000.0, "145.800 FM 中继"), (145_990_000.0, "145.990 分包")):
        pts = compute_doppler_curve(TLE, gs, p, freq, num_points=int(p.duration / 30) + 1)
        print(f"\n-- {tag} --")
        print(f"{'CST':<9}{'az°':>6}{'el°':>6}{'fd Hz':>9}{'目标频率 Hz':>15}{'fd rate':>9}")
        for pt in pts:
            t = pt.time.utc_datetime().astimezone(CST)
            print(f"{t:%H:%M:%S} {pt.azimuth:6.1f}{pt.elevation:6.1f}"
                  f"{pt.doppler_hz:9.1f}{freq + pt.doppler_hz:15.1f}"
                  f"{pt.doppler_rate_hz_s:9.2f}")
