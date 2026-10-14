# SPDX-License-Identifier: MIT
"""工具结果 spill 与 outcome 分类测试（2026-10-14 上游审计第二落点）。

背景：MCP 面 read_spectrum/read_iq 等工具动辄返回上千点 JSON，全量塞进
对话上下文会把 LLM 上下文打爆（desktop 侧只有 4000 字符硬截断、无落盘
指针）。本测试钉住兜底升级后的信封语义：

  * 小结果不 spill、信封零变化（既有行为不回退——既有调用方钉死相等性）；
  * 大结果 spill：信封字段齐全、落盘文件可完整读回、头尾预览可读且不重叠；
  * 写盘失败（OSError，makedirs / open 两条路径）：诚实
    ``spill_write_failed``，不假装成功、不给半截结果、不崩；
  * outcome 四分类（executed / gated / denied / error）+ unknown_tool=error
    各就各位，且只增键不改既有键（向后兼容）；
  * 阈值常量可注入（monkeypatch ``TOOL_RESULT_SPILL_THRESHOLD``）。

stub controller 用 ``types.SimpleNamespace``：只 duck-type 出被测工具会
调到的方法，不实例化真 SDRController、不触硬件；spill 目录一律注入
tmp_path，不污染真实 ``~/.mbdsdr``。
"""
from __future__ import annotations

import json
import types

import pytest

from mbdsdr_ai.core import mcp_server
from mbdsdr_ai.core.errors import SDRUserError
from mbdsdr_ai.core.mcp_server import (TOOL_RESULT_PREVIEW_CHARS,
                                       TOOL_RESULT_SPILL_THRESHOLD,
                                       MCPToolSet)
from mbdsdr_ai.tool_policy import ToolPolicy


# ---------------------------------------------------------------------------
# stub 助手
# ---------------------------------------------------------------------------
def _stub(**methods):
    """duck-type stub controller：只提供被测工具会调到的方法。"""
    return types.SimpleNamespace(**methods)


def _big_scan_results():
    """构造 >4000 字符的扫描结果（大 dict/大 list，模拟 read_spectrum 长输出）。"""
    return {"handle": "scan-1",
            "segments": [{"start_hz": 88_000_000 + i * 100_000,
                          "peak_db": -90.0 + (i % 50) * 0.25,
                          "snr_db": 3.0 + (i % 7) * 0.5}
                         for i in range(400)]}


def _use_tmp_spill(monkeypatch, tmp_path):
    """把 spill 目录注入 tmp_path（不存在的嵌套目录，顺带钉"现场创建"）。"""
    spill_dir = tmp_path / "nested" / "tool_results"
    monkeypatch.setattr(mcp_server, "TOOL_RESULT_SPILL_DIR", str(spill_dir))
    return spill_dir


# ---------------------------------------------------------------------------
# 常量钉死（规格值）
# ---------------------------------------------------------------------------
def test_default_constants_are_spec_values():
    assert TOOL_RESULT_SPILL_THRESHOLD == 4000
    assert TOOL_RESULT_PREVIEW_CHARS == 600
    assert mcp_server.TOOL_RESULT_SPILL_DIR == "~/.mbdsdr/tool_results"


# ---------------------------------------------------------------------------
# 小结果：零行为变化
# ---------------------------------------------------------------------------
def test_small_result_is_returned_as_is_no_spill(tmp_path, monkeypatch):
    spill_dir = _use_tmp_spill(monkeypatch, tmp_path)
    ts = MCPToolSet(controller=_stub(get_frequency=lambda: 98_500_000.0),
                    policy=ToolPolicy())
    out = ts.call_tool("get_frequency", {})
    # 信封就是 handler 结果本身：不落盘、不多任何键（既有相等性断言不回退）
    assert out == {"frequency_hz": 98_500_000.0}
    assert not spill_dir.exists()


def test_threshold_boundary_equal_size_not_spilled(tmp_path, monkeypatch):
    """恰好等于阈值（未"超过"）不 spill；超过一字符才 spill。"""
    spill_dir = _use_tmp_spill(monkeypatch, tmp_path)
    payload = {"pad": "x" * 60}          # 序列化后 74 字符
    text_len = len(json.dumps(payload, ensure_ascii=False, default=str))
    ts = MCPToolSet(controller=_stub(get_scan_results=lambda h: payload),
                    policy=ToolPolicy())

    monkeypatch.setattr(mcp_server, "TOOL_RESULT_SPILL_THRESHOLD", text_len)
    assert ts.call_tool("get_scan_results", {"handle": "h"}) == payload
    assert not spill_dir.exists()

    monkeypatch.setattr(mcp_server, "TOOL_RESULT_SPILL_THRESHOLD", text_len - 1)
    assert ts.call_tool("get_scan_results", {"handle": "h"})["truncated"] is True


# ---------------------------------------------------------------------------
# 大结果：spill 信封字段齐全、可完整读回
# ---------------------------------------------------------------------------
def test_large_result_spills_with_full_envelope(tmp_path, monkeypatch):
    spill_dir = _use_tmp_spill(monkeypatch, tmp_path)
    original = _big_scan_results()
    ts = MCPToolSet(controller=_stub(get_scan_results=lambda h: original),
                    policy=ToolPolicy())

    out = ts.call_tool("get_scan_results", {"handle": "scan-1"})

    # 信封字段齐全
    assert out["ok"] is True
    assert out["tool"] == "get_scan_results"
    assert out["outcome"] == "executed"
    assert out["truncated"] is True
    assert out["full_result_available"] is True
    # 目录不存在 -> 现场创建；文件落在目录内且可完整读回
    assert spill_dir.is_dir()
    spill_path = out["spill_path"]
    assert spill_path.startswith(str(spill_dir))
    assert spill_path.endswith(".json")
    with open(spill_path, encoding="utf-8") as f:
        assert json.load(f) == original
    # bytes = 完整序列化的 UTF-8 字节数
    text = json.dumps(original, ensure_ascii=False, default=str)
    assert out["bytes"] == len(text.encode("utf-8"))
    # 预览各 ≤ TOOL_RESULT_PREVIEW_CHARS，且确为完整文本的头 / 尾
    assert len(out["preview_head"]) <= TOOL_RESULT_PREVIEW_CHARS
    assert len(out["preview_tail"]) <= TOOL_RESULT_PREVIEW_CHARS
    assert text.startswith(out["preview_head"])
    assert text.endswith(out["preview_tail"])
    # 600×2 < 4000： spilled 结果头尾预览不可能重叠/交叠
    assert len(out["preview_head"]) + len(out["preview_tail"]) < len(text)


def test_preview_head_tail_concatenation_is_readable(tmp_path, monkeypatch):
    """头尾预览拼接后保留 JSON 首尾关键字段，中间是省略的长数组。"""
    _use_tmp_spill(monkeypatch, tmp_path)
    payload = {"head_marker": "频谱数据从这里开始",
               "points": [float(i) * 0.5 for i in range(2000)],
               "tail_marker": "频谱数据到这里结束"}
    ts = MCPToolSet(controller=_stub(get_scan_results=lambda h: payload),
                    policy=ToolPolicy())

    out = ts.call_tool("get_scan_results", {"handle": "h"})

    assert out["truncated"] is True
    assert "head_marker" in out["preview_head"]
    assert "tail_marker" in out["preview_tail"]
    # 头尾直接拼接是一段可读的"开头……结尾"文本（中间细节靠 spill_path 读回）
    joined = out["preview_head"] + out["preview_tail"]
    assert joined.startswith('{"head_marker"')
    assert joined.rstrip().endswith('"频谱数据到这里结束"}')


# ---------------------------------------------------------------------------
# 写盘失败：诚实报错，不假装成功
# ---------------------------------------------------------------------------
def test_spill_write_failure_makedirs_is_honest(tmp_path, monkeypatch,
                                                capsys):
    _use_tmp_spill(monkeypatch, tmp_path)

    def _boom(*a, **kw):
        raise OSError("磁盘只读（stub）")

    monkeypatch.setattr(mcp_server.os, "makedirs", _boom)
    ts = MCPToolSet(controller=_stub(get_scan_results=lambda h: _big_scan_results()),
                    policy=ToolPolicy())

    out = ts.call_tool("get_scan_results", {"handle": "h"})

    assert out["ok"] is False
    assert out["outcome"] == "error"
    assert out["error"] == "spill_write_failed"
    assert "磁盘只读（stub）" in out["technical_detail"]
    # 不假装成功：没有 spill_path / 没有伪 truncated
    assert "spill_path" not in out
    assert "truncated" not in out
    # stderr 留痕（日志通道），不污染协议流
    assert "spill 写入失败" in capsys.readouterr().err


def test_spill_write_failure_open_is_honest(tmp_path, monkeypatch):
    _use_tmp_spill(monkeypatch, tmp_path)
    real_open = open

    def _fake_open(file, *args, **kwargs):
        if str(file).endswith(".json"):
            raise OSError("句柄耗尽（stub）")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _fake_open)
    ts = MCPToolSet(controller=_stub(get_scan_results=lambda h: _big_scan_results()),
                    policy=ToolPolicy())

    out = ts.call_tool("get_scan_results", {"handle": "h"})

    assert out == {"ok": False, "outcome": "error",
                   "error": "spill_write_failed",
                   "technical_detail": "句柄耗尽（stub）"}


# ---------------------------------------------------------------------------
# outcome 四分类 + unknown_tool
# ---------------------------------------------------------------------------
def test_outcome_executed_lives_on_spill_envelope(tmp_path, monkeypatch):
    """executed 只落在超长结果的 spill 信封上；小结果信封零变化不带 outcome。"""
    _use_tmp_spill(monkeypatch, tmp_path)
    ts = MCPToolSet(controller=_stub(get_frequency=lambda: 98_500_000.0,
                                     get_scan_results=lambda h: _big_scan_results()),
                    policy=ToolPolicy())
    assert "outcome" not in ts.call_tool("get_frequency", {})
    spilled = ts.call_tool("get_scan_results", {"handle": "h"})
    assert spilled["outcome"] == "executed"


def test_outcome_gated_manual_gate():
    ts = MCPToolSet(controller=_stub(), policy=ToolPolicy())
    out = ts.call_tool("set_frequency", {"hz": 100e6})
    assert out["outcome"] == "gated"
    # 既有键一个不少（向后兼容，outcome 只增不改）
    assert out["ok"] is False and out["gated"] is True
    assert out["error"] == "手动模式：未执行 set_frequency"
    assert out["policy"] == "ask"
    assert "arm_manual=true" in out["hint"] and "approve_session=true" in out["hint"]


def test_outcome_denied_rule():
    policy = ToolPolicy([{"pattern": "set_frequency", "action": "deny"}])
    ts = MCPToolSet(controller=_stub(), policy=policy)
    out = ts.call_tool("set_frequency", {"hz": 100e6, "arm_manual": True})
    assert out["outcome"] == "denied"
    assert out["ok"] is False and out["gated"] is True and out["denied"] is True
    assert "denied_by_policy" in out["error"]


def test_outcome_error_sdr_user_error():
    def _raise(device_id):
        raise SDRUserError("设备炸了（stub）", suggestion="拔插重试",
                           technical_detail="stub detail")

    policy = ToolPolicy([{"pattern": "connect", "action": "allow"}])
    ts = MCPToolSet(controller=_stub(connect=_raise), policy=policy)
    out = ts.call_tool("connect", {"device_id": "debug"})
    # SDRUserError 既有信封四键原样，只多 outcome
    assert out == {"error": "sdr_error", "message": "设备炸了（stub）",
                   "suggestion": "拔插重试", "technical_detail": "stub detail",
                   "outcome": "error"}


def test_outcome_error_internal_exception():
    def _raise(handle):
        raise RuntimeError("stub 炸了")

    ts = MCPToolSet(controller=_stub(get_scan_results=_raise),
                    policy=ToolPolicy())
    out = ts.call_tool("get_scan_results", {"handle": "h"})
    assert out["outcome"] == "error"
    assert out["error"] == "internal_error"
    assert out["technical_detail"] == "stub 炸了"


def test_outcome_error_unknown_tool():
    ts = MCPToolSet(controller=_stub(), policy=ToolPolicy())
    out = ts.call_tool("no_such_tool", {})
    assert out["outcome"] == "error"
    assert out["error"] == "unknown_tool"
    assert out["tool"] == "no_such_tool"
    assert "suggestion" in out


# ---------------------------------------------------------------------------
# 阈值可注入（两个方向）
# ---------------------------------------------------------------------------
def test_threshold_injectable_both_directions(tmp_path, monkeypatch):
    _use_tmp_spill(monkeypatch, tmp_path)
    ts = MCPToolSet(controller=_stub(get_frequency=lambda: 98_500_000.0,
                                     get_scan_results=lambda h: _big_scan_results()),
                    policy=ToolPolicy())

    # 阈值压到 10：原本的小结果也 spill
    monkeypatch.setattr(mcp_server, "TOOL_RESULT_SPILL_THRESHOLD", 10)
    small = ts.call_tool("get_frequency", {})
    assert small["truncated"] is True
    assert small["tool"] == "get_frequency"
    assert small["preview_head"].startswith('{"frequency_hz"')
    files_after_small = list((tmp_path / "nested" / "tool_results").glob("*.json"))

    # 阈值拉到极大：大结果原样返回（不 spill，不新增落盘文件）
    monkeypatch.setattr(mcp_server, "TOOL_RESULT_SPILL_THRESHOLD", 10 ** 9)
    back = ts.call_tool("get_scan_results", {"handle": "h"})
    assert back == _big_scan_results()
    assert list((tmp_path / "nested" / "tool_results").glob("*.json")) == files_after_small


# ---------------------------------------------------------------------------
# 辅助函数直测（不可序列化 / extra 可观测字段补位）
# ---------------------------------------------------------------------------
def test_finalize_unserializable_returns_as_is():
    circular: dict = {}
    circular["self"] = circular
    assert mcp_server._finalize_tool_result("t", circular) is circular


def test_finalize_extra_only_fills_spill_envelope(tmp_path, monkeypatch):
    _use_tmp_spill(monkeypatch, tmp_path)
    # 小结果：extra 无处可加（零行为变化优先）
    small = mcp_server._finalize_tool_result("t", {"ok": True},
                                             extra={"approval": "session"})
    assert small == {"ok": True}
    # spill 信封：extra 补位，且不与信封既有键冲突
    out = mcp_server._finalize_tool_result("t", _big_scan_results(),
                                           extra={"approval": "session"})
    assert out["approval"] == "session"
    assert out["truncated"] is True
    out2 = mcp_server._finalize_tool_result("t", _big_scan_results(),
                                            extra={"ok": False})
    assert out2["ok"] is True  # 信封既有键优先，extra 不覆盖
