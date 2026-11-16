# SPDX-License-Identifier: MIT
"""
scripts/render_star_overlay_preview.py — 「线落在星上」离屏证据
==============================================================

做什么：把视图指向绑定到北斗（模拟望远镜解算出的真实光轴），打开中国星官
图层，各截一张图：

    宽视场 25°  —— 看整把勺子连起来
    窄视场 3°   —— 看只装得下一两颗时，线怎么往画面外延伸

产出 PNG 落在 ``output/star_overlay_preview/``。这是给"钦一下"看的证据，
不是产品功能：脚本不参与打包，也不改仓库里任何数据。

纪律：
  * 站址用中性示例站（45N/120E），**不指向任何真实观测者**；
  * 时刻固定为 2026-11-16 21:00 CST（天文夜，太阳高度足够低，恒星才画）；
  * 隔离 HOME/APPDATA，避免污染真实配置；
  * 解算结论一律标 ``not_verified``——这里验证的是"绑定与绘制"，不是解算精度。

用法：
    python scripts/render_star_overlay_preview.py
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile

# ---- 环境隔离（必须在导入 Qt / 项目模块之前）-----------------------------
_SANDBOX = os.path.join(tempfile.gettempdir(), "mbdsdr_overlay_preview")
for _var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
    os.environ[_var] = _SANDBOX
os.makedirs(_SANDBOX, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _load_cjk_font() -> str:
    """offscreen Qt 不会自动加载系统中文字体，不挂就是一片方框。"""
    for path in (r"C:\Windows\Fonts\msyh.ttc",
                 r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\simsun.ttc"):
        if not os.path.exists(path):
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                return str(families[0])
    return ""


_FAMILY = _load_cjk_font()
if _FAMILY:
    app.setFont(QFont(_FAMILY, 9))

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation, azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine.xingguan import get_xingguan  # noqa: E402

LAT, LON, ALT_M = 45.0, 120.0, 200.0          # 中性示例站，非真实观测点
FIXED_UTC = datetime.datetime(2026, 11, 16, 13, 0, tzinfo=datetime.timezone.utc)
OUT_DIR = os.path.abspath(os.path.join(_ROOT, os.pardir, "output",
                                      "star_overlay_preview"))
"""证据落在**仓库外**（与项目既有证据纪律一致），不进版本库。"""


def _beidou():
    for r in get_xingguan().regions:
        for g in r.xingguan:
            if g.name_zh == "北斗":
                return g
    raise SystemExit("星官数据里找不到北斗")


def _star_azalt(star, gs: GroundStation, jd: float):
    mat = j2000_to_altaz_many(
        vec_from_radec(float(star.ra_deg), float(star.dec_deg))[None, :], gs, jd)
    az, alt = azalt_from_vec_many(mat)
    return float(az[0]), float(alt[0])


def _beam_center(g, gs: GroundStation, jd: float):
    """北斗七星的几何中心（重矢量和，避免 az 环绕把中心算错）。"""
    vecs = []
    for poly in g.lines:
        for s in poly:
            if s.ra_deg is None:
                continue
            vecs.append(j2000_to_altaz_many(
                vec_from_radec(float(s.ra_deg), float(s.dec_deg))[None, :],
                gs, jd)[0])
    m = np.mean(np.vstack(vecs), axis=0)
    az, alt = azalt_from_vec_many(m[None, :])
    return float(az[0]), float(alt[0])


def _new_view(size: int = 900) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, ALT_M)
    v.set_layer("constellations", True)
    v.set_chinese_layer(True)
    return v


def _render(v: RFSkyView, path: str) -> str:
    v.repaint()
    QApplication.processEvents()
    pix = v.grab()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pix.save(path)
    return path


def main() -> int:
    engine = get_time_engine()
    engine.set_unix(FIXED_UTC.timestamp())
    engine.pause()

    gs = GroundStation(LON, LAT, ALT_M, "示例站")
    jd = engine.julian_date_ut1() if hasattr(engine, "julian_date_ut1") else None
    if jd is None:
        from mbdsdr_ai.new_spacetime import utc_to_jd
        jd = utc_to_jd(FIXED_UTC)

    g = _beidou()
    first = g.lines[0][0]
    star_az, star_alt = _star_azalt(first, gs, jd)
    mid_az, mid_alt = _beam_center(g, gs, jd)

    print(f"时刻 {FIXED_UTC:%Y-%m-%dT%H:%M:%SZ}  jd_ut1={jd:.5f}")
    print(f"站址 {LAT}N/{LON}E/{ALT_M}m（中性示例站，非真实观测点）")
    print(f"北斗首星 {first.zh_name or first.label}: az={star_az:.2f}° alt={star_alt:.2f}°")
    print(f"北斗中心: az={mid_az:.2f}° alt={mid_alt:.2f}°")

    shots = []

    # 1) 宽视场：整把勺子
    v = _new_view()
    ok = v.bind_pointing(mid_az, mid_alt, 25.0, source="preview:wide",
                         uncertainty_deg=0.0)
    print(f"\n[宽视场] 绑定={'成功' if ok else '失败'} fov=25°")
    res = v.overlay_for_current_view(site=gs, jd_ut1=jd)
    print(f"  drawable={res.drawable} 星官={res.visible_count} 段={res.total_segments} "
          f"裁剪={res.clipped_segments}")
    if res.drawable:
        bd = [a for a in res.asterisms if a.name == "北斗"]
        if bd:
            print(f"  北斗: {len(bd[0].segments)} 段, 成员 {bd[0].members_in_view}"
                  f"/{bd[0].members_total} 在视场内")
    shots.append(_render(v, os.path.join(OUT_DIR, "wide_25deg_beidou.png")))

    # 2) 窄视场：只看一颗，线往画面外延伸
    v2 = _new_view()
    ok2 = v2.bind_pointing(star_az, star_alt, 3.0, source="preview:narrow",
                           uncertainty_deg=0.0)
    print(f"\n[窄视场] 绑定={'成功' if ok2 else '失败'} fov=3°")
    res2 = v2.overlay_for_current_view(site=gs, jd_ut1=jd)
    print(f"  drawable={res2.drawable} 星官={res2.visible_count} 段={res2.total_segments} "
          f"裁剪={res2.clipped_segments}")
    if res2.drawable:
        bd = [a for a in res2.asterisms if a.name == "北斗"]
        if bd:
            states = sorted({s.clip for s in bd[0].segments})
            print(f"  北斗: {len(bd[0].segments)} 段, clip={states}"
                  f", 成员 {bd[0].members_in_view}/{bd[0].members_total} 在视场内")
    shots.append(_render(v2, os.path.join(OUT_DIR, "narrow_3deg_beidou.png")))

    print("\n产出：")
    for p in shots:
        print("  " + p)
    print("\nnot_verified：本脚本验证的是「指向绑定 + 叠加绘制」，不是解算精度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
