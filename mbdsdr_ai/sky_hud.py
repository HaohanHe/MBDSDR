# SPDX-License-Identifier: MIT
"""AR 指向 HUD 的纯数据绘制指令（不 import Qt）。

输入是融合位姿、识别目标、链路级别、当前视图参数与画布尺寸；输出是一份
绘制层可直接消费的指令字典——准星、标签、星官线段、卫星标记与人话提示。

级别语义与 ``sky_health.health_snapshot`` 同一套（L0–L4）：

* ``L0``：未知——只有准星与「还没对准」；
* ``L1``：单源粗略——只有准星与「粗略对准，可能偏几度」；
* ``L2``：多源融合——可画粗标签（只给名字）；
* ``L3``：已解算——精确标签（名字 + 角距 + 星等）；
* ``L4``：手动接管——加「手动」标记，视图以 ``view_params`` 为准，
  不再被位姿覆盖。

诚实边界
--------
* 位姿不可用（缺字段/NaN/alt 越界/``valid=False``）时不会拿视图中
  心冒充指向：级别降为 L0，提示「还没对准」，且不画任何目标；
* 目标缺 az/alt（或屏幕坐标）就跳过，不猜位置、不画假目标；
* 星官线段只做投影与可见性过滤，不在这里生成新连线；
* 本模块不读时钟、不碰设备、不写盘，只做确定性换算。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .celestial_geometry import AzimuthalEquidistantProjection
from .sky_interaction import (
    ViewState,
    angular_distance_deg,
    sky_to_screen_many,
)

__all__ = ["HUD_HINTS", "build_hud"]

#: 级别 → 默认界面提示（不出现 az/alt/FOV/vmag 等工程术语）。
HUD_HINTS: Dict[str, str] = {
    "L0": "还没对准",
    "L1": "粗略对准，可能偏几度",
    "L2": "已融合",
    "L3": "已锁定",
    "L4": "手动",
}

#: L2 粗标签最多给几条（再多人眼也读不过来，且会被避让层大量丢弃）。
_COARSE_LABEL_LIMIT = 8

#: 级别别名的归一化表：解算/健康模块的输出可直接喂进来。
_LEVEL_ALIASES = {
    "NONE": "L0",
    "UNKNOWN": "L0",
    "SINGLE": "L1",
    "COARSE": "L1",
    "FUSED": "L2",
    "SOLVED": "L3",
    "MANUAL": "L4",
}


def _finite(value: Any) -> Optional[float]:
    """有限 float；非数值/NaN/Inf 返回 None（调用方不得拿它猜位置）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _normalize_level(level: Any) -> Tuple[str, str]:
    """归一化级别；未知输入降为 L0 并给出原因，不假装高级别。"""
    if level is None:
        return "L0", ""
    text = str(level).strip().upper()
    if not text:
        return "L0", ""
    if text in HUD_HINTS:
        return text, ""
    alias = _LEVEL_ALIASES.get(text)
    if alias is not None:
        return alias, ""
    return "L0", f"级别非法（{level!r}），按 L0 处理"


def _pose_direction(pose: Any) -> Optional[Tuple[float, float, Optional[float],
                                                  Optional[float]]]:
    """取位姿的 (az, alt, roll, fov)；不可用返回 None。"""
    if not isinstance(pose, dict):
        return None
    if pose.get("valid") is False:
        return None
    az = _finite(pose.get("az_deg"))
    alt = _finite(pose.get("alt_deg"))
    if az is None or alt is None or not (-90.0 <= alt <= 90.0):
        return None
    roll = _finite(pose.get("roll_deg"))
    fov = _finite(pose.get("fov_deg"))
    if fov is not None and fov <= 0.0:
        fov = None
    return az % 360.0, alt, roll, fov


def _view_values(view_params: Any) -> Dict[str, Any]:
    """接受 dict；非法输入返回空 dict（调用方再按位姿兜底）。"""
    return view_params if isinstance(view_params, dict) else {}


def _build_view(values: Dict[str, Any],
                pose: Optional[Tuple[float, float, Optional[float],
                                     Optional[float]]]) -> Tuple[ViewState, Any]:
    """构造投影用 ViewState；``view_params`` 优先，缺失时用位姿兜底。

    ``min_fov`` 用 0.5°，与桌面视图一致：2.3° 目镜级视场不能被桌面
    默认的 30° 下限夹回去。投影实例由调用方传入（桌面用透视投影），
    未传时用等距方位投影，保持与 ``sky_to_screen_many`` 默认口径一致。
    """
    center_az = _finite(values.get("center_az"))
    if center_az is None:
        center_az = _finite(values.get("az_deg"))
    center_alt = _finite(values.get("center_alt"))
    if center_alt is None:
        center_alt = _finite(values.get("alt_deg"))
    if center_az is None or center_alt is None:
        if pose is not None:
            center_az, center_alt = pose[0], pose[1]
        else:
            center_az, center_alt = 0.0, 90.0
    fov = _finite(values.get("fov_deg"))
    if fov is None or fov <= 0.0:
        fov = pose[3] if pose is not None and pose[3] else 120.0
    rotation = _finite(values.get("rotation_deg"))
    if rotation is None:
        rotation = pose[2] if pose is not None and pose[2] is not None else 0.0

    min_fov = _finite(values.get("min_fov"))
    if min_fov is None or not (0.5 <= min_fov <= 180.0):
        min_fov = 0.5
    view = ViewState(center_az=center_az, center_alt=center_alt,
                     fov_deg=fov, rotation=rotation,
                     min_fov=min_fov, max_fov=180.0)
    projection = values.get("projection")
    if projection is None or not hasattr(projection, "project_vec_many"):
        projection = AzimuthalEquidistantProjection()
    return view, projection


def _coerce_targets(targets: Any) -> Tuple[List[Any], Optional[str]]:
    """目标序列归一化；非法容器当作空序列并说明原因。"""
    if targets is None:
        return [], None
    if isinstance(targets, (str, bytes, dict)) or not isinstance(targets, Iterable):
        return [], f"targets 必须是目标序列，收到 {type(targets).__name__}"
    return list(targets), None


def _target_azalt(target: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    az = _finite(target.get("az_deg"))
    if az is None:
        az = _finite(target.get("az"))
    if az is None:
        az = _finite(target.get("azimuth_deg"))
    alt = _finite(target.get("alt_deg"))
    if alt is None:
        alt = _finite(target.get("alt"))
    if alt is None:
        alt = _finite(target.get("elevation_deg"))
    if alt is None:
        alt = _finite(target.get("el_deg"))
    return az, alt


def _target_name(target: Dict[str, Any]) -> str:
    for key in ("name", "name_zh", "label", "title"):
        value = target.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _target_mag(target: Dict[str, Any]) -> Optional[float]:
    for key in ("vmag", "mag", "magnitude"):
        value = _finite(target.get(key))
        if value is not None:
            return value
    return None


def _target_distance(target: Dict[str, Any]) -> Optional[float]:
    for key in ("angular_distance_deg", "distance_deg", "offset_deg"):
        value = _finite(target.get(key))
        if value is not None:
            return value
    return None


def _target_kind(target: Dict[str, Any]) -> str:
    for key in ("kind", "type", "category", "obj_type"):
        value = target.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_satellite(target: Dict[str, Any]) -> bool:
    if target.get("is_satellite"):
        return True
    kind = _target_kind(target).lower()
    return "sat" in kind or "卫星" in kind


def _project_targets(view: ViewState, projection: Any,
                     size: Tuple[float, float],
                     targets: Sequence[Any]) -> List[Dict[str, Any]]:
    """把目标投影成屏幕坐标；非法/无坐标的目标原地跳过。"""
    rows: List[Dict[str, Any]] = []
    pending: List[int] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        x = _finite(target.get("x"))
        y = _finite(target.get("y"))
        if x is not None and y is not None:
            rows.append({"target": target, "x": x, "y": y,
                         "az": None, "alt": None})
            continue
        az, alt = _target_azalt(target)
        if az is None or alt is None or not (-90.0 <= alt <= 90.0):
            continue
        rows.append({"target": target, "x": None, "y": None,
                     "az": az, "alt": alt})
        pending.append(len(rows) - 1)

    if pending:
        xs, ys = sky_to_screen_many(
            [rows[i]["az"] for i in pending],
            [rows[i]["alt"] for i in pending],
            view, projection, size,
        )
        for slot, px, py in zip(pending, xs, ys):
            rows[slot]["x"] = float(px)
            rows[slot]["y"] = float(py)
    return rows


def _visible(x: Optional[float], y: Optional[float],
             size: Tuple[float, float]) -> bool:
    if x is None or y is None or not (math.isfinite(x) and math.isfinite(y)):
        return False
    w, h = size
    return 0.0 <= x <= w and 0.0 <= y <= h


def _format_label(name: str, mag: Optional[float],
                  distance: Optional[float], detail: str) -> str:
    if detail == "coarse":
        return name
    parts: List[str] = []
    if name:
        parts.append(name)
    if distance is not None:
        # 角距由融合位姿与目标坐标现算（模型推算），不是独立观测。
        parts.append(f"{distance:.2f}°（推算）")
    if mag is not None:
        # 星等来自星表/目标自述，非本次观测。
        parts.append(f"{mag:.1f}等（星表）")
    return " · ".join(parts)


def _sort_key(item: Dict[str, Any]) -> Tuple[Any, ...]:
    mag = item.get("mag")
    dist = item.get("distance_deg")
    return (mag is None, 0.0 if mag is None else mag,
            dist is None, 0.0 if dist is None else dist,
            item.get("name") or "")


def _segment_points(segment: Any) -> Tuple[str, List[Tuple[float, float]]]:
    """星官线段 -> (名字, [(az, alt), ...])；不认的格式返回空点表。"""
    name = ""
    points: Any = None
    if isinstance(segment, dict):
        value = segment.get("name") or segment.get("name_zh")
        if isinstance(value, str):
            name = value.strip()
        points = segment.get("points")
        if points is None:
            points = segment.get("line")
        if points is None:
            azs = segment.get("az_deg")
            alts = segment.get("alt_deg")
            if isinstance(azs, Iterable) and isinstance(alts, Iterable):
                az_list = list(azs)
                alt_list = list(alts)
                if len(az_list) == len(alt_list):
                    points = list(zip(az_list, alt_list))
        if points is None and isinstance(segment.get("a"), dict) \
                and isinstance(segment.get("b"), dict):
            # 兼容 overlay_asterisms.OverlaySegment.to_dict() 的 a/b 两点形态。
            points = [segment["a"], segment["b"]]
    else:
        points = segment
    if isinstance(points, (str, bytes, dict)) or not isinstance(points, Iterable):
        return name, []

    out: List[Tuple[float, float]] = []
    for point in points:
        try:
            if isinstance(point, dict):
                az = _finite(point.get("az_deg", point.get("az")))
                alt = _finite(point.get("alt_deg", point.get("alt")))
            else:
                az = _finite(point[0])
                alt = _finite(point[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if az is None or alt is None or not (-90.0 <= alt <= 90.0):
            continue
        out.append((az % 360.0, alt))
    return name, out


def _project_segments(segments: Any, view: ViewState, projection: Any,
                      size: Tuple[float, float]) -> List[Dict[str, Any]]:
    """投影星官线段并按可见性切成连续段；不足两点的段丢弃。"""
    if segments is None:
        return []
    if isinstance(segments, (str, bytes, dict)) or not isinstance(segments, Iterable):
        return []
    out: List[Dict[str, Any]] = []
    for segment in segments:
        name, points = _segment_points(segment)
        if len(points) < 2:
            continue
        xs, ys = sky_to_screen_many(
            [p[0] for p in points], [p[1] for p in points],
            view, projection, size,
        )
        run: List[List[float]] = []
        for px, py in zip(xs, ys):
            if _visible(float(px), float(py), size):
                run.append([float(px), float(py)])
            else:
                if len(run) >= 2:
                    out.append({"name": name, "points": run})
                run = []
        if len(run) >= 2:
            out.append({"name": name, "points": run})
    return out


def _sources_text(sources: Mapping[str, Any]) -> str:
    """运行时数据源 → 一行人话；只说快照里写明的状态，不补默认值。

    界面文案不出现 ``pps``/``nmea``/``az``/``alt`` 等工程缩写——原始码值
    仍完整保留在 ``build_hud`` 输出的 ``sources`` 结构里。
    """
    def _block(name: str) -> Mapping[str, Any]:
        value = sources.get(name)
        return value if isinstance(value, Mapping) else {}

    sdr = _block("sdr")
    gnss = _block("gnss")
    time_block = _block("time")
    site = _block("site")

    if sdr.get("stalled"):
        # 连接还在但设备读不出样本：不许再显示「已连接」冒充数据可用。
        parts = ["SDR 无样本（超时）"]
    else:
        parts = ["SDR 已连接" if sdr.get("connected") else "SDR 未连接"]

    time_source = str(time_block.get("source") or "").strip().lower()
    gnss_time = str(gnss.get("time_source") or "").strip().lower()
    if time_source == "gnss" or gnss_time in ("pps", "nmea"):
        # 本仓无 PPS 接线：卫星授时只到整秒级，别读成高精度时间。
        parts.append("时间：卫星授时（整秒级，无秒脉冲）")
    else:
        parts.append("时间：系统时钟（未校准）")

    site_source = str(site.get("source") or "").strip().lower()
    if site_source == "gnss":
        parts.append("位置：GNSS（接收机观测）")
    elif site_source == "manual":
        parts.append("位置：手动位置（未校准）")
    else:
        parts.append("位置：未设置")

    # 天线异常只在有明确 OPEN/SHORT 告警时提示；ok/unknown 不打扰。
    antenna = str(gnss.get("antenna") or "").strip().lower()
    if antenna == "open":
        parts.append("GNSS 天线开路")
    elif antenna == "short":
        parts.append("GNSS 天线短路")
    return " · ".join(parts)


def build_hud(pose: Any,
              targets: Any,
              level: Any,
              view_params: Any,
              size: Sequence[float],
              asterism_segments: Any = None,
              runtime_sources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把位姿/目标/级别/视图换算成一份绘制指令。

    Args:
        pose: 融合位姿 dict（``sky_pointing.fuse_poses`` 输出）。需要
            ``az_deg``/``alt_deg``；``valid=False``、缺字段、NaN、alt 越界
            都视为「没有位姿」。
        targets: 识别目标序列，每项建议含 ``az_deg``/``alt_deg``/``name``，
            可选 ``vmag``/``angular_distance_deg``/``kind``；也接受已经
            带屏幕坐标（``x``/``y``）的目标。
        level: ``"L0"``–``"L4"``；也接受 ``none/single/fused/solved/manual``。
            未知级别降为 L0。
        view_params: 当前视图参数 dict：``center_az``/``center_alt``/
            ``fov_deg``/``rotation_deg``/``min_fov``，可选 ``projection``、
            ``crosshair`` 或 ``crosshair_x``/``crosshair_y``。
        size: ``(width, height)`` 像素；非正/非有限会抛 ``ValueError``。
        asterism_segments: 星官线段（每段为 ``{"name", "points": [(az, alt)]}``
            或点序列），只投影与过滤，不新增数据。
        runtime_sources: ``runtime_sources.get_runtime_sources().snapshot()``
            的只读快照。传入时额外输出 ``sources``（原始结构）与
            ``sources_text``（一行人话：SDR 是否在线、时间/位置从哪来）；
            未连接/无数据就照实写「未连接」「系统时钟」「未设置」，不编造。
            不传时不新增任何键，输出与旧版逐字一致。

    Returns:
        dict：``level``（生效级别）/ ``requested_level`` / ``hint`` /
        ``crosshair`` / ``labels`` / ``asterism_segments`` /
        ``satellite_markers`` / ``manual`` / ``pose_used`` /
        ``targets_total`` / ``targets_visible`` / ``reason``；
        传入 ``runtime_sources`` 时另有 ``sources`` / ``sources_text``。

    Raises:
        ValueError: ``size`` 不是两个有限正数。
    """
    try:
        w = float(size[0])
        h = float(size[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError(f"size 必须是 (width, height)，收到 {size!r}") from None
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0.0 or h <= 0.0:
        raise ValueError(f"size 必须是两个有限正数，收到 {size!r}")

    reasons: List[str] = []
    requested_level, level_reason = _normalize_level(level)
    if level_reason:
        reasons.append(level_reason)

    manual = requested_level == "L4"
    pose_dir = _pose_direction(pose)
    if pose_dir is None and not manual:
        # 没有位姿就不能说「粗略/融合/锁定」——降为 L0，提示还没对准。
        if requested_level != "L0":
            reasons.append("位姿不可用，级别降为 L0")
        level_eff = "L0"
    else:
        level_eff = requested_level

    values = _view_values(view_params)
    if view_params is not None and not isinstance(view_params, dict):
        reasons.append(f"view_params 必须是 dict，收到 {type(view_params).__name__}")
    view, projection = _build_view(values, pose_dir)
    size_wh = (w, h)

    cross_x = _finite(values.get("crosshair_x"))
    cross_y = _finite(values.get("crosshair_y"))
    crosshair = values.get("crosshair")
    if isinstance(crosshair, (list, tuple)) and len(crosshair) >= 2:
        cross_x = _finite(crosshair[0])
        cross_y = _finite(crosshair[1])
    if cross_x is None:
        cross_x = w / 2.0
    if cross_y is None:
        cross_y = h / 2.0
    radius = _finite(values.get("crosshair_radius"))
    if radius is None or radius <= 0.0:
        radius = 12.0

    target_list, target_reason = _coerce_targets(targets)
    if target_reason:
        reasons.append(target_reason)
    rows = _project_targets(view, projection, size_wh, target_list)
    visible_rows = [row for row in rows
                    if _visible(row["x"], row["y"], size_wh)]
    skipped = len(target_list) - len(visible_rows)
    if skipped > 0 and target_list:
        reasons.append(f"{skipped} 个目标缺坐标或不在画面内，未绘制")

    labels: List[Dict[str, Any]] = []
    satellites: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []

    if level_eff in ("L2", "L3", "L4"):
        detail = "coarse" if level_eff == "L2" else "precise"
        for row in visible_rows:
            target = row["target"]
            name = _target_name(target)
            mag = _target_mag(target)
            distance = _target_distance(target)
            if (detail == "precise" and distance is None
                    and pose_dir is not None
                    and row["az"] is not None and row["alt"] is not None):
                distance = angular_distance_deg(
                    pose_dir[0], pose_dir[1], row["az"], row["alt"])
            text = _format_label(name, mag, distance, detail)
            if not text:
                continue
            labels.append({
                "name": name,
                "x": float(row["x"]),
                "y": float(row["y"]),
                "mag": mag,
                "distance_deg": distance,
                "text": text,
                "detail": detail,
                "kind": _target_kind(target),
            })
        labels.sort(key=_sort_key)
        if detail == "coarse":
            labels = labels[:_COARSE_LABEL_LIMIT]

        for row in visible_rows:
            target = row["target"]
            if not _is_satellite(target):
                continue
            name = _target_name(target)
            distance = _target_distance(target)
            if (distance is None and pose_dir is not None
                    and row["az"] is not None and row["alt"] is not None):
                distance = angular_distance_deg(
                    pose_dir[0], pose_dir[1], row["az"], row["alt"])
            satellites.append({
                "name": name,
                "x": float(row["x"]),
                "y": float(row["y"]),
                "mag": _target_mag(target),
                "distance_deg": distance,
                "text": _format_label(name, _target_mag(target), distance,
                                      "precise"),
            })

        segments = _project_segments(asterism_segments, view, projection, size_wh)

    hud = {
        "level": level_eff,
        "requested_level": requested_level,
        "hint": HUD_HINTS[level_eff],
        "crosshair": {"x": float(cross_x), "y": float(cross_y),
                      "radius": float(radius)},
        "labels": labels,
        "asterism_segments": segments,
        "satellite_markers": satellites,
        "manual": manual,
        "pose_used": pose_dir is not None and not manual,
        "targets_total": len(target_list),
        "targets_visible": len(visible_rows),
        "reason": "；".join(reasons),
    }
    if isinstance(runtime_sources, Mapping):
        sources = dict(runtime_sources)
        hud["sources"] = sources
        hud["sources_text"] = _sources_text(sources)
    return hud
