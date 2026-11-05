# SPDX-License-Identifier: MIT
"""新时空面板「刷新过境」的可观察性。

修前红：三种前置条件都静默——没 TLE / 没配站址时只留一句旧文案或干脆
算出 0,0 的过境；无过境文案也不带窗口数字；加载期间按钮仍可点、可重入。

覆盖：
1. 没有 TLE → 「未获取 TLE：请先更新卫星星历」；
2. 未配置观测站 → 「未配置观测站位置」，且不发起 TLE 请求；
3. 有数据但窗口内无过境 → 「未来 N 小时内无过境（门限 X°，窗口 Y 小时）」
   中的 N/X/Y 全部来自 effective_*；
4. 加载期间按钮置灰且拒绝同步重入。
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
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qapp):
    from desktop.new_spacetime_panel import NewSpacetimePanel

    p = NewSpacetimePanel()
    yield p
    p.close()


def _fixed_datetime_class():
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_UTC

    return _FixedDateTime


def _tle_entry():
    from mbdsdr_ai.new_spacetime_tle import TLEEntry

    return TLEEntry(name=ISS_NAME, line1=ISS_L1, line2=ISS_L2, catnr=25544)


def test_no_tle_hint(panel, monkeypatch):
    from desktop import new_spacetime_panel as nsp

    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(panel._tle_mgr, "fetch", lambda *_a, **_k: None)

    panel.refresh_passes()

    assert not panel.passes_hint.isHidden(), "空态提示必须可见"
    text = panel.passes_hint.text()
    assert text.startswith("未获取 TLE：请先更新卫星星历"), (
        f"没有 TLE 时用户看不出该做什么：{text!r}")


def test_no_station_hint(panel, monkeypatch):
    calls = []

    def _fetch(*_a, **_k):
        calls.append(1)
        return None

    # 默认 0,0 就是本面板的「未配置」哨兵
    monkeypatch.setattr(panel._tle_mgr, "fetch", _fetch)
    panel.refresh_passes()

    assert not panel.passes_hint.isHidden(), "空态提示必须可见"
    text = panel.passes_hint.text()
    assert text.startswith("未配置观测站位置"), (
        f"没配站址仍去算 0,0 的过境/打网络：{text!r}")
    assert calls == [], f"未配置站址时不应发起 TLE 请求，实际请求 {len(calls)} 次"


def test_empty_window_hint_uses_effective_values(panel, monkeypatch):
    from desktop import new_spacetime_panel as nsp
    import mbdsdr_ai.new_spacetime_timeline as nst

    panel._settings = {"pass_window_hours": 1,
                       "pass_min_elevation_deg": 10.0,
                       "show_low_elevation_passes": False}
    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(panel._tle_mgr, "fetch", lambda *_a, **_k: _tle_entry())
    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class())

    panel.refresh_passes()

    assert panel.passes_table.rowCount() == 0, "1 h 窗口内不应有过境"
    text = panel.passes_hint.text()
    assert "未来 1 小时内无过境" in text, text
    assert "门限 10°" in text, text
    assert "窗口 1 小时" in text, text
    assert "24" not in text, f"文案写死了窗口：{text!r}"


def test_refresh_button_disabled_and_reentry_refused(panel, monkeypatch):
    from desktop import new_spacetime_panel as nsp

    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES",
                        [(ISS_NAME, 25544), ("ISS-2", 99999)])
    calls = []

    def _fetch(catnr, name=""):
        calls.append(catnr)
        assert panel.refresh_btn.isEnabled() is False, (
            "加载期间刷新按钮必须置灰，否则连点会叠加请求")
        # 模拟连点造成的同步重入：必须被拒绝
        panel.refresh_passes()
        return _tle_entry()

    monkeypatch.setattr(panel._tle_mgr, "fetch", _fetch)
    panel.refresh_passes()

    assert len(calls) == 2, f"重入未被拒绝，fetch 被调了 {len(calls)} 次"
    assert panel.refresh_btn.isEnabled() is True, "加载结束后按钮必须恢复"
