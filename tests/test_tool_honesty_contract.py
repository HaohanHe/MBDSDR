# SPDX-License-Identifier: MIT
"""工具诚实性反证（2026-11-05 接入层批次）。

对 `docs/tool-honesty-matrix.md` 的判定做可执行钉扎：
  * 无设备：设值工具不得声称已下发硬件；测量工具不得返回数值；
  * 无设备：配置读取必须声明 `connected=false` + `source=configured-target`；
  * 离线：外部 MCP connect 撞上被拒绝的连接时必须 ok=false，不得假装连上；
  * 正证：连上调试源后，设值工具必须真的应用（不出现 applied=false）。

直接调 handler、绕过写工具的 confirm_token 门——本用例验证的是实现诚实性，
不是授权门本身（门另有测试）。
"""
from __future__ import annotations

import socket

import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet


def _call(ts: MCPToolSet, name: str, args=None):
    tool = next(t for t in ts._table if t["name"] == name)
    return tool["handler"](ts.controller, args or {})


SETTERS = (
    "set_frequency", "set_demod", "set_gain", "set_sample_rate",
    "set_bandwidth", "set_squelch", "set_volume", "set_mute",
    "set_anr_enabled", "set_anr_strength", "stop_audio",
)


@pytest.mark.parametrize("name", SETTERS)
def test_no_device_setters_declare_not_applied(name):
    ts = MCPToolSet()
    out = _call(ts, name, {
        "hz": 100_000_000.0, "db": 0.0, "sr": 2_400_000.0,
        "mode": "FM", "muted": False, "enabled": True, "level": 3,
    })
    assert out.get("ok") is True
    assert out.get("connected") is False, f"{name} 未声明未连接: {out}"
    assert out.get("applied") is False, f"{name} 谎称已下发硬件: {out}"
    assert out.get("stored") is True
    assert out.get("note"), f"{name} 缺说明"


@pytest.mark.parametrize("name,key", [
    ("get_frequency", "frequency_hz"),
    ("get_demod", "demod"),
])
def test_config_readers_declare_configured_target(name, key):
    ts = MCPToolSet()
    out = _call(ts, name)
    assert out.get("connected") is False
    assert out.get("source") == "configured-target"
    assert out.get("note")
    assert key in out


@pytest.mark.parametrize("name,args,code", [
    ("read_spectrum", {"nfft": 1024}, "not_connected"),
    ("read_iq", {"n": 1024}, "not_connected"),
    ("read_audio", {"n": 1024}, "not_connected"),
    ("start_scan", {"start_hz": 90e6, "stop_hz": 110e6, "step_hz": 1e6},
     "not_connected"),
    ("start_decoder", {"decoder_type": "pocsag"}, "not_connected"),
])
def test_no_device_measure_tools_fail(name, args, code):
    ts = MCPToolSet()
    out = _call(ts, name, args)
    # 老壳返回形如 {"data": None, "error": "not_connected"}（省略 ok）；
    # 语义上 ok 不成立：必须有 error 且没有数据。
    assert out.get("ok") is not True, f"{name} 无设备却返回成功: {out}"
    assert out.get("code") == code or out.get("error") == code
    assert out.get("data") is None
    assert out.get("suggestion"), f"{name} 失败没有可行动文案: {out}"


@pytest.mark.parametrize("name,args,code", [
    ("stop_scan", {"handle": "nope"}, "scan_not_found"),
    ("get_scan_results", {"handle": "nope"}, "scan_not_found"),
    ("stop_decoder", {"handle": "nope"}, "decoder_not_found"),
    ("get_decoder_messages", {"handle": "nope"}, "decoder_not_found"),
])
def test_unknown_handles_fail_not_silent(name, args, code):
    ts = MCPToolSet()
    out = _call(ts, name, args)
    assert out.get("ok") is False
    assert out.get("code") == code
    assert out.get("suggestion")


def test_start_audio_failure_is_actionable():
    ts = MCPToolSet()
    out = _call(ts, "start_audio")
    assert out.get("ok") is False
    assert out.get("code") == "not_connected"
    assert out.get("error") and out.get("suggestion")


def test_learn_noise_floor_failure_is_actionable():
    ts = MCPToolSet()
    out = _call(ts, "learn_noise_floor")
    assert out.get("error") == "not_connected"
    assert out.get("suggestion")


def test_invalid_fst4_text_is_refused_with_guidance():
    ts = MCPToolSet()
    out = _call(ts, "decoder_fst4_encode_text", {"text": "AUDIT"})
    assert out.get("error") == "invalid_text"
    assert out.get("suggestion")


def test_offline_external_mcp_connect_fails_instead_of_pretending(monkeypatch):
    def _deny(*a, **k):
        raise ConnectionRefusedError(111, "Connection refused (test offline)")

    monkeypatch.setattr(socket, "create_connection", _deny)
    ts = MCPToolSet()
    ts.mcp_clients.register({
        "name": "audit-offline",
        "type": "streamable_http",
        "url": "http://10.255.255.1:9/mcp",
    })
    out = _call(ts, "mcp_client_connect", {"name": "audit-offline"})
    assert out.get("ok") is False
    assert out.get("error") == "transport"
    assert "refused" in (out.get("detail") or "").lower()
    assert out.get("suggestion")


@pytest.mark.parametrize("name,args,readback", [
    ("set_frequency", {"hz": 145_800_000.0},
     lambda c: c.get_frequency() == 145_800_000.0),
    ("set_demod", {"mode": "USB"}, lambda c: c.get_demod() == "USB"),
])
def test_connected_debug_source_setters_really_apply(name, args, readback):
    """正证：连上调试源后设值工具必须真的下发（不是只回 ok）。"""
    ts = MCPToolSet()
    ts.controller.connect("debug")
    try:
        out = _call(ts, name, args)
        assert out.get("ok") is True
        assert out.get("applied", True) is not False
        # 真实状态已改变，而不是只记本地配置/只回口头成功。
        assert readback(ts.controller), f"{name} 回了 ok 但状态未变: {out}"
    finally:
        ts.controller.disconnect()
