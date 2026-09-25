"""
tests/test_sky_interaction_offscreen.py
=======================================
验证 desktop/rf_sky_view.py 已真正接通 mbdsdr_ai/sky_interaction.py 的
SkyInteractionHandler（Stellarium 视角模型独立重实现）：

  1. RFSkyView 多继承 SkyInteractionHandler，具备 view_state/projection/sky_objects；
  2. 点击已知天体的屏幕坐标 -> object_clicked 信号收到正确对象（正反投影统一）；
  3. 点击空白处 -> 不选中任何对象、不发信号；
  4. 滚轮事件 -> fov_deg 变化（指数缩放）；
  5. 拖拽 -> center_az/center_alt 变化（反投影平移）；
  6. reset_view() -> 回到天顶居中 + FOV=120°。

运行（可独立运行）：
    QT_QPA_PLATFORM=offscreen python3 tests/test_sky_interaction_offscreen.py
或：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_sky_interaction_offscreen.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
import unittest  # noqa: E402

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from mbdsdr_ai.sky_interaction import sky_to_screen  # noqa: E402


# ----------------------------------------------------------------------
def _new_view(size: int = 600) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    return v


def _click(v: RFSkyView, x: float, y: float) -> None:
    """模拟左键按下+释放（视为一次点击）。"""
    for typ in (QMouseEvent.Type.MouseButtonPress,
                QMouseEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(typ, QPointF(x, y),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        v.mousePressEvent(ev) if typ == QMouseEvent.Type.MouseButtonPress \
            else v.mouseReleaseEvent(ev)


def _drag(v: RFSkyView, x0: float, y0: float, x1: float, y1: float) -> None:
    """模拟左键按下 -> 多次移动 -> 释放。"""
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x0, y0),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    v.mousePressEvent(press)
    steps = 10
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        y = y0 + (y1 - y0) * i / steps
        mv = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x, y),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        v.mouseMoveEvent(mv)
    rel = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x1, y1),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    v.mouseReleaseEvent(rel)


# ----------------------------------------------------------------------
class TestHandlerWiring(unittest.TestCase):
    def test_view_state_and_projection_present(self):
        from mbdsdr_ai.sky_interaction import ViewState, SkyInteractionHandler
        v = _new_view()
        assert isinstance(v.view_state, ViewState)
        assert isinstance(v, SkyInteractionHandler)
        assert v.projection is not None
        # 默认天顶居中 + FOV=120
        assert abs(v.view_state.center_alt - 90.0) < 1e-3
        assert abs(v.view_state.fov_deg - 120.0) < 1e-3


class TestClickPick(unittest.TestCase):
    def test_click_object_emits_signal(self):
        v = _new_view()
        objs = [
            SkyObject("NOAA 15", 45.0, 60.0, frequency_hz=137.62e6),
            SkyObject("ISS", 180.0, 30.0, frequency_hz=145.8e6),
            SkyObject("NOAA 19", 300.0, 75.0, frequency_hz=137.1e6),
        ]
        v.set_objects(objs)

        picked = []
        v.object_clicked.connect(lambda o: picked.append(o))

        # 用与 handler 同一套正反投影算出 NOAA15 的屏幕坐标
        sx, sy = sky_to_screen(45.0, 60.0, v.view_state, v.projection,
                               (v.width(), v.height()))
        _click(v, sx, sy)

        assert len(picked) == 1, f"应选中 1 个对象, 实际 {len(picked)}"
        assert picked[0].name == "NOAA 15"
        assert v._hovered_object is objs[0]

    def test_click_blank_selects_nothing(self):
        v = _new_view()
        v.set_objects([SkyObject("A", 45.0, 60.0)])
        picked = []
        v.object_clicked.connect(lambda o: picked.append(o))

        # 点击角落（远离任何天体，且离屏）
        _click(v, 5.0, 5.0)
        assert picked == [], f"空白处不应发 object_clicked, 实际 {picked}"
        assert v._hovered_object is None


class TestWheelZoom(unittest.TestCase):
    def test_wheel_changes_fov(self):
        v = _new_view()
        fov0 = v.view_state.fov_deg
        # handler 的 _event_wheel_delta 接受纯数值（等价 angleDelta=120）
        v.wheelEvent(120)  # 上滚 -> 放大 -> fov 变小
        fov1 = v.view_state.fov_deg
        assert fov1 < fov0, f"上滚应缩小 FOV: {fov0} -> {fov1}"
        # 再下滚
        v.wheelEvent(-120)
        fov2 = v.view_state.fov_deg
        assert fov2 > fov1, f"下滚应放大 FOV: {fov1} -> {fov2}"
        # 夹取范围内
        assert v.view_state.min_fov <= fov2 <= v.view_state.max_fov


class TestDragPan(unittest.TestCase):
    def test_drag_changes_center(self):
        v = _new_view()
        v.set_objects([])
        # 天顶是方位角极点（az 奇异），先双击把视角中心移离极点，再拖拽。
        v.mouseDoubleClickEvent(QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick, QPointF(250, 250),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        az0, alt0 = v.view_state.center_az, v.view_state.center_alt
        # 从屏幕中心水平拖拽
        cx, cy = v.width() / 2.0, v.height() / 2.0
        _drag(v, cx, cy, cx + 80.0, cy)
        az1, alt1 = v.view_state.center_az, v.view_state.center_alt
        daz = abs((az1 - az0 + 180) % 360 - 180)
        self.assertGreater(daz, 1.0,
                           f"拖拽应改变 center_az: {az0} -> {az1}")


class TestResetView(unittest.TestCase):
    def test_reset_view_returns_to_zenith(self):
        v = _new_view()
        # 先双击移走 + 滚轮缩放，再 reset
        v.mouseDoubleClickEvent(QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick, QPointF(200, 200),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        v.wheelEvent(240)
        self.assertNotAlmostEqual(v.view_state.fov_deg, 120.0, places=1)
        self.assertGreater(abs(v.view_state.center_alt - 90.0), 1.0)
        v.reset_view()
        self.assertAlmostEqual(v.view_state.center_alt, 90.0, places=3)
        self.assertAlmostEqual(v.view_state.center_az, 0.0, places=3)
        self.assertAlmostEqual(v.view_state.fov_deg, 120.0, places=3)


# ----------------------------------------------------------------------
if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
