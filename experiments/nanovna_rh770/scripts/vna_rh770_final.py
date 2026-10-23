"""RH770 最终分析：SOLT 校准 + 五频点回损/VSWR + 谐振峰 + 馈线损耗修正。
干净区：段1 全段；段2 index 0-55（<=455MHz）。
"""
import sys, json
import numpy as np
sys.path.insert(0, "../..")
from mbdsdr_ai.vna_cal import solve_error_box, apply_calibration

D = "data"
KEYS = [(137.0, "NOAA/气象卫星"), (145.8, "ISS/业余卫星"), (162.4, "气象卫星"),
        (435.0, "70cm 业余/ADS-B邻")]

def load(n):
    return json.load(open(f"{D}/{n}.json", encoding="utf-8"))

def ca(s):
    return np.array([complex(*v) for v in s["s11"]])

op, sh, lo = load("open"), load("short"), load("load")
an, cbl = load("rh770"), load("cable_open")
so = {s["start_hz"]: s for s in op["segments"]}
ss = {s["start_hz"]: s for s in sh["segments"]}
sl = {s["start_hz"]: s for s in lo["segments"]}
sco = {s["start_hz"]: s for s in cbl["segments"]}

def rl_db(g):
    g = abs(g)
    return 99.0 if g <= 1e-9 else -20 * np.log10(min(g, 1.0))

def vswr(g):
    g = min(abs(g), 0.999999)
    return (1 + g) / (1 - g)

print("=" * 78)
print("RH770（拉杆全展，经用户馈线）SOLT 校准后 S11")
print("=" * 78)
rows = []
for seg in an["segments"]:
    st = seg["start_hz"]
    box = solve_error_box(ca(so[st]), ca(ss[st]), ca(sl[st]),
                          np.ones(101, complex), -np.ones(101, complex),
                          np.zeros(101, complex))
    g = apply_calibration(ca(seg), box)
    f = np.array(seg["freqs_hz"], dtype=float)
    n = len(f) if st < 3e8 else 56          # 段2 只用干净区
    f, g = f[:n], g[:n]
    print(f"\n—— 段 {st/1e6:.0f}-{f[-1]/1e6:.0f}MHz（{n} 点）——")
    print(f"{'频率(MHz)':>10} {'|S11|':>8} {'回损(dB)':>9} {'VSWR':>7} {'去线损回损':>11}  备注")
    for f0, note in KEYS:
        if not (f[0] <= f0 * 1e6 <= f[-1]):
            continue
        i = int(np.argmin(np.abs(f - f0 * 1e6)))
        # 线损修正：开口馈线在该频点的 |S11| = e^{-2αl}
        j = int(np.argmin(np.abs(np.array(sco[st]["freqs_hz"], dtype=float) - f[i])))
        loss_f = abs(complex(*sco[st]["s11"][j]))
        g_true = g[i] / loss_f if loss_f > 0.01 else g[i]
        print(f"{f[i]/1e6:10.1f} {abs(g[i]):8.4f} {rl_db(g[i]):9.2f} {vswr(g[i]):7.2f} "
              f"{rl_db(g_true):11.2f}  {note}")
        rows.append((f[i] / 1e6, abs(g[i]), rl_db(g[i]), vswr(g[i]), rl_db(g_true), note))
    # 段内最深谐振
    rls = np.array([rl_db(x) for x in g])
    iw = int(np.argmax(rls))
    print(f"   段内最深点: {f[iw]/1e6:.1f}MHz 回损 {rls[iw]:.1f}dB VSWR {vswr(g[iw]):.2f}")
    # 带宽（回损>10dB 的频段）
    wide = f[rls > 10]
    if len(wide):
        print(f"   回损>10dB 带宽: {wide[0]/1e6:.1f}-{wide[-1]/1e6:.1f}MHz ({len(wide)} 点)")

print("\n" + "=" * 78)
print("判定（VSWR<1.5 优 / <2.0 可用 / >2.0 差）:")
for fr, mag, rl, sw, rlc, note in rows:
    v = "优" if sw < 1.5 else ("可用" if sw < 2.0 else "差")
    print(f"  {fr:7.1f}MHz {note:12s} VSWR={sw:5.2f} ({v})  带线回损{rl:5.1f}dB  去线损{rlc:5.1f}dB")
