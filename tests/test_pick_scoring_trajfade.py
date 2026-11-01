# SPDX-License-Identifier: MIT
"""
点选评分（cleverFind 双键）+ 轨迹端部淡出 钉扎测试
====================================================

  1. _find_object_at：候选门 12px 不变；等亮度时近者优先；距离接近时
     高信号（signal_strength_db 归一）者优先；门外返回 None。
  2. _traj_alpha：n 点轨迹两端 fade_frac 段数线性渐入/出，中段恒 base；
     短线退化（n=2 单段取首段 ramp）；非法下标返回 0。
  3. 轨迹含 el<=0 段时断开只画可见段，grab 渲染不崩。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_pick_scoring_trajfade.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, SkyObject, _traj_alpha  # noqa: E402


def _view() -> RFSkyView:
    v = RFSkyView()
    v.resize(600, 600)
    v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
    return v


# ----------------------------------------------------------------------
class TestPickScoring:
    def test_nearer_wins_equal_brightness(self):
        v = _view()
        a = SkyObject("NEAR", 30.0, 50.0, signal_strength_db=-30.0)
        b = SkyObject("FAR", 30.0, 46.0, signal_strength_db=-30.0)
        v._objects.extend([a, b])
        pa = v._sky_to_screen(30.0, 50.0)
        pb = v._sky_to_screen(30.0, 46.0)
        # 点击 NEAR 正上方一点（仍距 NEAR 更近）
        x = pa.x()
        y = pa.y() - 3.0
        assert v._find_object_at(x, y) is a

    def test_brighter_wins_close_distances(self):
        v = _view()
        dim = SkyObject("DIM", 30.0, 50.0, signal_strength_db=-55.0)
        bright = SkyObject("BRIGHT", 30.0, 49.0, signal_strength_db=-5.0)
        v._objects.extend([dim, bright])
        pd = v._sky_to_screen(30.0, 50.0)
        pb = v._sky_to_screen(30.0, 49.0)
        # 两候选像素距点击点都 < 12px（距离接近），亮者应赢
        mid_x = (pd.x() + pb.x()) / 2.0
        mid_y = (pd.y() + pb.y()) / 2.0
        assert v._find_object_at(mid_x, mid_y) is bright

    def test_gate_outside_returns_none(self):
        v = _view()
        v._objects.append(SkyObject("SOLO", 30.0, 50.0,
                                    signal_strength_db=-10.0))
        p = v._sky_to_screen(30.0, 50.0)
        assert v._find_object_at(p.x() + 40.0, p.y()) is None
        assert v._find_object_at(p.x(), p.y()) is not None


# ----------------------------------------------------------------------
class TestTrajAlpha:
    def test_endpoints_fade_mid_full(self):
        n = 20
        assert _traj_alpha(0, n) < _traj_alpha(2, n)
        assert _traj_alpha(n - 2, n) < _traj_alpha(n - 4, n)
        assert _traj_alpha(9, n) == 110          # 中段恒满
        assert _traj_alpha(0, n) == _traj_alpha(n - 2, n)  # 两端对称

    def test_short_degenerates(self):
        assert _traj_alpha(0, 2) > 0             # 单段取首段 ramp
        assert _traj_alpha(5, 2) == 0            # 非法下标

    def test_paint_with_horizon_break(self):
        v = _view()
        pts = [(0.0, 30.0), (10.0, 40.0), (20.0, -5.0),   # 地平线断开
               (30.0, 35.0), (40.0, 45.0)]
        v._trajectories["T"] = pts
        v.grab()                                 # 渲染不崩即过
