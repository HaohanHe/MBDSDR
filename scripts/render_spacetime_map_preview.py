# SPDX-License-Identifier: MIT
"""离线生成「时空要素画在地图上」的预览图，用于验收画布呈现。

- 不联网、不碰硬件、不读用户配置；
- 要素是**明确标注的示例数据**，不是真实观测；
- 直接渲染真实的 ``OsmMapView``（EPSG:3857 Web Mercator），**不自绘地图**；
  离线零瓦片时署名按公用组件的口径不显示，不伪造 OSM 来源；
- 输出默认落在仓库外 ``D:/mbdsdr/output/``，不入库。

可复跑： ``python scripts/render_spacetime_map_preview.py --out <png>``
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QFontDatabase, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai import spacetime_features as sf  # noqa: E402
from mbdsdr_ai.ground_track import (  # noqa: E402
    footprint_central_angle_deg, footprint_ring,
)
from osm_map import OsmMapView  # noqa: E402
from spacetime_map_layer import SpacetimeFeatureLayer  # noqa: E402

W, H = 900, 520
#: 中性示例站址（不是真实位置，图上会明写）。产品代码里不内置任何坐标。
_SAMPLE_LAT, _SAMPLE_LON = 45.0, 120.0


def _load_cjk_font() -> str:
    """offscreen Qt 不会自动加载 Windows 字体库，中文会渲染成方框。

    显式挂一份系统中文字体；挂不上就返回空串，让调用方知道别指望中文。
    """
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=r"D:\mbdsdr\output\spacetime_map_preview.png")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    family = _load_cjk_font()
    # 离屏环境不会自动匹配中文字体；显式指定，避免角标变成方框。
    if family:
        app.setFont(QFont(family, 9))

    at_unix = time.time()  # 不用自造的「好看时刻」
    collection = sf.FeatureCollection([
        sf.SpatialFeature(
            "sample_station", "ground_station", _SAMPLE_LAT, _SAMPLE_LON,
            label="示例·本站",
            properties={"note": "中性示例坐标，非真实位置"}),
        sf.SpatialFeature(
            "sample_beacon", "beacon", _SAMPLE_LAT + 3.0, _SAMPLE_LON + 4.0,
            label="示例·信标"),
        sf.SpatialFeature(
            "sample_rfi", "rfi", _SAMPLE_LAT - 14.0, _SAMPLE_LON - 20.0,
            label="示例·干扰源（圈外）"),
        sf.SpatialFeature(
            "sample_track", "adsb", _SAMPLE_LAT + 1.0, _SAMPLE_LON + 8.0,
            label="示例·移动台",
            track=((_SAMPLE_LAT - 1.0, _SAMPLE_LON + 2.0),
                   (_SAMPLE_LAT, _SAMPLE_LON + 5.0),
                   (_SAMPLE_LAT + 1.0, _SAMPLE_LON + 8.0))),
    ])
    angle = footprint_central_angle_deg(550.0, 5.0)
    ring = footprint_ring(_SAMPLE_LAT, _SAMPLE_LON, angle, points=72)
    footprint = sf.Footprint(_SAMPLE_LAT, _SAMPLE_LON,
                             angle, at_unix=at_unix - 90.0)

    # 真实地图视图：投影、瓦片策略、署名口径全部走公用组件。
    view = OsmMapView(online=False)
    view.resize(W, H)
    if family:
        view.setFont(QFont(family, 9))
    view.set_style("dark")          # 同一份 OSM 瓦片本地反相；离线时只显示底色
    view.set_view(_SAMPLE_LAT, _SAMPLE_LON, 2)

    def paint_ring(painter: QPainter, project) -> None:
        outline = QPen(QColor("#e8a33d"), 1.6, Qt.DashLine)
        fill = QColor("#e8a33d")
        fill.setAlpha(30)
        painter.setPen(outline)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(QPolygonF([project(lat, lon)
                                       for lat, lon in ring]))
        painter.setBrush(Qt.NoBrush)

    view.add_layer("preview_footprint", paint_ring)
    bus = sf.TemporalBus()
    layer = SpacetimeFeatureLayer(view, collection, bus=bus)
    bus.publish(sf.TemporalContext(
        at_unix, sf.TimeInterval(at_unix, at_unix),
        basis="simulated", mode=sf.TemporalMode.RANGE_FILTER,
        has_time_data=True))
    layer.set_footprint(footprint)

    # 诚实标注：示例数据 / 离线零瓦片（真实产品里由 OsmMapView 画瓦片+署名）
    def paint_note(painter: QPainter, _project) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#9fb0c8")))
        painter.drawText(12, H - 12,
                         "离线预览 · 示例要素（非真实观测）· 读本地缓存瓦片，"
                         "署名按口径显示 · 不显示真实站址")
        painter.setPen(QPen(QColor("#e8a33d")))
        painter.drawText(12, H - 30,
                         "覆盖圈中心角 %.2f°（alt 550 km / 仰角 5°）" % angle)

    view.add_layer("preview_note", paint_note)
    view.repaint()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not view.grab().save(str(out)):
        return 1
    print(f"saved: {out}")
    print("layers:", view.layer_names())
    print("states:", layer.result.counts)
    print("badge:", layer.result.summary())
    print("footprint_age_s:", layer.result.footprint_age_s)
    layer.close()
    view.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
