#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IQ 文件 -> NOAA APT 图像（端到端，含多普勒补偿）
==================================================
rtl_sdr 录的裸 u8 IQ 一路处理成 APT 云图：

  1. 多普勒补偿（skyfield + TLE + GNSS 站址；把 ±3kHz 多普勒搬回 DC）
  2. 窄带低通（±20kHz，去带外噪声）
  3. AM 包络检波（APT 是 AM 调制）
  4. 重采样到 4160 Hz（APT 标准音频率）
  5. decode_apt()（仓库 noaa_apt_lite：行同步 + 双通道重建）
  6. save_apt_png()

用法：
  python apt_decode.py <iq.bin> <start_utc "YYYY-MM-DD HH:MM:SS"> \
      [--fs 1024000] [--sat "NOAA 19"] [--tle tle_noaa.txt] \
      [--no-dopp] [--polarity 1] [--out noaa19] [--dur 600]

先自检（验证仓库解码函数可用，再上真数据）：
  python apt_decode.py --selftest

看支持哪些解码模式：
  python apt_decode.py --list-modes
"""
import argparse, sys, os
import numpy as np
from scipy import signal

sys.path.insert(0, "D:/mbdsdr/MBDSDR")
from mbdsdr_ai import noaa_apt_lite as apl

LAT, LON, ALT = 43.774156, 125.397813, 230.8

# 本脚本支持的解码模式（--list-modes 列出；实现状态诚实标注）
MODES = [
    ("apt", "NOAA APT 气象云图（AM 音频域 + 行同步双通道）",
     "本脚本已实现：--polarity 1/-1（图像上下翻时换），任意 fs 内部重采样 48k"),
]


def list_modes():
    print("apt_decode 支持的解码模式：")
    for name, desc, how in MODES:
        print(f"  {name:<6} {desc}")
        print(f"         └ {how}")
    print("\n不在本脚本的模式（去对应工具，别在这找）：")
    print("  lrpt                  sat_catch.py --demod lrpt（METEOR QPSK 解调）")
    print("  nfm/wfm               doppler_fm.py（多普勒补偿 + FM 解调 → WAV）")
    print("  ft8/wspr/aprs/adsb/sstv   python -m mbdsdr_ai --decode-file <文件> --mode <模式>")


def load_sat(name, tle_files):
    from skyfield.api import EarthSatellite
    for fn in tle_files:
        if not os.path.exists(fn):
            continue
        L = [l.rstrip() for l in open(fn, encoding="utf-8", errors="replace") if l.strip()]
        for i in range(len(L) - 2):
            if name.lower() in L[i].lower() and L[i+1].startswith("1 "):
                return EarthSatellite(L[i+1], L[i+2], L[i])
    return None


def doppler_hz_at(sat, gs, ts, t):
    from datetime import timedelta
    from skyfield.api import utc
    t2 = t + timedelta(seconds=1)
    d0 = (sat - gs).at(ts.utc(t.replace(tzinfo=utc))).distance().km
    d1 = (sat - gs).at(ts.utc(t2.replace(tzinfo=utc))).distance().km
    return -(d1 - d0) / 299792.458 * 137.100e6


def am_to_audio(c, fs, fd_interp):
    """多普勒补偿 + 窄带 + 取实部 -> 音频域 AM 信号（含2400Hz副载波）

    注意：**不做包络检波**——decode_apt 内部第2步自己做 AM 解调
    （_am_discriminator）。这里给它的必须是"音频域 AM 信号"：
    复基带低通后取实部即得（RF AM = A(1+m(t))cos，基带取实部 = A(1+m(t))）。
    且采样率必须 >=8kHz 以承载 2400Hz 副载波。
    """
    n = len(c)
    if fd_interp is not None and np.any(fd_interp):
        ph = 2 * np.pi * np.cumsum(fd_interp) / fs
        c = c * np.exp(-1j * ph)
    b = signal.firwin(255, 20000 / (fs / 2))     # ±20kHz 窄带（APT 视频带宽）
    c = signal.lfilter(b, 1, c)
    audio = np.real(c)
    audio = audio - audio.mean()
    audio = signal.resample_poly(audio, 48000, int(round(fs)))
    return audio


def selftest():
    """用仓库合成器验证解码函数可用"""
    print("[selftest] 仓库合成 APT 音频 -> decode_apt")
    img_a, img_b = apl.synthesize_test_images(n_lines=60)
    audio = apl.synthesize_apt_audio(img_a, img_b, 24000)
    res = apl.decode_apt(audio, 24000, polarity=1)
    print("[selftest] decode_apt 返回 keys:", list(res.keys())[:8])
    n_lines = res.get("n_lines") or res.get("lines") or len(res.get("image_a", []))
    print(f"[selftest] 解出行数: {n_lines}")
    try:
        out = apl.save_apt_png(res, "selftest_apt")
        print("[selftest] save_apt_png ->", out)
    except Exception as e:
        print("[selftest] save_apt_png 失败:", e)
    print("[selftest] 链路 OK" if n_lines else "[selftest] 行数为 0，链路有问题")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iq", nargs="?")
    ap.add_argument("start_utc", nargs="?")
    ap.add_argument("--fs", type=float, default=1.024e6)
    ap.add_argument("--sat", default="NOAA 19")
    ap.add_argument("--tle", action="append", default=["tle_noaa.txt", "tle_weather.txt"])
    ap.add_argument("--no-dopp", action="store_true")
    ap.add_argument("--polarity", type=int, default=1, choices=[1, -1])
    ap.add_argument("--out", default="noaa_apt")
    ap.add_argument("--dur", type=float, default=0)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list-modes", action="store_true",
                    help="列出支持的解码模式后退出")
    a = ap.parse_args()

    if a.list_modes:
        list_modes()
        return
    if a.selftest:
        selftest()
        return
    if not a.iq or not a.start_utc:
        ap.error("需要 <iq.bin> <start_utc>，或用 --selftest / --list-modes")

    from datetime import datetime
    t_start = datetime.strptime(a.start_utc, "%Y-%m-%d %H:%M:%S")
    print(f"[iq] {a.iq}  fs={a.fs/1e6:.3f}MS/s  sat={a.sat}  start={a.start_utc} UTC")

    raw = np.fromfile(a.iq, dtype=np.uint8)
    n = (len(raw) // 2) * 2
    iq = (raw[:n].astype(np.float32) - 127.5) / 127.5
    c = iq[0::2] + 1j * iq[1::2]
    del raw, iq
    if a.dur > 0:
        c = c[:int(a.dur * a.fs)]
    print(f"[iq] {len(c)} samples = {len(c)/a.fs:.1f}s")

    fd_interp = None
    if not a.no_dopp:
        sat = load_sat(a.sat, a.tle)
        if sat is None:
            print(f"[dopp] TLE 里没找到 {a.sat}，跳过补偿（用 --no-dopp 语义）")
        else:
            from skyfield.api import load, Topos, utc
            from datetime import timedelta
            ts = load.timescale()
            gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
            dur = len(c) / a.fs
            ngrid = int(dur) + 3
            gt = [t_start + timedelta(seconds=i) for i in range(ngrid)]
            d0 = (sat - gs).at(ts.utc([g.replace(tzinfo=utc) for g in gt])).distance().km
            d1 = (sat - gs).at(ts.utc([(g + timedelta(seconds=1)).replace(tzinfo=utc)
                                       for g in gt])).distance().km
            fd_grid = -(d1 - d0) / 299792.458 * 137.100e6
            ta = np.arange(len(c)) / a.fs
            fd_interp = np.interp(ta, np.arange(ngrid), fd_grid)
            print(f"[dopp] {fd_interp[0]:+.0f} -> {fd_interp[-1]:+.0f} Hz "
                  f"(span {fd_interp.max()-fd_interp.min():.0f} Hz)")

    audio = am_to_audio(c, a.fs, fd_interp)
    print(f"[demod] AM音频域 -> 48kHz, {len(audio)/48000:.1f}s")
    res = apl.decode_apt(audio, 48000, polarity=a.polarity)
    keys = list(res.keys())
    print("[decode] keys:", keys[:10])
    n_lines = res.get("n_lines") or len(res.get("image_a", []))
    print(f"[decode] 解出行数: {n_lines}")
    if n_lines:
        out = apl.save_apt_png(res, a.out)
        print(f"[out] {out}")
    else:
        print("[out] 没解出行 —— 检查：信号有无/极性(--polarity -1)/带宽/多普勒方向")


if __name__ == "__main__":
    main()
