#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：真机录制回填（recorded 口径）一键出成果
=================================================

把 ``tools/onboarding/onboard.py``（或 cpp recorder）产出的一段录制目录
（标准 SigMF：``*.sigmf-data``(cf32_le) + ``*.sigmf-meta``，外加 onboard
写的 ``*_messages.json`` 解码转储）喂给本脚本，即可得到 **recorded 口径**的
指标表（CSV）+ 图 + manifest，直接进论文 Fig.1-10 体系：

  - 解码成功率 vs（估算）Eb/N0：对 ADS-B 录制做贪心 preamble 扫描，数出
    候选前导数 n 与 CRC-24 校验通过帧数 k -> 帧级成功率 k/n + Wilson 95% CI
    （沿用 common/runner.BinomialCell）。
  - 带内 SNR 估计：从录制 PSD 的"峰/中位数"比粗估（**未标定**），再按
    common/ebno.py 的 ``(Eb/N0)=SNR + 10log10(B/Rb)``（B=录制 fs，Rb 已知）
    折算成 Eb/N0 估计。**绝对值不可信**：RTL-SDR 增益/衰减未知，必须真机用
    已知信号源标定后才能当真值；无法估算时如实标 ``需真机 SNR 校准``。
  - AMR 识别：对录制 IQ 跑开箱 KNN-AMR，只报**预测类 + 置信度**。真实录制
    无地面真值标签，**不报准确率**（accuracy=N/A_no_ground_truth_labels）。
  - 定轨：跑 orbit_determination.extract_doppler_observations，报有效多普勒
    观测数；真正的"收敛到 X km"需要多过境 + TLE 真值，单段录制只给观测数，
    不编造收敛误差。

红线（与全仓一致）：
  * 数字**只来自注入的录制文件**，本脚本绝不合成信号、绝不补 OTA 格。
  * 口径恒为 ``recorded``（manifest/CSV/图都标），任何路径下都不写成 synthetic。
  * 无录制目录 / 录制里没有任何 .sigmf-data -> 诚实空态 N=0，退出码 0，
    只写一份空态 manifest，不产图/CSV。

用法：
  python3 experiments/exp_ota_run.py --recordings-dir <onboard 产物目录>
  python3 experiments/exp_ota_run.py --recording <xxx.sigmf-data>
  # 云内（无录制）跑 -> 空态：
  python3 experiments/exp_ota_run.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments.common import datasource, ebno, manifest, runner  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "recorded"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")

# onboard mode -> 信息比特率 Rb（bit/s）。只有比特流、且我们知道 Rb 的模式才
# 折算 Eb/N0；CW/APT 等非比特流口径不报（与 common/ebno.py 红线一致）。
# 注意：这里 B 取录制采样率 fs（保守，含带外噪声），不照搬 ebno.MODE_TABLE 里
# 绑死某实验 fs 的 B——真机录制 fs 以 SigMF meta 为准。
MODE_RB_BPS: Dict[str, float] = {
    "adsb": 1_000_000.0,   # ADS-B Mode-S 1 Mbps
}

# 单段录制贪心扫帧的上限（防御超长录制；ADS-B 0.1s@2.4MS/s 本就几十帧量级）
MAX_SCAN_FRAMES = 300
# 读入内存的样本上限（防超长录制爆内存；真实指标在截断前的样本上算，如实标注）
MAX_IQ_SAMPLES = 8_000_000


# ---------------------------------------------------------------------------
# 单段录制：只读元数据 + 估计 SNR
# ---------------------------------------------------------------------------
def _estimate_snr_db(iq: np.ndarray, fs: float) -> Dict[str, Any]:
    """从录制 PSD 粗估带内 SNR（峰/中位数比，未标定）。

    返回 dict：
      snr_estimate_db : 估计值（dB）；无法估计时为 None
      method         : 方法名（写进 CSV/manifest 自证）
      calibrated     : 恒 False（真机增益/衰减未标定）
    这是**相对谱峰检测 SNR 代理**，不是标定后的接收 SNR；绝对值需真机已知
    信号源校准。
    """
    out = {"snr_estimate_db": None,
           "method": "psd_peak_to_median_uncalibrated",
           "calibrated": False}
    if iq.size < 1024 or fs <= 0:
        return out
    nfft = 1024
    nseg = max(1, min(16, iq.size // nfft))
    spec = np.zeros(nfft, dtype=np.float64)
    for k in range(nseg):
        seg = iq[k * nfft:(k + 1) * nfft]
        if seg.size < nfft:
            break
        S = np.fft.fftshift(np.fft.fft(seg))
        spec += np.abs(S) ** 2
    spec /= nseg
    noise_floor = float(np.median(spec))
    peak = float(np.max(spec))
    if noise_floor <= 0:
        return out
    out["snr_estimate_db"] = float(round(10.0 * np.log10(peak / noise_floor), 2))
    return out


# ---------------------------------------------------------------------------
# ADS-B 贪心多帧扫描（复用真实前导检测，数候选 vs CRC 通过）
# ---------------------------------------------------------------------------
def _scan_adsb_frames(iq: np.ndarray, fs: float) -> Dict[str, int]:
    """贪心找前导 -> 解码 -> 掩码 -> 再找，统计候选数与 CRC 通过数。

    复用 mbdsdr_ai.adsb 的真实 ``_find_preamble`` + ``decode_baseband``，
    不重写判决逻辑。返回 {"candidates": n, "crc_ok": k}。
    """
    from mbdsdr_ai import adsb
    work = np.abs(iq) ** 2
    sps = int(round(fs / 1e6))
    candidates = 0
    crc_ok = 0
    # 一帧（前导 8us + 长帧 112us）+ 保护窗，样本数
    frame_len = int((8 + 120) * sps)
    for _ in range(MAX_SCAN_FRAMES):
        k = adsb._find_preamble(work, sps)
        if k is None:
            break
        candidates += 1
        seg = iq[k:k + frame_len]
        if seg.size >= frame_len // 2:
            try:
                res = adsb.decode_baseband(seg, fs)
            except Exception:  # noqa: BLE001 - 单帧解码失败不拖垮整批
                res = {"crc_ok": False}
            if res.get("crc_ok"):
                crc_ok += 1
        # 掩码掉这一帧区域，让检测器去找下一个前导
        end = min(work.size, k + frame_len)
        work[k:end] = 0.0
    return {"candidates": candidates, "crc_ok": crc_ok}


def _load_onboard_messages(recordings_dir: str) -> Dict[str, Any]:
    """扫描录制目录里 onboard 写的 ``*_messages.json``，汇总已解码帧数。

    这是 onboard decode 步的既有产物（CRC/FCS 已校验）。只读、不重算真值。
    返回 {"n_onboard_frames": int, "messages_files": [...]}。
    """
    out: Dict[str, Any] = {"n_onboard_frames": 0, "messages_files": []}
    if not recordings_dir or not os.path.isdir(recordings_dir):
        return out
    for name in sorted(os.listdir(recordings_dir)):
        if not (name.endswith("_messages.json") or name.endswith("messages.json")):
            continue
        p = os.path.join(recordings_dir, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        frames = obj.get("frames", []) if isinstance(obj, dict) else []
        if isinstance(frames, list):
            out["n_onboard_frames"] += len(frames)
            out["messages_files"].append(p)
    return out


# ---------------------------------------------------------------------------
# 单段录制 -> 一行指标
# ---------------------------------------------------------------------------
def measure_recording(data_path: str, recordings_dir: str,
                      run_amr: bool = True, run_doppler: bool = True) -> Dict[str, Any]:
    from mbdsdr_ai import playback as pb_mod

    meta_path = data_path[: -len(".sigmf-data")] + ".sigmf-meta" \
        if data_path.endswith(".sigmf-data") else data_path
    meta = pb_mod.parse_sigmf_meta(meta_path)
    raw = meta.get("raw", {}) or {}
    g = raw.get("global", {}) or {}
    caps = raw.get("captures", []) or [{}]
    cap0 = caps[0] if caps else {}

    mode = str(cap0.get("mbdsdr:mode", g.get("mbdsdr:mode", "")) or "")
    gain_db = cap0.get("mbdsdr:gain_db", g.get("mbdsdr:gain_db"))
    fs = float(meta["sample_rate_hz"])
    f0 = float(meta["center_freq_hz"])

    row: Dict[str, Any] = {
        "data_origin": ORIGIN,
        "recording_file": os.path.basename(data_path),
        "mode": mode,
        "fs_hz": round(fs, 1),
        "center_freq_hz": round(f0, 1),
        "datetime": meta["datetime"],
        "time_source": meta["time_source"],
        "gain_db": gain_db,
        "n_iq_samples": 0,
        "duration_s": 0.0,
        "snr_estimate_db": None,
        "snr_method": "",
        "snr_calibrated": False,
        "snr_calibration_note": "",
        "ebn0_db_estimate": None,
        "n_candidates": 0,
        "n_crc_ok": 0,
        "success_rate": "",
        "wilson_lo": "",
        "wilson_hi": "",
        "n_onboard_frames": "",
        "amr_predicted": "",
        "amr_confidence": "",
        "amr_accuracy": "N/A_no_ground_truth_labels",
        "n_doppler_observations": "",
    }

    # ---- 读 IQ（截断保护）----
    try:
        iq = np.fromfile(data_path, dtype=np.complex64)
    except OSError as e:
        row["note"] = f"读取 IQ 失败: {e}"
        return row
    truncated = False
    if iq.size > MAX_IQ_SAMPLES:
        iq = iq[:MAX_IQ_SAMPLES]
        truncated = True
    row["n_iq_samples"] = int(iq.size)
    row["duration_s"] = round(iq.size / fs, 4) if fs > 0 else 0.0
    if truncated:
        row["note"] = f"IQ 超过 {MAX_IQ_SAMPLES} 样本，截断到此上限（如实）"

    # ---- SNR 估计 + Eb/N0 ----
    snr = _estimate_snr_db(iq, fs)
    row["snr_method"] = snr["method"]
    row["snr_calibrated"] = bool(snr["calibrated"])
    row["snr_estimate_db"] = snr["snr_estimate_db"]
    if snr["snr_estimate_db"] is None:
        row["snr_calibration_note"] = "无法估计带内 SNR（样本过少或 fs 未知）；需真机 SNR 校准"
    else:
        row["snr_calibration_note"] = ("峰/中位数粗估、未标定；绝对值需真机已知信号源校准"
                                       "（增益/衰减未知）")
    if mode in MODE_RB_BPS and snr["snr_estimate_db"] is not None and fs > 0:
        rb = MODE_RB_BPS[mode]
        try:
            row["ebn0_db_estimate"] = round(
                ebno.snr_db_to_ebn0_db(snr["snr_estimate_db"], rb, fs), 2)
        except (ValueError, KeyError):
            row["ebn0_db_estimate"] = None
    elif mode not in MODE_RB_BPS:
        row["snr_calibration_note"] += f"；模式 '{mode}' 非比特流/无已知 Rb，不报 Eb/N0"

    # ---- 解码成功率（仅 ADS-B 有候选前导二项）----
    if mode == "adsb" and fs > 0 and iq.size > 0:
        scan = _scan_adsb_frames(iq, fs)
        row["n_candidates"] = scan["candidates"]
        row["n_crc_ok"] = scan["crc_ok"]
        cell = runner.BinomialCell(scan["crc_ok"], scan["candidates"])
        if scan["candidates"] > 0:
            lo, hi = cell.wilson
            row["success_rate"] = round(cell.rate, 4)
            row["wilson_lo"] = round(lo, 4)
            row["wilson_hi"] = round(hi, 4)

    # ---- onboard 既有解码帧数（交叉核对，只读）----
    ob = _load_onboard_messages(recordings_dir)
    row["n_onboard_frames"] = ob["n_onboard_frames"]

    # ---- AMR：只报预测 + 置信度（无真值标签，不报准确率）----
    if run_amr and iq.size >= 1024:
        try:
            from mbdsdr_ai.amr import AMRClassifier
            clf = AMRClassifier(k=5)
            res = clf.classify_iq(iq.tolist(), fs if fs > 0 else 1.0)
            row["amr_predicted"] = str(res.predicted_modulation.value)
            row["amr_confidence"] = round(float(res.confidence), 4)
        except Exception as e:  # noqa: BLE001
            row["amr_predicted"] = "ERROR"
            row["amr_confidence"] = f"amr 分类失败: {type(e).__name__}"

    # ---- 定轨：只报多普勒观测数（收敛需多过境+TLE，不编造）----
    if run_doppler and fs > 0 and iq.size > 0:
        try:
            from mbdsdr_ai import orbit_determination as od
            obs = od.extract_doppler_observations(
                iq, fs, f0=f0 if f0 > 0 else od.F0_DEFAULT_HZ)
            row["n_doppler_observations"] = len(obs)
        except Exception as e:  # noqa: BLE001
            row["n_doppler_observations"] = f"doppler 失败: {type(e).__name__}"

    return row


# ---------------------------------------------------------------------------
# 空态
# ---------------------------------------------------------------------------
def _write_empty_state(out: str, seed: int, recording: Optional[str],
                       recordings_dir: Optional[str]) -> int:
    mpath = manifest.write_manifest(
        out_dir=out, script=__file__, seed=seed, data_origin=ORIGIN,
        params={"recording": recording, "recordings_dir": recordings_dir,
                "status": "empty_state_no_recording"},
        n_samples=0,
        extra={"note": "未提供任何 .sigmf-data 录制；recorded 口径为空态 N=0，"
                       "不合成、不补 OTA 格",
               "how_to": "把 onboard 产出的录制目录 --recordings-dir 传入即可出图/CSV"},
        filename="manifest_ota_run.json")
    print(f"[空态] n_samples=0（data_origin={ORIGIN}）；不产出 recorded 图/CSV。")
    print(f"[写] {mpath}")
    return 0


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="真机录制回填：recorded 口径指标一键出成果")
    ap.add_argument("--recording", default=None, help="单个 .sigmf-data 录制文件")
    ap.add_argument("--recordings-dir", default=None,
                    help="onboard 产物目录（含 .sigmf-data + *_messages.json）")
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--no-amr", action="store_true", help="跳过 AMR 分类")
    ap.add_argument("--no-doppler", action="store_true", help="跳过多普勒观测提取")
    args = ap.parse_args(argv)

    # 收集录制文件（绝不合成）
    files: List[str] = []
    if args.recording:
        if not os.path.exists(args.recording):
            print(f"[空态] 指定录制不存在: {args.recording}")
            return _write_empty_state(args.out, args.seed, args.recording,
                                      args.recordings_dir)
        files = [args.recording]
    elif args.recordings_dir:
        files = datasource.find_recordings(args.recordings_dir)

    if not files:
        print("[OTA 回填] 未发现任何 .sigmf-data 录制 -> recorded 口径空态。")
        return _write_empty_state(args.out, args.seed, args.recording,
                                  args.recordings_dir)

    print(f"[OTA 回填] 发现 {len(files)} 段录制（口径={ORIGIN}）")
    rows: List[Dict[str, Any]] = []
    for fp in files:
        print(f"  - 处理 {fp}")
        row = measure_recording(fp, args.recordings_dir or os.path.dirname(fp),
                                run_amr=not args.no_amr,
                                run_doppler=not args.no_doppler)
        rows.append(row)
        print(f"    mode={row['mode']} fs={row['fs_hz']:.0f} "
              f"S/N≈{row['snr_estimate_db']}dB(Eb/N0≈{row['ebn0_db_estimate']}) "
              f"crc={row['n_crc_ok']}/{row['n_candidates']} "
              f"amr={row['amr_predicted']} doppler={row['n_doppler_observations']}")

    os.makedirs(args.out, exist_ok=True)

    # ---- CSV（逐录制一行；数字全部来自录制）----
    csv_path = os.path.join(args.out, "ota_recorded_metrics.csv")
    fields = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"[写] {csv_path}（{len(rows)} 行，口径={ORIGIN}）")

    # ---- 图：解码成功率 vs 估算 Eb/N0（仅用能估出 Eb/N0 的点）----
    n_total_candidates = sum(int(r["n_candidates"]) for r in rows)
    n_total_crc = sum(int(r["n_crc_ok"]) for r in rows)
    fig_path: Optional[str] = None
    xs, rates, los, his = [], [], [], []
    for r in rows:
        if r["ebn0_db_estimate"] is None or int(r["n_candidates"]) <= 0:
            continue
        xs.append(float(r["ebn0_db_estimate"]))
        rates.append(float(r["success_rate"]))
        los.append(float(r["wilson_lo"]))
        his.append(float(r["wilson_hi"]))
    if xs:
        fig_dir = os.path.join(args.out, "figures")
        fig_path = eplot.plot_success_vs_ebn0(
            {"recorded ADS-B": {"x": xs, "rate": rates, "lo": los, "hi": his}},
            origin=ORIGIN, n_samples=n_total_candidates, out_dir=fig_dir,
            fname_prefix="ota_recorded_success_vs_ebn0",
            xlabel="Estimated Eb/N0 (dB, uncalibrated)",
            ylabel="ADS-B frame decode success (CRC-pass / preambles)",
            title="Recorded OTA: decode success vs ESTIMATED Eb/N0 "
                  "(uncalibrated; needs real-machine SNR calibration)")
        print(f"[写] {fig_path}")
    else:
        print("[图] 无任何可估算 Eb/N0 的录制点 -> 不出 Eb/N0 曲线"
              "（需真机 SNR 校准）；CSV 仍落盘。")

    # ---- manifest ----
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"recordings_dir": args.recordings_dir,
                "n_recordings": len(files),
                "snr_estimate_method": "psd_peak_to_median_uncalibrated",
                "snr_calibration": "需真机已知信号源标定（增益/衰减未知）",
                "ebn0_convention": "Eb/N0 = SNR_est + 10log10(fs/Rb), B=录制fs (common/ebno.py)",
                "amr_accuracy": "N/A_no_ground_truth_labels（真实录制无标签）",
                "orbit": "只报多普勒观测数；收敛需多过境+TLE真值，未编造误差",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path) if fig_path else None},
        n_samples=n_total_candidates,
        extra={"per_recording": [
            {"file": r["recording_file"], "mode": r["mode"],
             "n_iq_samples": r["n_iq_samples"],
             "snr_estimate_db": r["snr_estimate_db"],
             "ebn0_db_estimate": r["ebn0_db_estimate"],
             "crc_ok": r["n_crc_ok"], "candidates": r["n_candidates"],
             "amr_predicted": r["amr_predicted"]} for r in rows]},
        filename="manifest_ota_run.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}；总前导候选 N={n_total_candidates}，"
          f"CRC 通过 {n_total_crc}；数字全部来自注入录制，未合成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
