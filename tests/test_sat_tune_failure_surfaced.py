# SPDX-License-Identifier: MIT
"""调谐下发异常不得静默（交接单 `3bd0e832` §5）。

原实现把下发异常整段吞掉：

    try:
        self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
    except Exception:
        pass

用户看到仰角/多普勒都在动、面板一切正常，实际一次都没调成功，也没有任何
文字线索。本文件断三件事：

1. 异常必须落状态栏：「调谐下发失败：<异常类型>: <信息>」；
2. 同一原因（类型 + 信息）连续出现只刷一次（去重键 = 整句 reason）；
3. 原因变化（换异常类型）再刷一次，不会被上一轮的键吃掉。

只看状态栏文案与频次，不碰真实硬件、不断言耗时。
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


class _PollLoop:
    """只回答轮询要用的接口；policy 随位置一起透出（与真实实现同款）。"""

    selected_name = _SAT

    def current_position(self):
        return {"valid": True, "azimuth": 180.0, "elevation": 45.0,
                "policy": {"allow_tune": True, "label": "星历新鲜",
                           "reason": "TLE 年龄 0.3 天",
                           "next_action": "可直接跟踪"}}

    def downlink_freq_hz(self):
        return 145.8e6


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


def _install(window, monkeypatch, exc):
    """装好一拍轮询所需的最小替身；``exc`` 为 ``_on_tune_sdr`` 要抛的异常。"""
    import mbdsdr_ai.doppler_link as dl

    monkeypatch.setattr(dl, "get_compensator", lambda: _Comp())
    monkeypatch.setattr(window, "_loop_sat_tracker", _PollLoop(), raising=False)
    monkeypatch.setattr(window, "_sat_track_mode", "FM", raising=False)
    monkeypatch.setattr(window, "_sat_signal_present", False, raising=False)
    monkeypatch.setattr(window, "_gimbal_follow", False, raising=False)
    monkeypatch.setattr(window, "sat_track_panel", _Panel(), raising=False)
    monkeypatch.setattr(window, "spectrum", _Spectrum(), raising=False)
    monkeypatch.setattr(window, "_update_doppler_chip", lambda _c: None,
                        raising=False)
    # 去重键跨用例共享同一个 window：每条用例从「还没提示过」开始，
    # 否则上一条留下的键会让本条第一拍就被去重掉（测的就不是本意）。
    monkeypatch.setattr(window, "_sat_tune_failed_reason", "", raising=False)

    def _raise(_f, _m):
        raise exc

    monkeypatch.setattr(window, "_on_tune_sdr", _raise, raising=False)


def _poll_once(window):
    import main_window as mw
    mw.MainWindow._poll_satellite_track(window)


def test_tune_failure_is_surfaced_with_exception_type(window, monkeypatch):
    _install(window, monkeypatch, RuntimeError("串口被占用"))
    window.statusBar().clearMessage()

    _poll_once(window)

    msg = window.statusBar().currentMessage()
    assert "调谐下发失败" in msg, f"下发异常被静默吞掉了，状态栏：{msg!r}"
    assert "RuntimeError" in msg, f"提示里没有异常类型，状态栏：{msg!r}"
    assert "串口被占用" in msg, f"提示里没有异常信息，状态栏：{msg!r}"


def test_same_tune_failure_only_posts_once(window, monkeypatch):
    _install(window, monkeypatch, RuntimeError("串口被占用"))
    window.statusBar().clearMessage()

    _poll_once(window)
    first = window.statusBar().currentMessage()
    assert "调谐下发失败" in first, first

    # 清空后再跑：同因同文不应重新刷出来（去重键是整句 reason）。
    window.statusBar().clearMessage()
    _poll_once(window)
    assert window.statusBar().currentMessage() == "", (
        "同一原因连续出现被反复刷屏："
        f"{window.statusBar().currentMessage()!r}")


def test_changed_tune_failure_posts_again(window, monkeypatch):
    _install(window, monkeypatch, RuntimeError("串口被占用"))
    window.statusBar().clearMessage()
    _poll_once(window)

    # 原因变了（换异常类型）→ 必须再提示一次，不能沿用旧去重键。
    _install(window, monkeypatch, ValueError("频率越界"))
    window.statusBar().clearMessage()
    _poll_once(window)
    msg = window.statusBar().currentMessage()
    assert "ValueError" in msg, f"原因变化后没有重新提示，状态栏：{msg!r}"
