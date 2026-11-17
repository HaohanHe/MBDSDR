# SPDX-License-Identifier: MIT
"""SDR 常驻重连 opt-in 开关（Task 78）的守卫。

全部使用调试信号源 / 假 connect / 临时配置目录：不打开真实硬件、不联网、
不依赖桌面 UI。开关只在**首次连接成功之后**生效，且不启动后台线程。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.core.controller import (  # noqa: E402
    SDR_AUTORECONNECT_ENV,
    SDR_AUTORECONNECT_KEY,
    SDRController,
)
from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402


@pytest.fixture(autouse=True)
def _no_switch_env(monkeypatch):
    """默认清掉开关环境变量，避免跑测机器的真实环境串进来。"""
    monkeypatch.delenv(SDR_AUTORECONNECT_ENV, raising=False)


def _controller(tmp_path) -> SDRController:
    return SDRController(backend=None, config_dir=str(tmp_path),
                         runtime_sources=RuntimeSources())


def _write_config(tmp_path, value, key=SDR_AUTORECONNECT_KEY) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({key: value}), encoding="utf-8")


def test_env_on_enables_only_after_first_successful_connect(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)

    # 还没连接：不启用、也没有监督器（不会自发连接）
    assert ctrl.sdr_reconnect_status()["enabled"] is False
    assert ctrl._sdr_supervisor is None

    assert ctrl.connect("debug") is True

    status = ctrl.sdr_reconnect_status()
    assert status["enabled"] is True
    assert status["source"] == "env"
    assert status["attempts"] == 0
    assert status["running"] is False, "自动 opt-in 不得启动后台线程"
    snap = ctrl.get_runtime_sources()["sdr"]["reconnect"]
    assert snap["enabled"] is True
    assert snap["source"] == "env"
    ctrl.shutdown()


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_truthy_variants_enable(tmp_path, monkeypatch, value):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, value)
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    assert ctrl.sdr_reconnect_status()["enabled"] is True
    ctrl.shutdown()


def test_env_unset_stays_disabled(tmp_path):
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    status = ctrl.sdr_reconnect_status()
    assert status["enabled"] is False
    assert status["source"] == "default"
    assert ctrl.get_runtime_sources()["sdr"]["reconnect"]["enabled"] is False
    ctrl.shutdown()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "maybe", ""])
def test_env_off_or_invalid_disables_and_beats_config(tmp_path, monkeypatch, value):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, value)
    # 配置文件写着开，但环境变量优先：必须仍为关
    _write_config(tmp_path, True)
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    status = ctrl.sdr_reconnect_status()
    assert status["enabled"] is False
    assert status["source"] == "env"
    ctrl.shutdown()


def test_config_key_enables_when_env_unset(tmp_path):
    _write_config(tmp_path, True)
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    status = ctrl.sdr_reconnect_status()
    assert status["enabled"] is True
    assert status["source"] == "config"
    ctrl.shutdown()


def test_no_spontaneous_connect_before_first_connect(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)
    calls = []
    monkeypatch.setattr(
        SDRController, "connect",
        lambda self, device_id: calls.append(device_id) or True)

    for _ in range(3):
        assert ctrl.sdr_reconnect_tick() is False
    assert calls == [], "未连接时不得自发尝试连接"
    assert ctrl._backend is None
    assert ctrl._last_device_id == ""
    assert ctrl.sdr_reconnect_status()["enabled"] is False


def test_disable_prevents_further_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    assert ctrl.sdr_reconnect_status()["enabled"] is True

    closed = ctrl.disable_sdr_autoreconnect()
    assert closed["enabled"] is False

    calls = []
    monkeypatch.setattr(
        ctrl, "connect", lambda device_id: calls.append(device_id) or True)
    for _ in range(3):
        ctrl.sdr_reconnect_tick()
    assert calls == [], "显式关闭后 tick 不得再发起连接"
    ctrl.shutdown()


def test_manual_connect_after_disable_does_not_reenable(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    ctrl.disable_sdr_autoreconnect()

    # 用户显式关闭的意图压过 opt-in：再手动连接成功也不自动拉起
    assert ctrl.connect("debug") is True
    assert ctrl.sdr_reconnect_status()["enabled"] is False
    ctrl.shutdown()


def test_disconnect_stops_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    assert ctrl.sdr_reconnect_status()["enabled"] is True

    ctrl.disconnect()
    assert ctrl.is_connected() is False
    assert ctrl.sdr_reconnect_status()["enabled"] is False
    ctrl.shutdown()


def test_shutdown_stops_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv(SDR_AUTORECONNECT_ENV, "1")
    ctrl = _controller(tmp_path)
    assert ctrl.connect("debug") is True
    assert ctrl.sdr_reconnect_status()["enabled"] is True

    ctrl.shutdown()
    assert ctrl.is_connected() is False
    status = ctrl.sdr_reconnect_status()
    assert status["enabled"] is False
    assert status["running"] is False
