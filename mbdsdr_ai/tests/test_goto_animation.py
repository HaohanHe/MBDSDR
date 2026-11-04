# SPDX-License-Identifier: MIT
"""
GotoAnimation 纯缓动行为测试 (mbdsdr_ai/tests/test_goto_animation.py)
====================================================================

钉扎 Stellarium 式 goto 语义（机制独立重实现）：
  * OutCubic 缓动曲线：0ms=起点、200ms=终点、中点进度 0.875（1-(1-½)³）；
  * 方位角最短弧插值（±180° 回绕，不绕远路）；
  * 越界夹取（elapsed<0 / >duration 不炸、不发散）；
  * fov target=None 保持起点 FOV；显式 fov 参与插值；
  * duration<=0 瞬移到目标；
  * step 纯函数：同一 elapsed 多次调用结果一致（可注入假时钟驱动）。

运行: python -m pytest mbdsdr_ai/tests/test_goto_animation.py -v
"""
import math

from ..celestial_geometry import vec_from_azalt, azalt_from_vec
from ..sky_interaction import (
    GotoAnimation,
    ViewState,
    DEFAULT_GOTO_DURATION_MS,
)


def _sep(a, b) -> float:
    """两 (az, alt) 的大圆角距（度）。"""
    va = vec_from_azalt(a[0], a[1])
    vb = vec_from_azalt(b[0], b[1])
    return math.degrees(math.acos(max(-1.0, min(1.0, float(va @ vb)))))


def _start() -> ViewState:
    return ViewState(center_az=350.0, center_alt=30.0, fov_deg=120.0)


def _anim(**kw) -> GotoAnimation:
    return GotoAnimation(_start(), target_az=10.0, target_alt=50.0, **kw)


class TestEasingCurve:
    def test_ease_out_cubic_endpoints(self):
        assert GotoAnimation.ease_out_cubic(0.0) == 0.0
        assert abs(GotoAnimation.ease_out_cubic(1.0) - 1.0) < 1e-12

    def test_ease_out_cubic_midpoint(self):
        # OutCubic: 1-(1-t)³，t=0.5 -> 0.875
        assert abs(GotoAnimation.ease_out_cubic(0.5) - 0.875) < 1e-12

    def test_ease_out_cubic_clamps_out_of_range(self):
        assert GotoAnimation.ease_out_cubic(-1.0) == 0.0
        assert GotoAnimation.ease_out_cubic(2.0) == 1.0


class TestStepTimeline:
    def test_zero_elapsed_is_start_not_done(self):
        vs, done = _anim().step(0.0)
        assert not done
        assert abs(vs.center_alt - 30.0) < 1e-9
        assert abs(vs.fov_deg - 120.0) < 1e-9

    def test_full_elapsed_reaches_target_done(self):
        vs, done = _anim().step(DEFAULT_GOTO_DURATION_MS)
        assert done
        assert abs(vs.center_az - 10.0) < 1e-9
        assert abs(vs.center_alt - 50.0) < 1e-9

    def test_midpoint_progress_0875(self):
        vs, done = _anim().step(DEFAULT_GOTO_DURATION_MS / 2.0)
        assert not done
        # 缓动 e=0.875 → 沿大圆走完 87.5% 的角距（不是 az/alt 各自线性）
        total = _sep((350.0, 30.0), (10.0, 50.0))
        walked = _sep((350.0, 30.0), (vs.center_az, vs.center_alt))
        assert abs(walked - 0.875 * total) < 1e-6

    def test_overshoot_clamps_to_target(self):
        vs, done = _anim().step(DEFAULT_GOTO_DURATION_MS * 3.0)
        assert done
        assert abs(vs.center_az - 10.0) < 1e-9
        assert abs(vs.center_alt - 50.0) < 1e-9

    def test_negative_elamped_to_start(self):
        vs, done = _anim().step(-50.0)
        assert not done
        assert abs(vs.center_alt - 30.0) < 1e-9


class TestAzimuthShortestArc:
    def test_wraps_through_zero_not_around(self):
        # 350° -> 10°：最短弧 +20°（过 0°），不是 -340°
        anim = _anim()
        assert abs(anim._az_delta - 20.0) < 1e-9
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert abs(vs.center_az - 10.0) < 1e-9

    def test_no_wrap_when_direct(self):
        anim = GotoAnimation(_start(), target_az=340.0, target_alt=50.0)
        # 350° -> 340°：-10° 直连
        assert abs(anim._az_delta - (-10.0)) < 1e-9

    def test_midpoint_crossing_north(self):
        anim = _anim()
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS / 2.0)
        # 过 0° 走最短弧：中点方位应落在 350°..10° 的窄弧内，而不是 180° 附近
        assert vs.center_az < 20.0 or vs.center_az > 340.0
        assert 30.0 < vs.center_alt < 50.0
        # 剩余角距 = 12.5%·总角距（最短路径，非绕远路）
        total = _sep((350.0, 30.0), (10.0, 50.0))
        remain = _sep((vs.center_az, vs.center_alt), (10.0, 50.0))
        assert abs(remain - 0.125 * total) < 1e-6


class TestGreatCirclePath:
    def test_near_zenith_stays_at_zenith_no_altitude_swoop(self):
        # 近天顶两点：若按 az/alt 线性会先掉到 45° 再爬回来；大圆应贴天顶走
        start = ViewState(center_az=0.0, center_alt=89.9, fov_deg=120.0)
        anim = GotoAnimation(start, target_az=180.0, target_alt=89.9)
        alts = []
        for k in range(1, 20):
            vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS * k / 20.0)
            alts.append(vs.center_alt)
        assert min(alts) > 89.5, f"大圆路径不该俯冲低于 89.5°，实测 {min(alts):.2f}°"
        vs, done = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert done
        assert abs(vs.center_az - 180.0) < 1e-9
        assert abs(vs.center_alt - 89.9) < 1e-9

    def test_crossing_zero_is_continuous(self):
        # 多次小步采样：相邻帧角距应单调平滑，不出现 >1° 的瞬跳（抽搐）
        anim = _anim()
        prev = (350.0, 30.0)
        max_jump = 0.0
        for k in range(1, 101):
            vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS * k / 100.0)
            max_jump = max(max_jump,
                           _sep(prev, (vs.center_az, vs.center_alt)))
            prev = (vs.center_az, vs.center_alt)
        assert max_jump < 1.0, f"单帧最大跳变 {max_jump:.3f}°"

    def test_antipodal_is_finite_and_lands_exact(self):
        start = ViewState(center_az=0.0, center_alt=-89.9, fov_deg=120.0)
        anim = GotoAnimation(start, target_az=180.0, target_alt=89.9)
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS / 2.0)
        assert math.isfinite(vs.center_az) and math.isfinite(vs.center_alt)
        vs, done = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert done
        assert abs(vs.center_az - 180.0) < 1e-9
        assert abs(vs.center_alt - 89.9) < 1e-9


class TestFovHandling:
    def test_default_keeps_start_fov(self):
        vs, done = _anim().step(DEFAULT_GOTO_DURATION_MS)
        assert done
        assert abs(vs.fov_deg - 120.0) < 1e-9

    def test_explicit_fov_interpolates(self):
        anim = _anim(target_fov=60.0)
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS / 2.0)
        # 120 + 0.875*(60-120) = 67.5
        assert abs(vs.fov_deg - 67.5) < 1e-9
        vs, done = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert done
        assert abs(vs.fov_deg - 60.0) < 1e-9

    def test_fov_clamped_to_view_bounds(self):
        anim = _anim(target_fov=1000.0)
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert vs.fov_deg <= vs.max_fov


class TestEdgeCases:
    def test_zero_duration_is_instant(self):
        vs, done = _anim(duration_ms=0.0).step(0.0)
        assert done
        assert abs(vs.center_az - 10.0) < 1e-9
        assert abs(vs.center_alt - 50.0) < 1e-9

    def test_step_is_pure_same_elapsed_same_result(self):
        anim = _anim()
        vs1, d1 = anim.step(100.0)
        vs2, d2 = anim.step(100.0)
        assert d1 == d2
        assert vs1.center_az == vs2.center_az
        assert vs1.center_alt == vs2.center_alt
        assert vs1.fov_deg == vs2.fov_deg

    def test_start_snapshot_isolated_from_mutation(self):
        start = _start()
        anim = GotoAnimation(start, target_az=10.0, target_alt=50.0)
        start.center_alt = -80.0  # 外部改动不应污染动画
        vs, _ = anim.step(DEFAULT_GOTO_DURATION_MS)
        assert abs(vs.center_alt - 50.0) < 1e-9


class TestSanity:
    def test_default_duration_matches_spec(self):
        assert DEFAULT_GOTO_DURATION_MS == 200.0

    def test_exported_in_all(self):
        import mbdsdr_ai.sky_interaction as si
        assert "GotoAnimation" in si.__all__
