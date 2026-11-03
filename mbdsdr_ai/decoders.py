# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 数字信号解码器模块
=====================================
Decoders：各种数字模式的解码器。

实现：
1. 卫星轨道计算（sgp4 + 内置 TLE）
2. NOAA APT 气象卫星图像解码
3. SSTV 慢扫描电视解码（简化版）
4. 跳频信号检测（FHSS）
5. FT4/FT8/WSPR/APRS/ADS-B（按各入口实际能力路由）
"""

import os
import time

import math
import numpy as np
from typing import Dict, Any, List, Optional, Sequence, Tuple
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
# 内置仅作离线回退（近似值）。真实使用优先从 celestrak 联网拉最新 TLE。
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
        "1 44016U 17008A   26250.50000000  .00000050  00000-0  10000-3 0  9997",
        "2 44016  98.7000 100.0000 0001000  90.0000 270.0000 14.20000000400000",
    ),
    "FENGYUN 3D": (
        "1 54234U 22152A   26250.50000000  .00000050  00000-0  10000-3 0  9998",
        "2 54234  98.9000 100.0000 0010000  90.0000 270.0000 14.20000000400000",
    ),
}

# 卫星名称 -> NORAD CATNR（用于从 celestrak 联网拉最新 TLE）
SATELLITE_CATNR = {
    "NOAA 15": 25338,
    "NOAA 18": 28654,
    "NOAA 19": 33591,
    "ISS (ZARYA)": 25544,
    "METEOR M2": 44016,
    "FENGYUN 3D": 54234,
}


# TLE 联网拉取失败冷却表：satellite_name -> monotonic 到期时刻。
# 卫星预测以 30s 步长扫窗口（24h = 2880 步），每步都过 _resolve_tle；
# 失败若不进冷却，外网不可达时每步都要等满一次网络超时（fetch_tle 的
# urlopen timeout=15s），单星预测被拖到小时量级、工具调用实际不可用
# （tests/ 根 test_tool_callability dry-call 即因此挂起 10 分钟以上）。
# 冷却期内直接回退内置 TLE，source 仍如实标 "builtin-fallback"——失败
# 缓存只省重试，不伪造联网来源；成功拉取即清冷却，冷却到期自动恢复重试。
_TLE_FAIL_UNTIL: Dict[str, float] = {}
_TLE_FAIL_COOLDOWN_S = 300.0


def _resolve_tle(satellite_name: str):
    """优先联网拉最新 TLE；失败回退内置近似值。返回 (line1, line2, source)。

    联网失败进入 _TLE_FAIL_COOLDOWN_S（5 分钟）负缓存冷却：预测循环逐步
    解析时不会每步重试网络；冷却到期后恢复重试。冷却期内 source 为
    "builtin-fallback"（诚实标注回退路径，不冒充 celestrak）。
    """
    catnr = SATELLITE_CATNR.get(satellite_name)
    if catnr is not None and time.monotonic() >= _TLE_FAIL_UNTIL.get(satellite_name, 0.0):
        try:
            from .orbit import fetch_tle
            tle = fetch_tle(catnr)
        except Exception:
            _TLE_FAIL_UNTIL[satellite_name] = time.monotonic() + _TLE_FAIL_COOLDOWN_S
        else:
            _TLE_FAIL_UNTIL.pop(satellite_name, None)
            return (*tle, "celestrak")
    return (*BUILTIN_TLE[satellite_name], "builtin-fallback")

def _load_satellite_frequencies() -> Dict[str, float]:
    """卫星下行频点表（MHz）：皮进加载自链路目录 sat_links.json，源码零字面量。

    键是 TLE 名拼法（BUILTIN_TLE 的键）：目录 norad_name 与可选
    tle_aliases 都桥接进来。目录查不到的卫星不进表，消费方
    ``.get(name, 0.0)`` 软降级——FENGYUN 3D 即此类：其 AHRPT 下行在
    1698–1710 MHz L 波段（NSMC 公开资料），不在本目录 137/145 MHz
    收听面内，旧表硬记的 137.100 系讹误，已随迁移移除。
    """
    table: Dict[str, float] = {}
    try:
        from .sat_links import list_link_sats, load_sat_link
    except Exception:
        return table
    for key in list_link_sats():
        try:
            entry = load_sat_link(key)
        except Exception:
            continue
        links = [l for l in entry.get("links", [])
                 if l.get("direction") == "downlink"]
        if not links:
            continue
        mhz = float(links[0]["freq_hz"]) / 1e6
        names = [entry.get("norad_name", "")]
        names.extend(entry.get("tle_aliases", []))
        for tle_name in names:
            if tle_name:
                table[str(tle_name)] = mhz
    return table


SATELLITE_FREQUENCIES = _load_satellite_frequencies()


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

    # 从 TLE 创建卫星对象（优先联网最新，回退内置）
    line1, line2, _src = _resolve_tle(satellite_name)
    satellite = Satrec.twoline2rv(line1, line2)

    # 计算卫星位置（TEME/ECI 坐标系，单位 km）
    e, r_teme, v_teme = satellite.sgp4(jd, fr)
    if e != 0:
        return None

    # TEME -> ECEF：绕 z 轴转 -GMST
    jd_utc = timestamp / 86400.0 + 2440587.5
    t_ut1 = (jd_utc - 2451545.0) / 36525.0
    gmst_sec = (67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t_ut1
                + 0.093104 * t_ut1 * t_ut1 - 6.2e-6 * t_ut1 ** 3)
    gmst = math.radians((gmst_sec % 86400.0) / 240.0) % (2.0 * math.pi)
    cg, sg = math.cos(-gmst), math.sin(-gmst)
    sat_x = cg * r_teme[0] - sg * r_teme[1]
    sat_y = sg * r_teme[0] + cg * r_teme[1]
    sat_z = r_teme[2]

    # 速度 TEME -> ECEF（含 Coriolis：v_ECEF = R·v_TEME - Ω×r）
    omega_earth = 7.2921159e-5
    vx = cg * v_teme[0] - sg * v_teme[1] + omega_earth * sat_y
    vy = sg * v_teme[0] + cg * v_teme[1] - omega_earth * sat_x
    vz = v_teme[2]

    # 观测点 ECEF（WGS84 椭球）
    obs_lat_rad = math.radians(observer_lat)
    obs_lon_rad = math.radians(observer_lon)
    earth_radius = 6378.137  # km
    ecc2 = 0.00669437999014
    sin_lat = math.sin(obs_lat_rad)
    cos_lat = math.cos(obs_lat_rad)
    n = earth_radius / math.sqrt(1.0 - ecc2 * sin_lat * sin_lat)
    alt_km = observer_alt / 1000.0
    obs_x = (n + alt_km) * cos_lat * math.cos(obs_lon_rad)
    obs_y = (n + alt_km) * cos_lat * math.sin(obs_lon_rad)
    obs_z = (n * (1.0 - ecc2) + alt_km) * sin_lat

    # 站心矢量
    dx = sat_x - obs_x
    dy = sat_y - obs_y
    dz = sat_z - obs_z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)

    # ENU 变换（正确基向量）
    sin_lon, cos_lon = math.sin(obs_lon_rad), math.cos(obs_lon_rad)
    east_comp = -sin_lon * dx + cos_lon * dy
    north_comp = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up_comp = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

    elevation = math.degrees(math.asin(max(-1.0, min(1.0, up_comp / distance)))) if distance > 0 else 0.0
    azimuth = (math.degrees(math.atan2(east_comp, north_comp)) + 360.0) % 360.0

    # 视线速度 = 卫星 ECEF 速度投影到站星方向
    if distance > 0:
        range_rate = (vx * dx + vy * dy + vz * dz) / distance  # km/s, 远离为正
    else:
        range_rate = 0.0

    # 多普勒频移
    if satellite_name in SATELLITE_FREQUENCIES:
        freq_hz = SATELLITE_FREQUENCIES[satellite_name] * 1e6
        c = 299792.458  # km/s
        doppler = freq_hz * range_rate / c
    else:
        doppler = 0.0
        freq_hz = 0

    # 卫星经纬度（从 ECEF 反算，简化为球坐标）
    sat_lon = math.degrees(math.atan2(sat_y, sat_x))
    sat_r = math.sqrt(sat_x**2 + sat_y**2 + sat_z**2)
    sat_lat = math.degrees(math.asin(sat_z / sat_r)) if sat_r > 0 else 0.0
    sat_alt = sat_r - earth_radius

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
    timestamp: float = None,
) -> List[SatellitePass]:
    """
    列出当前天空中可见的卫星。

    计算所有内置卫星的位置，筛选仰角大于 min_elevation 的卫星。
    timestamp: Unix 时间戳（秒，UTC）。None 时用 time.time()（向后兼容）。
              时间穿梭时由 TimeEngine.now_unix() 注入。
    """
    visible = []
    for name in BUILTIN_TLE:
        if satellite_type == "weather" and "NOAA" not in name and "METEOR" not in name and "FENGYUN" not in name:
            continue
        if satellite_type == "amateur" and "ISS" not in name:
            continue

        pos = compute_satellite_position(name, observer_lat, observer_lon, observer_alt,
                                         timestamp=timestamp)
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
    解码 NOAA POES APT 气象卫星云图（物理正确实现，转 noaa_apt_lite）。

    APT 物理参数：
    - 137.100/137.620/137.9125 MHz 宽带 FM 下行，2400Hz 副载波、频偏 ±416Hz；
    - 每行 0.5s，4160 视频率下 2080 样本（每秒 2 行，非 10 行）；
    - sync A/B(39)+space(47)+image(909)+telemetry(45)，A/B 两通道。

    输入：.wav（解调后音频）或 .cf32/.cu8/.cfile 等原始 IQ（内部宽带 FM 鉴频）。
    输出：PNG（通道 A、通道 B、A/B 拼接）。
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}
    if output_dir is None:
        output_dir = os.path.expanduser("~/.mbdsdr/noaa_apt")
    os.makedirs(output_dir, exist_ok=True)

    from .noaa_apt_lite import decode_apt, save_apt_png
    ext = os.path.splitext(input_path)[1].lower()
    try:
        if ext == ".wav":
            from scipy.io import wavfile
            sr_w, data = wavfile.read(input_path)
            data = np.asarray(data)
            if data.ndim > 1:
                data = data.mean(axis=1)
            if np.issubdtype(data.dtype, np.integer):
                data = data.astype(np.float64) / np.iinfo(data.dtype).max
            audio, a_sr = data.astype(np.float64), float(sr_w)
        else:
            from .sdr_backend import FileIQBackend
            fb = FileIQBackend(input_path)
            fb.connect()
            sr_iq = float(fb.get_sample_rate())
            fb.seek(0); fb._loop = False
            chunks = []
            while True:
                x = fb.read_samples(262144)
                if x is None or len(x) == 0:
                    break
                chunks.append(np.asarray(x, dtype=np.complex64))
            if not chunks:
                return {"error": "IQ 文件无数据"}
            iq = np.concatenate(chunks)
            z = iq - np.mean(iq)
            from scipy.signal import butter, sosfiltfilt, resample_poly
            from math import gcd
            inst = np.angle(z[1:] * np.conj(z[:-1])) * (sr_iq / (2.0 * np.pi))
            sos = butter(4, 4500.0 / (sr_iq / 2.0), btype="low", output="sos")
            inst = sosfiltfilt(sos, inst)
            out_sr = 24000
            g = gcd(out_sr, int(sr_iq))
            audio = resample_poly(inst, out_sr // g, int(sr_iq) // g)
            a_sr = float(out_sr)
    except Exception as e:
        return {"error": f"读取/解调失败: {e}"}

    res = decode_apt(audio, a_sr, min_lines=8)
    if not res.get("apt_present"):
        return {"error": f"未找到 APT 同步（对齐行 {res.get('lines_aligned', 0)}，"
                         f"锁定率 {res.get('lock_ratio', 0)}）；可能未在过境窗口或信号弱"}
    prefix = os.path.join(output_dir, f"NOAA_{int(time.time())}")
    paths = save_apt_png(res, prefix)
    h, w = res["image_a"].shape
    outputs = [
        {"channel": "A", "path": paths["a"], "size": (h, w)},
        {"channel": "B", "path": paths["b"], "size": res["image_b"].shape},
        {"channel": "combined", "path": paths["combo"], "size": (h, 2 * w)},
    ]
    return {"lines_decoded": res["lines_aligned"],
            "lock_ratio": res["lock_ratio"], "outputs": outputs}

    # ── 以下为早期占位实现（AM/10行每秒/包络等物理假设错误），保留不可达，勿用 ──
    if False:
        if output_dir is None:
            output_dir = os.path.expanduser("~/.mbdsdr/noaa_apt")

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
# 4. SSTV 慢扫描电视解码（委托 sstv_decoder 真实实现）
# ═══════════════════════════════════════════════════════

def decode_sstv(
    input_path: str,
    output_dir: str = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    """
    解码 SSTV 慢扫描电视图像。

    委托 sstv_decoder 的真实实现（VIS 头检测 + Martin M1/Scottie S1/
    Robot 36/72/PD 制式识别 + 逐行频率解调）。本入口保留兼容签名
    （output_dir 落盘 PNG）；解码失败如实返回 {"error": ...}，
    绝不生成包络示意图像（原「简化版」壳已退役）。
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}

    if output_dir is None:
        output_dir = os.path.expanduser("~/.mbdsdr/sstv")
    os.makedirs(output_dir, exist_ok=True)

    from .sstv_decoder import decode_sstv as _decode_sstv_real
    out_path = os.path.join(output_dir, f"SSTV_{int(time.time())}.png")
    res = _decode_sstv_real(input_path, output_path=out_path, mode=mode)
    res.pop("image", None)
    if not res.get("success"):
        return {"error": res.get("error", "SSTV 解码失败"),
                "detail": {k: v for k, v in res.items() if k != "success"}}
    return res


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
# 6. 通用数字解码框架（FT4/FT8/WSPR/APRS/ADS-B）
# ═══════════════════════════════════════════════════════

def _read_wav_audio(input_path: str) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
    """读 .wav 为单声道浮点音频。返回 (audio, sample_rate, error)。"""
    try:
        import wave
        with wave.open(input_path, "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            sw = wf.getsampwidth()
            ch = wf.getnchannels()
            sr = float(wf.getframerate())
        if sw == 2:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32767.0
        elif sw == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
        elif sw == 4:
            audio = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483647.0
        else:
            return None, 0.0, f"不支持的 WAV 采样宽度: {sw} 字节"
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)
    except Exception as e:
        return None, 0.0, f"读取 WAV 失败: {e}"
    if audio.size == 0:
        return None, sr, "WAV 中没有音频样本"
    return audio, sr, None


def _demod_iq_to_audio(input_path: str, sample_rate: float,
                       out_sr: int = 12500) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
    """把 .cf32/.cfile 复基带做宽带 FM 鉴频并重采样到 out_sr（送音频级解码器用）。

    返回 (audio, out_sr, error)。
    """
    try:
        raw = np.fromfile(input_path, dtype=np.float32)
        if raw.size < 4:
            return None, 0.0, "IQ 样本太少"
        iq = raw[0::2] + 1j * raw[1::2]
        z = iq - np.mean(iq)
        from scipy.signal import butter, sosfiltfilt, resample_poly
        from math import gcd
        inst = np.angle(z[1:] * np.conj(z[:-1])) * (float(sample_rate) / (2.0 * np.pi))
        sos = butter(4, min(4500.0, float(sample_rate) / 2.0 - 1.0) / (float(sample_rate) / 2.0),
                     btype="low", output="sos")
        inst = sosfiltfilt(sos, inst)
        g = gcd(int(out_sr), int(sample_rate))
        audio = resample_poly(inst, out_sr // g, int(sample_rate) // g)
    except Exception as e:
        return None, 0.0, f"IQ 鉴频失败: {e}"
    return np.asarray(audio, dtype=np.float64), float(out_sr), None


def _resample_audio(audio: np.ndarray, source_rate: float,
                    target_rate: float) -> np.ndarray:
    """用多相滤波保持窄带音频时长；支持 WSPR 下变频后的复序列。"""
    source = int(round(source_rate))
    target = int(round(target_rate))
    if source <= 0 or target <= 0 or source != source_rate or target != target_rate:
        raise ValueError("音频采样率必须是正整数")
    if source == target:
        return np.asarray(audio)
    from math import gcd
    from scipy.signal import resample_poly
    divisor = gcd(source, target)
    converted = resample_poly(audio, target // divisor, source // divisor)
    dtype = np.complex128 if np.iscomplexobj(audio) else np.float64
    return np.asarray(converted, dtype=dtype)


def _narrowband_reject(result: Dict[str, Any], mode: str,
                       reason: str, error: str) -> Dict[str, Any]:
    """非法输入或解码器异常；与有效捕获但零帧的 empty 区分。"""
    check_value = False if mode == "ft4" else None
    result.update({
        "ok": False,
        "status": "rejected",
        "rejected": True,
        "found": False,
        "messages": [],
        "num_messages": 0,
        "num_frames": 0,
        "reject_reason": reason,
        "parity_ok": check_value,
        "crc_ok": check_value,
        "error": error,
    })
    return result


def _narrowband_empty(result: Dict[str, Any], mode: str,
                      reason: str) -> Dict[str, Any]:
    """输入有效但未产出消息，不把空结果包装成异常或解码成功。"""
    check_value = False if mode == "ft4" else None
    result.update({
        "ok": False,
        "status": "empty",
        "rejected": False,
        "found": False,
        "messages": [],
        "num_messages": 0,
        "num_frames": 0,
        "reject_reason": reason,
        "parity_ok": check_value,
        "crc_ok": check_value,
    })
    return result


def _ft4_sync_info(sync) -> Dict[str, Any]:
    """把 Ft4SyncResult 的诊断字段转成可序列化字典，不带软能量大数组。"""
    return {
        key: getattr(sync, key)
        for key in (
            "synced", "base_freq_hz", "time_offset_samples", "sync_score",
            "sync_ratio", "sync_contrast", "reason", "n_time_bins", "n_freq_bins",
        )
    }


def _ft4_message(decoded: Dict[str, Any], candidate: Optional[Dict[str, Any]],
                 sample_rate: float) -> Dict[str, Any]:
    """保留已验证 77 bit 载荷；未知消息类型的 text 可以继续为 None。"""
    sync = decoded["sync"]
    message = {
        "text": decoded["text"],
        "data_bits": decoded["data_bits"],
        "unpacked": decoded["unpacked"],
        "parity_ok": decoded["parity_ok"],
        "crc_ok": decoded["crc_ok"],
        "frequency_hz": float(sync.base_freq_hz),
        "time_offset_seconds": float(sync.time_offset_samples / sample_rate),
        "sync": _ft4_sync_info(sync),
    }
    if candidate is not None:
        message["coarse_candidate"] = candidate
    return message


def _decode_ft4_wav(input_path: str, ext: str, result: Dict[str, Any],
                    center_frequency_hz: float,
                    frequency_search_hz: Optional[Tuple[float, float]],
                    ) -> Dict[str, Any]:
    """FT4 WAV 多峰候选经现有同步/FEC 解码，按 77 bit 有效载荷去重。"""
    result["note"] = (
        "内置 FT4 GFSK 非相干解调、Costas 时频同步、LDPC(174,91) 与 CRC14；"
        "逐个解码粗同步候选并按 77 bit 载荷去重，num_frames 不是重复候选数。"
        "候选上限与掩蔽半径是实现策略，不据此声明 OTA 或弱信号灵敏度已验收。"
    )
    if ext != ".wav":
        return _narrowband_reject(
            result, "ft4", "unsupported_format",
            f"FT4 窄带入口仅支持 .wav 实音频，收到 {ext or '无扩展名'}；"
            "不支持把原始 IQ 或宽带 FM 鉴频当作已验证的 FT4 输入。",
        )
    audio, audio_rate, error = _read_wav_audio(input_path)
    if error:
        return _narrowband_reject(result, "ft4", "invalid_wav", error)
    result["audio_sample_rate_hz"] = float(audio_rate)

    try:
        center = float(center_frequency_hz)
        if not math.isfinite(center):
            raise ValueError("center_frequency_hz 必须是有限数值")
        search = frequency_search_hz
        if search is None:
            # 默认工作点对应常见 1500 Hz 音频附近；不是 FT4 协议常量。
            search = (center - 80.0, center + 80.0)
        from .ft4_modem import FS_HZ, NSPS, Ft4Receiver
        working = _resample_audio(audio, audio_rate, FS_HZ)
        receiver = Ft4Receiver(
            fs=FS_HZ, nsps=NSPS, freq_search_hz=search,
        )

        rows = []
        candidate_count = 0
        max_start = int(working.size - receiver.frame_samples)
        if max_start >= 0 and bool((np.abs(working) > 0.0).any()):
            starts = np.arange(
                0, max_start + 1, receiver.coarse_time_step, dtype=np.int64,
            )
            if starts[-1] != max_start:
                starts = np.append(starts, max_start)
            frequencies = np.arange(
                receiver.freq_lo,
                receiver.freq_hi + receiver.coarse_freq_step * 0.5,
                receiver.coarse_freq_step,
                dtype=np.float64,
            )
            frequencies = frequencies[frequencies <= receiver.freq_hi]
            if frequencies[-1] < receiver.freq_hi:
                frequencies = np.append(frequencies, receiver.freq_hi)

            # 类接口只返回单个最佳峰；入口复用同一相关面选取多个粗候选，
            # 但每个候选仍由公开 sync/decode 与 LDPC/CRC 门独立验证。
            plane = receiver._score_plane(working, frequencies, starts)
            masked = plane.copy()
            candidates = []
            freq_radius = int(math.ceil(72.0 / receiver.coarse_freq_step))
            time_radius = int(math.ceil(
                receiver.nsps / receiver.coarse_time_step
            ))
            for _ in range(40):  # 捕获扫描预算，不是协议常量。
                f_index, t_index = np.unravel_index(
                    int(np.argmax(masked)), masked.shape,
                )
                score = float(masked[f_index, t_index])
                if not math.isfinite(score) or score <= 0.0:
                    break
                candidates.append({
                    "approx_base_hz": float(frequencies[f_index]),
                    "approx_start": int(starts[t_index]),
                    "coarse_score": score,
                })
                f0 = max(0, f_index - freq_radius)
                f1 = min(masked.shape[0], f_index + freq_radius + 1)
                t0 = max(0, t_index - time_radius)
                t1 = min(masked.shape[1], t_index + time_radius + 1)
                masked[f0:f1, t0:t1] = -np.inf

            candidate_count = len(candidates)
            for candidate in candidates:
                approximate = candidate["approx_base_hz"]
                lo = max(receiver.freq_lo, approximate - 6.0)
                hi = min(receiver.freq_hi, approximate + 6.0)
                local = Ft4Receiver(
                    fs=FS_HZ,
                    nsps=NSPS,
                    freq_search_hz=(lo, hi),
                    coarse_freq_step_hz=receiver.coarse_freq_step,
                    coarse_time_step_samples=receiver.coarse_time_step,
                    sync_ratio_threshold=receiver.ratio_threshold,
                    sync_contrast_threshold=receiver.contrast_threshold,
                )
                rows.append((candidate, local.decode(working)))
        if not rows:
            # 静音、短捕获或零相关面仍保留真实诊断；候选计数保持为零。
            rows.append((None, receiver.decode(working)))
    except ValueError as exc:
        return _narrowband_reject(
            result, "ft4", "invalid_input", f"FT4 输入/参数错误: {exc}",
        )
    except Exception as exc:
        return _narrowband_reject(
            result, "ft4", "decode_error", f"FT4 解码失败: {exc}",
        )

    valid_rows = [row for row in rows if row[1]["ok"]]
    unique_rows = []
    seen_payloads = set()
    for row in valid_rows:
        payload = tuple(row[1]["data_bits"])
        if payload not in seen_payloads:
            seen_payloads.add(payload)
            unique_rows.append(row)

    best = (unique_rows[0] if unique_rows else rows[0])[1]
    sync = best["sync"]
    for key, value in best.items():
        if key != "sync":
            result[key] = value
    messages = [
        _ft4_message(decoded, candidate, FS_HZ)
        for candidate, decoded in unique_rows
    ]
    result.update({
        "ok": bool(unique_rows),
        "status": "decoded" if unique_rows else (
            "rejected" if any(row[1]["sync"].synced for row in rows) else "empty"
        ),
        "rejected": False if unique_rows else any(
            row[1]["sync"].synced for row in rows
        ),
        "found": bool(unique_rows),
        "messages": messages,
        "num_messages": len(messages),
        "num_frames": len(messages),
        "candidate_count": candidate_count,
        "accepted_candidate_count": len(valid_rows),
        "duplicate_payload_count": len(valid_rows) - len(unique_rows),
        "reject_reason": "" if unique_rows else best["reject_reason"],
        "sync": _ft4_sync_info(sync),
        "decode_sample_rate_hz": float(FS_HZ),
        "frequency_hz": float(sync.base_freq_hz) if sync.synced else None,
        "time_offset_seconds": (
            float(sync.time_offset_samples / FS_HZ) if sync.synced else None
        ),
    })
    return result


def _decode_wspr_wav(input_path: str, ext: str, result: Dict[str, Any],
                     center_frequency_hz: float,
                     frequency_offsets_hz: Optional[Sequence[float]],
                     time_offsets_seconds: Optional[Sequence[float]],
                     ) -> Dict[str, Any]:
    """经典 WSPR Type 1 WAV → 下变频 → 调用者时频网格搜索译码。"""
    result["note"] = (
        "内置经典 WSPR Type 1：50 bit 消息、K=32/r=1/2 卷积码、162 信道符号、"
        "4FSK；先下变频到 375 Hz，再仅在指定候选时频网格内搜索。"
        "不含宽带盲捕获、频率漂移、多信号相消或 OTA/弱信号灵敏度验收。"
    )
    if ext != ".wav":
        return _narrowband_reject(
            result, "wspr", "unsupported_format",
            f"WSPR 窄带入口仅支持 .wav 实音频，收到 {ext or '无扩展名'}；"
            "不支持把原始 IQ 或宽带 FM 鉴频当作已验证的 WSPR 输入。",
        )
    audio, audio_rate, error = _read_wav_audio(input_path)
    if error:
        return _narrowband_reject(result, "wspr", "invalid_wav", error)
    result["audio_sample_rate_hz"] = float(audio_rate)

    try:
        from .wspr_modem import (
            REFERENCE_SAMPLE_RATE_HZ,
            REFERENCE_SAMPLES_PER_SYMBOL,
            WSPR_SYMBOL_COUNT,
            WSPR_TONE_SPACING_HZ,
            search_and_decode,
        )
        nominal = float(center_frequency_hz)
        if not math.isfinite(nominal):
            raise ValueError("center_frequency_hz 必须是有限数值")
        if (nominal - 1.5 * WSPR_TONE_SPACING_HZ < 0.0
                or nominal + 1.5 * WSPR_TONE_SPACING_HZ >= audio_rate / 2.0):
            raise ValueError("WSPR 标称四音中心必须位于实音频 Nyquist 区间内")
        # 只作复数下变频和抗混叠重采样，不进行宽带 FM 鉴频。
        times = np.arange(audio.size, dtype=np.float64) / audio_rate
        baseband = audio * np.exp(-2j * np.pi * nominal * times)
        baseband = _resample_audio(
            baseband, audio_rate, REFERENCE_SAMPLE_RATE_HZ,
        )
        decoded = search_and_decode(
            baseband,
            REFERENCE_SAMPLE_RATE_HZ,
            nominal_center_frequency_hz=0.0,
            frequency_offsets_hz=(
                (0.0,) if frequency_offsets_hz is None else frequency_offsets_hz
            ),
            time_offsets_seconds=(
                (0.0,) if time_offsets_seconds is None else time_offsets_seconds
            ),
        )
    except ValueError as exc:
        return _narrowband_reject(
            result, "wspr", "invalid_input", f"WSPR 输入/参数错误: {exc}",
        )
    except Exception as exc:
        return _narrowband_reject(
            result, "wspr", "decode_error", f"WSPR 解码失败: {exc}",
        )

    result["decode_sample_rate_hz"] = float(REFERENCE_SAMPLE_RATE_HZ)
    if decoded is None:
        if not bool((np.abs(audio) > 0.0).any()):
            reason = "no_signal"
        elif baseband.size < WSPR_SYMBOL_COUNT * REFERENCE_SAMPLES_PER_SYMBOL:
            reason = "short_capture"
        else:
            # 底层 Optional API 不区分同步门限、FEC 预算和字段拒绝。
            reason = "no_valid_candidate"
        return _narrowband_empty(result, "wspr", reason)

    absolute_frequency = nominal + decoded.frequency_hz
    message = {
        "callsign": decoded.message.callsign,
        "grid4": decoded.message.grid4,
        "power_dbm": decoded.message.power_dbm,
        "bits": list(decoded.bits),
        "frequency_hz": float(absolute_frequency),
        "start_sample": decoded.start_sample,
        "time_offset_seconds": decoded.time_offset_seconds,
        "sync_metric": decoded.sync_metric,
    }
    result.update({
        "ok": True,
        "status": "decoded",
        "rejected": False,
        "found": True,
        "messages": [message],
        "num_messages": 1,
        "num_frames": 1,
        "reject_reason": "",
        # 经典 WSPR 这条链没有 FT4/FT8 的 LDPC 奇偶校验或 CRC 字段。
        "parity_ok": None,
        "crc_ok": None,
        "frequency_hz": float(absolute_frequency),
        "time_offset_seconds": decoded.time_offset_seconds,
        "sync_metric": decoded.sync_metric,
    })
    return result


def decode_digital_mode(
    input_path: str,
    mode: str,
    output_dir: str = None,
    sample_rate: float = 1e6,
    baud: int = 512,
    center_frequency_hz: float = 1500.0,
    frequency_search_hz: Optional[Tuple[float, float]] = None,
    frequency_offsets_hz: Optional[Sequence[float]] = None,
    time_offsets_seconds: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """
    通用数字模式解码框架。

    adsb: 内置纯 numpy Mode-S DF17 解码器（CRC-24 MSB-first、8us 前导模板，
          已与 pyModeS 交叉验证），直接读 .cf32 复基带；仅支持 fs 为 1MHz
          整数倍（Mode-S 符号率 1Mbit/s）。dump1090 为可选外部增强。
    ft4:  .wav 实音频路由到内置 GFSK/Costas 同步及 LDPC(174,91)/CRC14。
          center_frequency_hz 是默认 tone-0 搜索中心；frequency_search_hz
          可显式指定实际音频 tone-0 搜索范围。
    ft8:  仓内已有 Costas 同步、LDPC(174,91)、CRC14 与消息编解码实现；
          ft8_lite.decode_ft8_audio 是独立音频 API，本通用入口尚未接线，
          jt9/wsjtx 仅为可选外部工具，不在这里虚构 WAV 解码结果。
    aprs: 可路由到内置 AX.25/AFSK(Bell202) 解码器；direwolf 为可选增强。
    pocsag: 路由到内置 pocsag_decoder.pocsag_decode（FSK 1200/2400Hz 音调，
          baud 支持 512/1200/2400）。.wav 直接解；.cf32 走宽带 FM 鉴频后解。
    acars: 路由到内置 acars_decoder.ACARSDecoder（MSK 2400Hz/2400baud 带音频，
          12.5kHz 走带）。.wav 直接解；.cf32 走宽带 FM 鉴频后解。
    wspr: .wav 实音频路由到仓内经典 Type 1 解码；center_frequency_hz 是标称
          四音中心，frequency_offsets_hz/time_offsets_seconds 指定候选网格。
    ais:  .cf32/.cfile 信道化复基带路由到内置 GMSK 9600bd + HDLC + M.1371
          报文解析（Type 1/2/3/5/18/24A），FCS 坏帧丢弃并计数。
    navtex: .wav 音频（SSB 解调后 100bd FSK，中心 1kHz）路由到内置
          SITOR-B/CCIR 476 恒比码 + 时间分集纠错 + ZCZC 帧解析。
    meteor: 路由到内置 meteor_sat.meteor_lrpt_demod（QPSK→DQPSK→Viterbi 骨架）。
    """
    if not os.path.exists(input_path):
        return {"error": f"输入文件不存在: {input_path}"}

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/.mbdsdr/{mode}")
    os.makedirs(output_dir, exist_ok=True)

    mode = (mode or "").lower()

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
        "sample_rate_hz": float(sample_rate),
        "external_tools_available": available_tools,
        "note": "",
    }

    # ---- 内置 ADS-B（Mode-S DF17）真解码 ----
    if mode == "adsb" and ext in (".cf32", ".cfile"):
        try:
            from .adsb import decode_baseband
            raw = np.fromfile(input_path, dtype=np.float32)
            iq = raw[0::2] + 1j * raw[1::2]
            ratio = sample_rate / 1e6
            if abs(ratio - round(ratio)) > 1e-6:
                result["note"] = (
                    f"内置 Mode-S 解码器要求 fs 为 1MHz 整数倍，当前 {ratio:g} MHz；"
                    "请重采样后再解码，或使用 dump1090。"
                )
            else:
                d = decode_baseband(iq, fs=float(sample_rate))
                result["found"] = bool(d.get("found", False))
                result["crc_ok"] = bool(d.get("crc_ok", False))
                result["icao_hex"] = d.get("icao")
                result["callsign"] = d.get("callsign")
                result["raw_hex"] = d.get("raw_bytes")
                result["preamble_index"] = d.get("preamble_index")
                result["note"] = "内置纯 numpy Mode-S DF17 解码器（已与 pyModeS 交叉验证）"
        except Exception as e:
            result["analysis_error"] = str(e)
    elif mode == "ft4":
        return _decode_ft4_wav(
            input_path, ext, result, center_frequency_hz, frequency_search_hz,
        )
    elif mode == "ft8":
        result["note"] = (
            "仓内已有 FT8 Costas 同步、LDPC(174,91)、CRC14 与 77 位消息编解码；"
            "ft8_lite.decode_ft8_audio 是独立音频 API，本通用入口尚未接线，"
            "因此不在这里宣称已完成 FT8 WAV 解码。"
        )
        if available_tools:
            result["note"] += f" 检测到可选外部工具 {available_tools[0]}。"
    elif mode == "aprs":
        result["note"] = (
            "APRS 为 1200 波特 AFSK(Bell202) over AX.25；内置 ax25.py 已具备 "
            "FM 鉴频+数字 PLL+CRC-16 能力（见 exp_ax25_performance），direwolf 为可选增强。"
        )
    elif mode == "pocsag":
        # 内置真解码：.wav 直接走音频；.cf32/.cfile 宽带 FM 鉴频后走音频
        baud = int(baud) if baud in (512, 1200, 2400) else 512
        if ext == ".wav":
            audio, a_sr, err = _read_wav_audio(input_path)
        elif ext in (".cf32", ".cfile"):
            audio, a_sr, err = _demod_iq_to_audio(input_path, sample_rate)
        else:
            audio, a_sr, err = None, 0.0, f"POCSAG 需 .wav 音频或 .cf32 IQ，收到 {ext}"
        if err:
            result["error"] = err
        else:
            try:
                from .pocsag_decoder import pocsag_decode
                msgs = pocsag_decode(audio, a_sr, baud)
                result["messages"] = msgs
                result["num_messages"] = len(msgs)
                result["baud"] = baud
                result["note"] = (
                    f"内置 multimon-ng 口径 POCSAG 解码（BCH(31,21) 纠错，{baud} baud）；"
                    f"解出 {len(msgs)} 条消息。"
                )
            except Exception as e:
                result["error"] = f"POCSAG 解码失败: {e}"
    elif mode == "acars":
        # 内置真解码：ACARS 为 MSK 2400Hz/2400baud 带音频（12.5kHz 走带）
        if ext == ".wav":
            audio, a_sr, err = _read_wav_audio(input_path)
        elif ext in (".cf32", ".cfile"):
            audio, a_sr, err = _demod_iq_to_audio(input_path, sample_rate, out_sr=12500)
        else:
            audio, a_sr, err = None, 0.0, f"ACARS 需 .wav 音频或 .cf32 IQ，收到 {ext}"
        if err:
            result["error"] = err
        else:
            try:
                from .acars_decoder import ACARSDecoder
                dec = ACARSDecoder(sample_rate=a_sr)
                msgs = dec.decode_audio(audio)
                result["messages"] = [m.to_dict() for m in msgs]
                result["num_messages"] = len(msgs)
                result["crc_ok_count"] = sum(1 for m in msgs if m.crc_ok)
                result["note"] = (
                    f"内置 MSK 解调+帧同步+解析（2400 baud），解出 {len(msgs)} 条 ACARS 消息"
                    f"（CRC 通过 {sum(1 for m in msgs if m.crc_ok)} 条）。"
                )
            except Exception as e:
                result["error"] = f"ACARS 解码失败: {e}"
    elif mode == "wspr":
        return _decode_wspr_wav(
            input_path, ext, result, center_frequency_hz,
            frequency_offsets_hz, time_offsets_seconds,
        )
    elif mode == "ais":
        # 内置真解码：GMSK 9600bd + HDLC（NRZI/stuffing/FCS-16）+ M.1371
        # 报文字段解析（Type 1/2/3/5/18/24A），吃 .cf32 信道化复基带
        if ext in (".cf32", ".cfile"):
            try:
                raw = np.fromfile(input_path, dtype=np.float32)
                iq = raw[0::2] + 1j * raw[1::2]
                from .ais_decoder import ais_decode_iq
                d = ais_decode_iq(iq, float(sample_rate))
                result["messages"] = d["messages"]
                result["num_messages"] = d["num_messages"]
                result["dropped_frames"] = d["dropped_frames"]
                result["note"] = (
                    "内置 AIS 解码（GMSK 9600bd + HDLC + ITU-R M.1371 "
                    "Type 1/2/3/5/18/24A 字段解析，FCS 校验失败帧丢弃并计数"
                    f" {d['dropped_frames']}）；解出 {d['num_messages']} 条。"
                )
            except Exception as e:
                result["error"] = f"AIS 解码失败: {e}"
        else:
            result["error"] = (
                f"AIS 需 .cf32/.cfile 复基带（9600bd GMSK，fs 为 9600 整数倍），"
                f"收到 {ext}")
    elif mode == "navtex":
        # 内置真解码：SITOR-B/CCIR 476 恒比码 + 时间分集 + ZCZC 帧解析
        if ext == ".wav":
            audio, a_sr, err = _read_wav_audio(input_path)
            if err:
                result["error"] = err
            else:
                try:
                    from .navtex_decoder import navtex_decode_audio
                    d = navtex_decode_audio(audio, a_sr)
                    result["messages"] = d["messages"]
                    result["num_messages"] = d["num_messages"]
                    result["phasing_ok"] = d["phasing_ok"]
                    result["fec_corrected"] = d["fec_corrected"]
                    result["fec_lost"] = d["fec_lost"]
                    result["note"] = (
                        "内置 NAVTEX 解码（SITOR-B 100bd FSK + CCIR 476 恒比检错"
                        f"+ 时间分集纠错 {d['fec_corrected']} 字符）；"
                        f"解出 {d['num_messages']} 条报文。"
                    )
                except Exception as e:
                    result["error"] = f"NAVTEX 解码失败: {e}"
        else:
            result["error"] = (
                f"NAVTEX 需 .wav 音频（SSB 解调后 100bd FSK，中心 1kHz），"
                f"收到 {ext}")
    elif mode == "meteor":
        # Meteor-M2 LRPT：QPSK→差分→去交织→Viterbi 骨架（.cf32 复基带）
        if ext in (".cf32", ".cfile"):
            try:
                raw = np.fromfile(input_path, dtype=np.float32)
                iq = raw[0::2] + 1j * raw[1::2]
                from .meteor_sat import meteor_lrpt_demod
                d = meteor_lrpt_demod(iq, float(sample_rate))
                result["lrpt"] = {k: d[k] for k in ("symbols", "coded_bits", "decoded_bits", "sps")}
                result["note"] = (
                    "内置 Meteor LRPT 解调骨架（QPSK→DQPSK 差分→卷积去交织→Viterbi）；"
                    "帧解析与图像重组见 meteor_sat 工具面。"
                )
            except Exception as e:
                result["error"] = f"Meteor LRPT 解调失败: {e}"
        else:
            result["error"] = f"Meteor LRPT 需 .cf32/.cfile 复基带，收到 {ext}"

    if not result.get("note"):
        if available_tools:
            result["note"] = f"检测到外部工具 {available_tools[0]}，可用于完整解码"
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
