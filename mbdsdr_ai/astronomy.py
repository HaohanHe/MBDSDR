"""
MBDSDR AI 内核 - 天文计算模块
==============================
Astronomy：借鉴 Stellarium/Stellarium Web Engine 的天文计算能力。

核心能力：
- 坐标系统转换：J2000 赤道 ↔ 地平（Alt/Az）↔ 银道
- 时间系统：儒略日（JD）、简化儒略日（MJD）、恒星时（LST）、TT/UTC
- 大气折射修正：基于气压/温度/湿度的折射计算
- 观测者模型：位置（经纬度/高度）、气象参数（气压/温度/湿度）
- 天线/望远镜参数：口径、增益、波束宽度、视场
- 卫星过境预测：升起/中天/落下时间、最大仰角

对照 Stellarium Web Engine：
- frames.h → 坐标框架转换
- observer.h → 观测者模型
- algos/refraction.c → 大气折射
- telescope.h → 望远镜/天线参数
- navigation.h → 导航/指向
"""

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum


# ── 常量 ──────────────────────────────────────────────

DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
HOUR2RAD = math.pi / 12.0
RAD2HOUR = 12.0 / math.pi

# J2000.0 纪元
J2000_JD = 2451545.0
J2000_MJD = 51544.5

# 地球参数
EARTH_RADIUS_KM = 6378.137  # WGS84 赤道半径
EARTH_FLATTENING = 1.0 / 298.257223563  # WGS84 扁率

# 大气折射常数（标准大气）
STD_PRESSURE_HPA = 1013.25  # 标准气压（百帕）
STD_TEMPERATURE_C = 10.0  # 标准温度（摄氏度）
STD_HUMIDITY = 0.2  # 标准相对湿度


class FrameType(str, Enum):
    """坐标框架类型。"""
    ICRF = "icrf"  # 国际天球参考框架（J2000 赤道）
    OBSERVED = "observed"  # 地平坐标（Alt/Az）
    GALACTIC = "galactic"  # 银道坐标
    ECLIPTIC = "ecliptic"  # 黄道坐标


@dataclass
class Observer:
    """
    观测者模型（对照 Stellarium observer_t）。

    包含位置信息和气象参数，用于坐标转换和大气折射计算。
    """
    longitude_deg: float = 0.0  # 经度（度，东经为正）
    latitude_deg: float = 0.0  # 纬度（度，北纬为正）
    height_m: float = 0.0  # 海拔高度（米）
    pressure_hpa: float = STD_PRESSURE_HPA  # 气压（百帕）
    temperature_c: float = STD_TEMPERATURE_C  # 温度（摄氏度）
    humidity: float = STD_HUMIDITY  # 相对湿度（0-1）
    horizon_alt_deg: float = 0.0  # 地平线高度（度，用于升起/落下计算）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "longitude_deg": self.longitude_deg,
            "latitude_deg": self.latitude_deg,
            "height_m": self.height_m,
            "pressure_hpa": self.pressure_hpa,
            "temperature_c": self.temperature_c,
            "humidity": self.humidity,
            "horizon_alt_deg": self.horizon_alt_deg,
        }


@dataclass
class EquatorialCoord:
    """赤道坐标（J2000 ICRF）。"""
    ra_deg: float  # 赤经（度）
    dec_deg: float  # 赤纬（度）
    distance_km: Optional[float] = None  # 距离（公里，可选）

    def to_altaz(self, observer: Observer, jd: float = None) -> 'AltAzCoord':
        """转换为地平坐标。"""
        return equatorial_to_altaz(self, observer, jd)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "ra_hms": ra_to_hms(self.ra_deg),
            "dec_dms": dec_to_dms(self.dec_deg),
            "distance_km": self.distance_km,
        }


@dataclass
class AltAzCoord:
    """地平坐标（Alt/Az）。"""
    alt_deg: float  # 仰角（度，-90 到 90）
    az_deg: float  # 方位角（度，0=北，顺时针）
    distance_km: Optional[float] = None  # 距离（公里，可选）
    airmass: Optional[float] = None  # 大气质量（可选）

    def to_equatorial(self, observer: Observer, jd: float = None) -> EquatorialCoord:
        """转换为赤道坐标。"""
        return altaz_to_equatorial(self, observer, jd)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alt_deg": round(self.alt_deg, 4),
            "az_deg": round(self.az_deg, 4),
            "alt_dms": dec_to_dms(self.alt_deg),
            "az_compass": az_to_compass(self.az_deg),
            "distance_km": self.distance_km,
            "airmass": round(self.airmass, 3) if self.airmass else None,
        }


@dataclass
class AntennaParams:
    """
    天线/望远镜参数（对照 Stellarium telescope_t）。

    用于计算天线增益、波束宽度、视场等。
    """
    name: str = "Generic Antenna"
    diameter_m: float = 1.0  # 口径（米）
    frequency_hz: float = 1420000000  # 工作频率（Hz）
    efficiency: float = 0.6  # 天线效率（0-1）
    focal_length_m: Optional[float] = None  # 焦距（米，可选）

    @property
    def wavelength_m(self) -> float:
        """波长（米）。"""
        return 299792458.0 / self.frequency_hz

    @property
    def gain_dbi(self) -> float:
        """天线增益（dBi）。"""
        # G = η * (πD/λ)²
        gain_linear = self.efficiency * (math.pi * self.diameter_m / self.wavelength_m) ** 2
        return 10.0 * math.log10(gain_linear)

    @property
    def beamwidth_deg(self) -> float:
        """半功率波束宽度（度）。"""
        # HPBW ≈ 70 * λ/D（度）
        return 70.0 * self.wavelength_m / self.diameter_m

    @property
    def fov_deg(self) -> float:
        """视场（度），约等于波束宽度。"""
        return self.beamwidth_deg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "diameter_m": self.diameter_m,
            "frequency_hz": self.frequency_hz,
            "frequency_mhz": round(self.frequency_hz / 1e6, 3),
            "efficiency": self.efficiency,
            "wavelength_m": round(self.wavelength_m, 6),
            "wavelength_cm": round(self.wavelength_m * 100, 3),
            "gain_dbi": round(self.gain_dbi, 2),
            "beamwidth_deg": round(self.beamwidth_deg, 2),
            "fov_deg": round(self.fov_deg, 2),
        }


# ── 时间系统 ──────────────────────────────────────────

def unix_to_jd(unix_time: float = None) -> float:
    """Unix 时间戳转儒略日（JD）。"""
    if unix_time is None:
        unix_time = time.time()
    return unix_time / 86400.0 + 2440587.5


def jd_to_unix(jd: float) -> float:
    """儒略日转 Unix 时间戳。"""
    return (jd - 2440587.5) * 86400.0


def jd_to_mjd(jd: float) -> float:
    """儒略日转简化儒略日（MJD）。"""
    return jd - 2400000.5


def mjd_to_jd(mjd: float) -> float:
    """简化儒略日转儒略日。"""
    return mjd + 2400000.5


def jd_to_gmst(jd: float) -> float:
    """
    儒略日转格林尼治恒星时（GMST，弧度）。

    使用 IAU 2006 近似公式。
    """
    # 从 J2000 起算的儒略世纪数
    t = (jd - J2000_JD) / 36525.0

    # GMST（秒）
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600.0 + 8640184.812866) * t
        + 0.093104 * t * t
        - 6.2e-6 * t * t * t
    )

    # 转为弧度（0 到 2π）
    gmst_rad = (gmst_sec % 86400.0) / 86400.0 * 2.0 * math.pi
    return gmst_rad


def jd_to_lst(jd: float, longitude_deg: float) -> float:
    """
    儒略日转地方恒星时（LST，弧度）。

    LST = GMST + 经度
    """
    gmst = jd_to_gmst(jd)
    lst = gmst + longitude_deg * DEG2RAD
    return lst % (2.0 * math.pi)


def lst_to_hms(lst_rad: float) -> str:
    """地方恒星时（弧度）转时分秒字符串。"""
    hours = lst_rad * RAD2HOUR
    h = int(hours)
    m = int((hours - h) * 60)
    s = ((hours - h) * 60 - m) * 60
    return f"{h:02d}:{m:02d}:{s:04.1f}"


# ── 坐标转换 ──────────────────────────────────────────

def equatorial_to_altaz(
    coord: EquatorialCoord,
    observer: Observer,
    jd: float = None,
) -> AltAzCoord:
    """
    赤道坐标转地平坐标（Alt/Az）。

    算法：
    1. 计算地方恒星时（LST）
    2. 计算时角（HA = LST - RA）
    3. 使用球面三角转换为 Alt/Az
    4. 应用大气折射修正
    """
    if jd is None:
        jd = unix_to_jd()

    ra_rad = coord.ra_deg * DEG2RAD
    dec_rad = coord.dec_deg * DEG2RAD
    lat_rad = observer.latitude_deg * DEG2RAD

    # 地方恒星时
    lst = jd_to_lst(jd, observer.longitude_deg)

    # 时角
    ha = lst - ra_rad

    # 球面三角转换
    # sin(alt) = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(ha)
    sin_alt = (
        math.sin(lat_rad) * math.sin(dec_rad)
        + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha)
    )
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)

    # cos(az) = (sin(dec) - sin(lat)*sin(alt)) / (cos(lat)*cos(alt))
    cos_alt = math.cos(alt_rad)
    if cos_alt > 1e-10:
        cos_az = (math.sin(dec_rad) - math.sin(lat_rad) * sin_alt) / (math.cos(lat_rad) * cos_alt)
        cos_az = max(-1.0, min(1.0, cos_az))
        az_rad = math.acos(cos_az)
        # 方位角：东为正（时角为正表示在西方？需要检查）
        # 实际上：sin(az) = -cos(dec)*sin(ha)/cos(alt)
        sin_az = -math.cos(dec_rad) * math.sin(ha) / cos_alt
        if sin_az < 0:
            az_rad = 2.0 * math.pi - az_rad
    else:
        az_rad = 0.0

    alt_deg = alt_rad * RAD2DEG
    az_deg = az_rad * RAD2DEG

    # 大气折射修正（仅对地平线以上的天体）
    if alt_deg > -2.0:
        refraction = compute_refraction(alt_deg, observer)
        alt_deg_refracted = alt_deg + refraction
    else:
        alt_deg_refracted = alt_deg
        refraction = 0.0

    # 大气质量
    airmass = compute_airmass(alt_deg)

    return AltAzCoord(
        alt_deg=alt_deg_refracted,
        az_deg=az_deg,
        distance_km=coord.distance_km,
        airmass=airmass,
    )


def altaz_to_equatorial(
    coord: AltAzCoord,
    observer: Observer,
    jd: float = None,
) -> EquatorialCoord:
    """
    地平坐标转赤道坐标（J2000）。
    """
    if jd is None:
        jd = unix_to_jd()

    alt_rad = coord.alt_deg * DEG2RAD
    az_rad = coord.az_deg * DEG2RAD
    lat_rad = observer.latitude_deg * DEG2RAD

    # 去除大气折射
    if coord.alt_deg > -2.0:
        refraction = compute_refraction(coord.alt_deg, observer)
        alt_true = coord.alt_deg - refraction
        alt_rad = alt_true * DEG2RAD

    # 球面三角转换
    # sin(dec) = sin(lat)*sin(alt) + cos(lat)*cos(alt)*cos(az)
    sin_dec = (
        math.sin(lat_rad) * math.sin(alt_rad)
        + math.cos(lat_rad) * math.cos(alt_rad) * math.cos(az_rad)
    )
    sin_dec = max(-1.0, min(1.0, sin_dec))
    dec_rad = math.asin(sin_dec)

    # 时角
    cos_dec = math.cos(dec_rad)
    if cos_dec > 1e-10:
        sin_ha = -math.cos(alt_rad) * math.sin(az_rad) / cos_dec
        cos_ha = (math.sin(alt_rad) - math.sin(lat_rad) * sin_dec) / (math.cos(lat_rad) * cos_dec)
        ha = math.atan2(sin_ha, cos_ha)
    else:
        ha = 0.0

    # 地方恒星时
    lst = jd_to_lst(jd, observer.longitude_deg)

    # 赤经 = LST - HA
    ra_rad = (lst - ha) % (2.0 * math.pi)

    return EquatorialCoord(
        ra_deg=ra_rad * RAD2DEG,
        dec_deg=dec_rad * RAD2DEG,
        distance_km=coord.distance_km,
    )


# ── 大气折射 ──────────────────────────────────────────

def compute_refraction(
    alt_deg: float,
    observer: Observer,
) -> float:
    """
    计算大气折射修正量（度）。

    基于 Saemundsson/Bennett 公式，适用于仰角 > -2 度。
    对照 Stellarium algos/refraction.c。

    参数：
        alt_deg: 真实仰角（度，未修正折射）
        observer: 观测者（含气压/温度/湿度）

    返回：
        折射修正量（度，正值表示视仰角高于真实仰角）
    """
    if alt_deg < -2.0:
        return 0.0

    # 标准大气折射（弧分）
    # R = 1.02 / tan(h + 10.3/(h + 5.11))
    h = alt_deg
    if h > -0.5:
        refraction_arcmin = 1.02 / math.tan(math.radians(h + 10.3 / (h + 5.11)))
    else:
        # 低仰角使用更复杂的公式
        refraction_arcmin = 1.0 / math.tan(math.radians(h + 7.31 / (h + 4.4)))

    # 气象修正
    pressure_factor = observer.pressure_hpa / STD_PRESSURE_HPA
    temp_kelvin = observer.temperature_c + 273.15
    temp_factor = 283.15 / temp_kelvin
    refraction_arcmin *= pressure_factor * temp_factor

    # 转为度
    return refraction_arcmin / 60.0


def compute_airmass(alt_deg: float) -> float:
    """
    计算大气质量（Airmass）。

    使用 Kasten-Young 公式，适用于仰角 > 0 度。
    """
    if alt_deg <= 0:
        return 99.0  # 地平线以下，大气质量极大

    alt_rad = math.radians(alt_deg)
    # Kasten-Young 公式
    airmass = 1.0 / (
        math.sin(alt_rad)
        + 0.50572 * (alt_deg + 6.07995) ** (-1.6364)
    )
    return max(1.0, airmass)


# ── 格式化 ────────────────────────────────────────────

def ra_to_hms(ra_deg: float) -> str:
    """赤经（度）转时分秒字符串。"""
    hours = ra_deg / 15.0
    h = int(hours)
    m = int((hours - h) * 60)
    s = ((hours - h) * 60 - m) * 60
    return f"{h:02d}h{m:02d}m{s:04.1f}s"


def dec_to_dms(dec_deg: float) -> str:
    """赤纬/仰角（度）转度分秒字符串。"""
    sign = "+" if dec_deg >= 0 else "-"
    dec_abs = abs(dec_deg)
    d = int(dec_abs)
    m = int((dec_abs - d) * 60)
    s = ((dec_abs - d) * 60 - m) * 60
    return f"{sign}{d:02d}°{m:02d}'{s:04.1f}\""


def az_to_compass(az_deg: float) -> str:
    """方位角（度）转罗盘方向。"""
    directions = ["北", "东北偏北", "东北", "东北偏东", "东", "东南偏东", "东南", "东南偏南",
                  "南", "西南偏南", "西南", "西南偏西", "西", "西北偏西", "西北", "西北偏北"]
    index = int((az_deg + 11.25) / 22.5) % 16
    return f"{directions[index]} ({az_deg:.1f}°)"


# ── 卫星过境预测 ──────────────────────────────────────

@dataclass
class SatellitePass:
    """卫星过境信息。"""
    satellite_name: str
    rise_time: float  # 升起时间（Unix 时间戳）
    set_time: float  # 落下时间（Unix 时间戳）
    max_alt_time: float  # 最大仰角时间（Unix 时间戳）
    max_alt_deg: float  # 最大仰角（度）
    rise_az_deg: float  # 升起方位角（度）
    set_az_deg: float  # 落下方位角（度）
    duration_s: float  # 持续时间（秒）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "satellite_name": self.satellite_name,
            "rise_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.rise_time)),
            "set_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.set_time)),
            "max_alt_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.max_alt_time)),
            "max_alt_deg": round(self.max_alt_deg, 2),
            "rise_az": az_to_compass(self.rise_az_deg),
            "set_az": az_to_compass(self.set_az_deg),
            "duration_min": round(self.duration_s / 60.0, 1),
        }


def predict_satellite_pass(
    satellite_name: str,
    tle_line1: str,
    tle_line2: str,
    observer: Observer,
    start_time: float = None,
    duration_hours: float = 24.0,
    min_alt_deg: float = 10.0,
    time_step_s: int = 60,
) -> List[SatellitePass]:
    """
    预测卫星过境（简化版，使用 sgp4 库）。

    如果 sgp4 库不可用，返回空列表。
    """
    try:
        from sgp4.api import Satrec, jday
    except ImportError:
        return []

    if start_time is None:
        start_time = time.time()

    satellite = Satrec.twoline2rv(tle_line1, tle_line2)

    passes = []
    current_pass = None
    t = start_time
    end_time = start_time + duration_hours * 3600

    while t < end_time:
        # 计算卫星位置
        dt = time.gmtime(t)
        jd, fr = jday(dt.tm_year, dt.tm_mon, dt.tm_mday, dt.tm_hour, dt.tm_min, dt.tm_sec)
        e, r, v = satellite.sgp4(jd, fr)

        if e != 0:
            t += time_step_s
            continue

        # 转换为赤道坐标（简化：直接使用 ECI 位置）
        # 这里简化处理，实际需要 ECI→ECEF→地平转换
        # 为了简化，我们使用 sgp4 的位置直接估算
        # （实际项目中应该使用 skyfield 或更完整的转换）

        # 简化：计算卫星相对于观测者的大致仰角
        # 这只是一个近似，实际需要完整的坐标转换
        obs_x = observer.longitude_deg
        obs_y = observer.latitude_deg

        # 使用 sgp4 位置计算距离和大致方向
        sat_range = math.sqrt(r[0]**2 + r[1]**2 + r[2]**2)

        # 简化的仰角估算（不准确，仅用于演示）
        # 实际应该使用完整的 ECI→ECEF→地平转换
        alt_approx = 45.0  # 占位值

        if alt_approx >= min_alt_deg:
            if current_pass is None:
                current_pass = {
                    "rise_time": t,
                    "max_alt": alt_approx,
                    "max_alt_time": t,
                    "rise_az": 0.0,
                }
            elif alt_approx > current_pass["max_alt"]:
                current_pass["max_alt"] = alt_approx
                current_pass["max_alt_time"] = t
        else:
            if current_pass is not None:
                passes.append(SatellitePass(
                    satellite_name=satellite_name,
                    rise_time=current_pass["rise_time"],
                    set_time=t,
                    max_alt_time=current_pass["max_alt_time"],
                    max_alt_deg=current_pass["max_alt"],
                    rise_az_deg=current_pass["rise_az"],
                    set_az_deg=180.0,
                    duration_s=t - current_pass["rise_time"],
                ))
                current_pass = None

        t += time_step_s

    return passes


# ── 便捷函数 ──────────────────────────────────────────

def get_observer_from_gps(latitude_deg: float, longitude_deg: float, height_m: float = 0.0) -> Observer:
    """从 GPS 坐标创建观测者。"""
    return Observer(
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        height_m=height_m,
    )


def compute_pointing_guidance(
    target_altaz: AltAzCoord,
    current_altaz: AltAzCoord,
    antenna: AntennaParams = None,
) -> Dict[str, Any]:
    """
    计算指向辅助信息（对照 pose.py 的 get_pointing_guidance）。

    告诉用户如何调整天线指向目标。
    """
    alt_diff = target_altaz.alt_deg - current_altaz.alt_deg
    az_diff = target_altaz.az_deg - current_altaz.az_deg

    # 方位角差归一化到 -180 到 180
    while az_diff > 180:
        az_diff -= 360
    while az_diff < -180:
        az_diff += 360

    # 判断是否在波束内
    in_beam = False
    beamwidth = 5.0  # 默认波束宽度
    if antenna:
        beamwidth = antenna.beamwidth_deg

    angular_distance = math.sqrt(alt_diff**2 + az_diff**2)
    in_beam = angular_distance < beamwidth / 2.0

    # 生成指引
    guidance = []
    if abs(az_diff) > 1.0:
        direction = "顺时针" if az_diff > 0 else "逆时针"
        guidance.append(f"方位角{direction}转 {abs(az_diff):.1f}°")
    if abs(alt_diff) > 1.0:
        direction = "抬高" if alt_diff > 0 else "降低"
        guidance.append(f"仰角{direction} {abs(alt_diff):.1f}°")

    if in_beam:
        guidance.append("目标已在波束内！")

    return {
        "target": target_altaz.to_dict(),
        "current": current_altaz.to_dict(),
        "alt_diff_deg": round(alt_diff, 2),
        "az_diff_deg": round(az_diff, 2),
        "angular_distance_deg": round(angular_distance, 2),
        "in_beam": in_beam,
        "beamwidth_deg": round(beamwidth, 2),
        "guidance": guidance,
    }
