# SPDX-License-Identifier: MIT
"""过境面板的用户可见警告：TLE 老化 + 站址可疑。

修前红：面板表格与提示区对两件事完全沉默——1 年前的 TLE 看起来和新 TLE
一样，(0,0) 的过境列表看起来和真实站址一样。
"""
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"


def _fixed_datetime_class(fixed):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    return _FixedDateTime


def _tle_entry():
    from mbdsdr_ai.new_spacetime_tle import TLEEntry
    return TLEEntry(name=ISS_NAME, line1=ISS_L1, line2=ISS_L2, catnr=25544)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qapp, isolated_profile):
    from desktop.new_spacetime_panel import NewSpacetimePanel
    p = NewSpacetimePanel()
    yield p
    p.close()


def test_panel_shows_expired_tle_warning(panel, monkeypatch):
    from desktop import new_spacetime_panel as nsp
    import mbdsdr_ai.new_spacetime_timeline as nst

    fixed = datetime(2009, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class(fixed))
    panel._settings = {"pass_window_hours": 24,
                       "pass_min_elevation_deg": 10.0,
                       "show_low_elevation_passes": False}
    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(panel._tle_mgr, "fetch", lambda *_a, **_k: _tle_entry())

    panel.refresh_passes()

    assert panel.passes_table.rowCount() > 0
    name = panel.passes_table.item(0, 0).text()
    assert "TLE" in name and ("失效" in name or "过期" in name), (
        f"表格里看不到 TLE 老化：{name!r}")
    label = panel.passes_filter_label.text()
    assert "TLE 提醒" in label, f"提示区没有 TLE 老化文案：{label!r}"
    assert "轨道已不可信" in label and "必须刷新" in label, (
        f"dead 必须给「不可信/必须刷新」原话：{label!r}")
    assert "60" in label and "时刻" in label, (
        f"必须说明 time_precision_s=60 是时刻精度：{label!r}")


def test_panel_shows_null_island_warning(panel, monkeypatch):
    """显式 (0,0) 照常算过境，但用户必须看见「位置可疑」的原话。"""
    from desktop import new_spacetime_panel as nsp
    import mbdsdr_ai.new_spacetime_timeline as nst

    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class(
        datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)))
    panel._settings = {"pass_window_hours": 24,
                       "pass_min_elevation_deg": 10.0,
                       "show_low_elevation_passes": False}
    panel.set_observer(0.0, 0.0, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(panel._tle_mgr, "fetch", lambda *_a, **_k: _tle_entry())

    panel.refresh_passes()

    assert panel.passes_table.rowCount() == 2, "(0,0) 仍是合法站址，应算出 2 条"
    label = panel.passes_filter_label.text()
    assert "站址提醒" in label, f"(0,0) 时用户看不到站址 warning：{label!r}"
    assert "null_island_0_0" in label, f"warning 必须带原因码：{label!r}"
    assert "几内亚湾" in label, f"warning 必须说清位置含义：{label!r}"
