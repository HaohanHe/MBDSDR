"""馈线分析：SOLT 校准后提取电气长度（相位斜率）+ 损耗（|S11| 幅度）。

只用干净区：段1 全段 + 段2 前 56 点（<=455MHz，435 目标在内）。

可从仓根运行，无需先 cd：
    python -m experiments.nanovna_rh770.scripts.vna_cable_analyze
    python experiments/nanovna_rh770/scripts/vna_cable_analyze.py

provenance: measured-archive（输入为入库真机采集件，见 data/ 下 SHA256 钉扎）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mbdsdr_ai.vna_cal import apply_calibration, solve_error_box

DATA = Path(__file__).resolve().parents[1] / "data"
C = 299792458.0
VF_RANGE = (0.66, 0.70)  # 实芯 PE ~0.66 / 发泡 PE ~0.85；本条按保守 0.66-0.70
CLEAN_POINTS = 56
TARGET_FREQS = (137e6, 145.8e6, 162.4e6, 435e6)


def load(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def _cab(s: dict) -> np.ndarray:
    return np.array([complex(*v) for v in s["s11"]])


def _calibrated() -> list[tuple[np.ndarray, np.ndarray]]:
    op, sh, lo, cb = load("open"), load("short"), load("load"), load("cable_open")
    so = {s["start_hz"]: s for s in op["segments"]}
    ss = {s["start_hz"]: s for s in sh["segments"]}
    sl = {s["start_hz"]: s for s in lo["segments"]}
    out = []
    for seg in cb["segments"]:
        st = seg["start_hz"]
        box = solve_error_box(
            _cab(so[st]), _cab(ss[st]), _cab(sl[st]),
            np.ones(101, complex), -np.ones(101, complex), np.zeros(101, complex),
        )
        out.append((np.array(seg["freqs_hz"], dtype=float), apply_calibration(_cab(seg), box)))
    return out


def analyze() -> dict:
    """返回馈线时延/长度/损耗；公式：τ=|s|/(4π)、Le=c·τ、L=Le·VF。"""
    segs = _calibrated()
    f1, g1 = segs[0]
    slope = np.polyfit(f1 / 1e6, np.unwrap(np.angle(g1)), 1)[0]  # rad/MHz
    tau_ns = abs(slope) / (4 * np.pi) * 1e-6 * 1e9
    le_m = C * tau_ns * 1e-9
    l_lo, l_hi = (le_m * VF_RANGE[0], le_m * VF_RANGE[1])

    loss_db = -20 * np.log10(np.abs(g1))
    f2, g2 = segs[1]
    ph2 = np.unwrap(np.angle(g2[:CLEAN_POINTS]))
    slope2 = np.polyfit(f2[:CLEAN_POINTS] / 1e6, ph2, 1)[0]
    le2_m = C * abs(slope2) / (4 * np.pi) * 1e-6

    key = []
    for f0 in TARGET_FREQS:
        for f, g in segs:
            if f[0] <= f0 <= f[-1]:
                i = int(np.argmin(np.abs(f - f0)))
                key.append({
                    "freq_hz": float(f[i]),
                    "s11": float(abs(g[i])),
                    "return_loss_db": float(-20 * np.log10(max(abs(g[i]), 1e-9))),
                    "phase_rad": float(np.angle(g[i])),
                })
                break

    return {
        "slope_rad_per_mhz": float(slope),
        "tau_ns": float(tau_ns),
        "electrical_length_m": float(le_m),
        "vf_range": VF_RANGE,
        "physical_length_m": (float(l_lo), float(l_hi)),
        "loss_db_range": (float(loss_db.min()), float(loss_db.max())),
        "slope2_rad_per_mhz": float(slope2),
        "electrical_length2_m": float(le2_m),
        "key_freqs": key,
    }


def main() -> None:
    r = analyze()
    for f, g in _calibrated():
        print(f"段 {f[0]/1e6:.0f}-{f[-1]/1e6:.0f}MHz 校准后 |S11| "
              f"{np.abs(g).min():.4f}-{np.abs(g).max():.4f}")
    print(f"\n段1 相位斜率: {r['slope_rad_per_mhz']:.4f} rad/MHz -> τ {r['tau_ns']:.3f} ns "
          f"-> 电气长度 Le {r['electrical_length_m']:.3f} m")
    print(f"  物理长度 L = Le·VF，VF={r['vf_range'][0]:.2f}-{r['vf_range'][1]:.2f} "
          f"-> {r['physical_length_m'][0]:.3f}-{r['physical_length_m'][1]:.3f} m")
    print(f"段2 干净区(400-455MHz) 相位斜率: {r['slope2_rad_per_mhz']:.4f} rad/MHz "
          f"-> 电气长度 {r['electrical_length2_m']:.3f} m (交叉验证)")
    print(f"\n段1 双程损耗: {r['loss_db_range'][0]:.2f}-{r['loss_db_range'][1]:.2f} dB")
    print("\n关键频点（开口馈线状态下的读数）:")
    for k in r["key_freqs"]:
        print(f"  {k['freq_hz']/1e6:7.1f}MHz  |S11|={k['s11']:.4f}  "
              f"回损={k['return_loss_db']:.2f}dB  ph={k['phase_rad']:+.3f}")


if __name__ == "__main__":
    main()
