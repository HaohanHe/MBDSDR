#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""手动核对 AR 指向链路：打印当前级别、目标与解算残差。

用法：
    python scripts/sky_pointing_check.py --input report.json
        [--now-utc 1700000000] [--json]

``report.json`` 最小字段：``observations``（数组）、``site``
（``lat_deg`` / ``lon_deg``）、``at_utc``（带时区偏移的 ISO 8601）。
可选：``camera``（``width_px`` / ``height_px`` / ``fov_deg``）、
``commanded``（``az_deg`` / ``el_deg``）、``stars``（像素坐标数组）、
``manual_override``、``solve_within_tolerance``、``camera_last_frame_utc``。

只读脚本：不连接设备、不联网、不写盘。缺站址/时刻/视场时如实说明并跳过
对应步骤，不补默认值；残差只报数字与阈值分级，不宣称对准。

退出码：0 正常（无解算请求，或解算成功）；2 输入文件/字段非法；
3 解算未完成（无可用解算器，或内外两条解算路径都失败）；1 其它内部错误。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.sky_health import health_snapshot  # noqa: E402
from mbdsdr_ai.sky_pointing import (  # noqa: E402
    PoseObservation,
    fuse_poses,
    identify_direction,
)
from mbdsdr_ai.sky_pointing_pipeline import (  # noqa: E402
    RESIDUAL_TOLERANCE_DEG,
    CameraParams,
    apply_solve,
    solve_and_verify,
)
from mbdsdr_ai.sky_solve import solver_status  # noqa: E402


class InputError(Exception):
    """输入文件或字段非法（退出码 2）。"""


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise InputError(f"{where} 必须是有限数值，收到 bool")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{where} 必须是有限数值，收到 {value!r}") from None
    if not math.isfinite(out):
        raise InputError(f"{where} 必须是有限数值，收到 {value!r}")
    return out


def _mapping(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{where} 必须是对象，收到 {type(value).__name__}")
    return value


def _require(mapping: Dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise InputError(f"{where} 缺少字段：{key}")
    return mapping[key]


def _parse_utc(text: Any, where: str) -> datetime:
    if not isinstance(text, str) or not text.strip():
        raise InputError(f"{where} 必须是 ISO 8601 字符串，收到 {text!r}")
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InputError(f"{where} 不是合法 ISO 8601：{exc}") from None
    if dt.tzinfo is None:
        raise InputError(f"{where} 缺少时区偏移，无法判定 UTC 时刻")
    return dt.astimezone(timezone.utc)


def _parse_observations(raw: Any) -> List[PoseObservation]:
    if not isinstance(raw, list):
        raise InputError("observations 必须是数组")
    out: List[PoseObservation] = []
    for index, item in enumerate(raw):
        where = f"observations[{index}]"
        obj = _mapping(item, where)
        source = _require(obj, "source", where)
        if not isinstance(source, str) or not source.strip():
            raise InputError(f"{where}.source 必须是非空字符串")
        out.append(PoseObservation(
            source=source,
            t_utc=_finite(_require(obj, "t_utc", where), f"{where}.t_utc"),
            az_deg=_finite(_require(obj, "az_deg", where), f"{where}.az_deg"),
            alt_deg=_finite(_require(obj, "alt_deg", where), f"{where}.alt_deg"),
            accuracy_deg=_finite(_require(obj, "accuracy_deg", where),
                                 f"{where}.accuracy_deg"),
            roll_deg=(0.0 if obj.get("roll_deg") is None else
                      _finite(obj["roll_deg"], f"{where}.roll_deg")),
            fov_deg=(None if obj.get("fov_deg") is None else
                     _finite(obj["fov_deg"], f"{where}.fov_deg")),
        ))
    return out


def _parse_site(raw: Any) -> Tuple[float, float]:
    obj = _mapping(raw, "site")
    lat = _finite(_require(obj, "lat_deg", "site"), "site.lat_deg")
    lon = _finite(_require(obj, "lon_deg", "site"), "site.lon_deg")
    if not (-90.0 <= lat <= 90.0):
        raise InputError(f"site.lat_deg 越界（{lat}°，合法 -90..90）")
    if not (-360.0 <= lon <= 360.0):
        raise InputError(f"site.lon_deg 越界（{lon}°，合法 -360..360）")
    return lat, lon


def _parse_camera(raw: Any) -> CameraParams:
    obj = _mapping(raw, "camera")
    width = _finite(_require(obj, "width_px", "camera"), "camera.width_px")
    height = _finite(_require(obj, "height_px", "camera"), "camera.height_px")
    fov = _finite(_require(obj, "fov_deg", "camera"), "camera.fov_deg")
    if width <= 0.0 or height <= 0.0:
        raise InputError(f"camera 尺寸必须为正，收到 {width}×{height}")
    if not (0.0 < fov <= 180.0):
        raise InputError(f"camera.fov_deg 必须在 (0, 180] 度内，收到 {fov}")
    return CameraParams(width_px=width, height_px=height, fov_deg=fov)


def _parse_commanded(raw: Any) -> Tuple[float, float]:
    obj = _mapping(raw, "commanded")
    az = _finite(_require(obj, "az_deg", "commanded"), "commanded.az_deg")
    el = _finite(_require(obj, "el_deg", "commanded"), "commanded.el_deg")
    if not (-90.0 <= el <= 90.0):
        raise InputError(f"commanded.el_deg 越界（{el}°，合法 -90..90）")
    return az, el


def _parse_stars(raw: Any) -> List[Tuple[float, float]]:
    if not isinstance(raw, list) or not raw:
        raise InputError("stars 必须是非空数组")
    points: List[Tuple[float, float]] = []
    for index, item in enumerate(raw):
        where = f"stars[{index}]"
        if isinstance(item, dict):
            x = _finite(_require(item, "x_px", where), f"{where}.x_px")
            y = _finite(_require(item, "y_px", where), f"{where}.y_px")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x = _finite(item[0], f"{where}[0]")
            y = _finite(item[1], f"{where}[1]")
        else:
            raise InputError(f"{where} 必须是 [x, y] 或含 x_px/y_px 的对象")
        points.append((x, y))
    return points


def _optional_bool(payload: Dict[str, Any], key: str) -> Optional[bool]:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise InputError(f"{key} 必须是布尔值，收到 {value!r}")
    return value


def _optional_float(payload: Dict[str, Any], key: str) -> Optional[float]:
    if key not in payload or payload[key] is None:
        return None
    return _finite(payload[key], key)


def _jd_from_utc(dt: datetime) -> float:
    """UTC datetime → 儒略日（复用随仓 astronomy 的 Unix→JD 换算）。"""
    from mbdsdr_ai.astronomy import unix_to_jd

    return float(unix_to_jd(dt.timestamp()))


def _target_block(pose: Dict[str, Any], camera: Optional[CameraParams],
                  jd: float, lat: float, lon: float) -> Dict[str, Any]:
    if not pose.get("valid"):
        return {"skipped": True,
                "reason": f"融合位姿不可用：{pose.get('reason') or '未知原因'}"}
    fov: Optional[float] = camera.fov_deg if camera is not None else None
    if fov is None:
        fov = pose.get("fov_deg")
    if fov is None:
        return {"skipped": True,
                "reason": "未提供视场（camera.fov_deg 或观测 fov_deg），"
                          "无法调用识别"}
    result = identify_direction(float(pose["az_deg"]), float(pose["alt_deg"]),
                                float(fov), jd, lat, lon, limit=3)
    block: Dict[str, Any] = {"skipped": False, "fov_deg": float(fov),
                             "result": result}
    return block


def _residual_block(stars: List[Tuple[float, float]],
                    camera: CameraParams,
                    commanded: Tuple[float, float],
                    site: Tuple[float, float],
                    at_utc: datetime,
                    pose: Dict[str, Any],
                    level_before: str,
                    source_count: int) -> Dict[str, Any]:
    outcome = solve_and_verify(stars, camera, commanded[0], commanded[1],
                               site[0], site[1], at_utc)
    update = apply_solve(pose, outcome, level_before=level_before,
                         source_count=source_count)
    return {
        "skipped": False,
        "ok": bool(outcome.ok),
        "error": outcome.error,
        "reason": outcome.reason,
        "residual_deg": outcome.residual_deg,
        "residual_tolerance_deg": RESIDUAL_TOLERANCE_DEG,
        "within_tolerance": bool(outcome.within_tolerance),
        "matched_stars": int(outcome.matched_stars),
        "applied": bool(update.applied),
        "level": update.level,
        "update_reason": update.reason,
    }


def _print_human(report: Dict[str, Any]) -> None:
    health = report["health"]
    pose = report["pose"]
    target = report["target"]
    residual = report["residual"]

    print(f"级别（level）：{health['level']}")
    print(f"原因：{health['reason']}")

    if pose.get("valid"):
        sources = "、".join(pose.get("sources") or [])
        print(f"融合位姿：az={pose['az_deg']:.3f}° alt={pose['alt_deg']:.3f}° "
              f"accuracy={pose['accuracy_deg']:.3f}° sources={sources}")
    else:
        print(f"融合位姿：不可用；{pose.get('reason') or '未知原因'}")

    if target.get("skipped"):
        print(f"目标：{target['reason']}")
    else:
        result = target["result"]
        if result.get("matched"):
            print(f"目标：az={result['query']['az_deg']:.3f}° "
                  f"alt={result['query']['alt_deg']:.3f}° "
                  f"fov={target['fov_deg']:.3f}° → "
                  f"命中 {result['total_matched']} 颗"
                  f"（truncated={result['truncated']}）")
            for star in result.get("stars") or []:
                names = "/".join(star.get("asterisms") or []) or "-"
                label = str(star.get("name") or "")
                hip = star.get("hip")
                hip_part = (f"（HIP {hip}）"
                            if hip and label != f"HIP {hip}" else "")
                print(f"  - {label}{hip_part} {star.get('con')} "
                      f"mag={star.get('vmag')} "
                      f"角距={star.get('angular_distance_deg')}° 星官={names}")
        else:
            print(f"目标：未命中；{result.get('reason') or '未知原因'}")

    if residual.get("skipped"):
        print(f"残差：{residual['reason']}")
        return
    if not residual["ok"]:
        print(f"残差（residual）：未算出；error={residual['error']}；"
              f"{residual['reason']}")
        print(f"  applied={residual['applied']} level={residual['level']}；"
              f"{residual['update_reason']}")
        return
    print(f"残差（residual）：{residual['residual_deg']:.6f}° "
          f"（阈值 {residual['residual_tolerance_deg']:g}°，"
          f"仅阈值分级，不构成对准结论）")
    print(f"  applied={residual['applied']} level={residual['level']} "
          f"matched_stars={residual['matched_stars']} "
          f"within_tolerance={residual['within_tolerance']}")
    if residual["update_reason"]:
        print(f"  说明：{residual['update_reason']}")


def _build_report(payload: Dict[str, Any], now_utc: float) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise InputError("输入文件顶层必须是 JSON 对象")

    observations = _parse_observations(_require(payload, "observations",
                                                "report"))
    site = _parse_site(_require(payload, "site", "report"))
    at_utc = _parse_utc(_require(payload, "at_utc", "report"), "at_utc")

    camera = _parse_camera(payload["camera"]) if payload.get("camera") is not None else None
    commanded = (_parse_commanded(payload["commanded"])
                 if payload.get("commanded") is not None else None)
    stars = _parse_stars(payload["stars"]) if payload.get("stars") is not None else None
    manual_override = _optional_bool(payload, "manual_override") or False
    solve_within_tolerance = _optional_bool(payload, "solve_within_tolerance")
    camera_last_frame_utc = _optional_float(payload, "camera_last_frame_utc")

    status = solver_status()
    health = health_snapshot(
        observations, now_utc, status,
        camera_last_frame_utc=camera_last_frame_utc,
        manual_override=manual_override,
        solve_within_tolerance=solve_within_tolerance,
    )
    pose = fuse_poses(observations, now_utc)

    target = _target_block(pose, camera, _jd_from_utc(at_utc),
                           site[0], site[1])

    if stars is not None and camera is not None and commanded is not None:
        residual = _residual_block(
            stars, camera, commanded, site, at_utc, pose,
            health["level"], max(1, len(pose.get("sources") or [])))
    else:
        missing = [name for name, value in
                   (("camera", camera), ("commanded", commanded),
                    ("stars", stars)) if value is None]
        residual = {
            "skipped": True,
            "reason": ("未提供解算输入，跳过残差"
                       f"（缺：{', '.join(missing)}）"),
        }

    return {
        "now_utc": now_utc,
        "at_utc": at_utc.isoformat(),
        "site": {"lat_deg": site[0], "lon_deg": site[1]},
        "health": health,
        "pose": pose,
        "target": target,
        "residual": residual,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="核对 AR 指向链路：打印级别、目标与解算残差（只读）")
    parser.add_argument("--input", required=True, metavar="REPORT.json",
                        help="观测报告 JSON 路径")
    parser.add_argument("--now-utc", type=float, default=None,
                        metavar="UNIX_S",
                        help="当前 Unix 秒；缺省取本机时钟（仅用于判定观测年龄）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    try:
        path = Path(args.input)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise InputError(f"无法读取输入文件 {path}：{exc}") from None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"输入文件不是合法 JSON：{exc}") from None
        now_utc = (time.time() if args.now_utc is None else
                   _finite(args.now_utc, "--now-utc"))
        report = _build_report(payload, now_utc)
    except InputError as exc:
        print(f"输入非法：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 其它未预期错误统一退出 1
        print(f"内部错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    residual = report["residual"]
    if not residual.get("skipped") and not residual.get("ok"):
        # 外部解算器不可用时会回退内置解算；两条路径都没算出残差，
        # 都不该以 0 退出让调用方误以为解算成功。
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
