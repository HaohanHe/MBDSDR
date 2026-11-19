# SPDX-License-Identifier: MIT
"""AR 指向闭环：把一次拍星解算变成「残差数字 + 结构化结论」。

为什么单独成模块
----------------
云台/赤道仪的粗指向只能到度级（IMU 户外方位误差常态 5–15°），spec D4
把「只有拍星解算的残差能证明对得准」写成承诺。但「解算成功」不等于
「指向准确」：解算器可能返回一个与指令指向相差几十度的姿态。本模块只做
三件事，并且对每一件的失败都给出可机读的原因：

1. **前置校验**：星点数量、站址/时刻、相机视场先验，任何一项不合格就
   返回结构化失败，不进入解算、不返回半信半疑的坐标；
2. **解算**：默认复用 ``sky_solve.solve_frame``（tetra3/ASTAP 可选适配），
   测试可注入替身；
3. **残差**：把解算得到的 RA/Dec 经既有 ``celestial_geometry`` 坐标链
   转到地平 az/el，与指令 az/el 求球面角距。

只给数字，不下结论
------------------
``residual_deg`` 是唯一可比较的指向证据；``within_tolerance`` 只是拿
``RESIDUAL_TOLERANCE_DEG`` 做一次分级，**不是「已对准」的结论**。是否
可以宣称对准，由上层结合系统误差、安装矩阵与业务门限决定，本模块的
``reason`` 也不写任何「已对准」字样。

坐标与角度口径
--------------
* 输入/输出角度均为度；az 北=0 东=90，el 地平=0 天顶=90；
* J2000 ↔ az/el 的换算全部走 ``celestial_geometry`` 的既有链
  （``j2000_to_altaz`` / ``altaz_to_j2000``），本模块不另写岁差/章动；
* 球面角距复用 ``sky_interaction.angular_distance_deg``；
* UTC → 儒略日复用 ``new_spacetime.utc_to_jd``。

解算器注入契约
--------------
``solver`` 为可调用对象，签名与 ``sky_solve.solve_frame`` 对齐::

    solver(centroids_px, image_size, fov_deg, *, prior, observer) -> Mapping

成功时返回的 Mapping 可以直接给 ``ra_deg`` / ``dec_deg``（本模块据此
算残差），也可以给 ``solved: {az_deg, alt_deg, roll_deg}``（``sky_solve``
的真实返回契约）；后者会经 ``altaz_to_j2000`` 反算成 RA/Dec，再正向
转回 az/el 算残差——同一条链，不另造一套公式。

诚实边界
--------
本模块只验证协议、残差算术与失败原因映射；**没有接相机、没有真机夜空
数据**，也没有在真实 tetra3 星表 / ASTAP 二进制上验证解算精度。假解算
用例通过只证明「给定已知 RA/Dec 时残差算得对」，不证明夜空已对准。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple

from .sky_solve import MIN_SOLVE_STARS

__all__ = [
    "CameraParams",
    "MIN_SOLVE_STARS",
    "RESIDUAL_TOLERANCE_DEG",
    "SolveOutcome",
    "StarCentroid",
    "solve_and_verify",
]

#: 残差分级阈值（度）。只用于 ``within_tolerance``，不构成对准结论。
RESIDUAL_TOLERANCE_DEG: float = 0.5


@dataclass(frozen=True)
class StarCentroid:
    """一个星点的像素坐标。单位由相机标定决定，本模块只要求有限数值。"""

    x_px: float
    y_px: float


@dataclass(frozen=True)
class CameraParams:
    """解算所需的相机先验。

    ``fov_deg`` 是视场直径（度），必须为 ``(0, 180]`` 内的有限值——
    盲解算不可靠，FOV 是必需输入而不是提示。
    """

    width_px: float
    height_px: float
    fov_deg: float


@dataclass(frozen=True)
class SolveOutcome:
    """一次「解算 → 残差」的完整结果。

    Attributes:
        ok: 解算成功且残差可算才为 True；即使残差很大也保持 True。
        ra_deg: 解算得到的赤经（度，J2000，[0, 360)）；失败为 None。
        dec_deg: 解算得到的赤纬（度，J2000，[-90, 90]）；失败为 None。
        roll_deg: 解算器给出的横滚（度），原样透传；未知为 None。
        matched_stars: 参与解算的星数（解算器报告值）。
        residual_deg: 解算指向与指令指向的球面角距（度）；失败为 None。
        reason: 人类可读原因；成功为空串，失败不含任何对准结论。
        error: 机读原因代码（``too_few_points`` / ``no_site`` /
            ``bad_camera`` / ``solver_unavailable`` / ``bad_command`` /
            ``bad_points`` / ``solver_failed`` / ``bad_solution``）；
            成功为空串。
        within_tolerance: ``residual_deg <= RESIDUAL_TOLERANCE_DEG`` 的
            分级结果；失败为 False。**不是「已对准」的结论。**
    """

    ok: bool
    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    matched_stars: int = 0
    residual_deg: Optional[float] = None
    reason: str = ""
    error: str = ""
    within_tolerance: bool = False


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fail(code: str, reason: str) -> SolveOutcome:
    return SolveOutcome(ok=False, reason=reason, error=code)


def _parse_camera(camera: Any
                  ) -> Tuple[Optional[Tuple[float, float, float]], Optional[str]]:
    if not isinstance(camera, CameraParams):
        return None, (f"相机参数必须是 CameraParams，收到 {type(camera).__name__}")
    width = _finite(camera.width_px)
    height = _finite(camera.height_px)
    if width is None or height is None or width <= 0.0 or height <= 0.0:
        return None, (f"相机尺寸非法：需要正的 width/height，收到 "
                      f"{camera.width_px!r}×{camera.height_px!r}")
    fov = _finite(camera.fov_deg)
    if fov is None or not (0.0 < fov <= 180.0):
        return None, (f"相机视场非法：fov_deg 必须在 (0, 180] 度内，"
                      f"收到 {camera.fov_deg!r}")
    return (width, height, fov), None


def _parse_centroids(centroids: Any
                     ) -> Tuple[Optional[list], Optional[Tuple[str, str]]]:
    """规整星点输入；返回 (points, None) 或 (None, (code, reason))。"""
    if centroids is None or isinstance(centroids, (str, bytes, dict)):
        return None, ("bad_points",
                      f"星点输入非法：需要 StarCentroid 或 (x, y) 序列，"
                      f"收到 {type(centroids).__name__}")
    try:
        items = list(centroids)
    except TypeError:
        return None, ("bad_points",
                      f"星点输入非法：需要 StarCentroid 或 (x, y) 序列，"
                      f"收到 {type(centroids).__name__}")
    if len(items) < MIN_SOLVE_STARS:
        return None, ("too_few_points",
                      f"星点不足：检测到 {len(items)} 颗，"
                      f"解算至少需要 {MIN_SOLVE_STARS} 颗")

    points = []
    for index, item in enumerate(items):
        if isinstance(item, StarCentroid):
            x, y = _finite(item.x_px), _finite(item.y_px)
        else:
            if isinstance(item, (str, bytes, dict)):
                return None, ("bad_points",
                              f"第 {index} 个星点不是 (x, y)，"
                              f"收到 {type(item).__name__}")
            try:
                coords = list(item)
            except TypeError:
                return None, ("bad_points",
                              f"第 {index} 个星点不是 (x, y)，"
                              f"收到 {type(item).__name__}")
            if len(coords) < 2:
                return None, ("bad_points",
                              f"第 {index} 个星点缺少 x/y"
                              f"（收到 {len(coords)} 个分量）")
            x, y = _finite(coords[0]), _finite(coords[1])
        if x is None or y is None:
            return None, ("bad_points",
                          f"第 {index} 个星点坐标非有限（NaN/Inf/非数值）")
        points.append((x, y))
    return points, None


def _parse_site(site_lat_deg: Any, site_lon_deg: Any, at_utc: Any
                ) -> Tuple[Optional[Tuple[float, float, float]], Optional[str]]:
    lat = _finite(site_lat_deg)
    lon = _finite(site_lon_deg)
    if lat is None or not (-90.0 <= lat <= 90.0):
        return None, (f"缺少或非法站址纬度：需要 -90..90 的有限度数，"
                      f"收到 {site_lat_deg!r}")
    if lon is None or not (-360.0 <= lon <= 360.0):
        return None, (f"缺少或非法站址经度：需要 -360..360 的有限度数，"
                      f"收到 {site_lon_deg!r}")
    if not isinstance(at_utc, datetime):
        return None, (f"缺少或非法观测时刻：需要 datetime，"
                      f"收到 {type(at_utc).__name__}")

    from .new_spacetime import utc_to_jd

    try:
        jd = float(utc_to_jd(at_utc))
    except Exception as exc:  # 时钟/时区库异常也要转成结构化原因
        return None, (f"观测时刻无法换算成儒略日："
                      f"{type(exc).__name__}: {exc}")
    if not math.isfinite(jd):
        return None, "观测时刻换算出的儒略日非有限"
    return (lat, lon, jd), None


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _parse_radec(raw: Mapping[str, Any],
                 station: Any, jd: float) -> Tuple[Optional[float],
                                                   Optional[float], Optional[str]]:
    """从解算结果取 RA/Dec；没有就直接取，有 ``solved`` 就反算。"""
    ra = _finite(_first(raw, "ra_deg", "ra", "RA", "RA_deg"))
    dec = _finite(_first(raw, "dec_deg", "dec", "Dec", "Dec_deg", "DEC"))
    if ra is not None and dec is not None:
        if -90.0 <= dec <= 90.0:
            return ra % 360.0, dec, None
        return None, None, f"解算赤纬越界（{dec}°，合法范围 -90..90）"

    solved = raw.get("solved")
    if isinstance(solved, Mapping):
        from .celestial_geometry import (altaz_to_j2000, radec_from_vec,
                                         vec_from_azalt)

        az = _finite(_first(solved, "az_deg", "az", "AZ"))
        alt = _finite(_first(solved, "alt_deg", "alt", "ALT", "Alt"))
        if az is None or alt is None or not (-90.0 <= alt <= 90.0):
            return None, None, "解算输出缺少可用的 RA/Dec 或 az/el"
        try:
            v_j2000 = altaz_to_j2000(vec_from_azalt(az, alt), station, jd)
            ra_deg, dec_deg = radec_from_vec(v_j2000)
        except Exception as exc:
            return None, None, (f"解算地平坐标反算 RA/Dec 失败："
                                f"{type(exc).__name__}: {exc}")
        return ra_deg % 360.0, float(dec_deg), None

    return None, None, "解算输出缺少 RA/Dec（也没有可反算的 az/el）"


def _parse_roll(raw: Mapping[str, Any]) -> Optional[float]:
    roll = _finite(_first(raw, "roll_deg", "Roll", "ROLL", "roll"))
    if roll is not None:
        return roll
    solved = raw.get("solved")
    if isinstance(solved, Mapping):
        return _finite(_first(solved, "roll_deg", "roll"))
    return None


def _parse_matched(raw: Mapping[str, Any]) -> int:
    value = _first(raw, "matched_stars", "Matches", "matches", "NUMSTARS")
    number = _finite(value)
    if number is None or number < 0.0:
        return 0
    return int(number)


def _default_solver(points: Iterable[Tuple[float, float]],
                    image_size: Tuple[float, float],
                    fov_deg: float, *, prior: Mapping[str, Any],
                    observer: Mapping[str, Any]) -> Mapping[str, Any]:
    """默认解算路径：复用 ``sky_solve.solve_frame`` 的唯一入口。"""
    from .sky_solve import solve_frame

    return solve_frame(points, image_size, fov_deg,
                       prior=prior, observer=observer)


def solve_and_verify(centroids: Any,
                     camera: CameraParams,
                     commanded_az_deg: Any,
                     commanded_el_deg: Any,
                     site_lat_deg: Any,
                     site_lon_deg: Any,
                     at_utc: Any,
                     solver: Optional[Callable[..., Mapping[str, Any]]] = None
                     ) -> SolveOutcome:
    """解算一帧星点并返回指向残差（不判定是否已对准）。

    Args:
        centroids: ``StarCentroid`` 序列或 ``(x_px, y_px)`` 序列；数量下限
            沿用 ``sky_solve.MIN_SOLVE_STARS``。
        camera: ``CameraParams``；FOV 必须在 ``(0, 180]`` 度内。
        commanded_az_deg / commanded_el_deg: 云台/赤道仪的指令指向（度），
            el 必须在 -90..90。
        site_lat_deg / site_lon_deg: 观测站纬度/经度（度）。
        at_utc: 观测时刻（``datetime``；naive 时刻按 UTC 解释）。
        solver: 可调用解算器，签名见模块 docstring；None 时探测
            ``sky_solve.solver_status()`` 并走 ``solve_frame``。

    Returns:
        ``SolveOutcome``。``ok=True`` 仅在解算成功且残差可算时出现；
        失败时 ``ra_deg`` / ``dec_deg`` / ``residual_deg`` 一律为 None，
        ``error`` 给出机读代码，``reason`` 给出具体原因。

    Raises:
        无。解算器抛出的异常会被转成 ``solver_failed``。
    """
    points, points_error = _parse_centroids(centroids)
    if points_error is not None:
        return _fail(*points_error)

    camera_checked, camera_error = _parse_camera(camera)
    if camera_error is not None:
        return _fail("bad_camera", camera_error)
    assert camera_checked is not None
    width, height, fov = camera_checked

    site, site_error = _parse_site(site_lat_deg, site_lon_deg, at_utc)
    if site_error is not None:
        return _fail("no_site", site_error)
    assert site is not None
    lat, lon, jd = site

    commanded_az = _finite(commanded_az_deg)
    commanded_el = _finite(commanded_el_deg)
    if commanded_az is None or commanded_el is None:
        return _fail("bad_command",
                     "指令指向非法：commanded_az_deg / commanded_el_deg "
                     "必须是有限数值")
    if not (-90.0 <= commanded_el <= 90.0):
        return _fail("bad_command",
                     f"指令俯仰越界（{commanded_el}°，合法范围 -90..90）")
    commanded_az %= 360.0

    if solver is None:
        from .sky_solve import solver_status

        status = solver_status()
        if not status.get("available"):
            reason = str(status.get("reason") or "星点解算库不可用")
            return _fail("solver_unavailable", f"星点解算不可用：{reason}")
        solver_fn: Callable[..., Mapping[str, Any]] = _default_solver
    else:
        if not callable(solver):
            return _fail("solver_unavailable",
                         f"注入的解算器不可调用，收到 {type(solver).__name__}")
        solver_fn = solver

    prior = {"az_deg": commanded_az, "alt_deg": commanded_el, "roll_deg": 0.0}
    observer = {"lat_deg": lat, "lon_deg": lon, "jd_ut1": jd}

    try:
        raw = solver_fn(points, (width, height), fov,
                        prior=prior, observer=observer)
    except Exception as exc:
        return _fail("solver_failed",
                     f"解算器抛出异常：{type(exc).__name__}: {exc}")

    if not isinstance(raw, Mapping):
        return _fail("bad_solution",
                     f"解算器返回不可解析（收到 {type(raw).__name__}）")
    if raw.get("ok") is False:
        code = str(raw.get("error") or "")
        detail = str(raw.get("reason") or "解算器未给出原因")
        mapped = {
            "unavailable": "solver_unavailable",
            "too_few_stars": "too_few_points",
        }.get(code, "solver_failed")
        return _fail(mapped, detail)

    from .celestial_geometry import (GroundStation, azalt_from_vec,
                                     j2000_to_altaz, vec_from_radec)

    station = GroundStation(lon, lat, 0.0, "sky_pointing_pipeline")

    ra, dec, radec_error = _parse_radec(raw, station, jd)
    if radec_error is not None:
        return _fail("bad_solution", radec_error)
    assert ra is not None and dec is not None

    try:
        v_altaz = j2000_to_altaz(vec_from_radec(ra, dec), station, jd)
        solved_az, solved_alt = azalt_from_vec(v_altaz)
    except Exception as exc:
        return _fail("solver_failed",
                     f"赤道→地平换算失败：{type(exc).__name__}: {exc}")

    from .sky_interaction import angular_distance_deg

    residual = angular_distance_deg(solved_az, solved_alt,
                                    commanded_az, commanded_el)

    return SolveOutcome(
        ok=True,
        ra_deg=ra,
        dec_deg=dec,
        roll_deg=_parse_roll(raw),
        matched_stars=_parse_matched(raw),
        residual_deg=residual,
        reason="",
        error="",
        within_tolerance=residual <= RESIDUAL_TOLERANCE_DEG,
    )
