#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""把程序内置图层导出成 GeoJSON 文件，供 QGIS 直接打开。

为什么要有这个脚本
==================

``sites_as_geojson()`` / ``ground_track.to_geojson()`` / ``PositionService.geojson()``
三个导出面早就有契约测试，但**没有任何一条路径把它们写到磁盘**——用户想在
QGIS 里看一眼台址分布，只能自己写 Python。本脚本补的就是这一步。

支持三个图层：

* ``observing-sites``：4 个科研台址 + 公众观星点（内置坐标，带来源 URL）；
* ``ground-track``：由**调用方给的 TLE 文件**传播出的地面轨迹；
* ``footprint``：同一份 TLE 在同一时刻的覆盖圈（多边形 + 星下点）。

TLE 必须显式传入——脚本不联网取 TLE、不用过期缓存冒充，也不在 TLE 缺失时
写空图层。

实时位置需要正在收的数据，本脚本不硬造——宁可不提供，也不给假数据。

口径
====
* RFC 7946 ``FeatureCollection``，WGS84，坐标序 ``[lon, lat]``；
* 写盘前用 ``allow_nan=False`` 严格序列化：NaN/Infinity 不是 JSON，
  QGIS 的解析器会整层失败，所以在写之前就炸掉；
* 临时文件 + ``os.replace`` 原子替换，失败不留半截文件；
* 轨迹按日界线切段（每段一条 LineString），不画横穿地图的假线；
  属性里带 TLE 历元，便于判断这条轨迹「是按哪份根数算的」。

用法
====
    python scripts/export_geojson.py sites.geojson
    python scripts/export_geojson.py sites.geojson --layer observing-sites
    python scripts/export_geojson.py iss.geojson --layer ground-track \\
        --tle experiments/satellite_rx/data/iss.tle --sat ISS \\
        --start 2026-11-17T00:00:00Z
    python scripts/export_geojson.py iss_fp.geojson --layer footprint \\
        --tle experiments/satellite_rx/data/iss.tle --sat ISS \\
        --start 2026-11-17T00:00:00Z --min-elevation 5
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mbdsdr_ai.astronomy import observing_sites as _sites  # noqa: E402
from mbdsdr_ai.ground_track import (  # noqa: E402
    ground_track,
    satellite_footprint,
    to_geojson,
)
from mbdsdr_ai.orbit import tle_epoch_iso  # noqa: E402

#: 图层名 → 标题。构造方式因图层而异，统一在 :func:`build_layer` 里分派。
LAYERS = {
    "observing-sites": "观测台址与公众观星点",
    "ground-track": "卫星地面轨迹（由调用方 TLE 传播）",
    "footprint": "卫星覆盖圈（由调用方 TLE 传播）",
}


def load_tle(path: Path, sat: str) -> Tuple[str, str, str]:
    """从 TLE 文本里取出一颗卫星的 (名字, line1, line2)。

    名字按「精确忽略大小写 → 子串」两级匹配；纯数字按 NORAD 编号匹配。
    匹配不到或不唯一都拒绝并列出候选——猜一颗星画出来的轨迹比不画更糟。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"读不到 TLE 文件 {path}：{exc}") from exc

    entries: List[Tuple[str, str, str]] = []
    name, line1 = "", ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("1 "):
            line1 = stripped
            continue
        if stripped.startswith("2 ") and line1:
            entries.append((name, line1, stripped))
            name, line1 = "", ""
            continue
        # 既不是 1 行也不是 2 行 → 当作卫星名行
        name = stripped

    if not entries:
        raise ValueError(f"{path} 里没有成对的 TLE 两行根数")

    target = sat.strip()
    if target.isdigit():
        hits = [e for e in entries if e[2][2:7].strip() == target]
    else:
        low = target.lower()
        hits = [e for e in entries if e[0].strip().lower() == low]
        if not hits:
            hits = [e for e in entries if low and low in e[0].strip().lower()]
    if not hits:
        sample = ", ".join(e[0] or "(无名)" for e in entries[:5])
        raise ValueError(f"{path} 里找不到卫星 {sat!r}；前几个候选：{sample}")
    if len(hits) > 1:
        names = ", ".join(e[0] or "(无名)" for e in hits[:5])
        raise ValueError(f"卫星 {sat!r} 匹配到多颗（{names}），请用完整名称或 NORAD 编号")
    name, l1, l2 = hits[0]
    return (name or sat, l1, l2)


def parse_start(value: Optional[str]) -> Optional[float]:
    """``--start``：Unix 秒或 ISO-8601（无时区按 UTC）。缺省返回 None → 当前时刻。"""
    if not value:
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"--start 既不是 Unix 秒也不是 ISO-8601：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def build_ground_track_layer(sat_tle: Tuple[str, str, str],
                             minutes: float, step_s: float,
                             start_unix: Optional[float]) -> Dict[str, Any]:
    """TLE → 按日界线切段的地面轨迹 FeatureCollection。"""
    name, line1, line2 = sat_tle
    track = ground_track(name, when_unix=start_unix, minutes=minutes,
                         step_s=step_s, tle=(line1, line2))
    fc = to_geojson(track, source=f"tle:{name}")
    epoch = tle_epoch_iso(line1)
    for feat in fc["features"]:
        feat["properties"].update({
            "satellite": name,
            "norad_id": int(line2[2:7]),
            # 没解析出历元就写 null，不编一个时间
            "tle_epoch_utc": epoch or None,
        })
    return fc


def split_ring_at_dateline(ring: List[Tuple[float, float]]
                           ) -> List[List[Tuple[float, float]]]:
    """把覆盖圈顶点序列按日界线切段（``(lat, lon)``，切点经度取 ±180）。

    跨日界线的整圈多边形在 QGIS 里会铺成一条横穿全图的色带——那不是卫星
    覆盖，是 360° 经度跳变。宁可退化成「分段边界线」，也不画这张假图。
    切点纬度按相邻两点线性插值，只用于画边界，不参与任何测量。
    """
    segments: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for point in ring:
        if current and abs(point[1] - current[-1][1]) > 180.0:
            prev = current[-1]
            delta = point[1] - prev[1]
            delta = delta - 360.0 if delta > 180.0 else delta
            delta = delta + 360.0 if delta < -180.0 else delta
            edge = 180.0 if delta > 0.0 else -180.0
            t = 0.0 if delta == 0.0 else (edge - prev[1]) / delta
            t = min(max(t, 0.0), 1.0)
            lat_b = prev[0] + t * (point[0] - prev[0])
            current.append((lat_b, edge))
            segments.append(current)
            current = [(lat_b, -edge)]
        current.append(point)
    if current:
        segments.append(current)
    return segments


def build_footprint_layer(sat_tle: Tuple[str, str, str],
                          when_unix: Optional[float],
                          min_elevation_deg: Optional[float]
                          ) -> Dict[str, Any]:
    """TLE → 覆盖圈（多边形或日界线切段）+ 星下点。"""
    name, line1, line2 = sat_tle
    kwargs: Dict[str, Any] = {"when_unix": when_unix, "tle": (line1, line2)}
    if min_elevation_deg is not None:
        kwargs["min_elevation_deg"] = float(min_elevation_deg)
    fp = satellite_footprint(name, **kwargs)
    sub = fp["subpoint"]
    epoch = tle_epoch_iso(line1)
    common = {
        "role": "footprint",
        "satellite": name,
        "norad_id": int(line2[2:7]),
        "tle_epoch_utc": epoch or None,
        "ellipsoid": fp["ellipsoid"],
        "unit": fp["unit"],
        "central_angle_deg": round(float(fp["central_angle_deg"]), 4),
        "radius_km": round(float(fp["radius_km"]), 3),
        "min_elevation_deg": float(fp["min_elevation_deg"]),
        "subpoint_lat_deg": round(float(sub.lat_deg), 6),
        "subpoint_lon_deg": round(float(sub.lon_deg), 6),
        "subpoint_alt_km": round(float(sub.sat_alt_km), 3),
        "at_unix": float(sub.unix_s),
    }
    ring = fp["ring"]
    features: List[Dict[str, Any]] = []
    segments = split_ring_at_dateline(ring)
    if len(segments) == 1 and segments[0][0] == segments[0][-1]:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[float(lon), float(lat)]
                                 for lat, lon in segments[0]]],
            },
            "properties": dict(common),
        })
    else:
        for seg in segments:
            if len(seg) < 2:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[float(lon), float(lat)]
                                    for lat, lon in seg],
                },
                "properties": dict(common, role="footprint_boundary",
                                   geometry_note="覆盖圈跨日界线，未闭合"
                                                 "填充，改为分段边界线"),
            })
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [float(sub.lon_deg), float(sub.lat_deg)]},
        "properties": dict(common, role="footprint_subpoint"),
    })
    return {"type": "FeatureCollection", "features": features}


def build_layer(name: str, *, tle: Optional[Tuple[str, str, str]] = None,
                minutes: float = 90.0, step_s: float = 30.0,
                start_unix: Optional[float] = None,
                min_elevation_deg: Optional[float] = None) -> dict:
    """生成图层并做写前校验（结构 + 严格 JSON）。"""
    if name == "observing-sites":
        fc = _sites.sites_as_geojson()
    elif name == "ground-track":
        if tle is None:
            raise ValueError("ground-track 图层需要 TLE")
        fc = build_ground_track_layer(tle, minutes, step_s, start_unix)
    elif name == "footprint":
        if tle is None:
            raise ValueError("footprint 图层需要 TLE")
        fc = build_footprint_layer(tle, start_unix, min_elevation_deg)
    else:  # argparse 已挡住未知图层；这里是防直接调用的兜底
        raise ValueError(f"未知图层：{name}")
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"图层 {name} 不是 FeatureCollection")
    # NaN / Infinity 不是合法 JSON；严格序列化一次作为写盘前体检
    json.dumps(fc, ensure_ascii=False, allow_nan=False)
    return {"title": LAYERS[name], "geojson": fc}


def write_json_atomic(path: Path, payload: dict) -> None:
    """同目录临时文件 + os.replace，避免失败时留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, allow_nan=False,
                      indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="把内置图层导出为 QGIS 可直接打开的 GeoJSON")
    ap.add_argument("output", help="输出文件路径（.geojson）")
    ap.add_argument("--layer", default="observing-sites",
                    choices=sorted(LAYERS),
                    help="要导出的图层（默认 observing-sites）")
    ap.add_argument("--tle", help="TLE 文本文件（ground-track 必填）")
    ap.add_argument("--sat", help="卫星名或 NORAD 编号（ground-track 必填）")
    ap.add_argument("--start", help="轨迹起始时刻：Unix 秒或 ISO-8601，缺省当前")
    ap.add_argument("--minutes", type=float, default=90.0,
                    help="轨迹时长（分钟，默认 90 覆盖一圈 LEO）")
    ap.add_argument("--step-s", type=float, default=30.0,
                    help="采样步长（秒，默认 30）")
    ap.add_argument("--min-elevation", type=float, default=None,
                    help="覆盖圈最小仰角（度；缺省取全仓可见门限）")
    args = ap.parse_args(argv)

    sat_tle: Optional[Tuple[str, str, str]] = None
    start_unix: Optional[float] = None
    if args.layer in ("ground-track", "footprint"):
        if not args.tle or not args.sat:
            ap.error(f"--layer {args.layer} 需要同时给 --tle 与 --sat")
        try:
            sat_tle = load_tle(Path(args.tle), args.sat)
            start_unix = parse_start(args.start)
        except ValueError as exc:
            print(f"导出失败：{exc}", file=sys.stderr)
            return 1

    out = Path(args.output).expanduser()
    try:
        built = build_layer(args.layer, tle=sat_tle, minutes=args.minutes,
                            step_s=args.step_s, start_unix=start_unix,
                            min_elevation_deg=args.min_elevation)
        write_json_atomic(out, built["geojson"])
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 1

    fc = built["geojson"]
    print(f"图层：{built['title']}（{args.layer}）")
    print(f"要素：{len(fc['features'])}")
    if sat_tle is not None:
        name, line1, _line2 = sat_tle
        print(f"卫星：{name}（NORAD {_norad_from_line1(line1)}）")
        print(f"TLE 历元：{tle_epoch_iso(line1) or '未解析'}")
        print(f"轨迹起点：{_iso_utc(start_unix)}")
        if args.layer == "footprint":
            gate = (args.min_elevation if args.min_elevation is not None
                    else "全仓可见门限")
            print(f"最小仰角：{gate}")
    print(f"文件：{out}")
    print("坐标：WGS84 / RFC 7946，[lon, lat]；QGIS 可直接拖入")
    return 0


def _norad_from_line1(line1: str) -> str:
    """line1 的 NORAD 编号（列 3-7）。取不到就说取不到。"""
    try:
        return line1[2:7].strip() or "未知"
    except (IndexError, TypeError):
        return "未知"


def _iso_utc(unix_s: Optional[float]) -> str:
    if unix_s is None:
        return "当前时刻"
    return _dt.datetime.fromtimestamp(
        float(unix_s), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
