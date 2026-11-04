# SPDX-License-Identifier: MIT
"""摄像机锁定 vs 多普勒跟踪：两个环同时跑的状态一致性。

语义（本轮定稿）：
  * 两个环语义独立，各自的勾选态/目标/解锁条件互不连带；
  * 换目标：摄像机跟随新选中，跟踪停止并要求用户重新开启（不偷偷换频）；
  * 反选/站址失效：两个环都收口，摄像机解锁并说明原因；
  * 外部拒启/自动停止跟踪：摄像机可继续锁，但必须显示「仅视角、未在调谐」。

运行：
    QT_QPA_PLATFORM=offscreen MBDSDR_NO_HW=1 python -m pytest tests/test_sky_dual_loops.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 43.9, 125.3
# 该时刻 NOAA 19 / NOAA 18 同时在地平线上（约 10°），保证「换目标」时
# 摄像机能真正重锁到第二颗星，而不是因落地失败而误判为解锁缺陷。
UNIX = 1790456820.0
SAT_A, SAT_B = "NOAA 19", "NOAA 18"


def _azalt_at(name: str, t_unix: float) -> tuple:
    l1, l2 = BUILTIN_TLE[name]
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(l1, l2)
    st = orbit._state_from_satrec(
        sat, name, t_unix / 86400.0 + 2440587.5, LAT, LON, 0.0)
    return float(st["azimuth"]), float(st["elevation"])


@pytest.fixture(autouse=True)
def engine():
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.set_rate(0.0)                     # 冻结：两条环的目标角不漂
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


def _catalog():
    return [
        {"name": SAT_A, "line1": BUILTIN_TLE[SAT_A][0],
         "line2": BUILTIN_TLE[SAT_A][1], "freq_mhz": 137.1,
         "kind": "weather"},
        {"name": SAT_B, "line1": BUILTIN_TLE[SAT_B][0],
         "line2": BUILTIN_TLE[SAT_B][1], "freq_mhz": 137.1,
         "kind": "weather"},
    ]


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(760, 760)
    v.set_observer(LAT, LON, 0.0)
    v.set_satellite_catalog(_catalog())
    objs = []
    for name in (SAT_A, SAT_B):
        az, el = _azalt_at(name, UNIX)
        objs.append(SkyObject(name, az, el, obj_type="satellite",
                              frequency_hz=137.1e6))
    v.set_objects(objs)
    _pick(v, SAT_A)
    return v


def _pick(v: RFSkyView, name: str) -> None:
    for obj in v._objects:
        if getattr(obj, "name", None) == name:
            v.on_object_picked(obj)
            return
    raise AssertionError(f"未找到可选中对象 {name}")


def _cam_on(v) -> None:
    if not v._camera_btn.isChecked():
        v._camera_btn.click()


def _track_on(v) -> None:
    if not v._track_btn.isChecked():
        v._track_btn.click()


def _card_crop(v: RFSkyView, dy: int) -> QImage:
    img = QImage(v.size(), QImage.Format_ARGB32)
    img.fill(Qt.black)
    p = QPainter(img)
    v._draw_info_overlay(p)
    p.end()
    card_x = 12
    card_y = v.height() - v.INFO_CARD_H - 12
    return img.copy(card_x, card_y + dy, 200, 16)


def _hud_crop(v: RFSkyView) -> QImage:
    img = QImage(v.size(), QImage.Format_ARGB32)
    img.fill(Qt.black)
    p = QPainter(img)
    v._draw_camera_hud(p)
    p.end()
    return img.copy(0, 24, 320, 100)


def _ink(img: QImage) -> int:
    return sum(1 for y in range(img.height()) for x in range(img.width())
               if img.pixelColor(x, y).lightness() > 40)


# ----------------------------------------------------------------------
class TestFourCombinations:
    def test_neither_loop_idle(self):
        v = _new_view()
        assert v._camera_btn.isChecked() is False
        assert v._track_btn.isChecked() is False
        assert v.camera_lock_target is None
        assert v.track_target_name is None
        assert v.loop_state_lines() == []
        assert _ink(_hud_crop(v)) == 0           # HUD 不无中生有
        v.close()

    def test_camera_only_is_honest_about_no_tuning(self):
        v = _new_view()
        _cam_on(v)
        assert v.camera_lock_target == SAT_A
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        lines = v.loop_state_lines()
        assert lines[0] == f"摄像机锁定: {SAT_A}"
        assert "未开启" in lines[1] and "未在调谐" in lines[1]
        assert _ink(_hud_crop(v)) > 0
        v.close()

    def test_track_only_is_honest_about_no_camera_lock(self):
        v = _new_view()
        _track_on(v)
        assert v.camera_lock_target is None
        assert v._track_btn.isChecked() is True
        assert v.track_target_name == SAT_A
        lines = v.loop_state_lines()
        assert lines[0] == "摄像机: 未锁定（仅调谐）"
        assert lines[1] == f"调谐跟踪: {SAT_A}"
        v.close()

    def test_both_loops_show_both_targets(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        assert v.camera_lock_target == SAT_A
        assert v.track_target_name == SAT_A
        assert v.loop_state_lines() == [
            f"摄像机锁定: {SAT_A}", f"调谐跟踪: {SAT_A}"]
        v.close()

    def test_status_card_rows_reflect_each_loop(self, ):
        v = _new_view()
        # 视角行：未锁 → 锁定必须改变该行像素
        # 批 1 在视角行前新增「恒星时」「视轴」两行 → 下移 36px（136→172）
        before = _card_crop(v, 172)
        _cam_on(v)
        after = _card_crop(v, 172)
        assert _ink(before) != _ink(after)
        # 调谐行：未开启 → 跟踪必须改变该行像素（同样下移 36px：154→190）
        t_before = _card_crop(v, 190)
        _track_on(v)
        t_after = _card_crop(v, 190)
        assert _ink(t_before) != _ink(t_after)
        v.close()


# ----------------------------------------------------------------------
class TestIndependenceAndOrder:
    def test_turning_one_off_keeps_the_other(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        v._camera_btn.click()                 # 只关视角
        assert v.camera_lock_target is None
        assert v._track_btn.isChecked() is True
        assert v.track_target_name == SAT_A
        v._camera_btn.click()                 # 视角重新打开
        v._track_btn.click()                  # 只关调谐
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        assert v.camera_lock_target == SAT_A
        v.close()

    def test_open_order_does_not_change_final_state(self):
        va = _new_view()
        _cam_on(va)
        _track_on(va)
        vb = _new_view()
        _track_on(vb)
        _cam_on(vb)
        assert va.camera_lock_target == vb.camera_lock_target == SAT_A
        assert va.track_target_name == vb.track_target_name == SAT_A
        assert va._track_btn.isChecked() == vb._track_btn.isChecked() is True
        assert va.loop_state_lines() == vb.loop_state_lines()
        assert va._gimbal_btn.isChecked() == vb._gimbal_btn.isChecked()
        va.close()
        vb.close()

    def test_toggle_off_does_not_emit_other_loop_signal(self):
        v = _new_view()
        events = []
        v.track_toggled.connect(lambda on, n, h: events.append(("track", on, n)))
        _cam_on(v)
        v._camera_btn.click()
        assert events == []                   # 关视角不碰跟踪环
        _track_on(v)
        v._track_btn.click()
        assert events and events[-1][0] == "track" and events[-1][1] is False
        v.close()


# ----------------------------------------------------------------------
class TestTargetChange:
    def test_switch_target_camera_follows_track_stops_clearly(self):
        v = _new_view()
        events = []
        v.track_toggled.connect(lambda on, n, h: events.append(("track", on, n)))
        _cam_on(v)
        _track_on(v)
        _pick(v, SAT_B)
        assert v.camera_lock_target == SAT_B       # 视角跟新选中
        assert v._track_btn.isChecked() is False   # 调谐不被偷偷改频
        assert v.track_target_name is None
        assert v._gimbal_btn.isChecked() is False  # 云台跟随随跟踪停
        assert ("track", False, SAT_A) in events
        assert "切换到" in v._track_notice and "重新开启" in v._track_notice
        # 摄像机确实收敛到新目标（引擎冻结，25% 平滑多帧收敛）
        for _ in range(200):
            v._apply_camera_lock()
        az, el = _azalt_at(SAT_B, UNIX)
        assert abs(((v.view_state.center_az - az + 180) % 360) - 180) < 0.2
        assert abs(v.view_state.center_alt - el) < 0.2
        v.close()

    def test_switch_target_when_track_only_does_not_touch_camera(self):
        v = _new_view()
        _track_on(v)
        _pick(v, SAT_B)
        assert v.camera_lock_target is None
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        v.close()

    def test_deselect_stops_both_and_explains(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        v.on_object_picked(None)
        assert v.camera_lock_target is None
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        assert "反选" in v._camera_notice
        assert v.loop_state_lines() == []
        v.close()

    def test_station_loss_stops_both_loop(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        v.set_observer(None, None)
        assert v.camera_lock_target is None
        assert "观测站" in v._camera_notice
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        assert v.loop_state_lines() == []
        v.close()


# ----------------------------------------------------------------------
class TestHonestBoundaries:
    def test_camera_denied_keeps_tracking_and_says_so(self, monkeypatch):
        v = _new_view()
        _track_on(v)
        monkeypatch.setattr(v, "_lock_target_azalt", lambda name: None)
        _cam_on(v)
        assert v.camera_lock_target is None
        assert v._track_btn.isChecked() is True
        assert "无法锁定" in v._camera_notice
        lines = v.loop_state_lines()
        assert lines[0] == "摄像机: 未锁定（仅调谐）"
        assert lines[1] == f"调谐跟踪: {SAT_A}"
        v.close()

    def test_tracking_refused_by_backend_keeps_camera_but_says_not_tuning(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        # 主窗口启动失败（无 SDR）后回写勾选态 False
        v.set_tracking_checked(False)
        assert v.camera_lock_target == SAT_A        # 视角仍锁着
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        assert v._track_notice
        assert "未在调谐" in v._track_notice
        assert "仅视角" in v._track_notice
        lines = v.loop_state_lines()
        assert lines[0] == f"摄像机锁定: {SAT_A}"
        assert "未开启" in lines[1]
        # 通知与状态行同时可见（HUD 非空）
        assert _ink(_hud_crop(v)) > 0
        v.close()

    def test_tracking_auto_stop_same_honest_semantics(self):
        v = _new_view()
        _cam_on(v)
        _track_on(v)
        v.set_tracking_checked(False)               # 过境结束自动停
        assert v._track_notice and "摄像机锁定仍保持" in v._track_notice
        assert v.camera_lock_target == SAT_A
        v.close()

    def test_no_fake_second_loop_after_external_stop(self):
        v = _new_view()
        _track_on(v)
        v.set_tracking_checked(False)
        assert v._track_btn.isChecked() is False
        assert v.track_target_name is None
        assert "摄像机也未锁定" in v._track_notice
        assert v.loop_state_lines() == []          # 两个环都没跑 → HUD 空
        v.close()
