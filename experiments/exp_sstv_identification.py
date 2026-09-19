#!/usr/bin/env python3
"""SSTV 自动制式识别实验（论文 VI.F 核心创新点）。

AI 定义无线电与传统 HAM 工具集的关键区别：用户不指定模式，系统从信号
本身自动判定制式。本实验量化数据驱动时序识别器（不依赖常解错的 VIS 码）：

(1) 制式识别准确率随 SNR：合成 Martin M1 / Robot36(per-line pysstv) 与
    纯噪声三类，音频域 AWGN 后仅用同步脉宽/周期特征识别，统计判对率与
    噪声虚警率。识别只取音频前缀（含足够同步），不做整图解码。

(2) Robot36 图像恢复质量随 SNR：320x240 参考图编码 -> AWGN -> auto 解码
    -> 逐通道 Pearson 相关 / 像素 RMSE / 解出行数。

(3) 真实 over-the-air 削波录音案例：记录自动判定的制式/布局/周期/行数。

固定随机种子，CSV 写入 paper/experiments/。
"""

import argparse
import csv
import json
import os
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.sstv_decoder import (  # noqa: E402
    TARGET_SAMPLE_RATE,
    _resample_if_needed,
    _instantaneous_frequency,
    _detect_vis_header,
    _identify_sstv_mode,
    decode_sstv,
)

OUT_DIR = os.path.join(ROOT, "paper", "experiments")
SR = 44100
SNR_GRID = (None, 20, 15, 10, 5, 0, -5)
PREFIX_S = 20.0  # 识别只用前 20s（含足够同步脉冲）


def _channel_corr(a, b):
    a = a.astype(np.float64) - a.mean()
    b = b.astype(np.float64) - b.mean()
    den = np.sqrt(np.sum(a * a) * np.sum(b * b))
    return float(np.sum(a * b) / den) if den > 1e-9 else 0.0


def _encode(mode, image):
    from pysstv.color import MartinM1, Robot36
    cls = MartinM1 if mode == "Martin M1" else Robot36
    enc = cls(image, SR, 16)
    raw = np.fromiter(enc.gen_samples(), dtype=np.int16).astype(np.float64) / 32768.0
    return raw


def _identify(audio, sr=SR):
    """音频 -> 重采样 -> 瞬时频率 -> VIS/数据起点 -> 时序识别制式。"""
    y = _resample_if_needed(audio.astype(np.float64), sr)
    freq = _instantaneous_frequency(y, TARGET_SAMPLE_RATE)
    vis, ds = _detect_vis_header(freq, TARGET_SAMPLE_RATE)
    name, info = _identify_sstv_mode(freq, TARGET_SAMPLE_RATE, ds, vis)
    return name, info


def run_identification(trials, seed):
    rng = np.random.default_rng(seed)
    # 两类参考图（Martin 320x256 / Robot 320x240）
    ref_m = Image.fromarray(np.full((256, 320, 3), 120, np.uint8), "RGB")
    ref_r = np.zeros((240, 320, 3), np.uint8)
    xx = np.arange(320)
    ref_r[:, :, 0] = (xx * 255 // 319).astype(np.uint8)
    ref_r[:, :, 1] = 128
    ref_r[:, :, 2] = (255 - xx * 255 // 319).astype(np.uint8)
    print("[ID] 编码 Martin M1 / Robot36 ...", flush=True)
    clean = {"Martin M1": _encode("Martin M1", ref_m),
             "Robot 36": _encode("Robot 36", Image.fromarray(ref_r, "RGB"))}
    rms = {k: float(np.sqrt(np.mean(v ** 2))) for k, v in clean.items()}
    cut = int(PREFIX_S * SR)
    rows = []
    for snr in SNR_GRID:
        for true_mode in ("Martin M1", "Robot 36"):
            base = clean[true_mode][:cut]
            preds = {}
            correct = 0
            for t in range(trials):
                if snr is None:
                    y = base.copy()
                else:
                    n = rng.standard_normal(len(base))
                    y = base + rms[true_mode] * (10 ** (-snr / 20.0)) * n
                try:
                    name, _ = _identify(np.clip(y, -1, 1))
                except Exception:
                    name = "error"
                preds[name] = preds.get(name, 0) + 1
                if name == true_mode:
                    correct += 1
            rows.append({
                "snr_db": "clean" if snr is None else snr,
                "true_mode": true_mode,
                "n_trials": trials,
                "correct_rate": round(correct / trials, 4),
                "predictions": json.dumps(preds, ensure_ascii=False),
            })
        # 纯噪声虚警：不应稳定判成任一具体 SSTV 制式（这里把"判出具体模式"记为误报）
        fa = 0
        for t in range(trials):
            n = rng.standard_normal(cut) * 0.1
            try:
                name, _ = _identify(np.clip(n, -1, 1))
            except Exception:
                name = "error"
            if name in ("Martin M1", "Robot 36", "Martin M2",
                        "Scottie S1", "Scottie S2"):
                fa += 1
        rows.append({
            "snr_db": "clean" if snr is None else snr,
            "true_mode": "noise",
            "n_trials": trials,
            "correct_rate": round(1 - fa / trials, 4),  # 正确拒绝率
            "predictions": json.dumps({"false_alarm": fa}, ensure_ascii=False),
        })
        mm = [r for r in rows if r["snr_db"] == rows[-1]["snr_db"]]
        print(f"[ID] SNR={rows[-1]['snr_db']:>5} " +
              " ".join(f"{r['true_mode']}={r['correct_rate']:.2f}" for r in mm),
              flush=True)
    return rows


def run_robot36_quality(trials, seed):
    rng = np.random.default_rng(seed + 1)
    h, w = 240, 320
    yy, xx = np.mgrid[0:h, 0:w]
    ref = np.zeros((h, w, 3), np.uint8)
    ref[..., 0] = (xx * 255 // (w - 1)).astype(np.uint8)
    ref[..., 1] = (yy * 255 // (h - 1)).astype(np.uint8)
    ref[..., 2] = ((((xx // 24) + (yy // 24)) % 2) * 255).astype(np.uint8)
    print("[R36] 编码 Robot36 参考图 ...", flush=True)
    clean = _encode("Robot 36", Image.fromarray(ref, "RGB"))
    rms = float(np.sqrt(np.mean(clean ** 2)))
    tmpdir = tempfile.mkdtemp(prefix="mbdsdr_r36_")
    rows = []
    for snr in SNR_GRID:
        corrs = [[], [], []]
        rmses, rowfrac, succ, idok = [], [], 0, 0
        for t in range(trials):
            if snr is None:
                y = clean.copy()
            else:
                n = rng.standard_normal(len(clean))
                y = clean + rms * (10 ** (-snr / 20.0)) * n
            out = os.path.join(tmpdir, f"o_{snr}_{t}.png")
            try:
                r = decode_sstv_from_samples_auto(np.clip(y, -1, 1), SR, out)
                if not r.get("success"):
                    continue
                succ += 1
                if r.get("mode") == "Robot 36":
                    idok += 1
                dec = np.asarray(Image.open(out).convert("RGB"))
                nr = min(dec.shape[0], h)
                d, g = dec[:nr].astype(np.float64), ref[:nr].astype(np.float64)
                for c in range(3):
                    corrs[c].append(_channel_corr(g[..., c], d[..., c]))
                rmses.append(float(np.sqrt(np.mean((d - g) ** 2))))
                rowfrac.append(r.get("rows_decoded", nr) / float(h))
            except Exception:
                continue
        rows.append({
            "snr_db": "clean" if snr is None else snr,
            "n_trials": trials,
            "success_rate": round(succ / trials, 4),
            "auto_id_rate": round(idok / trials, 4),
            "row_fraction": round(float(np.mean(rowfrac)), 4) if rowfrac else 0.0,
            "corr_r": round(float(np.mean(corrs[0])), 4) if corrs[0] else 0.0,
            "corr_g": round(float(np.mean(corrs[1])), 4) if corrs[1] else 0.0,
            "corr_b": round(float(np.mean(corrs[2])), 4) if corrs[2] else 0.0,
            "corr_mean": round(float(np.mean([np.mean(c) for c in corrs if c])), 4)
                         if any(corrs) else 0.0,
            "pixel_rmse": round(float(np.mean(rmses)), 2) if rmses else 999.0,
        })
        print(f"[R36] SNR={rows[-1]['snr_db']:>5} succ={rows[-1]['success_rate']:.2f} "
              f"id={rows[-1]['auto_id_rate']:.2f} corr={rows[-1]['corr_mean']:.3f} "
              f"rmse={rows[-1]['pixel_rmse']:.1f}", flush=True)
    return rows


def decode_sstv_from_samples_auto(y, sr, out_png):
    """写临时 wav 后走 auto 解码（与产品工具同路径）。"""
    import wave
    wav = out_png + ".wav"
    pcm = np.clip(y, -1, 1)
    pcm = (pcm * 32767).astype(np.int16)
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return decode_sstv(wav, out_png, "auto")


def run_real_ota(real_wav):
    if not os.path.exists(real_wav):
        print(f"[OTA] 未找到真实录音 {real_wav}，跳过", flush=True)
        return None
    out = os.path.join(OUT_DIR, "sstv_real_ota_decoded.png")
    r = decode_sstv(real_wav, out, "auto")
    rec = {k: r.get(k) for k in
           ("success", "mode", "layout", "period_ms", "pulse_ms",
            "rows_decoded", "cb_dc", "cr_dc", "identification")}
    rec["input"] = os.path.basename(real_wav)
    print(f"[OTA] 真实录音: {json.dumps(rec, ensure_ascii=False)}", flush=True)
    return rec


def _write_csv(name, rows, fields):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)
    print(f"写入 {path}（{len(rows)} 行）", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id-trials", type=int, default=8)
    ap.add_argument("--dec-trials", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--real-wav", default=os.path.join(ROOT, "real_sstv.wav"))
    ap.add_argument("--skip-decode", action="store_true")
    args = ap.parse_args()

    id_rows = run_identification(args.id_trials, args.seed)
    _write_csv("sstv_mode_identification.csv", id_rows,
               ["snr_db", "true_mode", "n_trials", "correct_rate", "predictions"])

    if not args.skip_decode:
        q_rows = run_robot36_quality(args.dec_trials, args.seed)
        _write_csv("sstv_robot36_quality.csv", q_rows,
                   ["snr_db", "n_trials", "success_rate", "auto_id_rate",
                    "row_fraction", "corr_r", "corr_g", "corr_b",
                    "corr_mean", "pixel_rmse"])

    ota = run_real_ota(args.real_wav)
    if ota:
        with open(os.path.join(OUT_DIR, "sstv_real_ota.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ota, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
