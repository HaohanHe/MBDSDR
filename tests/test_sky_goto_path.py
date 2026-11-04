# SPDX-License-Identifier: MIT
"""天空视图 goto 大圆插值（1x 与快转下）端到端钉扎。

A 项验收：
  ① 最短大圆路径（近天顶不俯冲、跨 0° 不走远路）；
  ② 方位角跨界连续，相邻帧无抽搐；
  ③ 插值结束精确落在目标（按显示精度）；
  ④ goto_animated 的 200ms OutCubic 时长/手感不变，快转下同样成立。

运行：
    QT_QPA_PLATFORM=offscreen MBDSDR_NO_HW=1 python -m pytest tests/test_sky_goto_path.py -v
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, angular_separation_deg  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

UNIX = 1790532300.0


@pytest.fixture(autouse=True)
def engine():
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.set_rate(1.0)
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


def _view() -> RFSkyView:
    v = RFSkyView()
    v.resize(760, 760)
    v.set_observer(43.9, 125.3, 0.0)
    return v


def _run_goto(v: RFSkyView, az: float, alt: float,
              clocks_ms, frames: int = 0):
    """用假墙钟驱动 _step_goto_anim，返回 (每帧中心, 完成时的耗时 ms)。"""
    v._goto_clock = lambda: 0.0
    v.goto_animated(az, alt)
    v._goto_start_ms = 0.0
    centers = [(v.view_state.center_az, v.view_state.center_alt)]
    done_at = None
    for i, t in enumerate(clocks_ms):
        v._goto_clock = (lambda tt: (lambda: tt))(t)
        v._step_goto_anim()
        centers.append((v.view_state.center_az, v.view_state.center_alt))
        if v._goto_anim is None and done_at is None:
            done_at = t
    return centers, done_at


class TestGreatCircleInView:
    def test_near_zenith_does_not_swoop(self):
        v = _view()
        v.view_state.look_at(0.0, 89.9)
        clocks = [i * 10.0 for i in range(1, 21)]
        centers, done_at = _run_goto(v, 180.0, 89.9, clocks)
        alts = [c[1] for c in centers]
        assert min(alts) > 89.5, f"近天顶路径俯冲至 {min(alts):.2f}°"
        assert done_at is not None and done_at <= 200.0
        az, alt = v.view_state.center_az, v.view_state.center_alt
        assert abs(az - 180.0) < 1e-9
        assert abs(alt - 89.9) < 1e-9
        v.close()

    def test_crossing_zero_no_zigzag_and_lands_exact(self):
        v = _view()
        v.view_state.look_at(350.0, 30.0)
        clocks = [i * 33.0 for i in range(1, 9)]
        centers, _ = _run_goto(v, 10.0, 50.0, clocks)
        # 无抽搐：累计路径长度 = 起终点直连角距（不绕路、不走回头路）；
        # OutCubic 前段快，首帧位移大是既有手感，不是抖动。
        jumps = [angular_separation_deg(a[0], a[1], b[0], b[1])
                 for a, b in zip(centers, centers[1:])]
        direct = angular_separation_deg(*centers[0], *centers[-1])
        assert abs(sum(jumps) - direct) < 1e-6, (
            f"路径长 {sum(jumps):.3f}° ≠ 直连 {direct:.3f}°（绕路/回摆）")
        remain = [angular_separation_deg(*c, 10.0, 50.0) for c in centers]
        assert all(a >= b - 1e-9 for a, b in zip(remain, remain[1:]))
        # 中点（第 4 帧左右）方位在窄弧 350→10 内，不绕到 180
        mid = centers[len(centers) // 2]
        assert mid[0] < 20.0 or mid[0] > 340.0
        assert abs(((v.view_state.center_az - 10.0 + 180) % 360) - 180) < 1e-9
        assert abs(v.view_state.center_alt - 50.0) < 1e-9
        v.close()

    def test_duration_and_easing_unchanged(self):
        v = _view()
        v.view_state.look_at(350.0, 30.0)
        clocks = [i * 33.0 for i in range(1, 9)]
        _, done_at = _run_goto(v, 10.0, 50.0, clocks)
        # 200ms 名义时长：最后一帧 198ms 未完成，231ms 必然完成（手感不变）
        assert done_at is not None and 198.0 < done_at <= 231.0
        v.close()


class TestUnderFastTime:
    def test_goto_under_1800x_lands_exact(self, engine):
        engine.set_rate(1800.0)
        engine.tick(0.1)                      # 模拟时间先快进 180s
        v = _view()
        v.view_state.look_at(350.0, 30.0)
        clocks = [i * 33.0 for i in range(1, 9)]
        centers, done_at = _run_goto(v, 10.0, 50.0, clocks)
        assert done_at is not None
        # 快转不改变 goto 的插值终点：仍按调用时给定的方向精确落地
        assert abs(((v.view_state.center_az - 10.0 + 180) % 360) - 180) < 1e-9
        assert abs(v.view_state.center_alt - 50.0) < 1e-9
        jumps = [angular_separation_deg(a[0], a[1], b[0], b[1])
                 for a, b in zip(centers, centers[1:])]
        direct = angular_separation_deg(*centers[0], *centers[-1])
        assert abs(sum(jumps) - direct) < 1e-6     # 最短大圆，不回摆
        v.close()

    def test_antipodal_target_under_1800x_finite(self, engine):
        engine.set_rate(1800.0)
        engine.tick(0.1)
        v = _view()
        v.view_state.look_at(0.0, -89.9)
        clocks = [i * 20.0 for i in range(1, 11)]
        centers, done_at = _run_goto(v, 180.0, 89.9, clocks)
        for az, alt in centers:
            assert math.isfinite(az) and math.isfinite(alt)
        assert done_at is not None
        assert abs(((v.view_state.center_az - 180.0 + 180) % 360) - 180) < 1e-9
        assert abs(v.view_state.center_alt - 89.9) < 1e-9
        v.close()
