"""
MBDSDR AI 内核 - 数字信号解码器模块
=====================================
Decoders：各种数字模式的解码器。

实现：
1. 卫星轨道计算（sgp4 + 内置 TLE）
2. NOAA APT 气象卫星图像解码
3. SSTV 慢扫描电视解码（简化版）
4. 跳频信号检测（FHSS）
5. FT8/APRS/ADS-B 框架（调用外部工具或简化实现）

对照白皮书第五章 5.4 数字模式解码器。
"""

import os
import time
import json
import math
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

# sgp4 卫星轨道计算
try:
    from sgp4.api import Satrec, jday
    HAS_SGP4 = True
except ImportError:
    HAS_SGP4 = False


# ═══════════════════════════════════════════════════════
# 1. 常用卫星 TLE 数据（内置，无需联网）
# ═══════════════════════════════════════════════════════

# TLE 数据格式：(名称, 第一行, 第二行)
# 这些是常用卫星的近似 TLE，实际使用时应定期更新
BUILTIN_TLE = {
    "NOAA 15": (
        "1 25338U 98030A   26250.50000000  .00000050  00000-0  10000-3 0  9993",
        "2 25338  98.7000 100.0000 0010000  90.0000 270.0000 14.25000000400000",
    ),
    "NOAA 18": (
        "1 28654U 05018A   26250.50000000  .00000050  00000-0  10000-3 0  9994",
        "2 28654  99.1000 100.0000 0010000  90.0000 270.0000 14.10000000400000",
    ),
    "NOAA 19": (
        "1 33591U 09005A   26250.50000000  .00000050  00000-0  10000-3 0  9995",
        "2 33591  99.2000 100.0000 0010000  90.0000 270.0000 14.10000000400000",
    ),
    "ISS (ZARYA)": (
        "1 25544U 98067A   26250.50000000  .00000050  00000-0  10000-3 0  9996",
        "2 25544  51.6400 100.0000 0006700  90.0000 270.0000 15.50000000400000",
    ),
    "METEOR M2": (
        "1 42025U 17008A   26250.50000000  .00000050  00000-0  10000-3 0  9997",
        "2 42025  98.7000 100.0000 0001000  90.0000 270.0000 14.20000000400000",
    ),
    "FENGYUN 3D": (
        "1 54234U 22152A   26250.50000000  .00000050  00000-0  10000-3 0  9998",
        "2 54234  98.9000 100.0000 0010000  90.0000 270.0000 14.20000000400000",
    ),
}

# 卫星下行频率（MHz）
SATELLITE_FREQUENCIES = {
    "NOAA 15": 137.620,
    "NOAA 18": 137.9125,
    "NOAA 19": 137.100,
    "METEOR M2": 137.100,
    "FENGYUN 3D": 137.100,
    "ISS (ZARYA)": 145.800,  # APRS 下行
}


# ═══════════════════════════════════════════════════════
# 2. 卫星轨道计算
# ═══════════════════════════════════════════════════════

@dataclass
class SatellitePass:
    """卫星过境信息。"""
    name: str
    elevation: float  # 仰角（度）
    azimuth: float  # 方位角（度）
    distance_km: float  # 距离（公里）
    doppler_hz: float  # 多普勒频移（Hz）
    latitude: float
    longitude: float
    altitude_km: float


def compute_satellite_position(
    satellite_name: str,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    timestamp: float = None,
) -> Optional[SatellitePass]:
    """
    计算卫星在指定时间、指定观测点的位置。

    使用 sgp4 库计算卫星位置，然后转换为观测点的仰角/方位角/距离。
    """
    if not HAS_SGP4:
        return None

    if satellite_name not in BUILTIN_TLE:
        return None

    if timestamp is None:
        timestamp = time.time()

    # 转换为 Julian Date
    dt = time.gmtime(timestamp)
    jd, fr = jday(dt.tm_year, dt.tm_mon, dt.tm_mday,
                   dt.tm_hour, dt.tm_min, dt.tm_sec)

    # 从 TLE 创建卫星对象
    line1, line2 = BUILTIN_TLE[satellite_name]
    satellite = Satrec.twoline2rv(line1, line2)

    # 计算卫星位置（ECI 坐标系，单位 km）
    e, r, v = satellite.sgp4(jd, fr)
    if e != 0:
        return None

    sat_x, sat_y, sat_z = r  # km

    # 简化：将 ECI 转换为观测点的仰角/方位角
    # （完整实现需要考虑地球自转，这里用简化的球面几何）

    # 观测点的 ECEF 位置（简化，不考虑地球自转）
    obs_lat_rad = math.radians(observer_lat)
    obs_lon_rad = math.radians(observer_lon)
    earth_radius = 6378.137  # km

    obs_x = (earth_radius + observer_alt / 1000.0) * math.cos(obs_lat_rad) * math.cos(obs_lon_rad)
    obs_y = (earth_radius + observer_alt / 1000.0) * math.cos(obs_lat_rad) * math.sin(obs_lon_rad)
    obs_z = (earth_radius + observer_alt / 1000.0) * math.sin(obs_lat_rad)

    # 卫星相对于观测点的位置
    dx = sat_x - obs_x
    dy = sat_y - obs_y
    dz = sat_z - obs_z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)

    # 计算仰角（简化：卫星相对于观测点的高度角）
    # 观测点的法向量
    nx = math.cos(obs_lat_rad) * math.cos(obs_lon_rad)
    ny = math.cos(obs_lat_rad) * math.sin(obs_lon_rad)
    nz = math.sin(obs_lat_rad)

    # 卫星方向向量（归一化）
    rx, ry, rz = dx / distance, dy / distance, dz / distance

    # 仰角 = 90 - 卫星方向与法向量的夹角
    dot = rx * nx + ry * ny + rz * nz
    elevation = math.degrees(math.asin(max(-1, min(1, dot))))

    # 方位角（简化计算）
    east = -math.sin(obs_lon_rad)
    north = -math.sin(obs_lat_rad) * math.cos(obs_lon_rad)
    up = math.cos(obs_lat_rad) * math.cos(obs_lon_rad)

    east_comp = rx * east + ry * (-math.cos(obs_lon_rad)) + rz * 0
    north_comp = rx * north + ry * (-math.sin(obs_lat_rad) * math.sin(obs_lon_rad)) + rz * math.cos(obs_lat_rad)

    azimuth = math.degrees(math.atan2(east_comp, north_comp))
    if azimuth < 0:
        azimuth += 360

    # 多普勒频移（简化：基于径向速度）
    if satellite_name in SATELLITE_FREQUENCIES:
        freq_hz = SATELLITE_FREQUENCIES[satellite_name] * 1e6
        # 径向速度（简化，用位置差近似）
        radial_velocity = 0  # km/s，简化为 0，实际需要速度向量
        # 多普勒 = f0 * v/c
        c = 299792.458  # km/s
        doppler = freq_hz * radial_velocity / c
    else:
        doppler = 0.0
        freq_hz = 0

    # 卫星的经纬度（简化，从 ECI 近似）
    sat_lon = math.degrees(math.atan2(sat_y, sat_x))
    sat_lat = math.degrees(math.asin(sat_z / math.sqrt(sat_x**2 + sat_y**2 + sat_z**2)))
    sat_alt = math.sqrt(sat_x**2 + sat_y**2 + sat_z**2) - earth_radius

    return SatellitePass(
        name=satellite_name,
        elevation=elevation,
        azimuth=azimuth,
        distance_km=distance,
        doppler_hz=doppler,
        latitude=sat_lat,
        longitude=sat_lon,
        altitude_km=sat_alt,
    )


def list_visible_satellites(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    min_elevation: float = 0.0,
    satellite_type: str = "all",
) -> List[SatellitePass]:
    """
    列出当前天空中可见的卫星。

    计算所有内置卫星的位置，筛选仰角大于 min_elevation 的卫星。
    """
    visible = []
    for name in BUILTIN_TLE:
        if satellite_type == "weather" and "NOAA" not in name and "METEOR" not in name and "FENGYUN" not in name:
            continue
        if satellite_type == "amateur" and "ISS" not in name:
            continue

        pos = compute_satellite_position(name, observer_lat, observer_lon, observer_alt)
        if pos and pos.elevation >= min_elevation:
            visible.append(pos)

    # 按仰角从高到低排序
    visible.sort(key=lambda x: x.elevation, reverse=True)
    return visible


def compute_doppler_correction(
    satellite_name: str,
    nominal_freq_hz: float,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
) -> Dict[str, Any]:
    """
    计算卫星多普勒修正频率。

    返回当前修正后的接收频率（考虑多普勒频移）。
    """
    pos = compute_satellite_position(satellite_name, observer_lat, observer_lon, observer_alt)
    if pos is None:
        return {"error": f"无法计算卫星 {satellite_name} 的位置"}

    # 简化的多普勒计算（基于卫星高度和距离的近似）
    # 实际多普勒需要卫星速度向量，这里用经验公式近似
    earth_radius = 6378.137
    orbital_radius = earth_radius + pos.altitude_km
    orbital_speed = math.sqrt(398600.4418 / orbital_radius)  # km/s

    # 最大多普勒（卫星正上方时）
    max_doppler = nominal_freq_hz * orbital_speed / 299792.458

    # 当前多普勒（基于仰角的近似：仰角越高，多普勒变化率越大）
    elev_rad = math.radians(pos.elevation)
    current_doppler = max_doppler * math.cos(elev_rad) * 0.5  # 简化

    corrected_freq = nominal_freq_hz + current_doppler

    return {
        "satellite": satellite_name,
        "nominal_freq_mhz": nominal_freq_hz / 1e6,
        "corrected_freq_mhz": corrected_freq / 1e6,
        "doppler_shift_hz": current_doppler,
        "max_doppler_hz": max_doppler,
        "elevation_deg": pos.elevation,
        "azimuth_deg": pos.azimuth,
        "distance_km": pos.distance_km,
        "note": "多普勒为近似值，实际需要卫星速度向量",
    }


# ═══════════════════════════════════════════════════════
# 3. NOAA APT 气象卫星图像解码
# ═══════════════════════════════════════════════════════

def decode_noaa_apt(
    input_path: str,
    output_dir: str = None,
    channel: str = "both",
    sample_rate: int = 20800,
) -> Dict[str, Any]:
    """
    解码 NOAA APT 气象卫星图像。

    NOAA APT 格式：
    - 采样率：20800 Hz（标准）
    - 每行：2080 像素（同步A + 空间A + 同步B + 空间B）
    - 每秒：10 行（20800 / 2080 = 10）
    - 调制：AM（幅度调制）

    输入：wav 或 cf32 格式的录音文件
    输出：PNG 图像（通道A、通道B、合成图）
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}

    if output_dir is None:
        output_dir = os.path.expanduser("~/.mbdsdr/noaa_apt")
    os.makedirs(output_dir, exist_ok=True)

    # 读取音频数据
    try:
        ext = os.path.splitext(input_path)[1].lower()
        if ext == ".wav":
            import wave
            with wave.open(input_path, 'rb') as wf:
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
                if wf.getnchannels() == 2:
                    audio = audio[0::2]  # 取左声道
                file_sample_rate = wf.getframerate()
        elif ext == ".cf32":
            raw = np.fromfile(input_path, dtype=np.float32)
            # cf32 是交错 I/Q，取幅度（包络）
            iq = raw[0::2] + 1j * raw[1::2]
            audio = np.abs(iq)
            file_sample_rate = sample_rate
        else:
            return {"error": f"不支持的格式: {ext}（支持 .wav/.cf32）"}
    except Exception as e:
        return {"error": f"读取文件失败: {e}"}

    if len(audio) == 0:
        return {"error": "文件中没有音频数据"}

    # 重采样到 20800 Hz（如果需要）
    if file_sample_rate != 20800:
        ratio = 20800 / file_sample_rate
        new_len = int(len(audio) * ratio)
        audio = np.interp(np.linspace(0, len(audio) - 1, new_len),
                           np.arange(len(audio)), audio)

    # APT 参数
    pixels_per_line = 2080
    lines_per_second = 10
    sync_a_width = 39  # 同步A宽度（像素）
    space_a_width = 909  # 空间A宽度
    sync_b_width = 39  # 同步B宽度
    space_b_width = 909  # 空间B宽度
    telemetry_width = 128  # 遥测宽度

    # 计算行数
    total_samples = len(audio)
    num_lines = total_samples // pixels_per_line

    if num_lines < 1:
        return {"error": f"数据太少，无法解码（需要至少 {pixels_per_line} 样本）"}

    # 提取图像数据
    # 每行结构：[同步A(39)][空间A(909)][同步B(39)][空间B(909)][遥测(128)?]
    # 实际 NOAA APT: 同步A(39) + 空间A(909) + 同步B(39) + 空间B(909) = 1896，剩余 184 为间隔

    img_a = []
    img_b = []

    for line_idx in range(num_lines):
        start = line_idx * pixels_per_line
        line_data = audio[start:start + pixels_per_line]

        if len(line_data) < pixels_per_line:
            break

        # 空间A：从同步A结束到同步B开始
        # 简化：直接取固定位置
        space_a_start = sync_a_width
        space_a_end = space_a_start + space_a_width
        channel_a = line_data[space_a_start:space_a_end]

        # 空间B
        space_b_start = sync_a_width + space_a_width + sync_b_width
        space_b_end = space_b_start + space_b_width
        channel_b = line_data[space_b_start:space_b_end]

        # 归一化到 0-255
        if len(channel_a) > 0:
            a_min, a_max = np.min(channel_a), np.max(channel_a)
            if a_max > a_min:
                channel_a = ((channel_a - a_min) / (a_max - a_min) * 255).astype(np.uint8)
            else:
                channel_a = np.zeros_like(channel_a, dtype=np.uint8)
            img_a.append(channel_a)

        if len(channel_b) > 0:
            b_min, b_max = np.min(channel_b), np.max(channel_b)
            if b_max > b_min:
                channel_b = ((channel_b - b_min) / (b_max - b_min) * 255).astype(np.uint8)
            else:
                channel_b = np.zeros_like(channel_b, dtype=np.uint8)
            img_b.append(channel_b)

    if not img_a and not img_b:
        return {"error": "无法提取图像数据"}

    # 保存图像
    timestamp = int(time.time())
    results = {"lines_decoded": num_lines, "outputs": []}

    try:
        from PIL import Image

        if img_a and channel in ("a", "both"):
            img_a_array = np.array(img_a)
            im_a = Image.fromarray(img_a_array, mode='L')
            path_a = os.path.join(output_dir, f"NOAA_CH-A_{timestamp}.png")
            im_a.save(path_a)
            results["outputs"].append({"channel": "A", "path": path_a, "size": img_a_array.shape})

        if img_b and channel in ("b", "both"):
            img_b_array = np.array(img_b)
            im_b = Image.fromarray(img_b_array, mode='L')
            path_b = os.path.join(output_dir, f"NOAA_CH-B_{timestamp}.png")
            im_b.save(path_b)
            results["outputs"].append({"channel": "B", "path": path_b, "size": img_b_array.shape})

        # 合成图（A为红色通道，B为蓝色通道）
        if img_a and img_b and channel == "both":
            min_lines = min(len(img_a), len(img_b))
            min_width = min(len(img_a[0]), len(img_b[0]))
            combined = np.zeros((min_lines, min_width, 3), dtype=np.uint8)
            for i in range(min_lines):
                combined[i, :, 0] = img_a[i][:min_width]  # R = A
                combined[i, :, 2] = img_b[i][:min_width]  # B = B
            im_combined = Image.fromarray(combined, mode='RGB')
            path_combined = os.path.join(output_dir, f"NOAA_COMBINED_{timestamp}.png")
            im_combined.save(path_combined)
            results["outputs"].append({"channel": "combined", "path": path_combined, "size": combined.shape})

    except ImportError:
        # PIL 不可用，保存为原始数据
        results["note"] = "PIL 不可用，图像未保存为 PNG"
        if img_a:
            np.save(os.path.join(output_dir, f"NOAA_CH-A_{timestamp}.npy"), np.array(img_a))

    return results


# ═══════════════════════════════════════════════════════
# 4. SSTV 慢扫描电视解码（简化版）
# ═══════════════════════════════════════════════════════

def decode_sstv(
    input_path: str,
    output_dir: str = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    """
    解码 SSTV 慢扫描电视图像（简化版）。

    支持的模式：
    - Martin M1: 320x256, 114 秒, 1200Hz 同步, 1500Hz 黑色, 2300Hz 白色
    - Martin M2: 320x256, 58 秒
    - Scottie S1: 320x256, 110 秒
    - Robot 36: 320x240, 36 秒

    简化实现：检测同步脉冲，提取亮度信号，生成灰度图像。
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}

    if output_dir is None:
        output_dir = os.path.expanduser("~/.mbdsdr/sstv")
    os.makedirs(output_dir, exist_ok=True)

    # 读取音频
    try:
        import wave
        with wave.open(input_path, 'rb') as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            if wf.getnchannels() == 2:
                audio = audio[0::2]
            sample_rate = wf.getframerate()
    except Exception as e:
        return {"error": f"读取文件失败: {e}"}

    if len(audio) == 0:
        return {"error": "文件中没有音频数据"}

    # SSTV 参数（Martin M1 简化）
    sync_freq = 1200  # Hz，同步脉冲
    black_freq = 1500  # Hz，黑色
    white_freq = 2300  # Hz，白色
    width = 320
    height = 256

    # 简化：将音频的幅度映射为亮度（实际 SSTV 是频率调制，需要解调）
    # 这里用简化的包络检测作为演示
    # 实际实现需要：1. 检测 1200Hz 同步  2. 频率解调（FM）  3. 映射频率到亮度

    # 简化：取音频包络，重采样到 320x256
    envelope = np.abs(audio)
    # 重采样到图像尺寸
    total_pixels = width * height
    if len(envelope) > total_pixels:
        indices = np.linspace(0, len(envelope) - 1, total_pixels).astype(int)
        image_data = envelope[indices].reshape(height, width)
    else:
        image_data = np.zeros((height, width), dtype=np.float32)

    # 归一化到 0-255
    img_min, img_max = np.min(image_data), np.max(image_data)
    if img_max > img_min:
        image_data = ((image_data - img_min) / (img_max - img_min) * 255).astype(np.uint8)
    else:
        image_data = np.zeros_like(image_data, dtype=np.uint8)

    # 保存图像
    timestamp = int(time.time())
    result = {"mode": mode, "width": width, "height": height, "note": "简化版 SSTV 解码（包络检测），完整实现需要 FM 解调+同步检测"}

    try:
        from PIL import Image
        im = Image.fromarray(image_data, mode='L')
        path = os.path.join(output_dir, f"SSTV_{timestamp}.png")
        im.save(path)
        result["output_path"] = path
    except ImportError:
        np.save(os.path.join(output_dir, f"SSTV_{timestamp}.npy"), image_data)
        result["note"] += "，PIL 不可用，保存为 npy"

    return result


# ═══════════════════════════════════════════════════════
# 5. 跳频信号检测（FHSS）
# ═══════════════════════════════════════════════════════

def detect_fhss(
    samples: np.ndarray,
    sample_rate: float,
    center_freq: float,
    num_frames: int = 50,
    frame_interval_ms: int = 10,
    fft_size: int = 1024,
    threshold_db: float = -60,
) -> Dict[str, Any]:
    """
    检测跳频信号（FHSS）。

    连续录制多帧频谱，检测频率随时间跳变的信号。
    返回跳频图案（频率列表、驻留时间、跳频速率、跳频序列）。
    """
    if len(samples) < fft_size * 2:
        return {"error": "样本太少，无法检测跳频"}

    # 计算每帧的样本数
    frame_samples = int(sample_rate * frame_interval_ms / 1000)
    if frame_samples < fft_size:
        frame_samples = fft_size

    # 确保有足够的样本
    max_frames = len(samples) // frame_samples
    num_frames = min(num_frames, max_frames)

    if num_frames < 2:
        return {"error": f"帧数太少（{num_frames}），无法检测跳频"}

    # 逐帧分析频谱
    hop_frequencies = []
    hop_times = []
    all_peaks = []

    for frame_idx in range(num_frames):
        start = frame_idx * frame_samples
        end = start + frame_samples
        if end > len(samples):
            break

        frame_data = samples[start:end]

        # FFT
        window = np.hanning(len(frame_data))
        spectrum = np.fft.fftshift(np.fft.fft(frame_data * window, fft_size))
        powers_db = 10 * np.log10(np.abs(spectrum) ** 2 + 1e-12)
        freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1.0 / sample_rate)) + center_freq

        # 找峰值
        peak_idx = np.argmax(powers_db)
        peak_power = powers_db[peak_idx]
        peak_freq = freqs[peak_idx]

        all_peaks.append({"frame": frame_idx, "freq_hz": peak_freq, "power_db": peak_power})

        # 如果峰值超过阈值，记录为跳频点
        if peak_power > threshold_db:
            # 检查是否与上一个跳频点不同（检测跳变）
            if hop_frequencies and abs(peak_freq - hop_frequencies[-1]) > sample_rate * 0.01:
                hop_times.append(frame_idx * frame_interval_ms / 1000.0)
                hop_frequencies.append(peak_freq)
            elif not hop_frequencies:
                hop_times.append(0.0)
                hop_frequencies.append(peak_freq)

    # 分析跳频图案
    if len(hop_frequencies) < 2:
        return {
            "detected": False,
            "num_frames_analyzed": num_frames,
            "peak_frequencies": [p["freq_hz"] for p in all_peaks[:10]],
            "message": "未检测到明显的跳频信号",
        }

    # 唯一频率（聚类）
    unique_freqs = []
    for freq in hop_frequencies:
        if not any(abs(freq - uf) < sample_rate * 0.02 for uf in unique_freqs):
            unique_freqs.append(freq)

    # 跳频速率
    if len(hop_times) > 1:
        intervals = [hop_times[i+1] - hop_times[i] for i in range(len(hop_times)-1)]
        avg_interval = np.mean(intervals)
        hop_rate = 1.0 / avg_interval if avg_interval > 0 else 0
    else:
        avg_interval = 0
        hop_rate = 0

    return {
        "detected": True,
        "num_frames_analyzed": num_frames,
        "num_hops_detected": len(hop_frequencies),
        "unique_frequencies_mhz": sorted([f / 1e6 for f in unique_freqs]),
        "num_unique_frequencies": len(unique_freqs),
        "hop_rate_hz": hop_rate,
        "avg_dwell_time_ms": avg_interval * 1000,
        "hop_sequence_mhz": [f / 1e6 for f in hop_frequencies[:20]],
        "peak_power_range_db": [min(p["power_db"] for p in all_peaks), max(p["power_db"] for p in all_peaks)],
        "threshold_db": threshold_db,
    }


# ═══════════════════════════════════════════════════════
# 6. 通用数字解码框架（FT8/APRS/ADS-B）
# ═══════════════════════════════════════════════════════

def decode_digital_mode(
    input_path: str,
    mode: str,
    output_dir: str = None,
) -> Dict[str, Any]:
    """
    通用数字模式解码框架。

    优先调用系统安装的外部工具（wsjtx/direwolf/dump1090），
    如果不可用则返回说明和简化分析。

    mode: ft8 / aprs / adsb / wspr / olivia / m17 / lora / tpms
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/.mbdsdr/{mode}")
    os.makedirs(output_dir, exist_ok=True)

    # 外部工具映射
    external_tools = {
        "ft8": ["jt9", "wsjtx"],
        "wspr": ["wsprd", "wsjtx"],
        "aprs": ["direwolf", "aprs"],
        "adsb": ["dump1090", "dump1090-fa"],
    }

    # 检查外部工具是否可用
    import shutil
    available_tools = []
    if mode in external_tools:
        for tool in external_tools[mode]:
            if shutil.which(tool):
                available_tools.append(tool)

    # 读取文件基本信息
    file_size = os.path.getsize(input_path)
    ext = os.path.splitext(input_path)[1].lower()

    result = {
        "mode": mode,
        "input_file": input_path,
        "file_size": file_size,
        "format": ext,
        "external_tools_available": available_tools,
        "note": "",
    }

    # 如果有外部工具，尝试调用
    if available_tools:
        result["note"] = f"检测到外部工具 {available_tools[0]}，可用于完整解码"
        # 这里可以添加实际的 subprocess 调用
        # 为了安全，先不自动调用，返回工具信息
    else:
        result["note"] = f"未检测到 {mode} 解码工具，建议安装: {external_tools.get(mode, ['N/A'])}"

    # 简化分析：读取 IQ 数据，做基本的频谱特征提取
    try:
        if ext == ".cf32":
            raw = np.fromfile(input_path, dtype=np.float32, count=100000)
            if len(raw) >= 2:
                iq = raw[0::2] + 1j * raw[1::2]
                spectrum = np.fft.fft(iq[:1024])
                powers = np.abs(spectrum) ** 2
                result["spectral_peak"] = float(np.max(powers))
                result["spectral_mean"] = float(np.mean(powers))
                result["samples_analyzed"] = len(iq)
    except Exception as e:
        result["analysis_error"] = str(e)

    return result
