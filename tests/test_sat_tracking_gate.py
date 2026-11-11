# SPDX-License-Identifier: MIT
"""P0-01 反证：卫星跟踪启停只认 `tracking_policy_for()` 的许可。

事故形态（P0-01）：桌面端自己比较 ``tle_tier``/``age_days`` 决定能不能跟踪，
与 AI/天空动作总线的档位判定各写一套，两处迟早对不上；过期星历、身份不符、
无数据都能照样调谐，用户拿着错误多普勒去收信号。

本文件用假 tracker 替换档位来源，只验桌面确实读 policy：
- ``command_allowed`` 为假 → 拒绝开始，状态栏给出档位原因；
- ``require_confirm`` 为真且用户说不 → 拒绝；
- ``require_confirm`` 为真且用户说是 / 无需确认 → 正常进入跟踪；
- 进入跟踪后每一拍下发调谐前同样读 ``allow_tune``（执行层闸门）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_SAT = "NOAA 19"


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._stop_satellite_tracking()
    except Exception:  # noqa: BLE001 - 收尾失败不影响断言结论
        pass


class _FakeLoopTracker:
    """只回答被调用的接口，其余一律吞掉：本用例判的是闸门放不放行。"""

    def __init__(self, _gs=None):
        self.selected_name = ""
        self.ground_station = None
        self.policy = None

    def tracking_policy_for(self, _name=None):
        # 闸门读的就是这个对象（执行跟踪的那个 tracker），故档位挂在这里。
        return self.policy

    def select_satellite(self, name):
        self.selected_name = name
        return True

    def current_position(self):
        # 面板会拿它刷 az/el：给「无有效解」而不是 None，面板走占位分支。
        return {"valid": False}

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class _PolicySource:
    """天空图侧 ``sat_tracker`` 的替身：只提供档位，用来验证闸门不读它。"""

    def __init__(self, policy):
        self.policy = policy

    def tracking_policy_for(self, _name=None):
        return self.policy


def _policy(allowed: bool, confirm: bool) -> dict:
    return {
        "command_allowed": allowed,
        "require_confirm": confirm,
        "label": "星历已过期（演示只读）" if not allowed else "星历新鲜",
        "reason": "TLE 年龄 21.4 天" if not allowed else "TLE 年龄 0.3 天",
        "next_action": "更新 TLE 后再跟踪" if not allowed else "可直接跟踪",
    }


@pytest.fixture()
def gate(window, monkeypatch):
    """把档位来源与 LoopSatTracker 换成假的，其余走真实桌面逻辑。"""
    import main_window as mw

    monkeypatch.setattr(mw, "LoopSatTracker", _FakeLoopTracker)
    monkeypatch.setattr(window, "_observer_lat", 43.9, raising=False)
    monkeypatch.setattr(window, "_observer_lon", 125.3, raising=False)
    window._loop_sat_tracker = None

    def _install(policy):
        # 档位挂在**执行跟踪**的 tracker 上：桌面闸门只读它（见
        # test_gate_reads_execution_tracker_not_sky_view）。
        tracker = _FakeLoopTracker()
        tracker.policy = policy
        window._loop_sat_tracker = tracker
        return policy

    yield _install
    try:
        window._stop_satellite_tracking()
    except Exception:  # noqa: BLE001
        pass


def _tracking_active(window) -> bool:
    timer = getattr(window, "_sat_track_timer", None)
    return bool(timer is not None and timer.isActive())


def test_blocked_policy_refuses_to_start(window, gate):
    gate(_policy(allowed=False, confirm=False))
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert not _tracking_active(window), "星历不合格仍进入了跟踪（闸门没生效）"
    assert "不跟踪" in window.statusBar().currentMessage()


def test_confirm_declined_refuses_to_start(window, gate, monkeypatch):
    gate(_policy(allowed=True, confirm=True))
    monkeypatch.setattr(window, "_confirm_tracking_policy",
                        lambda name, policy: False)
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert not _tracking_active(window), "用户选了「否」仍进入跟踪"
    assert "已取消" in window.statusBar().currentMessage()


def test_confirm_accepted_starts_tracking(window, gate, monkeypatch):
    gate(_policy(allowed=True, confirm=True))
    monkeypatch.setattr(window, "_confirm_tracking_policy",
                        lambda name, policy: True)
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert _tracking_active(window), "确认后仍不放行：闸门把合法跟踪也堵死"


def test_fresh_tle_starts_without_confirm(window, gate):
    gate(_policy(allowed=True, confirm=False))
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert _tracking_active(window), "新鲜星历不需要确认却没放行"


def test_gate_reads_execution_tracker_not_sky_view(window, gate, monkeypatch):
    """闸门必须问**执行跟踪的** ``_loop_sat_tracker``，不是天空图侧 ``sat_tracker``。

    两者 TLE 目录各自独立：在线刷新（``_on_update_sat_tle``）只写
    ``_loop_sat_tracker``，面板「TLE 日期」与每拍 ``current_position()["policy"]``
    也来自它。闸门若读 ``sat_tracker``，会出现「刚刷新成功却被旧根数拒绝」；
    反向则是「天空图侧新鲜放行、执行侧 dead 照样调谐」。这里把两侧档位
    摆成相反，任一侧读错都会红。
    """
    gate(_policy(allowed=False, confirm=False))          # 执行侧 dead
    monkeypatch.setattr(window, "sat_tracker",
                        _PolicySource(_policy(allowed=True, confirm=False)),
                        raising=False)
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert not _tracking_active(window), "执行侧 dead 却因天空图侧新鲜放行"
    assert "不跟踪" in window.statusBar().currentMessage()

    gate(_policy(allowed=True, confirm=False))           # 执行侧 fresh
    monkeypatch.setattr(window, "sat_tracker",
                        _PolicySource(_policy(allowed=False, confirm=False)),
                        raising=False)
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert _tracking_active(window), "执行侧新鲜却被天空图侧旧根数挡住"


def test_missing_policy_source_fails_closed(window, monkeypatch):
    """tracker 取不到数（字段异常）必须按禁止处理，不许放行。"""
    import main_window as mw

    monkeypatch.setattr(mw, "LoopSatTracker", _FakeLoopTracker)
    monkeypatch.setattr(window, "_observer_lat", 43.9, raising=False)
    monkeypatch.setattr(window, "_observer_lon", 125.3, raising=False)

    class _Broken:
        def tracking_policy_for(self, _name=None):
            raise ValueError("catalog 条目缺字段")

    window._loop_sat_tracker = _Broken()
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert not _tracking_active(window), "取数失败却放行：缺数据被当成可信"
    assert "不跟踪" in window.statusBar().currentMessage()


# --------------------------------------------------------------------------- #
# P0-01 执行层：500 ms 轮询下发调谐前必须读同一份 policy 的 allow_tune
# --------------------------------------------------------------------------- #
from mbdsdr_ai.tle_integrity import tracking_policy  # noqa: E402


class _PollLoop:
    """只回答轮询要用的两个接口；policy 随位置一起透出（真实实现同款）。"""

    selected_name = _SAT

    def __init__(self, policy, elevation):
        self._policy = policy
        self._elevation = elevation

    def current_position(self):
        return {"valid": True, "azimuth": 180.0,
                "elevation": self._elevation, "policy": self._policy}

    def downlink_freq_hz(self):
        return 145.8e6


def _poll_once(window, monkeypatch, policy, elevation=45.0):
    """跑一拍真实 ``_poll_satellite_track``，返回实际下发调谐的参数列表。"""
    import mbdsdr_ai.doppler_link as dl
    import main_window as mw

    tuned = []

    class _Comp:
        def refresh(self, _pos):
            pass

        def rx_freq(self):
            return 145.8e6

    class _Panel:
        def update_position(self, _pos):
            pass

    class _Spectrum:
        def compute_snr_db(self):
            return None

    monkeypatch.setattr(dl, "get_compensator", lambda: _Comp())
    monkeypatch.setattr(window, "_loop_sat_tracker",
                        _PollLoop(policy, elevation), raising=False)
    monkeypatch.setattr(window, "_sat_track_mode", "FM", raising=False)
    monkeypatch.setattr(window, "_sat_signal_present", False, raising=False)
    monkeypatch.setattr(window, "_gimbal_follow", False, raising=False)
    monkeypatch.setattr(window, "sat_track_panel", _Panel(), raising=False)
    monkeypatch.setattr(window, "spectrum", _Spectrum(), raising=False)
    monkeypatch.setattr(window, "_update_doppler_chip", lambda _c: None,
                        raising=False)
    monkeypatch.setattr(window, "_on_tune_sdr",
                        lambda f, m: (tuned.append((f, m)), True)[1],
                        raising=False)
    mw.MainWindow._poll_satellite_track(window)
    return tuned


@pytest.mark.parametrize("fresh", [
    {"tier": "dead", "age_days": 400.0},
    {"tier": "unknown", "age_days": None},
])
def test_poll_denial_never_tunes(window, monkeypatch, fresh):
    """dead/unknown 才是 0 次（expired 是「确认后 1 次」，见下一条）。"""
    policy = tracking_policy(fresh)
    assert policy["allow_tune"] is False, policy

    assert _poll_once(window, monkeypatch, policy) == []


@pytest.mark.parametrize("fresh", [
    {"tier": "usable", "age_days": 2.0},
    {"tier": "fresh", "age_days": 0.5},
])
def test_poll_allow_tunes_once(window, monkeypatch, fresh):
    """allow_tune 为真 → 该下发就下发，且频率是多普勒校正后的值。"""
    policy = tracking_policy(fresh)
    assert policy["allow_tune"] is True, policy

    tuned = _poll_once(window, monkeypatch, policy)

    assert len(tuned) == 1, tuned
    assert tuned[0][0] == pytest.approx(145.8e6)


def test_poll_denied_tuning_reports_policy_reason(window, monkeypatch):
    """不下发时必须给出可读原因：档位 + 为什么禁 + 下一步。"""
    policy = tracking_policy({"tier": "dead", "age_days": 400.0})

    assert _poll_once(window, monkeypatch, policy) == []

    msg = window.statusBar().currentMessage()
    assert "未下发调谐" in msg, msg
    assert policy["label"] in msg, msg
    assert policy["next_action"] in msg, msg


def test_poll_expired_tunes_because_confirm_happened_at_start(
        window, monkeypatch):
    """expired 的 tune 策略是 confirm（allow_tune=True）：确认点在开始闸门。

    轮询不重复弹确认，云台另由 allow_gimbal=False 禁掉；这条钉住的是
    「执行层只读 allow_tune，不自己另写一套档位判断」。
    """
    policy = tracking_policy({"tier": "expired", "age_days": 40.0})
    assert policy["allow_tune"] is True
    assert policy["require_confirm"] is True
    assert policy["allow_gimbal"] is False

    assert len(_poll_once(window, monkeypatch, policy)) == 1


# --------------------------------------------------------------------------- #
# 云台闸门：按「当前实际跟踪目标」判，未跟踪才回落天空选中星
# --------------------------------------------------------------------------- #
class _GimbalTracker:
    """跟踪器替身：目录含 A/B，档位按名字给（判据仍走 tle_integrity）。"""

    def __init__(self, tracked, tiers):
        self.selected_name = tracked
        self._tiers = tiers

    def list_satellites(self):
        return [{"name": n} for n in self._tiers]

    def tracking_policy_for(self, name=None):
        tier = self._tiers[name]
        return tracking_policy({"tier": tier,
                                "age_days": 0.3 if tier == "fresh" else 40.0})


class _Timer:
    def __init__(self, active):
        self._active = active

    def isActive(self):  # noqa: N802 - 模拟 QTimer 接口
        return self._active


class _ManualGimbal:
    """manual 模式后端：只要星历放行，``_gimbal_available`` 就该为真。"""

    def __init__(self):
        from types import SimpleNamespace
        self.status = SimpleNamespace(mode=SimpleNamespace(value="manual"))


def _gimbal_gate(window, monkeypatch, *, tracked, tiers, sky, active):
    from types import SimpleNamespace

    monkeypatch.setattr(window, "_loop_sat_tracker",
                        _GimbalTracker(tracked, tiers), raising=False)
    monkeypatch.setattr(window, "_sat_track_timer", _Timer(active),
                        raising=False)
    monkeypatch.setattr(window, "sky_view",
                        SimpleNamespace(_selected_info={"title": sky}),
                        raising=False)
    monkeypatch.setattr(window, "_gimbal_controller", lambda: _ManualGimbal(),
                        raising=False)


def test_gimbal_gate_uses_tracked_target_not_sky_selection(window, monkeypatch):
    """反证①：跟踪 A（dead）+ 天空选 B（fresh）→ 必须拒。

    按 ``_sky_selected_name()`` 判会因 B 新鲜而放行，云台就会追着一条
    dead 根数算出的方位走。
    """
    _gimbal_gate(window, monkeypatch, tracked="SAT A",
                 tiers={"SAT A": "dead", "SAT B": "fresh"},
                 sky="SAT B", active=True)

    reason = window._tle_gimbal_block_reason()
    assert reason, "跟踪目标 dead，却因天空选中星新鲜放行云台"
    assert "跟踪目标「SAT A」" in reason, reason
    assert window._gimbal_available() is False


def test_gimbal_gate_allows_when_tracked_target_fresh(window, monkeypatch):
    """反证②：跟踪 A（fresh）+ 天空选 B（dead）→ 必须放行。

    按 ``_sky_selected_name()`` 判会因 B 失效而误禁当前跟踪目标。
    """
    _gimbal_gate(window, monkeypatch, tracked="SAT A",
                 tiers={"SAT A": "fresh", "SAT B": "dead"},
                 sky="SAT B", active=True)

    assert window._tle_gimbal_block_reason() == "", \
        "天空选中星 dead 却拦住跟踪目标"
    assert window._gimbal_available() is True


def test_gimbal_gate_fallback_names_its_target(window, monkeypatch):
    """未跟踪才回落天空选中星，且理由必须写明口径（不许静默切换依据）。"""
    _gimbal_gate(window, monkeypatch, tracked="SAT A",
                 tiers={"SAT A": "fresh", "SAT B": "dead"},
                 sky="SAT B", active=False)

    reason = window._tle_gimbal_block_reason()
    assert "按天空选中星判" in reason, reason
    assert "SAT B" in reason, reason
    assert window._gimbal_available() is False

