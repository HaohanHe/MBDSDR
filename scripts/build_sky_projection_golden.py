"""为手机端生成坐标与投影的黄金向量（M1.2 / M1.3 的对拍依据）。

本机没有 Flutter 工具链（`docs/parameter-unification.md:453` 已记录），
Dart 侧无法用 `flutter test` 自证。可行的替代是：
**让桌面这份唯一真值算出一批输入/期望输出，Dart 测试读它逐条对拍。**
没有 Flutter 时，这批向量本身仍可用 Python 校验（它就是桌面算出来的）。

产物（`mobile/test/fixtures/`）
------------------------------
- `sky_transform_golden.json`  RA/Dec + 站址 + 时刻 -> (az, alt)
- `sky_projection_golden.json`  (az, alt) + 视图 -> 屏幕像素

纪律
----
* 黄金值**只能由本脚本生成**，不许手改——手改等于把期望值迁就实现。
* 生成时禁用章动补偿？不。桌面 `j2000_to_altaz` 默认含章动（IAU 2000B 主项
  ±9″），而 Dart 侧按设计不做章动。所以这里**显式记录**两边口径差，并由
  `tolerance_arcsec` 字段把容差写进数据，让测试的容差有出处、可审计。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEFAULT_OUT = os.path.join(_ROOT, "mobile", "test", "fixtures")

#: Dart 侧不做章动，与桌面（含 IAU 2000B 主项，±9″）的固有差。
#: 容差取 0.02°（72″）——是章动量的 8 倍，仍远小于手机罗盘误差（±3–5°），
#: 既不掩盖真实 bug，也不因模型差别误报。
TOLERANCE_DEG = 0.02

#: 若干有代表性的星：天狼（南天最亮）、织女（北天高纬）、北极星（近天极）、
#: 天枢（北斗第一颗，中国星官重点）、以及两个边界角（ra=0 / dec=0）。
STAR_CASES = [
    ("sirius", 101.28722, -16.716116),
    ("vega", 279.234734, 38.783689),
    ("polaris", 37.954560, 89.264109),
    ("beidou_tianshu", 165.93196, 61.75103),
    ("equator_origin", 0.0, 0.0),
    ("south_low", 200.0, -60.0),
]

#: 站址：中性示例站与一个南半球站，覆盖正负纬度。
SITE_CASES = [
    ("sample_45n_120e", 45.0, 120.0),
    ("south_34s_18e", -33.9, 18.4),
    ("equator_0_0", 0.0, 0.0),
]

#: 儒略日：J2000 本身、一个 2023 年、一个 2026 年夏、一个远端 2038。
JD_CASES = [2451545.0, 2460000.5, 2461355.25, 2480000.0]


def _build_transform_golden() -> Dict[str, Any]:
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)

    rows: List[Dict[str, Any]] = []
    for site_name, lat, lon in SITE_CASES:
        station = GroundStation(lon, lat, 0.0, site_name)
        for jd in JD_CASES:
            for star_name, ra, dec in STAR_CASES:
                v = vec_from_radec(ra, dec)
                altaz = j2000_to_altaz(v, station, jd)
                az, alt = azalt_from_vec(altaz)
                rows.append({
                    "star": star_name,
                    "site": site_name,
                    "ra_deg": ra,
                    "dec_deg": dec,
                    "lat_deg": lat,
                    "lon_deg": lon,
                    "jd_ut1": jd,
                    "az_deg": float(az) % 360.0,
                    "alt_deg": float(alt),
                })
    return {
        "meta": {
            "schema": "mbdsdr.mobile.sky_transform_golden.v1",
            "generator": "scripts/build_sky_projection_golden.py",
            "generated_utc": _dt.datetime.now(_dt.timezone.utc)
                                  .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_of_truth": "mbdsdr_ai.celestial_geometry.j2000_to_altaz",
            "coord_sys": "j2000_to_altaz_direct",
            "tolerance_deg": TOLERANCE_DEG,
            "tolerance_reason": (
                "桌面链含 IAU 2000B 章动主项（±9″），Dart 侧按设计不做章动；"
                "0.02° 覆盖该固有差异，且远小于手机罗盘误差（±3–5°）"),
            "count": len(rows),
        },
        "cases": rows,
    }


def _build_projection_golden() -> Dict[str, Any]:
    from mbdsdr_ai.celestial_geometry import PerspectiveProjection
    from mbdsdr_ai.sky_interaction import ViewState, sky_to_screen

    proj = PerspectiveProjection()

    # 视图组合：正视南方 / 高仰角（近天顶支）/ 带旋转 / 大视场 / 窄视场。
    views = [
        ("due_south_60deg", dict(center_az=180.0, center_alt=45.0,
                                fov=60.0, rotation=0.0)),
        ("near_zenith_50deg", dict(center_az=90.0, center_alt=89.0,
                                  fov=50.0, rotation=0.0)),
        ("rotated_25deg", dict(center_az=270.0, center_alt=30.0,
                               fov=25.0, rotation=17.0)),
        ("narrow_5deg", dict(center_az=0.0, center_alt=20.0,
                             fov=5.0, rotation=0.0)),
        ("wide_110deg", dict(center_az=135.0, center_alt=60.0,
                             fov=110.0, rotation=0.0)),
    ]
    sizes = [(1080, 1920), (1920, 1080), (800, 800)]

    # 采样点：视中心本身、四个方向的偏移、以及视场外（应为 null）。
    offsets = [(0.0, 0.0), (0.0, 20.0), (0.0, -20.0),
               (20.0, 0.0), (-20.0, 0.0), (10.0, 10.0)]

    cases: List[Dict[str, Any]] = []
    clamped_skipped = 0
    for view_name, vp in views:
        for (w, h) in sizes:
            # 显式给 min_fov/max_fov：ViewState 默认 min_fov=30°，
            # 会把 25°/5° 这两个窄视场悄悄夹成 30° —— 记录的就不再是
            # 我们想验的那组参数了。桌面视图自己是 min_fov=0.5
            # （`rf_sky_view.py:1855`），这里对齐真实运行值。
            view = ViewState(center_az=vp["center_az"],
                             center_alt=vp["center_alt"],
                             fov_deg=vp["fov"],
                             rotation=vp["rotation"],
                             min_fov=0.5, max_fov=180.0)
            pts: List[Dict[str, Any]] = []
            for d_az, d_alt in offsets:
                az = (view.center_az + d_az) % 360.0
                alt = max(-89.0, min(89.0, view.center_alt + d_alt))
                px, py = sky_to_screen(az, alt, view, proj, (w, h))
                visible = math.isfinite(px) and math.isfinite(py)
                if visible:
                    # 桌面 `_to_screen_arrays` 会把画布外有限点钳到
                    # ±4×短边（`SCREEN_OFFSCREEN_CLAMP_FACTOR`），这是为了
                    # 防止极端 FOV 下坐标爆炸成 1e6 像素。手机端**不做这个
                    # 钳制**——真实坐标才能让段裁剪算对方向。两种做法都对，
                    # 但数值对不上，所以这类点不进 fixtures（不是掩盖，
                    # 是在条目上记明跳过数）。
                    lim = 4.0 * float(min(w, h))
                    if (abs(px - w / 2.0) >= lim) or (abs(py - h / 2.0) >= lim):
                        clamped_skipped += 1
                        continue
                pts.append({
                    "az_deg": az,
                    "alt_deg": alt,
                    "visible": bool(visible),
                    # 不可见时写 null，而不是写一个哨兵数值——
                    # 哨兵会被下游当成真实坐标。
                    "px": float(px) if visible else None,
                    "py": float(py) if visible else None,
                })
            # 反面样本：视中心正后方（背对相机）必须不可见。
            behind_alt = -view.center_alt
            if -89.0 <= behind_alt <= 89.0:
                az = (view.center_az + 180.0) % 360.0
                px, py = sky_to_screen(az, behind_alt, view, proj, (w, h))
                visible = math.isfinite(px) and math.isfinite(py)
                pts.append({
                    "az_deg": az, "alt_deg": behind_alt,
                    "visible": bool(visible),
                    "px": float(px) if visible else None,
                    "py": float(py) if visible else None,
                })
            cases.append({
                "name": f"{view_name}_{w}x{h}",
                "view": {
                    # 全部写 ViewState 的**生效值**，不是传入值：
                    # 夹取之后的值才是真正参与计算的那一组。
                    "center_az_deg": view.center_az,
                    "center_alt_deg": view.center_alt,
                    "fov_deg": view.fov_deg,
                    "rotation_deg": view.rotation,
                    "min_fov_deg": view.min_fov,
                    "max_fov_deg": view.max_fov,
                    "width": w,
                    "height": h,
                },
                "points": pts,
            })

    return {
        "meta": {
            "schema": "mbdsdr.mobile.sky_projection_golden.v1",
            "generator": "scripts/build_sky_projection_golden.py",
            "generated_utc": _dt.datetime.now(_dt.timezone.utc)
                                  .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_of_truth": "mbdsdr_ai.sky_interaction.sky_to_screen "
                               "with PerspectiveProjection",
            "projection": "perspective",
            "tolerance_px": 0.5,
            "tolerance_reason": (
                "同一公式的两次实现，差只应来自 float32/float64 舍入；"
                "0.5 像素在显示意义上就是同一个位置"),
            "clamped_points_skipped": clamped_skipped,
            "clamped_skip_reason": (
                "桌面把画布外有限点钳到 ±4×短边（SCREEN_OFFSCREEN_CLAMP_FACTOR），"
                "手机端不钳、用真实坐标交给段裁剪；两种做法都对但数值不同，故不入本表"),
            "case_count": len(cases),
        },
        "cases": cases,
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成手机端坐标/投影黄金向量")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    transform = _build_transform_golden()
    tp = os.path.join(args.out, "sky_transform_golden.json")
    with open(tp, "w", encoding="utf-8") as fh:
        json.dump(transform, fh, ensure_ascii=False, indent=1)

    projection = _build_projection_golden()
    pp = os.path.join(args.out, "sky_projection_golden.json")
    with open(pp, "w", encoding="utf-8") as fh:
        json.dump(projection, fh, ensure_ascii=False, indent=1)

    n_pts = sum(len(c["points"]) for c in projection["cases"])
    print(f"sky_transform_golden.json: {transform['meta']['count']} 条")
    print(f"sky_projection_golden.json: {projection['meta']['case_count']} 组 / "
          f"{n_pts} 点")
    for path in (tp, pp):
        print(f"  {os.path.basename(path):<32} {os.path.getsize(path)/1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
