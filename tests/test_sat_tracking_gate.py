# SPDX-License-Identifier: MIT
"""P0-01 反证：卫星跟踪启停只认 `tracking_policy_for()` 的许可。

事故形态（P0-01）：桌面端自己比较 ``tle_tier``/``age_days`` 决定能不能跟踪，
与 AI/天空动作总线的档位判定各写一套，两处迟早对不上；过期星历、身份不符、
无数据都能照样调谐，用户拿着错误多普勒去收信号。

本文件用假 tracker 替换档位来源，只验桌面确实读 policy：
- ``command_allowed`` 为假 → 拒绝开始，状态栏给出档位原因；
- ``require_confirm`` 为真且用户说不 → 拒绝；
- ``require_confirm`` 为真且用户说是 / 无需确认 → 正常进入跟踪。
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

    def select_satellite(self, name):
        self.selected_name = name
        return True

    def current_position(self):
        # 面板会拿它刷 az/el：给「无有效解」而不是 None，面板走占位分支。
        return {"valid": False}

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


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

    class _FakeSatTracker:
        def __init__(self, policy):
            self.policy = policy

        def tracking_policy_for(self, _name=None):
            return self.policy

    monkeypatch.setattr(mw, "LoopSatTracker", _FakeLoopTracker)
    monkeypatch.setattr(window, "_observer_lat", 43.9, raising=False)
    monkeypatch.setattr(window, "_observer_lon", 125.3, raising=False)
    window._loop_sat_tracker = None

    def _install(policy):
        monkeypatch.setattr(window, "sat_tracker", _FakeSatTracker(policy),
                            raising=False)
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


def test_missing_policy_source_fails_closed(window, monkeypatch):
    """tracker 取不到数（字段异常）必须按禁止处理，不许放行。"""
    import main_window as mw

    monkeypatch.setattr(mw, "LoopSatTracker", _FakeLoopTracker)
    monkeypatch.setattr(window, "_observer_lat", 43.9, raising=False)
    monkeypatch.setattr(window, "_observer_lon", 125.3, raising=False)
    window._loop_sat_tracker = None

    class _Broken:
        def tracking_policy_for(self, _name=None):
            raise ValueError("catalog 条目缺字段")

    monkeypatch.setattr(window, "sat_tracker", _Broken(), raising=False)
    window._start_satellite_tracking(_SAT, 137.62e6)
    assert not _tracking_active(window), "取数失败却放行：缺数据被当成可信"
    assert "不跟踪" in window.statusBar().currentMessage()
