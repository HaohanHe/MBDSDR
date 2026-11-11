"""P0-1 反证：流式解码能力声明必须与实现一致。

背景：`SUPPORTED_DECODERS` 曾包含 10 类，但 `_resolve_decode_fn` 只实现了
adsb/rds；其余 8 类线程常驻空转，`start_decoder` 仍返回 ok=True。
本用例钉住：文件级解码器禁止走流式入口、不得创建线程、MCP 不得返回成功态。
"""
from __future__ import annotations

import threading

import pytest

from mbdsdr_ai.core import (
    DecoderNotAvailableError,
    DecoderNotStreamableError,
    FILE_ONLY_DECODERS,
    SDRController,
    STREAMABLE_DECODERS,
    SUPPORTED_DECODERS,
)
from mbdsdr_ai.core.mcp_server import MCPToolSet


class _AllowAllPolicy:
    """测试用策略：绕过写工具的人工确认门，只验证实现诚实性。"""

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


def test_declaration_partition_is_total_and_disjoint():
    """两份声明必须是 10 类的一个完整划分，不能漏也不能重。"""
    assert set(STREAMABLE_DECODERS).isdisjoint(FILE_ONLY_DECODERS)
    assert set(SUPPORTED_DECODERS) == (
        set(STREAMABLE_DECODERS) | set(FILE_ONLY_DECODERS)
    )
    assert len(SUPPORTED_DECODERS) == 10


@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_file_only_decoder_streaming_is_rejected(ctrl, dt):
    """反证 1：8 类文件级解码器逐个拒绝流式启动，错误码为 not_implemented。"""
    with pytest.raises(DecoderNotStreamableError) as ei:
        ctrl.start_decoder(dt)
    assert ei.value.code == "not_implemented"
    assert "文件级" in ei.value.message
    assert "--decode-file" in ei.value.suggestion


@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_mcp_reports_not_implemented(ctrl, dt):
    """反证 2：MCP 出口必须是 error/not_implemented，且没有 handle、没有 ok=True。"""
    ctrl.is_connected = lambda: True
    ts = MCPToolSet(ctrl, policy=_AllowAllPolicy())
    out = ts.call_tool("start_decoder", {"decoder_type": dt})
    assert out.get("ok") is not True
    assert out.get("outcome") == "error"
    assert out.get("error") == "not_implemented"
    assert "handle" not in out
    assert out.get("suggestion")


@pytest.mark.parametrize("dt", FILE_ONLY_DECODERS)
def test_file_only_does_not_create_decoder_thread(ctrl, dt):
    """反证 3：拒绝前后真实线程数不增，且未登记句柄。"""
    before = set(_dec_threads())
    with pytest.raises(DecoderNotStreamableError):
        ctrl.start_decoder(dt)
    after = set(_dec_threads())
    assert after == before, f"{dt} 被拒绝后仍创建了解码线程: {after - before}"
    assert ctrl._dec_counter == 0
    assert ctrl._decoders == {}


@pytest.mark.parametrize("dt", STREAMABLE_DECODERS)
def test_streamable_decoders_still_start(ctrl, dt):
    """反证 4：正向对照，adsb/rds 仍返回 ok=True + handle。"""
    ctrl.is_connected = lambda: True
    ts = MCPToolSet(ctrl, policy=_AllowAllPolicy())
    out = ts.call_tool("start_decoder", {"decoder_type": dt})
    assert out.get("ok") is True, f"{dt} 被误伤: {out}"
    handle = out["handle"]
    try:
        assert handle.startswith("dec-")
    finally:
        ctrl.stop_decoder(handle)


def test_unknown_decoder_is_not_mislabeled(ctrl):
    """反证 5：未知类型仍是 decoder_not_available，不与 not_implemented 混。"""
    with pytest.raises(DecoderNotAvailableError) as ei:
        ctrl.start_decoder("nonexistent_mode_xyz")
    assert type(ei.value) is DecoderNotAvailableError
    assert ei.value.code == "decoder_not_available"
