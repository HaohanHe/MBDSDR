# SPDX-License-Identifier: MIT
"""新时空面板 offscreen 测试
================================

验证 desktop/new_spacetime_panel.py 在 offscreen 平台下：
  1. 面板实例化 / show() 不崩；
  2. 初始 AMR 区域显"未检测到信号"，指标为 "--"；
  3. update_amr_result(FM, 0.85) 后标签含 "FM"；
  4. update_iq 喂一段合成 FM 音调后 AMR 区域不再是"未检测到信号"；
  5. 初始授时状态显"未同步"；
  6. FreqOrbitChart 无数据时 paintEvent 可渲染不崩；
  7. FreqOrbitChart 传入 3 个点后 set_data / update 不崩；
  8. 观测站经纬度输入框初始值不是任何硬编码城市坐标；
  9. 面板存在 AMR / 时间线 / 频率轨道三个区域。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_new_spacetime_panel.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 仓库根加入 sys.path，保证 `from desktop.xxx` / `from mbdsdr_ai.xxx` 可导入
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QGroupBox  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


from desktop.new_spacetime_panel import (  # noqa: E402
    NewSpacetimePanel, FreqOrbitChart,
)
from mbdsdr_ai.new_spacetime_amr import AMRStreamResult  # noqa: E402
from mbdsdr_ai.new_spacetime_timeline import (  # noqa: E402
    FreqOrbitPoint, SatellitePass,
)


# ---------------------------------------------------------------------------
# 1. offscreen 实例化 + show() 不崩
# ---------------------------------------------------------------------------
def test_panel_instantiation_offscreen(qapp):
    panel = NewSpacetimePanel()
    panel.show()
    panel.hide()
    panel.close()


# ---------------------------------------------------------------------------
# 2. 初始 AMR 区域显"未检测到信号"，指标为 "--"
# ---------------------------------------------------------------------------
def test_amr_no_signal_display(qapp):
    panel = NewSpacetimePanel()
    assert panel.amr_mod_label.text() == "未检测到信号"
    assert panel.bw_label.text() == "--"
    assert panel.peak_label.text() == "--"
    assert panel.snr_label.text() == "--"
    assert panel.offset_label.text() == "--"
    assert panel.amr_conf_bar.value() == 0
    panel.close()


# ---------------------------------------------------------------------------
# 3. 直接喂一个 FM 结果，标签应含 "FM"
# ---------------------------------------------------------------------------
def test_amr_update_with_result(qapp):
    panel = NewSpacetimePanel()
    result = AMRStreamResult(
        signal_detected=True,
        modulation="FM",
        confidence=0.85,
        bandwidth_hz=12500.0,
        peak_count=1,
        center_offset_hz=1000.0,
        noise_floor_db=-90.0,
        signal_power_db=-30.0,
        snr_db=60.0,
        spectral_features={},
        raw_label="FM",
    )
    panel.update_amr_result(result)
    assert "FM" in panel.amr_mod_label.text()
    assert panel.amr_conf_bar.value() == 85
    panel.close()


# ---------------------------------------------------------------------------
# 4. update_iq 喂合成 FM 音调，AMR 区域不再是"未检测到信号"
# ---------------------------------------------------------------------------
def test_feed_iq_triggers_analysis(qapp):
    panel = NewSpacetimePanel()
    sr = 1_000_000.0
    n = 8192
    t = np.arange(n) / sr
    f0 = 10_000.0
    # 带轻微频偏的复指数音调（窄带强信号）
    phase = 2 * np.pi * f0 * t + 2 * np.pi * 5000.0 / 1000.0 * np.sin(2 * np.pi * 1000.0 * t)
    iq = np.exp(1j * phase).astype(np.complex128)

    panel.update_iq(iq, sr, center_freq=0.0)
    # 至少不应崩；强音调应被检出，标签离开"未检测到信号"
    assert panel.amr_mod_label.text() != "未检测到信号"
    panel.close()


# ---------------------------------------------------------------------------
# 5. 初始授时状态显"未同步"
# ---------------------------------------------------------------------------
def test_time_sync_unsynchronized_default(qapp):
    panel = NewSpacetimePanel()
    assert "未同步" in panel.time_sync_label.text()
    panel.close()


# ---------------------------------------------------------------------------
# 6. FreqOrbitChart 无数据：resize + grab 触发 paintEvent 不崩
# ---------------------------------------------------------------------------
def test_freq_orbit_chart_no_data(qapp):
    chart = FreqOrbitChart()
    chart.resize(400, 260)
    pix = chart.grab()  # 触发 paintEvent
    assert not pix.isNull()
    chart.close()


# ---------------------------------------------------------------------------
# 7. FreqOrbitChart 传入 3 个点：set_data / update 不崩
# ---------------------------------------------------------------------------
def test_freq_orbit_chart_with_data(qapp):
    chart = FreqOrbitChart()
    chart.resize(400, 260)
    points = [
        FreqOrbitPoint(name="LEO-A", frequency_mhz=137.5, altitude_km=400.0,
                       inclination_deg=51.6, period_min=92.0,
                       eccentricity=0.001, catnr=25544),
        FreqOrbitPoint(name="LEO-B", frequency_mhz=137.1, altitude_km=850.0,
                       inclination_deg=98.7, period_min=102.0,
                       eccentricity=0.0012, catnr=33591),
        FreqOrbitPoint(name="MEO-C", frequency_mhz=1545.0, altitude_km=20200.0,
                       inclination_deg=55.0, period_min=720.0,
                       eccentricity=0.02, catnr=10001),
    ]
    chart.set_data(points)
    chart.update()
    pix = chart.grab()
    assert not pix.isNull()
    assert chart.has_data()
    chart.close()


# ---------------------------------------------------------------------------
# 8. 观测站经纬度输入框初始值不是硬编码城市坐标
# ---------------------------------------------------------------------------
def test_observer_is_readonly_and_fed_by_host(qapp):
    """面板不再持有可编辑坐标控件 —— 那曾是站址的第二个写入口。

    2026-11-09：三个 SpinBox 已删（填了只影响本面板过境，天空图不动）。
    现在只显示主窗推来的坐标，改地点走「更改地点…」。
    """
    panel = NewSpacetimePanel()
    try:
        assert not hasattr(panel, "lat_spin"), "面板不该再持有纬度输入框"
        assert not hasattr(panel, "lon_spin"), "面板不该再持有经度输入框"
        assert not hasattr(panel, "alt_spin"), "面板不该再持有海拔输入框"
        assert "未设置" in panel.observer_label.text()

        panel.set_observer(45.0, 120.0, 0.0)
        assert panel._observer_configured is True
        assert "45.0000" in panel.observer_label.text()
        assert "120.0000" in panel.observer_label.text()

        panel.set_observer(None, None)
        assert panel._observer_configured is False
        assert "未设置" in panel.observer_label.text()

        # 0.0/0.0 是合法站址（几内亚湾），不是"未设置"哨兵
        panel.set_observer(0.0, 0.0)
        assert panel._observer_configured is True
        assert "0.0000" in panel.observer_label.text()
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# 9. 面板有三个区域（AMR / 时间线 / 频率轨道）
# ---------------------------------------------------------------------------
def test_panel_layout_sections(qapp):
    panel = NewSpacetimePanel()
    assert panel.findChild(QGroupBox, "amrGroup") is not None, "缺少 AMR 区域"
    assert panel.findChild(QGroupBox, "timelineGroup") is not None, "缺少时间线区域"
    assert panel.findChild(QGroupBox, "freqOrbitGroup") is not None, "缺少频率轨道区域"
    # 图表实例存在
    assert isinstance(panel.chart, FreqOrbitChart)
    panel.close()


# ---------------------------------------------------------------------------
# 10. 跨 UTC 日界的过境必须带日期（P1-16）
# ---------------------------------------------------------------------------
def _mk_pass(name, rise, mx, st, max_elevation=45.0):
    return SatellitePass(
        name=name, rise_time=rise, rise_azimuth=0.0,
        max_time=mx, max_elevation=max_elevation, max_azimuth=0.0,
        set_time=st, set_azimuth=0.0, duration_sec=720.0,
        max_doppler_hz=-2500.0)


def test_cross_utc_day_pass_shows_date(qapp):
    """跨 UTC 日界的行逐时刻带日期；同日行仍只留时分。

    改前一律 ``%H:%M``，跨日条目显示成「23:58 → 00:03 → 00:10」，
    实测 23 条里就有 1 条如此，用户无法判断落在哪一天。去掉日期 → 本用例红。
    """
    from datetime import datetime
    panel = NewSpacetimePanel()
    try:
        cross = _mk_pass(
            "CROSS",
            datetime(2026, 11, 11, 23, 58),
            datetime(2026, 11, 12, 0, 3),
            datetime(2026, 11, 12, 0, 10))
        same = _mk_pass(
            "SAME",
            datetime(2026, 11, 12, 12, 0),
            datetime(2026, 11, 12, 12, 5),
            datetime(2026, 11, 12, 12, 12))
        panel._populate_passes([cross, same])

        text_cross = panel.passes_table.item(0, 0).text()
        text_same = panel.passes_table.item(1, 0).text()
        assert "11-11 23:58" in text_cross
        assert "11-12 00:03" in text_cross
        assert "11-12 00:10" in text_cross
        assert "23:58 → 00:03" not in text_cross
        assert "12:00" in text_same
        assert "11-12" not in text_same
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# 11. W84-P1-15：表头排序 —— 行序真的按过境时刻/峰值仰角变（不是按显示文本）
# ---------------------------------------------------------------------------
def _visible_rows(panel):
    return sum(1 for r in range(panel.passes_table.rowCount())
               if not panel.passes_table.isRowHidden(r))


def test_pass_table_header_sort_reorders_rows(qapp):
    """插入序是 LATE→EARLY→MID，排序后行序必须真的变。

    改前 passes_table 没开 sortingEnabled，行序恒等于插入序 → 本用例红。
    左列显示文本是「卫星名\\n时刻」，若按字面排则是 EARLY→LATE→MID 的
    字母序，与时刻序不同；这里断言以「升起时刻」为键。
    """
    from datetime import datetime
    panel = NewSpacetimePanel()
    try:
        late = _mk_pass(
            "LATE", datetime(2026, 12, 1, 20, 0), datetime(2026, 12, 1, 20, 5),
            datetime(2026, 12, 1, 20, 9), max_elevation=10.0)
        early = _mk_pass(
            "EARLY", datetime(2026, 12, 1, 1, 0), datetime(2026, 12, 1, 1, 5),
            datetime(2026, 12, 1, 1, 9), max_elevation=80.0)
        mid = _mk_pass(
            "MID", datetime(2026, 12, 1, 10, 0), datetime(2026, 12, 1, 10, 5),
            datetime(2026, 12, 1, 10, 9), max_elevation=45.0)
        panel._populate_passes([late, early, mid])

        def name_at(row):
            return panel.passes_table.item(row, 0).text().split("\n")[0]

        # 默认升序（表头第 0 列）：最早的 EARLY 排第一，插入序已被打乱
        assert name_at(0) == "EARLY"
        assert name_at(2) == "LATE"

        # 点表头第 0 列降序：行序真的翻转
        panel.passes_table.sortItems(0, Qt.DescendingOrder)
        assert name_at(0) == "LATE"
        assert name_at(2) == "EARLY"

        # 第 1 列按峰值仰角：降序时 80° 的 EARLY 回到第一
        panel.passes_table.sortItems(1, Qt.DescendingOrder)
        assert name_at(0) == "EARLY"
        panel.passes_table.sortItems(1, Qt.AscendingOrder)
        assert name_at(0) == "LATE"
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# 12. W84-P1-15：筛选输入框 —— 行数变化，空结果有明确文案
# ---------------------------------------------------------------------------
def test_pass_filter_changes_rows_and_empty_result_text(qapp):
    """改前面板没有筛选入口 → `passes_filter_edit` 不存在，本用例红。"""
    from datetime import datetime
    panel = NewSpacetimePanel()
    try:
        panel._populate_passes([
            _mk_pass("ALPHA-1", datetime(2026, 12, 2, 1, 0),
                     datetime(2026, 12, 2, 1, 5), datetime(2026, 12, 2, 1, 9)),
            _mk_pass("BETA-2", datetime(2026, 12, 2, 2, 0),
                     datetime(2026, 12, 2, 2, 5), datetime(2026, 12, 2, 2, 9)),
            _mk_pass("GAMMA-3", datetime(2026, 12, 2, 3, 0),
                     datetime(2026, 12, 2, 3, 5), datetime(2026, 12, 2, 3, 9)),
        ])
        assert _visible_rows(panel) == 3

        panel.passes_filter_edit.setText("alpha")
        assert _visible_rows(panel) == 1
        assert not panel.passes_table.isHidden()

        # 空结果：表收起 + 提示写明原因和恢复办法（不能留一块无字框）
        panel.passes_filter_edit.setText("zzz-no-such-sat")
        assert _visible_rows(panel) == 0
        assert panel.passes_table.isHidden()
        assert not panel.passes_hint.isHidden()
        hint = panel.passes_hint.text()
        assert "筛选无结果" in hint
        assert "zzz-no-such-sat" in hint
        assert "3 条过境" in hint

        # 清空筛选恢复全部行
        panel.passes_filter_edit.setText("")
        assert _visible_rows(panel) == 3
        assert not panel.passes_table.isHidden()
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# 13. W84-P1-15：下一次过境倒计时 —— 只一条，且目标时刻 = 全表最早升起
# ---------------------------------------------------------------------------
def test_next_pass_countdown_matches_earliest_rise(qapp):
    """改前没有倒计时控件/更新入口，本用例红。"""
    from datetime import datetime
    panel = NewSpacetimePanel()
    try:
        later = _mk_pass(
            "LATER", datetime(2026, 12, 1, 3, 0), datetime(2026, 12, 1, 3, 5),
            datetime(2026, 12, 1, 3, 9))
        nxt = _mk_pass(
            "NEXT", datetime(2026, 12, 1, 1, 0, 30),
            datetime(2026, 12, 1, 1, 5), datetime(2026, 12, 1, 1, 9))
        # 插入序反着来，验证挑选依据是升起时刻而不是行序
        panel._populate_passes([later, nxt])

        fixed = datetime(2026, 12, 1, 0, 0, 0)
        panel._update_next_pass_countdown(now=fixed)
        text = panel.next_pass_label.text()
        assert not panel.next_pass_label.isHidden()
        assert "NEXT" in text
        # 倒计时目标时刻与「下一次过境」的升起时间一致
        assert "12-01 01:00:30" in text
        assert "1:00:30 后" in text

        # 筛选只影响表格显示，不改变「下一次过境」的判定
        panel.passes_filter_edit.setText("LATER")
        panel._update_next_pass_countdown(now=fixed)
        assert "NEXT" in panel.next_pass_label.text()

        # 02:00 时 NEXT 已升起：「下一次」自动顺延到尚未开始的 LATER
        panel._update_next_pass_countdown(now=datetime(2026, 12, 1, 2, 0, 0))
        assert "LATER" in panel.next_pass_label.text()
        assert "1:00:00 后" in panel.next_pass_label.text()
        assert "12-01 03:00:00" in panel.next_pass_label.text()

        # 全部已开始：不硬报过去时刻，明确让用户刷新
        panel._update_next_pass_countdown(now=datetime(2026, 12, 1, 4, 0, 0))
        assert "均已开始" in panel.next_pass_label.text()

        # 生产口径：TimelineEngine 产出 tz-aware UTC 的 rise_time + aware 时钟
        from datetime import timezone
        aware = _mk_pass(
            "AWARE", datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc),
            datetime(2026, 12, 1, 5, 5, tzinfo=timezone.utc),
            datetime(2026, 12, 1, 5, 9, tzinfo=timezone.utc))
        panel._populate_passes([aware])
        panel._clock = lambda: datetime(2026, 12, 1, 4, 0, tzinfo=timezone.utc)
        panel._update_next_pass_countdown()
        assert "AWARE" in panel.next_pass_label.text()
        assert "1:00:00 后" in panel.next_pass_label.text()
        assert "12-01 05:00:00" in panel.next_pass_label.text()
    finally:
        panel.close()


# ---------------------------------------------------------------------------
# 14. W84-P1-15：行点击定位 —— 选中整行并记录命中的过境
# ---------------------------------------------------------------------------
def test_pass_row_click_locates_pass(qapp):
    """改前 itemClicked 未接线，currentRow / _located_pass 都不会更新 → 红。"""
    from datetime import datetime
    panel = NewSpacetimePanel()
    try:
        passes = [
            _mk_pass(f"SAT-{i:02d}", datetime(2026, 12, 1, 0, i, 0),
                     datetime(2026, 12, 1, 0, i, 5),
                     datetime(2026, 12, 1, 0, i, 9))
            for i in range(20)
        ]
        panel._populate_passes(passes)
        item = panel.passes_table.item(7, 0)
        assert item is not None
        panel._on_pass_clicked(item)
        assert panel.passes_table.currentRow() == 7
        assert panel._located_pass is not None
        assert panel._located_pass.name == "SAT-07"
        selected = panel.passes_table.selectedItems()
        assert selected and all(i.row() == 7 for i in selected)
    finally:
        panel.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
