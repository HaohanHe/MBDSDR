# SPDX-License-Identifier: MIT
"""
tests/test_meteor_showers.py
============================
流星雨目录 + 星图辐射点图层。

数据是 IMO 公开年历的典型值（峰值日期/ZHR 逐年漂移），这里只锁三件事：
  1. 目录自身自洽（代号唯一、辐射点/活动期范围合法、峰值在活动期内）；
  2. 活动包络（含跨年）按三角近似：峰值 1、界外 0，且 `active_showers`
     只返回活动项、按强度降序；
  3. 星图辐射点与恒星/DSO 同一套门：白天不画、地平线下不画、无站点不画，
     活动期且高于地平线才落笔并进避让队列。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai.astronomy import meteor_showers as _ms  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from rf_sky_view import RFSkyView  # noqa: E402

LAT, LON = 40.0, 116.0
PERSEID_NIGHT = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
PERSEID_UNIX = PERSEID_NIGHT.timestamp()


def _by_code(code: str) -> _ms.MeteorShower:
    for s in _ms.SHOWERS:
        if s.code == code:
            return s
    raise AssertionError(code)


# --------------------------------------------------------------------------
# 1. 目录自洽
# --------------------------------------------------------------------------
def test_radiants_match_independent_table():
    """辐射点与本机 Stellarium 插件表独立对照过（2026-11-17）。

    GEM/STA/URS 三处赤纬原先各差 1–2°，对照后修正；这里把 12 场一起钉住，
    防止以后凭印象再改回去。
    """
    expected = {
        "QUA": (230.0, 49.0), "LYR": (271.0, 34.0), "ETA": (338.0, -1.0),
        "SDA": (340.0, -16.0), "PER": (48.0, 58.0), "DRA": (262.0, 54.0),
        "ORI": (95.0, 16.0), "STA": (52.0, 15.0), "NTA": (58.0, 22.0),
        "LEO": (152.0, 22.0), "GEM": (112.0, 33.0), "URS": (217.0, 76.0),
    }
    assert set(expected) == {s.code for s in _ms.SHOWERS}
    for code, (ra, dec) in expected.items():
        s = _by_code(code)
        assert (s.ra_deg, s.dec_deg) == (ra, dec), code


def test_catalog_integrity():
    assert len(_ms.SHOWERS) == 12
    assert len({s.code for s in _ms.SHOWERS}) == 12
    for s in _ms.SHOWERS:
        assert 0.0 <= s.ra_deg < 360.0, s.code
        assert -90.0 <= s.dec_deg <= 90.0, s.code
        assert s.zhr_peak > 0 and s.velocity_kms > 0, s.code
        assert s.parent, s.code
        # 峰值必须落在活动期内（含端点）
        assert _ms.activity_fraction(s, s.peak_month, s.peak_day) == pytest.approx(1.0)


def test_major_showers_present():
    codes = {s.code for s in _ms.SHOWERS}
    assert {"PER", "GEM", "QUA", "LEO", "ORI"} <= codes


# --------------------------------------------------------------------------
# 2. 活动包络（含跨年）
# --------------------------------------------------------------------------
class TestActivity:
    def test_peak_is_one(self):
        assert _ms.activity_fraction(_by_code("PER"), 8, 12) == pytest.approx(1.0)

    def test_mid_window_between_zero_and_one(self):
        f = _ms.activity_fraction(_by_code("PER"), 8, 1)
        assert 0.0 < f < 1.0
        # 越靠近峰值越强（单调性；8-01 → 8-07）
        assert _ms.activity_fraction(_by_code("PER"), 8, 7) > f

    def test_outside_window_is_zero(self):
        assert _ms.activity_fraction(_by_code("PER"), 1, 1) == 0.0
        assert _ms.activity_fraction(_by_code("GEM"), 7, 1) == 0.0

    def test_cross_year_shower_not_split(self):
        """象限仪座 12-28 → 1-12：跨年不能断成两段。"""
        qua = _by_code("QUA")
        assert _ms.activity_fraction(qua, 1, 3) == pytest.approx(1.0)
        assert _ms.activity_fraction(qua, 12, 30) > 0.0
        assert _ms.activity_fraction(qua, 1, 10) > 0.0
        assert _ms.activity_fraction(qua, 12, 20) == 0.0
        assert _ms.activity_fraction(qua, 1, 20) == 0.0

    def test_active_list_sorted_and_filtered(self):
        active = _ms.active_showers(PERSEID_NIGHT)
        assert active, "8-12 至少应有英仙座流星雨"
        fr = [f for _s, f in active]
        assert fr == sorted(fr, reverse=True)
        assert active[0][0].code == "PER"
        # 3 月下旬是 12 场雨的空档
        assert _ms.active_showers(datetime(2026, 3, 25, tzinfo=timezone.utc)) == []

    def test_describe_does_not_invent_hourly_rate(self):
        txt = _ms.describe(_by_code("PER"), 1.0)
        assert "英仙座流星雨" in txt and "峰值 ZHR 100" in txt
        assert "每小时" not in txt


# --------------------------------------------------------------------------
# 3. 星图图层
# --------------------------------------------------------------------------
def _view(unix: float = PERSEID_UNIX) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(unix)
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = 120.0
    v._sky_brightness = 0.0
    return v


def _render(v: RFSkyView) -> QImage:
    img = QImage(700, 700, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    try:
        v._label_items = []
        v._draw_meteor_radiants(p)
    finally:
        p.end()
    return img


def _ink(img: QImage) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 90:
                n += 1
    return n


def test_radiant_drawn_on_peak_night():
    v = _view()
    try:
        ink = _ink(_render(v))
        labels = [ln for _pri, _pos, lines, _col in v._label_items
                  for ln, _sm, _dy in lines]
    finally:
        v.close()
    assert ink > 0, "英仙座峰值夜、辐射点在地平线上却没画"
    assert any("英仙座流星雨" in t for t in labels), labels


def test_radiant_hidden_by_daylight():
    v = _view()
    try:
        v.set_celestial_bodies({"az": 180.0, "alt": 30.0}, None, 0.0)
        assert _ink(_render(v)) == 0
    finally:
        v.close()


def test_radiant_requires_observer():
    v = _view()
    try:
        v._observer = None
        assert _ink(_render(v)) == 0
    finally:
        v.close()


def test_no_radiant_outside_shower_window():
    """3 月下旬（12 场雨空档）整层不画。"""
    v = _view(datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc).timestamp())
    try:
        assert _ink(_render(v)) == 0
        assert v._label_items == []
    finally:
        v.close()
