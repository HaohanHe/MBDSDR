# SPDX-License-Identifier: MIT
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
try:
    from .gmst import gmst_rad_iau1982
except ImportError:  # 扁平导入（部分脚本直挂 mbdsdr_ai 目录）
    from gmst import gmst_rad_iau1982
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

try:
    from sgp4.api import Satrec, jday
    _HAS_SGP4 = True
except ImportError:  # sgp4 属 satellite 可选依赖组；缺失时用到才诚实报错
    Satrec = None  # type: ignore[assignment,misc]
    jday = None  # type: ignore[assignment]
    _HAS_SGP4 = False

# TLE 新鲜度分级（与 orbit/sat_passes 同一口径，不另立阈值）
try:
    from .tle_integrity import freshness, accuracy, tracking_policy
except ImportError:  # 脚本 / 直接运行时
    from tle_integrity import freshness, accuracy, tracking_policy  # type: ignore

# 过境网格唯一事实源：粗扫/细扫步长与细扫实现都来自 sat_passes（P1-13）。
# 本模块不再私藏一套「网格点当峰值」的算法——面板与 AI 必须同网格。
try:
    from . import sat_passes as _sat_passes
except ImportError:  # 脚本 / 直接运行时
    import sat_passes as _sat_passes  # type: ignore


def _require_sgp4() -> None:
    if not _HAS_SGP4:
        raise ImportError(
            "sgp4 未安装：过境时间线需要它（pip install sgp4，"
            "或 pip install 'mbdsdr_ai[satellite]'）")


def _resolve_sampling_step(step_sec: Optional[float]) -> float:
    """过境粗扫步长：缺省/非法值一律回落 ``sat_passes.PASS_COARSE_STEP_S``。

    本模块不再自藏「30 秒」字面量——粗扫步长是 P1-13 的单一事实源。
    ``0``（除零）/ 负数 / NaN / Inf 会让 ``hours*3600/step`` 抛异常或退化
    成 1 步，这里统一按缺省处理，不把畸形输入变成无结果的空列表。
    """
    if step_sec is None:
        return float(_sat_passes.PASS_COARSE_STEP_S)
    try:
        value = float(step_sec)
    except (TypeError, ValueError):
        return float(_sat_passes.PASS_COARSE_STEP_S)
    if not math.isfinite(value) or value <= 0:
        return float(_sat_passes.PASS_COARSE_STEP_S)
    return value


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
# 光速（km/s）。保留本模块公开常量以兼容既有导入，值来自 sat_passes 的
# C_LIGHT_MPS，不再自成第二份字面量（多普勒公式见该模块单一实现）。
SPEED_OF_LIGHT_KM_S = _sat_passes.C_LIGHT_MPS / 1000.0


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
    """一次完整的卫星过境事件。

    TLE 老化字段（``tle_age_days`` / ``tle_tier`` / ``accuracy_note``）必须
    随结果一起返回：用一年前的星历算出的过境看起来和新鲜 TLE 一样，不给
    年龄就会静默冒充精确结果。
    """
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
    tle_epoch_utc: Optional[str] = None
    tle_age_days: Optional[float] = None
    tle_tier: str = ""
    tle_tier_label: str = ""
    accuracy_risk: str = ""
    accuracy_note: str = ""
    # 跟踪闸门 + 显示口径（tracking_policy 的完整返回）：面板只读字段，
    # 不再自己比较 tle_tier 或另写一份「过期/失效」文案。
    policy: Dict[str, Any] = field(default_factory=dict)
    # 轨迹采样步长（秒）。用户要能区分「星历精度」与「轨迹绘制精度」；
    # 中天时刻/仰角已由 sat_passes 的 2 s 细扫网格校正（见 _refine_peak），
    # 与 AI 口径一致，不再是「网格点即峰值」。本字段仍只表示**粗扫**步长，
    # AOS/LOS 的二分收敛不改变它的语义。
    sampling_step_s: float = 0.0
    # AOS/LOS 是否由二分收敛到 ``sat_passes.PASS_BISECT_TOL_S``。
    # 任一交叉点区间不成立 / 端点仰角异常 / 传播失败时保留粗扫网格点并置
    # False——不静默声称已收敛。
    aos_refined: bool = False
    los_refined: bool = False
    # 实际生效的升落二分容差（秒）；0.0 表示本次过境没有任何交叉点被二分。
    crossing_tol_s: float = 0.0

    @property
    def crossing_refined(self) -> bool:
        """升起与落下是否都已二分收敛（窗末截断 / 网格兜底时为 False）。"""
        return self.aos_refined and self.los_refined


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
    """频率-轨道关系散点。

    ``frequency_mhz`` 为 ``None`` 表示**频率未知**（真实缺省）；不得用
    ``0.0`` 兼任「无数据」——0 是可有意义的数值，混用会让未知点被画到
    坐标 0 处。
    """
    name: str
    frequency_mhz: Optional[float]
    altitude_km: float
    inclination_deg: float
    period_min: float
    eccentricity: float
    catnr: int

    @property
    def has_frequency(self) -> bool:
        return self.frequency_mhz is not None


# ============================================================
# 坐标变换工具
# ============================================================

def _gmst_rad(jd_ut1: float) -> float:
    """由 UT1 儒略日计算格林尼治平恒星时（弧度）。口径 A。

    IAU 1982 折叠式（Vallado 3-54），委托 gmst.gmst_rad_iau1982。
    其中的世纪增量不可漏，漏掉会让过境 AOS/LOS 偏到小时级。
    """
    return gmst_rad_iau1982(jd_ut1)


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

def _state_at(sat, obs_ecef: Tuple[float, float, float],
              lat_deg: float, lon_deg: float, t: datetime,
              freq_hz: float
              ) -> Optional[Tuple[float, float, float, float]]:
    """单时刻的 ``(仰角°, 方位角°, 距离 km, 多普勒 Hz)``；传播失败返回 None。

    粗扫与 2 s 细扫共用这一份求值，避免每个网格各写一套公式。
    """
    jd_int, jd_fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                         t.second + t.microsecond / 1e6)
    err, r_teme, v_teme = sat.sgp4(jd_int, jd_fr)
    if err != 0:
        return None
    gmst = _gmst_rad(jd_int + jd_fr)
    rs_ecef = _teme_to_ecef(r_teme[0], r_teme[1], r_teme[2], gmst)
    vs_ecef = _teme_to_ecef(v_teme[0], v_teme[1], v_teme[2], gmst)
    dx = rs_ecef[0] - obs_ecef[0]
    dy = rs_ecef[1] - obs_ecef[1]
    dz = rs_ecef[2] - obs_ecef[2]
    e, n, u = _ecef_to_enu(dx, dy, dz, lat_deg, lon_deg)
    range_km = math.sqrt(e * e + n * n + u * u)
    if range_km < 1e-6:
        return None
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, u / range_km))))
    azimuth = math.degrees(math.atan2(e, n)) % 360.0
    # 视线速度（沿观测者→卫星方向的径向速度，km/s）
    range_rate = ((vs_ecef[0]) * dx + (vs_ecef[1]) * dy
                  + (vs_ecef[2]) * dz) / range_km
    # 约定：接近时多普勒为正（接收频率升高），远离为负。
    # 公式唯一实现在 sat_passes.doppler_hz_from_range_rate，本模块不再
    # 重写一份符号约定（两条曲线必须同号同量）。
    doppler_hz = _sat_passes.doppler_hz_from_range_rate(freq_hz, range_rate)
    return elevation, azimuth, range_km, doppler_hz


def _bisect_elevation_crossing(el_at_s, lo_s: float, hi_s: float,
                               el_lo: float, el_hi: float,
                               target: float) -> Optional[float]:
    """在 ``[lo_s, hi_s]``（相对秒）内二分仰角穿越 ``target`` 的时刻。

    容差复用 ``sat_passes.PASS_BISECT_TOL_S``（全仓单一事实源，与
    ``grid_manifest()["crossing_precision_s"]`` 同值），本模块不另写魔数。
    端点必须在门限异侧，且区间宽度必须大于容差；任一采样失败或端点异常时
    返回 ``None``——调用方保留粗扫网格点并置 ``aos_refined``/``los_refined``
    为 False，不拿区间中点冒充收敛值。
    """
    tol = float(_sat_passes.PASS_BISECT_TOL_S)
    if not (math.isfinite(lo_s) and math.isfinite(hi_s)
            and math.isfinite(el_lo) and math.isfinite(el_hi)
            and math.isfinite(target)):
        return None
    if hi_s <= lo_s:
        return None
    if (el_lo < target) == (el_hi < target):
        return None
    if not math.isfinite(tol) or tol <= 0.0 or tol >= (hi_s - lo_s):
        # 容差不小于粗扫区间：没有可观测的细化收益，如实退回网格点。
        return None
    lo, hi = lo_s, hi_s
    flo = el_lo
    for _ in range(64):
        if hi - lo <= tol:
            break
        mid = 0.5 * (lo + hi)
        fm = el_at_s(mid)
        if fm is None or not math.isfinite(fm):
            return None
        if (fm < target) == (flo < target):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


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
                       step_sec: Optional[float] = None,
                       frequencies: Optional[Dict[str, float]] = None,
                       now_unix: Optional[float] = None
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
        step_sec : float, optional
            时间采样步长（秒）。缺省取 ``sat_passes.PASS_COARSE_STEP_S``
            （全仓唯一事实源）；非正/NaN/Inf 同样回落到该值。
        frequencies : dict, optional
            ``{name: 下行频率 Hz}``，用于多普勒计算。
        now_unix : float, optional
            预测起点（Unix UTC 秒）。缺省取全局 ``TimeEngine.now_unix()``，
            与时间机器/快转同源；显式传入只用于复算或测试钉死时刻。
        """
        _require_sgp4()
        step_sec = _resolve_sampling_step(step_sec)
        frequencies = frequencies or {}
        obs_ecef = _observer_ecef(observer_lat, observer_lon, observer_alt)
        # 时间基准只有一个来源（docs/time-source.md）：默认取 TimeEngine。
        # 旧实现用 datetime.now()，时间机器把引擎拨到另一天后面板仍从墙钟
        # 起算，过境列表整段落在错误的日期上。
        if now_unix is None:
            try:
                from .new_spacetime import get_time_engine
            except ImportError:  # 脚本 / 直接运行时
                from new_spacetime import get_time_engine  # type: ignore
            now_unix = float(get_time_engine().now_unix())
        now = datetime.fromtimestamp(float(now_unix), tz=timezone.utc)
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
            # TLE 年龄按本次预测起点计算（生产=墙钟，测试可 monkeypatch now）。
            fresh = freshness(line1, now=now)
            acc = accuracy(fresh)
            tle_meta = {
                "tle_epoch_utc": fresh.get("epoch_utc"),
                "tle_age_days": fresh.get("age_days"),
                "tle_tier": fresh.get("tier", ""),
                "tle_tier_label": fresh.get("label", ""),
                "accuracy_risk": acc.get("risk", ""),
                "accuracy_note": acc.get("note", ""),
                "policy": tracking_policy(fresh, "verified"),
            }

            current: Optional[SatellitePass] = None
            in_pass = False
            traj: List[PassPoint] = []
            # 粗扫网格上的上一个有效点，供 AOS/LOS 二分取异侧端点。
            prev_t: Optional[datetime] = None
            prev_state: Optional[Tuple[float, float, float, float]] = None

            for i in range(n_steps + 1):
                t = start + timedelta(seconds=i * step_sec)
                st = _state_at(sat, obs_ecef, observer_lat, observer_lon, t,
                              freq_hz)
                if st is None:
                    if in_pass and current is not None:
                        self._refine_peak(sat, obs_ecef, observer_lat,
                                          observer_lon, freq_hz, current)
                        self._finalize_pass(current, traj, freq_hz)
                        all_passes.append(current)
                    in_pass = False
                    current = None
                    traj = []
                    prev_t = None
                    prev_state = None
                    continue

                elevation, azimuth, range_km, doppler_hz = st

                if elevation >= min_elevation:
                    if not in_pass:
                        # 升起：在「上一个粗扫点 < 门限」与「本点 ≥ 门限」
                        # 之间二分，失败则保留本网格点并如实标记未收敛。
                        rise_time = t
                        rise_azimuth = azimuth
                        aos_refined = False
                        if (prev_t is not None and prev_state is not None
                                and prev_state[0] < min_elevation):
                            refined = self._refine_crossing(
                                sat, obs_ecef, observer_lat, observer_lon,
                                freq_hz, prev_t, t, prev_state[0], elevation,
                                min_elevation)
                            if refined is not None:
                                rise_time, rise_st = refined
                                rise_azimuth = rise_st[1]
                                aos_refined = True
                        in_pass = True
                        traj = []
                        current = SatellitePass(
                            name=name,
                            rise_time=rise_time,
                            rise_azimuth=rise_azimuth,
                            max_time=t,
                            max_elevation=elevation,
                            max_azimuth=azimuth,
                            set_time=None,
                            set_azimuth=0.0,
                            duration_sec=0.0,
                            frequency_hz=freq_hz,
                            sampling_step_s=step_sec,
                            aos_refined=aos_refined,
                            **tle_meta,
                        )
                    pt = PassPoint(
                        time_utc=t,
                        azimuth_deg=azimuth,
                        elevation_deg=elevation,
                        range_km=range_km,
                        doppler_hz=doppler_hz,
                    )
                    traj.append(pt)
                else:
                    if in_pass:
                        # 落下：同样在上一个「≥ 门限」点与本点之间二分。
                        set_time = t
                        set_azimuth = azimuth
                        los_refined = False
                        if (prev_state is not None
                                and prev_state[0] >= min_elevation):
                            refined = self._refine_crossing(
                                sat, obs_ecef, observer_lat, observer_lon,
                                freq_hz, prev_t, t, prev_state[0], elevation,
                                min_elevation)
                            if refined is not None:
                                set_time, set_st = refined
                                set_azimuth = set_st[1]
                                los_refined = True
                        in_pass = False
                        current.set_time = set_time
                        current.set_azimuth = set_azimuth
                        current.los_refined = los_refined
                        self._refine_peak(sat, obs_ecef, observer_lat,
                                          observer_lon, freq_hz, current)
                        self._finalize_pass(current, traj, freq_hz)
                        all_passes.append(current)
                        current = None
                        traj = []

                prev_t = t
                prev_state = st

            # 窗末仍在过境中（未等到落下）
            if in_pass and current is not None and current.rise_time is not None:
                current.set_time = end
                current.set_azimuth = traj[-1].azimuth_deg if traj else 0.0
                self._refine_peak(sat, obs_ecef, observer_lat, observer_lon,
                                  freq_hz, current)
                self._finalize_pass(current, traj, freq_hz)
                all_passes.append(current)

        all_passes.sort(key=lambda p: p.rise_time or end)
        return all_passes

    @staticmethod
    def _refine_crossing(sat, obs_ecef, lat_deg: float, lon_deg: float,
                         freq_hz: float, lo_t: datetime, hi_t: datetime,
                         el_lo: float, el_hi: float, target: float
                         ) -> Optional[Tuple[datetime,
                                             Tuple[float, float, float,
                                                   float]]]:
        """对相邻粗扫点做 AOS/LOS 二分，返回 ``(时刻, 该时刻状态)``。

        二分容差与手法复用 ``sat_passes``（``PASS_BISECT_TOL_S`` + 过零
        二分）；区间不成立 / 传播失败时返回 ``None``，调用方保留粗扫网格点。
        """
        span = (hi_t - lo_t).total_seconds()
        if span <= 0:
            return None

        def _el(sec: float) -> Optional[float]:
            st = _state_at(sat, obs_ecef, lat_deg, lon_deg,
                           lo_t + timedelta(seconds=sec), freq_hz)
            return st[0] if st else None

        sec = _bisect_elevation_crossing(_el, 0.0, span, el_lo, el_hi,
                                         target)
        if sec is None:
            return None
        t = lo_t + timedelta(seconds=sec)
        st = _state_at(sat, obs_ecef, lat_deg, lon_deg, t, freq_hz)
        if st is None or not math.isfinite(st[0]):
            return None
        return t, st

    @staticmethod
    def _refine_peak(sat, obs_ecef, lat_deg: float, lon_deg: float,
                     freq_hz: float, p: SatellitePass) -> None:
        """用 sat_passes 的 2 s 细扫网格校正中天时刻/仰角/方位（P1-13）。

        过去中天只能取粗扫网格点（面板 60 s），峰值时刻最多偏 30 s、仰角
        差 3–7.5°；现在与 AI 走同一个 ``refine_culmination()``，误差 ≤1 s。
        轨迹点保持原 ``step_sec`` 网格不变（面板绘制依赖它）。
        """
        if p.rise_time is None or p.set_time is None:
            return
        base = p.rise_time
        span = (p.set_time - base).total_seconds()
        if span <= 0:
            return

        def _el_az(sec: float):
            st = _state_at(sat, obs_ecef, lat_deg, lon_deg,
                          base + timedelta(seconds=sec), freq_hz)
            return (st[0], st[1]) if st else None

        try:
            best_sec, best_el = _sat_passes.refine_culmination(
                _el_az, 0.0, span)
        except Exception:
            return
        if best_el <= -900.0:      # 细扫全部失败：保留粗扫结果，不造假
            return
        st = _state_at(sat, obs_ecef, lat_deg, lon_deg,
                      base + timedelta(seconds=best_sec), freq_hz)
        if st is None:
            return
        p.max_time = base + timedelta(seconds=best_sec)
        p.max_elevation = st[0]
        p.max_azimuth = st[1]

    @staticmethod
    def _finalize_pass(p: SatellitePass,
                       traj: List[PassPoint],
                       freq_hz: float) -> None:
        p.trajectory = traj
        if p.rise_time and p.set_time:
            p.duration_sec = (p.set_time - p.rise_time).total_seconds()
        if traj:
            p.max_doppler_hz = max(abs(pt.doppler_hz) for pt in traj)
        if p.aos_refined or p.los_refined:
            p.crossing_tol_s = float(_sat_passes.PASS_BISECT_TOL_S)

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

            # 频率未知 = None（真实缺省），不再用 0.0 兼任「无数据」。
            raw_hz = frequencies.get(name)
            freq_hz = float(raw_hz) if raw_hz is not None else None
            out.append(FreqOrbitPoint(
                name=name,
                frequency_mhz=(freq_hz / 1e6
                               if freq_hz is not None and freq_hz > 0 else None),
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
