#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按过境时刻精确计时的录音器（你说的"38 起、53 落，就录 38→53"）
=================================================================
自动做三件事：
  1. 用 TLE + GNSS 站址算出本次过境的 AOS / 最大仰角 / LOS
  2. 等到 AOS - pad 才开始录，LOS + pad 立刻停 —— 一秒不多录
  3. 写标准 SigMF 双文件（data + meta），方便事后处理/归档

用法
----
# 按卫星名（从本地 TLE 库找）
python pass_recorder.py --sat "ISS (ZARYA)" --freq 145990000

# 按 NORAD 号 + 指定 TLE 文件
python pass_recorder.py --norad 25544 --tle iss.tle --freq 437800000

# 换门限/提前量/门限仰角
python pass_recorder.py --sat "METEOR-M2 3" --freq 137900000 --min-alt 15 --pad 20

# 过境已经开始：加 --now 立即开录到 LOS
python pass_recorder.py --sat "ISS (ZARYA)" --freq 145990000 --now

# 只看时刻不录（--dry）
python pass_recorder.py --sat "NOAA 19" --freq 137100000 --dry
"""
import argparse, subprocess, signal, time, json, os, sys, glob
from datetime import datetime, timedelta, timezone

from skyfield.api import load, EarthSatellite, Topos, utc

LAT, LON, ALT = 43.774156, 125.397813, 230.8      # GNSS 实测站址（长春）
RTL = r"C:\rtl-sdr\bin\rtl_sdr.exe"
OUTDIR = "."
HERE = os.path.dirname(os.path.abspath(__file__))
# TLE 库按优先级搜：仓库入库目录 data/ → 脚本目录 → 当前工作目录
TLE_DIRS = [os.path.join(HERE, os.pardir, "data"), HERE, os.getcwd()]

# SIGTERM/SIGBREAK 时走 KeyboardInterrupt 清理路径，保证 meta 落盘。
# 注意：Windows 下 os.kill(pid, SIGTERM) 是 TerminateProcess，处理器来不及跑；
# 能救的是 SIGBREAK/CTRL_BREAK/CTRL_CLOSE 与 POSIX 语义的 SIGTERM。
STOP = {"reason": None}


def _on_signal(signum, frame):
    try:
        STOP["reason"] = signal.Signals(signum).name
    except Exception:
        STOP["reason"] = f"signal {signum}"
    raise KeyboardInterrupt


def name_hit(query, name):
    """卫星名匹配打分：完整名/归一化名 2 > 词边界子串 1 > 0（防 "ISS" 命中 SWISSCUBE）"""
    import re
    q, n = (query or "").strip().lower(), (name or "").strip().lower()
    nq = re.sub(r"[^a-z0-9]+", "", q)
    nn = re.sub(r"[^a-z0-9]+", "", n)
    if not q:
        return 0
    if q == n or (nq and nq == nn):
        return 2
    if re.search(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", n):
        return 1
    return 0


def find_sat(norad=None, name=None, tle_files=None):
    files = list(tle_files) if tle_files else []
    if not files:
        for d in TLE_DIRS:
            d = os.path.abspath(d)
            files += sorted(glob.glob(os.path.join(d, "tle_*.txt")))
            files += [os.path.join(d, "iss.tle")]
    best = None                      # (score, name, sat, fn)
    for fn in files:
        if not os.path.exists(fn):
            continue
        L = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
        for i in range(len(L) - 2):
            l1, l2 = L[i + 1], L[i + 2]
            if not (l1.startswith("1 ") and l2.startswith("2 ")):
                continue
            if norad and l1[2:7].strip() == str(norad).strip():
                return L[i].strip(), EarthSatellite(l1, l2, L[i]), fn
            if name:
                s = name_hit(name, L[i])
                if s and (best is None or s > best[0]):
                    best = (s, L[i].strip(), EarthSatellite(l1, l2, L[i]), fn)
                    if s == 2:
                        return best[1], best[2], best[3]
    if best is not None:
        return best[1], best[2], best[3]
    return None, None, None


def compute_pass(sat, ts, gs, min_alt, hours=14, step=10.0):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    n = int(hours * 3600 / step)
    t0 = ts.utc(now.year, now.month, now.day, now.hour, now.minute,
                [float(i * step) for i in range(n)])
    import numpy as np
    alt, az, dist = (sat - gs).at(t0).altaz()
    ad = np.array(alt.degrees)
    idx = np.where(ad > min_alt)[0]
    if len(idx) == 0:
        return None
    for g in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
        im = g[int(np.argmax(ad[g]))]
        aos = t0[g[0]].utc_datetime().replace(tzinfo=None)
        los = t0[g[-1]].utc_datetime().replace(tzinfo=None)
        tmax = t0[im].utc_datetime().replace(tzinfo=None)
        if los > now - timedelta(minutes=2):        # 还没结束的都算
            return dict(aos=aos, los=los, tmax=tmax, alt=float(ad[im]),
                        az=float(az.degrees[im]), km=float(dist.km[im]))
    return None


def write_meta(meta_f, data_f, name, freq, rate, p, t_start, t_end, interrupted=None):
    """SigMF 元数据落盘——录音中断（SIGTERM/KeyboardInterrupt/异常）也必须写。

    mbdsdr:interrupted 非空 = 录制非正常走完，处理时按部分数据对待。
    """
    size = os.path.getsize(data_f) if os.path.exists(data_f) else 0
    meta = {
        "global": {
            "core:datatype": "ci8",
            "core:sample_rate": rate,
            "core:version": "1.0.0",
            "core:num_channels": 1,
            "core:description": f"MBDSDR pass recording of {name}",
            "core:recorder": "rtl_sdr (osmocom)",
            "core:author": "MBDSDR",
            "core:license": "MIT",
            "core:hw": "RTL2832U + Fitipower FC0012",
            "core:frequency": freq,
            "core:geolocation": {"latitude": LAT, "longitude": LON, "elevation": ALT},
            "core:extensions": [
                {"name": "mbdsdr", "version": "1.0.0", "optional": True}
            ],
            "mbdsdr:satellite": name,
            "mbdsdr:pass_aos_utc": p["aos"].isoformat() + "Z",
            "mbdsdr:pass_los_utc": p["los"].isoformat() + "Z",
            "mbdsdr:max_elevation_deg": round(p["alt"], 2),
            "mbdsdr:max_azimuth_deg": round(p["az"], 2),
            "mbdsdr:slant_range_km": round(p["km"], 1),
            "mbdsdr:record_start_utc": t_start.isoformat() + "Z",
            "mbdsdr:record_end_utc": t_end.isoformat() + "Z",
            "mbdsdr:record_bytes": size,
            "mbdsdr:interrupted": interrupted or "",
            "mbdsdr:provenance": "ota-candidate",
        },
        "captures": [
            {"core:sample_start": 0, "core:frequency": freq,
             "core:datetime": t_start.isoformat() + "Z"}
        ],
        "annotations": [],
    }
    with open(meta_f, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sat", help="卫星名（模糊匹配）")
    ap.add_argument("--norad", help="NORAD 号")
    ap.add_argument("--tle", help="指定 TLE 文件")
    ap.add_argument("--freq", type=float, required=True, help="下行中心频率 Hz")
    ap.add_argument("--rate", type=float, default=1.024e6, help="采样率（默认1.024M，稳）")
    ap.add_argument("--min-alt", type=float, default=10.0, help="AOS/LOS 门限仰角")
    ap.add_argument("--pad", type=float, default=15.0, help="AOS 前/LOS 后多录的秒数")
    ap.add_argument("--hours", type=float, default=14.0)
    ap.add_argument("--now", action="store_true", help="立即开录（过境已开始）")
    ap.add_argument("--dry", action="store_true", help="只算时刻不录")
    ap.add_argument("--out", help="输出前缀")
    a = ap.parse_args()

    # SIGTERM/SIGBREAK 一律走清理路径（录音中断也要写 meta）
    signal.signal(signal.SIGTERM, _on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _on_signal)

    name, sat, src = find_sat(a.norad, a.sat, [a.tle] if a.tle else None)
    if sat is None:
        print(f"[err] 没找到卫星：{a.sat or a.norad}（TLE 库里没有，或该星已再入/移出数据集）")
        sys.exit(1)

    ts = load.timescale()
    gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
    p = compute_pass(sat, ts, gs, a.min_alt, a.hours)
    if p is None:
        print(f"[err] 未来 {a.hours}h 内没有仰角 >{a.min_alt}° 的过境")
        sys.exit(1)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    L8 = timedelta(hours=8)
    print("=" * 70)
    print(f"卫星   : {name}   (TLE 来源 {src})")
    print(f"下行   : {a.freq/1e6:.6f} MHz   采样率 {a.rate/1e6:.3f} MS/s")
    print(f"AOS    : {(p['aos']+L8).strftime('%H:%M:%S')} 本地  (仰角过 {a.min_alt}°)")
    print(f"最大   : {(p['tmax']+L8).strftime('%H:%M:%S')} 本地  仰角 {p['alt']:.1f}°  方位 {p['az']:.0f}°  "
          f"距离 {p['km']:.0f} km")
    print(f"LOS    : {(p['los']+L8).strftime('%H:%M:%S')} 本地")
    print(f"过境时长: {(p['los']-p['aos']).total_seconds()/60:.1f} 分钟   加 pad {a.pad}s"
          f" -> 实际录制 {(p['los']-p['aos']).total_seconds()+2*a.pad:.0f}s")
    print("=" * 70)

    if a.dry:
        print("[dry] 只算时刻，不录音")
        return

    start = now if a.now else p["aos"] - timedelta(seconds=a.pad)
    stop = p["los"] + timedelta(seconds=a.pad)
    wait = (start - now).total_seconds()
    if wait > 2:
        print(f"[wait] 等待 {(wait/60):.1f} 分钟到 {start+L8 and (start+L8).strftime('%H:%M:%S')} 开录…")
        while (start - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() > 0.5:
            left = (start - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
            print(f"\r[wait] {left:7.1f}s 后开录   ", end="", flush=True)
            time.sleep(min(1.0, max(0.2, left)))
        print()
    elif wait < -30:
        print(f"[warn] AOS 已过去 {-wait:.0f}s，直接开录剩余部分")

    prefix = a.out or f"{name.replace(' ','_').replace('(','').replace(')','')}_{int(a.freq/1e3)}k"
    data_f = f"{prefix}.sigmf-data"
    meta_f = f"{prefix}.sigmf-meta"
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)

    cmd = [RTL, "-f", str(int(a.freq)), "-s", str(int(a.rate)), "-p", "0", data_f]
    print(f"[rec] 启动: {' '.join(cmd)}")
    total = (stop - t_start).total_seconds()
    proc = None
    interrupted = None
    t_end = t_start
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < total:
            left = total - (time.time() - t0)
            el = time.time() - t0
            sz = os.path.getsize(data_f) / 1e6 if os.path.exists(data_f) else 0
            print(f"\r[rec] {el:6.1f}/{total:.0f}s  剩 {left:6.1f}s  {sz:8.1f} MB   ", end="", flush=True)
            if proc.poll() is not None:
                print("\n[warn] rtl_sdr 提前退出（可能 USB 错误）")
                interrupted = "rtl_sdr-early-exit"
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        interrupted = interrupted or STOP["reason"] or "KeyboardInterrupt"
        print(f"\n[stop] 录音中断（{interrupted}）——收尾写 meta")
    finally:
        # 不管怎么收场（SIGTERM/SIGBREAK→KeyboardInterrupt、USB 错、意外异常），
        # 都要：杀录音进程 → 写 meta。录到多少写多少，mbdsdr:interrupted 记原因。
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        t_end = datetime.now(timezone.utc).replace(tzinfo=None)
        write_meta(meta_f, data_f, name, a.freq, a.rate, p, t_start, t_end, interrupted)

    size = os.path.getsize(data_f) if os.path.exists(data_f) else 0
    nsamp = size // 2
    print(f"\n[done] {data_f}  {size/1e6:.1f} MB  {nsamp} samples  "
          f"{nsamp/a.rate:.1f}s")
    print(f"[done] {meta_f}  (SigMF 元数据已写入"
          + (f"，interrupted={interrupted})" if interrupted else ")"))
    print(f"[hint] 解调: python doppler_fm.py {data_f} \"{t_start.strftime('%Y-%m-%d %H:%M:%S')}\" --fs {int(a.rate)}")


if __name__ == "__main__":
    main()
