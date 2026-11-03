# SPDX-License-Identifier: MIT
"""
tests/test_zoom_to_cursor.py
============================
验证 §2 zoom-to-cursor（SWE 机制：缩放锚在光标，反补 yaw/pitch）：

  1. 缩放后「光标下的天空方向」几乎不动（钉在指下）；
  2. 对照：不反补的绕中心缩放会让光标下方向明显漂走——证明反补真在干活；
  3. 纯数值滚轮桩（无光标）退化为绕中心缩放，视心不变（向后兼容）；
  4. 部件滚轮带光标位置时同样锚定；
  5. 光标下的卫星缩放后屏幕位移很小（端到端像素级）。

阈值锚定「本质量」（反补后漂移 << 不反补漂移），不绑投影非线性的绝对派生量。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_zoom_to_cursor.py -v
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

from rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from mbdsdr_ai.sky_interaction import screen_to_sky  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0


def _new_view(fov=60.0, center=(0.0, 45.0), size=700):
    eng = get_time_engine()
    eng.set_unix(datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp())
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.look_at(center[0], center[1])
    v.view_state.zoom_to(fov)
    return v


def _sky_at(v, px, py):
    size = (float(v.width()), float(v.height()))
    return screen_to_sky(px, py, v.view_state, v.projection, size)


def _sep(a1, e1, a2, e2):
    """两地平方向的近似角距（度）；az 折 ±180，按平均仰角缩经度。"""
    daz = (a1 - a2 + 180.0) % 360.0 - 180.0
    de = e1 - e2
    return math.hypot(daz * math.cos(math.radians((e1 + e2) / 2.0)), de)


class TestZoomAnchorsCursor:
    def test_zoom_in_preserves_sky_under_cursor(self):
        v = _new_view()
        cx, cy = v.width() * 0.5 + 70.0, v.height() * 0.5 + 45.0
        az0, alt0 = _sky_at(v, cx, cy)
        v.zoom_at_cursor(2.0, (cx, cy))          # 放大
        az1, alt1 = _sky_at(v, cx, cy)
        assert v.view_state.fov_deg < 60.0, "应真的放大了"
        assert _sep(az0, alt0, az1, alt1) < 1.0, \
            f"反补后光标下方向应几乎不动，实测漂移 {_sep(az0, alt0, az1, alt1):.3f}°"
        v.close()

    def test_zoom_out_preserves_sky_under_cursor(self):
        v = _new_view(fov=40.0)
        cx, cy = v.width() * 0.5 - 60.0, v.height() * 0.5 + 30.0
        az0, alt0 = _sky_at(v, cx, cy)
        v.zoom_at_cursor(-2.0, (cx, cy))         # 缩小
        az1, alt1 = _sky_at(v, cx, cy)
        assert v.view_state.fov_deg > 40.0
        assert _sep(az0, alt0, az1, alt1) < 1.0
        v.close()


class TestCompensationDoesRealWork:
    """对照：绕中心缩放（不反补）会让光标下方向明显漂走。"""

    def test_compensation_beats_center_zoom(self):
        # 反补路径
        v = _new_view()
        cx, cy = v.width() * 0.5 + 90.0, v.height() * 0.5 + 60.0
        az0, alt0 = _sky_at(v, cx, cy)
        v.zoom_at_cursor(3.0, (cx, cy))
        azc, altc = _sky_at(v, cx, cy)
        drift_comp = _sep(az0, alt0, azc, altc)
        v.close()

        # 不反补：同一缩放步，只改 fov（旧行为）
        v2 = _new_view()
        az0b, alt0b = _sky_at(v2, cx, cy)
        v2.view_state.zoom(3.0)                  # 绕中心，无视光标
        azb, altb = _sky_at(v2, cx, cy)
        drift_center = _sep(az0b, alt0b, azb, altb)
        v2.close()

        assert drift_center > 1.0, \
            f"不反补本应明显漂走，实测才 {drift_center:.3f}°（用例无效）"
        assert drift_comp < drift_center * 0.25, \
            f"反补应把漂移压到 1/4 以下：comp={drift_comp:.3f} center={drift_center:.3f}"


class TestNumericStubFallback:
    def test_wheel_numeric_zooms_about_center(self):
        v = _new_view()
        caz0, calt0 = v.view_state.center_az, v.view_state.center_alt
        v.wheelEvent(120)                        # 纯数值桩：无光标 -> 绕中心
        assert v.view_state.fov_deg < 60.0, "应放大"
        assert abs(v.view_state.center_az - caz0) < 1e-9, "无光标不得平移视心"
        assert abs(v.view_state.center_alt - calt0) < 1e-9
        v.close()


class TestWidgetWheelWithCursor:
    def test_dict_stub_with_position_anchors(self):
        v = _new_view()
        cx, cy = v.width() * 0.5 + 70.0, v.height() * 0.5 + 45.0
        az0, alt0 = _sky_at(v, cx, cy)
        # dict 桩同时带 delta + 光标位置：走 zoom-to-cursor
        v.wheelEvent({"delta": 120, "x": cx, "y": cy})
        az1, alt1 = _sky_at(v, cx, cy)
        assert v.view_state.fov_deg < 60.0
        assert _sep(az0, alt0, az1, alt1) < 1.0, "部件滚轮带光标应锚定"
        v.close()


class TestObjectStaysUnderCursor:
    def test_satellite_pixel_barely_moves(self):
        v = _new_view()
        # 在光标像素处放一颗卫星：先反推该像素的天空方向，把星放那儿
        cx, cy = v.width() * 0.5 + 70.0, v.height() * 0.5 + 45.0
        az0, alt0 = _sky_at(v, cx, cy)
        v.add_object(SkyObject("ANCHOR-SAT", az0, alt0, obj_type="satellite"))
        p_before = v._sky_to_screen(az0, alt0)
        v.zoom_at_cursor(2.0, (cx, cy))
        p_after = v._sky_to_screen(az0, alt0)
        moved = math.hypot(p_after.x() - p_before.x(),
                           p_after.y() - p_before.y())
        assert moved < 12.0, \
            f"光标下的星缩放后应钉在指下，实测位移 {moved:.1f}px"
        v.close()
