# SPDX-License-Identifier: MIT
"""桌面 AR 指向模式接线（Task 55）的离屏契约。

守四件事：

1. 默认关闭：不生成 HUD，paintEvent 前后 view_state 与既有交互不变；
2. 开启 + 位姿：视图跟随，HUD 指令按级别生成；
3. 用户手动接管（L4）：后续位姿更新不再覆盖视角；
4. 无位姿：只显示「还没对准」，不画假目标。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

POSE = {"valid": True, "az_deg": 33.0, "alt_deg": 52.0,
        "roll_deg": 0.0, "fov_deg": 2.3}
POSE_B = {"valid": True, "az_deg": 200.0, "alt_deg": 10.0,
          "roll_deg": 0.0, "fov_deg": 2.3}
TARGETS = [
    {"name": "角宿一", "az_deg": 33.2, "alt_deg": 52.1, "vmag": 1.0},
    {"name": "试验卫星", "az_deg": 32.8, "alt_deg": 51.9, "vmag": 3.5,
     "kind": "satellite"},
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _view():
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    v.resize(800, 600)
    return v


def _snapshot(v):
    return (float(v.view_state.center_az), float(v.view_state.center_alt),
            float(v.view_state.fov_deg), float(v.view_state.rotation))


def test_default_off_does_not_change_paint_or_interaction(qapp):
    v = _view()
    try:
        v.view_state.look_at(10.0, 20.0)
        v.view_state.zoom_to(60.0)
        before = _snapshot(v)

        assert v.sky_pointing_mode is False
        assert v.sky_hud is None
        # 关闭状态下喂位姿必须原样返回 False，且不产生 HUD。
        assert v.update_sky_pose(POSE, TARGETS, "L3") is False
        assert v.sky_hud is None

        v.grab()                     # 触发 paintEvent
        assert _snapshot(v) == before
        assert v.sky_hud is None

        # 既有交互仍按原语义工作（没有指向模式钩子介入）。
        v.view_state.look_at(100.0, 30.0)
        assert v.view_state.center_az == pytest.approx(100.0)
        v.view_state.zoom(1.0)
        assert v.view_state.fov_deg < 60.0
        v.view_state.pan(5.0, -2.0)
        assert v.view_state.center_az == pytest.approx(95.0)
    finally:
        v.close()


def test_enable_then_pose_follows_and_builds_hud(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.sky_pointing_mode is True
        # 刚开启、还没有位姿：只有准星 +「还没对准」，不画假目标。
        hud = v.sky_hud
        assert hud is not None and hud["hint"] == "还没对准"
        assert hud["labels"] == []

        assert v.update_sky_pose(POSE, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(2.3)

        hud = v.sky_hud
        assert hud["level"] == "L3"
        assert hud["hint"] == "已锁定"
        assert hud["labels"], "L3 应生成精确标签"
        assert "角宿一" in [lab["name"] for lab in hud["labels"]]
        assert [m["name"] for m in hud["satellite_markers"]] == ["试验卫星"]
        assert hud["crosshair"]["x"] == pytest.approx(400.0)
        assert hud["crosshair"]["y"] == pytest.approx(300.0)

        v.grab()                     # HUD 绘制路径不崩
    finally:
        v.close()


def test_manual_override_blocks_later_pose(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.update_sky_pose(POSE, TARGETS, "L3")
        v.set_manual_override(True)
        assert v.sky_manual_override is True
        assert v.sky_hud["level"] == "L4"
        assert v.sky_hud["hint"] == "手动"
        assert v.sky_hud["manual"] is True

        before = (float(v.view_state.center_az),
                  float(v.view_state.center_alt))
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is False
        after = (float(v.view_state.center_az),
                 float(v.view_state.center_alt))
        assert after == before, "L4 后位姿不得覆盖用户视角"
        assert v.sky_hud["level"] == "L4"

        # 放开手动接管后，位姿恢复驱动。
        v.set_manual_override(False)
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(200.0)
        assert v.view_state.center_alt == pytest.approx(10.0)
    finally:
        v.close()


def test_wheel_auto_enters_manual_l4(qapp):
    """用户滚轮微调后自动进入 L4，后续位姿不再把视角拽走。"""
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.update_sky_pose(POSE, TARGETS, "L3")
        assert v.sky_manual_override is False
        ev = QWheelEvent(QPointF(400.0, 200.0),
                         v.mapToGlobal(QPointF(400.0, 200.0)),
                         QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        v.wheelEvent(ev)
        assert v.sky_manual_override is True
        assert v.sky_hud["level"] == "L4"
        assert v.sky_hud["hint"] == "手动"

        before = (float(v.view_state.center_az),
                  float(v.view_state.center_alt))
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is False
        assert (float(v.view_state.center_az),
                float(v.view_state.center_alt)) == before
    finally:
        v.close()


def test_no_pose_shows_not_aligned_and_no_fake_targets(qapp):
    v = _view()
    try:
        start = _snapshot(v)
        v.set_sky_pointing_mode(True)
        v.update_sky_pose({}, TARGETS, "L3")
        hud = v.sky_hud
        assert hud["level"] == "L0"
        assert hud["hint"] == "还没对准"
        assert hud["labels"] == []
        assert hud["satellite_markers"] == []
        assert _snapshot(v) == start, "无位姿不得改动视图"
        v.grab()
    finally:
        v.close()
