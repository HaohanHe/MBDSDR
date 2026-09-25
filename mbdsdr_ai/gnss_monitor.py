"""
MBDSDR AI - GNSS 干扰监测模块
=============================

基于 SDR 的 GNSS 频带干扰监测与分类。
学术创新点：利用廉价 RTL-SDR 实现 L1/L2/L5 频带实时干扰监测，
AI 自动分类干扰类型（连续波/窄带/宽带/chirp），辅助干扰源定位。

参考：
- 张云等. 基于软件无线电的GNSS干扰和多径监测系统设计[J]. 电讯技术
- Kozhaya et al. Unveiling Starlink for PNT[J]. NAVIGATION, 2025

GNSS 频带：
- GPS L1: 1575.42 MHz
- GPS L2: 1227.60 MHz
- GPS L5: 1176.45 MHz
- BDS B1: 1561.098 MHz
- BDS B2: 1207.140 MHz
- BDS B3: 1268.520 MHz
"""

import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class InterferenceType(Enum):
    """干扰类型枚举。"""
    NONE = "none"           # 无干扰
    CW = "cw"               # 连续波干扰
    NARROWBAND = "nb"       # 窄带干扰
    WIDEBAND = "wb"         # 宽带干扰
    CHIRP = "chirp"         # 线性调频干扰
    PULSE = "pulse"         # 脉冲干扰
    UNKNOWN = "unknown"     # 未知干扰


@dataclass
class GNSSBandResult:
    """单个 GNSS 频带监测结果。"""
    band_name: str           # 频带名称（L1/L2/L5/B1/B2/B3）
    center_freq_hz: float    # 中心频率
    bandwidth_hz: float      # 监测带宽
    power_dbm: float = 0.0   # 平均功率（dBm）
    noise_floor_dbm: float = 0.0  # 噪声基底（dBm）
    inr_db: float = 0.0      # 干扰噪声比（dB）
    peak_freq_hz: float = 0.0  # 峰值频率
    peak_power_dbm: float = 0.0  # 峰值功率
    interference_type: InterferenceType = InterferenceType.NONE
    confidence: float = 0.0  # 分类置信度（0-1）
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class InterferenceAlert:
    """干扰告警。"""
    band_name: str
    interference_type: str
    inr_db: float
    peak_freq_hz: float
    severity: str            # low/medium/high/critical
    recommendation: str     # 处理建议
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# GNSS 频带定义
GNSS_BANDS = {
    "L1": {
        "center_freq_hz": 1575.42e6,
        "bandwidth_hz": 2.046e6,  # GPS L1 C/A 信号带宽
        "systems": ["GPS L1", "BDS B1", "Galileo E1"],
    },
    "L2": {
        "center_freq_hz": 1227.60e6,
        "bandwidth_hz": 2.046e6,
        "systems": ["GPS L2", "BDS B2"],
    },
    "L5": {
        "center_freq_hz": 1176.45e6,
        "bandwidth_hz": 20.46e6,
        "systems": ["GPS L5", "BDS B2a", "Galileo E5a"],
    },
    "B1": {
        "center_freq_hz": 1561.098e6,
        "bandwidth_hz": 4.092e6,
        "systems": ["BDS B1"],
    },
    "B2": {
        "center_freq_hz": 1207.140e6,
        "bandwidth_hz": 24.0e6,
        "systems": ["BDS B2"],
    },
    "B3": {
        "center_freq_hz": 1268.52e6,
        "bandwidth_hz": 16.368e6,
        "systems": ["BDS B3"],
    },
}


def classify_interference(
    spectrum_db: np.ndarray,
    freqs_hz: np.ndarray,
    noise_floor_db: float,
    threshold_db: float = 6.0,
) -> Tuple[InterferenceType, float, float, float]:
    """
    基于功率谱特征分类干扰类型。

    参数：
        spectrum_db: 功率谱（dB）
        freqs_hz: 频率轴（Hz）
        noise_floor_db: 噪声基底（dB）
        threshold_db: 检测门限（dB above noise floor）

    返回：
        (干扰类型, 置信度, 峰值频率Hz, 干扰噪声比dB)
    """
    # 1. 找超过门限的频率点
    excess = spectrum_db - noise_floor_db
    above_mask = excess > threshold_db
    above_indices = np.where(above_mask)[0]

    if len(above_indices) == 0:
        return InterferenceType.NONE, 1.0, 0.0, 0.0

    # 2. 计算干扰特征
    peak_idx = np.argmax(spectrum_db)
    peak_freq = freqs_hz[peak_idx]
    peak_power = spectrum_db[peak_idx]
    inr = peak_power - noise_floor_db

    # 3. 计算干扰带宽
    if len(above_indices) > 1:
        # 找连续超门限区间
        diff = np.diff(above_indices)
        breaks = np.where(diff > 3)[0]
        if len(breaks) == 0:
            # 单一连续区间
            occupied_bins = len(above_indices)
        else:
            # 多个区间，取最大的
            segments = np.split(above_indices, breaks + 1)
            occupied_bins = max(len(s) for s in segments)
    else:
        occupied_bins = 1

    total_bins = len(spectrum_db)
    occupied_ratio = occupied_bins / total_bins
    band_width = (freqs_hz[-1] - freqs_hz[0]) * occupied_ratio

    # 4. 分类逻辑
    # CW：单频点超门限（occupied_bins <= 2）
    # NB：窄带（occupied_bins <= total_bins * 0.1）
    # WB：宽带（occupied_bins > total_bins * 0.3）
    # Chirp：需要时间维度信息，这里用频谱展宽近似
    bandwidth_hz = freqs_hz[-1] - freqs_hz[0]

    if occupied_bins <= 2:
        itype = InterferenceType.CW
        confidence = min(0.95, 0.7 + inr / 20.0)
    elif occupied_ratio < 0.1:
        itype = InterferenceType.NARROWBAND
        confidence = min(0.90, 0.6 + inr / 25.0)
    elif occupied_ratio > 0.3:
        itype = InterferenceType.WIDEBAND
        confidence = min(0.85, 0.5 + inr / 30.0)
    else:
        itype = InterferenceType.UNKNOWN
        confidence = 0.5

    return itype, confidence, peak_freq, inr


def monitor_gnss_band(
    iq_samples: np.ndarray,
    center_freq_hz: float,
    sample_rate_hz: float,
    band_name: str = "L1",
) -> GNSSBandResult:
    """
    监测单个 GNSS 频带的干扰情况。

    参数：
        iq_samples: IQ 采样数据（复数，已调谐到中心频率）
        center_freq_hz: 中心频率
        sample_rate_hz: 采样率
        band_name: 频带名称

    返回：
        GNSSBandResult
    """
    # 计算功率谱（Welch 方法）
    n = len(iq_samples)
    nfft = min(4096, n)
    if nfft < 256:
        nfft = 256

    # FFT 功率谱
    fft_data = np.fft.fftshift(np.fft.fft(iq_samples[:nfft], nfft))
    psd = np.abs(fft_data) ** 2 / (nfft * sample_rate_hz)
    psd_db = 10 * np.log10(psd + 1e-12)

    # 频率轴
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / sample_rate_hz))
    freqs_hz = freqs + center_freq_hz

    # 噪声基底估计（中位数，抗峰值）
    noise_floor_db = float(np.median(psd_db))

    # 分类
    itype, confidence, peak_freq, inr = classify_interference(
        psd_db, freqs_hz, noise_floor_db
    )

    # 平均功率
    avg_power_db = float(np.mean(psd_db))

    # 峰值
    peak_idx = np.argmax(psd_db)
    peak_power = float(psd_db[peak_idx])

    return GNSSBandResult(
        band_name=band_name,
        center_freq_hz=center_freq_hz,
        bandwidth_hz=sample_rate_hz,
        power_dbm=avg_power_db,
        noise_floor_dbm=noise_floor_db,
        inr_db=inr,
        peak_freq_hz=peak_freq,
        peak_power_dbm=peak_power,
        interference_type=itype,
        confidence=confidence,
    )


def monitor_all_gnss_bands(
    iq_by_band: Dict[str, np.ndarray],
    sample_rate_hz: float,
) -> List[GNSSBandResult]:
    """
    监测所有 GNSS 频带。

    参数：
        iq_by_band: {band_name: iq_samples}，每个频带的IQ数据
        sample_rate_hz: 采样率

    返回：
        各频带监测结果列表
    """
    results = []
    for band_name, iq in iq_by_band.items():
        if band_name in GNSS_BANDS:
            center = GNSS_BANDS[band_name]["center_freq_hz"]
        else:
            center = 0.0
        result = monitor_gnss_band(iq, center, sample_rate_hz, band_name)
        results.append(result)
    return results


def generate_interference_alerts(
    results: List[GNSSBandResult],
    inr_warning_db: float = 10.0,
    inr_critical_db: float = 20.0,
) -> List[InterferenceAlert]:
    """
    从监测结果生成干扰告警。

    参数：
        results: 各频带监测结果
        inr_warning_db: 告警门限（dB）
        inr_critical_db: 严重门限（dB）

    返回：
        干扰告警列表
    """
    alerts = []
    for r in results:
        if r.interference_type == InterferenceType.NONE:
            continue

        if r.inr_db >= inr_critical_db:
            severity = "critical"
        elif r.inr_db >= inr_warning_db:
            severity = "high"
        elif r.inr_db >= 3.0:
            severity = "medium"
        else:
            severity = "low"

        # 处理建议
        if r.interference_type == InterferenceType.CW:
            rec = f"窄带连续波干扰，建议调谐避开 {r.peak_freq_hz/1e6:.3f} MHz，或使用窄带陷波滤波器"
        elif r.interference_type == InterferenceType.NARROWBAND:
            rec = "窄带干扰，建议使用GP全向天线检测方位，切换八木天线测向定位干扰源"
        elif r.interference_type == InterferenceType.WIDEBAND:
            rec = "宽带干扰，可能来自邻道设备或大功率发射机，建议检查周边电磁环境"
        else:
            rec = "检测到未知干扰，建议持续监测并记录频谱数据"

        alerts.append(InterferenceAlert(
            band_name=r.band_name,
            interference_type=r.interference_type.value,
            inr_db=r.inr_db,
            peak_freq_hz=r.peak_freq_hz,
            severity=severity,
            recommendation=rec,
        ))

    return alerts


def interference_direction_finding(
    rssi_by_azimuth: Dict[float, float],
) -> Dict[str, Any]:
    """
    基于 RSSI-方位角数据估算干扰源方向。
    （GP全向天线检测到干扰 → 切换八木天线扫描 → RSSI最强方向即干扰源方向）

    参数：
        rssi_by_azimuth: {方位角度: RSSI dBm}

    返回：
        干扰源方向估算
    """
    if not rssi_by_azimuth:
        return {"error": "无数据"}

    angles = np.array([float(a) for a in rssi_by_azimuth.keys()])
    rssi = np.array([float(v) for v in rssi_by_azimuth.values()])

    # 找 RSSI 最强方向
    peak_idx = np.argmax(rssi)
    peak_angle = angles[peak_idx]
    peak_rssi = rssi[peak_idx]

    # 质心法精化（加权平均）
    weights = np.maximum(0, rssi - np.median(rssi))
    if np.sum(weights) > 0:
        refined_angle = np.average(angles, weights=weights)
    else:
        refined_angle = peak_angle

    # 对称性检查（区分主瓣和镜像）
    sorted_angles = np.sort(angles)
    symmetric_angle = (peak_angle + 180) % 360

    return {
        "estimated_direction_deg": round(float(refined_angle), 1),
        "peak_rssi_dbm": round(float(peak_rssi), 1),
        "confidence": "high" if np.max(rssi) - np.median(rssi) > 10 else "medium",
        "note": "八木天线RSSI扫描质心法定位，建议多角度验证",
        "symmetric_check": f"对称方向 {symmetric_angle:.0f}° 需验证排除镜像",
    }


# ============================================================
# 真实串口 GNSS 定位封装（骨架，等真硬件调试）
# ------------------------------------------------------------
# 与上面的 SDR 干扰监测解耦：这里只负责从串口 GNSS 模块读 NMEA，
# 给上层（状态面板 / 天空图）提供一个干净的 get_position()。
# 无真实定位时 source="none"、坐标为 None，绝不造假。
# 解析/串口细节见 mbdsdr_ai/serial_gnss.py。
# ============================================================

from dataclasses import dataclass as _dc
from typing import Optional as _Opt

try:
    from .serial_gnss import SerialGNSSReader
except Exception:  # pragma: no cover - 包外直接运行/缺 pyserial 时降级
    try:
        from serial_gnss import SerialGNSSReader  # type: ignore
    except Exception:
        SerialGNSSReader = None  # type: ignore


@_dc
class GNSSPosition:
    """一次真实 GNSS 定位快照。无数据时 source='none'、坐标为 None。"""
    source: str = "none"            # "real" / "none"
    lat: _Opt[float] = None
    lon: _Opt[float] = None
    alt: _Opt[float] = None
    sats: _Opt[int] = None
    hdop: _Opt[float] = None
    speed: _Opt[float] = None       # km/h
    course: _Opt[float] = None       # 度
    utc_time: _Opt[str] = None
    timestamp: _Opt[float] = None


class RealGNSSMonitor:
    """封装 SerialGNSSReader，向上层暴露 get_position()。

    用法::
        m = RealGNSSMonitor()
        m.start()                 # auto_detect；找不到设备也不崩
        pos = m.get_position()     # GNSSPosition；无 fix 时 source="none"
        m.stop()
    """

    def __init__(self, port: Optional[str] = None, baudrate: Optional[int] = None):
        self.port = port
        self.baudrate = baudrate
        self._reader = SerialGNSSReader() if SerialGNSSReader is not None else None
        self._started = False

    def start(self) -> bool:
        if self._reader is None:
            return False
        try:
            self._started = bool(self._reader.start(self.port, self.baudrate))
        except Exception:
            self._started = False
        return self._started

    def stop(self):
        if self._reader is not None:
            try:
                self._reader.stop()
            except Exception:
                pass
        self._started = False

    def get_position(self) -> GNSSPosition:
        """返回最新定位。无串口/无 fix 时 source='none'，坐标全 None。"""
        if self._reader is None:
            return GNSSPosition(source="none")
        try:
            fix = self._reader.get_fix()
        except Exception:
            return GNSSPosition(source="none")
        if not fix or fix.get("source") != "real":
            return GNSSPosition(source="none")
        return GNSSPosition(
            source="real",
            lat=fix.get("latitude"),
            lon=fix.get("longitude"),
            alt=fix.get("altitude_m"),
            sats=fix.get("satellites"),
            hdop=fix.get("hdop"),
            speed=fix.get("speed_kmh"),
            course=fix.get("course_deg"),
            utc_time=fix.get("utc_time"),
            timestamp=fix.get("timestamp"),
        )

    # -- 天空图卫星分布（GSV/GSA）透传 -------------------------------------
    def get_gsv_frames(self):
        """透传 reader.get_gsv_frames()：各星座最新可见卫星列表；无数据 []。"""
        if self._reader is None:
            return []
        try:
            return self._reader.get_gsv_frames()
        except Exception:
            return []

    def get_gsa(self):
        """透传 reader.get_gsa()：最新 GSA（used 卫星 / 定位模式）；无数据 None。"""
        if self._reader is None:
            return None
        try:
            return self._reader.get_gsa()
        except Exception:
            return None
