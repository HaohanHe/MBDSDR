#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多普勒轨迹判决：判断一段 IQ 里到底有没有收到目标卫星
====================================================
每 4 秒取带内峰值频率，与 TLE 预测的多普勒曲线算相关系数：
  r -> 1  : 峰值跟着多普勒走 = 真信号（收到了）
  r -> 0  : 峰值随机跳 = 噪声（没收到）

用法：
  python waterfall.py <iq> <start_utc "YYYY-MM-DD HH:MM:SS"> <中心频率Hz> \
      <采样率Hz> [tle文件] [卫星名模糊匹配]
  python waterfall.py ... --sat "NOAA19"        # 卫星名也可用 --sat（模糊匹配）

示例：
  python waterfall.py NOAA_19_137100k.sigmf-data "2026-10-25 02:37:30" 137100000 1024000 tle_noaa.txt "NOAA 19"

卫星名模糊匹配规则（防 SWISSCUBE 抢走 "ISS" 这类子串误伤）：
  完整名 > 去符号归一化相等 > 词边界子串 > difflib 近似（会打印实际匹配到谁）
"""
import argparse, os, re
import numpy as np
from datetime import datetime, timedelta
from skyfield.api import load, EarthSatellite, Topos, utc

HERE = os.path.dirname(os.path.abspath(__file__))
TLE_DIRS = [os.path.join(HERE, os.pardir, "data"), HERE, os.getcwd()]
LAT, LON, ALT = 43.774156, 125.397813, 230.8


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def resolve_tle(path):
    """TLE 路径解析：给了就用；相对路径在 cwd/脚本目录/data 里找同名。"""
    if path:
        if os.path.exists(path):
            return path
        for d in TLE_DIRS:
            cand = os.path.join(os.path.abspath(d), path)
            if os.path.exists(cand):
                return cand
        raise SystemExit(f"[err] TLE 文件不存在: {path}")
    for d in TLE_DIRS:
        cand = os.path.join(os.path.abspath(d), "iss.tle")
        if os.path.exists(cand):
            return cand
    raise SystemExit(f"[err] 没给 TLE 文件，且 {'、'.join(TLE_DIRS)} 里都找不到 iss.tle")


def read_entries(tle_path):
    lines = [l.rstrip() for l in open(tle_path, encoding="utf-8", errors="replace") if l.strip()]
    entries = []
    for i in range(len(lines) - 2):
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            entries.append((lines[i].strip(), lines[i + 1], lines[i + 2]))
    return entries


def find_sat(query, tle_path):
    """模糊选星：完整名 4 > 归一化 3 > 词边界子串 2 > difflib 近似 1。

    词边界挡子串误伤："ISS" 不会命中 SWISSCUBE（ISS 是词内片段）。
    """
    entries = read_entries(tle_path)
    if not entries:
        raise SystemExit(f"[err] {tle_path} 里没有可用 TLE（应为 名/1行/2 行 三行体）")
    if not query:
        name, l1, l2 = entries[0]
        return EarthSatellite(l1, l2, name), name
    q = query.strip().lower()
    nq = norm(query)
    best, best_s = None, 0.0
    for name, l1, l2 in entries:
        n = name.lower()
        if q == n:
            s = 4.0
        elif nq and nq == norm(name):
            s = 3.0
        elif re.search(r"(?<![a-z0-9])" + re.escape(q) + r"(?![a-z0-9])", n):
            s = 2.0
        else:
            s = 0.0
        if s > best_s:
            best, best_s = (name, l1, l2), s
    if best is None:
        import difflib
        names = [e[0] for e in entries]
        hits = difflib.get_close_matches(query, names, n=1, cutoff=0.6)
        if hits:
            for name, l1, l2 in entries:
                if name == hits[0]:
                    print(f"[fuzzy] 卫星名模糊匹配: {query!r} -> {name!r}")
                    return EarthSatellite(l1, l2, name), name
        hints = difflib.get_close_matches(query, names, n=3, cutoff=0.4)
        raise SystemExit(f"[err] {os.path.basename(tle_path)} 里没有匹配 {query!r} 的卫星"
                         + (f"；最近似: {'、'.join(hints)}" if hints else ""))
    name, l1, l2 = best
    return EarthSatellite(l1, l2, name), name


def main():
    ap = argparse.ArgumentParser(
        description="多普勒轨迹判决：判断一段 IQ 里到底有没有收到目标卫星",
        epilog='示例: python waterfall.py iq.sigmf-data "2026-10-25 02:37:30" '
               '137100000 1024000 tle_noaa.txt "NOAA 19"')
    ap.add_argument("iq", nargs="?", help="IQ 文件（rtl_sdr u8 / sigmf-data）")
    ap.add_argument("start_utc", nargs="?",
                    help='起始 UTC "YYYY-MM-DD HH:MM:SS"（或只给 HH:MM:SS=今天）')
    ap.add_argument("freq", nargs="?", type=float, help="中心频率 Hz")
    ap.add_argument("rate", nargs="?", type=float, help="采样率 Hz")
    ap.add_argument("tle", nargs="?", help="TLE 文件（省略则在 data/、脚本目录、cwd 找 iss.tle）")
    ap.add_argument("sat", nargs="?", default="", help="卫星名（模糊匹配）")
    ap.add_argument("--sat", dest="sat_opt", default=None,
                    help="卫星名（模糊匹配；与第 6 个位置参数二选一）")
    a = ap.parse_args()

    FN = a.iq
    if not FN:
        ap.error("需要 <iq> 文件；完整用法: <iq> <start_utc> <freq> <rate> [tle] [sat]")
    if not os.path.exists(FN):
        raise SystemExit(f"[err] IQ 文件不存在: {FN}")
    if not a.start_utc or a.freq is None or a.rate is None:
        ap.error("需要 <start_utc> <freq> <rate>（TLE/卫星名可省略）")

    T0 = a.start_utc
    FC = float(a.freq)
    FS = float(a.rate)
    if FS <= 0:
        raise SystemExit(f"[err] 采样率须为正，收到 {a.rate}")
    TLE = resolve_tle(a.tle)
    SAT = a.sat_opt if a.sat_opt is not None else (a.sat or "")
    F0 = FC

    try:
        START = datetime.strptime(T0, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            START = datetime.strptime(f"{datetime.now().date()} {T0}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise SystemExit(f"[err] 起始时间解析不了: {T0!r}（期望 'YYYY-MM-DD HH:MM:SS'）")
    print(f"[cfg] 起始 UTC = {START}  中心 {F0/1e6:.4f} MHz  fs={FS/1e6:.3f}MS/s")

    sat, sname = find_sat(SAT, TLE)
    ts = load.timescale()
    gs = Topos(latitude_degrees=LAT, longitude_degrees=LON, elevation_m=ALT)
    print(f"[cfg] 卫星 {sname}  (TLE {TLE})")

    def pred_dopp(tm):
        tm = tm.replace(tzinfo=utc) if tm.tzinfo is None else tm
        d0 = (sat - gs).at(ts.utc(tm)).distance().km
        d1 = (sat - gs).at(ts.utc(tm + timedelta(seconds=1))).distance().km
        return -(d1 - d0) / 299792.458 * F0

    raw = np.fromfile(FN, dtype=np.uint8)
    n = (len(raw) // 2) * 2
    iq = (raw[:n].astype(np.float32) - 127.5) / 127.5
    c = iq[0::2] + 1j * iq[1::2]
    del raw, iq
    print(f"[iq] {len(c)/FS:.1f}s")

    STEP = int(FS * 4)
    NFFT = 16384
    rows = []
    for k in range(0, len(c) - NFFT, STEP):
        seg = c[k:k + NFFT] * np.hanning(NFFT)
        P = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
        f = np.fft.fftshift(np.fft.fftfreq(NFFT, 1 / FS))
        m = (f > -8000) & (f < 8000)
        fs_, Pw = f[m], P[m]
        i = int(np.argmax(Pw))
        med = np.median(Pw)
        rows.append((k / FS, fs_[i], 10 * np.log10(Pw[i] / med)))

    # 空数组保护：IQ 太短凑不出一个 FFT 窗时诚实退出，不给假判决
    if not rows:
        raise SystemExit(f"[err] IQ 太短或为空：凑不出一个 FFT 窗"
                         f"（需 > {NFFT} 样本 ≈ {NFFT/FS:.2f}s @ {FS/1e6:.3f}MS/s）")

    pred = np.array([pred_dopp(START + timedelta(seconds=r[0])) for r in rows])
    obs = np.array([r[1] for r in rows])
    snr = np.array([r[2] for r in rows])

    print(f"[judge] 预测多普勒 span {np.ptp(pred):.0f} Hz  观测峰值 span {np.ptp(obs):.0f} Hz")
    print(f"[judge] 峰值 SNR: 中位 {np.median(snr):.1f} dB  最大 {snr.max():.1f} dB")
    if len(pred) > 2 and pred.std() > 1 and obs.std() > 1:
        r = float(np.corrcoef(pred, obs)[0, 1])
        if not np.isfinite(r):       # 常数列/退化输入会出 nan，按无相关处理
            print("[judge] corrcoef 非有限（退化输入），按 r=0 处理")
            r = 0.0
    else:
        r = 0.0
    print(f"[judge] 相关系数 r = {r:+.3f}")
    if r > 0.7 and snr.max() > 6:
        print("[verdict] ★ 收到了（峰值跟随多普勒 + SNR 充足）")
    elif r > 0.4:
        print("[verdict] ◐ 疑似弱信号（部分跟随多普勒）")
    else:
        print("[verdict] ✗ 未收到（峰值与多普勒无关 = 噪声）")


if __name__ == "__main__":
    main()
