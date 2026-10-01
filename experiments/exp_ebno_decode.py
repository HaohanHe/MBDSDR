#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：数字模式解码成功率 vs Eb/N0（仿真口径）
=================================================

被测对象（均为 mbdsdr_ai 内**真实解码器**，非 mock）：
  1. AX.25/AFSK Bell202 1200 baud —— mbdsdr_ai.ax25.AFSKModem，判据 FCS(CRC16) 有效。
  2. ADS-B Mode-S 1 Mbps          —— mbdsdr_ai.adsb.decode_baseband，判据 CRC-24 有效。
  3. BPSK 10 kbps（自包含相干链）   —— 已知比特 -> ±1 脉冲成型 -> AWGN ->
                                     积分清零相干判决，判据整包 0 误码。

口径：纯合成（synthetic / 仿真）。信号与噪声全部由固定种子生成，云内可复现。
横轴 Eb/N0(dB) 由带内 SNR 经 common/ebno.py 实算换算（B=fs, Rb 见 MODE_TABLE）。
每格 trials 次独立试验，成功率 + Wilson 95% CI。

红线：本脚本只产 "synthetic" 口径产物；不读任何录制文件，不冒充 OTA。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai import ax25, adsb  # noqa: E402
from experiments.common import ebno, runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")


# ---------------------------------------------------------------------------
# 各模式 trial：给定带内 snr_db，跑一次解码，返回成功 bool
# ---------------------------------------------------------------------------
def trial_ax25(rng: np.random.Generator, snr_db: float,
               modem: ax25.AFSKModem) -> bool:
    """AX.25 1200: 合成 APRS 位置帧 -> AFSK 调制 -> 加噪 -> 解调 -> FCS 有效?"""
    k = int(rng.integers(0, 1000))
    frame = ax25.build_aprs_position_frame(
        "BI4MIB", 43.88 + k * 0.0001, 125.32 + k * 0.0001)
    audio = modem.modulate(frame)               # real @ 44100 Hz
    sp = float(np.mean(audio ** 2))
    if np.isfinite(snr_db):
        audio = audio + rng.normal(
            0.0, np.sqrt(sp / 10.0 ** (snr_db / 10.0)), size=audio.shape)
    out = modem.demodulate(audio)
    return bool(out and out[0].fcs_valid)


def trial_adsb(rng: np.random.Generator, snr_db: float, fs=4e6) -> bool:
    """ADS-B: 随机识别帧 -> PPM 基带 -> 加噪 -> 前导+CRC 判决?"""
    CALL = "ABCDEFGH0123456789"
    icao = f"{int(rng.integers(0, 0xFFFFFF)):06X}"
    cs = "".join(rng.choice(list(CALL), size=8))[:8]
    frame = adsb.build_identification_frame(icao, cs)
    lead = float(rng.uniform(0, 20))
    iq = adsb.modulate_baseband(frame, fs=fs, lead_us=lead)
    iq = adsb.add_awgn(iq, snr_db, rng)
    d = adsb.decode_baseband(iq, fs)
    return bool(d.get("crc_ok"))


def trial_bpsk(rng: np.random.Generator, snr_db: float,
               fs: float = 100_000.0, sps: int = 10, nbits: int = 200) -> bool:
    """BPSK 10k: 随机比特 -> ±1 矩形脉冲成型 -> AWGN -> 积分清零相干判决, 整包 0 误码?"""
    bits = rng.integers(0, 2, nbits)
    sym = np.where(bits == 1, 1.0, -1.0).astype(np.complex128)
    x = np.repeat(sym, sps)                      # 矩形脉冲成型
    x = x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)
    sp = float(np.mean(np.abs(x) ** 2))
    if np.isfinite(snr_db):
        noise_p = sp * 10.0 ** (-snr_db / 10.0)
        w = np.sqrt(noise_p / 2.0) * (rng.standard_normal(x.size)
                                       + 1j * rng.standard_normal(x.size))
        x = x + w
    # 相干积分清零（理想位同步/载波相位同步，best-case）
    y = x.reshape(nbits, sps).mean(axis=1)
    dec = (np.real(y) > 0).astype(int)
    return bool(np.array_equal(dec, bits))


MODES = {
    "afsk_ax25_1200": {
        "label": "AX.25/AFSK 1200",
        "trial": trial_ax25,
        "needs_modem": True,
    },
    "adsb_modes_1m": {
        "label": "ADS-B Mode-S 1M",
        "trial": trial_adsb,
        "needs_modem": False,
    },
    "bpsk_10k": {
        "label": "BPSK 10k",
        "trial": trial_bpsk,
        "needs_modem": False,
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--out", default=OUT_DIR)
    # 覆盖三模式拐点：BPSK~5-10dB、ADS-B~8-14dB、AX.25(Δ=15.6dB)~21-26dB
    ap.add_argument("--ebn0-min", type=float, default=0.0)
    ap.add_argument("--ebn0-max", type=float, default=30.0)
    ap.add_argument("--ebn0-step", type=float, default=2.0)
    args = ap.parse_args()

    runner_obj = runner.ExperimentRunner(args.seed)
    modem44 = ax25.AFSKModem(sample_rate=44100)

    ebn0_grid = np.arange(args.ebn0_min, args.ebn0_max + 1e-9, args.ebn0_step)
    all_rows = []
    curve_data: dict = {}
    for mode, info in MODES.items():
        p = ebno.mode_params(mode)
        rb, b = float(p["rb"]), float(p["b"])
        curves_x, curves_rate, curves_lo, curves_hi = [], [], [], []
        for ebn0_db in ebn0_grid:
            snr_db = ebno.ebn0_db_to_snr_db(float(ebn0_db), rb, b)
            rng = runner_obj.make_rng(f"{mode}|ebn0={ebn0_db:.1f}")
            succ = 0
            for _ in range(args.trials):
                if info["needs_modem"]:
                    ok = info["trial"](rng, snr_db, modem44)
                else:
                    ok = info["trial"](rng, snr_db)
                if ok:
                    succ += 1
            cell = runner.BinomialCell(succ, args.trials)
            lo, hi = cell.wilson
            row = {
                "mode": mode,
                "mode_label": info["label"],
                "rb_bps": rb, "b_hz": b,
                "ebn0_db": round(float(ebn0_db), 3),
                "snr_inband_db": round(snr_db, 3),
                "successes": succ, "n_trials": args.trials,
                "success_rate": round(cell.rate, 4),
                "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
                "data_origin": ORIGIN,
            }
            all_rows.append(row)
            curves_x.append(float(ebn0_db))
            curves_rate.append(cell.rate)
            curves_lo.append(lo)
            curves_hi.append(hi)
            print(f"[{info['label']:>16}] Eb/N0={ebn0_db:5.1f} dB "
                  f"(SNR_in={snr_db:6.2f})  success={succ}/{args.trials} "
                  f"({cell.rate*100:5.1f}%)", flush=True)
        curve_data[info["label"]] = {
            "x": curves_x, "rate": curves_rate, "lo": curves_lo, "hi": curves_hi}

    # --- CSV ---
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "ebno_decode_success.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n[写] {csv_path}")

    # --- 图 ---
    n_samples = len(all_rows) * args.trials
    fig_path = eplot.plot_success_vs_ebn0(
        curve_data, origin=ORIGIN, n_samples=n_samples,
        out_dir=FIG_DIR, fname_prefix="ebno_decode_success",
        title="Decode success vs Eb/N0")
    print(f"[写] {fig_path}")

    # --- manifest ---
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"trials": args.trials,
                "ebn0_grid": [float(x) for x in ebn0_grid],
                "modes": {m: {"rb": float(ebno.mode_params(m)["rb"]),
                              "b": float(ebno.mode_params(m)["b"])}
                         for m in MODES},
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        filename="manifest_ebno_decode.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（仿真）；总样本数 N={n_samples}")


if __name__ == "__main__":
    main()
