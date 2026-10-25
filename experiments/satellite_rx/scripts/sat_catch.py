#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sat_catch.py — 收卫星的总入口（大工具形态）
============================================
一条命令：给卫星名，出图像。全程零屏幕依赖：

  算过境 → 选最佳 → 等 AOS → rtl_sdr 录音(SigMF) → LOS 即停
  → 多普勒补偿 → 解调/解码 → 出图 → 文字报告

没有任何一步需要人看屏幕、点按钮。UI 想开就开（只是显示器），
这条链不依赖它。

用法：
  python sat_catch.py --sat "NOAA 19" --freq 137100000
  python sat_catch.py --sat "METEOR-M2 3" --freq 137900000 --demod lrpt
  python sat_catch.py --sat "ISS (ZARYA)" --freq 145990000 --demod nfm
  python sat_catch.py --sat "NOAA 19" --freq 137100000 --now      # 已在过境，立即录
  python sat_catch.py --sat "NOAA 19" --freq 137100000 --dry      # 只看时刻表
"""
import argparse, subprocess, time, os, sys, json, glob
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RTL = r"C:\rtl-sdr\bin\rtl_sdr.exe"
LAT, LON, ALT = 43.774156, 125.397813, 230.8


def sh(cmd, **kw):
    return subprocess.run(cmd, **kw)


def load_tles():
    sats = []
    for fn in sorted(glob.glob(os.path.join(HERE, "tle_*.txt"))) + [os.path.join(HERE, "iss.tle")]:
        if not os.path.exists(fn):
            continue
        L = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
        for i in range(len(L) - 2):
            if L[i+1].startswith("1 ") and L[i+2].startswith("2 "):
                sats.append((L[i].strip(), L[i+1], L[i+2], fn))
    return sats


def compute_passes(sat, min_alt, hours=14):
    from skyfield.api import load, EarthSatellite, Topos
    ts = load.timescale()
    gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    n = int(hours * 3600 / 10)
    t0 = ts.utc(now.year, now.month, now.day, now.hour, now.minute,
                [float(i * 10) for i in range(n)])
    alt, az, dist = (sat - gs).at(t0).altaz()
    ad = np.array(alt.degrees)
    idx = np.where(ad > min_alt)[0]
    out = []
    for g in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
        im = g[int(np.argmax(ad[g]))]
        aos = t0[g[0]].utc_datetime().replace(tzinfo=None)
        los = t0[g[-1]].utc_datetime().replace(tzinfo=None)
        if los > now - timedelta(minutes=2):
            out.append(dict(aos=aos, los=los, alt=float(ad[im]),
                            az=float(az.degrees[im]), km=float(dist.km[im])))
    return out


def record(sat_name, freq, start, stop, rate=1.024e6, prefix="catch"):
    """rtl_sdr 录音到 SigMF（data+meta），返回 (data_path, meta_path, t_start)"""
    data_f = os.path.join(HERE, f"{prefix}.sigmf-data")
    meta_f = os.path.join(HERE, f"{prefix}.sigmf-meta")
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    wait = (start - t_start).total_seconds()
    if wait > 2:
        print(f"[wait] {(wait/60):.1f} 分钟后开录…")
        while (start - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() > 0.5:
            left = (start - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
            print(f"\r[wait] {left:7.1f}s   ", end="", flush=True)
            time.sleep(min(1.0, max(0.2, left)))
        print()
    total = (stop - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
    print(f"[rec] 开录 {total:.0f}s -> {data_f}")
    env = dict(os.environ)
    env["PATH"] = r"C:\rtl-sdr\bin;" + env.get("PATH", "")
    cmd = [RTL, "-f", str(int(freq)), "-s", str(int(rate)), "-p", "0", data_f]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    t0 = time.time()
    while time.time() - t0 < total:
        el = time.time() - t0
        sz = os.path.getsize(data_f) / 1e6 if os.path.exists(data_f) else 0
        print(f"\r[rec] {el:6.1f}/{total:.0f}s  {sz:8.1f} MB   ", end="", flush=True)
        if p.poll() is not None:
            print("\n[warn] rtl_sdr 提前退出，重试一次")
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
            t0 = time.time() - el
        time.sleep(1.0)
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    t_end = datetime.now(timezone.utc).replace(tzinfo=None)
    size = os.path.getsize(data_f) if os.path.exists(data_f) else 0
    print(f"\n[done] {data_f}  {size/1e6:.1f} MB  {size/2/rate:.1f}s")
    meta = {
        "global": {
            "core:datatype": "ci8", "core:sample_rate": rate,
            "core:version": "1.0.0", "core:num_channels": 1,
            "core:description": f"MBDSDR sat_catch recording of {sat_name}",
            "core:recorder": "rtl_sdr (osmocom)", "core:author": "MBDSDR",
            "core:license": "MIT", "core:hw": "RTL2832U + Fitipower FC0012",
            "core:frequency": freq,
            "core:geolocation": {"latitude": LAT, "longitude": LON, "elevation": ALT},
            "mbdsdr:satellite": sat_name,
            "mbdsdr:record_start_utc": t_start.isoformat() + "Z",
            "mbdsdr:record_end_utc": t_end.isoformat() + "Z",
            "mbdsdr:provenance": "ota-candidate",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": freq,
                      "core:datetime": t_start.isoformat() + "Z"}],
        "annotations": [],
    }
    json.dump(meta, open(meta_f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return data_f, meta_f, t_start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sat", required=True)
    ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--demod", default="apt", choices=["apt", "nfm", "wfm", "iq"])
    ap.add_argument("--rate", type=float, default=1.024e6)
    ap.add_argument("--min-alt", type=float, default=25.0)
    ap.add_argument("--pad", type=float, default=20.0)
    ap.add_argument("--hours", type=float, default=14.0)
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    from skyfield.api import EarthSatellite
    sat = None
    tle_fn = "iss.tle"
    for name, l1, l2, fn in load_tles():
        if a.sat.lower() in name.lower():
            sat = EarthSatellite(l1, l2, name)
            sat_name = name
            tle_fn = fn
            break
    if sat is None:
        print(f"[err] TLE 里没有 {a.sat}")
        sys.exit(1)

    passes = compute_passes(sat, a.min_alt, a.hours)
    if not passes:
        print(f"[err] 未来 {a.hours}h 无 >{a.min_alt}° 过境")
        sys.exit(1)
    L8 = timedelta(hours=8)
    print("=" * 74)
    print(f"卫星 {sat_name}   {a.freq/1e6:.4f} MHz   门限 >{a.min_alt}°")
    for i, p in enumerate(passes[:6], 1):
        print(f"  {i}. AOS {(p['aos']+L8).strftime('%H:%M:%S')} → MAX {(p['alt']):.0f}° "
              f"az {p['az']:.0f}° → LOS {(p['los']+L8).strftime('%H:%M:%S')}  "
              f"({(p['los']-p['aos']).seconds//60}min, {p['km']:.0f}km)")
    best = max(passes, key=lambda p: p["alt"])
    p = best
    print(f"已选最高仰角那次：{(p['aos']+L8).strftime('%H:%M:%S')}–{(p['los']+L8).strftime('%H:%M:%S')} "
          f"max {p['alt']:.1f}° az {p['az']:.0f}°")
    print("=" * 74)
    if a.dry:
        print("[dry] 只排时刻")
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now if a.now else p["aos"] - timedelta(seconds=a.pad)
    stop = p["los"] + timedelta(seconds=a.pad)
    if (stop - now).total_seconds() < 30:
        print("[err] 该过境已结束")
        sys.exit(1)

    prefix = a.out or (sat_name.replace(" ", "_").replace("(", "").replace(")", "")
                       + f"_{int(a.freq/1e3)}k")
    data_f, meta_f, t_start = record(sat_name, a.freq, start, stop, a.rate, prefix)

    # ---- 处理 ----
    print("\n[post] 后处理…")
    if a.demod == "apt":
        r = sh([PY, os.path.join(HERE, "apt_decode.py"), data_f,
                t_start.strftime("%Y-%m-%d %H:%M:%S"), "--sat", sat_name,
                "--fs", str(int(a.rate)), "--out", prefix + "_apt"])
        print("[post] apt_decode 退出", r.returncode)
    elif a.demod == "lrpt":
        print("[post] METEOR LRPT QPSK 解调（仓库 meteor_sat.meteor_lrpt_demod）…")
        sys.path.insert(0, "D:/mbdsdr/MBDSDR")
        from mbdsdr_ai.meteor_sat import meteor_lrpt_demod
        from skyfield.api import load, EarthSatellite, Topos, utc
        from datetime import timedelta
        raw = np.fromfile(data_f, dtype=np.uint8)
        n = (len(raw) // 2) * 2
        iq = (raw[:n].astype(np.float32) - 127.5) / 127.5
        c = iq[0::2] + 1j * iq[1::2]
        del raw, iq
        # 多普勒补偿（同 apt_decode 口径）
        try:
            l1 = l2 = None
            for name, a1, a2, fn in load_tles():
                if sat_name.lower() in name.lower():
                    l1, l2 = a1, a2
                    break
            sat = EarthSatellite(l1, l2, sat_name)
            ts = load.timescale()
            gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
            dur = len(c) / a.rate
            ng = int(dur) + 3
            gt = [t_start + timedelta(seconds=i) for i in range(ng)]
            d0 = (sat - gs).at(ts.utc([g.replace(tzinfo=utc) for g in gt])).distance().km
            d1 = (sat - gs).at(ts.utc([(g + timedelta(seconds=1)).replace(tzinfo=utc)
                                       for g in gt])).distance().km
            fd = -(d1 - d0) / 299792.458 * a.freq
            ta = np.arange(len(c)) / a.rate
            fdi = np.interp(ta, np.arange(ng), fd)
            c = c * np.exp(-1j * 2 * np.pi * np.cumsum(fdi) / a.rate)
            print(f"[post] 多普勒补偿 {fd[0]:+.0f}→{fd[-1]:+.0f} Hz")
        except Exception as e:
            print(f"[post] 多普勒补偿跳过（{e}）")
        res = meteor_lrpt_demod(c, a.rate)
        print(f"[post] LRPT 解调: {res}")
        with open(os.path.join(HERE, prefix + "_lrpt.json"), "w") as f:
            json.dump(res, f, indent=2)
    elif a.demod in ("nfm", "wfm"):
        r = sh([PY, os.path.join(HERE, "doppler_fm.py"), data_f,
                t_start.strftime("%Y-%m-%d %H:%M:%S"), "--freq", str(int(a.freq)),
                "--fs", str(int(a.rate)), "--out", prefix + "_fm.wav"])
        print("[post] doppler_fm 退出", r.returncode)

    # ---- 判决：多普勒轨迹相关性 ----
    print("\n[judge] 多普勒轨迹判决…")
    r = sh([PY, os.path.join(HERE, "waterfall.py"), data_f,
            t_start.strftime("%Y-%m-%d %H:%M:%S"), str(int(a.freq)),
            str(int(a.rate)), tle_fn, sat_name])
    imgs = sorted(glob.glob(os.path.join(HERE, prefix + "*")))
    print("\n" + "=" * 74)
    print("产物清单：")
    for f in imgs:
        print(f"  {os.path.basename(f)}  {os.path.getsize(f)/1e6:.2f} MB")
    print("=" * 74)


if __name__ == "__main__":
    main()
