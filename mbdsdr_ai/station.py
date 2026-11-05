# SPDX-License-Identifier: MIT
"""站址坐标来源与可信度（P11-2）。

红线：不许拿缺省城市坐标（如「长春」）冒充用户位置。站址来源必须显式：

    GNSS（实时定位，精度可带） > 用户手填/配置（user/config） > 缺省（default）

``default``、缺失坐标（真没配）一律判「站址不可信」，调用方必须拒绝计算。
``(0,0)`` 与「明显海上位置」**不是**未配置哨兵：显式来源（gnss/user/config）
在只读/计算口径（:func:`trust_for_calculation`）下照常计算并留 warning；
写/控制口径（:func:`trust_for_tx`）仍严格拒绝。`(0,0)` 坐标合法性与海域
启发式分别留痕，不混成一句。

海陆判定是**低分辨率粗略多边形**，只用于提示「明显海上坐标」（如太平洋、
大西洋、几内亚湾）；它不替代 GIS 海岸线，结论字段标 ``coarse``。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "StationFix",
    "SOURCE_PRIORITY",
    "evaluate_station",
    "trust_for_calculation",
    "trust_for_tx",
    "resolve_station",
    "station_from_gnss",
    "station_from_user",
    "station_from_config",
    "coarse_land_status",
    "pass_prediction_allowed",
]

# 来源优先级（数字越小越可信）。default 仅用于明确标注的兜底，不参与过境。
SOURCE_PRIORITY: Dict[str, int] = {
    "gnss": 0,
    "user": 1,
    "config": 1,
    "default": 2,
    "none": 3,
    "unknown": 3,
}

# 粗略大陆包围盒 (lat_min, lat_max, lon_min, lon_max)。
_LAND_BOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (-90.0, -60.0, -180.0, 180.0), # 南极洲
    (60.0, 84.0, -73.0, -12.0),    # 格陵兰
    (15.0, 72.0, -168.0, -52.0),   # 北美
    (-56.0, 12.5, -82.0, -34.0),   # 南美
    (36.0, 72.0, -10.0, 40.0),     # 欧洲
    (-35.0, 37.5, -18.0, 52.0),    # 非洲
    (5.0, 78.0, 52.0, 145.0),      # 亚洲主体
    (-11.0, 23.0, 95.0, 141.0),    # 东南亚/印尼
    (-45.0, -10.0, 112.0, 154.0),  # 澳洲
    (-47.0, -34.0, 166.0, 179.0),  # 新西兰
    (-26.0, -12.0, 43.0, 51.0),    # 马达加斯加
    (18.0, 22.0, -161.0, -154.0),  # 夏威夷
    (50.0, 61.0, -8.0, 2.0),       # 不列颠
    (30.0, 46.0, 129.0, 146.0),    # 日本列岛
    (21.0, 26.0, 119.0, 123.0),    # 台湾
    (18.0, 21.0, 108.0, 111.0),    # 海南
)

# 近岸海域排除盒（命中即判海，优先于大陆盒）。
_SEA_BOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (5.0, 23.0, 80.0, 95.0),       # 孟加拉湾
    (5.0, 25.0, 55.0, 75.0),       # 阿拉伯海
    (30.0, 46.0, -6.0, 36.0),      # 地中海
    (12.0, 30.0, 32.0, 44.0),      # 红海
    (24.0, 31.0, 48.0, 57.0),      # 波斯湾
    (18.0, 31.0, -98.0, -80.0),    # 墨西哥湾
    (9.0, 23.0, -88.0, -60.0),     # 加勒比
    (53.0, 66.0, 9.0, 30.0),       # 波罗的海
    (51.0, 62.0, -4.0, 9.0),       # 北海
    (40.0, 47.0, 27.0, 42.0),      # 黑海
    (36.0, 47.0, 46.0, 55.0),      # 里海
    (38.0, 47.0, 128.0, 142.0),    # 日本海
    (32.0, 41.0, 119.0, 127.0),    # 黄海
    (24.0, 33.0, 120.0, 128.0),    # 东海
    (3.0, 23.0, 105.0, 122.0),     # 南海
    (8.0, 20.0, 92.0, 99.0),       # 安达曼海
    (-5.0, 8.0, -10.0, 15.0),      # 几内亚湾
)

# 0,0 判定的容差（度）。注意：0,0 是几内亚湾的真实坐标，只对**非显式来源**
# 当占位值；gnss/user/config 的显式输入按用户声明处理（见 _evaluate）。
_NULL_ISLAND_TOL_DEG = 0.01

# 显式来源：用户/GNSS/配置明确声明的位置，启发式不得直接否掉。
_EXPLICIT_SOURCES = ("gnss", "user", "config")

# 精度元数据阈值：超过 50 km 的定位不应用于过境对准。
_MAX_USABLE_ACCURACY_M = 50000.0


@dataclass
class StationFix:
    """一次站址定位结果（含来源与精度元数据）。

    ``source``: ``gnss`` / ``user`` / ``config`` / ``default`` / ``none``。
    ``accuracy_m``: 定位精度（米），可缺省；缺省表示「来源未提供」。
    """

    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_m: float = 0.0
    source: str = "none"
    accuracy_m: Optional[float] = None
    origin: str = ""
    captured_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lat": self.lat, "lon": self.lon, "alt_m": self.alt_m,
            "source": self.source, "accuracy_m": self.accuracy_m,
            "origin": self.origin, "captured_at": self.captured_at,
        }


def _coerce(value: Any, source: Optional[str] = None) -> StationFix:
    """把 dict / tuple / StationFix 统一成 StationFix。"""
    if isinstance(value, StationFix):
        fix = value
        if source and fix.source in ("none", "unknown", ""):
            fix = StationFix(fix.lat, fix.lon, fix.alt_m, source,
                             fix.accuracy_m, fix.origin, fix.captured_at)
        return fix
    if isinstance(value, dict):
        return StationFix(
            lat=value.get("lat", value.get("latitude")),
            lon=value.get("lon", value.get("longitude")),
            alt_m=float(value.get("alt_m", value.get("altitude_m", 0.0)) or 0.0),
            source=str(value.get("source", source or "unknown")),
            accuracy_m=value.get("accuracy_m"),
            origin=str(value.get("origin", "")),
            captured_at=value.get("captured_at"),
        )
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return StationFix(lat=value[0], lon=value[1],
                          alt_m=float(value[2]) if len(value) > 2 else 0.0,
                          source=source or "unknown")
    return StationFix(source=source or "none")


def station_from_gnss(lat: float, lon: float, alt_m: float = 0.0,
                      accuracy_m: Optional[float] = None,
                      origin: str = "gnss") -> StationFix:
    """GNSS 定位结果 → 站址（最高优先级）。"""
    return StationFix(float(lat), float(lon), float(alt_m), "gnss",
                      accuracy_m, origin)


def station_from_user(lat: float, lon: float, alt_m: float = 0.0,
                      accuracy_m: Optional[float] = None,
                      origin: str = "manual") -> StationFix:
    """用户手填坐标 → 站址（次优先级）。"""
    return StationFix(float(lat), float(lon), float(alt_m), "user",
                      accuracy_m, origin)


def station_from_config(path: Optional[str] = None) -> StationFix:
    """从 ``~/.mbdsdr/config.json`` 读取站址（source=config）。

    未配置/字段缺失返回 ``StationFix(source="none")``，绝不回退到任何城市。
    可选的 ``ground_station_accuracy_m`` 作为精度元数据。
    """
    cfg_path = path or os.path.expanduser("~/.mbdsdr/config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError, TypeError):
        return StationFix(source="none", origin=cfg_path)
    lat = cfg.get("ground_station_lat")
    lon = cfg.get("ground_station_lon")
    if lat is None or lon is None:
        return StationFix(source="none", origin=cfg_path)
    try:
        return StationFix(
            lat=float(lat), lon=float(lon),
            alt_m=float(cfg.get("ground_station_alt_m", 0.0) or 0.0),
            source="config", accuracy_m=cfg.get("ground_station_accuracy_m"),
            origin=cfg_path,
        )
    except (TypeError, ValueError):
        return StationFix(source="none", origin=cfg_path)


def resolve_station(gnss: Any = None, user: Any = None,
                    config: Any = None, default: Any = None) -> StationFix:
    """按 GNSS > 用户手填 > 配置 > 缺省 的优先级解析站址并留痕。

    返回的 ``StationFix`` 总是带 ``source``；没有任何输入时 source="none"，
    绝不生成内置城市坐标。
    """
    ordered: Sequence[Tuple[str, Any]] = (
        ("gnss", gnss), ("user", user), ("config", config), ("default", default),
    )
    for src, val in ordered:
        if val is None:
            continue
        fix = _coerce(val, src)
        if fix.lat is not None and fix.lon is not None:
            if not fix.source or fix.source in ("unknown", "none"):
                fix = StationFix(fix.lat, fix.lon, fix.alt_m, src,
                                 fix.accuracy_m, fix.origin, fix.captured_at)
            return fix
    return StationFix(source="none")


def coarse_land_status(lat: float, lon: float) -> Dict[str, Any]:
    """粗略海陆判定。

    返回 ``{"status": "land"|"sea"|"unknown", "box": str, "coarse": True}``。
    先查近岸海域排除盒，再查大陆/岛屿包围盒；都不命中判 ``sea``。
    """
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return {"status": "unknown", "box": "", "coarse": True}
    for i, (la0, la1, lo0, lo1) in enumerate(_SEA_BOXES):
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return {"status": "sea", "box": f"sea_box_{i}", "coarse": True}
    for i, (la0, la1, lo0, lo1) in enumerate(_LAND_BOXES):
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return {"status": "land", "box": f"land_box_{i}", "coarse": True}
    return {"status": "sea", "box": "open_ocean", "coarse": True}


def _evaluate(fix: Any, require_accuracy: bool = False,
              allow_explicit_sea: bool = False) -> Dict[str, Any]:
    """站址可信度评估的**唯一**判定实现。

    两个公开入口（:func:`trust_for_calculation` / :func:`trust_for_tx`）只是
    改变这里的 ``allow_explicit_sea``，不各自复制一套判定。

    ``allow_explicit_sea=True``（只读/计算口径）：显式来源
    （``gnss``/``user``/``config``）的 ``(0,0)`` 或落在粗略海域的坐标**降为
    warning 并照常计算**——用户显式声明的位置是声明，不该被启发式否掉。
    ``allow_explicit_sea=False``（写/控制口径）：保持严格拒绝。

    两类问题分别留痕，不混成一句：
      * ``null_island_0_0``：坐标合法性/占位值问题；
      * ``at_sea_explicit``：位置启发式可信度问题。

    返回 ``{"trusted", "reasons", "warnings", "station_warning", "land",
    "source", "fix", "priority"}``。
    """
    st = _coerce(fix)
    reasons: List[str] = []
    warnings: List[str] = []
    location_warnings: List[str] = []
    source = str(st.source or "none")
    lat, lon = st.lat, st.lon
    land = {"status": "unknown", "box": "", "coarse": True}
    explicit = source in _EXPLICIT_SOURCES

    if lat is None or lon is None:
        reasons.append("missing_coordinates（未提供经纬度）")
    else:
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            reasons.append("non_numeric_coordinates（经纬度不是数值）")
            lat = lon = None
        else:
            if not (math.isfinite(lat) and math.isfinite(lon)):
                reasons.append("non_finite_coordinates（经纬度含 NaN/Inf）")
            elif not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                reasons.append(f"out_of_range（{lat:.3f}, {lon:.3f} 超出经纬度范围）")
            else:
                land = coarse_land_status(lat, lon)
                if abs(lat) <= _NULL_ISLAND_TOL_DEG and abs(lon) <= _NULL_ISLAND_TOL_DEG:
                    # (0,0) 是真实坐标，不是越界哨兵。显式来源放行、留 warning。
                    if allow_explicit_sea and explicit:
                        msg = ("null_island_0_0（显式输入坐标恰为 0,0（几内亚湾）；"
                               "照常计算，但位置可疑）")
                        warnings.append(msg)
                        location_warnings.append(msg)
                    else:
                        reasons.append("null_island_0_0（疑似未设置的占位坐标）")
                elif land["status"] == "sea":
                    if allow_explicit_sea and explicit:
                        msg = (f"at_sea_explicit（显式输入落在粗略海域：{land['box']}；"
                               "照常计算，但位置可疑）")
                        warnings.append(msg)
                        location_warnings.append(msg)
                    else:
                        reasons.append(
                            f"at_sea（粗略海陆判定为海上：{land['box']}；"
                            "站址不可信，拒绝算过境）")

    if source in ("none", "unknown", ""):
        reasons.append(f"source_missing（来源={source or '空'}，无凭据）")
    elif source == "default":
        reasons.append("default_coordinates（缺省兜底坐标不得冒充用户位置）")

    if st.accuracy_m is not None:
        try:
            acc = float(st.accuracy_m)
        except (TypeError, ValueError):
            reasons.append("accuracy_invalid（精度元数据非数值）")
        else:
            if acc <= 0:
                reasons.append("accuracy_invalid（精度 <= 0）")
            elif acc > _MAX_USABLE_ACCURACY_M:
                reasons.append(f"accuracy_too_coarse（±{acc:.0f} m > 50 km）")

    if source in ("user", "config") and st.accuracy_m is None:
        warnings.append("accuracy_unprovided（手填/配置坐标未带定位精度元数据）")
    if require_accuracy and st.accuracy_m is None:
        reasons.append("accuracy_required_missing（本功能要求精度元数据）")

    trusted = not reasons
    return {
        "trusted": trusted,
        "reasons": reasons,
        "warnings": warnings,
        # 只汇总「坐标被启发式怀疑」的 warning，供调用方原样透传给用户。
        "station_warning": "；".join(location_warnings),
        "land": land,
        "source": source,
        "fix": st.to_dict(),
        "priority": SOURCE_PRIORITY.get(source, 3),
    }


def trust_for_calculation(fix: Any, require_accuracy: bool = False) -> Dict[str, Any]:
    """只读/计算口径：显式来源的 (0,0)/海上坐标降为 warning 并照常计算。

    用于过境预测、可见性、天文、纯展示；不得用于 TX 授权/指向/发射安全。
    """
    return _evaluate(fix, require_accuracy=require_accuracy,
                     allow_explicit_sea=True)


def trust_for_tx(fix: Any, require_accuracy: bool = False) -> Dict[str, Any]:
    """写/控制口径：保持严格（显式 (0,0)/海上坐标仍然拒绝）。"""
    return _evaluate(fix, require_accuracy=require_accuracy,
                     allow_explicit_sea=False)


def evaluate_station(fix: Any, require_accuracy: bool = False) -> Dict[str, Any]:
    """严格（写/控制）口径的兼容入口；语义等同 :func:`trust_for_tx`。

    新代码请显式选 :func:`trust_for_calculation` 或 :func:`trust_for_tx`；
    保留本名只为不改变既有调用点的对外语义。
    """
    return _evaluate(fix, require_accuracy=require_accuracy,
                     allow_explicit_sea=False)


def pass_prediction_allowed(fix: Any, require_accuracy: bool = False) -> bool:
    """过境预测是否可用该站址（只读口径：显式 (0,0)/海上坐标也放行）。"""
    return bool(trust_for_calculation(
        fix, require_accuracy=require_accuracy)["trusted"])
