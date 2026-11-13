# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 卫星受晒 / 地影判定
====================================

对一颗地球卫星，给出 **受晒(sunlit) / 本影(umbra) / 半影(penumbra)** 三态，
供天空视图、过境预报和链路预算调用。

模型
----
两套几何模型，返回结果里显式标注用的是哪一套：

1. ``conical``（圆锥影，默认）：与 gpredict ``Sat_Eclipsed`` 同构
   （``reference/gpredict/src/sgpsdp/solar.c:46-64``，由 ``sat-vis.c:55-105``
   调用）。在卫星处算地球视半径 ``sd_e = asin(R_e / |r|)``、太阳视半径
   ``sd_s = asin(R_s / |r_sun - r|)``，以及卫星→太阳与卫星→地球的夹角
   ``delta``：

   - ``delta < sd_e - sd_s`` → 本影（太阳被完全遮挡）
   - ``sd_e - sd_s <= delta < sd_e + sd_s`` → 半影
   - 其余 → 受晒

   gpredict 本身把半影当受晒（二值），本仓保留三态以便渲染晨昏线。

2. ``cylindrical``（圆柱影）：把地球阴影当作沿反日方向的无限长圆柱。
   卫星投影落在背阳侧且到地日轴的垂距 ``< R_e`` 即本影，否则受晒。
   该模型没有半影带——这是模型本身的近似，不是漏算。

不确定度
--------
两套模型都是几何近似，不建模大气/折射/太阳视直径变化，也没有卫星位置
误差和太阳历表误差的传播。返回值里的 ``boundary_uncertainty_s`` 是**状态
切换边界附近**的经验不保证量（不是全局误差）：

- 圆锥影 ±2 s：太阳方向低精度公式误差约 0.1°，LEO 交线角速度约
  0.06°/s，折算约 1.7 s；半影带本身约 8 s 宽，边界取整到 2 s。
- 圆柱影 ±5 s：圆柱把半影带压成零宽，本影半径用的是地球赤道半径
  （比同高度圆锥本影半径大约 30 km），LEO 下折合约 4~5 s。

时间与输入纪律
--------------
- 时间**必须显式**：``at_unix``（Unix UTC 秒）或 ``time_engine``（任何带
  ``now_unix()`` 的对象）。两者都没有时取全局 :class:`TimeEngine`；本模块
  **不读墙钟**，也不 import ``time``/``datetime.now``。
- 卫星位置与太阳方向都是显式入参。太阳方向为零向量属非法输入，直接
  ``ValueError``；没有太阳方向数据时传 ``None``，返回 ``unknown`` 而不是猜。

渲染层契约
----------
:class:`Illumination` 是稳定接口：``status``、``sunlit``、``basis``、``model``、
``boundary_uncertainty_s``、``at_unix``、``geometry`` 字段只增不改；
``to_dict()`` 输出可直接 JSON 序列化。渲染层只需 ``status`` 和 ``basis``。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

__all__ = [
    "STATUS_SUNLIT",
    "STATUS_UMBRA",
    "STATUS_PENUMBRA",
    "STATUS_UNKNOWN",
    "MODEL_CONICAL",
    "MODEL_CYLINDRICAL",
    "MODELS",
    "BOUNDARY_UNCERTAINTY_S",
    "Illumination",
    "sun_direction_eci",
    "evaluate_illumination",
    "evaluate_from_position",
    "evaluate_from_tle",
]

# ── 状态（渲染层按这三个字符串分支；理由见模块 docstring） ──────────────
STATUS_SUNLIT = "sunlit"
STATUS_UMBRA = "umbra"
STATUS_PENUMBRA = "penumbra"
STATUS_UNKNOWN = "unknown"

MODEL_CONICAL = "conical"
MODEL_CYLINDRICAL = "cylindrical"
MODELS = (MODEL_CONICAL, MODEL_CYLINDRICAL)

#: 边界附近不保证的秒数（经验值，依据见模块 docstring）
BOUNDARY_UNCERTAINTY_S: Dict[str, float] = {
    MODEL_CONICAL: 2.0,
    MODEL_CYLINDRICAL: 5.0,
}

#: 地球赤道半径（WGS84），km
R_EARTH_KM = 6378.137
#: 太阳半径，km
R_SUN_KM = 696000.0
#: 1 AU，km（几何判定只需量级，日地距离变化 <2% 对边界影响 <1 s）
AU_KM = 149597870.700

_UNIX_EPOCH_JD = 2440587.5


@dataclass(frozen=True)
class Illumination:
    """一次受晒判定的结果（稳定契约，见模块 docstring）。"""

    status: str
    model: str
    basis: str
    boundary_uncertainty_s: float
    sunlit: Optional[bool]
    at_unix: Optional[float] = None
    geometry: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 友好快照（渲染层/日志直接用）。"""
        return {
            "status": self.status,
            "model": self.model,
            "basis": self.basis,
            "boundary_uncertainty_s": self.boundary_uncertainty_s,
            "sunlit": self.sunlit,
            "at_unix": self.at_unix,
            "geometry": dict(self.geometry),
        }


def _finite_vec(v: Sequence[float], label: str, expected_len: int = 3) -> Tuple[float, float, float]:
    if v is None or len(v) != expected_len:
        raise ValueError(f"{label}必须是 {expected_len} 维向量，收到 {v!r}")
    out = tuple(float(x) for x in v)
    if not all(math.isfinite(x) for x in out):
        raise ValueError(f"{label}含 NaN/Inf：{v!r}")
    return out


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


def _unit(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    n = _norm(v)
    return (v[0] / n, v[1] / n, v[2] / n)


def _angle_rad(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    """两个方向矢量之间的夹角（弧度），自带夹取防止 acos 域外。"""
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        raise ValueError("零向量无法定义夹角")
    dot = sum(x * y for x, y in zip(a, b)) / (na * nb)
    return math.acos(max(-1.0, min(1.0, dot)))


def sun_direction_eci(at_unix: float) -> Tuple[float, float, float]:
    """显式时刻 ``at_unix`` 对应的地心→太阳单位矢量（赤道惯性系）。

    低精度 NOAA 公式（与 ``sat_passes._sun_eci_unit`` 同一口径），误差约
    0.1°；对地影判定的边界影响已在 ``boundary_uncertainty_s`` 里计入。
    不做 TT/UT1 修正（差异 <0.02°，远小于公式本身的近似误差）。

    参数:
        at_unix: Unix UTC 秒。**必须显式传入**，本函数不读系统时钟。
    """
    ts = float(at_unix)
    if not math.isfinite(ts):
        raise ValueError(f"at_unix 必须是有限数值，收到 {at_unix!r}")
    jd = ts / 86400.0 + _UNIX_EPOCH_JD
    n = jd - 2451545.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(mean_lon + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    return (
        math.cos(lam),
        math.cos(eps) * math.sin(lam),
        math.sin(eps) * math.sin(lam),
    )


def _resolve_unix(at_unix: Optional[float],
                  time_engine: Any = None) -> Tuple[float, str]:
    """解析显式时间源。返回 (unix, 来源标签)；三个入口都没有时取 TimeEngine。"""
    if at_unix is not None:
        ts = float(at_unix)
        if not math.isfinite(ts):
            raise ValueError(f"at_unix 必须是有限数值，收到 {at_unix!r}")
        return ts, "at_unix"
    if time_engine is not None:
        ts = float(time_engine.now_unix())
        if not math.isfinite(ts):
            raise ValueError("time_engine.now_unix() 返回非有限数值")
        return ts, "time_engine"
    try:
        from .new_spacetime import get_time_engine
    except ImportError:  # 旧脚本扁平导入
        from new_spacetime import get_time_engine  # type: ignore
    ts = float(get_time_engine().now_unix())
    if not math.isfinite(ts):
        raise ValueError("全局 TimeEngine 返回非有限数值")
    return ts, "global_time_engine"


def evaluate_illumination(
    position_eci_km: Sequence[float],
    sun_direction: Optional[Sequence[float]] = None,
    *,
    model: str = MODEL_CONICAL,
    at_unix: Optional[float] = None,
) -> Illumination:
    """核心判据：给定卫星位置与太阳方向，返回三态受晒结果。

    参数:
        position_eci_km: 地心惯性系卫星位置 ``(x, y, z)``，km。必须显式。
        sun_direction: 地心→太阳的**方向**（不必是单位矢量，内部归一）。
            ``None`` 表示没有太阳数据 → 返回 ``unknown``，不做猜测；
            零向量/NaN 属非法输入 → ``ValueError``。
        model: ``"conical"``（默认，gpredict 同构）或 ``"cylindrical"``。
        at_unix: 仅用于结果溯源与记录；本函数不依赖时间（位置已给出）。

    返回:
        :class:`Illumination`。``penumbra`` 时 ``sunlit=None``（部分受照，
        不是二值可回答的状态）。
    """
    if model not in MODELS:
        raise ValueError(f"未知地影模型 {model!r}，可选：{MODELS}")
    pos = _finite_vec(position_eci_km, "卫星位置")
    r_sat = _norm(pos)
    if r_sat <= R_EARTH_KM:
        # 位置在地球内部：不是可判定的在轨状态，明确报错而不是给个状态。
        raise ValueError(
            f"卫星位置距地心 {r_sat:.1f} km 未高于地球半径 {R_EARTH_KM:.1f} km，"
            "无法做地影判定（请确认传入的是 ECI 位置而非经纬度）")

    if sun_direction is None:
        return Illumination(
            status=STATUS_UNKNOWN,
            model=model,
            basis="未提供太阳方向数据，未做受晒判定（不猜测）",
            boundary_uncertainty_s=0.0,
            sunlit=None,
            at_unix=at_unix,
        )

    sun_raw = _finite_vec(sun_direction, "太阳方向")
    if _norm(sun_raw) == 0.0:
        raise ValueError(
            "太阳方向向量长度为零，无法定义地日轴；"
            "请提供地心→太阳的非零方向（可先归一化）")
    sun = _unit(sun_raw)

    if model == MODEL_CYLINDRICAL:
        return _cylinder(pos, r_sat, sun, at_unix)
    return _cone(pos, r_sat, sun, at_unix)


def _base_geometry(pos: Tuple[float, float, float],
                   sun: Tuple[float, float, float]) -> Dict[str, float]:
    return {
        "r_sat_km": _norm(pos),
        "sun_x": sun[0],
        "sun_y": sun[1],
        "sun_z": sun[2],
    }


def _cone(pos: Tuple[float, float, float], r_sat: float,
          sun: Tuple[float, float, float],
          at_unix: Optional[float]) -> Illumination:
    """圆锥影（gpredict ``Sat_Eclipsed`` 同构）。"""
    sun_vec = (sun[0] * AU_KM, sun[1] * AU_KM, sun[2] * AU_KM)
    to_sun = (sun_vec[0] - pos[0], sun_vec[1] - pos[1], sun_vec[2] - pos[2])
    to_earth = (-pos[0], -pos[1], -pos[2])

    sd_earth = math.asin(min(1.0, R_EARTH_KM / r_sat)) if r_sat > R_EARTH_KM else math.pi / 2
    sd_sun = math.asin(min(1.0, R_SUN_KM / _norm(to_sun)))
    delta = _angle_rad(to_sun, to_earth)

    geom = _base_geometry(pos, sun)
    geom.update({
        "earth_angular_radius_deg": math.degrees(sd_earth),
        "sun_angular_radius_deg": math.degrees(sd_sun),
        "sun_earth_separation_deg": math.degrees(delta),
        "umbra_half_angle_deg": math.degrees(sd_earth - sd_sun),
        "penumbra_half_width_deg": math.degrees(2.0 * sd_sun),
    })

    if sd_earth < sd_sun:
        # 卫星远到地球视半径比太阳还小（深空），不可能成影；按受晒。
        status = STATUS_SUNLIT
        basis = (f"圆锥影：地球视半径 {math.degrees(sd_earth):.2f}° 小于太阳视半径 "
                 f"{math.degrees(sd_sun):.2f}°，地球不足以遮挡日面 → 受晒")
    elif delta < sd_earth - sd_sun:
        status = STATUS_UMBRA
        basis = (f"圆锥影：夹角 δ={math.degrees(delta):.2f}° < 地球视半径-太阳视半径 "
                 f"{math.degrees(sd_earth - sd_sun):.2f}° → 本影（太阳完全被遮）")
    elif delta < sd_earth + sd_sun:
        status = STATUS_PENUMBRA
        basis = (f"圆锥影：δ={math.degrees(delta):.2f}° 落在半影带 "
                 f"[{math.degrees(sd_earth - sd_sun):.2f}°, "
                 f"{math.degrees(sd_earth + sd_sun):.2f}°] → 半影（部分受照）")
    else:
        status = STATUS_SUNLIT
        basis = (f"圆锥影：δ={math.degrees(delta):.2f}° ≥ 地球视半径+太阳视半径 "
                 f"{math.degrees(sd_earth + sd_sun):.2f}° → 受晒")

    return Illumination(
        status=status,
        model=MODEL_CONICAL,
        basis=basis,
        boundary_uncertainty_s=BOUNDARY_UNCERTAINTY_S[MODEL_CONICAL],
        sunlit=(True if status == STATUS_SUNLIT else
                False if status == STATUS_UMBRA else None),
        at_unix=at_unix,
        geometry=geom,
    )


def _cylinder(pos: Tuple[float, float, float], r_sat: float,
              sun: Tuple[float, float, float],
              at_unix: Optional[float]) -> Illumination:
    """圆柱影：沿反日方向的无限长圆柱，无半影带。"""
    along = sum(p * s for p, s in zip(pos, sun))
    perp_vec = (pos[0] - along * sun[0],
                pos[1] - along * sun[1],
                pos[2] - along * sun[2])
    perp = _norm(perp_vec)

    geom = _base_geometry(pos, sun)
    geom.update({
        "along_sun_axis_km": along,
        "perp_axis_km": perp,
        "shadow_radius_km": R_EARTH_KM,
    })

    if along >= 0.0:
        status = STATUS_SUNLIT
        basis = (f"圆柱影：卫星在朝阳半球（沿日轴投影 {along:.0f} km ≥ 0）"
                 " → 受晒")
    elif perp < R_EARTH_KM:
        status = STATUS_UMBRA
        basis = (f"圆柱影：卫星在背阳侧，到地日轴垂距 {perp:.0f} km < "
                 f"地球半径 {R_EARTH_KM:.0f} km → 本影")
    else:
        status = STATUS_SUNLIT
        basis = (f"圆柱影：卫星在背阳侧但垂距 {perp:.0f} km ≥ "
                 f"地球半径 {R_EARTH_KM:.0f} km → 受晒")
    # 圆柱模型没有半影带：边界附近靠 boundary_uncertainty_s 诚实标注。

    return Illumination(
        status=status,
        model=MODEL_CYLINDRICAL,
        basis=basis,
        boundary_uncertainty_s=BOUNDARY_UNCERTAINTY_S[MODEL_CYLINDRICAL],
        sunlit=(status == STATUS_SUNLIT),
        at_unix=at_unix,
        geometry=geom,
    )


def evaluate_from_position(
    position_eci_km: Sequence[float],
    sun_direction: Optional[Sequence[float]] = None,
    *,
    at_unix: Optional[float] = None,
    time_engine: Any = None,
    model: str = MODEL_CONICAL,
) -> Illumination:
    """位置入口：太阳方向缺省时按显式时刻算，时间来源记入返回值。

    ``at_unix`` / ``time_engine`` 都没有时取全局 TimeEngine；三者都拿不到
    可用时间则 ValueError（本模块不读墙钟）。
    """
    ts, _source = _resolve_unix(at_unix, time_engine)
    if sun_direction is None:
        sun_direction = sun_direction_eci(ts)
    return evaluate_illumination(
        position_eci_km, sun_direction, model=model, at_unix=ts)


def evaluate_from_tle(
    tle_line1: str,
    tle_line2: str,
    *,
    at_unix: Optional[float] = None,
    time_engine: Any = None,
    sun_direction: Optional[Sequence[float]] = None,
    model: str = MODEL_CONICAL,
    name: str = "",
) -> Illumination:
    """TLE + 显式时刻入口（skyfield 传播，位置与太阳方向自动导出）。

    ``at_unix`` 优先；否则用 ``time_engine.now_unix()``；再否则全局
    TimeEngine。TLE 解析/传播失败抛异常，绝不返回假状态。
    """
    ts, _source = _resolve_unix(at_unix, time_engine)
    from .skyfield_compat import _require_skyfield, make_timescale
    _require_skyfield()
    from skyfield.api import EarthSatellite  # local import：可选依赖

    timescale = make_timescale()
    sat = EarthSatellite(tle_line1, tle_line2, name or "SAT", timescale)
    t = timescale.utc(datetime.fromtimestamp(ts, tz=timezone.utc))
    pos = tuple(float(x) for x in sat.at(t).position.km)
    if sun_direction is None:
        sun_direction = sun_direction_eci(ts)
    return evaluate_illumination(pos, sun_direction, model=model, at_unix=ts)
