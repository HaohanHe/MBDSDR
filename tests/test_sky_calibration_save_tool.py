# SPDX-License-Identifier: MIT
"""AR 校准写工具（Task 15.2）：sky_calibration_save 必须走框架确认门。

验五件事
--------
1. 注册为写工具、分级 ``confirm``（不暴露给模型自签）；
2. 模型通道无 token → ``manual_gate_required`` 且 handler 一次都不执行
   （目标文件不存在）；
3. 外部确认通道签发一次性 token 后 → 真写盘，``load_profile`` 能读回；
4. 残差超阈值 → 拒绝写入，文件不存在；
5. 观测/路径非法 → 结构化失败，不抛异常。
"""

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_calibration import load_profile  # noqa: E402
from mbdsdr_ai.sky_tools import register_sky_tools  # noqa: E402
from mbdsdr_ai.tool_registry import (  # noqa: E402
    ToolRegistry,
    write_tool_grade,
    write_tool_grade_reason,
)

NAME = "sky_calibration_save"

#: 两个整体偏 1° 的合成校准点（与 tests/test_sky_calibration.py 同一口径）。
OBSERVATIONS = [
    {"known_az_deg": 100.0, "known_alt_deg": 40.0,
     "read_az_deg": 99.0, "read_alt_deg": 39.0},
    {"known_az_deg": 130.0, "known_alt_deg": 50.0,
     "read_az_deg": 129.0, "read_alt_deg": 49.0},
]

#: 两方向严重不一致 → 最大残差约 12.65°，远超默认 0.5° 阈值。
INCONSISTENT = [
    {"known_az_deg": 100.0, "known_alt_deg": 40.0,
     "read_az_deg": 100.0, "read_alt_deg": 40.0},
    {"known_az_deg": 130.0, "known_alt_deg": 50.0,
     "read_az_deg": 115.0, "read_alt_deg": 35.0},
]


class _FakeAgent:
    def __init__(self):
        self.tool_registry = ToolRegistry()
        register_sky_tools(self)


@pytest.fixture()
def agent():
    return _FakeAgent()


@pytest.fixture(autouse=True)
def _clean_gate(tmp_path, monkeypatch):
    from mbdsdr_ai.tx_guard import reset_tx_audit, reset_tx_gate

    monkeypatch.setenv("MBDSDR_TX_AUDIT_PATH",
                       str(tmp_path / "tx_audit.jsonl"))
    reset_tx_audit()
    reset_tx_gate()
    yield
    reset_tx_gate()


def _args(path, observations=None):
    return {
        "observations": (OBSERVATIONS if observations is None
                         else observations),
        "device_id": "示例设备",
        "fov_deg": 8.0,
        "path": str(path),
    }


def _call_model(agent, args):
    return agent.tool_registry.call_from_model({
        "id": "call_1",
        "type": "function",
        "function": {"name": NAME, "arguments": json.dumps(args)},
    })


def test_registered_as_confirm_write_tool(agent):
    tool = agent.tool_registry.tools[NAME]
    assert tool["write"] is True
    assert tool["category"] == "sky"
    assert agent.tool_registry.is_write_tool(NAME) is True
    assert write_tool_grade(NAME) == "confirm"
    assert write_tool_grade_reason(NAME).strip()


def test_model_channel_without_token_is_gated_and_writes_nothing(
        agent, tmp_path):
    target = tmp_path / "calibration.json"

    result = _call_model(agent, _args(target))

    assert result.success is False
    assert result.error == "manual_gate_required"
    assert result.data.get("gated") is True
    assert result.data.get("grade") == "confirm"
    assert not target.exists(), "无授权时 handler 不得执行，文件不能出现"


def test_model_channel_with_one_shot_token_really_writes(agent, tmp_path):
    from mbdsdr_ai.tx_guard import issue_confirmation

    target = tmp_path / "calibration.json"
    args = _args(target)
    token = issue_confirmation(NAME, args, ttl_s=30, source="test")

    result = _call_model(agent, {**args, "confirm_token": token})

    assert result.success is True, result.content
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert os.path.abspath(str(target)) == payload["path"]
    assert math.isfinite(float(payload["residual_deg"]))
    assert payload["profile"]["device_id"] == "示例设备"
    assert payload["profile"]["fov_deg"] == pytest.approx(8.0)
    assert payload["profile"]["source"] == "two_star"
    # 内部修正量刻意不暴露给模型。
    assert "boresight_az_deg" not in payload["profile"]
    assert "roll_zero_deg" not in payload["profile"]

    loaded = load_profile(str(target))
    assert loaded["ok"] is True, loaded.get("reason")
    assert loaded["profile"]["device_id"] == "示例设备"
    assert math.isfinite(loaded["profile"]["boresight_az_deg"])


def test_residual_over_threshold_is_rejected_without_file(agent, tmp_path):
    from mbdsdr_ai.tx_guard import issue_confirmation

    target = tmp_path / "bad.json"
    args = _args(target, INCONSISTENT)
    token = issue_confirmation(NAME, args, ttl_s=30, source="test")

    result = _call_model(agent, {**args, "confirm_token": token})

    assert result.success is False
    assert result.error == "residual_too_large"
    assert not target.exists()
    payload = json.loads(result.content)
    assert payload["ok"] is False
    assert payload["reason"]
    assert payload["next_step"]
    assert "12" in payload["reason"]


def test_bad_observation_and_path_fail_structured(agent, tmp_path):
    cases = [
        {"observations": [{"known_az_deg": 1.0}], "device_id": "示例设备",
         "fov_deg": 8.0, "path": str(tmp_path / "a.json")},
        _args(tmp_path / "b.json", observations=[]),
        {"observations": OBSERVATIONS, "device_id": "",
         "fov_deg": 8.0, "path": str(tmp_path / "c.json")},
        {"observations": OBSERVATIONS, "device_id": "示例设备",
         "fov_deg": 0.0, "path": str(tmp_path / "d.json")},
        {"observations": OBSERVATIONS, "device_id": "示例设备",
         "fov_deg": 8.0, "path": ""},
    ]
    for args in cases:
        result = agent.tool_registry.call(NAME, args)
        assert result.success is False
        payload = json.loads(result.content)
        assert payload["ok"] is False
        assert payload["error"]
        assert payload["reason"]
        assert payload["next_step"]
    for name in ("a.json", "b.json", "c.json", "d.json"):
        assert not (tmp_path / name).exists(), "失败时不得留下档案文件"
