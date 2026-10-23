"""馈线分析：SOLT 校准后提取电气长度（相位斜率）+ 损耗（|S11| 幅度）。
只用干净区：段1 全段 + 段2 前 56 点（<=455MHz，435 目标在内）。
"""
import sys, json
import numpy as np
sys.path.insert(0, "../..")
from mbdsdr_ai.vna_cal import solve_error_box, apply_calibration

D = "data"
C = 299792458.0

def load(n):
    return json.load(open(f"{D}/{n}.json", encoding="utf-8"))

def ca(s):
    return np.array([complex(*v) for v in s["s11"]])

op, sh, lo, cb = load("open"), load("short"), load("load"), load("cable_open")
so = {s["start_hz"]: s for s in op["segments"]}
ss = {s["start_hz"]: s for s in sh["segments"]}
sl = {s["start_hz"]: s for s in lo["segments"]}

cal_segs = []
for seg in cb["segments"]:
    st = seg["start_hz"]
    box = solve_error_box(ca(so[st]), ca(ss[st]), ca(sl[st]),
                          np.ones(101, complex), -np.ones(101, complex),
                          np.zeros(101, complex))
    g = apply_calibration(ca(seg), box)
    f = np.array(seg["freqs_hz"], dtype=float)
    cal_segs.append((f, g))
    print(f"段 {st/1e6:.0f}-{seg['stop_hz']/1e6:.0f}MHz 校准后 |S11| "
          f"{np.abs(g).min():.4f}-{np.abs(g).max():.4f}")

# 电气长度：段1 全段相位解缠斜率（段1 无 race 污染）
f1, g1 = cal_segs[0]
ph1 = np.unwrap(np.angle(g1))
slope = np.polyfit(f1 / 1e6, ph1, 1)[0]          # rad/MHz
Le = abs(slope) / 1e6 * C / (4 * np.pi)          # Δph = -4πf·Le/c
print(f"\n段1 相位斜率: {slope:.4f} rad/MHz -> 电气长度 {Le:.3f} m")
print(f"  若物理长度 1.0m -> VF = {Le:.3f} (>1 不可能，长度存疑)")
print(f"  若 VF=0.66 (实芯PE) -> 物理长度 {Le/0.66:.2f} m")
print(f"  若 VF=0.85 (发泡PE) -> 物理长度 {Le/0.85:.2f} m")

# 损耗：|S11| 幅度偏离 1 的部分（开口线双程损耗近似）
m1 = np.abs(g1)
loss_db = -20 * np.log10(m1)
print(f"\n段1 双程损耗: {loss_db.min():.2f}-{loss_db.max():.2f} dB "
      f"(均值 {loss_db.mean():.2f} dB -> 单程约 {loss_db.mean()/2:.2f} dB/100MHz跨度)")

# 段2 干净区（<=455MHz）交叉验证相位斜率
f2, g2 = cal_segs[1]
n_clean = 56
ph2 = np.unwrap(np.angle(g2[:n_clean]))
slope2 = np.polyfit(f2[:n_clean] / 1e6, ph2, 1)[0]
Le2 = abs(slope2) / 1e6 * C / (4 * np.pi)
print(f"段2 干净区(400-455MHz) 相位斜率: {slope2:.4f} rad/MHz -> 电气长度 {Le2:.3f} m (交叉验证)")

# 关键频点回损（若这根线将来带天线，这些频点的线损有参考价值）
print("\n关键频点（开口馈线状态下的读数）:")
for f0 in (137e6, 145.8e6, 162.4e6, 435e6):
    for f, g in cal_segs:
        if f[0] <= f0 <= f[-1]:
            i = int(np.argmin(np.abs(f - f0)))
            print(f"  {f[i]/1e6:7.1f}MHz  |S11|={abs(g[i]):.4f}  "
                  f"回损={-20*np.log10(max(abs(g[i]),1e-9)):.2f}dB  ph={np.angle(g[i]):+.3f}")
            break
