"""通用 SOLT 分析：校准任意标签的数据 + 可选梳状分析。
用法: python vna_cal_any.py <标签> [--comb]
"""
import sys, json
import numpy as np
sys.path.insert(0, "../..")
from mbdsdr_ai.vna_cal import solve_error_box, apply_calibration

D = "data"
LABEL = sys.argv[1]
COMB = "--comb" in sys.argv

def load(label):
    with open(f"{D}/{label}.json", encoding="utf-8") as f:
        return json.load(f)

def complex_arr(s):
    return np.array([complex(*v) for v in s["s11"]])

op, sh, lo = load("open"), load("short"), load("load")
an = load(LABEL)
so = {s["start_hz"]: s for s in op["segments"]}
ss = {s["start_hz"]: s for s in sh["segments"]}
sl = {s["start_hz"]: s for s in lo["segments"]}

print(f"== {LABEL} 校准后 S11 ==")
for seg in an["segments"]:
    start = seg["start_hz"]
    freqs = np.array(seg["freqs_hz"], dtype=np.float64)
    mo, ms, ml = complex_arr(so[start]), complex_arr(ss[start]), complex_arr(sl[start])
    box = solve_error_box(mo, ms, ml,
                          np.ones(101, dtype=np.complex128),
                          -np.ones(101, dtype=np.complex128),
                          np.zeros(101, dtype=np.complex128))
    ga = apply_calibration(complex_arr(seg), box)
    mag = np.abs(ga)
    print(f"段 {start/1e6:.0f}-{seg['stop_hz']/1e6:.0f}MHz: |S11| {mag.min():.4f}-{mag.max():.4f} "
          f"({20*np.log10(max(mag.max(),1e-12)):.2f}~{20*np.log10(max(mag.min(),1e-12)):.2f} dB)")
    if COMB:
        # 找 |S11| 极小值（梳状谷）
        order = np.argsort(mag)
        dips = []
        for i in order:
            if all(abs(freqs[i] - d) > 8e6 for d in dips):
                dips.append(freqs[i])
            if len(dips) >= 12:
                break
        dips = np.sort(dips)
        if len(dips) >= 2:
            sp = np.diff(dips)
            print(f"  谷频点(MHz): {[round(d/1e6,1) for d in dips]}")
            print(f"  谷间距(MHz): {[round(s/1e6,1) for s in sp]} 均值 {sp.mean()/1e6:.1f}MHz")
            le = 299792458.0 / (2 * sp.mean())
            print(f"  电气长度(按谷距 c/2Δf): {le:.3f} m")
            if le > 0:
                print(f"  若物理长1.0m -> VF≈{1.0/le:.3f}")
