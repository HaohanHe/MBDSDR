#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
goto 缓动 offscreen 驱动测试 (tests/test_goto_anim_driver.py)
=============================================================

RFSkyView 侧的动画驱动钉扎（假时钟注入，不绑墙钟——测试确定性纪律）：
  * goto_animated 建立动画并记录起点墙钟；
  * _step_goto_anim 按假时钟推进，终点与帧数无关（帧率解耦）；
  * 完成后再步进不回头（动画清空）；
  * 拖拽/滚轮打断进行中的飞行；
  * 点星 on_object_picked 触发飞行到目标 az/el。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_goto_anim_driver.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def sky(qapp):
    from rf_sky_view import RFSkyView, SkyObject
    view = RFSkyView()
    view.resize(400, 400)
    view.show()
    # 假时钟：1000.0 ms 起步，手动拨针
    clock = {"t": 1000.0}
    view._goto_clock = lambda: clock["t"]
    view._test_clock = clock
    obj = SkyObject("TEST-SAT", azimuth_deg=40.0, elevation_deg=60.0)
    view._objects.append(obj)
    yield view
    view.close()


class TestDriver:
    def test_goto_animated_sets_anim(self, sky):
        sky.view_state.look_at(0.0, 20.0)
        sky.goto_animated(40.0, 60.0)
        assert sky._goto_anim is not None
        assert sky._goto_start_ms == 1000.0

    def test_step_advances_toward_target(self, sky):
        sky._test_clock["t"] = 1100.0  # 半程
        sky._step_goto_anim()
        assert sky._goto_anim is not None  # 未完成
        assert sky.view_state.center_alt > 20.0
        assert sky.view_state.center_alt < 60.0

    def test_completion_reaches_target_and_clears(self, sky):
        sky._test_clock["t"] = 1300.0  # 超过 200ms 时长
        sky._step_goto_anim()
        assert sky._goto_anim is None
        assert abs(sky.view_state.center_az - 40.0) < 1e-9
        assert abs(sky.view_state.center_alt - 60.0) < 1e-9

    def test_step_after_done_is_noop(self, sky):
        sky._step_goto_anim()
        assert abs(sky.view_state.center_alt - 60.0) < 1e-9

    def test_framerate_independence(self, sky):
        """同样 200ms：一次 33ms 步进 vs 十次 20ms 步进，终点一致。"""
        from mbdsdr_ai.sky_interaction import ViewState
        results = []
        for steps, dt in ((1, 200.0), (10, 20.0)):
            sky.view_state = ViewState(center_az=0.0, center_alt=10.0,
                                       fov_deg=120.0)
            clock = {"t": 0.0}
            sky._goto_clock = lambda: clock["t"]
            sky.goto_animated(30.0, 45.0)
            for _ in range(steps):
                clock["t"] += dt
                sky._step_goto_anim()
            results.append((sky.view_state.center_az,
                            sky.view_state.center_alt))
            sky._goto_clock = lambda: 1000.0
        (az1, alt1), (az2, alt2) = results
        assert abs(az1 - az2) < 1e-9
        assert abs(alt1 - alt2) < 1e-9
        assert abs(alt1 - 45.0) < 1e-9


class TestInterruption:
    def test_mouse_press_cancels_flight(self, sky):
        sky.goto_animated(200.0, 70.0)
        assert sky._goto_anim is not None
        sky.mousePressEvent((10.0, 10.0))
        assert sky._goto_anim is None

    def test_wheel_cancels_flight(self, sky):
        sky.goto_animated(200.0, 70.0)
        assert sky._goto_anim is not None
        sky.wheelEvent(120)
        assert sky._goto_anim is None

    def test_new_goto_replaces_old(self, sky):
        sky.goto_animated(100.0, 50.0)
        first = sky._goto_anim
        sky.goto_animated(200.0, 60.0)
        assert sky._goto_anim is not None
        assert sky._goto_anim is not first

    def test_pick_flies_to_object(self, sky):
        sky.view_state.look_at(0.0, 10.0)
        obj = sky._objects[0]
        sky.on_object_picked(obj)
        assert sky._goto_anim is not None
        assert abs(sky._goto_anim.target_az - 40.0) < 1e-9
        assert abs(sky._goto_anim.target_alt - 60.0) < 1e-9
        sky._cancel_goto_anim()


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
