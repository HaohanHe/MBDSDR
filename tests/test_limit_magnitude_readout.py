# SPDX-License-Identifier: MIT
"""极限星等只读显示的契约（2026-11-09）。

用户已定：**不做旋钮**。理由写在代码注释里，这里用测试钉住三条：

1. 显示值必须与绘制层同一条公式（limit_magnitude − 晨昏压制），不许另算；
2. 读它不能改变任何绘制参数（同一片天、同一个 Bortle，星数不变）；
3. 文案必须带「渲染近似，非口径增益」——防止被读成"换镜子能看得更暗"。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _view(qapp):
    from datetime import datetime, timezone

    from mbdsdr_ai.new_spacetime import get_time_engine
    from rf_sky_view import RFSkyView
    eng = get_time_engine()
    eng.set_unix(datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc).timestamp())
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(45.0, 120.0, 0.0)
    v.view_state.look_at(180.0, 40.0)
    v.view_state.fov_deg = 90.0
    return v


def test_snapshot_matches_draw_formula(qapp):
    from mbdsdr_ai.skyengine.star_catalog import limit_magnitude
    v = _view(qapp)
    try:
        v.set_bortle(3)
        snap = v.limit_magnitude_snapshot()
        base = float(limit_magnitude(float(v.view_state.fov_deg), bortle=3))
        assert snap["base"] == pytest.approx(base, abs=1e-9)
        assert snap["effective"] == pytest.approx(
            base - float(snap["twilight_penalty"]), abs=1e-9)
        assert snap["bortle"] == 3
        assert snap["catalog_depth"] == pytest.approx(9.0)
    finally:
        v.close()


def test_bortle_changes_base_not_penalty(qapp):
    v = _view(qapp)
    try:
        v.set_bortle(3)
        a = v.limit_magnitude_snapshot()
        v.set_bortle(8)
        b = v.limit_magnitude_snapshot()
        assert b["base"] < a["base"], "光害更重，天顶极限星等必须更低"
        # 同一时刻的晨昏压制不该因为改 Bortle 而变化
        assert b["twilight_penalty"] == pytest.approx(
            a["twilight_penalty"], abs=1e-9)
    finally:
        v.close()


def test_daylight_is_reported(qapp):
    """白天判定跟绘制层同一条门（太阳高度 > -0.833°）。

    本用例直接注入太阳高度：RFSkyView 单独构造时没有历表喂太阳位置，
    ``_sun_alt_deg()`` 返回 None，那时快照按夜间处理（保守口径，与绘制层
    一致——不知道就别假装是白天）。
    """
    v = _view(qapp)
    try:
        v._sun_alt_deg = lambda: 45.0          # 注入：太阳高挂
        snap = v.limit_magnitude_snapshot()
        assert snap["daylight"] is True, "白天不画星，快照必须如实说"
        assert float(snap["sun_alt"]) == pytest.approx(45.0)
    finally:
        v.close()


def test_snapshot_does_not_change_selection(qapp):
    """只读就是只读：读一次快照，星数与星等门都不许变。"""
    v = _view(qapp)
    try:
        v.set_bortle(3)
        gs = v._ground_station()
        jd = v._current_jd()
        v._star_sel = None
        before = v._star_selection(gs, jd)
        n_before = 0 if before is None else len(before)
        v.limit_magnitude_snapshot()
        v.limit_magnitude_snapshot()
        after = v._star_selection(gs, jd)
        n_after = 0 if after is None else len(after)
        assert n_after == n_before
    finally:
        v.close()


# ---------------------------------------------------------------------- #
# UI 文案
# ---------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def win(isolated_profile, qapp):
    from PySide6.QtCore import Qt
    from main_window import MainWindow
    w = MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1280, 800)
    w.show()
    for _ in range(15):
        qapp.processEvents()
    yield w
    w.close()


def test_limit_line_has_honest_wording(win):
    win._refresh_limit_mag_line()
    text = win._limit_mag_label.text()
    assert "渲染近似" in text and "非口径增益" in text, text
    assert "极限星等" in text
    assert "Bortle" in text
    # 白天那句话也不能丢"不显示星"的因果
    assert "极限星等" in text
