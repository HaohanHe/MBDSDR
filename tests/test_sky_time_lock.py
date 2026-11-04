# SPDX-License-Identifier: MIT
"""
tests/test_sky_time_lock.py
===========================
「时间控制与锁定的交互」（第六轮）：

  1. 倍率切换（1x→×60→×300→×1800→回 1x）锁定不断、切换瞬间画面不跳、
     回 1x 后仍是 25% 指数平滑（不被快转直贴状态污染）。
  2. 一次性时间跳转（面板 ±10m / 跳到此刻）的确定语义：
     - 目标仍在地平线上 -> 保持锁定，但按新时刻**直接贴住**并提示；
     - 目标已落地 -> 诚实解锁并提示是跳转所致；
     - 未锁定 -> 无副作用。
  3. 倍率显示诚实：×1 明确「实时」，快转写「×N 模拟时间」，
     暂停写「⏸ 暂停」，倒放写「倒放 ×N 模拟时间」；提示文案说明
     倍率是模拟时间推进速度，不是画面帧率。
  4. 暂停/倒放：⏸ 在跟随墙钟态也必须真正冻结（先钉时刻再 pause）；
     引擎负倍率（倒放）下锁定与轨迹仍按当前模拟时刻重算；UI 无倒放按钮
     （不留「点了没反应」的控件）。

时间推进全部用显式假时钟（eng.set_unix + eng.tick(0.033)），不睡真实时间。
运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_time_lock.py -v
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

import rf_sky_view as rsv  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView, RFSkyViewPanel, angular_separation_deg, format_duration_s,
)
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402

# 测试锚点站址（SGP4 地面站夹具坐标，不代表任何个人位置）
LAT, LON = 43.9, 125.3
ISS = "ISS (ZARYA)"
NOAA19 = "NOAA 19"
LINE1_ISS, LINE2_ISS = BUILTIN_TLE[ISS]
LINE1_N19, LINE2_N19 = BUILTIN_TLE[NOAA19]

# 与快转用例同一锚点：ISS 仰角≈55°，t0+220s 内始终 >9°
UNIX = 1790532300.0

RATES = [(60.0, 2), (300.0, 2), (1800.0, 2)]


def _jd(unix_s: float) -> float:
    return unix_s / 86400.0 + 2440587.5


def _azalt_at(name: str, unix_s: float):
    from sgp4.api import Satrec
    line1, line2 = {"ISS (ZARYA)": (LINE1_ISS, LINE2_ISS),
                    "NOAA 19": (LINE1_N19, LINE2_N19)}[name]
    sat = Satrec.twoline2rv(line1, line2)
    st = orbit._state_from_satrec(sat, name, _jd(unix_s), LAT, LON, 0.0)
    if st is None:
        return None
    return float(st["azimuth"]), float(st["elevation"])


def _catalog() -> list:
    return [
        {"name": ISS, "line1": LINE1_ISS, "line2": LINE2_ISS,
         "freq_mhz": 145.8, "kind": "amateur"},
        {"name": NOAA19, "line1": LINE1_N19, "line2": LINE2_N19,
         "freq_mhz": 137.1, "kind": "weather"},
    ]


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON, 0.0)
    v.set_satellite_catalog(_catalog())
    return v


@pytest.fixture
def engine():
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.set_rate(1.0)
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


def _precenter(v, t: float) -> None:
    az, el = _azalt_at(ISS, t)
    v.view_state.look_at(az, el)


def _step(engine, v, rate: float, dt: float = 0.033):
    engine.set_rate(rate)
    engine.tick(dt)
    v._on_refresh_tick()
    return engine.now_unix()


def _center(v):
    return v.view_state.center_az, v.view_state.center_alt


# ---------------------------------------------------------------------------
# 1. 倍率切换：锁定不断、不跳、回 1x 恢复平滑
# ---------------------------------------------------------------------------
class TestRateSwitchLock:
    def test_lock_survives_full_rate_round_trip(self, engine):
        engine.set_rate(1.0)
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True

        prev_center = _center(v)
        prev_true = _azalt_at(ISS, engine.now_unix())
        prev_rate = 1.0
        for rate, frames in [(1.0, 2)] + RATES + [(1.0, 3)]:
            for i in range(frames):
                err_before = angular_separation_deg(
                    *_center(v), *_azalt_at(ISS, engine.now_unix()))
                now = _step(engine, v, rate)
                true = _azalt_at(ISS, now)
                assert true is not None and true[1] > 0.0
                err = angular_separation_deg(*_center(v), *true)
                if abs(rate) > 1.0:
                    # 快转逐帧直贴：目标永远压在画面中心
                    assert err < 0.05, f"×{rate:g} 中心误差 {err:.3f}°"
                    assert err <= err_before + 1e-5, (
                        f"×{rate:g} 直贴后误差反而变大 {err_before:.3f}→{err:.3f}°")
                else:
                    # 1x 走 25% 指数平滑：稳态滞后 = 3 倍单帧目标位移，
                    # 不会累积发散（上限远小于 0.25°）
                    assert err < 0.25, f"1x 平滑滞后过大 {err:.3f}°"
                disp = angular_separation_deg(*prev_center, *_center(v))
                true_disp = angular_separation_deg(*prev_true, *true)
                if i == 0 and rate != prev_rate and abs(rate) > 1.0:
                    # 切档首帧只允许吸收切档前的平滑残差（单向贴住），
                    # 不许比残差再多跳一点，更不许过冲（「跳一下再回来」）
                    assert abs(disp - true_disp) <= err_before + 1e-6, (
                        f"×{rate:g} 切档首帧多跳 "
                        f"{abs(disp - true_disp) - err_before:.3f}°")
                elif abs(rate) > 1.0:
                    # 快转稳态：画面位移必须就是目标真实位移（无额外抽搐）
                    assert abs(disp - true_disp) < 0.005, (
                        f"×{rate:g} 帧间位移 {disp:.3f}° ≠ 真位移 "
                        f"{true_disp:.3f}°")
                assert v._camera_lock == ISS
                assert "已解锁" not in v._camera_notice
                prev_center, prev_true = _center(v), true
            prev_rate = rate

    def test_rate_switch_itself_does_not_jump_view(self, engine):
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True
        for _ in range(5):                       # 1x 先跑到平滑稳态
            _step(engine, v, 1.0)
        before_c, before_t = _center(v), _azalt_at(ISS, engine.now_unix())
        err_before = angular_separation_deg(*before_c, *before_t)

        now = _step(engine, v, 60.0)             # 切档瞬间只走一帧
        after_c, after_t = _center(v), _azalt_at(ISS, now)
        disp = angular_separation_deg(*before_c, *after_c)
        true_disp = angular_separation_deg(*before_t, *after_t)
        # 唯一允许多出的位移 = 切档前已存在的平滑滞后（一次单向吸收），
        # 不存在「过冲后再回来」；贴住后误差归零。
        assert abs(disp - true_disp) <= err_before + 1e-6, (
            f"切档多跳 {abs(disp - true_disp) - err_before:.4f}°")
        assert err_before < 0.25
        assert angular_separation_deg(*after_c, *after_t) < 0.05
        # 下一帧起按真实位移推进（不再额外补偿）
        prev_c, prev_t = after_c, after_t
        now2 = _step(engine, v, 60.0)
        t2 = _azalt_at(ISS, now2)
        assert abs(angular_separation_deg(*prev_c, *_center(v))
                   - angular_separation_deg(*prev_t, *t2)) < 0.005

    def test_back_to_1x_restores_25pct_smoothing(self, engine, monkeypatch):
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True
        _step(engine, v, 300.0)                  # 快转直贴（首帧无位移基准）
        _step(engine, v, 300.0)                  # 第二帧才产生「跟不上」提示
        assert v._lock_snap_rate == 300.0
        assert "逐帧直贴" in v._camera_notice
        _step(engine, v, 1.0)                    # 回 1x：直贴状态必须复位
        assert v._lock_snap_rate is None

        alt0 = v.view_state.center_alt
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda name: (v.view_state.center_az, alt0 + 0.1))
        v._apply_camera_lock()
        # 25% 指数趋近：0.1° 的偏差只走 0.025°，不是直贴
        assert abs(v.view_state.center_alt - (alt0 + 0.025)) < 1e-9
        assert v._lock_snap_rate is None


# ---------------------------------------------------------------------------
# 2. 一次性时间跳转：确定语义（继续锁并贴住 / 落地则诚实解锁）
# ---------------------------------------------------------------------------
class TestTimeJumpLock:
    def test_visible_target_stays_locked_and_snaps(self, engine):
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True
        for _ in range(3):
            _step(engine, v, 1.0)

        t_new = UNIX + 60.0
        assert _azalt_at(ISS, t_new)[1] > 5.0
        v.notify_time_jump(format_duration_s(60.0))
        engine.set_unix(t_new)
        engine.tick(0.0)                          # 只跳时刻，不额外推进
        v._on_refresh_tick()

        true = _azalt_at(ISS, t_new)
        # 一帧内就贴住（不是瞬移后花几帧缓慢追上）
        assert angular_separation_deg(*_center(v), *true) < 0.05
        assert v._camera_lock == ISS
        assert "时间跳转" in v._camera_notice
        assert "直接贴住" in v._camera_notice
        assert "继续锁定" in v._camera_notice

    def test_target_below_horizon_unlocks_honestly(self, engine):
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True
        _step(engine, v, 1.0)

        t_land = None
        for k in range(1, 24 * 60):
            t = UNIX + k * 120.0
            s = _azalt_at(ISS, t)
            if s is not None and s[1] < -5.0:
                t_land = t
                break
        assert t_land is not None, "24h 内 ISS 应有落地时刻"

        v.notify_time_jump(format_duration_s(t_land - engine.now_unix()))
        engine.set_unix(t_land)
        engine.tick(0.0)
        v._on_refresh_tick()

        assert v._camera_lock is None
        assert "时间跳转" in v._camera_notice
        assert "地平线下" in v._camera_notice

    def test_jump_when_unlocked_is_noop(self, engine):
        v = _new_view()
        _precenter(v, UNIX)
        v.notify_time_jump(format_duration_s(600.0))
        engine.set_unix(UNIX + 600.0)
        engine.tick(0.0)
        v._on_refresh_tick()
        assert v._camera_lock is None
        assert v._pending_time_jump is None
        assert v._camera_notice == ""

    def test_panel_jump_buttons_notify_view(self, engine):
        p = RFSkyViewPanel()
        p.resize(900, 600)
        p._adjust(600)
        assert "分钟" in p.sky_view._pending_time_jump
        assert engine.now_unix() == pytest.approx(UNIX + 600.0, abs=1e-3)

        p._reset_time()
        assert p.sky_view._pending_time_jump.startswith("跳到此刻")
        assert engine.get_rate() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 3. 倍率显示：实时 / 快转 / 暂停 / 倒放，且与帧率区分
# ---------------------------------------------------------------------------
class TestRateLabelHonesty:
    @pytest.fixture(autouse=True)
    def _panel(self):
        self.p = RFSkyViewPanel()
        self.p.resize(900, 600)
        yield
        self.p.close()

    def test_1x_explicitly_says_realtime(self):
        self.p._set_rate(1.0)
        txt = self.p.time_label.text()
        assert "实时" in txt
        assert "×" not in txt and "⏸" not in txt
        assert "UTC" in txt

    def test_fast_rate_says_simulated_time(self):
        self.p._set_rate(60.0)
        txt = self.p.time_label.text()
        assert "×60" in txt
        assert "模拟时间" in txt
        # 提示文案明确：这是模拟时间速度，不是帧率
        assert "帧率" in self.p.time_label.toolTip()
        assert "帧率" in self.p._rate_buttons["×60"].toolTip()

    def test_pause_label_and_reverse_label(self):
        self.p._set_rate(0.0)
        txt = self.p.time_label.text()
        assert "⏸" in txt and "暂停" in txt

        get_time_engine().set_rate(-300.0)
        self.p._update_label()
        txt = self.p.time_label.text()
        assert "倒放" in txt and "×300" in txt and "模拟时间" in txt

    def test_no_reverse_button_offered(self):
        """引擎支持倒放，但界面不暴露；不存在「点了没反应」的倒放按钮。"""
        labels = list(self.p._rate_buttons)
        assert all("-" not in lb and "倒放" not in lb for lb in labels)
        for lb in labels:
            self.p._rate_buttons[lb].click()
            assert get_time_engine().get_rate() >= 0.0


# ---------------------------------------------------------------------------
# 4. 暂停 / 倒放：时间真的冻结 / 目标真的按当前模拟时刻重算
# ---------------------------------------------------------------------------
class TestPauseAndReverse:
    def test_pause_freezes_time_even_when_following_wall_clock(self, engine):
        engine.set_time_now()                     # _sim_jd=None（跟随墙钟）
        assert engine._sim_jd is None
        p = RFSkyViewPanel()
        p.resize(900, 600)
        try:
            p._rate_buttons["⏸"].click()
            assert engine._sim_jd is not None     # 已钉住，否则 pause 形同虚设
            t0 = engine.now_unix()
            engine.tick(1.0)                      # 1 真实秒
            assert engine.now_unix() == pytest.approx(t0, abs=1e-9)
            assert "暂停" in p.time_label.text()
        finally:
            p.close()

    def test_pause_keeps_lock_and_trajectory_anchor(self, engine):
        v = _new_view()
        _precenter(v, UNIX)
        assert v.set_camera_lock(ISS) is True
        _step(engine, v, 1.0)
        v.set_trajectory(ISS, [_azalt_at(ISS, UNIX + k * 60.0)
                               for k in range(11)],
                         anchor_unix=UNIX, span_s=600.0)
        engine.set_rate(0.0)
        t_frozen = engine.now_unix()             # 暂停时刻（已含上面一帧推进）
        for _ in range(3):
            engine.tick(1.0)                     # 暂停：模拟时间不走
            v._on_refresh_tick()
        assert engine.now_unix() == pytest.approx(t_frozen, abs=1e-9)
        assert v._traj_anchor[ISS] == UNIX        # 时间冻结，轨迹锚点不漂
        assert v._camera_lock == ISS
        assert angular_separation_deg(
            *_center(v), *_azalt_at(ISS, t_frozen)) < 0.05

    def test_reverse_lock_tracks_true_sgp4(self, engine):
        t0 = UNIX + 120.0
        engine.set_unix(t0)
        engine.set_rate(-300.0)
        v = _new_view()
        _precenter(v, t0)
        assert v.set_camera_lock(ISS) is True

        for _ in range(2):
            engine.tick(0.033)
            v._on_refresh_tick()
            now = engine.now_unix()
            true = _azalt_at(ISS, now)
            assert true is not None and true[1] > 0.0
            err = angular_separation_deg(*_center(v), *true)
            assert err < 0.05, f"倒放中心误差 {err:.3f}°"
            assert v._camera_lock == ISS
        assert engine.now_unix() < t0            # 确实在倒放

    def test_reverse_recomputes_stale_trajectory(self, engine):
        engine.set_unix(UNIX)
        engine.set_rate(-300.0)
        v = _new_view()
        stale = [_azalt_at(ISS, UNIX + k * 60.0) for k in range(11)]
        v.set_trajectory(ISS, stale, anchor_unix=UNIX, span_s=600.0)

        for _ in range(2):
            engine.tick(0.033)
            v._on_refresh_tick()

        now = engine.now_unix()
        assert now < UNIX
        assert abs(v._traj_anchor[ISS] - now) < 1e-9
        pts = v._trajectories[ISS]
        err = angular_separation_deg(pts[0][0], pts[0][1],
                                     *_azalt_at(ISS, now))
        # 重算链与独立 SGP4 对拍：1e-5° ≈ 0.04″，仅浮点舍入量级
        assert err < 1e-5
