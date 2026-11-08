# SPDX-License-Identifier: MIT
"""投影切换的契约（2026-11-09）。

守四件事：

1. 四种投影的 **正投影↔反投影可逆**（正射只在可见半球内成立，理由见下）；
2. 两种形态（专业 / 全天）**各自记住**自己的投影，互不覆盖；
3. FOV 上限跟随投影（透视 120 / 球极 160 / 正射 180 / 等距方位 180），
   切完当场夹住当前 FOV，不留"显示不了的视角"；
4. 未知投影名当场抛错，不留一个点了没反应的假开关。
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    PROJECTION_INFO, make_projection, projection_names,
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("name", projection_names())
def test_roundtrip_within_visible_domain(name):
    """项目 → 反投影 → 还原；正射只承诺可见半球（背面没有原像）。

    正射的 ``_backward`` 在 x²+y²>1（离轴 >90°）时把点截断到圆盘边缘，
    那不是 bug 而是"看不见的那半个天球本来就没有画面位置"。所以这里对
    正射只取离轴 < 80° 的样本，其余三种取全范围（≤ 170°）。
    """
    proj = make_projection(name)
    limit = 80.0 if name == "orthographic" else 170.0
    for az in (0.0, 47.0, 123.0, 245.0, 359.0):
        for alt in (5.0, 30.0, 60.0, 85.0):
            x, y = proj.project_azalt(az, alt)
            az2, alt2 = proj.unproject_azalt(x, y)
            d_alt = abs(alt2 - alt)
            d_az = abs((az2 - az + 180.0) % 360.0 - 180.0)
            assert d_alt < 1e-6, f"{name} alt 不可逆：{alt} -> {alt2}"
            if abs(alt - 90.0) < limit:
                assert d_az < 1e-6, f"{name} az 不可逆：{az} -> {az2}"


@pytest.mark.parametrize("name", projection_names())
def test_every_projection_has_chinese_info(name):
    info = PROJECTION_INFO[name]
    assert info["zh"] and info["hint"]
    assert float(info["fov_max"]) > 0


def test_switch_keeps_two_modes_independent(qapp):
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    try:
        v.resize(700, 700)
        assert v.projection_name == "perspective"

        v.set_projection("stereographic")            # 专业模式
        assert v.projection_name == "stereographic"
        assert v.view_state.max_fov == pytest.approx(160.0)

        v.set_all_sky_mode(True)
        assert v.projection_name == "stereographic"  # 全天默认球极
        v.set_projection("orthographic")             # 只改全天
        assert v.projection_name == "orthographic"
        assert v.view_state.max_fov == pytest.approx(v.ALL_SKY_FOV_DEG)

        v.set_all_sky_mode(False)
        assert v.projection_name == "stereographic", "退回专业模式不该被全天选择污染"
        assert v.view_state.max_fov == pytest.approx(160.0)

        v.set_all_sky_mode(True)
        assert v.projection_name == "orthographic", "全天模式的选择也要记住"
    finally:
        v.close()


def test_switch_clamps_current_fov(qapp):
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    try:
        v.resize(700, 700)
        v.view_state.zoom_to(170.0)          # 先把 FOV 拉大（会被 120 夹住）
        v.set_projection("perspective")
        assert v.view_state.fov_deg <= 120.0 + 1e-6
        v.set_projection("orthographic")
        v.view_state.zoom_to(170.0)
        assert v.view_state.fov_deg == pytest.approx(170.0)
        v.set_projection("perspective")      # 切回去必须夹回来
        assert v.view_state.fov_deg <= 120.0 + 1e-6
    finally:
        v.close()


def test_unknown_projection_raises(qapp):
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    try:
        with pytest.raises(ValueError):
            v.set_projection("not-a-projection")
        # 失败不该留下半截状态
        assert v.projection_name == "perspective"
    finally:
        v.close()


def test_projection_change_is_visible_in_screen_coords(qapp):
    """换投影必须真的改变画面（不是只改了个名字）。"""
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    try:
        v.resize(700, 700)
        v.view_state.look_at(0.0, 45.0)
        v.view_state.zoom_to(90.0)
        v.set_projection("perspective")
        p1 = v._sky_to_screen(60.0, 45.0)
        v.set_projection("azimuthal_equidistant")
        p2 = v._sky_to_screen(60.0, 45.0)
        assert (p1 - p2).manhattanLength() > 1.0, "同一个点在不同投影里应有不同像素位置"
    finally:
        v.close()
