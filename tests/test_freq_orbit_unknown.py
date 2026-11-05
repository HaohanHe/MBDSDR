# SPDX-License-Identifier: MIT
"""频率未知的卫星不得被当成 0 MHz 画到坐标 0 处。

修前：``compute_freq_orbit`` 用 ``0.0`` 兼任「无频率数据」，散点图把它画在
Y=0 MHz，与真实频点混在一起。改用 ``None``（真实缺省）表示未知。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
NOAA_L1 = "1 25338U 98030A   08264.51782528 -.00000022  00000-0  10000-3 0  2345"
NOAA_L2 = "2 25338  98.7452  34.5678 0009876 123.4567 236.6289 14.25987654321012"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _engine():
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    return TimelineEngine()


def test_unknown_frequency_is_none_not_zero():
    eng = _engine()
    pts = eng.compute_freq_orbit(
        [(ISS_NAME, ISS_L1, ISS_L2), ("NOAA 15", NOAA_L1, NOAA_L2)],
        frequencies={ISS_NAME: 137.1e6},
    )
    assert len(pts) == 2
    by_name = {p.name: p for p in pts}
    assert by_name[ISS_NAME].frequency_mhz == pytest.approx(137.1), (
        "已知频点必须照常保留")
    assert by_name["NOAA 15"].frequency_mhz is None, (
        "频率未知必须是 None；0.0 是合法数值，不能兼任「未知」")
    assert by_name["NOAA 15"].has_frequency is False
    assert by_name[ISS_NAME].has_frequency is True


def test_unknown_point_is_not_plotted_at_zero(qapp):
    from desktop.new_spacetime_panel import FreqOrbitChart
    from mbdsdr_ai.new_spacetime_timeline import FreqOrbitPoint

    known = FreqOrbitPoint("ISS (ZARYA)", 137.1, 420.0, 51.6, 92.0, 0.0007, 25544)
    unknown = FreqOrbitPoint("NOAA 15", None, 850.0, 98.7, 102.0, 0.001, 25338)
    chart = FreqOrbitChart()
    chart.set_data([known, unknown])

    plotted = chart.plottable_points()
    assert plotted == [known], (
        "频率未知的点不得进入绘制循环（否则会落在 Y=0 MHz 的坐标 0 处）")
    assert chart.has_data() is True
    chart.set_data([unknown])
    assert chart.plottable_points() == []
    assert chart.has_data() is False
