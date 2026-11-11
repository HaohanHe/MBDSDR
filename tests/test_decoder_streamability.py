# SPDX-License-Identifier: MIT
"""W65 反证：8 类文件级解码器的两处行为必须诚实。

P0-03（流式入口）：8 类文件级解码器既不能起流式线程，也不能经 MCP 回
`ok=True`+handle——改前它们全部「启动成功、永远 0 条」。
P0-04（文件级入口）：拿静音/全零输入喂文件级入口，必须给出失败码，
不得落回 `ok=True`、`num_messages=0`（「没信号」被说成「解码成功」）。

覆盖边界（不夸大）：`noaa_apt`/`sstv` 没有走 decode_router 的文件级路由，
本文件只钉住它们的流式拒绝；它们的文件级入图链另有工单（P1-23/P2-05）。
"""
from __future__ import annotations

import threading
import wave

import numpy as np
import pytest

from mbdsdr_ai.core import (
    FILE_ONLY_DECODERS,
    SDRController,
    STREAMABLE_DECODERS,
)
from mbdsdr_ai.core.errors import DecoderNotStreamableError
from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.decode_router import run_digital_mode, supported_modes


class _AllowAllPolicy:
    def action_for(self, name, is_write, domain=""):
        return "allow"


def _dec_threads():
    return [t for t in threading.enumerate() if t.name.startswith("dec-")]


@pytest.fixture
def ctrl(tmp_path):
    c = SDRController(config_dir=str(tmp_path))
    yield c
    try:
        c.shutdown()
    except Exception:
        pass


@pytest.fixture
def silent_wav(tmp_path):
    path = tmp_path / "silence_12k.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(12000)
        w.writeframes(np.zeros(12000 * 6, dtype=np.int16).tobytes())
    return str(path)


@pytest.fixture
def zero_cf32(tmp_path):
    path = tmp_path / "zeros.cf32"
    np.zeros(200_000, dtype=np.float32).tofile(str(path))
    return str(path)


# ---------------------------------------------------------------- P0-03
@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_streaming_entry_rejected_without_side_effect(ctrl, dt):
    """文件级解码器拒绝流式：不建线程、不占句柄、错误码可读。"""
    before = set(_dec_threads())
    with pytest.raises(DecoderNotStreamableError) as ei:
        ctrl.start_decoder(dt)
    assert ei.value.code == "not_implemented"
    assert dt in ei.value.suggestion or "--decode-file" in ei.value.suggestion
    assert set(_dec_threads()) == before
    assert ctrl._dec_counter == 0 and ctrl._decoders == {}


@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_mcp_streaming_never_ok(ctrl, dt):
    """MCP 出口：error 信封、无 handle、ok 不为 True。"""
    ctrl.is_connected = lambda: True
    ts = MCPToolSet(ctrl, policy=_AllowAllPolicy())
    out = ts.call_tool("start_decoder", {"decoder_type": dt})
    assert out.get("ok") is not True
    assert out.get("outcome") == "error"
    assert "handle" not in out
    assert out.get("suggestion")


@pytest.mark.parametrize("dt", STREAMABLE_DECODERS)
def test_streamable_entry_still_works(ctrl, dt):
    """正向对照：adsb/rds 仍能起流式（防止把能力一刀切禁掉）。"""
    ctrl.is_connected = lambda: True
    ts = MCPToolSet(ctrl, policy=_AllowAllPolicy())
    out = ts.call_tool("start_decoder", {"decoder_type": dt})
    assert out.get("ok") is True, out
    try:
        assert out["handle"].startswith("dec-")
    finally:
        ctrl.stop_decoder(out["handle"])


# ---------------------------------------------------------------- P0-04
_ROUTER_FILE_ONLY = tuple(
    m for m in FILE_ONLY_DECODERS if m in supported_modes())


def test_router_file_only_scope_is_documented():
    """锁住覆盖范围：6 类走 decode_router，noaa_apt/sstv 不在其中。"""
    assert set(_ROUTER_FILE_ONLY) == {
        "aprs", "acars", "pocsag", "wspr", "ft8", "meteor"}
    assert "noaa_apt" not in supported_modes()
    assert "sstv" not in supported_modes()


@pytest.mark.parametrize("mode", _ROUTER_FILE_ONLY)
def test_zero_input_never_reports_success(mode, silent_wav, zero_cf32,
                                         tmp_path):
    """静音/全零输入：ok 必须为 False 且带 error_code，不能是「成功 0 条」。"""
    src = zero_cf32 if mode == "meteor" else silent_wav
    r = run_digital_mode(src, mode, output_dir=str(tmp_path / f"out_{mode}"))
    assert r.get("ok") is False, f"{mode} 把无信号说成成功: {r}"
    assert r.get("error_code"), f"{mode} 失败却没有错误码: {r}"
    assert r.get("num_messages") in (0, None)


@pytest.mark.parametrize("mode", ["aprs", "acars", "pocsag"])
def test_wav_silence_is_classified_as_nothing_detected(mode, silent_wav,
                                                       tmp_path):
    """WAV 静音必须落到 nothing_detected（三态之一），不是泛化 decode_error。"""
    r = run_digital_mode(
        silent_wav, mode, output_dir=str(tmp_path / f"out_{mode}"))
    assert r["error_code"] == "nothing_detected", r
    assert r["status"] in ("empty", "error"), r


def test_ft8_generic_entry_is_honest_not_silent(silent_wav, tmp_path):
    """FT8 通用入口当前未接线：必须明确失败，不得假装解码成功。"""
    r = run_digital_mode(
        silent_wav, "ft8", output_dir=str(tmp_path / "out_ft8"))
    assert r.get("ok") is False
    assert r.get("error_code") in ("not_implemented", "nothing_detected"), r
