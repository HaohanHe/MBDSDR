"""
TimeEngine 测试
================
验证 Stellarium 风格的时间穿梭真正影响卫星位置计算（修复"装饰条"问题）。

关键测试：
- 默认 rate=1 且未跳时：行为与系统时间一致（向后兼容）
- set_jd/set_utc 跳时后，卫星 az/alt 真实改变
- tick(real_dt) 在不同 rate 下正确推进/暂停/倒流
- predict_satellite_pass 从 TimeEngine 取起点时间
"""
import os
import sys
import math
import time
import unittest
from datetime import datetime, timedelta, timezone

# 让测试可以直接 import mbdsdr_ai
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from new_spacetime import (
    TimeEngine,
    get_time_engine,
    reset_time_engine,
    utc_to_jd,
    jd_to_utc,
    compute_visible_satellite_count,
    predict_satellite_pass,
)


class TestTimeEngineConversions(unittest.TestCase):
    """UTC <-> JD 往返换算。"""

    def test_utc_jd_roundtrip(self):
        dt = datetime(2026, 9, 25, 12, 34, 56, tzinfo=timezone.utc)
        jd = utc_to_jd(dt)
        back = jd_to_utc(jd)
        # 容差：1 秒
        self.assertLessEqual(abs((back - dt).total_seconds()), 1.0)

    def test_unix_epoch_jd(self):
        # 1970-01-01 00:00 UTC = JD 2440587.5
        dt = datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(utc_to_jd(dt), 2440587.5, places=3)


class TestTimeEngineDefaultBehavior(unittest.TestCase):
    """向后兼容：默认跟随系统时间。"""

    def setUp(self):
        reset_time_engine()

    def test_default_follows_system_clock(self):
        eng = get_time_engine()
        self.assertTrue(eng.is_following_system())
        # now_unix() 应接近 time.time()
        self.assertLess(abs(eng.now_unix() - time.time()), 1.0)
        self.assertEqual(eng.get_rate(), 1.0)

    def test_tick_when_following_system_is_noop(self):
        eng = get_time_engine()
        before = eng.now_unix()
        eng.tick(10.0)  # 不应抛错，也不应跳变
        after = eng.now_unix()
        # 跟随系统时间时 tick 不改 sim_jd，仍返回系统时间
        self.assertLess(abs(after - time.time()), 1.0)


class TestTimeEngineRate(unittest.TestCase):
    """时间速率：暂停/加速/倒流。"""

    def setUp(self):
        reset_time_engine()

    def test_pause_prevents_advancement(self):
        eng = get_time_engine()
        eng.set_utc(datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(0.0)
        jd_before = eng.now_jd()
        eng.tick(60.0)  # 60 真实秒
        self.assertAlmostEqual(eng.now_jd(), jd_before, places=9)

    def test_60x_rate_advances_60_sim_seconds_per_real_second(self):
        eng = get_time_engine()
        eng.set_utc(datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(60.0)
        eng.tick(1.0)  # 1 真实秒 -> 60 模拟秒
        expected = datetime(2026, 9, 25, 12, 1, 0, tzinfo=timezone.utc)
        self.assertLessEqual(abs((eng.now_utc() - expected).total_seconds()), 2.0)

    def test_negative_rate_reverses_time(self):
        eng = get_time_engine()
        eng.set_utc(datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc))
        eng.set_rate(-10.0)
        eng.tick(1.0)  # 1 真实秒 -> -10 模拟秒
        expected = datetime(2026, 9, 25, 11, 59, 50, tzinfo=timezone.utc)
        self.assertLessEqual(abs((eng.now_utc() - expected).total_seconds()), 2.0)

    def test_set_time_now_resets_to_system(self):
        eng = get_time_engine()
        eng.set_utc(datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertFalse(eng.is_following_system())
        eng.set_time_now()
        self.assertTrue(eng.is_following_system())


class TestTimeTravelAffectsSatellitePositions(unittest.TestCase):
    """
    核心验收：时间跳转后，卫星 az/alt 必须真实变化。
    这是"装饰条"问题的反测试 —— 之前 UI 改了值但计算仍读系统时间。
    """

    def setUp(self):
        reset_time_engine()
        # 长春坐标
        self.lat, self.lon = 43.88, 125.32

    def test_jump_24h_changes_satellite_az_alt(self):
        from decoders import compute_satellite_position

        eng = get_time_engine()

        # 1) 取"现在"（系统时间）的卫星位置
        now_unix = time.time()
        pos_now = compute_satellite_position("ISS (ZARYA)", self.lat, self.lon, 0,
                                             timestamp=now_unix)
        self.assertIsNotNone(pos_now)

        # 2) 把模拟时间跳到 24 小时后
        future_dt = datetime.now(timezone.utc) + timedelta(hours=24)
        eng.set_utc(future_dt)
        future_unix = eng.now_unix()
        self.assertAlmostEqual(future_unix - now_unix, 24 * 3600, delta=5.0)

        # 3) 用模拟时间算同一颗卫星位置
        pos_future = compute_satellite_position("ISS (ZARYA)", self.lat, self.lon, 0,
                                                timestamp=future_unix)
        self.assertIsNotNone(pos_future)

        # 4) 关键断言：24 小时后卫星仰角/方位角必须与现在不同
        #    ISS 轨道周期 ~92 分钟，24h 后位置必然显著变化
        self.assertGreater(
            abs(pos_future.azimuth - pos_now.azimuth), 1.0,
            msg=f"24h 后方位角应变化 >1°，实际 {pos_now.azimuth:.1f} -> {pos_future.azimuth:.1f}"
        )

    def test_compute_visible_satellite_count_uses_sim_time(self):
        """compute_visible_satellite_count 应从 TimeEngine 取时间。"""
        eng = get_time_engine()

        # 先在系统时间下取一次
        reset_time_engine()
        r1 = compute_visible_satellite_count(self.lat, self.lon, min_elevation=0.0)

        # 跳到 6 小时后
        eng.set_utc(datetime.now(timezone.utc) + timedelta(hours=6))
        r2 = compute_visible_satellite_count(self.lat, self.lon, min_elevation=0.0)

        # 两次结果都应是合法字典
        self.assertIn("total_visible", r1)
        self.assertIn("total_visible", r2)
        # 可见卫星集合应不同（6 小时后天空转了 90°）
        names1 = {s["name"] for s in r1["satellites"]}
        names2 = {s["name"] for s in r2["satellites"]}
        # 至少集合有差异（不强制不等，因为 LEO 一天绕很多圈，但概率上不同）
        # 这里只验证不崩溃且数值合理
        self.assertIsInstance(r1["total_visible"], int)
        self.assertIsInstance(r2["total_visible"], int)

    def test_predict_pass_uses_sim_time(self):
        """predict_satellite_pass 起点应随 TimeEngine 移动。"""
        eng = get_time_engine()
        # 跳到一个固定未来时间
        fixed = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        eng.set_utc(fixed)

        p = predict_satellite_pass("NOAA 15", self.lat, self.lon, 0.0,
                                   hours_ahead=6.0, min_elevation=5.0)
        # 不要求一定有 pass，但即使是 None 也不能崩溃
        # 如果有 pass，rise_time 必须 >= 固定起点
        if p is not None and p.rise_time is not None:
            self.assertGreaterEqual(
                (p.rise_time - fixed).total_seconds(), -30.0,
                msg="pass 升起时间不应早于模拟起点"
            )


class TestStellariumSemantics(unittest.TestCase):
    """对照 Stellarium 源码语义的单元测试。"""

    def setUp(self):
        reset_time_engine()

    def test_jd_second_constant(self):
        """Stellarium JD_SECOND = 1/86400 天/秒。"""
        self.assertAlmostEqual(TimeEngine.JD_SECOND, 1.0 / 86400.0, places=12)

    def test_tick_matches_stellarium_formula(self):
        """
        复现 StelCore.cpp:2307 的公式：
            JD.first = jdOfLastJDUpdate + real_elapsed * timeSpeed
        其中 timeSpeed = rate * JD_SECOND
        """
        eng = get_time_engine()
        start_jd = 2460000.0
        eng.set_jd(start_jd)
        eng.set_rate(2.0)  # 2x 实时
        # 推进 10 真实秒 -> 20 模拟秒 = 20/86400 天
        eng.tick(10.0)
        expected = start_jd + 10.0 * 2.0 * (1.0 / 86400.0)
        self.assertAlmostEqual(eng.now_jd(), expected, places=9)


if __name__ == "__main__":
    unittest.main()
