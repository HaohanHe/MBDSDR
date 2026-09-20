"""
SSTV 慢扫描电视解码器（Martin M1 / Scottie S1 / Robot 36）。

从 wav 音频文件解码 SSTV 图像，输出 PNG。
支持模式：Martin M1 (320x256), Scottie S1 (320x256), Robot 36 (320x240)。

原理：
1. 读取 wav，重采样到 48000Hz 单声道
2. 过零率估算瞬时频率
3. 检测 VIS 头（1900Hz break + 1200Hz 引导 + VIS 码）
4. 按模式时序逐行采样 RGB
5. 频率 1500Hz=黑，2300Hz=白，线性映射
"""

import numpy as np
import struct
import os
from typing import Dict, Any, Optional, Tuple

try:
    from scipy.io import wavfile
    from scipy.signal import resample
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# SSTV 模式定义（时序单位：毫秒，频率单位：Hz）
SSTV_MODES = {
    "Martin M1": {
        "vis_code": 0x2C,  # pysstv 权威 VIS（旧表误写 0x5C）
        "width": 320,
        "height": 256,
        "sync_freq": 1200,
        "sync_ms": 4.862,
        "sep_freq": 1500,
        "sep_ms": 0.572,
        "channel_order": ["G", "B", "R"],  # pysstv MartinM1 COLOR_SEQ=(green,blue,red)
        "pixel_ms": 0.4576,  # SCAN 146.432ms / 320，单通道像素时钟
    },
    "Scottie S1": {
        "vis_code": 0x3C,
        "width": 320,
        "height": 256,
        "sync_freq": 1200,
        "sync_ms": 9.0,
        "sep_freq": 1500,
        "sep_ms": 1.5,
        "channel_order": ["R", "G", "B"],
        "pixel_ms": 0.2308,  # 73.83ms / 320
    },
    "Robot 36": {
        "vis_code": 0x08,
        "width": 320,
        "height": 240,
        "sync_freq": 1200,
        "sync_ms": 9.0,
        "sep_freq": 1500,
        "sep_ms": 1.0,
        "channel_order": ["Y", "R-Y", "B-Y"],
        "pixel_ms": 0.2604,  # 83.33ms / 320
    },
    # ---- PD 系列（G3PLX，两行组：Y0 / Cb(两行平均) / Cr(两行平均) / Y1）----
    # 通用：SYNC=20ms、PORCH=2.08ms、无通道间隔；色度为相邻两行平均。
    # 参数来自 pysstv 权威实现。像素时钟解码时按实测组周期反推，不硬编码。
    "PD90":  {"vis_code": 0x63, "width": 320, "height": 256, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.532, "family": "pd"},
    "PD120": {"vis_code": 0x5F, "width": 640, "height": 496, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.19, "family": "pd"},
    "PD160": {"vis_code": 0x62, "width": 512, "height": 400, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.382, "family": "pd"},
    "PD180": {"vis_code": 0x60, "width": 640, "height": 496, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.286, "family": "pd"},
    "PD240": {"vis_code": 0x61, "width": 640, "height": 496, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.382, "family": "pd"},
    "PD290": {"vis_code": 0x5E, "width": 800, "height": 616, "sync_ms": 20.0,
              "porch_ms": 2.08, "pixel_ms": 0.286, "family": "pd"},
}

TARGET_SAMPLE_RATE = 48000


def _read_wav(file_path: str) -> Tuple[np.ndarray, int]:
    """读取 wav 文件，返回 (单声道浮点数组, 采样率)。"""
    if not HAS_SCIPY:
        # fallback: 手动解析 16-bit PCM wav
        with open(file_path, "rb") as f:
            riff = f.read(4)
            if riff != b"RIFF":
                raise ValueError("不是有效的 WAV 文件")
            f.read(4)  # file size
            f.read(4)  # WAVE
            f.read(4)  # fmt 
            f.read(4)  # chunk size
            audio_format = struct.unpack("<H", f.read(2))[0]
            channels = struct.unpack("<H", f.read(2))[0]
            sample_rate = struct.unpack("<I", f.read(4))[0]
            f.read(6)  # byte rate + block align
            bits_per_sample = struct.unpack("<H", f.read(2))[0]
            # 找 data chunk
            while True:
                chunk_id = f.read(4)
                if chunk_id == b"data":
                    break
                chunk_size = struct.unpack("<I", f.read(4))[0]
                f.read(chunk_size)
            data_size = struct.unpack("<I", f.read(4))[0]
            raw = f.read(data_size)
        if bits_per_sample == 16:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif bits_per_sample == 8:
            samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
        else:
            raise ValueError(f"不支持的位深: {bits_per_sample}")
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        return samples, sample_rate

    sample_rate, data = wavfile.read(file_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype == np.int16:
        samples = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int8:
        samples = data.astype(np.float32) / 128.0
    else:
        samples = data.astype(np.float32)
    return samples, sample_rate


def _resample_if_needed(samples: np.ndarray, orig_rate: int) -> np.ndarray:
    """重采样到 48000Hz。"""
    if orig_rate == TARGET_SAMPLE_RATE:
        return samples
    if HAS_SCIPY:
        num_samples = int(len(samples) * TARGET_SAMPLE_RATE / orig_rate)
        return resample(samples, num_samples).astype(np.float32)
    # 简单线性重采样
    ratio = TARGET_SAMPLE_RATE / orig_rate
    indices = np.arange(int(len(samples) * ratio)) / ratio
    indices = np.clip(indices, 0, len(samples) - 1).astype(np.int32)
    return samples[indices]


def _instantaneous_frequency(samples: np.ndarray, sample_rate: int, window: int = 64) -> np.ndarray:
    """用过零率+抛物线插值估算瞬时频率，比希尔伯特变换更稳定。

    对 SSTV 1200-2300Hz 正弦波，过零率法精度更高、噪声更小。
    """
    n = len(samples)
    if n < 4:
        return np.zeros(n, dtype=np.float32)

    # 去直流（避免直流偏移导致过零计数错误）
    samples = samples - np.mean(samples)

    # 找过零点（符号变化）
    signs = np.sign(samples)
    signs[signs == 0] = 1  # 零值视为正
    zero_crossings = np.where(np.diff(signs) != 0)[0]

    if len(zero_crossings) < 2:
        return np.full(n, 1500.0, dtype=np.float32)  # 默认中间频率

    # 计算每个过零点的精确位置（线性插值）
    cross_positions = []
    for zc in zero_crossings:
        if zc + 1 < n:
            y0, y1 = samples[zc], samples[zc + 1]
            if y1 != y0:
                frac = -y0 / (y1 - y0)
                cross_positions.append(zc + frac)
            else:
                cross_positions.append(float(zc))
        else:
            cross_positions.append(float(zc))
    cross_positions = np.array(cross_positions)

    # 计算相邻过零点之间的频率（两个过零点 = 半个周期）
    inst_freq = np.full(n, 1500.0, dtype=np.float32)
    if len(cross_positions) >= 2:
        periods = np.diff(cross_positions) * 2  # 半周期 → 全周期（样本数）
        freqs = sample_rate / np.clip(periods, 1, sample_rate)
        # 将频率分配到对应区间
        for i in range(len(freqs)):
            start = int(cross_positions[i])
            end = int(cross_positions[i + 1]) if i + 1 < len(cross_positions) else n
            inst_freq[start:end] = freqs[i]

    # 滑动中值滤波（去除尖峰噪声），比均值滤波更保边缘
    if len(inst_freq) > window and window > 1:
        from scipy.ndimage import median_filter as _median
        inst_freq = _median(inst_freq, size=min(window, 31))

    return inst_freq


def _freq_to_pixel(freq: float) -> int:
    """频率转像素值：1500Hz=0(黑), 2300Hz=255(白)。"""
    val = int((freq - 1500.0) / 800.0 * 255.0)
    return max(0, min(255, val))


def _detect_vis_header(freq: np.ndarray, sample_rate: int) -> Tuple[Optional[int], int]:
    """检测 VIS 头，返回 (vis_code, 数据起始样本索引)。"""
    # 找 1900Hz break（VIS 头标志）
    threshold_break = 1800
    threshold_1200 = (1100, 1300)
    threshold_1300 = (1250, 1350)
    threshold_1100 = (1050, 1150)

    # 简化：扫描找连续的 1900Hz 区域
    in_break = False
    break_start = 0
    for i in range(len(freq)):
        if freq[i] > threshold_break and not in_break:
            in_break = True
            break_start = i
        elif freq[i] < threshold_break and in_break:
            break_len = i - break_start
            if break_len > sample_rate * 0.005:  # 至少 5ms
                # 找到 break，后面是 1200Hz 引导 + VIS 码
                vis_start = i
                # 跳过 1200Hz 引导（约 300ms）
                vis_code_start = vis_start + int(sample_rate * 0.35)
                # 解码 VIS 码（30ms/bit，起始位+7数据+结束位）
                bit_samples = int(sample_rate * 0.030)
                vis_code = 0
                for bit in range(7):
                    bit_start = vis_code_start + (bit + 1) * bit_samples
                    if bit_start + bit_samples // 2 < len(freq):
                        bit_freq = np.mean(freq[bit_start:bit_start + bit_samples // 2])
                        if threshold_1300[0] < bit_freq < threshold_1300[1]:
                            vis_code |= (1 << bit)
                return vis_code, vis_code_start + 9 * bit_samples

    return None, 0


# ---------------------------------------------------------------------------
# 数据驱动的制式识别（不依赖可能解错的 VIS 码）
#
# 真实 over-the-air 录音里 VIS 头常因切入时机、噪声、频偏而解错（实测一份
# Robot36 录音 VIS 解出非标准码 121，旧逻辑静默回退 Martin M1 导致斜条纹）。
# 因此制式判定以信号本身可测的物理特征为准：1200Hz 行同步的脉宽与间隔、
# 一个同步周期内的通道段结构；VIS 仅作辅助校验。
# ---------------------------------------------------------------------------
def _find_sync_markers(freq: np.ndarray, sr: int, data_start: int,
                       sync_ms_nom: float = 9.0,
                       band_hz: float = 110.0):
    """检测 1200Hz 同步脉冲，做周期清洗后返回起点数组及脉宽/间隔中位值。

    Returns:
        (markers np.ndarray[int], pulse_ms float, gap_ms float)
    """
    fr = np.where(np.isfinite(freq), freq, 1500.0)
    band = np.abs(fr - 1200.0) <= band_hz
    band[:data_start] = False
    min_len = int(0.6 * sr * sync_ms_nom / 1000.0)
    raw: list = []
    widths: list = []
    try:
        from scipy import ndimage as _ndi
        labeled = _ndi.label(band)[0]
        for k in range(1, int(labeled.max()) + 1):
            idx = np.where(labeled == k)[0]
            if len(idx) >= min_len:
                raw.append(int(idx[0]))
                widths.append(len(idx) / sr * 1000.0)
    except Exception:
        i = data_start
        while i < len(fr):
            if band[i]:
                j = i
                while j < len(fr) and band[j]:
                    j += 1
                if j - i >= min_len:
                    raw.append(i)
                    widths.append((j - i) / sr * 1000.0)
                i = j
            else:
                i += 1
    if len(raw) < 4:
        return np.array(raw, dtype=int), (
            float(np.median(widths)) if widths else 0.0), 0.0

    raw_arr = np.array(raw)
    gaps = np.diff(raw_arr) / sr * 1000.0
    # 候选间隔的众数（在合理 100~1200ms 内取中位）
    valid = gaps[(gaps > 100.0) & (gaps < 1200.0)]
    if len(valid) == 0:
        return raw_arr, float(np.median(widths)), 0.0
    period = float(np.median(valid))
    win = int(0.015 * sr)
    period_samp = period * sr / 1000.0
    # 首锚点：其后 1/2/3 个周期都有候选
    first = None
    for c in raw_arr:
        hits = sum(
            1 for k in (1, 2, 3)
            if np.any(np.abs(raw_arr - (c + k * period_samp)) <= win)
        )
        if hits >= 3:
            first = int(c)
            break
    if first is None:
        first = int(raw_arr[0])
    markers: list = []
    g = 0
    while True:
        exp = first + g * period_samp
        if exp > len(fr):
            break
        m = raw_arr[np.abs(raw_arr - exp) <= win]
        markers.append(int(m[np.argmin(np.abs(m - exp))]) if len(m) else int(exp))
        g += 1
        if g > 4096:
            break
    return np.array(markers, dtype=int), float(np.median(widths)), period


def _identify_sstv_mode(freq: np.ndarray, sr: int, data_start: int,
                        vis_code: Optional[int]):
    """基于同步时序特征识别 SSTV 制式。

    返回 (mode_name, info_dict)。Robot36 同步脉宽约 9ms，存在两种发送变体：
    逐行式（pysstv，每 150ms 一个同步）与组首式（多数空中实现，每两行一个
    同步，约 288~300ms）。Martin/Scottie 家族同步脉宽约 4.862ms 或 9ms、
    间隔显著不同。
    """
    # 用较松的 4.5ms 最小脉宽，一次把 4.862ms 与 9ms 同步都抓到
    fr = np.where(np.isfinite(freq), freq, 1500.0)
    band = np.abs(fr - 1200.0) <= 110.0
    band[:data_start] = False
    min_len = int(0.6 * sr * 4.5 / 1000.0)
    raw: list = []
    widths: list = []
    try:
        from scipy import ndimage as _ndi
        labeled = _ndi.label(band)[0]
        for k in range(1, int(labeled.max()) + 1):
            idx = np.where(labeled == k)[0]
            if len(idx) >= min_len:
                raw.append(int(idx[0]))
                widths.append(len(idx) / sr * 1000.0)
    except Exception:
        pass
    if len(raw) < 4:
        return "unknown", {"reason": "no-sync-markers", "vis": vis_code,
                           "n_markers": len(raw)}
    raw_arr = np.array(raw)
    gaps = np.diff(raw_arr) / sr * 1000.0
    valid = gaps[(gaps > 100.0) & (gaps < 1200.0)]
    pulse_ms = float(np.median(widths)) if widths else 0.0
    period_ms = float(np.median(valid)) if len(valid) else 0.0
    # 噪声门限：真实 SSTV 同步周期高度一致（变异系数很小）；纯噪声/弱信号里
    # 随机触发的"1200Hz 段"周期杂乱。标记过少或周期不稳时判 unknown，不瞎猜制式。
    n_markers = len(raw)
    period_cv = float(np.std(valid) / period_ms) if (
        len(valid) >= 6 and period_ms > 0) else 1.0
    info = {"pulse_ms": pulse_ms, "period_ms": period_ms, "vis": vis_code,
            "n_markers": n_markers, "period_cv": round(period_cv, 3)}
    if n_markers < 8 or period_cv > 0.35:
        return "unknown", {**info, "reason": "weak-or-noise"}

    # Robot36：9ms 同步，周期 ~150ms（逐行）或 ~288-300ms（组首两行）
    if pulse_ms >= 7.0 and (130.0 <= period_ms <= 175.0):
        return "Robot 36", {**info, "robot_layout": "per_line"}
    if pulse_ms >= 7.0 and (270.0 <= period_ms <= 330.0):
        return "Robot 36", {**info, "robot_layout": "grouped"}
    # PD 系列：SYNC 约 20ms（明显长于 Robot 9ms），组周期 450~1050ms。
    # 按标称组周期最近邻细分型号（Y0+Cb+Cr+Y1，WIDTH/HEIGHT 随型号）。
    if pulse_ms >= 15.0 and (450.0 <= period_ms <= 1050.0):
        pd_period = {"PD120": 508.0, "PD90": 703.0, "PD180": 754.0,
                     "PD160": 804.0, "PD290": 937.0, "PD240": 1000.0}
        best = min(pd_period, key=lambda k: abs(pd_period[k] - period_ms))
        return best, {**info, "family": "pd", "pd_ref_period": pd_period[best]}
    # Martin M1：4.862ms 同步、~446ms；Martin M2 ~227ms
    if pulse_ms < 7.0 and 400.0 <= period_ms <= 480.0:
        return "Martin M1", info
    if pulse_ms < 7.0 and 200.0 <= period_ms <= 250.0:
        return "Martin M2", info
    # Scottie：9ms 同步但 sync 位于红通道前，周期 ~278(S2)/~427(S1)
    if pulse_ms >= 7.0 and 260.0 <= period_ms <= 295.0:
        return "Scottie S2", info
    if pulse_ms >= 7.0 and 400.0 <= period_ms <= 440.0:
        return "Scottie S1", info
    # 兜底：VIS 能对上才用 VIS（VIS 在合成/真实信号上都曾解错，仅作弱兜底）
    if vis_code is not None and vis_code in (8, 44, 40, 60, 56):
        for name, mdef in SSTV_MODES.items():
            if mdef.get("vis_code") == vis_code:
                return name, info
    return "unknown", {**info, "reason": "no-match"}


def _freq_to_pixel_series(fr: np.ndarray, pos: int, px_samples: float,
                          n: int, half_win: float) -> np.ndarray:
    """按浮点像素时钟读取 n 个像素，取每像素中心窗口均值并映射到 0-255。

    用累积和做 O(n) 向量化滑动窗口均值，避免逐像素切片（76800 次 mean）
    带来的数十秒级解码耗时。
    """
    h = max(1, int(round(px_samples * half_win)))
    centers = pos + (np.arange(n) + 0.5) * px_samples
    c = np.round(centers).astype(np.int64)
    a = np.clip(c - h, 0, len(fr) - 1)
    b = np.clip(c + h, 1, len(fr))
    csum = np.concatenate(([0.0], np.cumsum(fr.astype(np.float64))))
    winsum = csum[b] - csum[a]
    length = np.clip(b - a, 1, None)
    mean_f = winsum / length
    return np.clip((mean_f - 1500.0) / 800.0 * 255.0, 0, 255)


def _ycbcr_to_rgb(Y: np.ndarray, Cb: np.ndarray, Cr: np.ndarray):
    R = np.clip(Y + 1.402 * (Cr - 128.0), 0, 255)
    G = np.clip(Y - 0.344136 * (Cb - 128.0) - 0.714136 * (Cr - 128.0), 0, 255)
    B = np.clip(Y + 1.772 * (Cb - 128.0), 0, 255)
    return R, G, B


def _decode_robot36(freq: np.ndarray, sr: int, data_start: int,
                    layout: str = "grouped") -> Dict[str, Any]:
    """数据驱动解码 Robot36（320x240，YUV 4:2:0 风格两行一组）。

    像素时钟不硬编码，而由实测同步周期反推（发射/录音链路时基偏差可达 4%），
    以消除累积水平错位。兼容逐行式（~150ms/同步）与组首式（~300ms/同步）。
    经 pysstv 合成闭环验证：偶数行色差为 Cb(B-Y)、奇数行为 Cr(R-Y)。

    色差直流恢复：真实 over-the-air 录音常削波/带频偏，使 Cb/Cr 中值偏离
    中性电平 128 而整幅偏色；按全帧色差中值对齐 128 校正（自然图像色差中值
    本应在中性附近，干净信号 DC≈0 不受影响）。
    """
    width, height = 320, 240
    markers, pulse_ms, period_ms = _find_sync_markers(
        freq, sr, data_start, sync_ms_nom=9.0)
    if len(markers) < 2 or period_ms <= 0:
        return {"success": False, "error": "Robot36: 同步标记不足"}
    if layout == "auto":
        layout = "per_line" if period_ms < 200.0 else "grouped"

    fr = np.where(np.isfinite(freq), freq, 1500.0)
    sync_ms, porch_ms, gap_ms = 9.0, 3.0, 6.0  # gap 含 4.5ms 色差间隔+1.5ms porch
    image = np.zeros((height, width, 3), dtype=np.uint8)

    # 先收集每行的 Y 与对应 Cb/Cr，再做全帧色差直流恢复后统一着色
    row_y: Dict[int, np.ndarray] = {}
    row_cb: Dict[int, np.ndarray] = {}
    row_cr: Dict[int, np.ndarray] = {}

    if layout == "per_line":
        # 每行：sync+porch, Y(88), gap, UV(44)；周期约 150ms
        y_plus_uv = (period_ms - sync_ms - porch_ms - gap_ms)
        y_scan = max(1.0, y_plus_uv * 2.0 / 3.0)
        uv_scan = max(1.0, y_plus_uv / 3.0)
        ypx = sr * y_scan / width / 1000.0
        uvpx = sr * uv_scan / width / 1000.0
        o_y = int((sync_ms + porch_ms) * sr / 1000.0)
        o_uv = int((sync_ms + porch_ms + y_scan + gap_ms) * sr / 1000.0)
        line_uv: Dict[int, np.ndarray] = {}
        n_lines = 0
        for li, s in enumerate(markers):
            if li >= height or s + o_uv + int(uv_scan * sr / 1000.0) >= len(fr):
                break
            row_y[li] = _freq_to_pixel_series(fr, s + o_y, ypx, width, 0.4)
            line_uv[li] = _freq_to_pixel_series(fr, s + o_uv, uvpx, width, 0.4)
            n_lines = li + 1
        # 偶数行色差=Cb，奇数行=Cr；两行组成一对共享色度
        for li in range(n_lines):
            if li % 2 == 0:
                row_cb[li] = line_uv[li]
                row_cr[li] = line_uv.get(li + 1, line_uv[li])
            else:
                row_cr[li] = line_uv[li]
                row_cb[li] = line_uv.get(li - 1, line_uv[li])
    else:
        # 组首式：每同步周期含两行 Y0,UV0(Cb),Y1,UV1(Cr)，周期约 300ms
        y_plus_uv = (period_ms - sync_ms - porch_ms) / 2.0 - gap_ms
        y_scan = max(1.0, y_plus_uv * 2.0 / 3.0)
        uv_scan = max(1.0, y_plus_uv / 3.0)
        ypx = sr * y_scan / width / 1000.0
        uvpx = sr * uv_scan / width / 1000.0
        y_samp = int(y_scan * sr / 1000.0)
        uv_samp = int(uv_scan * sr / 1000.0)
        gap_samp = int(gap_ms * sr / 1000.0)
        o_y0 = int((sync_ms + porch_ms) * sr / 1000.0)
        o_cb = o_y0 + y_samp + gap_samp
        o_y1 = o_cb + uv_samp
        o_cr = o_y1 + y_samp + gap_samp
        r0 = 0
        for s in markers:
            if r0 + 1 >= height or s + o_cr + uv_samp >= len(fr):
                break
            Y0 = _freq_to_pixel_series(fr, s + o_y0, ypx, width, 0.4)
            Cb = _freq_to_pixel_series(fr, s + o_cb, uvpx, width, 0.4)
            Y1 = _freq_to_pixel_series(fr, s + o_y1, ypx, width, 0.4)
            Cr = _freq_to_pixel_series(fr, s + o_cr, uvpx, width, 0.4)
            row_y[r0], row_cb[r0], row_cr[r0] = Y0, Cb, Cr
            row_y[r0 + 1], row_cb[r0 + 1], row_cr[r0 + 1] = Y1, Cb, Cr
            r0 += 2

    if not row_y:
        return {"success": False, "error": "Robot36: 未解码出任何行"}

    # 全帧色差直流恢复（中值对齐中性 128）
    cb_all = np.concatenate([row_cb[r] for r in sorted(row_cb)])
    cr_all = np.concatenate([row_cr[r] for r in sorted(row_cr)])
    cb_dc = float(np.median(cb_all)) - 128.0
    cr_dc = float(np.median(cr_all)) - 128.0

    for r in sorted(row_y):
        Cb = row_cb[r] - cb_dc
        Cr = row_cr[r] - cr_dc
        R, G, B = _ycbcr_to_rgb(row_y[r], Cb, Cr)
        image[r, :, 0] = R
        image[r, :, 1] = G
        image[r, :, 2] = B

    return {
        "success": True,
        "mode": "Robot 36",
        "width": width,
        "height": height,
        "layout": layout,
        "period_ms": round(period_ms, 2),
        "pulse_ms": round(pulse_ms, 2),
        "cb_dc": round(cb_dc, 1),
        "cr_dc": round(cr_dc, 1),
        "rows_decoded": int(np.sum(np.any(image > 0, axis=(1, 2)))),
        "image": image,
    }


def _decode_pd(freq: np.ndarray, sr: int, data_start: int,
               mode: str = "PD120") -> Dict[str, Any]:
    """数据驱动解码 PD 系列（G3PLX）。

    一组两行：SYNC(20ms) + PORCH(2.08ms) + Y0(width) + Cb(两行平均)
    + Cr(两行平均) + Y1(width)，无通道间隔。色度为相邻两行共享平均。
    像素时钟由实测组周期反推（不硬编码 PIXEL），消除时基偏差累积错位。
    经 pysstv PD 合成闭环验证。
    """
    mdef = SSTV_MODES[mode]
    width, height = mdef["width"], mdef["height"]
    sync_ms = mdef.get("sync_ms", 20.0)
    porch_ms = mdef.get("porch_ms", 2.08)

    markers, pulse_ms, period_ms = _find_sync_markers(
        freq, sr, data_start, sync_ms_nom=20.0)
    if len(markers) < 2 or period_ms <= 0:
        return {"success": False, "error": f"{mode}: 同步标记不足"}

    fr = np.where(np.isfinite(freq), freq, 1500.0)
    # 反推像素时钟：组周期 = sync + porch + 4*width*px
    px_ms = max(0.05, (period_ms - sync_ms - porch_ms) / (4.0 * width))
    px_samples = sr * px_ms / 1000.0
    image = np.zeros((height, width, 3), dtype=np.uint8)
    row_y: Dict[int, np.ndarray] = {}
    row_cb: Dict[int, np.ndarray] = {}
    row_cr: Dict[int, np.ndarray] = {}

    o_y0 = int((sync_ms + porch_ms) * sr / 1000.0)
    seg = int(width * px_samples)
    o_cb = o_y0 + seg
    o_cr = o_cb + seg
    o_y1 = o_cr + seg

    r0 = 0
    for s in markers:
        if r0 + 1 >= height or s + o_y1 + seg >= len(fr):
            break
        Y0 = _freq_to_pixel_series(fr, s + o_y0, px_samples, width, 0.4)
        # pysstv PD 顺序：Y0 / Cr(两行平均,p[2]) / Cb(两行平均,p[1]) / Y1
        # （PIL YCbCr=(Y,Cb,Cr)，index2=Cr、index1=Cb）——勿写反，否则红蓝互换
        Cr = _freq_to_pixel_series(fr, s + o_cb, px_samples, width, 0.4)
        Cb = _freq_to_pixel_series(fr, s + o_cr, px_samples, width, 0.4)
        Y1 = _freq_to_pixel_series(fr, s + o_y1, px_samples, width, 0.4)
        row_y[r0], row_cb[r0], row_cr[r0] = Y0, Cb, Cr
        row_y[r0 + 1], row_cb[r0 + 1], row_cr[r0 + 1] = Y1, Cb, Cr
        r0 += 2

    if not row_y:
        return {"success": False, "error": f"{mode}: 未解码出任何行"}

    cb_all = np.concatenate([row_cb[r] for r in sorted(row_cb)])
    cr_all = np.concatenate([row_cr[r] for r in sorted(row_cr)])
    cb_dc = float(np.median(cb_all)) - 128.0
    cr_dc = float(np.median(cr_all)) - 128.0

    for r in sorted(row_y):
        R, G, B = _ycbcr_to_rgb(row_y[r], row_cb[r] - cb_dc, row_cr[r] - cr_dc)
        image[r, :, 0] = R
        image[r, :, 1] = G
        image[r, :, 2] = B

    return {
        "success": True, "mode": mode, "width": width, "height": height,
        "period_ms": round(period_ms, 2), "pulse_ms": round(pulse_ms, 2),
        "px_ms": round(px_ms, 3), "cb_dc": round(cb_dc, 1),
        "cr_dc": round(cr_dc, 1),
        "rows_decoded": int(np.sum(np.any(image > 0, axis=(1, 2)))),
        "image": image,
    }


def decode_sstv(file_path: str, output_path: Optional[str] = None,
                 mode: str = "auto") -> Dict[str, Any]:
    """
    解码 SSTV 图像。

    Args:
        file_path: 输入 wav 文件路径
        output_path: 输出 PNG 路径（默认同目录同名 .png）
        mode: 模式 ("auto" 自动检测, 或 "Martin M1"/"Scottie S1"/"Robot 36")

    Returns:
        dict: {success, mode, width, height, output_path, note}
    """
    if not os.path.exists(file_path):
        return {"error": f"文件不存在: {file_path}"}

    try:
        samples, orig_rate = _read_wav(file_path)
    except Exception as e:
        return {"error": f"WAV 读取失败: {e}"}

    samples = _resample_if_needed(samples, orig_rate)
    sr = TARGET_SAMPLE_RATE

    if len(samples) < sr * 2:  # 至少 2 秒
        return {"error": "音频太短，无法解码 SSTV（至少需要 2 秒）"}

    # 计算瞬时频率
    freq = _instantaneous_frequency(samples, sr)

    # 检测模式。VIS 头同时给出图像数据起点 data_start，无论是否自动选模式
    # 都需要它来跳过 leader/校准脉冲，否则显式指定模式时会从 VIS 头里误锁同步。
    detected_mode = mode
    vis_code, data_start = _detect_vis_header(freq, sr)
    id_info: Dict[str, Any] = {}
    if mode == "auto":
        detected_mode, id_info = _identify_sstv_mode(freq, sr, data_start, vis_code)

    # Robot36 走数据驱动的两行组解码器（兼容逐行/组首两种发送变体）
    if detected_mode in ("Robot 36", "Robot36"):
        layout = id_info.get("robot_layout", "auto")
        res = _decode_robot36(freq, sr, data_start, layout=layout)
        if not res.get("success"):
            return {"error": res.get("error", "Robot36 解码失败")}
        image = res.pop("image")
        if output_path is None:
            output_path = os.path.splitext(file_path)[0] + "_sstv.png"
        if HAS_PIL:
            Image.fromarray(image).save(output_path)
        else:
            np.save(output_path + ".npy", image)
            output_path = output_path + ".npy"
        return {
            "success": True,
            "mode": "Robot 36",
            "width": res["width"],
            "height": res["height"],
            "layout": res["layout"],
            "period_ms": res["period_ms"],
            "rows_decoded": res["rows_decoded"],
            "output_path": output_path,
            "identification": {
                "method": "timing",
                "vis_raw": vis_code,
                "pulse_ms": res["pulse_ms"],
                "period_ms": res["period_ms"],
            },
        }

    # PD 系列走数据驱动两行组解码器（Y0/Cb/Cr/Y1，色度两行平均）
    if SSTV_MODES.get(detected_mode, {}).get("family") == "pd":
        res = _decode_pd(freq, sr, data_start, mode=detected_mode)
        if not res.get("success"):
            return {"error": res.get("error", "PD 解码失败")}
        image = res.pop("image")
        if output_path is None:
            output_path = os.path.splitext(file_path)[0] + "_sstv.png"
        if HAS_PIL:
            Image.fromarray(image).save(output_path)
        else:
            np.save(output_path + ".npy", image)
            output_path = output_path + ".npy"
        return {
            "success": True,
            "mode": res["mode"],
            "width": res["width"],
            "height": res["height"],
            "period_ms": res["period_ms"],
            "px_ms": res["px_ms"],
            "rows_decoded": res["rows_decoded"],
            "output_path": output_path,
            "identification": {
                "method": "timing", "vis_raw": vis_code,
                "pulse_ms": res["pulse_ms"], "period_ms": res["period_ms"],
            },
        }

    if mode == "auto" and detected_mode not in SSTV_MODES:
        # 自动识别未能可靠判定（噪声/弱信号/非SSTV）：诚实返回，不兜底出垃圾图
        return {
            "success": False,
            "mode": "unknown",
            "error": "未检测到可靠的 SSTV 同步时序（噪声/弱信号/非 SSTV）",
            "identification": id_info,
        }

    if detected_mode not in SSTV_MODES:
        return {"error": f"不支持的模式: {detected_mode}"}

    mdef = SSTV_MODES[detected_mode]
    width = mdef["width"]
    height = mdef["height"]

    # 初始化图像
    image = np.zeros((height, width, 3), dtype=np.uint8)

    # --- 鲁棒行同步：先检测候选同步段，再按行周期跟踪/外推 ---
    # 噪声会让 1200Hz 同步段的瞬时频率抖出判据带而被切碎，因此：
    # 1) 对 |f-1200|<=110Hz 二值图做形态学闭运算，桥接亚毫秒级抖动；
    # 2) 保留长度 >=60% 标称同步时长的段作为候选；
    # 3) 以行周期跟踪，某行同步丢失时按周期外推（FM 内容位置仍准确）。
    fr = np.where(np.isfinite(freq), freq, 1500.0)
    sync_band = np.abs(fr - 1200.0) <= 110.0
    sync_band[:data_start] = False
    min_sync_len = int(0.6 * sr * mdef["sync_ms"] / 1000)
    candidates = []
    try:
        from scipy import ndimage as _ndi
        labeled = _ndi.label(sync_band)[0]
        for k in range(1, int(labeled.max()) + 1):
            idx = np.where(labeled == k)[0]
            if len(idx) >= min_sync_len:
                candidates.append(int(idx[0]))
    except Exception:
        i = data_start
        while i < len(fr):
            if sync_band[i]:
                j = i
                while j < len(fr) and sync_band[j]:
                    j += 1
                if j - i >= min_sync_len:
                    candidates.append(i)
                i = j
            else:
                i += 1

    # 行周期 = 同步 + 起始分隔 + 3*(通道像素 + 通道间隔)
    row_period_ms = (mdef["sync_ms"] + mdef["sep_ms"]
                     + 3 * (mdef["pixel_ms"] * width + mdef["sep_ms"]))
    row_period = int(round(row_period_ms * sr / 1000.0))
    sync_starts = []
    if candidates:
        window = int(0.012 * sr)  # 期望位置 ±12ms 内接受候选
        # 首锚点必须成周期序列：其后 1/2/3 个行周期位置至少 2 个仍有候选，
        # 以剔除噪声在首行前制造的孤立假同步段（周期外推对首锚点极敏感）。
        first = None
        for c in candidates:
            hits = sum(
                1 for k in (1, 2, 3)
                if any(abs(x - (c + k * row_period)) <= window for x in candidates)
            )
            if hits >= 2:
                first = c
                break
        if first is None:
            first = candidates[0]
        for row in range(height):
            expected = first + row * row_period
            near = [c for c in candidates if abs(c - expected) <= window]
            sync_starts.append(min(near, key=lambda c: abs(c - expected))
                               if near else expected)

    sync_samples = int(round(sr * mdef["sync_ms"] / 1000))
    sep_samples = int(round(sr * mdef["sep_ms"] / 1000))
    # 像素时钟用浮点累积（如 Martin M1 为 20.18 样本/像素），int 截断会让
    # 每个通道短约 1.3ms，累积到第二、三通道造成水平错位（棋盘格反相）。
    px_float = sr * mdef["pixel_ms"] / 1000.0
    channel_samples = int(round(px_float * width))
    half_win = max(1, int(round(px_float * 0.4)))

    n_rows = min(height, len(sync_starts))
    for row in range(n_rows):
        # 同步起点 + 同步脉冲 + 起始分隔，到达第一个颜色通道。
        pos = sync_starts[row] + sync_samples + sep_samples
        if pos + channel_samples * 3 >= len(freq):
            break

        # 采样三个通道（pysstv Martin：每通道后都有 INTER_CH_GAP 黑电平间隔）
        channel_data = {}
        for ch_name in mdef["channel_order"]:
            if pos + channel_samples >= len(freq):
                break
            # 按浮点像素时钟取每个像素中心窗口的均值
            pixels = np.zeros(width, dtype=np.uint8)
            for px in range(width):
                center = int(round(pos + (px + 0.5) * px_float))
                a = max(0, center - half_win)
                b = min(center + half_win, len(freq))
                if b > a:
                    pixels[px] = _freq_to_pixel(np.mean(freq[a:b]))
            channel_data[ch_name] = pixels
            pos += channel_samples + sep_samples

        # 组合 RGB
        if "R" in channel_data and "G" in channel_data and "B" in channel_data:
            image[row, :, 0] = channel_data["R"]
            image[row, :, 1] = channel_data["G"]
            image[row, :, 2] = channel_data["B"]
        elif "Y" in channel_data:
            # Robot 36 YUV
            Y = channel_data.get("Y", np.zeros(width))
            RY = channel_data.get("R-Y", np.zeros(width))
            BY = channel_data.get("B-Y", np.zeros(width))
            image[row, :, 0] = np.clip(Y + 1.402 * (RY - 128), 0, 255)
            image[row, :, 1] = np.clip(Y - 0.344 * (BY - 128) - 0.714 * (RY - 128), 0, 255)
            image[row, :, 2] = np.clip(Y + 1.772 * (BY - 128), 0, 255)

    # 保存图像
    if output_path is None:
        output_path = os.path.splitext(file_path)[0] + "_sstv.png"

    if HAS_PIL:
        img = Image.fromarray(image)
        img.save(output_path)
    else:
        # 用 numpy 保存原始数据（无 PIL 时）
        np.save(output_path + ".npy", image)
        output_path = output_path + ".npy"

    result = {
        "success": True,
        "mode": detected_mode,
        "width": width,
        "height": height,
        "output_path": output_path,
        "rows_decoded": int(np.sum(np.any(image > 0, axis=(1, 2)))),
    }
    if mode == "auto" and detected_mode != "Martin M1":
        result["note"] = f"自动检测到模式: {detected_mode}"
    return result


def decode_sstv_from_samples(samples: np.ndarray, sample_rate: int,
                              output_path: str, mode: str = "Martin M1") -> Dict[str, Any]:
    """从 numpy 样本数组解码 SSTV（用于实时流）。"""
    # 写临时 wav 然后调用 decode_sstv
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    if HAS_SCIPY:
        wavfile.write(tmp_path, sample_rate, (samples * 32767).astype(np.int16))
    else:
        # 简化写 wav
        with open(tmp_path, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + len(samples) * 2))
            f.write(b"WAVEfmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 1))  # PCM
            f.write(struct.pack("<H", 1))  # mono
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<I", sample_rate * 2))
            f.write(struct.pack("<H", 2))
            f.write(struct.pack("<H", 16))
            f.write(b"data")
            f.write(struct.pack("<I", len(samples) * 2))
            f.write((samples * 32767).astype(np.int16).tobytes())
    result = decode_sstv(tmp_path, output_path, mode=mode)
    os.unlink(tmp_path)
    return result
