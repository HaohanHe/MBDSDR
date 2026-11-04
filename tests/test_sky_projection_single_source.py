# SPDX-License-Identifier: MIT
"""
tests/test_sky_projection_single_source.py
==========================================
方案③ 第 1+3 步：投影单一事实源 + 可逆性。

契约：
  1. 天球 -> 屏幕只有 `sky_interaction._to_screen_arrays` 一份换算核；
     `sky_to_screen`（单点）与 `sky_to_screen_many`（批量）必须逐点一致，
     `rf_sky_view._sky_to_screen_many` 只许委托，不许再写透视公式；
  2. 透视（专业 120°）与球极（全天 160°）在画布四角、正中、地平线附近
     往返误差 <= 1e-6 px / 1e-6°；
  3. FOV=160° 与 179.9°：可见点半径落在画布内、全部有限；整片天区投影
     无 NaN/Inf、无 1e6 级坐标（离屏点钳制到固定边距）；
  4. 全天模式与专业模式状态隔离；Esc 分层退出；主视场仍 120° 上限。

反证：
  - 若视图层恢复第二套透视批量公式：用例 1 的源码断言与逐点一致必红；
  - 若球极沿用等距 screen_scale：用例 2 的 160° 边缘点往返/半径必红；
  - 若 180° 边缘不夹取：用例 3 出现 Inf/溢出必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import inspect  # noqa: E402
import math  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view  # noqa: E402
from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    PerspectiveProjection,
    StereographicProjection,
    vec_from_azalt,
)
from mbdsdr_ai.sky_interaction import (  # noqa: E402
    ViewState,
    build_view_rotation,
    screen_to_sky,
    sky_to_screen,
    sky_to_screen_many,
)

SIZE = (700, 700)
CORNERS = [(350, 350), (60, 60), (640, 60), (60, 640), (640, 640),
           (350, 60), (350, 640)]


def test_batch_altaz_matches_scalar():
    """批量赤道->地平与单点走同一条链（P·N·R），逐点误差 <= 1e-9°。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation,
        azalt_from_vec,
        azalt_from_vec_many,
        j2000_to_altaz,
        j2000_to_altaz_many,
        vec_from_radec,
    )
    gs = GroundStation(116.0, 40.0)
    jd = 2451545.0 + 4000.0
    ras = [0.0, 45.0, 130.0, 200.0, 310.0]
    decs = [0.0, 40.0, -25.0, 70.0, -80.0]
    vecs = np.array([vec_from_radec(r, d) for r, d in zip(ras, decs)])
    many = j2000_to_altaz_many(vecs, gs, jd)
    az_b, alt_b = azalt_from_vec_many(many)
    for i in range(len(ras)):
        az1, alt1 = azalt_from_vec(j2000_to_altaz(vecs[i], gs, jd))
        daz = abs((float(az_b[i]) - az1 + 180.0) % 360.0 - 180.0)
        assert daz < 1e-9, (i, daz)
        assert abs(float(alt_b[i]) - alt1) < 1e-9, (i, alt1)
    # 零向量：按 (0,0)，不产生 NaN
    az0, alt0 = azalt_from_vec_many(np.array([[0.0, 0.0, 0.0]]))
    assert np.isfinite(az0).all() and np.isfinite(alt0).all()
    assert float(az0[0]) == 0.0 and float(alt0[0]) == 0.0


def _view(fov: float, alt: float = 45.0, az: float = 180.0,
          projection_max: float = 179.999) -> ViewState:
    return ViewState(center_az=az, center_alt=alt, fov_deg=fov,
                     min_fov=0.5, max_fov=projection_max)


def _off_axis_deg(az: float, alt: float, view: ViewState) -> float:
    v = build_view_rotation(view) @ vec_from_azalt(az, alt)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(-v[2])))))


def _angular_sep_deg(az1, alt1, az2, alt2) -> float:
    a1, h1 = math.radians(az1), math.radians(alt1)
    a2, h2 = math.radians(az2), math.radians(alt2)
    c = (math.sin(h1) * math.sin(h2)
         + math.cos(h1) * math.cos(h2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# --------------------------------------------------------------------------
# 1. 单点/批量同一核；视图层不再持有第二套投影公式
# --------------------------------------------------------------------------
@pytest.mark.parametrize("projection", [PerspectiveProjection(),
                                        StereographicProjection()])
def test_scalar_and_batch_match_point_by_point(projection):
    view = _view(160.0 if isinstance(projection, StereographicProjection)
                 else 120.0, alt=45.0)
    az = np.array([180.0, 120.0, 240.0, 180.0, 150.0])
    alt = np.array([45.0, 10.0, 10.0, 0.0, 70.0])
    px, py = sky_to_screen_many(az, alt, view, projection, SIZE)
    for i in range(len(az)):
        sx, sy = sky_to_screen(float(az[i]), float(alt[i]),
                               view, projection, SIZE)
        assert px[i] == pytest.approx(sx, abs=1e-9)
        assert py[i] == pytest.approx(sy, abs=1e-9)


def test_view_layer_delegates_projection_to_single_core():
    src = inspect.getsource(RFSkyView._sky_to_screen_many)
    assert "sky_to_screen_many(" in src
    for banned in ("screen_scale", "vv[:, 2]", "build_view_rotation",
                   "x_n =", "py = cy"):
        assert banned not in src, f"视图层仍有第二套投影公式: {banned}"


# --------------------------------------------------------------------------
# 2. 可逆性：四角/正中/地平线，透视与球极
# --------------------------------------------------------------------------
@pytest.mark.parametrize("projection,fov", [
    (PerspectiveProjection(), 120.0),
    (StereographicProjection(), 160.0),
    (StereographicProjection(), 179.9),
])
def test_screen_to_sky_roundtrip_corners_and_center(projection, fov):
    view = _view(fov, alt=45.0)
    for x, y in CORNERS:
        az, alt = screen_to_sky(float(x), float(y), view, projection, SIZE)
        px, py = sky_to_screen(az, alt, view, projection, SIZE)
        assert math.isfinite(px) and math.isfinite(py)
        assert math.hypot(px - x, py - y) < 1e-6, (x, y, px, py)


@pytest.mark.parametrize("projection,fov", [
    (PerspectiveProjection(), 120.0),
    (StereographicProjection(), 160.0),
    (StereographicProjection(), 179.9),
])
def test_sky_to_screen_roundtrip_horizon_and_edges(projection, fov):
    view = _view(fov, alt=30.0)
    # 地平线附近（alt=0）+ 视场边缘（离轴 = fov/2 - 0.01°）
    for az in (0.0, 45.0, 90.0, 180.0, 270.0):
        for alt in (0.0, 5.0, 30.0, 60.0):
            px, py = sky_to_screen(az, alt, view, projection, SIZE)
            if not (math.isfinite(px) and math.isfinite(py)):
                continue
            # 钳制只作用于画布外的点，可逆性只对可见点承诺
            if not (0.0 <= px <= SIZE[0] and 0.0 <= py <= SIZE[1]):
                continue
            az2, alt2 = screen_to_sky(px, py, view, projection, SIZE)
            assert _angular_sep_deg(az, alt, az2, alt2) < 1e-6, (az, alt)


# --------------------------------------------------------------------------
# 3. 160°/179.9°：有限、有界、可见点落在画布内
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fov", [160.0, 179.9])
def test_wide_fov_projection_finite_and_bounded(fov):
    view = _view(fov, alt=30.0, projection_max=fov)
    az = np.arange(0.0, 360.0, 7.5)
    alt = np.arange(-80.0, 80.1, 5.0)
    az_grid, alt_grid = np.meshgrid(az, alt)
    px, py = sky_to_screen_many(az_grid.ravel(), alt_grid.ravel(),
                                view, StereographicProjection(), SIZE)
    # 整片天区（网格不含精确对跖点）不得出现 NaN/Inf
    assert np.isfinite(px).all() and np.isfinite(py).all()
    # 离屏点钳制到 4 个画布短边的边距，不存在 1e6 级坐标
    lim = 4.0 * min(SIZE)
    cx, cy = SIZE[0] / 2.0, SIZE[1] / 2.0
    assert np.max(np.abs(px - cx)) <= lim + 1e-9
    assert np.max(np.abs(py - cy)) <= lim + 1e-9
    # 可见点（离轴 <= fov/2）投影必须落在画布内
    off = np.array([_off_axis_deg(a, h, view)
                    for a, h in zip(az_grid.ravel(), alt_grid.ravel())])
    vis = off <= fov / 2.0 + 1e-9
    assert vis.any()
    assert np.all(np.abs(px[vis] - cx) <= SIZE[0] / 2.0 + 1e-6)
    assert np.all(np.abs(py[vis] - cy) <= SIZE[1] / 2.0 + 1e-6)


def test_stereographic_screen_scale_is_finite_at_180_boundary():
    proj = StereographicProjection()
    for fov in (160.0, 179.9, 179.999):
        scale = proj.screen_scale(float(min(SIZE)), fov)
        assert math.isfinite(scale) and scale > 0.0
    # 160° 边缘（离轴 80°）归一化半径 2·tan40°，应正好落在 min_dim/2
    r_edge = 2.0 * math.tan(math.radians(160.0) / 4.0)
    assert r_edge * proj.screen_scale(float(min(SIZE)), 160.0) \
        == pytest.approx(min(SIZE) / 2.0, abs=1e-6)


# --------------------------------------------------------------------------
# 4. 全天模式：入口、隔离、Esc 分层退出、上限
# --------------------------------------------------------------------------
def test_all_sky_mode_entry_and_isolation():
    v = RFSkyView()
    try:
        v.resize(700, 700)
        v.view_state.fov_deg = 45.0
        v.view_state.look_at(123.0, 20.0)
        pro_fov = v.view_state.fov_deg
        v.set_all_sky_mode(True)
        assert v.all_sky_active
        assert isinstance(v.projection, StereographicProjection)
        assert v.view_state.fov_deg == pytest.approx(v.ALL_SKY_FOV_DEG)
        assert v.view_state.center_az == pytest.approx(123.0)
        assert v.view_state.center_alt == pytest.approx(20.0)
        v.view_state.zoom_to(200.0)
        assert v.view_state.fov_deg == pytest.approx(160.0)

        v.set_all_sky_mode(False)
        assert not v.all_sky_active
        assert isinstance(v.projection, PerspectiveProjection)
        assert v.view_state.fov_deg == pytest.approx(pro_fov)
        assert v.view_state.center_az == pytest.approx(123.0)
        assert v.view_state.center_alt == pytest.approx(20.0)
    finally:
        v.close()


def test_key_a_toggles_and_escape_exits_only_all_sky():
    v = RFSkyView()
    try:
        v.resize(700, 700)
        events = []
        v.all_sky_mode_changed.connect(events.append)
        v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))
        assert v.all_sky_active
        assert isinstance(v.projection, StereographicProjection)
        assert events == [True]
        v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape,
                                  Qt.NoModifier))
        assert not v.all_sky_active
        assert isinstance(v.projection, PerspectiveProjection)
        assert events == [True, False]
        # 已退出后再按 Esc：本视图不得再抢事件/再发切换信号
        v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape,
                                  Qt.NoModifier))
        assert events == [True, False]
    finally:
        v.close()


def test_professional_fov_cap_is_still_120():
    v = RFSkyView()
    try:
        v.set_all_sky_mode(False)      # no-op
        v.view_state.zoom_to(180.0)
        assert v.view_state.fov_deg == pytest.approx(120.0)
        assert v.view_state.max_fov == pytest.approx(120.0)
    finally:
        v.close()
