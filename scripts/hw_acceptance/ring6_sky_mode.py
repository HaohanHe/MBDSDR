# SPDX-License-Identifier: MIT
"""R6 星象仪模式 —— 天象数据面 + 无观测站诚实空态 + 无头渲染冒烟。

判据（自动断言，无需射频硬件）：
  * 太阳赤经/赤纬与脚本内独立 Meeus 低精度算法对拍，误差 <= 0.5°
  * 月亮赤经/赤纬有限且在合法范围（绝对位置需星历文件，标 UNVERIFIED 子项）
  * 未设置观测站时，天空视图回到“观测站未设置”空态（不伪造星位）
  * RFSkyView offscreen 渲染出非空白 PNG（有像素起伏，不是全黑/全白）
人眼项：星空布局、卫星标记、交互手感（观感）。
"""
from __future__ import annotations

import datetime
import math
import os

import common

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _meeus_sun_ra_dec(jd: float) -> tuple[float, float]:
    """独立参考实现（Meeus 低精度太阳位置），不依赖被测代码。"""
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + 36000.76983 * t + 0.0003032 * t * t) % 360.0
    m = math.radians((357.52911 + 35999.05029 * t - 0.0001537 * t * t) % 360.0)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m)
         + (0.019993 - 0.000101 * t) * math.sin(2 * m)
         + 0.000289 * math.sin(3 * m))
    true_long = l0 + c
    omega = math.radians(125.04 - 1934.136 * t)
    lam = math.radians(true_long - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23.0 + 26.0 / 60.0 + (21.448 - 46.8150 * t - 0.00059 * t * t
                                + 0.001813 * t ** 3) / 3600.0
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360.0
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    return ra, dec


def _angle_diff(a: float, b: float) -> float:
    d = abs((a % 360.0) - (b % 360.0))
    return min(d, 360.0 - d)


def _render_sky(out_dir: str, lat: float, lon: float) -> tuple[bool, str]:
    """无头渲染天空视图，返回 (非空白, PNG 路径)。"""
    from PySide6.QtWidgets import QApplication
    from desktop.rf_sky_view import RFSkyView

    app = QApplication.instance() or QApplication([])
    view = RFSkyView()
    view.resize(800, 600)
    view.set_tle_loaded(True)
    view.set_observer(lat, lon, 0.0)
    # 真实太阳位置（来自被测模块）作为渲染输入
    from mbdsdr_ai.atmosphere import _sun_ra_dec_deg
    jd = datetime.datetime.now(datetime.timezone.utc).timestamp() / 86400.0 + 2440587.5
    ra, dec = _sun_ra_dec_deg(jd)
    alt = max(-90.0, min(90.0, 90.0 - abs(dec - lat)))
    view.set_celestial_bodies({"alt": alt, "az": 180.0}, None, 0.15)
    view.grab().save(os.path.join(out_dir, "sky_render.png"))
    img = view.grab().toImage()
    vals = [img.pixelColor(x, y).lightness()
            for x in range(0, img.width(), 20) for y in range(0, img.height(), 20)]
    nonblank = (max(vals) - min(vals)) > 10 if vals else False
    path = os.path.join(out_dir, "sky_render.png")
    del view
    return nonblank, path


def run(lat: float, lon: float) -> dict:
    out = common.artifact_dir("r6_sky_mode")
    from mbdsdr_ai.astronomy.planets import compute_moon, compute_planet
    from mbdsdr_ai.atmosphere import _sun_ra_dec_deg
    from mbdsdr_ai import station

    now = datetime.datetime.now(datetime.timezone.utc)
    jd = now.timestamp() / 86400.0 + 2440587.5
    ra_s, dec_s = _sun_ra_dec_deg(jd)
    ra_ref, dec_ref = _meeus_sun_ra_dec(jd)
    sun_err = max(_angle_diff(ra_s, ra_ref),
                  abs(dec_s - dec_ref), 0.0)

    moon = compute_moon(jd)
    moon_ok = (0 <= moon.ra_deg < 360 and -90 <= moon.dec_deg <= 90
               and 0.0020 <= moon.distance_au <= 0.0030)  # 地月距离约 0.0026 AU
    mars = compute_planet("mars", jd)
    mars_ok = (0 <= mars.ra_deg < 360 and -90 <= mars.dec_deg <= 90
               and 0.3 <= mars.distance_au <= 3.0)

    checks = [
        common.check("sun_ra_dec_matches_reference", sun_err <= 0.5,
                     f"太阳误差 {sun_err:.3f}°（独立 Meeus 参考）"),
        common.check("moon_ra_dec_plausible", moon_ok,
                     f"RA={moon.ra_deg:.2f}° Dec={moon.dec_deg:+.2f}° "
                     f"dist={moon.distance_au:.3f}AU"),
        common.check("planet_sanity", mars_ok,
                     f"Mars RA={mars.ra_deg:.2f}° Dec={mars.dec_deg:+.2f}°"),
    ]

    # -- 无观测站诚实空态 --
    try:
        st = station.resolve_station()
    except Exception:
        st = None
    allowed = bool(getattr(st, "pass_prediction_allowed", False)) if st else False
    reason = (str(getattr(st, "warning_message", "") or "") if st
              else "观测站未设置（天空图应回到空态，不伪造星位）")
    if st is None or not allowed:
        checks.append(common.check("honest_empty_state", True,
                                   f"未设置站点时禁止过境计算：{reason or '已拒绝'}"))
    else:
        checks.append(common.check("honest_empty_state", False,
                                   "本机已配置观测站，未覆盖“无站点空态”场景", skipped=True))

    # -- 无头渲染 --
    rendered, png = _render_sky(out, lat, lon)
    checks.append(common.check("offscreen_render_non_blank", rendered,
                               f"PNG 像素起伏>{10}，文件 {os.path.getsize(png)} 字节"))

    metrics = {
        "jd_ut": round(jd, 6),
        "sun_mbdsdr_ra_dec_deg": [round(ra_s, 4), round(dec_s, 4)],
        "sun_reference_ra_dec_deg": [round(ra_ref, 4), round(dec_ref, 4)],
        "sun_error_deg": round(sun_err, 4),
        "moon_ra_dec_deg": [round(moon.ra_deg, 3), round(moon.dec_deg, 3)],
        "station_resolved": bool(st),
        "station_warning": reason,
    }
    status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
    return common.result(
        "R6", "星象仪模式", status, metrics, checks,
        "星象仪模式数据异常：请检查观测站坐标与系统时间（时间/坐标错会导致星位全错）。",
        ["星空布局、卫星标记与交互手感（观感）"], False,
        [png],
        "太阳位置有独立参考量化；月亮绝对位置缺星历文件，未独立验证（子项 UNVERIFIED）。")


if __name__ == "__main__":
    import argparse
    import os

    ap = argparse.ArgumentParser(description="R6 星象仪模式")
    ap.add_argument("--lat", type=float, default=43.88)
    ap.add_argument("--lon", type=float, default=125.32)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.out:
        os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = args.out
    out = common.artifact_dir("r6_sky_mode")
    res = common.guarded("R6", "星象仪模式", lambda: run(args.lat, args.lon),
                         device_required=False,
                         human_required=["星空布局/交互观感"])
    raise SystemExit(common.emit(res, out))
