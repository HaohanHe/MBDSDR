# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 真实轨道计算模块 (SGP4)
==========================================
用 sgp4 + 真实 celestrak TLE，替换手写占位 TLE 与经验多普勒近似。

能力：
- 按 CATNR 在线获取真实 TLE（celestrak），本地缓存避免重复联网；
- sgp4 传播得 TEME 位置/速度向量；
- TEME -> ECEF（GMST 旋转）-> 站心 ENU -> 仰角/方位/距离；
- 多普勒用真视线速度（卫星速度向量投影到站星方向），非经验公式。

依据：SGP4/SDP4 标准（Spacetrack Report #3 及 Vallado 等 2006 修订）。
椭球参数统一使用 WGS-72（与 Python sgp4 库默认 legacy 模式一致），避免轨道
WGS-72 / 站心 WGS-84 混用引入的米级系统差。

gpredict 仅作为技术参考与致谢，本模块未包含其源代码。
"""
from __future__ import annotations

import math
import os
import time
import urllib.request
import ssl
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple

from .sat_illumination import evaluate_illumination, sun_direction_eci

try:
    from sgp4.api import Satrec, jday
    _HAS_SGP4 = True
except ImportError:  # sgp4 属 satellite 可选依赖组；主链（sdr_tools→orbit）须可无它导入
    Satrec = None  # type: ignore[assignment,misc]
    jday = None  # type: ignore[assignment]
    _HAS_SGP4 = False


def _require_sgp4() -> None:
    """sgp4 缺失时给出可操作的诚实报错（而非 AttributeError: NoneType）。"""
    if not _HAS_SGP4:
        raise ImportError(
            "sgp4 未安装：TLE 传播需要它（pip install 'mbdsdr_ai[satellite]'"
            " 或 pip install sgp4）")

# WGS-72 椭球参数（SGP4/SDP4 标准，Spacetrack Report #3）。
# SGP4 内核全程 WGS-72，站心椭球必须一致，否则 ECEF 站位置与卫星
# TEME→ECEF 旋转后的坐标存在 ~米级系统差。
WGS72_A = 6378.135          # km, WGS-72 赤道半径
WGS72_F = 1.0 / 298.26      # WGS-72 扁率
WGS72_B = WGS72_A * (1.0 - WGS72_F)
WGS72_E2 = WGS72_F * (2.0 - WGS72_F)
OMEGA_E = 7.292115e-5       # rad/s, 地球自转角速度 (WGS-72)
C_LIGHT = 299792.458          # km/s


class TLEUnavailableError(RuntimeError):
    """TLE 不可得（离线无缓存 / 下载失败）。

    调用方应据诚实空态降级（告知用户缺 TLE、建议联网或导入），
    不得据此编造轨道数据。
    """


# 内置卫星 -> NORAD CATNR（真实编号，TLE 在线拉取）
# 编号必须与真实对象一致（2026-11 校正）：
#   METEOR-M 2 = 40069（44016 实为 ATLAS 5 CENTAUR DEB 残骸）
#   FENGYUN 3D = 43010（54234 实为 NOAA 21/JPSS-2）
# 三方校验见 tle_integrity.scan_catalog；改动前先跑全目录扫描。
BUILTIN_SATS: Dict[str, int] = {
    "NOAA 15": 25338,
    "NOAA 18": 28654,
    "NOAA 19": 33591,
    "ISS (ZARYA)": 25544,
    "METEOR M2": 40069,
    "FENGYUN 3D": 43010,
}

_TLE_CACHE: Dict[str, Tuple[float, Tuple[str, str]]] = {}
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mbdsdr", "tle_cache")
_CTX = ssl.create_default_context()

# 网络门（杜绝无限阻塞 / 假造 TLE）：
#   - MBDSDR_OFFLINE=1 时禁网，仅认磁盘缓存，无缓存即诚实抛错。
#     测试/CI/离线环境应设此变量（与 MBDSDR_SETTINGS_DIR 同属环境隔离一族），
#     避免 dry-call/全量套件因联网挂死（2026-10-20 实测 CI 全量 pytest 永久卡死）。
#   - 在线时用 _bounded_urlopen：urllib 的 timeout 不覆盖 getaddrinfo(DNS)，
#     无网/黑洞网络下 DNS 解析可无限阻塞；线程 join 硬截止覆盖含 DNS 的全链路。
_OFFLINE_ENV = "MBDSDR_OFFLINE"
_FETCH_TIMEOUT_S = 15.0          # socket 层（连接/读）软超时
_FETCH_HARD_DEADLINE_S = 20.0    # 含 DNS 的墙钟硬截止


def _network_disabled() -> bool:
    """是否禁网（MBDSDR_OFFLINE=1/true/yes/on）。"""
    return os.environ.get(_OFFLINE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _bounded_urlopen(req, timeout_s: float, hard_deadline_s: float):
    """工作线程内 urlopen，join(hard_deadline) 硬截止（覆盖 DNS 阶段）。

    urllib 的 timeout 不约束 getaddrinfo：无网/被黑洞时 DNS 解析无限阻塞，
    是 CI 全量 pytest 永久挂死的真凶。线程 daemon 执行 + join 截止，超时抛
    TimeoutError 交给上层诚实降级（绝不假造 TLE、绝不静默吞掉）。
    """
    import threading

    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["r"] = urllib.request.urlopen(req, timeout=timeout_s, context=_CTX)
        except Exception as e:  # noqa: BLE001 - 原样上抛给调用方
            box["e"] = e

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(hard_deadline_s)
    if worker.is_alive():
        raise TimeoutError(
            f"TLE 下载超时（>{hard_deadline_s:.0f}s，含 DNS 解析）：{req.full_url}"
        )
    if "e" in box:
        raise box["e"]
    return box["r"]


def _gmst_days(jd_ut1: float) -> float:
    """Greenwich 平恒星时（弧度），基于儒略日。"""
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    gmst_rad = math.radians((gmst_sec % 86400.0) / 240.0)
    return gmst_rad % (2.0 * math.pi)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> Tuple[float, float, float]:
    """WGS-72 大地坐标 -> ECEF (km)。椭球与 SGP4 输出一致。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = WGS72_A / math.sqrt(1.0 - WGS72_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon)
    y = (n + alt_km) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS72_E2) + alt_km) * sin_lat
    return x, y, z


def tle_epoch_iso(line1: str) -> str:
    """TLE line1 历元（列 19-32，YYDDD.DDDDDDDD）→ ISO UTC 字符串。

    例: "26250.50000000" -> "2026-09-06 12:00:00 UTC"。
    年份按 TLE 惯例折叠（YY<57 -> 20YY，否则 19YY）。
    解析失败返回 ""（调用方如实缺省，不编造历元）。
    """
    try:
        # 统一走 time_basis：两位年份折叠 + 年积日越界拒绝（不静默顺延）
        from .time_basis import tle_epoch_utc
        dt = tle_epoch_utc(line1)
        if dt is None:
            return ""
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, IndexError, OverflowError):
        return ""


def _tle_opener(url: str, timeout_s: float, deadline_s: float) -> bytes:
    """把 urllib 请求包成 tle_fetch 需要的 opener（返回原始字节）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})
    return _bounded_urlopen(req, timeout_s, deadline_s).read()


_TLE_FETCHER: Optional[Any] = None


def _get_fetcher():
    """单例 TLE 获取器；缓存目录/内存字典用 provider 动态读取。

    provider 形式让测试仍可 monkeypatch ``orbit._CACHE_DIR`` /
    ``orbit._TLE_CACHE`` 生效（与重构前行为一致）。
    """
    global _TLE_FETCHER
    if _TLE_FETCHER is None:
        from .tle_fetch import FetchPolicy, TleFetcher
        _TLE_FETCHER = TleFetcher(
            opener=_tle_opener,
            cache_dir_provider=lambda: _CACHE_DIR,
            memory_provider=lambda: _TLE_CACHE,
            offline_provider=_network_disabled,
            clock=time.time,
            error_cls=TLEUnavailableError,
            source="celestrak",
            policy=FetchPolicy(request_timeout_s=_FETCH_TIMEOUT_S,
                               hard_deadline_s=_FETCH_HARD_DEADLINE_S),
        )
    return _TLE_FETCHER


def tle_source_status(now: Optional[float] = None) -> Dict[str, Any]:
    """TLE 源健康度（最近成功/失败、退避剩余、策略）——供诊断与 UI。"""
    return _get_fetcher().health_report(now=now)


def refresh_tle_round(entries, reason: str = "startup",
                      now: Optional[float] = None) -> Dict[str, Any]:
    """跑一轮刷新调度：按 24h 门槛去重、退避期间不重试（P12）。"""
    return _get_fetcher().refresh_round(entries, reason=reason, now=now)


def fetch_tle(catnr: int, max_age_hours: float = 24.0) -> Tuple[str, str]:
    """按 CATNR 从 celestrak 拉真实 TLE，内存+磁盘缓存。失败抛异常。

    离线（MBDSDR_OFFLINE）或下载失败时：仅认缓存，无可用缓存即抛
    TLEUnavailableError——绝不假造 TLE、绝不无限阻塞。刷新门槛、失败
    退避与限速由 :mod:`mbdsdr_ai.tle_fetch` 统一执行。
    """
    rec = _get_fetcher().fetch_record(catnr, max_age_hours=max_age_hours)
    return rec["line1"], rec["line2"]


def fetch_tle_record(catnr: int, max_age_hours: float = 24.0,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """按 CATNR 取 TLE 并附带「是否刷新成功」的诚实记录（不静默）。

    与 :func:`fetch_tle` 的区别：取新数据失败时**保留旧缓存值**并在记录里
    标明 ``refresh_status="not_updated"``（附 ``refresh_error``），而不是
    把旧值藏起来或抛错。只有「完全无数据」才抛 TLEUnavailableError。

    返回::

        {"catnr", "name"(line0 对象名), "line1", "line2", "source",
         "fetched_at", "refresh_status", "refresh_error", "cache_path"}

    ``refresh_status``: ``memory`` / ``cache``（缓存未过期）/ ``network``
    （本次联网成功）/ ``not_updated``（联网失败或离线，沿用旧值）。
    """
    return _get_fetcher().fetch_record(catnr, max_age_hours=max_age_hours,
                                       now=now)


def _state_from_satrec(
    sat: Satrec,
    satellite_name: str,
    jd_utc: float,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    epoch: str = "",
) -> Optional[Dict[str, Any]]:
    """核心坐标变换：TEME -> ECEF(绕 z 转 -GMST) -> ENU -> 仰角/方位/距离/视线速度。

    路线 (a)：把卫星 TEME 转到 ECEF（与把地面站转到 TEME 互为逆，数学等价）。
    GMST 折叠式见 _gmst_days。
    """
    _require_sgp4()
    jd = int(jd_utc)
    fr = jd_utc - jd
    e, r_teme, v_teme = sat.sgp4(jd, fr)
    if e != 0:
        return None
    # TEME(km) -> ECEF：绕 z 轴转 -GMST
    gmst = _gmst_days(jd_utc)
    cg, sg = math.cos(-gmst), math.sin(-gmst)
    rx = cg * r_teme[0] - sg * r_teme[1]
    ry = sg * r_teme[0] + cg * r_teme[1]
    r_ecef = (rx, ry, r_teme[2])
    # 速度：旋转 − Coriolis 项（输运定理 v_ECEF = R·v_TEME − Ω×r）
    # 输运定理 v_ECEF = R·v_TEME − Ω×r：站速项 Ω×r_ECEF = +(ωy,-ωx,0)
    vx = cg * v_teme[0] - sg * v_teme[1]
    vy = sg * v_teme[0] + cg * v_teme[1]
    vx += OMEGA_E * r_ecef[1]    # - (Ω×r)_x = +ω·y
    vy += -OMEGA_E * r_ecef[0]   # - (Ω×r)_y = -ω·x
    v_ecef = (vx, vy, v_teme[2])
    # 站 ECEF（WGS-72 椭球，与 SGP4 一致）
    s_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)
    # 站心矢量
    rx_s = r_ecef[0] - s_ecef[0]
    ry_s = r_ecef[1] - s_ecef[1]
    rz_s = r_ecef[2] - s_ecef[2]
    dist = math.sqrt(rx_s * rx_s + ry_s * ry_s + rz_s * rz_s)
    if dist <= 0:
        return None
    # 站坐标 ENU 旋转
    lat = math.radians(observer_lat)
    lon = math.radians(observer_lon)
    sin_l, cos_l = math.sin(lat), math.cos(lat)
    sin_b, cos_b = math.sin(lon), math.cos(lon)
    e_east = -sin_b * rx_s + cos_b * ry_s
    e_north = -sin_l * cos_b * rx_s - sin_l * sin_b * ry_s + cos_l * rz_s
    e_up = cos_l * cos_b * rx_s + cos_l * sin_b * ry_s + sin_l * rz_s
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, e_up / dist))))
    azimuth = (math.degrees(math.atan2(e_east, e_north)) + 360.0) % 360.0
    # 视线速度：卫星速度投影到站星方向单位矢量（远离为正）
    los = (rx_s / dist, ry_s / dist, rz_s / dist)
    range_rate = (v_ecef[0] * los[0] + v_ecef[1] * los[1] + v_ecef[2] * los[2])
    # 受晒/地影：与天空视图、sat_passes 共用 sat_illumination 的唯一口径
    # （圆锥影，边界 ±2 s）。这里用 sgp4 已算出的 TEME 位置，不再二次传播。
    at_unix = (jd_utc - 2440587.5) * 86400.0
    illum = evaluate_illumination(
        r_teme, sun_direction_eci(at_unix), at_unix=at_unix)
    return {
        "satellite": satellite_name,
        "elevation": elevation,
        "azimuth": azimuth,
        "range_km": dist,
        "range_rate_kms": range_rate,
        "altitude_km": math.sqrt(r_ecef[0]**2 + r_ecef[1]**2 + r_ecef[2]**2) - WGS72_A,
        "epoch": epoch,
        "sunlit": illum.sunlit,
        "sunlit_status": illum.status,
        "illumination_model": illum.model,
        "boundary_uncertainty_s": illum.boundary_uncertainty_s,
    }


def _engine_now_unix() -> float:
    """全链路统一缺省时间：TimeEngine 模拟时刻优先，不可用才退系统时钟。"""
    try:
        from .new_spacetime import get_time_engine
        return float(get_time_engine().now_unix())
    except Exception:  # noqa: BLE001 - 引擎不可用不阻断位置计算
        return time.time()


def compute_satellite_state(
    satellite_name: str,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    when: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """用 sgp4 真传播，返回站坐标下的仰角/方位/距离/视线速度/多普勒。

    When:
        unix 秒；缺省取 TimeEngine（模拟时刻优先，未穿越即墙钟）。
    """
    _require_sgp4()
    catnr = BUILTIN_SATS.get(satellite_name)
    if catnr is None:
        return None
    try:
        line1, line2 = fetch_tle(catnr)
    except Exception:
        return None
    sat = Satrec.twoline2rv(line1, line2)
    if when is None:
        when = _engine_now_unix()
    # unix -> UTC 儒略日
    jd_utc = when / 86400.0 + 2440587.5
    return _state_from_satrec(sat, satellite_name, jd_utc,
                              observer_lat, observer_lon, observer_alt,
                              epoch=line1[18:32].strip())


def doppler_correction(
    satellite_name: str,
    nominal_freq_hz: float,
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    when: Optional[float] = None,
) -> Dict[str, Any]:
    """真视线速度多普勒：f_rx = f_tx * (1 - v_los/c)（远离为正）。

    ``when`` 显式 Unix UTC 秒；缺省才用当前时刻。
    """
    _require_sgp4()
    st = compute_satellite_state(satellite_name, observer_lat, observer_lon,
                                 observer_alt, when=when)
    if st is None:
        return {"error": f"无法计算卫星 {satellite_name} 位置（TLE 不可用或不在内置列表）"}
    v_los = st["range_rate_kms"]
    shift = -nominal_freq_hz * v_los / C_LIGHT  # 远离时频率降低
    return {
        "satellite": satellite_name,
        "nominal_freq_mhz": round(nominal_freq_hz / 1e6, 4),
        "corrected_freq_mhz": round((nominal_freq_hz + shift) / 1e6, 4),
        "doppler_shift_hz": round(shift, 1),
        "range_rate_kms": round(v_los, 3),
        "elevation_deg": round(st["elevation"], 1),
        "azimuth_deg": round(st["azimuth"], 1),
        "range_km": round(st["range_km"], 1),
        "epoch": st["epoch"],
        "method": "sgp4-teme-ecef-real-velocity",
    }


def visible_satellites(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    min_elevation: float = 0.0,
    satellite_type: str = "all",
    when: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """列出指定时刻仰角超过阈值的卫星（真 sgp4）。

    ``when`` 为显式 Unix UTC 秒；缺省才用当前时刻。仿真/快转时必须传入，
    否则与 ``sky_view_visible``（TimeEngine 口径）结果分叉。
    """
    out = []
    for name in BUILTIN_SATS:
        if satellite_type == "weather" and not any(
                k in name for k in ("NOAA", "METEOR", "FENGYUN")):
            continue
        if satellite_type == "amateur" and "ISS" not in name:
            continue
        st = compute_satellite_state(name, observer_lat, observer_lon,
                                     observer_alt, when=when)
        if st and st["elevation"] >= min_elevation:
            out.append(st)
    out.sort(key=lambda x: x["elevation"], reverse=True)
    return out


def _bisect_threshold(el_fn, t_lo: float, t_hi: float, target: float,
                      tol_s: float = 0.25, max_iter: int = 40) -> float:
    """二分法求仰角过零（阈值）时刻。

    细扫到 |el|<0.005°。
    这里用时间二分，收敛到 tol_s（0.25s），对 LEO 仰角变化率 ~0.5°/s 即 ~0.125° 精度，
    远好于原固定 30s 步长（±15s、~7° 误差）。
    """
    f_lo = el_fn(t_lo)
    f_hi = el_fn(t_hi)
    if f_lo is None or f_hi is None:
        return t_lo
    # 若两端同侧，直接返回中点（不应发生，调用方保证跨越）
    if (f_lo - target) * (f_hi - target) > 0:
        return 0.5 * (t_lo + t_hi)
    for _ in range(max_iter):
        if t_hi - t_lo < tol_s:
            break
        t_mid = 0.5 * (t_lo + t_hi)
        f_mid = el_fn(t_mid)
        if f_mid is None:
            return t_mid
        if (f_mid - target) * (f_lo - target) <= 0:
            t_hi = t_mid
            f_hi = f_mid
        else:
            t_lo = t_mid
            f_lo = f_mid
    return 0.5 * (t_lo + t_hi)


def _predict_passes_impl(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    hours: float = 24.0,
    min_elevation: float = 10.0,
    satellite_type: str = "all",
    nominal_freq_hz: Optional[float] = None,
    allow_stale: bool = True,
    start_unix: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """过境预测内核：返回 ``(passes, degraded)``。

    ``degraded`` 逐条说明「这颗星为什么没算/为什么不可信」——身份错配
    （编号指向另一颗对象）直接跳过；TLE 过期/失效仍出预报但降精度并标注。
    """
    _require_sgp4()
    from .tle_integrity import accuracy, freshness, verify_identity
    try:
        from .time_basis import validate_pass_window
    except ImportError:  # 扁平导入（脚本直接 import orbit）
        from time_basis import validate_pass_window

    coarse_step = 60.0   # 粗扫量级（~0.5min）
    fine_step = 2.0      # pass 内采样步长，找中天/多普勒极值
    # 显式时刻优先（仿真/测试口径）；缺省取 TimeEngine，与天空视图同一时间源
    t0 = _engine_now_unix() if start_unix is None else float(start_unix)
    t_end = t0 + hours * 3600.0
    names = [n for n in BUILTIN_SATS
             if not (satellite_type == "weather" and not any(
                 k in n for k in ("NOAA", "METEOR", "FENGYUN")))
             and not (satellite_type == "amateur" and "ISS" not in n)]
    passes: List[Dict[str, Any]] = []
    degraded: List[Dict[str, Any]] = []
    for name in names:
        catnr = BUILTIN_SATS[name]
        try:
            rec = fetch_tle_record(catnr)
        except Exception as e:  # noqa: BLE001 - 无数据如实留痕
            degraded.append({
                "satellite": name, "catnr": catnr,
                "reason": "tle_unavailable", "detail": str(e)})
            continue
        line1, line2 = rec["line1"], rec["line2"]
        object_name = str(rec.get("name") or "")
        identity = verify_identity(name, object_name)
        if identity["status"] == "mismatch":
            # 红线：编号指向另一颗/另一类对象，绝不拿错轨道算过境
            degraded.append({
                "satellite": name, "catnr": catnr,
                "reason": "identity_mismatch",
                "declared_name": name,
                "object_name": object_name,
                "detail": identity["reason"],
            })
            continue
        fresh = freshness(line1, rec.get("fetched_at"))
        acc = accuracy(fresh)
        if not acc["pass_eligible"] and not allow_stale:
            degraded.append({
                "satellite": name, "catnr": catnr,
                "reason": f"tle_{fresh['tier']}",
                "tle_tier": fresh["tier"],
                "detail": acc["note"],
            })
            continue
        if rec.get("refresh_status") == "not_updated":
            acc = dict(acc, refresh_status="not_updated",
                       refresh_error=rec.get("refresh_error", ""))
        tfmt = "%H:%M:%S" if acc["time_precision_s"] <= 1 else "%H:%M"
        sat = Satrec.twoline2rv(line1, line2)
        epoch_str = line1[18:32].strip()

        def _state_at(t_unix: float) -> Optional[Dict[str, Any]]:
            jd_utc = t_unix / 86400.0 + 2440587.5
            return _state_from_satrec(sat, name, jd_utc,
                                      observer_lat, observer_lon, observer_alt,
                                      epoch=epoch_str)

        def _el_at(t_unix: float) -> Optional[float]:
            st = _state_at(t_unix)
            return st["elevation"] if st else None

        # a) 粗扫：60s 步长记录 (t, el)
        coarse: List[Tuple[float, Optional[float]]] = []
        t = t0
        while t <= t_end:
            coarse.append((t, _el_at(t)))
            t += coarse_step
        # 跳过 sgp4 失效的段
        if all(e is None for _, e in coarse):
            continue

        # b) 找仰角 >= min_elevation 的粗采样段，细扫边界
        i = 0
        n = len(coarse)
        while i < n:
            ti, ei = coarse[i]
            if ei is None or ei < min_elevation:
                i += 1
                continue
            # 上升沿：找前一个 < min_elevation 的点
            j = i
            while j < n and coarse[j][1] is not None and coarse[j][1] >= min_elevation:
                j += 1
            # 本段为 coarse[i:j]（均 >= threshold）。
            # AOS = 在 coarse[i-1] 与 coarse[i] 之间二分（若 i==0 则 rise=ti）
            if i > 0 and coarse[i - 1][1] is not None:
                t_rise = _bisect_threshold(_el_at, coarse[i - 1][0], ti, min_elevation)
            else:
                t_rise = ti
            # LOS = 在 coarse[j-1] 与 coarse[j] 之间二分（若 j==n 则 set=coarse[j-1]）
            if j < n and coarse[j][1] is not None:
                t_set = _bisect_threshold(_el_at, coarse[j - 1][0], coarse[j][0], min_elevation)
            else:
                t_set = coarse[j - 1][0]

            # P11 守卫：负时长/零时长（时钟回跳或输入错乱）丢弃并留痕，
            # 绝不当正常过境输出（口径与 sat_passes 共用 validate_pass_window）。
            window = validate_pass_window(t_rise, t_set)
            if not window["ok"]:
                degraded.append({
                    "satellite": name, "catnr": catnr,
                    "reason": "non_positive_duration",
                    "detail": window["warning"],
                })
                i = j
                continue

            # c) pass 内细采样找最大仰角、方位、多普勒
            seg_states = []
            ts = t_rise
            while ts <= t_set + 1e-6:
                st = _state_at(ts)
                if st is not None:
                    seg_states.append((ts, st))
                ts += fine_step
            if not seg_states:
                i = j
                continue
            max_idx = max(range(len(seg_states)),
                          key=lambda k: seg_states[k][1]["elevation"])
            t_cul, s_cul = seg_states[max_idx]
            s_rise = _state_at(t_rise) or seg_states[0][1]
            s_set = _state_at(t_set) or seg_states[-1][1]
            dop = None
            if nominal_freq_hz and seg_states:
                shifts = [-nominal_freq_hz * s["range_rate_kms"] / C_LIGHT
                          for _, s in seg_states]
                dop = {"min_hz": round(min(shifts), 1),
                       "max_hz": round(max(shifts), 1)}
            passes.append({
                "satellite": name,
                # 内部排序键：rise_time 只有 HH:MM:SS，跨午夜按字符串排序会把
                # 明天 05:34 排到今晚 20:48 前面。按真实 epoch 排序，输出前删除。
                "_rise_epoch": t_rise,
                "rise_time": time.strftime(tfmt, time.localtime(t_rise)),
                "rise_azimuth": round(s_rise["azimuth"], 1),
                "culmination_time": time.strftime(tfmt, time.localtime(t_cul)),
                "max_elevation": round(s_cul["elevation"], 1),
                "culmination_azimuth": round(s_cul["azimuth"], 1),
                "set_time": time.strftime(tfmt, time.localtime(t_set)),
                "set_azimuth": round(s_set["azimuth"], 1),
                "duration_s": round(t_set - t_rise),
                "doppler_hz": dop,
                # 真实性透出：身份、历元、新鲜度、精度风险
                "tle_verified": identity["status"] == "verified",
                "tle_epoch_utc": fresh.get("epoch_utc"),
                "tle_tier": fresh["tier"],
                "tle_tier_label": fresh["label"],
                "tle_age_days": fresh["age_days"],
                "tle_cache_time_utc": (
                    datetime.fromtimestamp(float(rec["fetched_at"]),
                                           tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                    if rec.get("fetched_at") is not None else None),
                "refresh_status": rec.get("refresh_status", "unknown"),
                "refresh_error": rec.get("refresh_error", ""),
                "accuracy_risk": acc["risk"],
                "accuracy_note": acc["note"],
                "time_precision_s": acc["time_precision_s"],
            })
            i = j
    passes.sort(key=lambda p: p["_rise_epoch"])
    for p in passes:
        p.pop("_rise_epoch", None)
    return passes, degraded


def predict_passes(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    hours: float = 24.0,
    min_elevation: float = 10.0,
    satellite_type: str = "all",
    nominal_freq_hz: Optional[float] = None,
    start_unix: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """预测未来 hours 小时内的卫星过境事件（列表兼容接口）。

    ``start_unix`` 显式 UTC 秒；缺省取 TimeEngine（模拟时刻优先，未穿越即墙钟）。

    自适应粗/细扫描（60s 粗扫 + 二分收敛 + 2s 中天采样）。每条结果都附带
    TLE 身份/历元/新鲜度/精度风险字段；过期或失效时时刻自动降到分钟级。
    """
    return _predict_passes_impl(
        observer_lat, observer_lon, observer_alt, hours,
        min_elevation, satellite_type, nominal_freq_hz,
        start_unix=start_unix)[0]


def predict_passes_report(
    observer_lat: float,
    observer_lon: float,
    observer_alt: float = 0.0,
    hours: float = 24.0,
    min_elevation: float = 10.0,
    satellite_type: str = "all",
    nominal_freq_hz: Optional[float] = None,
    allow_stale: bool = True,
    require_trusted_station: bool = False,
    start_unix: Optional[float] = None,
) -> Dict[str, Any]:
    """过境面板用的完整报告：过境列表 + 被跳过/降级的诚实清单。

    ``allow_stale=False`` 时过期/失效 TLE 默认不参与过境（目录默认口径）；
    无论是否放行，``degraded`` 都会逐条说明原因，调用方不得静默忽略。

    ``require_trusted_station=True`` 时先评站址可信度（P11），口径为**只读/
    计算**：缺坐标/缺省兜底 → 不计算任何过境，``degraded`` 留痕
    ``station_untrusted``；显式 (0,0)/海上坐标照常计算，但把可疑位置原因写进
    ``station.station_warning`` 与 ``degraded`` 的 ``station_warning`` 条目。
    """
    from .station import trust_for_calculation, station_from_user

    station_ev = trust_for_calculation(station_from_user(
        observer_lat, observer_lon, observer_alt))
    if require_trusted_station and not station_ev["trusted"]:
        return {
            "n_passes": 0,
            "passes": [],
            "degraded": [{
                "satellite": "（站址）", "catnr": 0,
                "reason": "station_untrusted",
                "detail": "；".join(station_ev["reasons"]),
            }],
            "station": station_ev,
            "tle_policy": {
                "allow_stale": bool(allow_stale),
                "fresh_max_hours": 24.0,
                "usable_max_days": 7.0,
                "expired_max_days": 30.0,
            },
        }
    passes, degraded = _predict_passes_impl(
        observer_lat, observer_lon, observer_alt, hours,
        min_elevation, satellite_type, nominal_freq_hz, allow_stale,
        start_unix)
    if station_ev.get("station_warning"):
        # 放宽必须留痕：位置被启发式怀疑也要进降级清单，用户看得见。
        # 放在末尾：站址 warning 是「照常计算但位置可疑」，不该抢掉
        # 首条（首条仍属于真正导致跳过/降级的原因，如 identity_mismatch）。
        degraded = list(degraded) + [{
            "satellite": "（站址）", "catnr": 0,
            "reason": "station_warning",
            "detail": station_ev["station_warning"],
        }]
    return {
        "n_passes": len(passes),
        "passes": passes,
        "degraded": degraded,
        "station": station_ev,
        "tle_policy": {
            "allow_stale": bool(allow_stale),
            "fresh_max_hours": 24.0,
            "usable_max_days": 7.0,
            "expired_max_days": 30.0,
        },
    }
