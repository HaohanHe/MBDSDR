"""
MBDSDR 新时空 —— 卫星过境时间线 / 授时状态 / 频率-轨道关系
================================================================

本模块为"新时空"面板提供三块纯计算能力：

1. ``TimelineEngine.compute_passes``
   用 SGP4 真算卫星位置（TEME→ECEF→站心 ENU），在 [now, now+hours_ahead]
   时间窗内检测仰角穿越最低仰角的过境事件，输出每次过境的升起 / 中天 /
   落下时间、方位、距离与逐点轨迹，并给出多普勒频移。
   观测站坐标由调用方逐次传入，本模块不持有、不预存任何默认台站坐标。

2. ``TimelineEngine.compute_time_sync``
   授时状态。GNSS RMC 有效优先；否则尝试 NTP；两者都没有 / 都失败时
   显式返回"未同步"，绝不拿系统时间冒充已同步。

3. ``TimelineEngine.compute_freq_orbit``
   从 TLE 直接解析轨道根数（倾角 / 偏心率 / 平均运动），由平均运动推
   半长轴、平均高度与周期，并关联下行频率，供频率-轨道散点图使用。

设计约束（硬红线）：
- 观测站经纬度 / 海拔是必填参数，不设默认值，不硬编码任何城市坐标。
- 无 GNSS/NTP 信号时 ``TimeSyncStatus.synchronized=False``，detail="未同步"。
- 不伪造 TLE、不伪造轨道数据、不预存地区台站列表。
"""

import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

from sgp4.api import Satrec, jday

# 复用 new_spacetime 里已经写好的授时 / GNSS 解析能力（解耦：不依赖 TLE 模块）
try:
    from .new_spacetime import (
        parse_gnss_rmc,
        get_ntp_time,
        NTPError,
        DEFAULT_NTP_SERVERS,
    )
except ImportError:  # 脚本 / 直接运行时
    from new_spacetime import (  # type: ignore
        parse_gnss_rmc,
        get_ntp_time,
        NTPError,
        DEFAULT_NTP_SERVERS,
    )


# ============================================================
# 物理常数（WGS-84 椭球 + 光速）
# ============================================================

WGS84_A_KM = 6378.137          # 地球长半轴（km）
WGS84_E2 = 0.00669437999014    # 偏心率平方
MU_EARTH_KM3_S2 = 398600.4418  # 地球引力常数 GM（km^3/s^2）
SPEED_OF_LIGHT_KM_S = 299792.458  # 光速（km/s）


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PassPoint:
    """过境轨迹上的一个采样点。"""
    time_utc: datetime
    azimuth_deg: float
    elevation_deg: float
    range_km: float
    doppler_hz: float


@dataclass
class SatellitePass:
    """一次完整的卫星过境事件。"""
    name: str
    rise_time: Optional[datetime]
    rise_azimuth: float
    max_time: Optional[datetime]
    max_elevation: float
    max_azimuth: float
    set_time: Optional[datetime]
    set_azimuth: float
    duration_sec: float
    trajectory: List[PassPoint] = field(default_factory=list)
    frequency_hz: float = 0.0
    max_doppler_hz: float = 0.0


@dataclass
class TimeSyncStatus:
    """授时状态。"""
    synchronized: bool
    source: str           # "gnss" / "ntp" / "none"
    utc_time: Optional[datetime]
    offset_ms: Optional[float]
    detail: str           # 人类可读说明


@dataclass
class FreqOrbitPoint:
    """频率-轨道关系散点。"""
    name: str
    frequency_mhz: float
    altitude_km: float
    inclination_deg: float
    period_min: float
    eccentricity: float
    catnr: int


# ============================================================
# 坐标变换工具
# ============================================================

def _gmst_rad(jd_ut1: float) -> float:
    """由 UT1 儒略日计算格林尼治平恒星时（弧度）。

    使用 IAU 1982  GMST 表达式（Vallado 式 3-54）：
        GMST(s) = 67310.54841 + (876600*3600)*T + 0.093104*T^2 - 6.2e-6*T^3
    其中 T = (JD - 2451545.0)/36525。再对 86400s 取模，秒换算弧度。
    """
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    gmst_sec = gmst_sec % 86400.0
    # 1 小时角秒 = 15 角秒 = 1/240 度
    return math.radians(gmst_sec / 240.0)


def _observer_ecef(lat_deg: float, lon_deg: float, alt_km: float
                   ) -> Tuple[float, float, float]:
    """WGS-84 大地坐标 → ECEF 直角坐标（km）。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    n = WGS84_A_KM / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * math.cos(lat) * math.cos(lon)
    y = (n + alt_km) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return x, y, z


def _teme_to_ecef(x: float, y: float, z: float, gmst: float
                  ) -> Tuple[float, float, float]:
    """TEME → ECEF：绕 z 轴旋转 -GMST。"""
    c = math.cos(gmst)
    s = math.sin(gmst)
    return (c * x + s * y,
            -s * x + c * y,
            z)


def _ecef_to_enu(dx: float, dy: float, dz: float,
                 lat_deg: float, lon_deg: float
                 ) -> Tuple[float, float, float]:
    """ECEF 站心向量差 → ENU（东 / 北 / 上，km）。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    e = -sin_lon * dx + cos_lon * dy
    n = (-sin_lat * cos_lon * dx
         - sin_lat * sin_lon * dy
         + cos_lat * dz)
    u = (cos_lat * cos_lon * dx
         + cos_lat * sin_lon * dy
         + sin_lat * dz)
    return e, n, u


# ============================================================
# TimelineEngine
# ============================================================

class TimelineEngine:
    """卫星过境时间线 + 授时状态 + 频率-轨道关系计算引擎。

    本类不持有任何默认观测站坐标；每次 ``compute_passes`` 都必须显式传入
    观测站纬度 / 经度 / 海拔。
    """

    def __init__(self) -> None:
        # 不预存任何台站坐标 / 地区台站列表
        pass

    # --------------------------------------------------------
    # 过境时间线
    # --------------------------------------------------------
    def compute_passes(self,
                       observer_lat: float,
                       observer_lon: float,
                       observer_alt: float,
                       satellites: List[Tuple[str, str, str]],
                       hours_ahead: float = 24.0,
                       min_elevation: float = 5.0,
                       step_sec: float = 30.0,
                       frequencies: Optional[Dict[str, float]] = None
                       ) -> List[SatellitePass]:
        """计算未来 hours_ahead 内所有卫星的过境时间线。

        Parameters
        ----------
        observer_lat, observer_lon, observer_alt : float
            观测站大地坐标（度 / 度 / km）。**必填，无默认值**。
        satellites : list of (name, tle_line1, tle_line2)
            卫星列表。
        hours_ahead : float
            预测窗长度（小时）。
        min_elevation : float
            最低过境仰角（度）。
        step_sec : float
            时间采样步长（秒）。
        frequencies : dict, optional
            ``{name: 下行频率 Hz}``，用于多普勒计算。
        """
        frequencies = frequencies or {}
        obs_ecef = _observer_ecef(observer_lat, observer_lon, observer_alt)
        now = datetime.now(timezone.utc)
        start = now
        end = now + timedelta(hours=hours_ahead)
        n_steps = max(1, int(round(hours_ahead * 3600.0 / step_sec)))

        all_passes: List[SatellitePass] = []

        for name, line1, line2 in satellites:
            try:
                sat = Satrec.twoline2rv(line1, line2)
            except Exception:
                continue
            if sat.error != 0:
                continue

            freq_hz = float(frequencies.get(name, 0.0))

            current: Optional[SatellitePass] = None
            in_pass = False
            traj: List[PassPoint] = []
            max_el = -90.0

            for i in range(n_steps + 1):
                t = start + timedelta(seconds=i * step_sec)
                yy, mm, dd = t.year, t.month, t.day
                jd_int, jd_fr = jday(yy, mm, dd, t.hour, t.minute,
                                     t.second + t.microsecond / 1e6)

                err, r_teme, v_teme = sat.sgp4(jd_int, jd_fr)
                if err != 0:
                    if in_pass:
                        self._finalize_pass(current, traj, freq_hz)
                        all_passes.append(current)
                    in_pass = False
                    current = None
                    traj = []
                    continue

                jd_now = jd_int + jd_fr
                gmst = _gmst_rad(jd_now)
                rs_ecef = _teme_to_ecef(r_teme[0], r_teme[1], r_teme[2], gmst)
                vs_ecef = _teme_to_ecef(v_teme[0], v_teme[1], v_teme[2], gmst)

                dx = rs_ecef[0] - obs_ecef[0]
                dy = rs_ecef[1] - obs_ecef[1]
                dz = rs_ecef[2] - obs_ecef[2]
                e, n, u = _ecef_to_enu(dx, dy, dz, observer_lat, observer_lon)
                range_km = math.sqrt(e * e + n * n + u * u)
                if range_km < 1e-6:
                    continue

                elevation = math.degrees(math.asin(max(-1.0, min(1.0, u / range_km))))
                azimuth = math.degrees(math.atan2(e, n)) % 360.0

                # 视线速度（沿观测者→卫星方向的径向速度，km/s）
                range_rate = ((vs_ecef[0]) * dx +
                              (vs_ecef[1]) * dy +
                              (vs_ecef[2]) * dz) / range_km
                # 约定：接近时多普勒为正（接收频率升高），远离为负。
                doppler_hz = -freq_hz * range_rate / SPEED_OF_LIGHT_KM_S

                if elevation >= min_elevation:
                    if not in_pass:
                        # 升起
                        in_pass = True
                        traj = []
                        max_el = elevation
                        current = SatellitePass(
                            name=name,
                            rise_time=t,
                            rise_azimuth=azimuth,
                            max_time=t,
                            max_elevation=elevation,
                            max_azimuth=azimuth,
                            set_time=None,
                            set_azimuth=0.0,
                            duration_sec=0.0,
                            frequency_hz=freq_hz,
                        )
                    pt = PassPoint(
                        time_utc=t,
                        azimuth_deg=azimuth,
                        elevation_deg=elevation,
                        range_km=range_km,
                        doppler_hz=doppler_hz,
                    )
                    traj.append(pt)
                    if elevation > max_el:
                        max_el = elevation
                        current.max_time = t
                        current.max_elevation = elevation
                        current.max_azimuth = azimuth
                else:
                    if in_pass:
                        # 落下
                        in_pass = False
                        current.set_time = t
                        current.set_azimuth = azimuth
                        self._finalize_pass(current, traj, freq_hz)
                        all_passes.append(current)
                        current = None
                        traj = []

            # 窗末仍在过境中（未等到落下）
            if in_pass and current is not None and current.rise_time is not None:
                current.set_time = end
                current.set_azimuth = traj[-1].azimuth_deg if traj else 0.0
                self._finalize_pass(current, traj, freq_hz)
                all_passes.append(current)

        all_passes.sort(key=lambda p: p.rise_time or end)
        return all_passes

    @staticmethod
    def _finalize_pass(p: SatellitePass,
                       traj: List[PassPoint],
                       freq_hz: float) -> None:
        p.trajectory = traj
        if p.rise_time and p.set_time:
            p.duration_sec = (p.set_time - p.rise_time).total_seconds()
        if traj:
            p.max_doppler_hz = max(abs(pt.doppler_hz) for pt in traj)

    # --------------------------------------------------------
    # 授时状态
    # --------------------------------------------------------
    def compute_time_sync(self,
                         gnss_nmea: Optional[str] = None,
                         ntp_server: Optional[str] = None,
                         try_ntp: bool = True) -> TimeSyncStatus:
        """授时状态探测。

        优先级：GNSS RMC 有效 → NTP → "未同步"。
        系统时间不算"已同步"。
        """
        # 1) GNSS 优先
        if gnss_nmea:
            info = parse_gnss_rmc(gnss_nmea)
            if info and info.get("valid"):
                lat = info.get("latitude")
                lon = info.get("longitude")
                loc = ""
                if lat is not None and lon is not None:
                    loc = f", {lat:.4f}°, {lon:.4f}°"
                return TimeSyncStatus(
                    synchronized=True,
                    source="gnss",
                    utc_time=info.get("utc_time"),
                    offset_ms=None,
                    detail=f"GNSS RMC 有效定位{loc}",
                )

        # 2) NTP
        if try_ntp:
            candidates: List[str] = []
            if ntp_server:
                candidates.append(ntp_server)
            for srv in DEFAULT_NTP_SERVERS:
                if srv not in candidates:
                    candidates.append(srv)
            # 最多尝试 2 个服务器
            for srv in candidates[:2]:
                try:
                    ntp_utc, rtt_ms = get_ntp_time(srv, timeout=3.0)
                    sys_utc = datetime.now(timezone.utc)
                    offset_ms = (ntp_utc - sys_utc).total_seconds() * 1000.0
                    return TimeSyncStatus(
                        synchronized=True,
                        source="ntp",
                        utc_time=ntp_utc,
                        offset_ms=offset_ms,
                        detail=f"NTP: {srv}, RTT {rtt_ms:.0f}ms",
                    )
                except NTPError:
                    continue
                except Exception:
                    continue

        # 3) 都没有 / 都失败 → 未同步
        return TimeSyncStatus(
            synchronized=False,
            source="none",
            utc_time=None,
            offset_ms=None,
            detail="未同步",
        )

    # --------------------------------------------------------
    # 频率-轨道关系
    # --------------------------------------------------------
    def compute_freq_orbit(self,
                           satellites: List[Tuple[str, str, str]],
                           frequencies: Optional[Dict[str, float]] = None
                           ) -> List[FreqOrbitPoint]:
        """从 TLE 提取轨道根数并关联频率。"""
        frequencies = frequencies or {}
        out: List[FreqOrbitPoint] = []
        for name, line1, line2 in satellites:
            try:
                mm = self.tle_mean_motion(line2)          # rev/day
                inc = self.tle_inclination(line2)          # deg
                ecc = self.tle_eccentricity(line2)         # 0..1
                catnr = int(line1[2:7].strip())
            except Exception:
                continue

            # 平均运动（rad/s）→ 半长轴
            n_rad_s = mm * 2.0 * math.pi / 86400.0
            if n_rad_s <= 0:
                continue
            a_km = (MU_EARTH_KM3_S2 / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)
            altitude_km = a_km - WGS84_A_KM
            period_min = 1440.0 / mm

            freq_hz = float(frequencies.get(name, 0.0))
            out.append(FreqOrbitPoint(
                name=name,
                frequency_mhz=freq_hz / 1e6 if freq_hz > 0 else 0.0,
                altitude_km=altitude_km,
                inclination_deg=inc,
                period_min=period_min,
                eccentricity=ecc,
                catnr=catnr,
            ))
        return out

    # --------------------------------------------------------
    # TLE 字段解析（静态工具）
    # --------------------------------------------------------
    @staticmethod
    def tle_mean_motion(line2: str) -> float:
        """从 TLE line2 提取平均运动（rev/day，列 53-63）。"""
        return float(line2[52:63])

    @staticmethod
    def tle_inclination(line2: str) -> float:
        """从 TLE line2 提取倾角（度，列 9-16）。"""
        return float(line2[8:16])

    @staticmethod
    def tle_eccentricity(line2: str) -> float:
        """从 TLE line2 提取偏心率（列 27-33，小数点前补 0.）。"""
        return float("0." + line2[26:33].strip())
