# SPDX-License-Identifier: MIT
"""过境仰角设置接线：用户设置必须真正改变列表集合（A1/A9）。

修前红：`pass_min_elevation_deg` / `show_low_elevation_passes` 在 HEAD 上
零消费点，`refresh_passes()` 恒按 `sat_passes.PASS_MIN_ALT_DEG=10°` 跑，
5° 与 10° 两次刷新返回**完全相同**的过境集合，5–10° 的低仰角过境永远不出现。

固定用例（离线、不读用户配置）：
- TLE：ISS，epoch 2008-264（确定性）
- 站点：39.9°N / 116.4°E / 0 km
- 基准时刻：2008-09-20 06:00:00 UTC
- 该 24h 窗口内：5° 门 6 条、10° 门 5 条；唯一差异是 15:30 峰值 9.8° 的低仰角
  过境（表格按 1° 显示，所以断言表达「落在 5–10° 区间」而非钉死显示值）
"""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
# 该窗口内 5° 门比 10° 门多出的唯一一条过境：峰值落在 5–10° 区间。
# 表格按 1° 显示，9.8° 会显示成 10.0°，所以按区间 + 显示容差断言，不钉数字。
_LOW_BAND_DEG = (5.0, 10.0)
_PEAK_TOLERANCE_DEG = 0.5


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _fixed_datetime_class():
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_UTC

    return _FixedDateTime


def _refresh(panel, monkeypatch, settings: dict):
    """在固定时刻跑一次 refresh_passes，返回 [(name, peak_deg)]。

    `_settings` 直接注入：修前代码不读它（于是红），修后读它。
    """
    from desktop import new_spacetime_panel as nsp
    import mbdsdr_ai.new_spacetime_timeline as nst
    from mbdsdr_ai.new_spacetime_tle import TLEEntry

    panel._settings = dict(settings)
    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(
        panel._tle_mgr, "fetch",
        lambda catnr, name="": TLEEntry(name=ISS_NAME, line1=ISS_L1,
                                        line2=ISS_L2, catnr=25544))
    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class())
    panel.refresh_passes()
    out = []
    for row in range(panel.passes_table.rowCount()):
        # 左列两行：卫星名（可能带「低仰角」）\n过境时刻。集合比较
        # 只能取名字行，否则 5°/10° 的分钟差会让每一行都算「多出来」。
        name = panel.passes_table.item(row, 0).text().splitlines()[0]
        # 2026-11-11 表格压成 2 列：右列两行「峰值仰角°\n持续分」
        alt = panel.passes_table.item(row, 1).text().splitlines()[0]
        out.append((name, float(alt.rstrip("°"))))
    return out


def test_refresh_passes_respects_user_min_elevation(qapp, monkeypatch):
    """5° 与 10° 的返回集合必须不同（修前红：完全相同）。"""
    from desktop.new_spacetime_panel import NewSpacetimePanel

    panel = NewSpacetimePanel()
    try:
        at5 = _refresh(panel, monkeypatch,
                       {"pass_min_elevation_deg": 5.0,
                        "show_low_elevation_passes": False})
        at10 = _refresh(panel, monkeypatch,
                        {"pass_min_elevation_deg": 10.0,
                         "show_low_elevation_passes": False})
    finally:
        panel.close()

    assert at5 != at10, (
        "设 5° 与设 10° 返回了同一集合：说明 refresh_passes 没有消费 "
        "settings.effective_pass_min_elevation_deg，仍在用写死的 10°")
    extra = [row for row in at5 if row not in at10]
    assert len(extra) == 1, (
        f"5° 应比 10° 恰好多列 1 条 5–10° 过境，实际多出 {extra}（5°={at5}，10°={at10}）")
    peak = extra[0][1]
    assert (_LOW_BAND_DEG[0] - _PEAK_TOLERANCE_DEG
            <= peak < _LOW_BAND_DEG[1] + _PEAK_TOLERANCE_DEG), (
        f"多出的过境应是 5–10° 的低仰角过境"
        f"（显示容差 ±{_PEAK_TOLERANCE_DEG}°），实际 {extra[0]}")


def test_show_low_elevation_passes_lists_and_marks_5_to_10(qapp, monkeypatch):
    """开关打开后 5–10° 过境出现，并在卫星名上标注「低仰角」。"""
    from desktop.new_spacetime_panel import NewSpacetimePanel

    panel = NewSpacetimePanel()
    try:
        off = _refresh(panel, monkeypatch,
                       {"pass_min_elevation_deg": 10.0,
                        "show_low_elevation_passes": False})
        on = _refresh(panel, monkeypatch,
                      {"pass_min_elevation_deg": 10.0,
                       "show_low_elevation_passes": True})
        label = panel.passes_filter_label.text()
    finally:
        panel.close()

    assert off != on, "「显示低仰角过境」开关没有生效，两次列表相同"
    extra = [row for row in on if row not in off]
    assert len(extra) == 1, (
        f"开关打开后应恰好多列 1 条 5–10° 过境，实际多出 {extra}（关={off}，开={on}）")
    peak = extra[0][1]
    assert (_LOW_BAND_DEG[0] - _PEAK_TOLERANCE_DEG
            <= peak < _LOW_BAND_DEG[1] + _PEAK_TOLERANCE_DEG), (
        f"多出的过境应是 5–10° 的低仰角过境"
        f"（显示容差 ±{_PEAK_TOLERANCE_DEG}°），实际 {extra[0]}")
    low_on = [row for row in on if "低仰角" in row[0]]
    assert low_on, f"开关打开后低仰角过境未在表格里标注「低仰角」，实际 {on}"
    assert "峰值仰角" in label and "10" in label, (
        f"筛选口径文案没有写清「峰值仰角 ≥ N°」，实际：{label}")
    assert "低仰角" in label, f"文案没有解释低仰角开关，实际：{label}"


def test_sat_track_panel_keeps_visible_gate_separate(qapp, monkeypatch):
    """7° 时跟踪面板仍可跟踪（可见门 5°），但列表门按用户设置显示 10°。

    要求 4：VISIBLE_MIN_ALT_DEG 与用户设置**不合并**。
    """
    from desktop.sat_track_panel import SatTrackPanel
    from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG

    panel = SatTrackPanel()
    panel._settings = {"pass_min_elevation_deg": 10.0,
                       "show_low_elevation_passes": False}
    try:
        panel.update_position({"valid": True, "azimuth": 180.0, "elevation": 7.0,
                               "doppler_hz": 0.0, "corrected_freq_hz": 1.0,
                               "range_km": 1000.0})
        state = panel.state_label.text()
        gate_text = panel.pass_gate_label.text()
    finally:
        panel.close()

    assert "跟踪中" in state, f"7° 仍在可见门 {VISIBLE_MIN_ALT_DEG}° 之上，实际状态：{state}"
    assert "10" in gate_text and "过境" in gate_text, (
        f"跟踪面板没有显示用户过境筛选门，实际：{gate_text}")
