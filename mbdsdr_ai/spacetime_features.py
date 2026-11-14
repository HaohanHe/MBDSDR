# SPDX-License-Identifier: MIT
"""轻量时空要素层：把地面对象当作**可查询的要素**，而不是临时画出来的多边形。

借的是 QGIS 时空管线的骨架，不是它的坐标栈：

- ``QgsTemporalController`` 只广播一个 ``QgsDateTimeRange``，行为由消费者
  自己的 ``temporalProperties().mode()`` 决定 —— 这里对应 :class:`TemporalMode`
  与 :class:`TemporalContext` 的单一广播量。
- ``QgsDataProviderTemporalCapabilities`` 声明「数据有什么时间」，
  图层声明「想怎么用它」—— 这里对应 :class:`SpatialFeature` 的
  :class:`TimeInterval`（事实）与查询时传入的 ``mode``（意图）。
- ``QgsTemporalUtils::usedTemporalRangesForProject`` + ``mergeRanges``
  合并真实区间且保留空洞 —— 这里对应 :func:`merge_ranges` / :func:`gaps_between`。
- ``RedrawLayerOnly``：随时间重绘但**不过滤要素** —— 这里对应
  ``TemporalMode.REDRAW_ONLY``，天空/全天球必须定这一档，星不会被时间筛掉。

设计红线：

- 零新依赖，只用球面原语；不引入 PROJ / WKT / proj.db，不做自绘底图。
- ``None`` 端点表示 ±∞，用「无效值」表达无穷，**不伪造任何预览时刻**。
- 没有时间数据就返回 ``unknown`` 并给出原因，绝不推断一个看似合理的范围。
- 本模块不内置任何站点、信标或干扰源；要素一律由调用方从真实来源注入。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "TemporalMode", "TimeInterval", "SpatialFeature", "Footprint",
    "FeatureState", "QueryResult", "FeatureCollection", "TemporalContext",
    "TemporalBus", "get_temporal_bus", "great_circle_angle_deg",
    "merge_ranges", "gaps_between", "match_closest_before",
    "get_ground_features", "upsert_ground_station",
]


class TemporalMode(str, Enum):
    """数据消费时间的方式（QGIS Vector/RasterTemporalMode 的子集）。

    ``REDRAW_ONLY`` 是全天球/星象仪的档位：时间只改变投影/姿态，
    **绝不产生任何要素过滤**。
    """

    NONE = "none"                    # 该数据没有时间维（如 OSM 底图）
    FIXED_RANGE = "fixed_range"      # 固定有效期（TLE 有效期）
    RANGE_FILTER = "range_filter"    # 按起止区间筛要素（过境窗口、地面对象）
    TIME_STEPPED = "time_stepped"    # 离散时间步（频谱瀑布、IQ 回放）
    REDRAW_ONLY = "redraw_only"      # 只重绘不过滤（全天球）


@dataclass(frozen=True)
class TimeInterval:
    """半开闭可控的时间区间；``None`` 端点表示 ±∞。

    语义按 ``QgsTemporalRange<T>`` 实读结果：两端都无效是 infinite（不空）、
    单端有效是半无限（不空）、``lower > upper`` 才空、单点只在**两端都排除**
    时为空。用 ``None`` 而不是「无效 QDateTime」表达无穷，是因为项目里
    过境时刻是 Unix 秒 / skyfield Time，没有「无效值」概念。
    """

    start: Optional[float] = None
    end: Optional[float] = None
    include_start: bool = True
    include_end: bool = True

    @property
    def is_infinite(self) -> bool:
        return self.start is None and self.end is None

    @property
    def is_instant(self) -> bool:
        return (self.start is not None and self.end is not None
                and self.start == self.end
                and (self.include_start or self.include_end))

    @property
    def is_empty(self) -> bool:
        if self.start is None or self.end is None:
            return False
        if self.start > self.end:
            return True
        if self.start == self.end:
            # QGIS：单点只要**至少一端包含**就是合法 instant；
            # 只有两端都排除才空。写成 and 会把 [t, t] / (t, t] 误判为空。
            return not (self.include_start or self.include_end)
        return False

    def contains(self, t: float) -> bool:
        """判断时刻是否落在区间内（边界开闭参与判定）。"""
        if self.is_empty:
            return False
        if self.start is not None:
            if t < self.start or (t == self.start and not self.include_start):
                return False
        if self.end is not None:
            if t > self.end or (t == self.end and not self.include_end):
                return False
        return True

    def describe(self) -> str:
        if self.is_empty:
            return "空区间"
        if self.is_infinite:
            return "全程有效"
        if self.start is None:
            return f"至 {self.end:g} 之前"
        if self.end is None:
            return f"自 {self.start:g} 起"
        return f"{self.start:g} – {self.end:g}"


#: 默认「全程有效」是**显式缺省**，不是伪造的时间段。
ANY_TIME = TimeInterval()


@dataclass(frozen=True)
class SpatialFeature:
    """一个地面对象：几何 + 属性 + 有效时间区间。

    ``track`` 是可选的 (lat, lon) 历史点串，对应 QGIS 里逐时间步的
    LineString；没有轨迹时为空元组。不内置任何默认坐标。
    """

    feature_id: str
    kind: str                       # ground_station / beacon / rfi / adsb / aprs ...
    lat: float
    lon: float
    label: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    valid: TimeInterval = ANY_TIME
    track: Tuple[Tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.feature_id:
            raise ValueError("feature_id 不能为空")
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"纬度越界: {self.lat}")
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"经度越界: {self.lon}")


def great_circle_angle_deg(lat1: float, lon1: float,
                           lat2: float, lon2: float) -> float:
    """两点间球面中心角（度）。haversine，短弧，跨 ±180° 经线安全。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(((lon2 - lon1 + 180.0) % 360.0) - 180.0)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(max(0.0, a)))))


@dataclass(frozen=True)
class Footprint:
    """卫星覆盖圈：星下点 + 中心角（度）。

    用「中心角 ≤ λ」做球面判定，而不是多边形 point-in-polygon —— 后者在
    跨日界线切段后会漏判，且 ``ground_track.footprint_ring`` 本来就是按
    ``destination_point`` 逐点落在同一中心角上生成的。
    """

    lat_deg: float
    lon_deg: float
    central_angle_deg: float
    at_unix: Optional[float] = None

    def contains(self, lat: float, lon: float) -> bool:
        return (great_circle_angle_deg(self.lat_deg, self.lon_deg, lat, lon)
                <= self.central_angle_deg + 1e-9)

    def angle_to(self, lat: float, lon: float) -> float:
        return great_circle_angle_deg(self.lat_deg, self.lon_deg, lat, lon)


@dataclass(frozen=True)
class FeatureState:
    """一个要素在给定时刻的查询结果。``state`` 一律显式，不折叠成布尔。"""

    feature: SpatialFeature
    state: str                      # inside/outside/expired/not_yet/unknown/shown
    angle_deg: Optional[float] = None
    reason: str = ""

    @property
    def is_highlighted(self) -> bool:
        return self.state in ("inside", "shown")


@dataclass(frozen=True)
class QueryResult:
    """一次时空联合查询的完整结果（可序列化、可直接喂给地图图层）。"""

    at_unix: float
    mode: TemporalMode
    items: Tuple[FeatureState, ...] = ()
    footprint: Optional[Footprint] = None
    basis: str = "unknown"          # real / simulated / replay / unknown
    has_time_data: bool = False
    gaps: Tuple[Tuple[float, float], ...] = ()
    unknown_reason: str = ""

    @property
    def inside(self) -> Tuple[FeatureState, ...]:
        return tuple(i for i in self.items if i.state == "inside")

    @property
    def unknown(self) -> Tuple[FeatureState, ...]:
        return tuple(i for i in self.items if i.state == "unknown")

    @property
    def highlighted(self) -> Tuple[FeatureState, ...]:
        return tuple(i for i in self.items if i.is_highlighted)

    @property
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for item in self.items:
            out[item.state] = out.get(item.state, 0) + 1
        return out

    @property
    def footprint_age_s(self) -> Optional[float]:
        """当前时刻与覆盖圈计算时刻之差。没有圈就返回 None（不猜）。"""
        if self.footprint is None or self.footprint.at_unix is None:
            return None
        return self.at_unix - self.footprint.at_unix

    def summary(self) -> str:
        if self.unknown_reason:
            return self.unknown_reason
        if not self.items:
            return "未接入地面对象 · 不推断"
        c = self.counts
        return (f"{len(self.items)} 个要素 · {c.get('inside', 0)} 在圈内"
                f" · {c.get('unknown', 0)} 未知")


def merge_ranges(ranges: Iterable[Tuple[float, float]]
                 ) -> List[Tuple[float, float]]:
    """合并重叠区间为有序表（QGIS ``mergeRanges`` 思路）。

    只合并**真实存在**的区间；区间之间的空洞原样保留，绝不填平。
    """
    src = sorted((float(a), float(b)) for a, b in ranges if a <= b)
    out: List[Tuple[float, float]] = []
    for start, end in src:
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def gaps_between(ranges: Sequence[Tuple[float, float]]
                 ) -> Tuple[Tuple[float, float], ...]:
    """返回合并后区间表里的空洞段。少于两段时没有空洞。"""
    merged = merge_ranges(ranges)
    return tuple((merged[i][1], merged[i + 1][0])
                 for i in range(len(merged) - 1)
                 if merged[i][1] < merged[i + 1][0])


def match_closest_before(t: float, samples: Iterable[float]) -> Optional[float]:
    """取 ``<= t`` 的最近已有样本（QGIS FindClosestMatchToStartOfRange）。

    没有就返回 ``None``：**不插值、不造帧、不借用未来样本**。
    """
    candidates = [float(s) for s in samples if float(s) <= t]
    return max(candidates) if candidates else None


class FeatureCollection:
    """地面对象容器。只持有从真实来源注入的要素，不内置任何默认站点。"""

    def __init__(self, features: Iterable[SpatialFeature] = ()) -> None:
        self._items: Dict[str, SpatialFeature] = {}
        for f in features:
            self.upsert(f)

    def upsert(self, feature: SpatialFeature) -> None:
        self._items[feature.feature_id] = feature

    def remove(self, feature_id: str) -> None:
        self._items.pop(feature_id, None)

    def all(self) -> Tuple[SpatialFeature, ...]:
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def available_ranges(self) -> List[Tuple[float, float]]:
        """所有要素有限有效期的合并区间表；无限期要素不产生区间。"""
        return merge_ranges(
            (f.valid.start, f.valid.end) for f in self._items.values()
            if f.valid.start is not None and f.valid.end is not None
            and not f.valid.is_empty)

    def query_at(self, t: float,
                 footprint: Optional[Footprint] = None,
                 mode: TemporalMode = TemporalMode.RANGE_FILTER,
                 *, basis: str = "unknown",
                 has_time_data: bool = True) -> QueryResult:
        """时空联合查询：给定时刻 + 覆盖圈 → 每个要素的显式状态。

        ``footprint is None`` 或 ``has_time_data=False`` 时一律返回
        ``unknown`` 状态并写明原因，不猜测范围、不造预览时刻。
        """
        items: List[FeatureState] = []
        unknown_reason = ""
        if not has_time_data:
            unknown_reason = "未设定时间范围 · 不推断"
        elif footprint is None and mode in (TemporalMode.RANGE_FILTER,
                                            TemporalMode.REDRAW_ONLY):
            unknown_reason = "未获得覆盖圈 · 不推断"

        for f in self._items.values():
            if mode == TemporalMode.NONE:
                items.append(FeatureState(f, "shown", None, "无时间维"))
                continue
            if mode == TemporalMode.REDRAW_ONLY:
                # 天空档：全集恒显，绝不按时间或空间筛掉任何要素。
                items.append(FeatureState(
                    f, "shown", None,
                    "redraw_only：时间只改变姿态，不筛要素"))
                continue
            if not has_time_data or (
                    footprint is None and mode == TemporalMode.RANGE_FILTER):
                items.append(FeatureState(f, "unknown", None, unknown_reason))
                continue
            if not f.valid.contains(t):
                state = ("not_yet" if f.valid.start is not None
                         and t < f.valid.start else "expired")
                items.append(FeatureState(
                    f, state, None,
                    f"有效期外：{f.valid.describe()}"))
                continue
            if footprint is None:
                items.append(FeatureState(f, "shown", None, "无覆盖圈约束"))
                continue
            angle = footprint.angle_to(f.lat, f.lon)
            inside = footprint.contains(f.lat, f.lon)
            items.append(FeatureState(
                f, "inside" if inside else "outside", angle,
                ("中心角 {:.2f}° ≤ {:.2f}°".format(angle, footprint.central_angle_deg)
                 if inside else
                 "中心角 {:.2f}° > {:.2f}°".format(angle, footprint.central_angle_deg))))

        return QueryResult(
            at_unix=float(t), mode=mode, items=tuple(items),
            footprint=footprint, basis=basis,
            has_time_data=has_time_data,
            gaps=gaps_between(self.available_ranges()),
            unknown_reason=unknown_reason)


@dataclass(frozen=True)
class TemporalContext:
    """唯一广播量：当前时刻 + 可选帧区间 + 时间基准 + 消费语义。"""

    at_unix: float
    interval: TimeInterval = ANY_TIME
    basis: str = "unknown"          # real / simulated / replay / unknown
    mode: TemporalMode = TemporalMode.RANGE_FILTER
    has_time_data: bool = False
    step_index: Optional[int] = None

    @classmethod
    def unknown(cls) -> "TemporalContext":
        """诚实空态：没有时间数据。不填 24h、不取「好看的时刻」。"""
        return cls(at_unix=0.0, interval=TimeInterval(),
                   basis="unknown", mode=TemporalMode.NONE,
                   has_time_data=False)

    @property
    def is_simulated(self) -> bool:
        return self.basis in ("simulated", "replay")


class TemporalBus:
    """单点产出、多处订阅的时间广播（对应 QgsTemporalController 的单信号）。

    ``publish`` 做值去重：同一 ``TemporalContext`` 不重播，避免 30 fps
    重绘里反复触发全量查询。
    """

    def __init__(self) -> None:
        self._subscribers: List[Callable[[TemporalContext], None]] = []
        self._last: Optional[TemporalContext] = None

    @property
    def last(self) -> Optional[TemporalContext]:
        return self._last

    def subscribe(self, cb: Callable[[TemporalContext], None]) -> Callable[[], None]:
        self._subscribers.append(cb)
        if self._last is not None:
            try:
                cb(self._last)
            except Exception:
                pass

        def unsubscribe() -> None:
            if cb in self._subscribers:
                self._subscribers.remove(cb)
        return unsubscribe

    def publish(self, ctx: TemporalContext) -> bool:
        if self._last == ctx:
            return False
        self._last = ctx
        for cb in list(self._subscribers):
            try:
                cb(ctx)
            except Exception:
                # 一个消费者出错不该让广播链断掉，也不该静默吞掉主流程。
                pass
        return True

    def publish_from_engine(self, engine: Any, *,
                            mode: TemporalMode = TemporalMode.RANGE_FILTER
                            ) -> TemporalContext:
        """从 TimeEngine 单点取时刻，产出广播量。

        ``following_system`` 决定 basis：显式跳时就标 simulated，
        不把模拟态说成实时。引擎不可用时返回 :meth:`TemporalContext.unknown`。
        """
        try:
            t = float(engine.now_unix())
            following = bool(engine.is_following_system())
        except Exception:
            ctx = TemporalContext.unknown()
            self.publish(ctx)
            return ctx
        ctx = TemporalContext(
            at_unix=t,
            interval=TimeInterval(t, t),
            basis="real" if following else "simulated",
            mode=mode,
            has_time_data=True)
        self.publish(ctx)
        return ctx


_global_bus: Optional[TemporalBus] = None


def get_temporal_bus() -> TemporalBus:
    """进程级单例（与 get_time_engine 同一约定）。"""
    global _global_bus
    if _global_bus is None:
        _global_bus = TemporalBus()
    return _global_bus


# ---------------------------------------------------------------------------
# 地面对象集合：进程级单例，让「站址唯一写入口」与「地图图层」解耦。
# 地图面板是懒创建的，站址可能先于地图设置好；用同一集合即可免去时序依赖。
# ---------------------------------------------------------------------------
_global_collection: Optional[FeatureCollection] = None
_STATION_ID = "ground_station:current"


def get_ground_features() -> FeatureCollection:
    global _global_collection
    if _global_collection is None:
        _global_collection = FeatureCollection()
    return _global_collection


def upsert_ground_station(lat: Optional[float], lon: Optional[float], *,
                          source: str = "",
                          collection: Optional[FeatureCollection] = None
                          ) -> Optional[SpatialFeature]:
    """把主窗站址写成一个真实要素；``None`` 表示未定位，直接移除，不造坐标。

    站址唯一写入口是 ``main_window.set_observer_site``；这里不读配置、
    不硬编码城市、不猜测位置。
    """
    col = collection or get_ground_features()
    if lat is None or lon is None:
        col.remove(_STATION_ID)
        return None
    feature = SpatialFeature(
        feature_id=_STATION_ID,
        kind="ground_station",
        lat=float(lat), lon=float(lon),
        label="本站",
        properties={"source": source} if source else {},
        valid=ANY_TIME)
    col.upsert(feature)
    return feature
