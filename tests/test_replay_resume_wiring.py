# SPDX-License-Identifier: MIT
"""
tests/test_replay_resume_wiring.py
===================================
验证"回放后实时接收停摆"修复（2026-11-02 SDR 审计 P0）:

  1. 恢复判据是"实时后端在不在"，不是旧 UI 线程 _iq_poll_timer 的活动态;
  2. _stop_replay 在后端连着时真调 _start_iq_streams 恢复;
  3. 后端未连接时不恢复, 频谱标未连接。

本文件只做行为断言，不读/不 pin 源码文本：判据用"定时器在跑但后端不在 →
不得恢复"和"定时器停着但后端在 → 必须恢复"两条直接证伪旧实现。

窗口走真实构造（新的 MainWindow()）而不是 __new__：_stop_replay 会访问
doppler_chip 等真实控件。生产路径里 _stop_replay 只在 __init__ 建完 UI 之后
被调用（回放播完 / 再点回放 / 拆连接 / 关窗），不存在"面板还没建就早停"的
路径，所以缺属性是测试夹具的问题，不给生产加 getattr 守卫。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_replay_resume_wiring.py -v
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402

_log = logging.getLogger(__name__)


class _SpectrumStub:
    def __init__(self):
        self.connected = None

    def set_connected(self, on):
        self.connected = on


@pytest.fixture(scope="module")
def window(isolated_profile):
    """真实构造的 MainWindow（isolated_profile 隔离设置/布局落盘）。"""
    w = MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception as exc:  # noqa: BLE001 - Qt 对象可能已随 app 销毁，留证不静默
        _log.warning("用例收尾失败（断开连接）：%s: %s",
                     type(exc).__name__, exc)


def _arm_replay_state(window, monkeypatch, backend_present):
    """把窗口置成"回放刚结束"：只动回放字段，其余保持真实控件。"""
    calls = []
    monkeypatch.setattr(window, "_start_iq_streams", lambda: calls.append(1))
    monkeypatch.setattr(window, "spectrum", _SpectrumStub())
    window._replay_timer = None
    window._replay_iq = None
    window._replay_pos = 0
    window._replay_resume_poll = True
    monkeypatch.setattr(window, "_active_sdr_backend",
                        object() if backend_present else None)
    return calls


class TestReplayResume:
    def test_resume_when_backend_connected_even_if_poll_timer_idle(
            self, window, monkeypatch):
        """iq 轮询定时器停着、后端在 → 仍必须恢复实时流水线。

        旧实现把 _iq_poll_timer.isActive() 当判据，回放结束后该定时器本就是停的，
        于是"恢复"永远不触发——这条用例在旧实现下必红。
        """
        calls = _arm_replay_state(window, monkeypatch, backend_present=True)
        window._iq_poll_timer.stop()
        assert window._iq_poll_timer.isActive() is False
        window._stop_replay()
        assert calls == [1], "后端连着就必须恢复实时流水线（与 iq 轮询定时器状态无关）"
        assert window._replay_resume_poll is False

    def test_no_resume_without_backend_even_if_poll_timer_running(
            self, window, monkeypatch):
        """iq 轮询定时器在跑、后端不在 → 不得恢复，频谱标未连接。

        旧实现在这条上会误恢复（判据只看定时器），这条用例同样能钉住回归。
        """
        calls = _arm_replay_state(window, monkeypatch, backend_present=False)
        window._iq_poll_timer.start()
        try:
            assert window._iq_poll_timer.isActive() is True
            window._stop_replay()
        finally:
            window._iq_poll_timer.stop()
        assert calls == [], "后端不在就不得恢复（iq 轮询定时器在跑也不能作为判据）"
        assert window.spectrum.connected is False

    def test_stop_replay_restores_real_widgets(self, window, monkeypatch):
        """停止回放会回到真实控件（doppler_chip 恢复可点），证明窗口是真构造。"""
        _arm_replay_state(window, monkeypatch, backend_present=False)
        window.doppler_chip.setEnabled(False)
        window._stop_replay()
        assert window.doppler_chip.isEnabled() is True
