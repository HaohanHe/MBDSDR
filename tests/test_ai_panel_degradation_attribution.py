# SPDX-License-Identifier: MIT
"""AI 面板两处降级的归因：配置兜底读取 + 未执行硬件调用的参数闸门。

两件事分开：

1. ``_load_config`` 的兜底路径（AI 内核不可用时的直读 JSON）读坏了仍返回
   空配置，但必须留下原因；
2. 模型请求、工具环未执行的 MCP 硬件调用要转发给桌面执行。参数解析失败
   **不得**拿空参数下发——那是「看起来执行了」的假动作；跳过时还必须让
   用户看见「这次没执行」，而不是一张永远停在「运行中」的步骤卡。
"""
import json
import logging
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

ai_panel = pytest.importorskip("ai_panel")


# ---------------------------------------------------------------- 参数闸门

@pytest.mark.parametrize("raw,expect", [
    ({"freq_mhz": 98.5}, {"freq_mhz": 98.5}),
    ('{"freq_mhz": 98.5}', {"freq_mhz": 98.5}),
    (None, {}),          # 没给参数：可下发空 dict
    ("   ", {}),         # 空白串：同上，不是坏参数
])
def test_forwardable_args_passes_valid(raw, expect):
    args, why = ai_panel._forwardable_args(raw)
    assert args == expect
    assert why == ""


@pytest.mark.parametrize("raw,needle", [
    ("{broken", "JSONDecodeError"),
    ('["not", "an", "object"]', "list"),     # 解析成功但不是对象：也不猜
    (123, "int"),                            # 类型不支持
])
def test_forwardable_args_rejects_bad(raw, needle):
    args, why = ai_panel._forwardable_args(raw)
    assert args is None, "坏参数不得被归一成空 dict 下发"
    assert needle in why


class _FakeSignal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def _stub():
    notes = []
    return SimpleNamespace(
        _mcp_tool_names={"tune_fm", "ptt_set"},
        tool_call_requested=_FakeSignal(),
        _add_system_message=notes.append,
        notes=notes,
    )


def test_bad_args_are_not_forwarded_and_user_is_told(caplog):
    stub = _stub()
    calls = [{"name": "tune_fm", "arguments": "{broken"}]

    with caplog.at_level(logging.WARNING, logger="ai_panel"):
        ai_panel.AIPanel._forward_unexecuted_mcp_calls(stub, calls, 0)

    assert stub.tool_call_requested.calls == [], "参数坏了仍向硬件转发了"
    assert len(stub.notes) == 1
    assert "未执行工具 tune_fm" in stub.notes[0]
    assert "未向设备下发任何指令" in stub.notes[0]
    assert any("未执行工具 tune_fm" in r.getMessage() for r in caplog.records)


def test_valid_string_args_are_forwarded_as_dict():
    stub = _stub()
    calls = [{"name": "tune_fm", "arguments": '{"freq_mhz": 98.5}'}]

    ai_panel.AIPanel._forward_unexecuted_mcp_calls(stub, calls, 0)

    assert stub.tool_call_requested.calls == [("tune_fm", {"freq_mhz": 98.5})]
    assert stub.notes == []


def test_executed_and_software_calls_are_skipped():
    """已执行的调用（工具环内落到硬件）与软件工具都不进二次转发。"""
    stub = _stub()
    calls = [
        {"name": "tune_fm", "arguments": {"freq_mhz": 98.5}},   # 已执行
        {"name": "read_file", "arguments": {"path": "x"}},      # 非 MCP
    ]

    ai_panel.AIPanel._forward_unexecuted_mcp_calls(stub, calls, 1)

    assert stub.tool_call_requested.calls == []
    assert stub.notes == []


# ------------------------------------------------------------ 配置兜底读取

def test_fallback_config_read_failure_is_attributable(monkeypatch, tmp_path, caplog):
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ai_panel, "ConfigStore", None)
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_FILE", str(cfg), raising=False)

    with caplog.at_level(logging.WARNING, logger="ai_panel"):
        assert ai_panel.AIPanel._load_config() == {}

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "兜底读配置失败" in msg
    assert "JSONDecodeError" in msg
    assert str(cfg) in msg


def test_fallback_missing_config_is_normal(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ai_panel, "ConfigStore", None)
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_FILE",
                        str(tmp_path / "nope.json"), raising=False)

    with caplog.at_level(logging.WARNING, logger="ai_panel"):
        assert ai_panel.AIPanel._load_config() == {}

    assert caplog.records == []


def test_fallback_valid_config_still_reads(monkeypatch, tmp_path, caplog):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"api_key": "sk-x"}), encoding="utf-8")
    monkeypatch.setattr(ai_panel, "ConfigStore", None)
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_FILE", str(cfg), raising=False)

    with caplog.at_level(logging.WARNING, logger="ai_panel"):
        assert ai_panel.AIPanel._load_config() == {"api_key": "sk-x"}

    assert caplog.records == []


def test_configstore_read_failure_is_attributable(monkeypatch, tmp_path, caplog):
    """ConfigStore 读盘失败同样不得与「没配过」同貌：路径与异常类型要可查。"""
    cfg = str(tmp_path / "config.json")

    class _BoomStore:
        def __init__(self, path):
            self.path = path

        def read(self):
            raise OSError("config locked by another process")

    monkeypatch.setattr(ai_panel, "ConfigStore", _BoomStore)
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_FILE", cfg, raising=False)

    with caplog.at_level(logging.WARNING, logger="ai_panel"):
        assert ai_panel.AIPanel._load_config() == {}

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "读取配置失败" in msg
    assert "OSError" in msg
    assert cfg in msg
