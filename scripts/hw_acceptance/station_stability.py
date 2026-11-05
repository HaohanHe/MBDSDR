#!/usr/bin/env python3
"""同台重复性实验：19 kHz 导频频率/幅度 与 噪声底漂移。

两个子命令，都固定**同一个电台 + 同一个增益**，不做跨台比较::

    # 导频：同一台连录 N 段（默认 5 段 × 30 s），逐段给频率/幅度
    python scripts/hw_acceptance/station_stability.py pilot --freq 98.0e6 --segments 5 --seconds 30

    # 同一台同一增益下，换采样率复测导频（验「我们侧」的速率/重采样）
    python scripts/hw_acceptance/station_stability.py pilot --freq 98.0e6 --rates 1024000,2048000,2400000 --seconds 6

    # 噪声底漂移：同一台同一增益连跑 N 次（与 ring2 完全同口径）
    python scripts/hw_acceptance/station_stability.py floor --freq 98.0e6 --runs 5

    # 先扫一遍，看哪台最强/有没有导频
    python scripts/hw_acceptance/station_stability.py scan

测量方法（与 ring3 的 `_pilot` 不同，这里是长时相干平均）：
  1. 正交鉴频 → MPX，用 ``resample_poly`` **带抗混叠滤波**降到 200 kHz；
  2. Welch（65536 点/段、Hann、约 90 段平均）→ 3.05 Hz 分辨率；
  3. 18.5–19.5 kHz 取峰 + 抛物线插值 → 亚 bin 频率；
  4. 同时给两个幅度口径：
     * ``pilot_over_mpx_db = 10log10(P_pilot/P_mpx)``（产品链 pilot_snr_db 的定义，
       典型 FM 导频注入 8–10% → 约 −20 dB，**不是**信噪比）；
     * ``peak_over_neigh_db = 10log10(峰/邻域中位数)``（ring3 用的口径）。
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

import common
from ring2_spectrum_waterfall import FLOOR_DRIFT_MAX_DB

FS = 2.4e6
MPX_FS = 200000
SEG_N = 65536
DEFAULT_GAIN = 17.9  # FC0012 的固定档位之一（实测可选 -9.9/-4.0/7.1/17.9/19.2 dB）


def _mpx_chunk(x: np.ndarray, fs: float) -> np.ndarray:
    """IQ → MPX（瞬时频偏），带抗混叠滤波降到 200 kHz。"""
    from math import gcd

    x = np.asarray(x, dtype=np.complex128)
    ph = np.angle(x[1:] * np.conj(x[:-1]))
    mpx = ph * (fs / (2.0 * np.pi))
    g = gcd(int(round(fs)), MPX_FS)
    from scipy import signal as sg

    return sg.resample_poly(mpx, MPX_FS // g, int(round(fs)) // g)


def analyze(mpx: np.ndarray) -> dict:
    """Welch 平均后在 18.5–19.5 kHz 找导频；返回频率与两个幅度口径。"""
    n = SEG_N
    k_seg = len(mpx) // n
    if k_seg < 8:
        return {"ok": False, "reason": f"MPX 太短：{len(mpx)} 点，{k_seg} 段"}
    w = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    for i in range(k_seg):
        seg = mpx[i * n:(i + 1) * n]
        acc += np.abs(np.fft.rfft((seg - seg.mean()) * w)) ** 2
    acc /= k_seg
    fr = np.arange(len(acc)) * MPX_FS / n
    band = np.flatnonzero((fr > 18500) & (fr < 19500))
    if len(band) == 0:
        return {"ok": False, "reason": "搜索带为空"}
    k = int(band[np.argmax(acc[band])])
    y0, y1, y2 = (math.log(float(acc[k + d]) + 1e-30) for d in (-1, 0, 1))
    den = y0 - 2 * y1 + y2
    delta = 0.5 * (y0 - y2) / den if den else 0.0
    f_p = (k + delta) * MPX_FS / n
    neigh = ((fr > 16500) & (fr < 18000)) | ((fr > 20000) & (fr < 21500))
    peak_db = 10.0 * math.log10(float(acc[k]) / (float(np.median(acc[neigh])) + 1e-30))
    p_total = float(np.sum(acc[1:])) + 1e-30
    ratio_db = 10.0 * math.log10(float(acc[k]) / p_total)
    return {
        "ok": True, "pilot_hz": round(f_p, 2),
        "peak_over_neigh_db": round(peak_db, 2),
        "pilot_over_mpx_db": round(ratio_db, 2),
        "welch_segments": k_seg,
        "bin_hz": round(MPX_FS / n, 3),
    }


READ_CHUNK = 262144  # 实测吞吐最好（131072→2.15、262144→2.28 MS/s；小块被 Python 调用开销拖慢）


def capture_mpx(ctrl, fs: float, seconds: float):
    """连续采 seconds 秒，返回 (mpx@200k, 实际采样数, 墙钟秒, 实际采样率)。"""
    chunk = READ_CHUNK
    parts, total = [], 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        x = ctrl.read_iq(chunk)
        total += len(x)
        parts.append(_mpx_chunk(x, fs))
    elapsed = time.perf_counter() - t0
    mpx = np.concatenate(parts) if parts else np.zeros(0)
    return mpx, total, elapsed, (total / elapsed if elapsed else 0.0)


def _open(freq: float, fs: float, gain: float):
    ctrl = common.open_controller(f"rtlsdr:{common.real_devices()[0].serial}")
    ctrl.set_sample_rate(fs)
    ctrl.set_frequency(freq)
    ctrl.set_gain(gain)
    time.sleep(0.3)
    return ctrl


def cmd_scan(args) -> int:
    ctrl = common.open_controller(f"rtlsdr:{common.real_devices()[0].serial}")
    try:
        ctrl.set_sample_rate(FS)
        freq, pf = common.find_strong_fm(ctrl, 1024, FS)
        print(f"[scan] 最强台 {freq/1e6:.3f} MHz，峰底比 {pf:.1f} dB")
        out = []
        for f in (98000000.0, freq):
            if not f:
                continue
            ctrl.set_frequency(f)
            time.sleep(0.4)
            mpx, total, elapsed, actual = capture_mpx(ctrl, FS, args.seconds)
            r = analyze(mpx)
            r.update({"freq_hz": f, "actual_rate_msps": round(actual / 1e6, 4)})
            out.append(r)
            print(f"[scan] {f/1e6:.3f} MHz -> {r}")
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    finally:
        try:
            ctrl.disconnect()
        except Exception:
            pass
    return 0


def cmd_pilot(args) -> int:
    rates = [float(x) for x in args.rates.split(",")] if args.rates else [args.rate]
    seconds = args.seconds
    result: dict = {"freq_hz": args.freq, "gain_db": args.gain, "segments": [],
                     "by_rate": []}

    if len(rates) > 1:
        # 换采样率复测：同一台同一段内容，频率应不随请求速率漂移
        for fs in rates:
            try:
                ctrl = _open(args.freq, fs, args.gain)
            except Exception as e:
                result["by_rate"].append({"rate": fs, "error": f"{type(e).__name__}: {e}"})
                continue
            try:
                mpx, total, elapsed, actual = capture_mpx(ctrl, fs, seconds)
                r = analyze(mpx)
                r.update({"requested_rate": fs, "actual_rate_msps": round(actual / 1e6, 4),
                          "rate_ratio": round(actual / fs, 6)})
                result["by_rate"].append(r)
                print(f"[pilot] 请求 {fs/1e6:.3f} MS/s 实际 {actual/1e6:.4f} MS/s "
                      f"({actual/fs:.4%}) -> {r}")
            finally:
                try:
                    ctrl.disconnect()
                except Exception:
                    pass
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return 0

    ctrl = _open(args.freq, rates[0], args.gain)
    try:
        for i in range(args.segments):
            mpx, total, elapsed, actual = capture_mpx(ctrl, rates[0], seconds)
            r = analyze(mpx)
            r.update({"segment": i + 1, "actual_rate_msps": round(actual / 1e6, 4),
                      "samples": total, "elapsed_s": round(elapsed, 2)})
            result["segments"].append(r)
            print(f"[pilot] 段 {i+1}/{args.segments}: {r}")
    finally:
        try:
            ctrl.disconnect()
        except Exception:
            pass
    segs = [s for s in result["segments"] if s.get("ok")]
    if segs:
        freqs = [s["pilot_hz"] for s in segs]
        result["summary"] = {
            "pilot_hz_min": round(min(freqs), 2), "pilot_hz_max": round(max(freqs), 2),
            "pilot_hz_range": round(max(freqs) - min(freqs), 2),
            "pilot_over_mpx_db_min": min(s["pilot_over_mpx_db"] for s in segs),
            "pilot_over_mpx_db_max": max(s["pilot_over_mpx_db"] for s in segs),
        }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("[pilot] summary:", result.get("summary"))
    return 0


def cmd_floor(args) -> int:
    """与 ring2 完全同口径：2.4 MS/s、1024 点、±700 kHz、读 35 帧丢前 3 帧。"""
    runs = []
    for i in range(args.runs):
        ctrl = _open(args.freq, FS, args.gain)
        try:
            floors = []
            for j in range(35):
                db = ctrl.read_spectrum(1024)
                if j < 3 or db is None:
                    continue
                arr = np.fft.fftshift(np.asarray(db, dtype=np.float64))
                floors.append(float(np.median(arr)))
                time.sleep(0.02)  # 与 ring2 同口径：帧间隔 20 ms
            drift = max(floors) - min(floors) if floors else float("nan")
            run = {"run": i + 1, "frames": len(floors), "drift_db": round(drift, 2),
                   "floor_mean_db": round(float(np.mean(floors)), 2),
                   "floor_min_db": round(min(floors), 2),
                   "floor_max_db": round(max(floors), 2)}
            runs.append(run)
            print(f"[floor] 第 {i+1}/{args.runs} 次: {run}")
        finally:
            try:
                ctrl.disconnect()
            except Exception:
                pass
        time.sleep(0.4)
    drifts = [r["drift_db"] for r in runs]
    summary = {
        "runs": len(runs),
        "drift_db_values": drifts,
        "drift_db_max": max(drifts) if drifts else None,
        "drift_db_min": min(drifts) if drifts else None,
        "drift_db_range": round(max(drifts) - min(drifts), 2) if drifts else None,
        "drift_db_mean": round(float(np.mean(drifts)), 2) if drifts else None,
        "drift_db_sd": round(float(np.std(drifts, ddof=1)), 2) if len(drifts) > 1 else None,
        "current_threshold_db": FLOOR_DRIFT_MAX_DB,
    }
    if drifts:
        summary["threshold_from_distribution_1p5x_max"] = round(1.5 * max(drifts), 2)
        summary["threshold_from_distribution_mean_plus_3sd"] = round(
            float(np.mean(drifts)) + 3.0 * float(np.std(drifts, ddof=1)), 2)
    print("[floor] summary:", summary)
    Path(args.out).write_text(json.dumps({"runs": runs, "summary": summary},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="同台重复性实验（导频 / 噪声底）")
    ap.add_argument("mode", choices=["scan", "pilot", "floor"])
    ap.add_argument("--freq", type=float, default=98.0e6)
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN)
    ap.add_argument("--rate", type=float, default=2048000.0)
    ap.add_argument("--rates", default="", help="逗号分隔，多个速率依次复测")
    ap.add_argument("--segments", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not args.out:
        stamp = time.strftime("%H%M%S")
        args.out = f"D:\\mbdsdr\\tmp\\stab_{args.mode}_{stamp}.json"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    if not common.device_present():
        print("[stab] 无设备，退出（不伪造数字）")
        return 2
    return {"scan": cmd_scan, "pilot": cmd_pilot, "floor": cmd_floor}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
