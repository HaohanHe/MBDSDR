# SPDX-License-Identifier: MIT
"""TLE 刷新失败的用户可见性：失败必须带 code + 原因 + 建议动作。

修前红：面板提示与状态栏是两条与错误码无关的固定文案——
「未获取 TLE：请先更新卫星星历（离线或无网络时无法拉取）」与
「TLE 在线更新失败，使用内置 TLE」。抓取链路的 code/label/reason
在中途被丢掉，用户分不出是断网、星历不存在还是数据损坏。
"""
import os
import sys
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"

FIXED_CLOCK = 1_700_000_000.0

# 三个典型失败：网络不可达 / 星历不存在（404） / 解析失败（坏数据）
FAILURE_CASES = ("network_unreachable", "http_4xx", "parse_failure")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _body_opener(body):
    def _opener(url, timeout, deadline):
        return body
    return _opener


class _FakeResponse:
    """tracker 的 _bounded_requests_get 契约：返回带 text/raise_for_status 的对象。"""

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _resp_opener(text):
    def _opener(url, timeout, deadline):
        return _FakeResponse(text)
    return _opener


def _raising_opener(exc):
    def _opener(url, timeout, deadline):
        raise exc
    return _opener


def _make_store(tmp_path, opener):
    from mbdsdr_ai.tle_fetch import FetchPolicy, TleFetcher
    from mbdsdr_ai.new_spacetime_tle import TLEFetchError
    return TleFetcher(
        opener=opener,
        cache_dir_provider=lambda: str(tmp_path),
        offline_provider=lambda: False,
        policy=FetchPolicy(backoff_base_s=60.0),
        clock=lambda: FIXED_CLOCK,
        sleep=lambda _s: None,
        error_cls=TLEFetchError,
    )


def _failure_store(tmp_path, case):
    if case == "network_unreachable":
        return _make_store(
            tmp_path,
            _raising_opener(urllib.error.URLError("dns 解析失败（测试注入）")))
    if case == "http_4xx":
        return _make_store(
            tmp_path,
            _raising_opener(urllib.error.HTTPError(
                "https://example.invalid/tle", 404, "Not Found", None, None)))
    if case == "parse_failure":
        return _make_store(
            tmp_path,
            _body_opener("这不是 TLE\n<garbage> 数据损坏\n"))
    raise AssertionError(case)


def _make_manager(tmp_path, case):
    from mbdsdr_ai.new_spacetime_tle import TLEManager
    mgr = TLEManager(cache_dir=str(tmp_path))
    mgr._tle_store = _failure_store(tmp_path, case)
    return mgr


def _assert_failure_copy(text, case):
    """文案必须带 code 或原因、带建议动作，且不出现「成功」。"""
    assert "未获取 TLE" in text or "TLE 在线" in text, text
    assert case in text, f"{case} 的失败码没出现在文案里：{text!r}"
    assert "建议" in text, f"缺少下一步动作：{text!r}"
    assert "成功" not in text, f"失败不得出现「成功」：{text!r}"
    # 旧的两条固定文案一个字都不许留
    assert "请先更新卫星星历（离线或无网络时无法拉取）" not in text
    assert "TLE 在线更新失败，使用内置 TLE" not in text


@pytest.mark.parametrize("case", FAILURE_CASES)
def test_panel_hint_renders_failure_code(qapp, monkeypatch, isolated_profile,
                                         tmp_path, case):
    """面板全失败时：code + label + 原因原文 + 建议动作一起显示。"""
    from desktop import new_spacetime_panel as nsp
    from desktop.new_spacetime_panel import NewSpacetimePanel

    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    panel = NewSpacetimePanel()
    try:
        panel._tle_mgr = _make_manager(tmp_path, case)
        panel._settings = {"pass_window_hours": 24,
                           "pass_min_elevation_deg": 10.0,
                           "show_low_elevation_passes": False}
        panel.set_observer(39.9, 116.4, 0.0)
        panel.refresh_passes()
        text = panel.passes_hint.text()
        _assert_failure_copy(text, case)
    finally:
        panel.close()


@pytest.mark.parametrize("case", FAILURE_CASES)
def test_status_bar_renders_failure_code(monkeypatch, tmp_path, case):
    """状态栏走同一条链路：真实 tracker 失败 → MainWindow 只渲染失败文案。"""
    import mbdsdr_ai.sat_tracker as st_mod
    from mbdsdr_ai import orbit
    from mbdsdr_ai.sat_tracker import SatelliteTracker

    monkeypatch.setattr(orbit, "_network_disabled", lambda: False)
    if case == "parse_failure":
        monkeypatch.setattr(st_mod, "_bounded_requests_get",
                            _resp_opener("这不是 TLE\n<garbage> 数据损坏\n"))
    else:
        monkeypatch.setattr(st_mod, "_bounded_requests_get",
                            _failure_store(tmp_path, case)._opener)
    tracker = SatelliteTracker()
    n = tracker.update_tle_from_celestrak(timeout=1.0)
    assert n == 0
    failure = tracker.last_refresh_failure
    assert failure is not None, "刷新失败必须留痕，不许静默"
    assert failure["code"] == case, failure

    class _Status:
        def __init__(self):
            self.msg = ""
            self.flags = None

        def showMessage(self, msg, flags=None):
            self.msg = str(msg)
            self.flags = flags

    class _Panel:
        def populate_satellites(self, names):
            self.names = list(names)

        def set_tle_date(self, date_str):
            self.date = date_str

    class _Holder:
        pass

    holder = _Holder()
    holder._loop_sat_tracker = tracker
    holder.sat_track_panel = _Panel()
    holder._status = _Status()
    holder.statusBar = lambda: holder._status

    from desktop.main_window import MainWindow
    MainWindow._on_update_sat_tle(holder)
    text = holder._status.msg
    _assert_failure_copy(text, case)


def test_three_failures_have_distinct_copy():
    """三种典型失败必须分话术，不能共用一句「刷新失败」。"""
    from mbdsdr_ai.tle_fetch import failure_copy
    labels = {
        "network_unreachable": failure_copy(
            "network_unreachable", "URLError: dns")["label"],
        "http_4xx": failure_copy("http_4xx", "HTTP 404", 404)["label"],
        "parse_failure": failure_copy("parse_failure", "坏响应体")["label"],
    }
    assert len(set(labels.values())) == 3, labels
    assert "网络不可达" in labels["network_unreachable"]
    assert "星历不存在" in labels["http_4xx"]
    assert "数据损坏" in labels["parse_failure"] or "解析失败" in labels["parse_failure"]


def _dead_note() -> str:
    from mbdsdr_ai.tle_integrity import accuracy
    return accuracy({"tier": "dead", "age_days": 365.0})["note"]


def test_dead_band_note_has_one_source_across_panel_tooltip_sdr(
        qapp, monkeypatch, isolated_profile):
    """dead 档文案在面板 / tooltip 数据源 / SDR 文本三处逐字同源。

    「同源」= 三处都等于 tle_integrity.accuracy() 的 note，任何一处
    自己拼一句都算红。
    """
    from mbdsdr_ai.new_spacetime_timeline import SatellitePass
    from desktop.new_spacetime_panel import NewSpacetimePanel
    from mbdsdr_ai.tle_integrity import accuracy, band_note

    expected = _dead_note()
    assert expected == band_note("dead", 365.0)

    # 1) 面板提示区：只贴数据层原文
    panel = NewSpacetimePanel()
    try:
        p = SatellitePass(
            name=ISS_NAME, rise_time=None, rise_azimuth=0.0,
            max_time=None, max_elevation=0.0, max_azimuth=0.0,
            set_time=None, set_azimuth=0.0, duration_sec=0.0,
            tle_age_days=365.0, tle_tier="dead", tle_tier_label="失效",
            accuracy_risk="unusable", accuracy_note=expected)
        lines = panel._pass_warning_lines([p], {})
        assert lines and lines[-1].endswith(expected), lines
    finally:
        panel.close()

    # 2) tooltip 数据源（卫星跟踪目录条目）：_annotate_entry 走 tle_integrity
    import mbdsdr_ai.sat_tracker as st_mod
    tracker = st_mod.SatelliteTracker()
    monkeypatch.setattr(
        st_mod, "freshness",
        lambda *_a, **_k: {"tier": "dead", "label": "失效",
                           "age_days": 365.0, "epoch_utc": None,
                           "pass_eligible": False})
    assert tracker.load_tle([ISS_NAME, ISS_L1, ISS_L2])
    entry = [s for s in tracker.list_satellites() if s["name"] == ISS_NAME][0]
    assert entry["accuracy_note"] == expected, (
        f"tracker 目录条目自己拼了文案：{entry['accuracy_note']!r}")

    # 3) SDR 文本：过境报告逐字贴 accuracy_note
    import mbdsdr_ai.sdr_tools as st
    from mbdsdr_ai import orbit

    rep = {
        "n_passes": 1,
        "passes": [{
            "satellite": ISS_NAME,
            "rise_time": "2009-09-20 12:00:00",
            "rise_azimuth": 10.0,
            "culmination_time": "2009-09-20 12:05:00",
            "max_elevation": 40.0,
            "culmination_azimuth": 20.0,
            "set_time": "2009-09-20 12:10:00",
            "set_azimuth": 30.0,
            "duration_s": 600.0,
            "accuracy_risk": "unusable",
            "tle_tier": "dead",
            "tle_age_days": 365.0,
            "accuracy_note": expected,
        }],
        "degraded": [],
        "station": {},
        "tle_policy": {},
    }
    monkeypatch.setattr(orbit, "predict_passes_report", lambda *a, **k: rep)
    text = st._satellite_passes({"latitude": 39.9, "longitude": 116.4,
                                 "hours": 24.0})
    assert expected in text, f"SDR 文本没有复用数据层文案：{text[-300:]!r}"
