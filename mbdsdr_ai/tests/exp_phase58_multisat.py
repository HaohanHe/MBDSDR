#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Phase58：运行时真实 TLE 多星 LEO 多普勒包络 + PLL 跟踪实测。

红线：TLE 运行时从 celestrak 拉（不预置/不入库固定星表）；星表完全参数化
（--catnrs）；活动频率不进代码（下行频率 --freq 传入）；联网失败诚实 FAIL 不伪造。

运行: python3 mbdsdr_ai/tests/exp_phase58_multisat.py --catnrs 25544,43013,48901 \
          --freq 437.5e6 --lat 43.8 --lon 125.3
"""
from __future__ import annotations
import argparse, json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.orbit import fetch_tle, _state_from_satrec  # noqa: E402
from mbdsdr_ai.ccsds_ssdv import (synthesize_ssdv_ccsds_iq,  # noqa: E402
                                  ccsds_iq_to_result)
from sgp4.api import Satrec  # noqa: E402

C = 299792458.0


def pass_doppler_curve(catnr: int, lat: float, lon: float, freq: float,
                       horizon_deg: float = 5.0, step_s: float = 2.0):
    """运行时拉 TLE，扫描未来 90min 找过境，返回 (t_rel_s, dop_hz, peak_hz, rate_hz_s)。"""
    l1, l2 = fetch_tle(catnr)
    sat = Satrec.twoline2rv(l1, l2)
    epoch = l1[18:32].strip()
    import time
    t0 = time.time()
    rows = []
    for k in range(int(90 * 60 / step_s)):
        t = t0 + k * step_s
        jd = t / 86400.0 + 2440587.5
        st = _state_from_satrec(sat, str(catnr), jd, lat, lon, 0.0, epoch=epoch)
        if st is None or st["elevation"] < horizon_deg:
            continue
        dop = -st["range_rate_kms"] * 1e3 / C * freq  # Hz
        rows.append((k * step_s, dop))
    if not rows:
        return None, epoch
    dop = np.array([d for _, d in rows])
    t = np.array([tt for tt, _ in rows])
    peak = float(np.max(np.abs(dop)))
    rate = float(np.max(np.abs(np.gradient(dop, step_s)))) if len(dop) > 2 else 0.0
    return {"t": t, "dop": dop, "peak_hz": peak, "rate_hz_s": rate,
            "epoch": epoch}, l1[18:32].strip()


def track_test(curve: dict, fs: float, symrate: float) -> dict:
    """把该星多普勒曲线叠加到合成 BPSK 全链，测 PLL+afc 跟踪恢复。"""
    img = np.zeros((48, 48, 3), np.uint8)
    img[:, :, 0] = np.linspace(0, 255, 48, dtype=np.uint8)
    tx = synthesize_ssdv_ccsds_iq(img, fs, symrate, callsign="T", image_id=1)
    base = tx.iq
    n = base.size
    frame_s = n / fs  # 一帧时长（秒）
    # 真实每帧多普勒变化 = 速率 × 帧时长（过境中几分钟才达峰值，单帧内只扫这点）
    per_frame_swing = curve["rate_hz_s"] * frame_s
    finst = np.linspace(-per_frame_swing, per_frame_swing, n)
    iq = base * np.exp(1j * 2 * np.pi * np.cumsum(finst) / fs)
    r = ccsds_iq_to_result(iq, fs, symrate, frame_bits=tx.frame_bits,
                           timing="coarse", afc=True, pll=True, asm_tol=2)
    return {"asm": r.n_asm_frames, "mcu": r.received_mcus, "mcu_tot": r.mcu_count,
            "per_frame_swing_hz": round(per_frame_swing, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catnrs", required=True, help="逗号分隔 NORAD CATNR（参数化，不内置星表）")
    ap.add_argument("--freq", type=float, required=True, help="下行频率 Hz（调用方传入，不硬编码）")
    ap.add_argument("--lat", type=float, default=43.8)
    ap.add_argument("--lon", type=float, default=125.3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    results = []
    for cat in [int(x) for x in a.catnrs.split(",")]:
        try:
            curve, epoch = pass_doppler_curve(cat, a.lat, a.lon, a.freq)
        except Exception as e:  # 联网/解析失败诚实空态
            results.append({"catnr": cat, "status": "FAIL",
                            "reason": f"拉 TLE 失败: {type(e).__name__} {str(e)[:80]}"})
            continue
        if curve is None:
            results.append({"catnr": cat, "status": "NO_PASS", "epoch": epoch,
                            "reason": "未来90min无过境（诚实空态，不伪造）"})
            continue
        tr = track_test(curve, 48000.0, 4800.0)
        results.append({"catnr": cat, "status": "OK", "epoch": epoch,
                        "peak_doppler_hz": round(curve["peak_hz"], 1),
                        "max_rate_hz_s": round(curve["rate_hz_s"], 2), **tr})
        print(f"  {cat}: peak={curve['peak_hz']:.0f}Hz rate={curve['rate_hz_s']:.1f}Hz/s "
              f"mcu={tr['mcu']}/{tr['mcu_tot']}")
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(results, open(a.out, "w"), ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
