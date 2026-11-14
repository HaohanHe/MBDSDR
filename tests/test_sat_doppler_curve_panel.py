# SPDX-License-Identifier: MIT
"""过境详情 f_d(t) 曲线：面板只画宿主给的采样，缺数据写明原因。

验收口径：
  1. provider 行里的 ``doppler``/``tca_unix``/``freq_hz`` 原样进入绘图控件，
     按时间升序；AOS/TCA/LOS 标线取同一行；
  2. 没有采样点时不画「零偏移」假曲线，note 说明原因；
  3. 曲线频率与当前输入不一致时明说「下次取数后更新」，不静默冒充新频率；
  4. 换星/失效清空曲线，不把上一颗星的曲线留在界面上。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

#: 固定时基（2027-01-15T08:00:00Z），不依赖跑测时的真实时钟。
BASE = 1_800_000_000.0
FREQ_HZ = 437_000_000.0


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _panel(names=("ISS",)):
    from desktop.sat_track_panel import SatTrackPanel
    p = SatTrackPanel()
    p.populate_satellites(list(names))
    p.set_downlink_mhz(FREQ_HZ / 1e6)
    return p


def _curve():
    return [
        {"t": BASE + 100, "fd": -3200.0},
        {"t": BASE + 150, "fd": 0.0},
        {"t": BASE + 250, "fd": 4200.0},
        {"t": BASE + 400, "fd": -1500.0},
    ]


def _row(**extra):
    row = {"aos_unix": BASE + 100, "los_unix": BASE + 400, "max_alt": 42.0,
           "tca_unix": BASE + 250, "freq_hz": FREQ_HZ,
           "doppler": _curve()}
    row.update(extra)
    return row


def test_provider_curve_reaches_plot_with_markers(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [_row()])
    p.update_position({"valid": False}, now_unix=BASE)

    pts = p.doppler_plot.curve_points()
    assert [round(t, 3) for t, _ in pts] == [BASE + 100, BASE + 150,
                                             BASE + 250, BASE + 400]
    assert [round(fd, 3) for _, fd in pts] == [-3200.0, 0.0, 4200.0, -1500.0]
    assert p.doppler_plot.markers() == pytest.approx(
        {"aos": BASE + 100, "tca": BASE + 250, "los": BASE + 400})
    assert p.doppler_plot.freq_hz() == pytest.approx(FREQ_HZ)
    assert "437.0000 MHz" in p.doppler_plot.note(), p.doppler_plot.note()

    # 真正走一遍 paintEvent：绘制路径不能抛异常。
    p.doppler_plot.resize(320, 170)
    assert not p.doppler_plot.grab().isNull()


def test_missing_samples_says_why_instead_of_fake_zero_line(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [_row(doppler=[])])
    p.update_position({"valid": False}, now_unix=BASE)

    assert p.doppler_plot.curve_points() == []
    assert "没有多普勒采样点" in p.doppler_plot.note(), p.doppler_plot.note()


def test_missing_frequency_is_reported_not_assumed(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [_row(doppler=[], freq_hz=None)])
    p.update_position({"valid": False}, now_unix=BASE)

    assert p.doppler_plot.curve_points() == []
    assert "未提供下行频率" in p.doppler_plot.note(), p.doppler_plot.note()


def test_frequency_mismatch_marks_curve_as_stale(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [_row()])
    p.update_position({"valid": False}, now_unix=BASE)

    p.set_downlink_mhz(145.0)
    p.update_position({"valid": False}, now_unix=BASE)
    note = p.doppler_plot.note()
    assert "437.0000 MHz" in note and "下次取数后更新" in note, note
    assert "145.0000 MHz" in note, note


def test_invalidate_clears_stale_curve(qapp, isolated_profile):
    p = _panel()
    p.set_pass_provider(lambda name: [_row()])
    p.update_position({"valid": False}, now_unix=BASE)
    assert p.doppler_plot.curve_points()

    p.invalidate_passes()
    assert p.doppler_plot.curve_points() == []
    assert p.doppler_plot.note(), "清空后应保留说明，而不是空白"


def test_plot_filters_malformed_and_sorts(qapp, isolated_profile):
    from desktop.sat_track_panel import DopplerCurvePlot
    w = DopplerCurvePlot()
    w.set_curve([
        {"t": BASE + 20, "fd": 10.0},
        {"t": "bad", "fd": 1.0},
        {"t": BASE + 10, "fd": 5.0},
        {"t": BASE + 30, "fd": float("nan")},
        {"nope": 1},
    ], note="")
    assert [round(t, 3) for t, _ in w.curve_points()] == [BASE + 10, BASE + 20]
    assert w.note() == "", "有有效采样时不应再显示缺数据说明"


# 仓库内离线 TLE（experiments/satellite_rx/data/iss.tle，历元 2026-297）
ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)


def test_main_window_provider_emits_tca_freq_and_doppler(qapp, isolated_profile):
    """宿主 provider 必须一次给出 AOS/TCA/LOS + 频率 + 整条 f_d(t)。"""
    from types import SimpleNamespace

    from desktop.main_window import MainWindow
    from desktop.sat_track_panel import SatTrackPanel
    from mbdsdr_ai.sat_passes import GroundStation

    panel = SatTrackPanel()
    panel.populate_satellites(["ISS"])
    panel.set_downlink_mhz(FREQ_HZ / 1e6)
    gs = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0)
    tracker = SimpleNamespace(
        ground_station=gs,
        list_satellites=lambda: [{"name": "ISS", "tle": list(ISS_TLE)}])
    fake = SimpleNamespace(_loop_sat_tracker=tracker,
                           _observer_lat=None, _observer_lon=None,
                           settings={}, sat_track_panel=panel)

    rows = MainWindow._provide_sat_passes(fake, "ISS")
    assert rows, "北京站 24h 内应有 ISS 过境"
    row = rows[0]
    assert row["aos_unix"] <= row["tca_unix"] <= row["los_unix"]
    assert row["freq_hz"] == pytest.approx(FREQ_HZ)
    assert len(row["doppler"]) == 61
    assert row["doppler"][0]["t"] == pytest.approx(row["aos_unix"], abs=1.0)
    assert row["doppler"][-1]["t"] == pytest.approx(row["los_unix"], abs=1.0)
    # 过境两端多普勒异号、峰值不为零：曲线不是一条直线
    assert row["doppler"][0]["fd"] * row["doppler"][-1]["fd"] < 0
    assert max(abs(pt["fd"]) for pt in row["doppler"]) > 100.0

    panel.set_pass_provider(lambda name: rows)
    panel.update_position({"valid": False}, now_unix=row["aos_unix"] + 1.0)
    assert len(panel.doppler_plot.curve_points()) == 61
    assert "437.0000 MHz" in panel.doppler_plot.note()

