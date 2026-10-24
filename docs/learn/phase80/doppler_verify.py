#!/usr/bin/env python3
# ARISS 全链路保障 -- NCO 多普勒补偿实测（对应 2026-10-25 06:27 CST 过境）
# 用真实 SGP4 多普勒曲线驱动合成 IQ，验证仓内 remove_doppler_shift 的补偿精度。
# 判据（FM 语音 ±2.5 kHz 通带）：
#   补偿后各音频音调是否回到装订位置、语音谱是否留在 ±2.5 kHz 通带内。
import json
from datetime import timedelta, timezone
import sys
import numpy as np

sys.path.insert(0, "/workspace/MBDSDR")
from mbdsdr_ai.doppler_compensation import remove_doppler_shift, apply_doppler_shift
from mbdsdr_ai.sat_passes import GroundStation, predict_passes, compute_doppler_curve

# ---- 场景参数 -------------------------------------------------------------
QTH = GroundStation(43.8267, 125.4189, 220.0)   # 吉林农业大学
F0 = 145.800e6                                   # ARISS 下行
VOICE_BW = 2500.0                                # FM 语音通带 ±2.5 kHz
FS = 48000.0                                     # 基带 IQ 采样率

tle_json = json.loads(open(
    "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d4d-3cb31d5a8000/iss_tle.txt"
).read())
TLE = [tle_json["header"], tle_json["line1"], tle_json["line2"]]

passes = predict_passes(TLE, QTH, start_time=None, hours=10.0, min_alt=5.0)
# 目标过境：2026-10-25 06:27 CST = 10-24 22:27Z
main = [p for p in passes
        if p.rise_time.utc_datetime().hour == 22
        and p.rise_time.utc_datetime().day == 24][0]
curve = compute_doppler_curve(TLE, QTH, main, F0, num_points=5051)
DURATION_S = main.duration
fd_curve = np.array([pt.doppler_hz for pt in curve])
t_curve = np.array([(pt.time.utc_datetime() - main.rise_time.utc_datetime()).total_seconds()
                    for pt in curve])
rate = np.diff(fd_curve) / np.diff(t_curve)

print("== 过境 ==")
from datetime import timedelta
CST = timezone(timedelta(hours=8))
r = main.rise_time.utc_datetime().astimezone(CST)
s = main.set_time.utc_datetime().astimezone(CST)
m = main.max_alt_time.utc_datetime().astimezone(CST)
print(f"AOS {r:%H:%M:%S} az={main.rise_az:.1f} | MAX {m:%H:%M:%S} alt={main.max_alt:.1f} "
      f"| LOS {s:%H:%M:%S} az={main.set_az:.1f} | dur={DURATION_S:.0f}s")
print(f"fd range [{fd_curve.min():.1f}, {fd_curve.max():.1f}] Hz, "
      f"rate [{rate.min():.1f}, {rate.max():.1f}] Hz/s")

# ---- 合成 IQ：FM 语音音调簇 + 噪声，装订在 0 Hz ---------------------------
rng = np.random.default_rng(20261025)
n = int(DURATION_S * FS)
tt = np.arange(n) / FS
tones = np.array([0.0, 600.0, -600.0, 1500.0, -1500.0, 2400.0, -2400.0])
amps = np.array([1.0, 0.55, 0.55, 0.35, 0.35, 0.18, 0.18])
base = np.zeros(n, dtype=complex)
for f, a in zip(tones, amps):
    base += a * np.exp(2j * np.pi * f * tt)
noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.35
iq0 = base + noise
iq0 /= np.abs(iq0).max()

# 真实多普勒频移：曲线插值到每样本
fd_per_sample = np.interp(tt, t_curve, fd_curve)
received = apply_doppler_shift(iq0, FS, fd_per_sample)

# ---- 频谱测量：每 1 s 块估计主音频率（抛物线插值） -------------------------
def measure_chunk_freq(x, fs, chunk_s=1.0):
    """返回每秒块的估计音调频率（Hz，复数加权质心近似）。"""
    m = int(chunk_s * fs)
    freqs = []
    win = np.hanning(m)
    for s in range(0, len(x) - m + 1, m):
        seg = x[s:s + m] * win
        sp = np.abs(np.fft.fftshift(np.fft.fft(seg, 8192)))
        fb = np.fft.fftshift(np.fft.fftfreq(8192, 1.0 / fs))
        k = int(np.argmax(sp))
        # 抛物线插值精修
        if 0 < k < len(sp) - 1:
            y0, y1, y2 = np.log(sp[k - 1] + 1e-12), np.log(sp[k] + 1e-12), np.log(sp[k + 1] + 1e-12)
            d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
        else:
            d = 0.0
        freqs.append(fb[k] + d * (fb[1] - fb[0]))
    return np.array(freqs)

# ---- 方案 A：精确曲线（每样本） ------------------------------------------
comp_exact = remove_doppler_shift(received, FS, fd_per_sample)
m_exact = measure_chunk_freq(comp_exact, FS)

# ---- 方案 B1：1 Hz 线性插值更新（智能跟踪：1 Hz 预测点间线性内插） --------
fd_1hz = np.interp(tt, t_curve[::10], fd_curve[::10])   # 10 点/秒 -> 1 Hz 采样线性内插
comp_1hz = remove_doppler_shift(received, FS, fd_1hz)
m_1hz = measure_chunk_freq(comp_1hz, FS)

# ---- 方案 B2：1 Hz 零阶保持 ZOH（C++ 1 Hz 循环真实行为：保持上拍值） ------
tick = np.floor(tt).astype(int)
fd_zoh = np.interp(tick, np.floor(t_curve).astype(int), fd_curve)
comp_zoh = remove_doppler_shift(received, FS, fd_zoh)
m_zoh = measure_chunk_freq(comp_zoh, FS)

# ---- 方案 C：2 Hz 阶梯更新 -------------------------------------------------
fd_2hz = np.interp(tt, t_curve[::5], fd_curve[::5])
comp_2hz = remove_doppler_shift(received, FS, fd_2hz)
m_2hz = measure_chunk_freq(comp_2hz, FS)

# ---- 方案 D：不补偿（装订 AOS 频率后静止不动） -----------------------------
comp_off = received
m_off = measure_chunk_freq(comp_off, FS)

# ---- 报告 ----------------------------------------------------------------
def report(name, meas, truth):
    err = meas - truth
    inband = np.abs(meas) <= VOICE_BW
    print(f"{name:<14} 残余 |err| max={np.abs(err).max():7.1f} Hz  "
          f"p95={np.percentile(np.abs(err),95):6.1f} Hz  "
          f"语音在 ±{VOICE_BW/1000:.1f}k 通带内占比 {inband.mean()*100:5.1f}%")

print("\n== 补偿精度（音调簇应回到 0 Hz 附近） ==")
truth0 = np.zeros_like(m_exact)
report("A 精确曲线", m_exact, truth0)
report("B1 1Hz线性", m_1hz, truth0)
report("B2 1Hz零阶保持", m_zoh, truth0)
report("C 2 Hz 线性", m_2hz, truth0)
# 不补偿时音调被频偏推离 0：与真实频偏的差就是装订失配
print(f"D 不补偿     音调频率范围 [{m_off.min():8.1f}, {m_off.max():8.1f}] Hz "
      f"(通带 ±{VOICE_BW:.0f} Hz 内占比 {float((np.abs(m_off)<=VOICE_BW).mean())*100:.1f}%)")

# ---- 过零区特写：速率最大处（AOS 后 ~15 s） ------------------------------
k0 = int(np.argmax(np.abs(rate))) + 1
t_peak_rate = t_curve[k0]
chunk0 = int(round(t_peak_rate))          # 对应的 1 s 测量块
print(f"\n== 速率极值点附近（t={t_peak_rate:.1f}s, fd={fd_curve[k0]:.0f} Hz, "
      f"rate={rate[k0-1]:.1f} Hz/s, 测量块 {chunk0}） ==")
for name, meas in (("A", m_exact), ("B1", m_1hz), ("B2", m_zoh), ("C", m_2hz)):
    seg = meas[max(0, chunk0 - 2):chunk0 + 3]
    print(f"  {name}: " + " ".join(f"{float(v):+7.1f}" for v in seg) + " Hz")

# ---- 判定 ----------------------------------------------------------------
ok_exact = np.abs(m_exact).max() < 60.0
ok_1hz = np.abs(m_1hz).max() < 60.0
ok_zoh = np.abs(m_zoh).max() < 120.0
inband_1hz = (np.abs(m_1hz) <= VOICE_BW).mean() > 0.99
inband_zoh = (np.abs(m_zoh) <= VOICE_BW).mean() > 0.99
print("\n== 判定 ==")
print(f"PASS 精确曲线残余 < 60 Hz        : {ok_exact}")
print(f"PASS 1 Hz 线性残余 < 60 Hz       : {ok_1hz}")
print(f"PASS 1 Hz ZOH  残余 < 120 Hz     : {ok_zoh}")
print(f"PASS 1 Hz 线性语音通带保持 > 99% : {inband_1hz}")
print(f"PASS 1 Hz ZOH  语音通带保持 > 99%: {inband_zoh}")
sys.exit(0 if all([ok_exact, ok_1hz, ok_zoh, inband_1hz, inband_zoh]) else 1)
