#!/usr/bin/env python3
"""数字模式解码纯软件闭环实验（论文 VI.F）。

不依赖 dump1090 / wsjtx / RTL-SDR，全部用产品代码在合成基带/音频上闭环：

(1) SSTV（Martin M1）：pysstv 编码一张固定彩色参考图 -> 音频域 AWGN ->
    内置 sstv_decoder 恢复图像 -> 与原图逐通道 Pearson 相关、像素 RMSE、
    行同步率，量化"会话触发的慢扫描图像解码技能"在不同信噪比下的可用性。

(2) ADS-B（Mode-S 1090ES DF17 机载识别）：随机 ICAO/航班号报文（含
    CRC-24）-> 复基带 PPM 调制（随机到达时刻）-> 复高斯噪声 ->
    adsb.decode_baseband 前导检测/位判决/CRC/航班号解码，统计检测率、
    CRC 通过率（=报文完全正确）、航班号正确率、误比特率随 SNR 变化。

(3) 纯噪声虚警：统计纯噪声窗被误判为前导、以及 CRC 误通过的概率
    （CRC 级误报理论上界约 2^-24）。

固定随机种子，trials 可调；CSV 写入 paper/experiments/。测的就是 LLM
通过 sdr_decode_sstv / sdr_decode_adsb 工具调用的同一份解码代码。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import adsb  # noqa: E402
from mbdsdr_ai.sstv_decoder import decode_sstv_from_samples  # noqa: E402

OUT_DIR = os.path.join(ROOT, "paper", "experiments")


# ---------------------------------------------------------------------------
# SSTV 图像恢复质量
# ---------------------------------------------------------------------------

def _reference_image():
    """320x256 彩色参考图：水平/垂直梯度 + 棋盘 + 圆，频率内容丰富。"""
    h, w = 256, 320
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[..., 0] = (xx * 255 // (w - 1)).astype(np.uint8)          # R 水平梯度
    img[..., 1] = (yy * 255 // (h - 1)).astype(np.uint8)          # G 垂直梯度
    block = (((xx // 32) + (yy // 32)) % 2) * 255                 # B 棋盘
    img[..., 2] = block.astype(np.uint8)
    cy, cx = 128, 160
    circle = (xx - cx) ** 2 + (yy - cy) ** 2 < 45 ** 2
    img[circle] = [255, 255, 255]
    return Image.fromarray(img, "RGB")


def _encode_sstv(image, sr=44100):
    from pysstv.color import MartinM1
    enc = MartinM1(image, sr, 16)
    raw = np.fromiter(enc.gen_samples(), dtype=np.int16).astype(np.float64)
    return raw / 32768.0


def _channel_corr(a, b):
    a = a.astype(np.float64) - a.mean()
    b = b.astype(np.float64) - b.mean()
    den = np.sqrt(np.sum(a * a) * np.sum(b * b))
    return float(np.sum(a * b) / den) if den > 1e-9 else 0.0


def run_sstv(trials, seed, sr=44100, snr_grid=(None, 20, 15, 10, 5, 0, -5)):
    rng = np.random.default_rng(seed)
    ref = _reference_image()
    ref_arr = np.asarray(ref)
    print("[SSTV] 编码 Martin M1 参考图 ...", flush=True)
    clean = _encode_sstv(ref, sr)
    rms = float(np.sqrt(np.mean(clean ** 2)))
    rows = []
    tmpdir = tempfile.mkdtemp(prefix="mbdsdr_sstv_")
    for snr in snr_grid:
        corrs = [[], [], []]
        rmses, rowfrac, succ = [], [], 0
        for t in range(trials):
            if snr is None:
                y = clean.copy()
            else:
                n = rng.standard_normal(len(clean))
                y = clean + rms * (10 ** (-snr / 20.0)) * n
            y = np.clip(y, -1.0, 1.0).astype(np.float32)
            out_png = os.path.join(tmpdir, f"o_{snr}_{t}.png")
            try:
                r = decode_sstv_from_samples(y, sr, output_path=out_png,
                                             mode="Martin M1")
                if not r.get("success"):
                    continue
                dec = np.asarray(Image.open(out_png).convert("RGB"))
                nrows = min(dec.shape[0], ref_arr.shape[0])
                d = dec[:nrows].astype(np.float64)
                g = ref_arr[:nrows].astype(np.float64)
                for c in range(3):
                    corrs[c].append(_channel_corr(g[..., c], d[..., c]))
                rmses.append(float(np.sqrt(np.mean((d - g) ** 2))))
                rowfrac.append(r.get("rows_decoded", nrows) / 256.0)
                succ += 1
            except Exception:
                continue
        row = {
            "snr_db": "clean" if snr is None else snr,
            "n_trials": trials,
            "success_rate": round(succ / trials, 4),
            "row_sync_fraction": round(float(np.mean(rowfrac)), 4) if rowfrac else 0.0,
            "corr_r": round(float(np.mean(corrs[0])), 4) if corrs[0] else 0.0,
            "corr_g": round(float(np.mean(corrs[1])), 4) if corrs[1] else 0.0,
            "corr_b": round(float(np.mean(corrs[2])), 4) if corrs[2] else 0.0,
            "corr_mean": round(float(np.mean([np.mean(c) for c in corrs if c])), 4)
                         if any(corrs) else 0.0,
            "pixel_rmse": round(float(np.mean(rmses)), 2) if rmses else 999.0,
        }
        rows.append(row)
        print(f"[SSTV] SNR={row['snr_db']:>5} success={row['success_rate']:.2f} "
              f"corr={row['corr_mean']:.3f} rmse={row['pixel_rmse']:.1f}", flush=True)
    return rows


# ---------------------------------------------------------------------------
# ADS-B Mode-S 解码
# ---------------------------------------------------------------------------

CALL_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _random_frame(rng):
    icao = f"{rng.integers(0, 0xFFFFFF):06X}"
    cs = "".join(rng.choice(list(CALL_CHARS), size=6))
    frame = adsb.build_identification_frame(icao, "".join(cs))
    txbits = adsb._bytes_to_bits(frame)
    return frame, txbits, "".join(cs)


def run_adsb(trials, seed, fs=4e6,
             snr_grid=(-4, -2, 0, 2, 4, 6, 8, 10, 12, 15)):
    rng = np.random.default_rng(seed)
    rows = []
    for snr in snr_grid:
        n_found = n_crc = n_cs = 0
        bers = []
        for _ in range(trials):
            frame, txbits, cs = _random_frame(rng)
            lead = float(rng.uniform(0, 20))
            iq = adsb.modulate_baseband(frame, fs=fs, lead_us=lead)
            iq = adsb.add_awgn(iq, snr, rng)
            d = adsb.decode_baseband(iq, fs)
            if d.get("found"):
                n_found += 1
                bers.append(adsb.ber_against(d["bits"], txbits))
                if d.get("crc_ok"):
                    n_crc += 1
                if d.get("callsign") == cs:
                    n_cs += 1
        row = {
            "snr_db": snr,
            "n_trials": trials,
            "preamble_detect_rate": round(n_found / trials, 4),
            "crc_pass_rate": round(n_crc / trials, 4),
            "callsign_correct_rate": round(n_cs / trials, 4),
            "ber_when_detected": round(float(np.mean(bers)), 5) if bers else 1.0,
        }
        rows.append(row)
        print(f"[ADS-B] SNR={snr:>3} detect={row['preamble_detect_rate']:.2f} "
              f"crc={row['crc_pass_rate']:.3f} callsign={row['callsign_correct_rate']:.3f} "
              f"BER={row['ber_when_detected']:.5f}", flush=True)
    return rows


def run_adsb_false_alarm(trials, seed, fs=4e6):
    rng = np.random.default_rng(seed + 1)
    n_found = n_crc = 0
    # 长度覆盖典型报文窗口（lead + 前导 + 112bit）。
    n_samp = int((20 + 8 + 112) * fs / 1e6)
    for _ in range(trials):
        n = (rng.standard_normal(n_samp) + 1j * rng.standard_normal(n_samp)) \
            / np.sqrt(2)
        d = adsb.decode_baseband(n, fs)
        if d.get("found"):
            n_found += 1
        if d.get("crc_ok"):
            n_crc += 1
    row = {
        "n_trials": trials,
        "preamble_false_alarm_rate": round(n_found / trials, 6),
        "crc_false_alarm_rate": round(n_crc / trials, 8),
        "crc_theory_24bit": round(2.0 ** -24, 9),
    }
    print(f"[ADS-B 虚警] 前导={row['preamble_false_alarm_rate']:.4f} "
          f"CRC={row['crc_false_alarm_rate']:.8f} (理论 2^-24="
          f"{row['crc_theory_24bit']:.2e})", flush=True)
    return row


def _write_csv(name, rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    if not rows:
        return path
    keys = list(rows[0].keys())
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(str(r[k]) for k in keys) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--sstv-trials", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260919)
    args = ap.parse_args()

    sstv_rows = run_sstv(args.sstv_trials, args.seed)
    adsb_rows = run_adsb(args.trials, args.seed)
    fa_row = run_adsb_false_alarm(max(args.trials, 500), args.seed)

    p1 = _write_csv("digital_sstv_quality.csv", sstv_rows)
    p2 = _write_csv("digital_adsb_decode.csv", adsb_rows)
    p3 = _write_csv("digital_adsb_false_alarm.csv", [fa_row])
    print("\nCSV:")
    for p in (p1, p2, p3):
        print(" ", p)

    # --- 论文级 JSON 结论输出 ---
    sstv_clean = next((r for r in sstv_rows if r["snr_db"] == "clean"), sstv_rows[0])
    adsb_best = max(adsb_rows, key=lambda r: r["crc_pass_rate"])
    result = {
        "experiment": "digital_modes_sstv_adsb",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "trials": args.trials,
            "sstv_trials": args.sstv_trials,
            "seed": args.seed,
        },
        "metrics": {
            "sstv_clean_corr_mean": sstv_clean["corr_mean"],
            "sstv_clean_success_rate": sstv_clean["success_rate"],
            "adsb_best_crc_pass_rate": adsb_best["crc_pass_rate"],
            "adsb_best_crc_pass_snr_db": adsb_best["snr_db"],
            "adsb_false_alarm_rate": fa_row["crc_false_alarm_rate"],
            "adsb_preamble_fa_rate": fa_row["preamble_false_alarm_rate"],
        },
        "samples": {
            "sstv_trials_total": args.sstv_trials * len(sstv_rows),
            "adsb_trials_total": args.trials * len(adsb_rows),
            "adsb_fa_trials": max(args.trials, 500),
        },
        "output_files": [p1, p2, p3],
    }
    print("\n=== JSON RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
