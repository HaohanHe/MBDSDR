# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/overlay_asterisms.py — 星官叠加数据层（T08）
====================================================

这一层只回答一个问题：

    「给定指向、视场、视口与时刻，画面上该画哪些线、每段裁到哪、名字标在哪？」

**它不画任何东西。** 输出是纯数据（天球坐标 + 裁剪后的屏幕折线），
渲染层拿到就能直接画。这样做的两个理由：

1. 可以在无窗口环境下逐段单测（裁剪、可见比例、跨视场判定都能算）；
2. 桌面 Qt 与手机 Flutter 共用同一份数据契约，不各写一套几何。

与既有实现的边界
----------------
- 星官数据取自 ``skyengine.xingguan``（项目已有 287 官/31 区域，不新建第二套）；
- 坐标链走 ``celestial_geometry`` 唯一入口（RA/Dec → 地平），不另造；
- **投影不自带**：调用方注入 ``project(az_array, alt_array) -> (x_array, y_array)``，
  通常是天空视图自己的 ``_sky_to_screen_many``。这一层不猜投影类型、
  不近似视场几何——投影只能有一个真源。

**本层不是桌面实时渲染路径。** 桌面唯一渲染实现是
``desktop/rf_sky_view.py`` 的 ``_draw_constellations``。本层的定位是
「共享数据契约 + 移动端对拍基准（``mobile/lib/astro/asterism_overlay.dart``、
``tests/test_mobile_asterism_overlay.py`` 以本层为基准）+ 未来解算路径的
参考实现」，不接入渲染热路径。依据（实测：800×800 视口、3° 视场、
31 区域 / 1244 颗成员星）：

- 成本：``build_overlay()`` 首次 288 ms、稳态约 170 ms/次（``_build_one``
  对每颗星单独调一次 ``j2000_to_altaz_many``）；实时层同款批量换算
  （``j2000_to_altaz_many`` 全表一次）约 0.05 ms；
- 范围：本层只支持 ``culture="chinese"``，没有 IAU 88 星座数据，接进主路径
  只能换掉一半，另一半仍在旧实现；
- 口径：本层用几何高度投影；实时层 ``_layer_points_full`` 先经
  ``_display_alt_deg`` 转视高度（含大气折射）再投影，近地平差可达约 0.5°
  （2.3° 目镜视场约 20% 画幅），接进去线就不落在星上。

一致性由 ``tests/test_asterism_overlay_drift.py``（漂移守卫）与
``tests/test_asterism_overlay_frame_budget.py``（每帧坐标换算次数上限）锁住。

为什么在屏幕空间裁剪（而不是照搬 Stellarium 的球面大圆裁剪）
------------------------------------------------------------
窄视场（2–8°，望远镜工况）下透视投影在画面上近似线性，屏幕空间直线裁剪与
球面裁剪的差别远小于一个像素。球面 ``clipGreatCircle`` 是为了解决大视场
（120°）下"弧被当成弦"和"跨越 180° 出鬼影"两件事——本项目的望远镜视场
不触发这两个问题。所以这里借它的**顺序**（先裁后画、裁到边界为止），
不借它的**球面细分**。这是"科学理性务实"的具体落点，不是偷懒。

诚实边界
--------
- 站址/时刻缺失 → ``drawable=False``，不返回任何折线（不画"大概位置"）；
- 光轴误差超限 → ``drawable=False``（线整体偏移比不画更糟，见 PRD §13.2）；
- 端点投影失败（NaN，例如落在投影背面）→ 该端按不可见处理，不硬凑坐标；
- 星官数据缺 HIP（三台/西咸/羽林军）→ ``data_complete=False``，如实上报。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .celestial_geometry import (
    GroundStation,
    azalt_from_vec_many,
    j2000_to_altaz_many,
    vec_from_radec,
)

# 裁剪状态（枚举字符串；渲染层按它决定要不要画、怎么画）
CLIP_BOTH_IN = "both_in"
CLIP_A_IN = "a_in"
CLIP_B_IN = "b_in"
CLIP_CROSSING = "crossing"
CLIP_BOTH_OUT = "both_out"          # 仅用于上报，不产出可画折线

CLIP_STATES = (CLIP_BOTH_IN, CLIP_A_IN, CLIP_B_IN, CLIP_CROSSING, CLIP_BOTH_OUT)

KIND_XINGGUAN = "xingguan"
KIND_REGION = "region"

ProjectFn = Callable[[Sequence[float], Sequence[float]],
                     Tuple[np.ndarray, np.ndarray]]
"""``project(az_deg_array, alt_deg_array) -> (x_px_array, y_px_array)``。

不可投影的点用 ``NaN`` 表示（不是 0）。必须是调用方视图自己的投影。
"""


# ---------------------------------------------------------------------------
# 视口与输出契约
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OverlayViewport:
    """画布事实：多大、什么视场、朝哪、转了多少。"""

    width_px: int
    height_px: int
    fov_deg: float
    center_az_deg: float
    center_alt_deg: float
    roll_deg: float = 0.0
    projection: str = ""
    """投影类型自由标签（``perspective`` / ``stereographic`` / ``fisheye`` …）。
    本层不解释它，只透传记录，便于审计。"""

    def __post_init__(self) -> None:
        object.__setattr__(self, "width_px", int(self.width_px or 0))
        object.__setattr__(self, "height_px", int(self.height_px or 0))
        object.__setattr__(self, "fov_deg", float(self.fov_deg))
        object.__setattr__(self, "center_az_deg", float(self.center_az_deg) % 360.0)
        object.__setattr__(self, "center_alt_deg", float(self.center_alt_deg))
        object.__setattr__(self, "roll_deg", float(self.roll_deg or 0.0))
        object.__setattr__(self, "projection", str(self.projection or ""))

    @property
    def usable(self) -> bool:
        return (self.width_px > 0 and self.height_px > 0
                and math.isfinite(self.fov_deg)
                and 0.0 < self.fov_deg < 180.0
                and math.isfinite(self.center_alt_deg))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width_px": self.width_px,
            "height_px": self.height_px,
            "fov_deg": self.fov_deg,
            "center_az_deg": self.center_az_deg,
            "center_alt_deg": self.center_alt_deg,
            "roll_deg": self.roll_deg,
            "projection": self.projection,
            "usable": self.usable,
        }


@dataclass(frozen=True)
class OverlaySegment:
    """一条已裁剪好、可直接画的屏幕线段。

    ``screen`` 是折线点列：``both_in`` 两个点；``a_in``/``b_in``/``crossing``
    也是两个点（一端被换成了画面边界上的交点）。保留点列而非两点，
    是为了将来若要加密采样时接口不变。
    """

    a_az_deg: float
    a_alt_deg: float
    b_az_deg: float
    b_alt_deg: float
    clip: str
    screen: Tuple[Tuple[float, float], ...]
    visible_frac: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "a": {"az_deg": self.a_az_deg, "alt_deg": self.a_alt_deg},
            "b": {"az_deg": self.b_az_deg, "alt_deg": self.b_alt_deg},
            "clip": self.clip,
            "screen": [{"x_px": x, "y_px": y} for x, y in self.screen],
            "visible_frac": self.visible_frac,
        }


@dataclass(frozen=True)
class OverlayAsterism:
    """一个可叠加对象（星官或垣/宿）。"""

    key: str
    name: str
    pronounce: str
    kind: str
    members_total: int
    members_in_view: int
    data_complete: bool
    label_anchor: Optional[Tuple[float, float]]
    """名字锚点 (x_px, y_px)；**没有可见段时为 None**（不渲染名字）。"""
    label_az_deg: Optional[float]
    label_alt_deg: Optional[float]
    segments: Tuple[OverlaySegment, ...]

    @property
    def visible(self) -> bool:
        return bool(self.segments)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "pronounce": self.pronounce,
            "kind": self.kind,
            "members_total": self.members_total,
            "members_in_view": self.members_in_view,
            "data_complete": self.data_complete,
            "label_anchor": ({"x_px": self.label_anchor[0],
                              "y_px": self.label_anchor[1]}
                             if self.label_anchor else None),
            "label_az_deg": self.label_az_deg,
            "label_alt_deg": self.label_alt_deg,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass
class OverlayResult:
    """一次叠加数据请求的结论。

    ``drawable=False`` 时 ``asterisms`` 必须为空——这是硬约束：不知道画在哪，
    就一条线都不给。宁可没有线，也不画错位的线。
    """

    drawable: bool = False
    refuse_reason: str = ""
    asterisms: List[OverlayAsterism] = field(default_factory=list)
    viewport: Optional[OverlayViewport] = None
    axis_error_deg: Optional[float] = None
    min_confident_axis_deg: Optional[float] = None
    total_segments: int = 0
    clipped_segments: int = 0
    culture: str = ""

    def __post_init__(self) -> None:
        if not self.drawable and self.asterisms:
            raise ValueError(
                "drawable=False 时不允许携带 asterisms：不知道画在哪就一条线都不给")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drawable": self.drawable,
            "refuse_reason": self.refuse_reason,
            "culture": self.culture,
            "axis_error_deg": self.axis_error_deg,
            "min_confident_axis_deg": self.min_confident_axis_deg,
            "total_segments": self.total_segments,
            "clipped_segments": self.clipped_segments,
            "viewport": self.viewport.to_dict() if self.viewport else None,
            "asterisms": [a.to_dict() for a in self.asterisms],
        }

    @property
    def visible_count(self) -> int:
        return sum(1 for a in self.asterisms if a.visible)


# ---------------------------------------------------------------------------
# 屏幕空间线段裁剪（Liang–Barsky）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Clipped:
    p0: Tuple[float, float]
    p1: Tuple[float, float]
    state: str
    frac: float


def clip_segment_to_rect(p0: Tuple[float, float], p1: Tuple[float, float],
                         width_px: float, height_px: float
                         ) -> Optional[_Clipped]:
    """把屏幕线段裁到 ``[0,w]×[0,h]`` 矩形内。

    返回 None 表示完全在画面外且不穿越（不该画）。
    ``frac`` 是裁剪后长度与原长之比（原长为 0 时记 1.0）。
    """
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None

    w = float(width_px)
    h = float(height_px)
    if w <= 0 or h <= 0:
        return None

    in0 = (0.0 <= x0 <= w) and (0.0 <= y0 <= h)
    in1 = (0.0 <= x1 <= w) and (0.0 <= y1 <= h)
    if in0 and in1:
        return _Clipped((x0, y0), (x1, y1), CLIP_BOTH_IN, 1.0)

    dx = x1 - x0
    dy = y1 - y0
    t_enter, t_exit = 0.0, 1.0
    for p, q in ((-dx, x0), (dx, w - x0), (-dy, y0), (dy, h - y0)):
        if p == 0.0:
            if q < 0.0:
                return None            # 平行且在外侧：整段不可见
            continue
        t = q / p
        if p < 0.0:
            if t > t_exit:
                return None
            if t > t_enter:
                t_enter = t
        else:
            if t < t_enter:
                return None
            if t < t_exit:
                t_exit = t
    if t_enter > t_exit:
        return None

    cx0 = x0 + t_enter * dx
    cy0 = y0 + t_enter * dy
    cx1 = x0 + t_exit * dx
    cy1 = y0 + t_exit * dy
    length = math.hypot(dx, dy)
    frac = 1.0 if length <= 1e-12 else max(0.0, t_exit - t_enter)

    if in0:
        state = CLIP_A_IN
    elif in1:
        state = CLIP_B_IN
    else:
        state = CLIP_CROSSING
    return _Clipped((cx0, cy0), (cx1, cy1), state, frac)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_overlay(
    *,
    site: Any,
    jd_ut1: float,
    viewport: OverlayViewport,
    project: ProjectFn,
    axis_error_deg: Optional[float] = None,
    min_confident_axis_deg: Optional[float] = None,
    culture: str = "chinese",
    sky_source: Any = None,
    include_regions: bool = True,
    min_visible_frac: float = 0.0,
) -> OverlayResult:
    """构造一次叠加数据（T08）。

    Args:
        site: 站址；``GroundStation`` 或带 ``to_ground_station()`` 的事实对象。
        jd_ut1: UT1 儒略日（真实时刻）。
        viewport: 画布事实。
        project: 注入的投影函数（视图自己的那个）。
        axis_error_deg: 光轴不确定度（来自解算的 ``FieldSolution``）；None 表示未解算。
        min_confident_axis_deg: 允许画线的误差上限。``axis_error_deg`` 超限即拒画。
            两个都给且超限 → ``drawable=False``。
        culture: 文化标签；目前只支持项目内置的中国星官（``chinese``）。
            其它值原样记录但不取数据（不假装有）。
        sky_source: 星官数据注入点（默认 ``get_xingguan()``），测试可替换。
        include_regions: 是否连垣/宿的墙线一起返回。
        min_visible_frac: 可见比例低于此值的段丢弃（默认 0，即只要沾边就画）。

    Returns:
        ``OverlayResult``；``drawable=False`` 时不含任何星官与折线。
    """
    result = OverlayResult(
        culture=str(culture or ""),
        viewport=viewport,
        axis_error_deg=axis_error_deg,
        min_confident_axis_deg=min_confident_axis_deg,
    )

    if not viewport.usable:
        result.refuse_reason = "视口不可用：尺寸或视场非法"
        return result

    station = _as_station(site)
    if station is None:
        result.refuse_reason = "站址不可用：缺纬度/经度/海拔"
        return result

    try:
        jd = float(jd_ut1)
    except (TypeError, ValueError):
        jd = float("nan")
    if not math.isfinite(jd):
        result.refuse_reason = "时刻不可用：jd_ut1 非有限值"
        return result

    if (min_confident_axis_deg is not None and axis_error_deg is not None
            and float(axis_error_deg) > float(min_confident_axis_deg)):
        result.refuse_reason = (
            f"光轴误差 {float(axis_error_deg):.3f}° 超过允许的 "
            f"{float(min_confident_axis_deg):.3f}°：线会整体偏移，不画")
        return result

    if str(culture or "chinese") != "chinese":
        result.refuse_reason = f"暂无 {culture!r} 文化的星官数据"
        return result

    data = sky_source if sky_source is not None else _default_source()
    if data is None:
        result.refuse_reason = "星官数据不可用"
        return result

    targets = _collect_targets(data, include_regions=include_regions)
    if not targets:
        result.refuse_reason = "星官数据为空"
        return result

    w = float(viewport.width_px)
    h = float(viewport.height_px)
    out: List[OverlayAsterism] = []
    total_seg = 0
    clipped = 0

    for key, name, pronounce, kind, polys, stars, complete in targets:
        asterism, n_seg, n_clip = _build_one(
            key=key, name=name, pronounce=pronounce, kind=kind,
            polys=polys, stars=stars, complete=complete,
            station=station, jd=jd, project=project,
            w=w, h=h, min_visible_frac=min_visible_frac,
        )
        total_seg += n_seg
        clipped += n_clip
        if asterism.segments:
            out.append(asterism)

    result.asterisms = out
    result.total_segments = total_seg
    result.clipped_segments = clipped
    result.drawable = bool(out)
    if not out:
        result.refuse_reason = "当前视场内没有可画的星官线段：一条线都不给"
    return result


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _as_station(site: Any) -> Optional[GroundStation]:
    if site is None:
        return None
    if isinstance(site, GroundStation):
        return site
    conv = getattr(site, "to_ground_station", None)
    if callable(conv):
        try:
            return conv()
        except Exception:  # pragma: no cover - 事实对象的转换失败按不可用处理
            return None
    try:
        return GroundStation(float(site.longitude), float(site.latitude),
                             float(getattr(site, "height_m", 0.0) or 0.0),
                             str(getattr(site, "source", "") or ""))
    except (AttributeError, TypeError, ValueError):
        return None


def _default_source():
    try:
        from .skyengine.xingguan import get_xingguan
    except Exception:  # pragma: no cover - 数据缺失按不可用处理
        return None
    try:
        return get_xingguan()
    except (FileNotFoundError, OSError, ValueError):  # pragma: no cover
        return None


def _collect_targets(data: Any, *, include_regions: bool
                     ) -> List[Tuple[str, str, str, str, list, list, bool]]:
    """把 XingguanSet 摊平成「可叠加对象」列表（星官 + 可选垣/宿）。"""
    out: List[Tuple[str, str, str, str, list, list, bool]] = []
    regions = getattr(data, "regions", ()) or ()
    for region in regions:
        rkey = str(getattr(region, "key", "") or getattr(region, "name_zh", ""))
        rname = str(getattr(region, "name_zh", "") or rkey)
        rpinyin = str(getattr(region, "pinyin", "") or "")
        rstars = list(getattr(region, "stars", ()) or ())
        if include_regions:
            rlines = list(getattr(region, "lines", ()) or ())
            if rlines:
                out.append((f"region:{rkey}", rname, rpinyin, KIND_REGION,
                            rlines, rstars,
                            not (getattr(region, "unresolved_tokens", ()) or ())))
        for xg in (getattr(region, "xingguan", ()) or ()):
            xname = str(getattr(xg, "name_zh", "") or "")
            if not xname:
                continue
            xlines = list(getattr(xg, "lines", ()) or ())
            xstars = list(getattr(xg, "stars", ()) or ())
            complete = not (getattr(xg, "unresolved_tokens", ()) or ())
            if complete and any(not getattr(s, "mapped", True) for s in xstars):
                complete = False
            out.append((f"xingguan:{xname}", xname,
                        str(getattr(xg, "pinyin", "") or ""), KIND_XINGGUAN,
                        xlines, xstars, complete))
    return out


def _stars_to_altaz(stars, station: GroundStation, jd: float
                    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """成员星 → (az, alt, valid_mask)。无坐标的星标 False，不参与。"""
    n = len(stars)
    az = np.full(n, np.nan)
    alt = np.full(n, np.nan)
    valid = np.zeros(n, dtype=bool)
    vecs: List[np.ndarray] = []
    idx: List[int] = []
    for i, s in enumerate(stars):
        ra = getattr(s, "ra_deg", None)
        dec = getattr(s, "dec_deg", None)
        if ra is None or dec is None:
            continue
        try:
            v = vec_from_radec(float(ra), float(dec))
        except (TypeError, ValueError):
            continue
        if not np.all(np.isfinite(v)):
            continue
        vecs.append(v)
        idx.append(i)
    if not vecs:
        return az, alt, valid
    mat = j2000_to_altaz_many(np.vstack(vecs), station, jd)
    a, e = azalt_from_vec_many(mat)
    for j, i in enumerate(idx):
        az[i] = float(a[j])
        alt[i] = float(e[j])
        valid[i] = math.isfinite(az[i]) and math.isfinite(alt[i])
    return az, alt, valid


def _project_safe(project: ProjectFn, az: np.ndarray, alt: np.ndarray
                  ) -> Tuple[np.ndarray, np.ndarray]:
    if az.size == 0:
        return np.zeros(0), np.zeros(0)
    try:
        x, y = project(az, alt)
    except Exception:  # pragma: no cover - 投影异常按不可投影处理（诚实）
        return np.full(az.size, np.nan), np.full(az.size, np.nan)
    x = np.asarray(x, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    if x.size != az.size or y.size != az.size:  # pragma: no cover - 契约违约
        return np.full(az.size, np.nan), np.full(az.size, np.nan)
    return x, y


def _build_one(*, key: str, name: str, pronounce: str, kind: str,
               polys: list, stars: list, complete: bool,
               station: GroundStation, jd: float, project: ProjectFn,
               w: float, h: float, min_visible_frac: float
               ) -> Tuple[OverlayAsterism, int, int]:
    # 1) 成员星 → 地平 → 屏幕（用于统计"画面内可见几颗"）
    saz, salt, svalid = _stars_to_altaz(stars, station, jd)
    sx, sy = _project_safe(project, saz[svalid], salt[svalid])
    in_view = 0
    if sx.size:
        ok = np.isfinite(sx) & np.isfinite(sy)
        in_view = int(np.count_nonzero(
            ok & (sx >= 0) & (sx <= w) & (sy >= 0) & (sy <= h)))

    # 2) 折线 → 地平 → 屏幕 → 逐段裁剪
    flat_az: List[float] = []
    flat_alt: List[float] = []
    seg_index: List[Tuple[int, int]] = []
    for poly in polys:
        base = len(flat_az)
        for s in poly:
            ra = getattr(s, "ra_deg", None)
            dec = getattr(s, "dec_deg", None)
            flat_az.append(float("nan"))
            flat_alt.append(float("nan"))
            if ra is None or dec is None:
                continue
            try:
                v = vec_from_radec(float(ra), float(dec))
            except (TypeError, ValueError):
                continue
            if not np.all(np.isfinite(v)):
                continue
            mat = j2000_to_altaz_many(v[None, :], station, jd)
            a, e = azalt_from_vec_many(mat)
            flat_az[-1] = float(a[0])
            flat_alt[-1] = float(e[0])
        for i in range(len(poly) - 1):
            seg_index.append((base + i, base + i + 1))

    total_seg = len(seg_index)
    segments: List[OverlaySegment] = []
    n_clipped = 0
    if seg_index:
        az_arr = np.asarray(flat_az, dtype=float)
        alt_arr = np.asarray(flat_alt, dtype=float)
        finite = np.isfinite(az_arr) & np.isfinite(alt_arr)
        px = np.full(az_arr.size, np.nan)
        py = np.full(az_arr.size, np.nan)
        if np.any(finite):
            fx, fy = _project_safe(project, az_arr[finite], alt_arr[finite])
            px[finite] = fx
            py[finite] = fy
        for ia, ib in seg_index:
            p0 = (float(px[ia]), float(py[ia]))
            p1 = (float(px[ib]), float(py[ib]))
            c = clip_segment_to_rect(p0, p1, w, h)
            if c is None:
                continue
            if c.state != CLIP_BOTH_IN:
                n_clipped += 1
            if c.frac < float(min_visible_frac):
                continue
            segments.append(OverlaySegment(
                a_az_deg=float(az_arr[ia]), a_alt_deg=float(alt_arr[ia]),
                b_az_deg=float(az_arr[ib]), b_alt_deg=float(alt_arr[ib]),
                clip=c.state, screen=(c.p0, c.p1),
                visible_frac=float(c.frac)))

    # 3) 名字锚点：最长可见段的中点（无可见段 → None，不标名字）
    anchor = None
    anchor_az = None
    anchor_alt = None
    if segments:
        best = max(segments, key=lambda s: math.hypot(
            s.screen[-1][0] - s.screen[0][0],
            s.screen[-1][1] - s.screen[0][1]))
        anchor = ((best.screen[0][0] + best.screen[-1][0]) / 2.0,
                  (best.screen[0][1] + best.screen[-1][1]) / 2.0)
        anchor_az = (best.a_az_deg + best.b_az_deg) / 2.0
        anchor_alt = (best.a_alt_deg + best.b_alt_deg) / 2.0

    return OverlayAsterism(
        key=key, name=name, pronounce=pronounce, kind=kind,
        members_total=len(stars), members_in_view=in_view,
        data_complete=bool(complete),
        label_anchor=anchor, label_az_deg=anchor_az, label_alt_deg=anchor_alt,
        segments=tuple(segments),
    ), total_seg, n_clipped
