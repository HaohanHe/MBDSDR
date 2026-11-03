# SPDX-License-Identifier: MIT
"""
tests/test_selection_crosshair.py
=================================
验证选中标记的"呼吸"玩法（2026-11-03 从 Stellarium Web 录屏学到）：

  1. 卫星选中 = 4 段 90° 等分圆弧十字 + 2s 呼吸（纯几何确定性）；
  2. 天体（月/行星/恒星/DSO）选中 = 2s 呼吸环半径（纯几何确定性）；
  3. 部件留出 _breath_clock 时间缝，注入固定时刻可复现，不绑墙钟；
  4. 选中卫星后 offscreen 重绘不崩，标记真的画在卫星屏位附近。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_selection_crosshair.py -v
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view as _rfs  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView, SkyObject,
    selection_crosshair_geometry, breathing_ring_radius,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0


class TestCrosshairGeometryPure:
    """纯几何：4 段圆弧十字 + 2s 呼吸半径，时间注入不读墙钟。"""

    def test_four_dashes_ninety_apart(self):
        geo = selection_crosshair_geometry(0.0)
        dashes = geo["dashes"]
        assert len(dashes) == 4, "十字必须 4 段圆弧"
        # 4 段中心（起始角 + 跨度/2）应两两相差 90°
        centers = sorted((s + w / 2.0) % 360.0 for s, w in dashes)
        gaps = [(centers[(i + 1) % 4] - centers[i]) % 360.0
                for i in range(4)]
        for g in gaps:
            assert abs(g - 90.0) < 1e-6, f"4 段应 90° 等分，实际间隔 {gaps}"

    def test_all_dashes_same_sweep(self):
        geo = selection_crosshair_geometry(0.0, dash_sweep=30.0)
        sweeps = {round(w, 6) for _, w in geo["dashes"]}
        assert sweeps == {30.0}, "4 段跨度应一致"

    def test_period_is_two_seconds(self):
        assert selection_crosshair_geometry(0.0)["period"] == 2.0, \
            "呼吸周期必须 2s（SWE 同款）"

    def test_breathing_radius_over_one_period(self):
        base, amp = 14.0, 1.6
        # t=0 -> sin0=0 -> 半径=base（确定性，不绑墙钟）
        assert abs(selection_crosshair_geometry(0.0, base, amp)["radius"]
                   - base) < 1e-9
        # t=period/4 -> sin(pi/2)=1 -> 半径=base+amp（吸气峰）
        assert abs(selection_crosshair_geometry(0.5, base, amp)["radius"]
                   - (base + amp)) < 1e-9
        # t=period/2 -> sin(pi)=0 -> 回到 base
        assert abs(selection_crosshair_geometry(1.0, base, amp)["radius"]
                   - base) < 1e-9
        # t=3*period/4 -> sin(3pi/2)=-1 -> 半径=base-amp（呼气谷）
        assert abs(selection_crosshair_geometry(1.5, base, amp)["radius"]
                   - (base - amp)) < 1e-9

    def test_radius_periodic(self):
        a = selection_crosshair_geometry(0.37, 14.0)["radius"]
        b = selection_crosshair_geometry(0.37 + 2.0, 14.0)["radius"]
        assert abs(a - b) < 1e-9, "2s 后半径应完全复现（周期函数）"


class TestBreathingRingPure:
    """天体呼吸环半径：与十字同律（2s），时间注入不读墙钟。"""

    def test_ring_breathes(self):
        assert abs(breathing_ring_radius(0.0, 16.0, 1.6) - 16.0) < 1e-9
        assert abs(breathing_ring_radius(0.5, 16.0, 1.6) - 17.6) < 1e-9
        assert abs(breathing_ring_radius(1.5, 16.0, 1.6) - 14.4) < 1e-9

    def test_ring_periodic(self):
        a = breathing_ring_radius(0.8, 16.0)
        b = breathing_ring_radius(0.8 + 2.0, 16.0)
        assert abs(a - b) < 1e-9


def _new_view():
    eng = get_time_engine()
    eng.set_unix(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp())
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON, 0.0)
    return v


class TestWidgetSeam:
    """部件缝：呼吸时钟可注入固定时刻，重绘不崩且画在卫星屏位附近。"""

    def test_breath_clock_injectable(self):
        v = _new_view()
        assert callable(v._breath_clock), "应留 _breath_clock 时间缝"
        v._breath_clock = lambda: 0.5   # 注入吸气峰时刻
        assert abs(v._breath_clock() - 0.5) < 1e-12

    def test_has_crosshair_painter(self):
        v = _new_view()
        assert hasattr(v, "_draw_selection_crosshair"), \
            "应有 _draw_selection_crosshair 绘制法"

    def test_selected_satellite_paints_marker(self):
        v = _new_view()
        v._breath_clock = lambda: 0.5   # 固定时刻：半径=base+amp，可复现
        v.add_object(SkyObject("SAT A", 90.0, 45.0, obj_type="satellite"))
        obj = v._objects[0]
        v.on_object_picked(obj)
        assert v._hovered_object is obj
        v.view_state.look_at(90.0, 45.0)
        v.show()
        app.processEvents()
        v.repaint()
        img = v.grab().toImage()
        pos = v._sky_to_screen(90.0, 45.0)
        cx, cy = int(round(pos.x())), int(round(pos.y()))
        # 十字半径 ~15.6px：环带（半径 12..20）应有点亮像素（accent 标记）
        lit = 0
        for dy in range(-20, 21):
            for dx in range(-20, 21):
                d = math.hypot(dx, dy)
                if not (11.0 <= d <= 20.0):
                    continue
                c = img.pixelColor(cx + dx, cy + dy)
                if c.red() + c.green() + c.blue() > 90:
                    lit += 1
        assert lit > 3, f"选中卫星应在环带画出十字标记，实测亮点 {lit}"
        v.close()


class TestNoDoubleRingRegression:
    """回归：_draw_picked_info 不再自画静态双环（十字由 _draw_objects 统一）。"""

    def test_picked_info_has_no_static_double_ring(self):
        src = open(os.path.join(_ROOT, "desktop", "rf_sky_view.py"),
                   encoding="utf-8").read()
        i = src.index("def _draw_picked_info")
        j = src.index("def ", i + 10)
        body = src[i:j]
        assert "drawEllipse(pos, 12, 12)" not in body, \
            "_draw_picked_info 不应再画静态双环（改由呼吸十字统一）"
        assert "drawEllipse(pos, 16, 16)" not in body
