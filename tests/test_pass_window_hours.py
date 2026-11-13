# SPDX-License-Identifier: MIT
"""过境预测窗口 `pass_window_hours` 必须真正生效（A2）。

修前红：两处过境列表都把窗口写死 24 h——
- `desktop/new_spacetime_panel.py:refresh_passes` 的 `hours_ahead=24.0`
- `desktop/rf_sky_view.py:_refresh_passes` 的 `PASS_WIN_HOURS=24.0`
于是设 6 h 与设 24 h 返回条数完全相同，1 h / 72 h 也只是数字不落地。

固定用例（离线、不读用户配置）：
- TLE：ISS，epoch 2008-264（确定性）
- 站点：39.9°N / 116.4°E / 0 km
- 基准时刻：2008-09-20 06:00:00 UTC
- 该窗口内首个过境 AOS ≈ 10:38:30Z；10° 门在 24 h 内共 5 条
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pin_engine(fixed=FIXED_UTC):
    """把全局 TimeEngine 钉在固定时刻（F1 之后预测起点只认 TimeEngine）。"""
    from mbdsdr_ai.new_spacetime import get_time_engine

    get_time_engine().set_unix(fixed.timestamp())


def _refresh_panel(panel, monkeypatch, settings: dict) -> int:
    """在固定时刻跑一次 refresh_passes，返回列表条数。"""
    from desktop import new_spacetime_panel as nsp
    from mbdsdr_ai.new_spacetime_tle import TLEEntry

    panel._settings = dict(settings)
    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(
        panel._tle_mgr, "fetch",
        lambda catnr, name="": TLEEntry(name=ISS_NAME, line1=ISS_L1,
                                        line2=ISS_L2, catnr=25544))
    _pin_engine()
    panel.refresh_passes()
    return panel.passes_table.rowCount()


class _FakeSkyView:
    def __init__(self, settings: dict):
        self._settings = dict(settings)
        self.rows = None

    def window(self):
        return SimpleNamespace(settings=self._settings)

    def set_upcoming_passes(self, rows, gate_deg=None, show_low=False,
                            window_hours=None, scanned=None,
                            catalog_size=None):
        # 2026-11-06：接口新增 scanned / catalog_size（标题要如实写「仅扫前
        # N/M 颗」）。假视图必须跟着签名走——否则 _refresh_passes 里被吞掉的
        # TypeError 会让"过境列表清空"伪装成"没有过境"。
        self.rows = list(rows)
        self.window_hours = window_hours
        self.scan_note = (scanned, catalog_size)


def _rf_rows(settings: dict):
    """走 SatelliteTracker._refresh_passes，返回 (行数, 标题窗口小时数)。"""
    from desktop.rf_sky_view import SatelliteTracker

    jd = FIXED_UTC.timestamp() / 86400.0 + 2440587.5
    tracker = object.__new__(SatelliteTracker)
    tracker.sky_view = _FakeSkyView(settings)
    tracker.lat, tracker.lon, tracker.alt_km = 39.9, 116.4, 0.0
    tracker._catalog = [{"name": ISS_NAME, "line1": ISS_L1, "line2": ISS_L2}]
    tracker._passes_anchor_unix = None
    tracker._passes_wall = 0.0
    tracker._refresh_passes(jd)
    return (list(tracker.sky_view.rows or []),
            tracker.sky_view.window_hours)


def test_panel_window_6h_vs_24h_counts_differ(qapp, monkeypatch):
    """6 h 与 24 h 的过境条数必须不同（修前红：都是 24 h，条数相同）。"""
    from desktop.new_spacetime_panel import NewSpacetimePanel

    panel = NewSpacetimePanel()
    try:
        n6 = _refresh_panel(panel, monkeypatch,
                            {"pass_window_hours": 6,
                             "pass_min_elevation_deg": 10.0,
                             "show_low_elevation_passes": False})
        n24 = _refresh_panel(panel, monkeypatch,
                             {"pass_window_hours": 24,
                              "pass_min_elevation_deg": 10.0,
                              "show_low_elevation_passes": False})
    finally:
        panel.close()

    assert n6 != n24, (
        f"窗口 6 h 与 24 h 的过境条数相同（{n6} 条）：refresh_passes 仍在用"
        "写死的 hours_ahead=24.0，没有消费 settings.effective_pass_window_hours")
    assert n6 == 1 and n24 == 5, (
        f"实测条数应为 6 h=1、24 h=5，实际 6 h={n6}、24 h={n24}")


def test_panel_window_schema_bounds_1h_vs_72h(qapp, monkeypatch):
    """schema 边界 1 h / 72 h 也要真的改条数。"""
    from desktop.new_spacetime_panel import NewSpacetimePanel

    panel = NewSpacetimePanel()
    try:
        n1 = _refresh_panel(panel, monkeypatch,
                            {"pass_window_hours": 1,
                             "pass_min_elevation_deg": 10.0,
                             "show_low_elevation_passes": False})
        n72 = _refresh_panel(panel, monkeypatch,
                             {"pass_window_hours": 72,
                              "pass_min_elevation_deg": 10.0,
                              "show_low_elevation_passes": False})
    finally:
        panel.close()

    assert n1 == 0, f"1 h 窗口内（06:00–07:00Z）不应有过境，实际 {n1} 条"
    assert n72 > 5, f"72 h 窗口应比 24 h（5 条）更多，实际 {n72} 条"


def test_rf_sky_tracker_window_1h_vs_72h():
    """天空图侧同样读设置：1 h 无过境，72 h 至少 1 条。"""
    one, one_win = _rf_rows({"pass_window_hours": 1,
                             "pass_min_elevation_deg": 10.0,
                             "show_low_elevation_passes": False})
    long, long_win = _rf_rows({"pass_window_hours": 72,
                               "pass_min_elevation_deg": 10.0,
                               "show_low_elevation_passes": False})

    assert one == [], (
        f"1 h 窗口不应列出过境，实际 {len(one)} 条：_refresh_passes 仍在用"
        "写死的 PASS_WIN_HOURS=24.0")
    assert len(long) >= 1, f"72 h 窗口应至少有 1 条过境，实际 {long}"
    # 标题用的窗口必须与设置一致，不许在 UI 上留下第二个 24h
    assert (one_win, long_win) == (1, 72), (
        f"天空图标题窗口应随设置变化，实际 1h→{one_win}、72h→{long_win}")
