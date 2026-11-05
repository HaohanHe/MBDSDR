# SPDX-License-Identifier: MIT
"""R2 频谱与瀑布 —— 噪声底 / 信噪比 / 频轴对齐 / 瀑布帧稳定性。

判据（全部自动断言）：
  * 32 帧频谱构成瀑布矩阵，形状一致、无 NaN
  * 已知强台（自动扫 88–108 MHz 选最强）峰值高出噪声底 >= 10 dB
  * 峰值质心与调谐中心偏差 <= 40 kHz
  * 32 帧噪声底极差 <= 6 dB（无增益跳变/丢帧）
人眼项：瀑布滚动是否顺滑、配色是否可读（观感项）。
"""
from __future__ import annotations

import time

import numpy as np

import common

FS = 2.4e6
NFFT = 1024
BIN_HZ = FS / NFFT


def _shift(db: np.ndarray) -> np.ndarray:
    return np.fft.fftshift(db)


def _floor(db: np.ndarray) -> float:
    return float(np.median(db))


def _centroid_off(db: np.ndarray, span_bins: int) -> float | None:
    db = _shift(db)
    c = len(db) // 2
    win = db[c - span_bins:c + span_bins]
    thr = float(np.max(win)) - 6.0
    idx = np.where(win >= thr)[0]
    if len(idx) == 0:
        return None
    w = win[idx] - thr + 1e-6
    return float(np.average(idx, weights=w) - span_bins) * BIN_HZ


def _find_station(ctrl) -> tuple[float, float]:
    """扫 89–107 MHz 找最强台；返回 (频率 Hz, 峰底比 dB)。"""
    return common.find_strong_fm(ctrl, NFFT, FS)


def run(freq_hz: float) -> dict:
    out = common.artifact_dir("r2_spectrum_waterfall")
    from mbdsdr_ai.core import SDRController

    ctrl = SDRController()
    ctrl.connect(f"rtlsdr:{common.real_devices()[0].serial}")
    try:
        ctrl.set_sample_rate(FS)
        found_f, scan_pf = _find_station(ctrl)
        target = freq_hz or found_f
        if not target:
            return common.result("R2", "频谱与瀑布", common.FAIL,
                                 {"scan_best_peak_floor_db": round(scan_pf, 1)},
                                 [common.check("station_found", False, "88–108 MHz 无显著台")],
                                 common.USER_MSG["no_signal"], ["瀑布视觉"], True)
        ctrl.set_frequency(target)
        time.sleep(0.25)

        frames = []
        for _ in range(32):
            db = ctrl.read_spectrum(NFFT)
            if db is None:
                continue
            frames.append(np.asarray(db, dtype=np.float64))
            time.sleep(0.02)
        checks = [common.check("waterfall_frames", len(frames) == 32, f"{len(frames)}/32 帧")]
        if not frames:
            return common.result("R2", "频谱与瀑布", common.FAIL,
                                 {"target_hz": target}, checks,
                                 common.USER_MSG["no_signal"], ["瀑布视觉"], True)
        mat = np.stack(frames)
        nan_count = int(np.isnan(mat).sum())
        checks.append(common.check("no_nan", nan_count == 0, f"NaN={nan_count}"))
        toggled = np.stack([_shift(f) for f in frames])
        floor_db = _floor(toggled)
        c = NFFT // 2
        edge = int(300e3 / BIN_HZ)
        center_win = toggled[:, c - edge:c + edge]
        peak = float(np.max(center_win))
        snr = peak - floor_db
        offsets = [o for o in (_centroid_off(f, edge) for f in frames) if o is not None]
        off_hz = float(np.median(offsets)) if offsets else float("nan")
        frame_floors = [float(np.median(f)) for f in toggled]
        drift = float(np.max(frame_floors) - np.min(frame_floors))

        checks += [
            common.check("snr_ge_10db", snr >= 10.0, f"峰值高出噪声底 {snr:.1f} dB"),
            common.check("axis_offset_le_40k", abs(off_hz) <= 40e3,
                         f"质心峰偏 {off_hz/1e3:+.1f} kHz"),
            common.check("floor_drift_le_6db", drift <= 6.0, f"32 帧噪声底极差 {drift:.1f} dB"),
        ]
        np.savez_compressed(
            f"{out}/waterfall.npz",
            matrix_dbfs=toggled.astype(np.float32),
            center_freq_hz=np.float64(target),
            sample_rate_hz=np.float64(FS),
            nfft=np.int32(NFFT),
        )
        metrics = {
            "target_hz": round(target, 0),
            "scan_peak_floor_db": round(scan_pf, 1),
            "noise_floor_db": round(floor_db, 2),
            "peak_db": round(peak, 2),
            "snr_db": round(snr, 2),
            "axis_offset_hz": round(off_hz, 1),
            "floor_drift_db": round(drift, 2),
            "frames": len(frames),
            "nfft": NFFT,
            "bin_hz": round(BIN_HZ, 2),
        }
        status = common.PASS if all(c["ok"] for c in checks) else common.FAIL
        return common.result(
            "R2", "频谱与瀑布", status, metrics, checks, common.USER_MSG["no_signal"],
            ["瀑布滚动是否顺滑、配色可读性（人眼）"], True,
            [f"{out}/waterfall.npz"],
            "32 帧真实频谱；峰值/质心/噪声底均为实测。瀑布矩阵已存 npz 供复看。")
    finally:
        ctrl.disconnect()


if __name__ == "__main__":
    args = common.common_args("R2 频谱与瀑布")
    out = common.artifact_dir("r2_spectrum_waterfall")
    res = common.guarded("R2", "频谱与瀑布", lambda: run(args.freq))
    raise SystemExit(common.emit(res, out))
