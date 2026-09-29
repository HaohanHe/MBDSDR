# SPDX-License-Identifier: MIT
"""NOAA POES APT 气象卫星云图 lite 解码（依据 NOAA APT 公开格式独立实现）。

处理链（NOAA APT 广播公开格式 + 标准 DSP）：
  1) 重采样到 work_rate=12480Hz（=FINAL_RATE*3），带 DC 去除低通
     （cutout=4800Hz≈2×副载波）抗混叠；
  2) AM 包络解调：两采样鉴别器 y[i]=sqrt(prev²+curr²-2cosφ·prev·curr)/sinφ，
     φ=2π·2400/fs；
  3) 解调后低通到 FINAL_RATE/2=2080Hz；
  4) 重采样到 FINAL_RATE=4160Hz（一像素一样本）；
  5) 行同步：生成 38 样本 ±1 方波 guard，滑动互相关找行首；
  6) 按 2080 样本/行（PX_PER_ROW）切 A/B 两通道，各取 909 像素图像区。

APT 行结构（NOAA APT 公开格式）：
  sync 39 | space 47 | image 909 | telemetry 45 = 1040/通道，两通道 = 2080/行。
  行率 2 行/秒（2080 像素/行 × 2 = 4160 = FINAL_RATE）。

noaa-apt（https://github.com/martinber/noaa-apt）、SatDump 等开源项目仅作
技术参考与致谢，本仓未包含其源代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# APT 常量（NOAA APT 公开格式）
# --------------------------------------------------------------------------- #
# FINAL_RATE=4160：最终视频采样率，一像素一样本
APT_VIDEO_RATE = 4160          # 最终视频采样率，一像素一样本
# APT AM 副载波 2400 Hz
APT_SUBCARRIER = 2400.0        # AM 副载波 Hz
# 每行样本（两通道）2080
APT_LINE_SAMPLES = 2080        # 每行样本（两通道）
# 2080 像素/行，FINAL_RATE=4160 => 2 行/秒
APT_LINE_SECONDS = 0.5
# 行同步段 39 像素
APT_SYNC_LEN = 39
# 同步后间隔 47 像素
APT_SPACE_LEN = 47
# 图像区 909 像素
APT_IMAGE_LEN = 909
# 遥测段 45 像素
APT_TELEM_LEN = 45
# 每通道 1040 像素 = 39+47+909+45
APT_HALF = APT_SYNC_LEN + APT_SPACE_LEN + APT_IMAGE_LEN + APT_TELEM_LEN  # 1040
# 通道 A 图像偏移 = sync+space = 86；通道 B 图像偏移 = 1040+86 = 1126
APT_IMG_A_OFFSET = APT_SYNC_LEN + APT_SPACE_LEN               # 86
APT_IMG_B_OFFSET = APT_HALF + APT_SYNC_LEN + APT_SPACE_LEN    # 1126

# APT 行结构与 NOAA 公开 APT 格式一致：同步段 39 像素、图像区 909、偏移 86/1126。
# 数字 HRPT 帧参数见 mbdsdr_ai/satdump_adapter.py HRPT_*：
#   665.4kbps BPSK, 60-bit sync 0x0A116FD719D83C95, 11090 words x 10 bits。

# 内部工作参数（work_rate=12480=FINAL_RATE*3，抗混叠/低通 FIR 设计）
APT_WORK_RATE = 12480          # 内部工作采样率
APT_RESAMPLE_CUTOUT = 4800.0   # 重采样低通截止 Hz（≈2×载波 2400）
APT_RESAMPLE_ATTEN = 30.0      # 重采样阻带衰减 dB
APT_RESAMPLE_DELTA = 1000.0    # 重采样过渡带 Hz
APT_DEMOD_ATTEN = 25.0         # 解调后低通阻带衰减 dB

# APT 行同步 guard 模板（38 样本 ±1 方波）：
# 模板 = 2 个 -1 开头 + 7 个完整周期(2×-1,2×+1) + 8 个 -1 尾。
# 在 FINAL_RATE=4160（pixel_width=1）下长度 = 2 + 28 + 8 = 38 样本，±1 方波。
_SYNC_GUARD = np.array([
    -1, -1,  -1, -1, +1, +1,  -1, -1, +1, +1,  -1, -1, +1, +1,
    -1, -1,  +1, +1, -1, -1,  +1, +1, -1, -1,  +1, +1, -1, -1,
    +1, +1,  -1, -1, -1, -1,  -1, -1, -1, -1,
], dtype=np.float64)

# 行结构里 sync 占 39 像素：guard(38) + 1 个尾随黑像素 = 39，
# 供合成器构造完整行（0=黑,255=白）。
_SYNC_WORD = np.concatenate([
    (_SYNC_GUARD > 0).astype(np.float64),  # 38 样本 0/1
    [0.0],                                  # 第 39 像素：尾随黑
])


# --------------------------------------------------------------------------- #
# FIR 滤波（Kaiser 窗 sinc，标准 FIR 设计）
# --------------------------------------------------------------------------- #
def _kaiser_beta(atten: float) -> float:
    """Kaiser 窗 beta（标准 Kaiser 窗公式）。"""
    if atten > 50.0:
        return 0.1102 * (atten - 8.7)
    if atten < 21.0:
        return 0.0
    return 0.5842 * (atten - 21.0) ** 0.4 + 0.07886 * (atten - 21.0)


def _kaiser_lowpass(cutout_hz: float, fs: float, atten: float,
                    delta_hz: float) -> np.ndarray:
    """设计 Kaiser 窗 sinc 低通 FIR（矩形窗 sinc × Kaiser 窗）。

    长度公式 length=ceil((atten-8)/(2.285·delta_rad))+1，取奇数。
    """
    nyq = fs / 2.0
    cutoff = cutout_hz / nyq            # 归一化截止
    delta_w = delta_hz / nyq            # 过渡带
    beta = _kaiser_beta(atten)
    # 长度：(atten-8)/(2.285·delta_rad)，delta_rad = pi·delta_w
    length = int(np.ceil((atten - 8.0) / (2.285 * np.pi * delta_w))) + 1
    if length % 2 == 0:                 # 强制奇数
        length += 1
    length = max(length, 11)
    n = np.arange(length) - (length - 1) / 2.0
    # sinc 冲激响应：h[n]=sin(n·pi·cutout)/(n·pi)，n=0 时=cutout
    h = np.sinc(n * cutoff) * cutoff
    win = np.kaiser(length, beta)       # Kaiser 窗
    h = h * win
    return h / np.sum(h)


def _filtfilt_zero(h: np.ndarray, x: np.ndarray) -> np.ndarray:
    """零相位 FIR 卷积（edge 收敛）。"""
    from scipy.signal import fftconvolve
    y = fftconvolve(x, h, mode="same")
    return y


# --------------------------------------------------------------------------- #
# 合成（自检信号源）
# --------------------------------------------------------------------------- #
def _gray_to_amp(gray: np.ndarray) -> np.ndarray:
    """0-255 灰度 -> AM 包络幅度（黑≈0.15，白≈1.0）。

    解调后信号幅度即像素亮度；保留最小载波防过调幅。
    """
    g = np.clip(gray, 0, 255).astype(np.float64) / 255.0
    return 0.15 + g * 0.85


def build_apt_line(image_a_row: np.ndarray, image_b_row: np.ndarray,
                   space_level: float = 128.0,
                   telem_a: Optional[np.ndarray] = None,
                   telem_b: Optional[np.ndarray] = None) -> np.ndarray:
    """构造一行 2080 个灰度采样（A/B 通道）。

    APT 行结构（公开格式）。
    """
    a = np.clip(image_a_row, 0, 255).astype(np.float64)
    b = np.clip(image_b_row, 0, 255).astype(np.float64)
    if len(a) != APT_IMAGE_LEN or len(b) != APT_IMAGE_LEN:
        raise ValueError(f"图像行需 {APT_IMAGE_LEN} 像素")
    sync = (_SYNC_WORD * 255.0)         # 0/255 两极
    space = np.full(APT_SPACE_LEN, space_level)
    ta = telem_a if telem_a is not None else np.linspace(0, 255, APT_TELEM_LEN)
    tb = telem_b if telem_b is not None else np.linspace(0, 255, APT_TELEM_LEN)
    line = np.concatenate([sync, space, a, ta,
                           sync, space, b, tb]).astype(np.float64)
    assert len(line) == APT_LINE_SAMPLES
    return line


def synthesize_test_images(n_lines: int = 80) -> Tuple[np.ndarray, np.ndarray]:
    """生成两幅可判别测试图：A=水平灰阶+黑白边带，B=竖直条带。"""
    x = np.linspace(0, 255, APT_IMAGE_LEN)
    a = np.tile(x, (n_lines, 1))
    a[:, :60] = 0
    a[:, -60:] = 255
    b = np.zeros((n_lines, APT_IMAGE_LEN))
    for k in range(12):
        b[:, k * APT_IMAGE_LEN // 12:(k + 1) * APT_IMAGE_LEN // 12] = (
            255 if k % 2 == 0 else 0)
    return a.astype(np.uint8), b.astype(np.uint8)


def synthesize_apt_audio(image_a: np.ndarray, image_b: np.ndarray,
                         fs: int = 24000, noise_std: float = 0.0,
                         rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """把 A/B 图像逐行 AM 调制到 2400Hz 副载波实音频（fs，默认 24kHz）。

    卫星端用视频幅度调制 2400Hz 副载波，幅度=亮度。
    合成器产生可被 decode_apt() 完整往返解调的测试音频（合成信号，非硬件）。
    """
    if rng is None:
        rng = np.random.default_rng(0)
    if fs < 8000:
        raise ValueError("音频采样率需 ≥8kHz 以承载 2400Hz 副载波")
    n_lines = min(len(image_a), len(image_b))
    gray = np.concatenate([build_apt_line(image_a[i], image_b[i])
                           for i in range(n_lines)])
    amp_video = _gray_to_amp(gray)                  # 4160 视频率，每样本一像素包络
    n_aud = int(round(len(amp_video) * fs / APT_VIDEO_RATE))
    px_idx = np.clip((np.arange(n_aud) * APT_VIDEO_RATE / fs).astype(np.int64),
                     0, len(amp_video) - 1)
    amp_aud = amp_video[px_idx]
    t = np.arange(n_aud) / float(fs)
    audio = amp_aud * np.cos(2.0 * np.pi * APT_SUBCARRIER * t)
    if noise_std:
        audio = audio + noise_std * rng.standard_normal(len(audio))
    peak = np.max(np.abs(audio)) or 1.0
    return (audio / peak * 0.9).astype(np.float64)


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def _resample_to(x: np.ndarray, fs_in: float, fs_out: float) -> np.ndarray:
    """有理重采样（上采样→抗混叠 FIR→下采样，scipy resample_poly）。"""
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(round(fs_in)), int(round(fs_out)))
    up = int(round(fs_out)) // g
    down = int(round(fs_in)) // g
    return resample_poly(x, up, down)


def _am_discriminator(x: np.ndarray, fs: float,
                      fc: float = APT_SUBCARRIER) -> np.ndarray:
    """APT AM 包络解调（两采样鉴别器）。

        phi     = 2*pi*fc/fs
        y[i]    = sqrt(prev^2 + curr^2 - 2*cos(phi)*prev*curr) / sin(phi)
    对被载波 cos(2πfc·t) 调制的信号，输出即慢变包络（视频幅度）。
    """
    phi = 2.0 * np.pi * fc / fs
    cosphi2 = 2.0 * np.cos(phi)
    sinphi = np.sin(phi)
    prev = np.concatenate([[x[0]], x[:-1]])
    out = np.sqrt(np.maximum(prev * prev + x * x - cosphi2 * prev * x, 0.0)) / sinphi
    out[0] = out[1] if len(out) > 1 else 0.0
    return out


def find_line_starts(video4160: np.ndarray) -> Tuple[np.ndarray, float]:
    """归一化互相关找同步头，返回 (行首样本位置, 网格锁定率)。

    滑动窗与 38 样本 ±1 guard 互相关，最小峰距 = 行距 × 8/10。
    """
    from scipy.signal import find_peaks
    v = video4160 - np.mean(video4160)
    norm = np.std(v) + 1e-12
    tpl = _SYNC_GUARD.copy()
    tpl = tpl - tpl.mean()
    tpl = tpl / (np.linalg.norm(tpl) + 1e-12)
    half = len(_SYNC_GUARD) // 2
    corr = np.convolve(v / norm, tpl[::-1], mode="same")
    # min_distance = samples_per_row*8/10
    min_dist = int(APT_LINE_SAMPLES * 0.8)
    PEAK_H = 4.0      # 真 sync 相关峰 ≈ sqrt(38)≈6.2，噪声 <3.5
    peaks, props = find_peaks(corr, height=PEAK_H, distance=min_dist)
    if len(peaks) == 0:
        return np.array([], dtype=np.int64), 0.0

    n_v = len(video4160)
    mid_lo, mid_hi = int(n_v * 0.15), int(n_v * 0.85)
    mid_idx = [i for i, p in enumerate(peaks) if mid_lo <= p <= mid_hi]
    if mid_idx:
        ai = max(mid_idx, key=lambda i: props["peak_heights"][i])
    else:
        ai = int(np.argmax(props["peak_heights"]))
    anchor_c = peaks[ai]
    if props["peak_heights"][ai] < 5.0:
        return np.array([], dtype=np.int64), 0.0

    edge_guard = APT_LINE_SAMPLES
    centers: List[int] = []
    clean_locked = 0
    expected = 0
    max_k = n_v // APT_LINE_SAMPLES + 2
    for k in range(-max_k, max_k + 1):
        c0 = anchor_c + k * APT_LINE_SAMPLES
        lo, hi = c0 - 20, c0 + 20
        if lo < 0 or hi >= len(corr):
            continue
        seg = corr[lo:hi]
        j = int(np.argmax(seg))
        line_start = c0 - half
        full_line = (line_start >= 0 and line_start + APT_LINE_SAMPLES <= n_v)
        clean_line = (line_start - edge_guard >= 0
                      and line_start + APT_LINE_SAMPLES + edge_guard <= n_v)
        if seg[j] >= PEAK_H:
            centers.append(lo + j)
            if clean_line:
                clean_locked += 1
        if clean_line:
            expected += 1
    centers = sorted(set(centers))
    lock_ratio = clean_locked / max(expected, 1)
    starts = np.array(sorted(c - half for c in centers), dtype=np.int64)
    return starts, lock_ratio


def decode_apt(audio: np.ndarray, sample_rate: float, polarity: int = 1,
               min_lines: int = 4) -> Dict:
    """解码 APT 音频为 A/B 两通道灰度图。

    polarity=1 默认黑=低幅度/白=高幅度；真机若反相传 -1。
    """
    audio = np.asarray(audio, dtype=np.float64)
    audio = audio - np.mean(audio)
    if len(audio) < sample_rate * APT_LINE_SECONDS * min_lines:
        return {"apt_present": False, "reason": "too_short"}

    # 1) 重采样到 work_rate=12480
    work = _resample_to(audio, sample_rate, APT_WORK_RATE)
    # DC 去除低通（cutout=4800Hz，去直流护带）
    h_bp = _kaiser_lowpass(APT_RESAMPLE_CUTOUT, APT_WORK_RATE,
                           APT_RESAMPLE_ATTEN, APT_RESAMPLE_DELTA)
    work = _filtfilt_zero(h_bp, work)

    # 2) AM 包络解调
    video = _am_discriminator(work, APT_WORK_RATE, APT_SUBCARRIER)

    # 3) 解调后低通到 FINAL_RATE/2=2080Hz
    lp_cut = APT_VIDEO_RATE / 2.0
    h_lp = _kaiser_lowpass(lp_cut, APT_WORK_RATE, APT_DEMOD_ATTEN,
                          APT_RESAMPLE_DELTA)
    video = _filtfilt_zero(h_lp, video)

    # 4) 重采样到 FINAL_RATE=4160
    v = _resample_to(video, APT_WORK_RATE, APT_VIDEO_RATE)

    # 5) 行同步
    starts, lock_ratio = find_line_starts(v)
    if len(starts) < min_lines or lock_ratio < 0.6:
        return {"apt_present": False, "lines_aligned": int(len(starts)),
                "lock_ratio": round(float(lock_ratio), 3),
                "reason": "no_sync"}

    rows_a: List[np.ndarray] = []
    rows_b: List[np.ndarray] = []
    used = 0
    for s in starts:
        if s + APT_LINE_SAMPLES > len(v):
            continue
        line = v[s:s + APT_LINE_SAMPLES]
        ga = line[APT_IMG_A_OFFSET:APT_IMG_A_OFFSET + APT_IMAGE_LEN]
        gb = line[APT_IMG_B_OFFSET:APT_IMG_B_OFFSET + APT_IMAGE_LEN]
        rows_a.append(ga)
        rows_b.append(gb)
        used += 1
    if used < min_lines:
        return {"apt_present": False, "lines_aligned": used, "reason": "too_few_lines"}

    def to_gray(rows: np.ndarray) -> np.ndarray:
        # low→0, high→255，clamp 到 [0,255]。这里用 2%~98% 百分位稳健定标。
        g = rows.astype(np.float64).copy()
        g = g * polarity
        lo, hi = np.percentile(g, 2), np.percentile(g, 98)
        if hi - lo > 1e-6:
            g = (g - lo) / (hi - lo)
        return np.clip(g * 255.0, 0, 255).astype(np.uint8)

    img_a = to_gray(np.array(rows_a))
    img_b = to_gray(np.array(rows_b))
    return {
        "apt_present": True,
        "image_a": img_a,
        "image_b": img_b,
        "lines_aligned": used,
        "lock_ratio": round(float(lock_ratio), 3),
        "duration_s": round(len(audio) / sample_rate, 2),
    }


def save_apt_png(result: Dict, prefix: str) -> Dict[str, str]:
    """把解码结果 A/B 两通道并排存为 PNG，返回路径。"""
    from PIL import Image
    import os
    a, b = result["image_a"], result["image_b"]
    h = min(len(a), len(b))
    combo = np.concatenate([a[:h], b[:h]], axis=1)
    path = f"{prefix}_apt.png"
    Image.fromarray(combo, mode="L").save(path)
    paths = {"combo": os.path.abspath(path)}
    for nm, img in (("a", a[:h]), ("b", b[:h])):
        p = f"{prefix}_apt_{nm}.png"
        Image.fromarray(img, mode="L").save(p)
        paths[nm] = os.path.abspath(p)
    return paths
