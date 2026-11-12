# SPDX-License-Identifier: MIT
"""AI 侧 SDR 会话委托（桌面单权威接线）的守门用例。

背景：桌面 ``_active_sdr_backend`` 与 ``agent.sdr_manager`` 原本互不通信，
「AI 回断开成功、电台还在收」。这里锁死三件事：读直通桌面、写必须经桌面
确认、确认不了就显式失败（不许假装成功）。
"""
from __future__ import annotations

import pytest


class _FakeBackend:
    def __init__(self, name: str = "fake"):
        self.name = name


class _FakeWindow:
    """桌面宿主替身：真实实现见 desktop/main_window.py 的连接/拆除路径。"""

    def __init__(self):
        self._active_sdr_backend = None
        self.connect_calls = 0
        self.teardown_calls = 0
        self.connect_takes_effect = True

    def _connect_backend(self, backend):
        self.connect_calls += 1
        if self.connect_takes_effect:
            self._active_sdr_backend = backend
        return True

    def _teardown_connection(self):
        self.teardown_calls += 1
        self._active_sdr_backend = None


class _StubbornWindow(_FakeWindow):
    """拆不断（模拟 teardown 失败）：委托必须报失败，而不是报成功。"""

    def _teardown_connection(self):
        self.teardown_calls += 1


class _FakeLocal:
    def __init__(self, backends=None):
        self.backends = backends or {}

    def list_devices(self):
        return [{"device_id": k} for k in self.backends]

    def list_all_devices(self):
        return self.list_devices()

    def vfo_list(self):
        return ["VFO-A"]


def _session(window, backends=None):
    from desktop.ai_sdr_session import DesktopSdrSession

    return DesktopSdrSession(window=window, local_manager=_FakeLocal(backends))


def test_get_active_returns_desktop_backend():
    be = _FakeBackend()
    win = _FakeWindow()
    win._active_sdr_backend = be
    assert _session(win).get_active() is be


def test_connect_goes_through_desktop_and_reports_success_after_confirmation():
    be = _FakeBackend()
    win = _FakeWindow()
    session = _session(win, {"rtl_0": be})

    assert session.connect("rtl_0") is True
    assert win.connect_calls == 1
    assert win._active_sdr_backend is be


def test_connect_returns_false_when_desktop_did_not_take_effect():
    be = _FakeBackend()
    win = _FakeWindow()
    win.connect_takes_effect = False
    session = _session(win, {"rtl_0": be})

    assert session.connect("rtl_0") is False
    assert "未确认" in session.last_connect_notice
    assert win._active_sdr_backend is None


def test_disconnect_goes_through_desktop_teardown():
    be = _FakeBackend()
    win = _FakeWindow()
    win._active_sdr_backend = be
    session = _session(win)

    assert session.disconnect() is True
    assert win.teardown_calls == 1
    assert win._active_sdr_backend is None


def test_disconnect_fails_loudly_when_desktop_still_active():
    win = _StubbornWindow()
    win._active_sdr_backend = _FakeBackend()
    session = _session(win)

    assert session.disconnect() is False
    assert "未确认断连" in session.last_connect_notice


def test_unknown_device_is_refused_with_available_ids():
    win = _FakeWindow()
    session = _session(win, {"rtl_0": _FakeBackend()})

    assert session.connect("nope") is False
    assert "不在 AI 侧设备表" in session.last_connect_notice
    assert "rtl_0" in session.last_connect_notice
    assert win.connect_calls == 0


def test_no_window_refuses_instead_of_pretending():
    session = _session(lambda: None)

    assert session.get_active() is None
    assert session.connect("rtl_0") is False
    assert "尚未接上桌面窗口" in session.last_connect_notice


def test_non_session_members_delegate_to_internal_manager():
    session = _session(_FakeWindow(), {"rtl_0": _FakeBackend()})

    # VFO/扫描/IQ 等仍由内部真 manager 提供，不随桌面会话走。
    assert session.vfo_list() == ["VFO-A"]
    assert [d["device_id"] for d in session.list_devices()] == ["rtl_0"]


def test_agent_uses_injected_session_before_tool_registration(isolated_profile, monkeypatch):
    import mbdsdr_ai.agent as agent_mod

    seen = {}

    def fake_register(agent):
        seen["mgr"] = getattr(agent, "sdr_manager", None)

    monkeypatch.setattr(agent_mod, "register_sdr_tools", fake_register)
    sentinel = object()
    agent_mod.MBDSDRAgent(
        agent_mod.AgentConfig(api_key="test-key-not-used"), sdr_session=sentinel)

    assert seen["mgr"] is sentinel


def test_agent_without_injection_lets_register_create_its_own(isolated_profile, monkeypatch):
    import mbdsdr_ai.agent as agent_mod

    seen = {}

    def fake_register(agent):
        seen["has"] = hasattr(agent, "sdr_manager")

    monkeypatch.setattr(agent_mod, "register_sdr_tools", fake_register)
    agent_mod.MBDSDRAgent(agent_mod.AgentConfig(api_key="test-key-not-used"))

    assert seen["has"] is False
