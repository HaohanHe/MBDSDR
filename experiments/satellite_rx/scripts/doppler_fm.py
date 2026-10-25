#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ARISS / ISS 接收：多普勒补偿 + NFM 解调
======================================
输入：rtl_sdr 录的 8-bit unsigned IQ (cf32 之前是 u8)，中心频率固定
流程：
  1. 按 TLE + 站址算出每时刻的多普勒频移 f_d(t)
  2. 相位补偿  c * exp(-j*2*pi*cumsum(f_d)/fs)  -> 把信号搬回 DC
  3. 多级抗混叠抽取 2.048MS/s -> 48kS/s
  4. FM 鉴频 -> WAV

用法：
  python doppler_fm.py <iq.bin> <start_utc "YYYY-MM-DD HH:MM:SS"> \
         [--freq 145.990e6] [--fs 2.048e6] [--dur 5.7] [--target 145.990e6] \
         [--out out.wav] [--no-dopp]

口径说明：本脚本是"后处理多普勒补偿"——录制时中心频率固定，
多普勒在解算时补偿。优点：不丢数据、可反复调优；
代价：不能实时听。实时听需 SDR 支持频率扫描（待补）。
"""
import argparse
import os
import numpy as np
from datetime import datetime, timedelta, timezone

from skyfield.api import load, EarthSatellite, Topos, utc
from scipy import signal

LAT, LON, ALT = 43.774156, 125.397813, 230.8      # GNSS 实测站址（长春）
TLE_FILE = "iss.tle"
C = 299792.458  # km/s


def load_sat():
    if not os.path.exists(TLE_FILE):
        raise SystemExit(f"[err] TLE 文件不存在: {TLE_FILE}（把 iss.tle 放 cwd，或用 --no-dopp）")
    lines = [l.rstrip() for l in open(TLE_FILE, encoding="utf-8") if l.strip()]
    return EarthSatellite(lines[1], lines[2], lines[0])


def doppler_curve(sat, gs, ts, t_start, n_samples, fs, freq_hz):
    """返回每样本的多普勒频移数组 (Hz)。"""
    dur_s = n_samples / fs
    # 以 1s 为网格算距离率，再插值到每样本（足够平滑：ISS 多普勒变化 ~30Hz/s）
    n_grid = int(dur_s) + 3
    grid_t = [t_start + timedelta(seconds=i) for i in range(n_grid)]
    tt = ts.utc([g.replace(tzinfo=utc) for g in grid_t])
    d = (sat - gs).at(tt).distance().km
    rate = np.diff(d)  # km/s，正=远离
    rate = np.concatenate([rate, rate[-1:]])
    # 多普勒：远离 -> 频率降低
    dopp_grid = -rate / C * freq_hz
    t_axis = np.arange(n_samples) / fs
    return np.interp(t_axis, np.arange(n_grid), dopp_grid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iq")
    ap.add_argument("start_utc", help='"YYYY-MM-DD HH:MM:SS"')
    ap.add_argument("--fs", type=float, default=2.048e6)
    ap.add_argument("--freq", type=float, default=145.990e6, help="SDR 中心频率(Hz)")
    ap.add_argument("--target", type=float, default=145.990e6,
                    help="卫星标称下行频率(Hz)，决定补偿后 DC 对齐点")
    ap.add_argument("--dur", type=float, default=0, help="只处理前 N 秒，0=全部")
    ap.add_argument("--out", default="iss_fm.wav")
    ap.add_argument("--no-dopp", action="store_true", help="不做多普勒补偿（对照）")
    ap.add_argument("--tone-hz", type=float, default=0,
                    help="非零则把目标搬到该中频(如 12000)便于看频谱")
    a = ap.parse_args()

    fs = a.fs
    raw = np.fromfile(a.iq, dtype=np.uint8)
    n = (len(raw) // 2) * 2
    iq = (raw[:n].astype(np.float32) - 127.5) / 127.5
    c = iq[0::2] + 1j * iq[1::2]
    del raw, iq
    if a.dur > 0:
        c = c[:int(a.dur * fs)]
    n_samp = len(c)
    print(f"[iq] {n_samp} samples = {n_samp/fs:.2f}s @ {fs/1e6:.3f} MS/s")

    t_start = datetime.strptime(a.start_utc, "%Y-%m-%d %H:%M:%S")
    print(f"[t]  start UTC {t_start}  (local +8h: "
          f"{(t_start+timedelta(hours=8)).strftime('%H:%M:%S')})")

    # ---- 目标相对 SDR 中心的固定偏移 ----
    f_offset_static = a.target - a.freq

    if a.no_dopp:
        print("[dopp] 关闭（对照组）")
        ph = 2 * np.pi * f_offset_static * np.arange(n_samp) / fs
        c = c * np.exp(-1j * ph)
    else:
        sat = load_sat()
        ts = load.timescale()
        gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
        fd = doppler_curve(sat, gs, ts, t_start, n_samp, fs, a.target)
        print(f"[dopp] f_d: {fd[0]:+.0f} Hz -> {fd[-1]:+.0f} Hz "
              f"(span {fd.max()-fd.min():.0f} Hz)")
        total = f_offset_static + fd
        phase = 2 * np.pi * np.cumsum(total) / fs
        c = c * np.exp(-1j * phase)

    if a.tone_hz:
        c = c * np.exp(1j * 2 * np.pi * a.tone_hz * np.arange(n_samp) / fs)

    # ---- 多级抗混叠抽取：fs -> ~32k，再 -> 48k ----
    # 目标：保留 DC 附近 ±12.5kHz（NFM 信道带宽）。
    # 校验（fs 非默认 2.048e6/1.024e6 时老代码会算错输出率）：
    #   ① fs 须能承载音频通道；② 抽取后率不得带不动 3.5kHz 音频；
    #   ③ 最终采样率按实际比值算，不许无条件 cur*3/2（那是 32k 特例的偷懒）。
    out_fs = 48000
    if not np.isfinite(fs) or fs <= 0:
        raise SystemExit(f"[err] --fs 须为正有限值，收到 {a.fs}")
    if fs < 32000:
        raise SystemExit(f"[err] --fs {fs:.0f} Hz < 32kHz：承载不了 NFM 音频通道（需 ≥32k）")
    stages = []
    cur = float(fs)
    for d in (8, 8, 4):        # 2.048M->256k->32k；1.024M->128k->32k；其余按需跳过
        if cur / d < 32000:
            continue
        if len(c) < 32 * d:
            raise SystemExit(f"[err] IQ 太短（{len(c)} 样本），不够做 {d} 倍抽取（需 ≥{32*d}）")
        c = signal.decimate(c, d, ftype="fir", zero_phase=True)
        cur /= d
        stages.append(f"{cur/1e3:.1f}k")
    print(f"[dec] {'->'.join(stages) if stages else '(无抽取)'}  "
          f"{fs/1e3:.1f}k -> {cur/1e3:.1f}k")
    if cur < 16000:
        raise SystemExit(f"[err] 抽取后 {cur:.0f} S/s 带不动 3.5kHz 音频（需 ≥16k），检查 --fs")
    # ~32k -> 48k：任意 fs 用有理比重采样（32k 特例=3/2，其余各自算）
    if abs(cur - out_fs) > 1:
        from fractions import Fraction
        ratio = Fraction(int(out_fs), int(round(cur))).limit_denominator(2000)
        c = signal.resample_poly(c, ratio.numerator, ratio.denominator)
        cur = cur * ratio.numerator / ratio.denominator
    print(f"[dec] final {cur/1e3:.2f} kHz")

    # ---- FM 鉴频 ----
    d = np.angle(c[1:] * np.conj(c[:-1]))
    d = d / (2 * np.pi) * cur           # 瞬时频率(Hz)
    # 去直流（频率偏移残差）
    d = d - np.median(d)
    # 音频带限 3.5kHz（话音）
    b = signal.firwin(129, 3500 / (cur / 2))
    audio = signal.lfilter(b, 1, d)
    # 重采样到 48k 若还未对齐
    if abs(cur - out_fs) > 1:
        audio = signal.resample_poly(audio, out_fs, int(round(cur)))
    audio = audio / (np.max(np.abs(audio)) + 1e-9) * 0.9

    import wave
    with wave.open(a.out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(out_fs)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    print(f"[out] {a.out}  {len(audio)/out_fs:.2f}s @ {out_fs}Hz")

    # 诊断：音频频谱特征
    P = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    f = np.fft.rfftfreq(len(audio), 1 / out_fs)
    PdB = 10 * np.log10(P + 1e-20)
    PdB -= PdB.max()
    band = [(0, 500), (500, 1500), (1500, 2500), (2500, 3500)]
    print("[audio] 相对谱能量(dB):")
    for lo, hi in band:
        m = (f >= lo) & (f < hi)
        print(f"   {lo:5}-{hi:5} Hz : {10*np.log10(P[m].mean()+1e-20):6.1f} dB")
    # SSTV 特征：1200Hz 同步 + 1500-2300Hz 扫描
    m = (f > 1150) & (f < 1250)
    print(f"   SSTV 1200Hz 同步头候选: {10*np.log10(P[m].mean()+1e-20):6.1f} dB")


if __name__ == "__main__":
    main()
