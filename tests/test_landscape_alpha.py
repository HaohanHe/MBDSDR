# SPDX-License-Identifier: MIT
"""
tests/test_landscape_alpha.py
=============================
SWE §1 地景透明度规则的落地断言（见 output/ui-prototype/swe-study-notes.md）：

  alpha_fov  = smoothstep(1°,20°,fov)   —— 放大到紧 fov 地景淡出防糊
  alpha_down = 0.5+0.5·smoothstep(-45°,0°,视线alt) —— 俯视地下渐隐一半
  地景整体不透明度 = alpha_fov × alpha_down；≤~0 时 _draw_landscape 整层跳过。

纯函数用精确值锚定；widget 层用"轮廓计算调用数"探针验证 guard 真接线，
不依赖像素阈值（fov 同时改几何与透明度，像素比对会被投影尺度混淆）。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_landscape_alpha.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import desktop.rf_sky_view as mod  # noqa: E402
from desktop.rf_sky_view import (  # noqa: E402
    RFSkyView, _landscape_alpha, _smoothstep,
)


def _new_view(size: int = 400) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
    return v


# ----------------------------------------------------------------------
class TestSmoothstepPure:
    def test_edges_and_midpoint(self):
        assert _smoothstep(0.0, 1.0, 0.0) == pytest.approx(0.0)
        assert _smoothstep(0.0, 1.0, 1.0) == pytest.approx(1.0)
        assert _smoothstep(0.0, 1.0, 0.5) == pytest.approx(0.5)

    def test_clamps_outside(self):
        assert _smoothstep(0.0, 1.0, -3.0) == 0.0
        assert _smoothstep(0.0, 1.0, 5.0) == 1.0

    def test_reverse_edges(self):
        # edge0 > edge1：反向衰减
        assert _smoothstep(1.0, 0.0, 1.0) == pytest.approx(0.0)
        assert _smoothstep(1.0, 0.0, 0.0) == pytest.approx(1.0)
        assert _smoothstep(1.0, 0.0, 0.5) == pytest.approx(0.5)

    def test_monotonic_nondecreasing(self):
        xs = [i / 20.0 for i in range(-5, 26)]
        ys = [_smoothstep(0.0, 1.0, x) for x in xs]
        assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))


# ----------------------------------------------------------------------
class TestLandscapeAlphaPure:
    def test_wide_fov_above_horizon_is_full(self):
        assert _landscape_alpha(120.0, 90.0) == pytest.approx(1.0)
        assert _landscape_alpha(60.0, 10.0) == pytest.approx(1.0)
        assert _landscape_alpha(20.0, 0.0) == pytest.approx(1.0)

    def test_tight_fov_fades_out(self):
        # fov<=1° 完全淡出
        assert _landscape_alpha(1.0, 0.0) == pytest.approx(0.0)
        assert _landscape_alpha(0.5, 0.0) == pytest.approx(0.0)

    def test_reachable_min_fov_fades_substantially(self):
        # 本视图 min_fov=5°：淡到 ~11%，不是 0（仍可见但很淡）
        a = _landscape_alpha(5.0, 90.0)
        assert 0.05 < a < 0.20

    def test_fov_monotonic(self):
        a_tight = _landscape_alpha(5.0, 10.0)
        a_mid = _landscape_alpha(12.0, 10.0)
        a_wide = _landscape_alpha(30.0, 10.0)
        assert a_tight < a_mid < a_wide
        assert a_wide == pytest.approx(1.0)

    def test_look_down_fades_to_half(self):
        assert _landscape_alpha(60.0, 0.0) == pytest.approx(1.0)
        assert _landscape_alpha(60.0, -45.0) == pytest.approx(0.5)
        assert _landscape_alpha(60.0, -90.0) == pytest.approx(0.5)
        assert _landscape_alpha(60.0, -22.5) == pytest.approx(0.75)

    def test_combined_tight_and_down(self):
        # 紧 fov + 俯视：两因子相乘 -> 近 0
        assert _landscape_alpha(1.0, -90.0) == pytest.approx(0.0)

    def test_always_in_unit_range(self):
        for fov in (0.5, 1, 3, 5, 10, 20, 45, 90, 120, 180):
            for alt in (-90, -60, -45, -10, 0, 30, 90):
                a = _landscape_alpha(float(fov), float(alt))
                assert 0.0 <= a <= 1.0


# ----------------------------------------------------------------------
class TestDrawLandscapeWiring:
    """探针验证 _draw_landscape 真用 _landscape_alpha 并据此跳过/渲染。"""

    def _spy_profile(self, monkeypatch):
        calls = {"n": 0}
        real = mod._landscape_layer_profile

        def spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(mod, "_landscape_layer_profile", spy)
        return calls

    def test_guard_receives_view_fov_and_alt(self, monkeypatch):
        v = _new_view()
        seen = {}

        def fake_alpha(fov, alt):
            seen["fov"], seen["alt"] = fov, alt
            return 1.0

        monkeypatch.setattr(mod, "_landscape_alpha", fake_alpha)
        self._spy_profile(monkeypatch)
        img = QImage(v.size(), QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        v._draw_landscape(p)
        p.end()
        assert seen["fov"] == v.view_state.fov_deg
        assert seen["alt"] == v.view_state.center_alt

    def test_zero_alpha_skips_layer(self, monkeypatch):
        v = _new_view()
        monkeypatch.setattr(mod, "_landscape_alpha", lambda fov, alt: 0.0)
        calls = self._spy_profile(monkeypatch)
        img = QImage(v.size(), QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        v._draw_landscape(p)
        p.end()
        assert calls["n"] == 0            # 整层跳过，轮廓一次都不算

    def test_full_alpha_renders_silhouette(self, monkeypatch):
        v = _new_view()
        monkeypatch.setattr(mod, "_landscape_alpha", lambda fov, alt: 1.0)
        calls = self._spy_profile(monkeypatch)
        img = QImage(v.size(), QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        v._draw_landscape(p)
        p.end()
        assert calls["n"] > 0             # 正常渲染：逐方位算剪影轮廓

    def test_reachable_min_fov_still_draws_faded(self):
        # 真实 min_fov=5° -> alpha≈0.11 > 阈值：不跳过（只是很淡），
        # 保证用户放到最紧时地景不会硬消失。
        v = _new_view()
        v.view_state.zoom_to(v.view_state.min_fov)
        assert mod._landscape_alpha(v.view_state.fov_deg,
                                    v.view_state.center_alt) > 0.003
