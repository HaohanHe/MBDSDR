# SPDX-License-Identifier: MIT
"""投影有效性域与跨域裁剪守卫（spec Task 10.2）。

参考 aladin-lite `src/core/src/math/projection/domain/`（LGPLv3）的
**ProjDef + SDF 思路**：把「投影在画面上的有效区域」写成有符号距离
（``sdf <= 0`` 为域内），越界顶点推到边界；代码全部重写，未复制其源码。

本仓四个投影的有效域都是解析的，所以边界用「大圆弧二分」求，而不是
沿 SDF 迭代推进（见 `mbdsdr_ai/sky_interaction.py::clip_segment_to_domain`
文档）。本文件锁三件事：

1. `is_valid_vec` / `validity_mask` 与各自 `_forward` 的「有定义」判据一致；
2. `domain_sdf` / `is_in_domain` 的圆盘半径与符号正确；
3. 星官连线「一端在域内、一端在域外」时裁到边界落笔——**旧实现会把整段
   丢掉**，所以本条同时是反证：若有人退回「任一端不可见就跳过」，它必红。
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    AzimuthalEquidistantProjection,
    OrthographicProjection,
    PerspectiveProjection,
    StereographicProjection,
    vec_from_azalt,
)
from mbdsdr_ai.sky_interaction import (  # noqa: E402
    ViewState,
    clip_segment_to_domain,
    direction_is_drawable,
)

PROJECTIONS = [
    PerspectiveProjection(),
    StereographicProjection(),
    OrthographicProjection(),
    AzimuthalEquidistantProjection(),
]


def _grid_vectors():
    """覆盖全天的视空间方向，另加六个轴向（含正/反对跖点）。"""
    out = []
    for az in range(0, 360, 15):
        for alt in range(-85, 86, 10):
            out.append(vec_from_azalt(float(az), float(alt)))
    for v in ([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
              [0.0, 1.0, 0.0], [0.0, -1.0, 0.0],
              [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]):
        out.append(np.array(v))
    return np.array(out)


@pytest.mark.parametrize("projection", PROJECTIONS,
                         ids=lambda p: p.name)
def test_validity_mask_matches_defined_projection(projection):
    """「有屏幕坐标」的判定必须与投影本身一致，不只靠有限值去猜。"""
    vecs = _grid_vectors()
    mask = np.asarray(projection.validity_mask(vecs), dtype=bool)
    assert len(mask) == len(vecs)
    assert mask.any() and (~mask).any(), "网格必须同时覆盖有效与无效方向"

    for v, ok in zip(vecs, mask):
        x, y = projection.project_vec(v)
        defined = math.isfinite(x) and math.isfinite(y)
        if ok:
            assert defined, f"{projection.name} 判有效却给不出坐标: {v}"
        else:
            # 球极对跖点给出 FAR_SENTINEL（有限哨兵），其余投影给 NaN/Inf；
            # 两者都不许被绘制端当成「可画」。
            assert (not defined) or abs(x) >= 1e5, (
                f"{projection.name} 判无效却给了正常坐标: {v} -> {(x, y)}")


def test_domain_sdf_signs_and_radii():
    ortho = OrthographicProjection()
    assert ortho.is_in_domain(0.0, 0.0)
    assert not ortho.is_in_domain(1.01, 0.0)
    assert ortho.domain_sdf(0.5, 0.0) == pytest.approx(-0.5)
    assert ortho.domain_sdf(1.5, 0.0) == pytest.approx(0.5)

    aeqd = AzimuthalEquidistantProjection()
    assert aeqd.is_in_domain(3.0, 0.0)
    assert not aeqd.is_in_domain(3.2, 0.0)
    assert aeqd.domain_sdf(0.0, math.pi) == pytest.approx(0.0, abs=1e-12)

    # 透视/球极的有限坐标处处有效（域是全平面）。
    for proj in (PerspectiveProjection(), StereographicProjection()):
        assert proj.is_in_domain(1e6, -1e6)
        assert proj.domain_sdf(1e6, -1e6) <= 0.0


def test_clip_segment_matches_analytic_boundary():
    """赤道上的段 + 解析域 ``az <= 45``：边界必须落到 45°，顺序不乱。"""
    drawable = lambda az, alt: az <= 45.0 + 1e-12   # noqa: E731
    project = lambda az, alt: (az, alt)             # noqa: E731

    both_in = clip_segment_to_domain(0.0, 0.0, 30.0, 0.0, drawable, project)
    assert both_in == [(0.0, 0.0), (30.0, 0.0)]

    both_out = clip_segment_to_domain(60.0, 0.0, 90.0, 0.0, drawable, project)
    assert both_out == []

    forward = clip_segment_to_domain(0.0, 0.0, 90.0, 0.0, drawable, project,
                                     iterations=40)
    assert len(forward) == 2
    assert forward[0] == pytest.approx((0.0, 0.0))
    assert forward[1][0] == pytest.approx(45.0, abs=1e-6)

    reverse = clip_segment_to_domain(90.0, 0.0, 0.0, 0.0, drawable, project,
                                     iterations=40)
    assert len(reverse) == 2
    assert reverse[0][0] == pytest.approx(45.0, abs=1e-6)
    assert reverse[1] == pytest.approx((0.0, 0.0))


def test_direction_is_drawable_uses_projection_domain():
    view = ViewState(center_az=0.0, center_alt=0.0, fov_deg=120.0,
                     min_fov=0.5, max_fov=120.0)
    persp = PerspectiveProjection()
    assert direction_is_drawable(0.0, 0.0, view, persp)
    assert direction_is_drawable(80.0, 0.0, view, persp)
    # 离轴 90° = 透视地平线，再往后没有屏幕坐标。
    assert not direction_is_drawable(90.0, 0.0, view, persp)
    assert not direction_is_drawable(180.0, 0.0, view, persp)

    stereo = StereographicProjection()
    assert direction_is_drawable(179.0, 0.0, view, stereo)
    assert not direction_is_drawable(180.0, 0.0, view, stereo)


# --------------------------------------------------------------------------
# Qt 端：星官连线跨边界裁剪
# --------------------------------------------------------------------------
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(700, 700)
    v.set_refraction_params(enabled=False)
    v.view_state.look_at(0.0, 45.0)
    v.view_state.zoom_to(120.0)
    return v


def _prepared(az1: float, alt1: float, az2: float, alt2: float):
    prepared = {
        "keys": ("a", "b"),
        "vecs": np.array([vec_from_azalt(az1, alt1),
                          vec_from_azalt(az2, alt2)]),
        "index": {"a": 0, "b": 1},
        "region": (("a", "b"),),
        "detail": (),
        "labels": (),
    }
    return prepared, np.array([az1, az2]), np.array([alt1, alt2])


def test_constel_path_clips_crossing_segment_instead_of_dropping_it():
    """一端在相机背后：旧实现整段丢，新实现裁到边界（moveTo+lineTo）。"""
    v = _new_view()
    try:
        prepared, az, alt = _prepared(0.0, 45.0, 180.0, 45.0)
        points = {"a": QPointF(350.0, 350.0), "b": None}
        path = v._constel_path(prepared["region"], points, prepared, az, alt)
        assert path.elementCount() == 2, (
            "跨投影边界的线段被整段丢弃（应裁到有效域边界）")

        empty = v._constel_path(prepared["region"],
                                {"a": None, "b": None}, prepared, az, alt)
        assert empty.elementCount() == 0
    finally:
        v.close()


def test_constel_path_keeps_normally_visible_segment_unchanged():
    v = _new_view()
    try:
        prepared, az, alt = _prepared(0.0, 45.0, 10.0, 40.0)
        points = {"a": QPointF(350.0, 350.0), "b": QPointF(420.0, 320.0)}
        path = v._constel_path(prepared["region"], points, prepared, az, alt)
        assert path.elementCount() == 2
        assert path.elementAt(0).x == pytest.approx(350.0)
        assert path.elementAt(1).x == pytest.approx(420.0)
    finally:
        v.close()
