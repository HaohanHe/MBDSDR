"""NOAA POES APT 气象卫星云图 lite 解码（137MHz 宽带 FM 下行的 2400Hz 副载波）。

对标 aptdec/wxtoimg/atpdec 的最小可用子集，纯 numpy/scipy、可离线复现：
  - APT 帧：每行 0.5s、4160 样本率下 2080 样本，sync A/B（39）+ space（47）+
    image（909）+ telemetry（45），A/B 两通道各一幅；
  - 合成器按标准行结构把测试/云图灰度调制到 2400Hz 副载波（频偏 ±416Hz），供自检；
  - 解码器：带通选通 → Hilbert 解析信号取瞬时频率（视频）→ 重采样到 4160 →
    39 样本 sync 归一化互相关找行首 → 按 2080 行距对齐 → 切 A/B 图像并灰度映射。

不做：通道标定（可见光/红外 wedge 温度反演）、去斜、地图投影、降噪、PRT 温度定标；
极性（黑/白方向）可由 polarity 参数翻转，真机用 telemetry wedge 标定（后续）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

APT_VIDEO_RATE = 4160          # 标准 APT 视频采样率
APT_LINE_SAMPLES = 2080       # 每行样本
APT_LINE_SECONDS = 0.5
APT_SUBCARRIER = 2400.0
APT_DEVIATION = 416.0         # 灰度满量程对应频偏（半峰）
APT_SYNC_LEN = 39
APT_SPACE_LEN = 47
APT_IMAGE_LEN = 909
APT_TELEM_LEN = 45
APT_HALF = APT_SYNC_LEN + APT_SPACE_LEN + APT_IMAGE_LEN + APT_TELEM_LEN  # 1040
APT_IMG_A_OFFSET = APT_SYNC_LEN + APT_SPACE_LEN                          # 86
APT_IMG_B_OFFSET = APT_HALF + APT_SYNC_LEN + APT_SPACE_LEN               # 1126

# 39 样本 APT 同步向量（1040Hz 方波，7 个脉冲），0/1
_SYNC_WORD = np.array([
    0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
    0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
], dtype=np.float64)


# --------------------------------------------------------------------------- #
# 合成（自检信号源）
# --------------------------------------------------------------------------- #
def _gray_to_freq(gray: np.ndarray) -> np.ndarray:
    """0-255 灰度 -> 瞬时频率（黑 2400-416，白 2400+416）。"""
    g = np.clip(gray, 0, 255).astype(np.float64) / 255.0
    return APT_SUBCARRIER + (g - 0.5) * 2.0 * APT_DEVIATION


def _sync_line_levels() -> np.ndarray:
    s = _SYNC_WORD.copy()
    return s * 2.0 - 1.0  # ±1，对应频偏两极


def build_apt_line(image_a_row: np.ndarray, image_b_row: np.ndarray,
                   space_level: float = 128.0,
                   telem_a: Optional[np.ndarray] = None,
                   telem_b: Optional[np.ndarray] = None) -> np.ndarray:
    """构造一行 2080 个灰度采样（A/B 通道）。"""
    a = np.clip(image_a_row, 0, 255).astype(np.float64)
    b = np.clip(image_b_row, 0, 255).astype(np.float64)
    if len(a) != APT_IMAGE_LEN or len(b) != APT_IMAGE_LEN:
        raise ValueError(f"图像行需 {APT_IMAGE_LEN} 像素")
    sync = (_SYNC_WORD * 255.0)  # 0/255 两极
    space = np.full(APT_SPACE_LEN, space_level)
    ta = telem_a if telem_a is not None else np.linspace(0, 255, APT_TELEM_LEN)
    tb = telem_b if telem_b is not None else np.linspace(0, 255, APT_TELEM_LEN)
    line = np.concatenate([
        sync, space, a, ta,
        sync, space, b, tb,
    ]).astype(np.float64)
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
    """把 A/B 图像逐行调制为 2400Hz FM 副载波实音频（音频率 fs，默认 24kHz）。

    4160 是解调后的视频（像素）率，无法表示 2400Hz 副载波，故在音频率 fs 上：
    把每像素瞬时频率零阶保持 fs/4160 个样本，再做 FM 相位积分。
    """
    if rng is None:
        rng = np.random.default_rng(0)
    if fs < 8000:
        raise ValueError("音频采样率需 ≥8kHz 以承载 2400Hz 副载波")
    n_lines = min(len(image_a), len(image_b))
    gray = np.concatenate([build_apt_line(image_a[i], image_b[i])
                           for i in range(n_lines)])
    freq_video = _gray_to_freq(gray)                 # 4160 视频率，每样本一像素
    # 零阶保持上采样瞬时频率到音频率
    n_aud = int(round(len(freq_video) * fs / APT_VIDEO_RATE))
    px_idx = np.clip((np.arange(n_aud) * APT_VIDEO_RATE / fs).astype(np.int64),
                     0, len(freq_video) - 1)
    freq_aud = freq_video[px_idx]
    phase = 2.0 * np.pi * np.cumsum(freq_aud) / fs
    audio = np.cos(phase)
    if noise_std:
        audio = audio + noise_std * rng.standard_normal(len(audio))
    peak = np.max(np.abs(audio)) or 1.0
    return (audio / peak * 0.9).astype(np.float64)


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def _instant_frequency_video(audio: np.ndarray, fs: float) -> np.ndarray:
    """带通选通 2400 副载波，Hilbert 解析信号取瞬时频率，返回视频（频偏 Hz）。"""
    from scipy.signal import butter, sosfiltfilt, hilbert
    nyq = fs / 2.0
    lo = max(300.0, (APT_SUBCARRIER - 1100.0)) / nyq
    hi = min(0.99, (APT_SUBCARRIER + 1100.0) / nyq)
    sos = butter(4, [lo, hi], btype="band", output="sos")
    band = sosfiltfilt(sos, audio)
    z = hilbert(band)
    inst = np.angle(z[1:] * np.conj(z[:-1])) / (2.0 * np.pi) * fs
    return inst


def _resample_video(video: np.ndarray, fs: float) -> np.ndarray:
    if int(round(fs)) == APT_VIDEO_RATE:
        return video
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(round(fs)), APT_VIDEO_RATE)
    up = APT_VIDEO_RATE // g
    down = int(round(fs)) // g
    return resample_poly(video, up, down)


def _sync_template() -> np.ndarray:
    """sync 对应的频偏模板（两极 ±APT_DEVIATION），去均值归一化。"""
    t = _SYNC_WORD * 2.0 - 1.0
    t = t - t.mean()
    return t / (np.linalg.norm(t) + 1e-12)


def find_line_starts(video4160: np.ndarray) -> Tuple[np.ndarray, float]:
    """归一化互相关找同步头，返回 (行首样本位置, 网格锁定率)。

    互相关峰对齐 sync 模板中心，故先收集各线 sync 中心再统一减去半个 sync 长度。
    用绝对相关峰高（白噪声局部相关约 2~3.5，真 sync 约 6）与 2080 行距网格锁定率
    双重门限，避免纯噪声按网格“填”出假行。
    """
    from scipy.signal import find_peaks
    v = video4160 - np.mean(video4160)
    norm = np.std(v) + 1e-12
    tpl = _sync_template()
    half = len(_SYNC_WORD) // 2
    corr = np.convolve(v / norm, tpl[::-1], mode="same")
    min_dist = int(APT_LINE_SAMPLES * 0.85)
    PEAK_H = 4.5      # 真 sync ~6，噪声 <3.5
    peaks, props = find_peaks(corr, height=PEAK_H, distance=min_dist)
    if len(peaks) == 0:
        return np.array([], dtype=np.int64), 0.0
    anchor_c = peaks[np.argmax(props["peak_heights"])]  # 锚线 sync 中心
    if props["peak_heights"].max() < 5.0:
        return np.array([], dtype=np.int64), 0.0
    centers = []
    expected = 0
    max_k = len(video4160) // APT_LINE_SAMPLES + 2
    for k in range(-max_k, max_k + 1):
        c0 = anchor_c + k * APT_LINE_SAMPLES
        lo, hi = c0 - 20, c0 + 20
        if lo < 0 or hi >= len(corr):
            continue
        expected += 1
        seg = corr[lo:hi]
        j = int(np.argmax(seg))
        if seg[j] >= PEAK_H:
            centers.append(lo + j)
    centers = sorted(set(centers))
    lock_ratio = len(centers) / max(expected, 1)
    starts = np.array(sorted(c - half for c in centers), dtype=np.int64)
    return starts, lock_ratio


def decode_apt(audio: np.ndarray, sample_rate: float, polarity: int = 1,
               min_lines: int = 4) -> Dict:
    """解码 APT 音频为 A/B 两通道灰度图。

    polarity=1 默认黑=低频/白=高频；真机若反相传 -1。返回图像、行数、对齐质量。
    """
    audio = np.asarray(audio, dtype=np.float64)
    audio = audio - np.mean(audio)
    if len(audio) < sample_rate * APT_LINE_SECONDS * min_lines:
        return {"apt_present": False, "reason": "too_short"}
    video = _instant_frequency_video(audio, sample_rate)
    v = _resample_video(video, sample_rate)
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
        # 频偏 -> 灰度，按标准 ±416 映射后做稳健线性拉伸
        g = (rows - APT_SUBCARRIER) / (2.0 * APT_DEVIATION) + 0.5
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
