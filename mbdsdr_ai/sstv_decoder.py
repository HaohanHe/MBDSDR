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
        "vis_code": 0x5C,
        "width": 320,
        "height": 256,
        "sync_freq": 1200,
        "sync_ms": 4.862,
        "sep_freq": 1500,
        "sep_ms": 0.572,
        "channel_order": ["G", "B", "R"],
        "pixel_ms": 0.2288,  # 73.216ms / 320
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
    """用解析信号相位差分估算瞬时频率。"""
    # 希尔伯特变换求解析信号
    if HAS_SCIPY:
        from scipy.signal import hilbert
        analytic = hilbert(samples)
    else:
        # 简化：用过零率
        analytic = samples.astype(np.complex64)
    phase = np.unwrap(np.angle(analytic))
    # 相位差分 = 角频率
    inst_freq = np.diff(phase) * sample_rate / (2 * np.pi)
    # 滑动平均平滑
    if len(inst_freq) > window:
        kernel = np.ones(window) / window
        inst_freq = np.convolve(inst_freq, kernel, mode="same")
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

    # 检测模式
    detected_mode = mode
    data_start = 0
    if mode == "auto":
        vis_code, data_start = _detect_vis_header(freq, sr)
        if vis_code is not None:
            for mname, mdef in SSTV_MODES.items():
                if mdef["vis_code"] == vis_code:
                    detected_mode = mname
                    break
        if detected_mode == "auto":
            detected_mode = "Martin M1"  # 默认

    if detected_mode not in SSTV_MODES:
        return {"error": f"不支持的模式: {detected_mode}"}

    mdef = SSTV_MODES[detected_mode]
    width = mdef["width"]
    height = mdef["height"]

    # 初始化图像
    image = np.zeros((height, width, 3), dtype=np.uint8)

    # 逐行解码
    pos = data_start
    sync_samples = int(sr * mdef["sync_ms"] / 1000)
    sep_samples = int(sr * mdef["sep_ms"] / 1000)
    pixel_samples = max(1, int(sr * mdef["pixel_ms"] / 1000))
    channel_samples = pixel_samples * width

    for row in range(height):
        if pos + sync_samples + sep_samples + channel_samples * 3 >= len(freq):
            break

        # 找同步脉冲（1200Hz）
        sync_search_end = min(pos + sync_samples * 3, len(freq))
        sync_found = False
        for s in range(pos, sync_search_end):
            if 1100 < freq[s] < 1300:
                pos = s
                sync_found = True
                break

        # 跳过同步 + 分隔
        pos += sync_samples + sep_samples

        # 采样三个通道
        channel_data = {}
        for ch_name in mdef["channel_order"]:
            if pos + channel_samples >= len(freq):
                break
            # 每像素取中间样本
            pixels = np.zeros(width, dtype=np.uint8)
            for px in range(width):
                px_start = pos + px * pixel_samples
                px_end = min(px_start + pixel_samples, len(freq))
                if px_end > px_start:
                    px_freq = np.mean(freq[px_start:px_end])
                    pixels[px] = _freq_to_pixel(px_freq)
            channel_data[ch_name] = pixels
            pos += channel_samples

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
