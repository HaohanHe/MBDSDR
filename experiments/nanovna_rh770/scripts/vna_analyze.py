"""SOLT 后端校准 + RH770 S11 分析。
用法: python vna_analyze.py
读 vna_data/{open,short,load,rh770}.json -> 写 vna_data/rh770_calibrated.json + 打印表
"""
import sys, json, math
import numpy as np
sys.path.insert(0, "../..")
from mbdsdr_ai.vna_cal import solve_error_box, apply_calibration

D = "data"
KEY_FREQS = [137_000_000, 145_800_000, 162_400_000, 435_000_000]

def load(label):
    with open(f"{D}/{label}.json", encoding="utf-8") as f:
        return json.load(f)

def seg_of(d):
    return {s["start_hz"]: s for s in d["segments"]}

def complex_arr(s):
    return np.array([complex(*v) for v in s["s11"]])

def nearest(freqs, target):
    return int(np.argmin(np.abs(np.asarray(freqs) - target)))

def rl_db(gamma):
    g = abs(gamma)
    if g <= 0:
        return math.inf
    if g >= 1:
        return 0.0
    return -20.0 * math.log10(g)

def vswr(gamma):
    g = abs(gamma)
    if g >= 1:
        return math.inf
    return (1 + g) / (1 - g)

# ---- 采三标准 + 天线 ----
op, sh, lo, an = load("open"), load("short"), load("load"), load("rh770")
segs = {k: seg_of(d) for k, d in (("open", op), ("short", sh), ("load", lo), ("rh770", an))}

report = {"antenna": "RH770", "cal": "SOLT 理想定义件(后端数学校准)",
          "segments": []}
print("=" * 74)
print("标准件自检（原始读数 |S11|，应分别 ≈1 / ≈1 / ≈0；若固件旧校准生效则更接近理想）")
for name, d in (("open", op), ("short", sh), ("load", lo)):
    for start, s in seg_of(d)[name].items():
        m = np.abs(complex_arr(s))
        print(f"  {name:6s} {start/1e6:5.0f}-{s['stop_hz']/1e6:5.0f}MHz  "
              f"|S11| min={m.min():.4f} max={m.max():.4f} mean={m.mean():.4f}  扫次={s['sweeps_ok']}")
print("=" * 74)

for start, seg in seg_of(an)["rh770"].items():
    freqs = seg["freqs_hz"]
    so = seg_of(an)["open"][start]
    ss = seg_of(an)["short"][start]
    sl = seg_of(an)["load"][start]
    mo, ms, ml = complex_arr(so), complex_arr(ss), complex_arr(sl)
    # 理想定义件
    go = np.ones_like(freqs, dtype=np.complex128)
    gs = -np.ones_like(freqs, dtype=np.complex128)
    gl = np.zeros_like(freqs, dtype=np.complex128)
    box = solve_error_box(mo, ms, ml, go, gs, gl)
    print(f"段 {start/1e6:.0f}-{seg['stop_hz']/1e6:.0f}MHz 误差盒: "
          f"|e00|均值={np.abs(box.e00).mean():.4f} |t|均值={np.abs(box.t).mean():.4f} "
          f"|e11|均值={np.abs(box.e11).mean():.4f}")
    ga = apply_calibration(complex_arr(seg), box)
    seg_out = {"start_hz": start, "stop_hz": seg["stop_hz"],
               "freqs_hz": freqs,
               "s11_cal": [[v.real, v.imag] for v in ga]}
    report["segments"].append(seg_out)
    print(f"  {'freq(MHz)':>10} {'|S11|':>8} {'RL(dB)':>8} {'VSWR':>7}   判定")
    for f0 in KEY_FREQS:
        if not (start <= f0 <= seg["stop_hz"]):
            continue
        i = nearest(freqs, f0)
        g = ga[i]
        rl, sw = rl_db(g), vswr(g)
        verdict = "优" if sw < 1.5 else ("可用" if sw < 2.0 else "差")
        print(f"  {freqs[i]/1e6:10.3f} {abs(g):8.4f} {rl:8.2f} {sw:7.2f}   {verdict}")
    # 段内最差点
    rls = np.array([rl_db(g) for g in ga])
    iw = int(np.argmin(rls))
    print(f"  段内最深谐振点: {freqs[iw]/1e6:.2f}MHz  RL={rls[iw]:.1f}dB  VSWR={vswr(ga[iw]):.2f}")

with open(f"{D}/rh770_calibrated.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False)
print("SAVED:", f"{D}/rh770_calibrated.json")
