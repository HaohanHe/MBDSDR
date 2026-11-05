# SPDX-License-Identifier: MIT
"""
tests/test_sky_frequency_honesty.py
===================================
「目录缺 freq_mhz → 0.0」的两处造值点（rf_sky_view 目录映射与实时刷新）改成
``None``（显式「未登记」），并钉死所有消费方：一律先判 ``None`` 再比大小。

反证口径（改回旧行为必须红）：

1. 目录项无 ``freq_mhz`` → ``_sat_catalog[...]["freq_mhz"] is None``（旧: 0.0）；
2. 用户 TLE 文件未写 MHz → 同上（旧: 0.0）；
3. ``SatelliteTracker.refresh()`` 产出的 ``SkyObject.frequency_hz is None``
   （旧: ``0.0 * 1e6`` = 0 Hz）；
4. 未登记频率的多普勒必须是 ``None``，不是 0（旧: 返回 0.0）；
5. ``RFSkyView.grab()`` 画到未登记频率的卫星不得抛 ``TypeError``
   （旧: ``None > 0`` 直接崩）；
6. 主窗口点击卫星：未登记频率时 ``backend.set_frequency`` 一次都不许被调
   （旧: ``None > 0`` 崩；若退回 0.0 则会向 SDR 下发 0 Hz）。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_frequency_honesty.py -v
"""
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import desktop.rf_sky_view as rfsk  # noqa: E402
from desktop.rf_sky_view import (  # noqa: E402
    RFSkyView, SatelliteTracker, SkyObject, _positive_mhz_or_none,
)

# 固定历史 TLE（NOAA-19，epoch 2024-01-01），与 test_satellite_tracker.py 同源。
TLE_L1 = ("1 33591U 09005A   24001.50000000  .00000300  00000-0  16087-3 0  9990")
TLE_L2 = ("2 33591  99.1900 312.3500 0014200  80.0000 280.0000 14.11715000458920")
T_TEST = 1704153600.0  # 2024-01-02 00:00 UTC


def _new_view(size: int = 600) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
    return v


def test_helper_treats_missing_blank_zero_and_invalid_as_none():
    """唯一认可的「未登记」语义是 None：0/负/NaN/空串/鬼话都不许当频点。"""
    assert _positive_mhz_or_none(None) is None
    assert _positive_mhz_or_none("") is None
    assert _positive_mhz_or_none("not-a-number") is None
    assert _positive_mhz_or_none(0.0) is None
    assert _positive_mhz_or_none(-137.1) is None
    assert _positive_mhz_or_none(float("nan")) is None
    assert _positive_mhz_or_none(float("inf")) is None
    assert _positive_mhz_or_none("137.620") == pytest.approx(137.62)


def test_catalog_entry_without_freq_is_none_not_zero():
    """目录缺 freq_mhz / 显式 0.0 → 元数据 None（旧行为会落 0.0）。"""
    view = _new_view()
    view.set_satellite_catalog([
        {"name": "NOAA 19", "line1": TLE_L1, "line2": TLE_L2, "kind": "weather"},
        {"name": "NOAA 18", "line1": TLE_L1, "line2": TLE_L2,
         "freq_mhz": 0.0, "kind": "weather"},
        {"name": "NOAA 15", "line1": TLE_L1, "line2": TLE_L2,
         "freq_mhz": 137.62, "kind": "weather"},
    ])
    assert view._sat_catalog["NOAA 19"]["freq_mhz"] is None
    assert view._sat_catalog["NOAA 18"]["freq_mhz"] is None
    assert view._sat_catalog["NOAA 15"]["freq_mhz"] == pytest.approx(137.62)
    view.close()


def test_user_tle_file_without_mhz_is_unregistered(tmp_path, monkeypatch):
    """用户 TLE 名称行不带 MHz → freq_mhz None（旧行为会落 0.0）。"""
    tle_dir = tmp_path / ".mbdsdr" / "tle"
    tle_dir.mkdir(parents=True)
    (tle_dir / "noaa19.tle").write_text(
        "NOAA 19\n" + TLE_L1 + "\n" + TLE_L2 + "\n", encoding="utf-8")
    monkeypatch.setattr(rfsk.os.path, "expanduser",
                        lambda p: str(tle_dir) if p == "~/.mbdsdr/tle" else p)
    cat = rfsk._load_user_tle_catalog()
    assert len(cat) == 1
    assert cat[0]["name"] == "NOAA 19"
    assert cat[0]["freq_mhz"] is None


@pytest.fixture()
def tracker_env(monkeypatch):
    """隔离网络/用户目录：目录由测试手写，时间/日月/过境全部打桩。"""
    monkeypatch.setattr(rfsk, "_load_user_tle_catalog", lambda: [])
    monkeypatch.setattr(rfsk, "_load_celestrak_tle_catalog", lambda: [])
    view = _new_view()
    tr = SatelliteTracker(view, None, None)
    tr._catalog = []
    monkeypatch.setattr(tr, "_engine_unix", lambda: T_TEST)
    monkeypatch.setattr(tr, "_compute_sun_moon", lambda _t: (None, None, 0.0))
    monkeypatch.setattr(tr, "_maybe_refresh_passes", lambda *a, **k: None)
    tr.lat, tr.lon, tr.alt_km = 40.0, 116.0, 0.0
    yield tr, view
    view.close()


def test_tracker_refresh_without_freq_yields_none(tracker_env):
    """实时刷新链（旧 :7603）→ SkyObject.frequency_hz 必须是 None。"""
    tr, view = tracker_env
    tr._catalog = [{"name": "NOAA 19", "line1": TLE_L1, "line2": TLE_L2,
                    "kind": "weather"}]
    tr.refresh()
    objs = [o for o in view.sky_objects if o.name == "NOAA 19"]
    assert len(objs) == 1
    assert objs[0].frequency_hz is None
    assert view._sat_catalog["NOAA 19"]["freq_mhz"] is None


def test_tracker_refresh_with_freq_keeps_real_hz(tracker_env):
    """正向对照：登记了 137.62 MHz 就必须是 137.62e6 Hz（不是被 None 吃掉）。"""
    tr, view = tracker_env
    tr._catalog = [{"name": "NOAA 15", "line1": TLE_L1, "line2": TLE_L2,
                    "freq_mhz": 137.62, "kind": "weather"}]
    tr.refresh()
    objs = [o for o in view.sky_objects if o.name == "NOAA 15"]
    assert len(objs) == 1
    assert objs[0].frequency_hz == pytest.approx(137.62e6)


def test_doppler_of_unregistered_frequency_is_none_not_zero():
    """未登记频率不参与多普勒：返回 None（旧行为按 0 Hz 算出一个 0.0）。"""
    obj = SkyObject(name="NOAA 19", azimuth_deg=0.0, elevation_deg=45.0,
                    frequency_hz=None, description="径向速度 2.0 km/s")
    assert RFSkyView._estimate_doppler_hz(obj) is None
    obj_ok = SkyObject(name="NOAA 19", azimuth_deg=0.0, elevation_deg=45.0,
                       frequency_hz=137.62e6, description="径向速度 2.0 km/s")
    dop = RFSkyView._estimate_doppler_hz(obj_ok)
    assert dop is not None and dop < 0.0
    assert dop == pytest.approx(-2.0 * 1000.0 * 137.62e6 / 299792458.0)


def test_draw_with_unregistered_frequency_does_not_crash():
    """绘制（旧 :5201 的 ``None > 0``）不得抛 TypeError。"""
    view = _new_view()
    view.set_objects([
        SkyObject(name="NOAA 19", azimuth_deg=0.0, elevation_deg=45.0,
                  frequency_hz=None, obj_type="satellite", description=""),
        SkyObject(name="NOAA 15", azimuth_deg=180.0, elevation_deg=30.0,
                  frequency_hz=137.62e6, obj_type="satellite", description=""),
    ])
    pixmap = view.grab()  # 触发 paintEvent
    assert not pixmap.isNull()
    view.close()


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _Bar:
    def __init__(self):
        self.messages = []

    def showMessage(self, msg, *args):
        self.messages.append(msg)


class _SkyStub:
    def __init__(self):
        self.antenna = None

    def set_antenna(self, antenna):
        self.antenna = antenna


def _fake_window(backend):
    win = types.SimpleNamespace()
    win.sky_view = _SkyStub()
    win._active_sdr_backend = backend
    win._worker = None
    win.freq_label = _Label()
    win.status_freq = _Label()
    win._vfo_center_hz = 98e6
    win._backend_center_hz = 98e6
    win._update_main_vfo_offset = lambda: None
    bar = _Bar()
    win.statusBar = lambda: bar
    win._status_bar = bar
    return win


def test_main_window_click_unregistered_freq_never_sends_zero_hz():
    """未登记频率：不向 SDR 下发任何频率（尤其不是 0 Hz），并明说缺频。"""
    from desktop import main_window as mw

    backend = MagicMock()
    win = _fake_window(backend)
    obj = SkyObject(name="NOAA 19", azimuth_deg=180.0, elevation_deg=45.0,
                    frequency_hz=None, obj_type="satellite")
    mw.MainWindow._on_sky_object_clicked(win, obj)
    backend.set_frequency.assert_not_called()
    assert win.freq_label.text == "" and win.status_freq.text == ""
    assert win._status_bar.messages
    assert "没有配置下行频率" in win._status_bar.messages[-1]


def test_main_window_click_registered_freq_tunes_real_hz():
    """正向对照：登记频率时下发的是 137.1e6 Hz，不是 0。"""
    from desktop import main_window as mw

    backend = MagicMock()
    win = _fake_window(backend)
    obj = SkyObject(name="NOAA 19", azimuth_deg=180.0, elevation_deg=45.0,
                    frequency_hz=137.1e6, obj_type="satellite")
    mw.MainWindow._on_sky_object_clicked(win, obj)
    backend.set_frequency.assert_called_once_with(pytest.approx(137.1e6))
    assert win.freq_label.text.startswith("137.1")
