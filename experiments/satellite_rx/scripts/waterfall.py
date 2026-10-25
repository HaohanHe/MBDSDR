#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多普勒轨迹判决：判断一段 IQ 里到底有没有收到目标卫星
====================================================
每 4 秒取带内峰值频率，与 TLE 预测的多普勒曲线算相关系数：
  r -> 1  : 峰值跟着多普勒走 = 真信号（收到了）
  r -> 0  : 峰值随机跳 = 噪声（没收到）

用法：
  python waterfall.py <iq> <start_utc "YYYY-MM-DD HH:MM:SS"> <中心频率Hz> \
      <采样率Hz> [tle文件] [卫星名模糊匹配]

示例：
  python waterfall.py NOAA_19_137100k.sigmf-data "2026-10-25 02:37:30" 137100000 1024000 tle_noaa.txt "NOAA 19"
"""
import sys
import numpy as np
from datetime import datetime, timedelta
from skyfield.api import load, EarthSatellite, Topos, utc

FN = sys.argv[1] if len(sys.argv) > 1 else "iss_145990_iq.bin"
T0 = sys.argv[2] if len(sys.argv) > 2 else "2026-10-24 22:32:37"
FC = float(sys.argv[3]) if len(sys.argv) > 3 else 145.990e6
FS = float(sys.argv[4]) if len(sys.argv) > 4 else 2.048e6
TLE = sys.argv[5] if len(sys.argv) > 5 else "iss.tle"
SAT = sys.argv[6] if len(sys.argv) > 6 else ""

LAT, LON, ALT = 43.774156, 125.397813, 230.8
F0 = FC

try:
    START = datetime.strptime(T0, "%Y-%m-%d %H:%M:%S")
except ValueError:
    START = datetime.strptime(f"{datetime.now().date()} {T0}", "%Y-%m-%d %H:%M:%S")
print(f"[cfg] 起始 UTC = {START}  中心 {F0/1e6:.4f} MHz  fs={FS/1e6:.3f}MS/s")


def find_sat():
    lines = [l.rstrip() for l in open(TLE, encoding="utf-8", errors="replace") if l.strip()]
    if SAT:
        for i in range(len(lines) - 2):
            if SAT.lower() in lines[i].lower() and lines[i+1].startswith("1 "):
                return EarthSatellite(lines[i+1], lines[i+2], lines[i]), lines[i]
    for i in range(len(lines) - 2):
        if lines[i+1].startswith("1 ") and lines[i+2].startswith("2 "):
            return EarthSatellite(lines[i+1], lines[i+2], lines[i]), lines[i]
    raise SystemExit(f"[err] {TLE} 里没找到卫星")


sat, sname = find_sat()
ts = load.timescale()
gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
print(f"[cfg] 卫星 {sname}")


def pred_dopp(tm):
    tm = tm.replace(tzinfo=utc) if tm.tzinfo is None else tm
    d0 = (sat - gs).at(ts.utc(tm)).distance().km
    d1 = (sat - gs).at(ts.utc(tm + timedelta(seconds=1))).distance().km
    return -(d1 - d0) / 299792.458 * F0


raw = np.fromfile(FN, dtype=np.uint8)
n = (len(raw) // 2) * 2
iq = (raw[:n].astype(np.float32) - 127.5) / 127.5
c = iq[0::2] + 1j * iq[1::2]
del raw, iq
print(f"[iq] {len(c)/FS:.1f}s")

STEP = int(FS * 4)
NFFT = 16384
rows = []
for k in range(0, len(c) - NFFT, STEP):
    seg = c[k:k + NFFT] * np.hanning(NFFT)
    P = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(NFFT, 1 / FS))
    m = (f > -8000) & (f < 8000)
    fs_, Pw = f[m], P[m]
    i = int(np.argmax(Pw))
    med = np.median(Pw)
    rows.append((k / FS, fs_[i], 10 * np.log10(Pw[i] / med)))

pred = np.array([pred_dopp(START + timedelta(seconds=r[0])) for r in rows])
obs = np.array([r[1] for r in rows])
snr = np.array([r[2] for r in rows])

print(f"[judge] 预测多普勒 span {np.ptp(pred):.0f} Hz  观测峰值 span {np.ptp(obs):.0f} Hz")
print(f"[judge] 峰值 SNR: 中位 {np.median(snr):.1f} dB  最大 {snr.max():.1f} dB")
if len(pred) > 2 and pred.std() > 1 and obs.std() > 1:
    r = float(np.corrcoef(pred, obs)[0, 1])
else:
    r = 0.0
print(f"[judge] 相关系数 r = {r:+.3f}")
if r > 0.7 and snr.max() > 6:
    print("[verdict] ★ 收到了（峰值跟随多普勒 + SNR 充足）")
elif r > 0.4:
    print("[verdict] ◐ 疑似弱信号（部分跟随多普勒）")
else:
    print("[verdict] ✗ 未收到（峰值与多普勒无关 = 噪声）")
