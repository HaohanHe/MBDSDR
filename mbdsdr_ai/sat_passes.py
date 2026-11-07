# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 真实卫星过境(pass)预测模块
============================================
依据公开 SGP4/SDP4 标准与站心几何独立实现
--------------------------------------------
本模块实现卫星过境几何与预测，使用 Python ``sgp4`` 库做轨道传播、
``skyfield`` 做时间系统与 ECI→站心地平坐标(az/alt)变换。不链接任何外部
卫星预测软件二进制。

公开标准与方法：

1. TLE 解析 + SGP4 传播
   - 按 Spacetrack Report #3（Hoots & Roeber）及 Vallado 等 2006 修订的
     SGP4/SDP4 标准；本模块用 skyfield.EarthSatellite（内部即
     sgp4.api.Satrec.twoline2rv + sgp4()）。

2. ECI(TEME) → 地面站地平坐标(az/alt)
   - 站心 topocentric (S/E/Z) 变换：slantRange = satECI - obsECI，再旋转到站心；
     高度分量>0 即仰角方向。本模块用 skyfield ``(sat - topo).at(t).altaz()``。

3. 可见性 / 地影
   - 先判仰角>0（在地平线上），再判太阳高度，最后按 Vallado 锥几何判本影/半影/食。
   本模块默认只做几何可见性(仰角>min_alt)；可选 ``require_sunlit`` 走几何地影判断。

4. 过境扫描（rise/set/中天）
   - 沿时间轴步长采样仰角曲线，叠加粗扫+二分过零：
     a) 粗扫 60s 步长定位仰角跨越 min_alt 的区间；
     b) 二分法精确定位 rise(AOS)/set(LOS) 时刻；
     c) pass 内 2s 细采样找最大仰角(中天)时刻与方位。

5. trail 轨迹点采样：见 :func:`sample_trail`，按固定间隔逐时刻采样仰角。

Stellarium Satellites 插件与 gpredict 仅作为技术参考与致谢，本模块未包含其源代码。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from skyfield.api import EarthSatellite, Topos, Time, load as _sky_load
    _HAS_SKYFIELD = True
except ImportError:  # skyfield 属 satellite 可选依赖组；缺它模块仍可导入
    EarthSatellite = None  # type: ignore[assignment,misc]
    Topos = None  # type: ignore[assignment,misc]
    Time = None  # type: ignore[assignment,misc]
    _sky_load = None  # type: ignore[assignment]
    _HAS_SKYFIELD = False

from datetime import datetime, timezone


def _require_skyfield() -> None:
    """skyfield 缺失时给出可操作的诚实报错（而非 AttributeError: NoneType）。"""
    if not _HAS_SKYFIELD:
        raise ImportError(
            "skyfield 未安装：过境预报/多普勒曲线需要它"
            "（pip install 'mbdsdr_ai[satellite]' 或 pip install skyfield）")

__all__ = [
    "GroundStation",
    "SatellitePass",
    "predict_passes",
    "sample_trail",
    "make_timescale",
    "DopplerPoint",
    "compute_doppler_curve",
    "predict_upcoming_passes",
]

try:
    from .time_basis import validate_pass_window
except ImportError:  # 扁平导入（脚本/测试直接 import sat_passes）
    from time_basis import validate_pass_window

# 光速 m/s（c = 299792458 m/s，国际定义）。
C_LIGHT_MPS = 299792458.0

# AU/km 换算（skyfield position.au 为 AU，需转 km 做点积）。
_AU_KM = 149597870.700

# SGP4 传播：Python sgp4 库默认 WGS-72 legacy 模式；对 LEO 过境时刻影响在
# 秒级以内，满足 SDR 预报需求。

# 粗扫/细扫步长（秒）。**全仓唯一事实源**：桌面端过境列表（天空图/新时空面板）
# 必须从这里 import，不许各自再写一套（两套步长会让同一次过境的峰值时刻/AOS/LOS
# 分叉）。粗扫按固定间隔采样仰角曲线，细采样在峰值邻域找中天。
PASS_COARSE_STEP_S = 60.0     # 粗扫：60s 定位过境区间
PASS_FINE_STEP_S = 2.0        # pass 内细采样：找中天/最大仰角
# 过境搜索窗口默认值（小时）。AI/无头面（controller.get_satellite_pass）
# 没有桌面 settings 依赖，默认值只在这里定义一次；桌面面板的默认值来自
# config_schema，经 settings.effective_pass_window_hours() 显式注入。
PASS_WINDOW_HOURS = 24.0
# 内核窗口安全边界（小时）。桌面侧 settings.effective_pass_window_hours()
# 已做 1–72 钳位，但 MCP / agent / CLI 直接调 controller.get_satellite_pass
# 时那个钳位根本不在链路上：hours=10000 → 60 万次 SGP4 传播 + 每段 2s 细扫，
# 服务长时间无响应。数值与 desktop/config_schema.py 的
# pass_window_hours(minimum=1, maximum=72) 一致（测试做跨模块一致性断言），
# 但职责不同：schema 是「用户可选范围」，这里是「内核拒绝 DoS 的硬上界」。
PASS_WINDOW_MIN_HOURS = 1.0
PASS_WINDOW_MAX_HOURS = 72.0
# 60s 粗扫的擦边补点判据（度）：两端都没越门限、但至少一端离门限不超过
# 该值且在地平以上时，探测区间中点。LEO 仰角连续变化，60s 网格能整段吞掉
# 时长 <60s 的擦边过境（实测峰值 10.4°/门限 10°/时长 50s 返回空）。只探测
# 地平附近的少数区间，不把整个粗扫成本翻倍。这是工程近似（设计项），非物理定律。
PASS_EDGE_PROBE_MARGIN_DEG = 15.0
# 兼容旧调用点的私有别名（值只在这里定义一次，别名不构成第二事实源）
_COARSE_STEP_S = PASS_COARSE_STEP_S
_FINE_STEP_S = PASS_FINE_STEP_S
_BISECT_TOL_S = 0.5       # 二分收敛容差（秒），LEO 仰角变化 ~0.5°/s ⇒ ~0.25° 精度


def clamp_pass_window_hours(hours: Any) -> float:
    """把过境搜索窗口夹到内核安全区间 ``[1, 72]`` 小时。

    坏类型 / NaN / inf 一律回默认 24h，绝不把一次畸形输入变成无限循环。
    返回的是**实际生效值**，调用方可据此写「仅搜索前 Nh」而不是假装按请求值算。
    """
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return PASS_WINDOW_HOURS
    if not math.isfinite(value):
        return PASS_WINDOW_HOURS
    return max(PASS_WINDOW_MIN_HOURS,
               min(PASS_WINDOW_MAX_HOURS, value))


# 仰角门：**两个语义，两个常量**，不许用一个名字两个数
# ---------------------------------------------------------------------------
# VISIBLE_MIN_ALT_DEG：当前仰角门——"现在还能不能收/跟不跟"。低仰角仍有可通联
#   窗口，5° 是业余操作的通行下限；达到它不等于一次值得排班的过境。
# PASS_MIN_ALT_DEG：过境预测门——"这次过境值不值得列出来"。峰值仰角太低，
#   大气损耗和多普勒都大、可用时间短，10° 起步。
# 历史问题：SatelliteTracker.next_pass() 把 5° 传进了默认 10° 的
# predict_passes()，导致"下一次过境"这条链与直接预测拿到两个门。
VISIBLE_MIN_ALT_DEG = 5.0
PASS_MIN_ALT_DEG = 10.0


def make_timescale():
    """返回 skyfield 时间系统（缓存，避免重复加载 ephemeris/UT1 表）。"""
    if not hasattr(make_timescale, "_ts"):
        _require_skyfield()
        make_timescale._ts = _sky_load.timescale()
    return make_timescale._ts


@dataclass
class GroundStation:
    """地面站（观测者）位置。

    地面站纬度/经度/海拔。
    """
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0


@dataclass
class SatellitePass:
    """一次卫星过境事件（AOS→中天→LOS）。

    时间字段均为 skyfield.api.Time（UTC），与任务约束一致。
    """
    rise_time: Time          # 仰角升至 min_alt 时刻 (AOS)
    set_time: Time           # 仰角回落至 min_alt 时刻 (LOS)
    max_alt_time: Time       # 过境期间最大仰角(中天)时刻
    max_alt: float           # 最大仰角（度）
    rise_az: float           # 升起方位角（度，北=0，东=90）
    set_az: float            # 落下方位角（度）
    duration: float          # 过境总时长（秒）
    sat_name: str = ""       # 卫星名（TLE 标题行，若有）
    sunlit: Optional[bool] = None   # 过境中点卫星是否被太阳照亮（None=未计算）

    def summary(self) -> str:
        dt_r = self.rise_time.utc_datetime()
        dt_s = self.set_time.utc_datetime()
        dt_m = self.max_alt_time.utc_datetime()
        return (f"{self.sat_name or 'sat'}: rise {dt_r:%H:%M:%S}Z az={self.rise_az:.0f}° | "
                f"max {dt_m:%H:%M:%S}Z alt={self.max_alt:.1f}° | "
                f"set {dt_s:%H:%M:%S}Z az={self.set_az:.0f}° | "
                f"dur={self.duration/60:.1f}min sunlit={self.sunlit}")


def _parse_tle(tle_lines: Sequence[str]) -> Optional[Tuple[str, str, str]]:
    """从 TLE 文本中提取 (name, line1, line2)。

    接受：
      - 3 行（标题行 + 1 + 2）：celestrak 标准格式
      - 2 行（1 + 2）：无标题，name 从 line2 编号推断
      - 列表里混有空行/注释；自动跳过。
    解析失败（无有效 1/2 行）返回 None —— 调用方据此返回空列表，不造假。

    按 Spacetrack Report #3 的 TLE 列布局解析。
    """
    lines = [l.strip() for l in tle_lines if l and l.strip()]
    if len(lines) < 2:
        return None
    i1 = next((i for i, l in enumerate(lines) if l.startswith("1 ")), None)
    i2 = next((i for i, l in enumerate(lines) if l.startswith("2 ")), None)
    if i1 is None or i2 is None or i2 <= i1:
        return None
    line1, line2 = lines[i1], lines[i2]
    if len(line1) < 69 or len(line2) < 69:
        return None
    # 标题行 = line1 之前的非数字行
    name = ""
    if i1 > 0:
        name = lines[i1 - 1].strip()
    if not name:
        # 用 NORAD 编号兜底
        name = "NORAD " + line2[2:7].strip()
    return name, line1, line2


def _alt_az(diff, t: Time) -> Optional[Tuple[float, float]]:
    """在时刻 t 计算 (仰角°, 方位角°)；传播失败返回 None。

    slantRange = satECI - obsECI，再旋转到站心 Up/East/North，Up 分量给出仰角。
    skyfield 的 ``(sat - topo).at(t).altaz()`` 是同一变换的官方实现。
    """
    try:
        topoc = diff.at(t)
        alt, az, _dist = topoc.altaz()
        return float(alt.degrees), float(az.degrees)
    except Exception:
        return None


def _bisect_cross(el_fn, lo: float, hi: float, target: float,
                  tol: float = _BISECT_TOL_S) -> float:
    """在 [lo, hi]（秒，相对 t0）之间二分求仰角=target 的时刻。

    调用方保证两端仰角在 target 异侧；逐时刻采样仰角曲线后二分过零。
    """
    flo = el_fn(lo)
    fhi = el_fn(hi)
    if flo is None or fhi is None:
        return 0.5 * (lo + hi)
    if (flo - target) * (fhi - target) > 0:
        return 0.5 * (lo + hi)
    for _ in range(40):
        if hi - lo < tol:
            break
        mid = 0.5 * (lo + hi)
        fm = el_fn(mid)
        if fm is None:
            return mid
        if (fm - target) * (flo - target) <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def predict_passes(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    start_time: Optional[Time] = None,
    hours: float = PASS_WINDOW_HOURS,
    min_alt: float = PASS_MIN_ALT_DEG,
    require_sunlit: bool = False,
    anomalies: Optional[List[Dict[str, Any]]] = None,
    start_unix: Optional[float] = None,
) -> List[SatellitePass]:
    """预测未来 ``hours`` 小时内卫星从 ``start_time`` 起的过境事件。

    参数
    ----
    tle_lines:
        TLE 文本（标题行可选）。空/无效 → 返回空列表，不造假。
    ground_station:
        :class:`GroundStation` 观测者位置。
    start_time:
        skyfield Time（UTC）。None 取 TimeEngine（模拟时刻优先，未穿越即墙钟）。
    start_unix:
        UTC 秒，``start_time`` 为 None 时优先于 TimeEngine（测试/回放钉死时钟入口）。
    hours:
        向前预测时长（小时）。
    min_alt:
        过境最低仰角阈值（度），默认 10°。
    require_sunlit:
        若 True，仅保留过境中点卫星被太阳照亮的事件（Vallado 锥几何地影判断）。
        默认 False。

    返回
    ----
    List[SatellitePass]，按 rise_time 升序。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed

    ts = make_timescale()
    if start_time is None:
        if start_unix is not None:
            # 显式 Unix 时刻优先（测试/回放钉死时钟的唯一入口）
            start_time = ts.utc(datetime.fromtimestamp(float(start_unix),
                                                       tz=timezone.utc))
        else:
            # 缺省与全链路一致：取 TimeEngine（模拟时刻优先，未穿越即系统时钟）
            try:
                from .new_spacetime import get_time_engine
            except ImportError:  # 扁平导入（脚本/测试）
                from new_spacetime import get_time_engine
            start_time = ts.utc(datetime.fromtimestamp(
                get_time_engine().now_unix(), tz=timezone.utc))

    # twoline2rv + SGP4 传播器
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []

    # 站心 Topos（skyfield 等价于站心 ECI 变换）
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo

    def el_at_s(sec: float) -> Optional[float]:
        t = start_time + sec / 86400.0   # skyfield Time 支持 +天数
        r = _alt_az(diff, t)
        return r[0] if r else None

    def el_az_at_s(sec: float) -> Optional[Tuple[float, float]]:
        t = start_time + sec / 86400.0
        return _alt_az(diff, t)

    # 内核硬上界：MCP/agent/CLI 不经过桌面 settings 的 1–72 钳位，
    # 任意 hours 会直接变成数十万次 SGP4 传播。
    hours = clamp_pass_window_hours(hours)
    total_s = hours * 3600.0
    n_steps = int(total_s / PASS_COARSE_STEP_S) + 1

    # a) 粗扫：60s 步长记录 (sec, el)。
    coarse: List[Tuple[float, Optional[float]]] = []
    for k in range(n_steps + 1):
        sec = min(k * PASS_COARSE_STEP_S, total_s)
        coarse.append((sec, el_at_s(sec)))
    if all(e is None for _, e in coarse):
        return []

    # a2) 擦边补点：60s 网格可能整段吞掉「峰值刚好越门限、时长 <60s」的
    # 过境（两端都低于 min_alt，峰在中间）。只对地平附近、离门限不远的区间
    # 探测中点；命中就把中点插进 coarse，后面的分段/二分/细扫原样复用。
    probe_floor = max(0.0, min_alt - PASS_EDGE_PROBE_MARGIN_DEG)
    extra: List[Tuple[float, float]] = []
    for k in range(len(coarse) - 1):
        sec0, el0 = coarse[k]
        sec1, el1 = coarse[k + 1]
        if el0 is None or el1 is None:
            continue
        if el0 >= min_alt or el1 >= min_alt:      # 常规分段已覆盖
            continue
        if max(el0, el1) < probe_floor:           # 离得太远/还在地平下
            continue
        mid_s = 0.5 * (sec0 + sec1)
        el_mid = el_at_s(mid_s)
        if el_mid is not None and el_mid >= min_alt:
            extra.append((mid_s, el_mid))
    if extra:
        coarse.extend(extra)
        coarse.sort(key=lambda item: item[0])

    passes: List[SatellitePass] = []
    n = len(coarse)
    i = 0
    while i < n:
        sec_i, el_i = coarse[i]
        if el_i is None or el_i < min_alt:
            i += 1
            continue
        # 找到一段连续 >= min_alt 的粗采样 coarse[i:j]
        j = i
        while j < n and coarse[j][1] is not None and coarse[j][1] >= min_alt:
            j += 1

        # b) rise/set 二分精确定位（过零检测）
        if i > 0 and coarse[i - 1][1] is not None:
            t_rise_s = _bisect_cross(el_at_s, coarse[i - 1][0], sec_i, min_alt)
        else:
            t_rise_s = sec_i
        if j < n and coarse[j][1] is not None:
            t_set_s = _bisect_cross(el_at_s, coarse[j - 1][0], coarse[j][0], min_alt)
        else:
            t_set_s = coarse[j - 1][0]

        # P11 守卫：负时长（时钟回跳/输入错乱）绝不当成正常过境输出。
        # 判定口径统一走 time_basis.validate_pass_window（单一事实源）。
        window = validate_pass_window(t_rise_s, t_set_s)
        if not window["ok"]:
            if anomalies is not None:
                anomalies.append({
                    "satellite": name,
                    "reason": "non_positive_duration",
                    "detail": (f"rise={t_rise_s:.1f}s set={t_set_s:.1f}s；"
                               f"{window['warning']}，已丢弃该过境"),
                })
            i = max(j, i + 1)
            continue

        # c) pass 内 2s 细采样，找最大仰角时刻
        best_sec, best_el = t_rise_s, -999.0
        s = t_rise_s
        while s <= t_set_s + 1e-6:
            r = el_az_at_s(s)
            if r is not None and r[0] > best_el:
                best_el, best_sec = r[0], s
            s += PASS_FINE_STEP_S

        rise_r = el_az_at_s(t_rise_s) or (None, None)
        set_r = el_az_at_s(t_set_s) or (None, None)
        rise_az = rise_r[1] if rise_r[1] is not None else float("nan")
        set_az = set_r[1] if set_r[1] is not None else float("nan")

        mid_s = 0.5 * (t_rise_s + t_set_s)
        sunlit = None
        if require_sunlit:
            sunlit = _is_sunlit_geometric(sat, start_time + mid_s / 86400.0)

        p = SatellitePass(
            rise_time=start_time + t_rise_s / 86400.0,
            set_time=start_time + t_set_s / 86400.0,
            max_alt_time=start_time + best_sec / 86400.0,
            max_alt=float(best_el),
            rise_az=float(rise_az),
            set_az=float(set_az),
            duration=float(t_set_s - t_rise_s),
            sat_name=name,
            sunlit=sunlit,
        )
        if (not require_sunlit) or sunlit:
            passes.append(p)
        i = j

    passes.sort(key=lambda p: p.rise_time.utc)
    return passes


def sample_trail(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    t_center: Time,
    before_s: float = 600.0,
    after_s: float = 600.0,
    step_s: float = 20.0,
) -> List[Tuple[Time, float, float]]:
    """采样过去/未来轨迹点（trail），返回 [(Time, az_deg, alt_deg), ...]。

    以当前时刻为中心，从 ``t_center - before_s`` 起按固定 ``step_s`` 逐时刻
    采样。无效点跳过。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo
    out: List[Tuple[Time, float, float]] = []
    n = int((before_s + after_s) / step_s)
    for k in range(n + 1):
        sec = -before_s + k * step_s
        t = t_center + sec / 86400.0
        r = _alt_az(diff, t)
        if r is not None:
            out.append((t, r[1], r[0]))
    return out


# ---------------------------------------------------------------------------
# 几何地影判断（可选，Vallado 锥几何）
# ---------------------------------------------------------------------------
def _sun_eci_unit(t: Time) -> Tuple[float, float, float]:
    """低精度太阳方向单位矢量（TEME/ECI，无量纲）。

    统一委托 :func:`mbdsdr_ai.sat_illumination.sun_direction_eci`，避免
    地影判定与太阳矢量出现第二份数学源。误差与口径见该模块 docstring。
    """
    from .sat_illumination import sun_direction_eci
    return sun_direction_eci(float(t.utc_datetime().timestamp()))


def _is_sunlit_geometric(sat: EarthSatellite, t: Time) -> bool:
    """几何地影判断：卫星是否被太阳照亮（不在地球本影内）。

    统一委托 :func:`mbdsdr_ai.sat_illumination.evaluate_illumination`
    （圆锥影，与 gpredict Sat_Eclipsed 同构）。半影按「受晒」处理，保持
    本函数原有的二值语义（三态结果请直接调用 sat_illumination）。
    """
    try:
        pos_km = tuple(float(x) for x in sat.at(t).position.km)
    except Exception:
        return True
    from .sat_illumination import STATUS_UMBRA, evaluate_illumination
    try:
        state = evaluate_illumination(pos_km, _sun_eci_unit(t))
    except ValueError:
        return True
    return state.status != STATUS_UMBRA


# ===========================================================================
# 多普勒频移预测（SDR 接收用）
# ===========================================================================
#
# 多普勒参考（公开轨道力学）：
#   slantRange       = satECIPos - observerECIPos      (位置差向量, km)
#   slantRangeVel    = satECIVel - observerECIVel      (速度差向量, km/s)
#   range            = |slantRange|                    (斜距, km)
#   rangeRate        = slantRange.dot(slantRangeVel)/|slantRange|
#     ^^^ 径向速度（km/s），远离为正。这就是多普勒计算的核心。
#   多普勒频移 f_d = -f_0 * v_r / c；本模块统一用 Hz，c 用 m/s。
#
# 物理符号约定（rangeRate 远离为正）：
#   range_rate > 0 → 卫星远离地面站 → 接收频率降低 → f_d < 0
#   range_rate < 0 → 卫星接近地面站 → 接收频率升高 → f_d > 0
#   AOS（升起）时卫星朝向观测者飞来 ⇒ v_r<0 ⇒ f_d>0（正偏移）
#   中天（最大仰角）时运动方向与视线垂直 ⇒ v_r≈0 ⇒ f_d≈0
#   LOS（落下）时卫星远离观测者 ⇒ v_r>0 ⇒ f_d<0（负偏移）
# ===========================================================================


@dataclass
class DopplerPoint:
    """过境过程中某一时刻的多普勒状态采样点。

    含 Range / Range rate / Altitude，外加 az/el/多普勒频率与变化率。
    """
    time: Time                # 采样时刻（UTC, skyfield Time）
    azimuth: float            # 方位角（度，北=0，东=90）
    elevation: float          # 仰角（度）
    range_km: float           # 斜距（km）
    range_rate_km_s: float    # 径向速度（km/s），远离为正
    doppler_hz: float         # 多普勒频移（Hz），f_d = -v_r/c * f_0
    doppler_rate_hz_s: float  # 多普勒变化率（Hz/s），SDR AFC 跟踪用


def _range_and_rate(diff, t: Time) -> Optional[Tuple[float, float, float, float, float]]:
    """在时刻 t 计算 (az_deg, el_deg, range_km, range_rate_km_s, unused)。

      slantRange       = satECIPos - observerECIPos
      slantRangeVel   = satECIVel - observerECIVel
      range           = |slantRange|
      rangeRate       = dot(slantRange, slantRangeVel) / |slantRange|
    skyfield 的 ``(sat - topo).at(t)`` 直接给出相对位置/速度向量
    （ICRS/地球中心惯性系，与 TEME 在 LEO 多普勒量级下差异可忽略）。
    """
    try:
        geom = diff.at(t)
        alt, az, dist = geom.altaz()
        # position 单位 AU → km；velocity 单位 AU/day → km/s
        pos_km = [float(x) * _AU_KM for x in geom.position.au]
        vel_kms = [float(x) * _AU_KM / 86400.0 for x in geom.velocity.au_per_d]
        rng = math.sqrt(sum(x * x for x in pos_km))
        if rng <= 0:
            return None
        # rangeRate = dot(slantRange, slantRangeVel)/|slantRange|
        rr = sum(p * v for p, v in zip(pos_km, vel_kms)) / rng
        return float(az.degrees), float(alt.degrees), float(rng), float(rr), 0.0
    except Exception:
        return None


def compute_doppler_curve(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    pass_: SatellitePass,
    freq_hz: float,
    num_points: int = 60,
) -> List[DopplerPoint]:
    """对一次过境事件采样完整多普勒曲线。

    参数
    ----------
    tle_lines:
        TLE 文本（标题行可选），与 ``predict_passes`` 同格式。
    ground_station:
        地面站位置。
    pass_:
        :class:`SatellitePass` 过境事件（由 predict_passes 产生）。
    freq_hz:
        下行/上行标称频率（Hz）。例：ISS 中继 437 MHz = 437000000。
    num_points:
        在 [rise_time, set_time] 区间内均匀采样点数，默认 60。

    返回
    ----
    List[DopplerPoint]，按时间升序。无 TLE/传播失败返回空列表。

    算法
    ----
    对每个采样时刻调用 :func:`_range_and_rate`，然后：
        f_d = -range_rate_mps / c * freq_hz
    其中 c = 299792458 m/s。
    多普勒变化率 doppler_rate_hz_s 用相邻点中心差分：
        df_d/dt ≈ (f_d[i+1] - f_d[i-1]) / (t[i+1] - t[i-1])
    端点用前向/后向差分。
    """
    parsed = _parse_tle(tle_lines)
    if parsed is None:
        return []
    name, line1, line2 = parsed
    ts = make_timescale()
    try:
        sat = EarthSatellite(line1, line2, name, ts)
    except Exception:
        return []
    topo = Topos(latitude_degrees=ground_station.lat_deg,
                 longitude_degrees=ground_station.lon_deg,
                 elevation_m=ground_station.alt_m)
    diff = sat - topo

    t0 = pass_.rise_time
    t1 = pass_.set_time
    dt_days = t1.tt - t0.tt
    if dt_days <= 0 or num_points < 2:
        return []

    raw: List[Tuple[Time, float, float, float, float]] = []
    for k in range(num_points):
        frac = k / (num_points - 1)
        t = ts.tt_jd(t0.tt + frac * dt_days)
        r = _range_and_rate(diff, t)
        if r is None:
            continue
        az, el, rng, rr, _ = r
        # f_d = -freq * (rangeRate_mps / c)；range_rate_km_s → m/s 乘 1000
        dop = -rr * 1000.0 / C_LIGHT_MPS * freq_hz
        raw.append((t, az, el, rng, rr))

    if len(raw) < 2:
        return []

    # 计算多普勒变化率（Hz/s）：中心差分
    dop_vals = [-rr * 1000.0 / C_LIGHT_MPS * freq_hz for (_, _, _, _, rr) in raw]
    t_days = [t.tt for (t, _, _, _, _) in raw]
    dop_rate = [0.0] * len(raw)
    for i in range(len(raw)):
        if i == 0:
            dt_s = (t_days[1] - t_days[0]) * 86400.0
            dop_rate[i] = (dop_vals[1] - dop_vals[0]) / dt_s if dt_s > 0 else 0.0
        elif i == len(raw) - 1:
            dt_s = (t_days[-1] - t_days[-2]) * 86400.0
            dop_rate[i] = (dop_vals[-1] - dop_vals[-2]) / dt_s if dt_s > 0 else 0.0
        else:
            dt_s = (t_days[i + 1] - t_days[i - 1]) * 86400.0
            dop_rate[i] = (dop_vals[i + 1] - dop_vals[i - 1]) / dt_s if dt_s > 0 else 0.0

    out: List[DopplerPoint] = []
    for i, (t, az, el, rng, rr) in enumerate(raw):
        out.append(DopplerPoint(
            time=t,
            azimuth=az,
            elevation=el,
            range_km=rng,
            range_rate_km_s=rr,
            doppler_hz=dop_vals[i],
            doppler_rate_hz_s=dop_rate[i],
        ))
    return out


def predict_upcoming_passes(
    tle_list: Sequence[Sequence[str]],
    ground_station: GroundStation,
    hours: float = PASS_WINDOW_HOURS,
    min_alt: float = PASS_MIN_ALT_DEG,
    max_sats: Optional[int] = None,
    freq_hz: Optional[float] = None,
) -> List[Dict]:
    """多颗卫星的未来过境事件列表，按 AOS 时间排序。

    多颗卫星未来过境事件列表：沿时间轴固定步长采样仰角曲线，叠加
    粗扫+二分过零（见本模块 predict_passes 注释），构成多星事件列表。

    参数
    ----------
    tle_list:
        多颗卫星的 TLE 列表，每项是一个 TLE 文本序列（标题行可选）。
        例：[["ISS (ZARYA)", "1 ...", "2 ..."], ["NOAA 15", "1 ...", "2 ..."]]
        空/无效 TLE 自动跳过（返回空结果，不造假）。
    ground_station:
        地面站位置。
    hours:
        向前预测时长（小时），默认 24。
    min_alt:
        过境最低仰角阈值（度），默认 10°。
    max_sats:
        最多返回多少颗卫星的过境（取仰角峰值最高的前 N 颗）；None 不限制。
    freq_hz:
        若提供，对每条过境计算峰值多普勒（Hz）；否则不计算。

    返回
    ----
    List[Dict]，按 rise_time 升序，每项：
      - pass: SatellitePass 对象
      - tle_lines: 原始 TLE 文本（便于后续 compute_doppler_curve 使用）
      - peak_doppler_hz: 最大 |f_d|（Hz，若 freq_hz 提供）
      - doppler_aos_hz: AOS 时刻多普勒（正=接近，若 freq_hz 提供）
      - doppler_los_hz: LOS 时刻多普勒（负=远离，若 freq_hz 提供）
    """
    # 按星分组收集 (max_alt, rise_time_tt, SatellitePass, tle_lines)；
    # 组键 = (line1, line2)，同一 TLE 重复列出仍算一颗星。
    groups: Dict[Tuple[str, str],
                 List[Tuple[float, float, SatellitePass, Sequence[str]]]] = {}
    for tle_lines in tle_list:
        parsed = _parse_tle(tle_lines)
        if parsed is None:
            continue
        try:
            passes = predict_passes(tle_lines, ground_station, hours=hours,
                                    min_alt=min_alt)
        except Exception:
            continue
        entries = [(p.max_alt, p.rise_time.tt, p, tle_lines) for p in passes]
        if entries:
            groups.setdefault((parsed[1], parsed[2]), []).extend(entries)

    collected = [item for bucket in groups.values() for item in bucket]
    if not collected:
        return []

    # max_sats 数的是「星」：按各星峰值仰角取前 N 颗，再展开其全部过境；
    # 一颗多过境卫星只占一份配额。
    if max_sats is not None and max_sats > 0 and max_sats < len(groups):
        ranked = sorted(groups.values(),
                        key=lambda b: max(x[0] for x in b), reverse=True)
        collected = [item for bucket in ranked[:max_sats] for item in bucket]

    # 按 rise_time 升序排序
    collected.sort(key=lambda x: x[1])

    result: List[Dict] = []
    for _, _, p, tle_lines in collected:
        entry: Dict = {"pass": p, "tle_lines": list(tle_lines)}
        if freq_hz is not None:
            try:
                curve = compute_doppler_curve(
                    tle_lines, ground_station, p, freq_hz, num_points=30,
                )
            except Exception:
                curve = []
            if curve:
                entry["peak_doppler_hz"] = max(abs(pt.doppler_hz) for pt in curve)
                entry["doppler_aos_hz"] = curve[0].doppler_hz
                entry["doppler_los_hz"] = curve[-1].doppler_hz
            else:
                entry["peak_doppler_hz"] = None
                entry["doppler_aos_hz"] = None
                entry["doppler_los_hz"] = None
        result.append(entry)
    return result
