# -*- coding: utf-8 -*-
"""P13 工具结果契约回归锁。

把三条纪律钉成测试：
  1. 不许静默返回空：success=True 但 content/data 全空 → 注册表强制降级为失败，
     并给出 reason_code=empty_result_without_reason。
  2. 不许吞异常：handler 抛异常 → 调用失败 + error 以 exception: 开头，绝不装成功。
  3. 「已执行但没结果」必须说原因：解码器空结果要带诚实空态原因；
     未连接设备时查询/切换要如实失败，不许虚构频率或假成功。

离线、无硬件、静音、offscreen；不联网。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402


def _register(reg, name, handler, properties=None, required=None):
    reg.register(
        name=name,
        description="契约测试工具",
        parameters={"type": "object", "properties": properties or {},
                    **({"required": required} if required is not None else {})},
        handler=handler,
        category="test",
    )


# ── 1. 静默空结果守卫 ──────────────────────────────────────────
def test_success_with_empty_content_is_demoted():
    reg = ToolRegistry()
    _register(reg, "silent_empty", lambda a: ToolResult(success=True, content=""))
    r = reg.call("silent_empty", {})
    assert r.success is False
    assert r.error == "empty_result_without_reason"
    assert "empty_result_without_reason" in r.content
    assert r.content.strip()


def test_success_with_empty_whitespace_is_demoted():
    reg = ToolRegistry()
    _register(reg, "silent_blank", lambda a: ToolResult(success=True, content="  \n\t"))
    r = reg.call("silent_blank", {})
    assert r.success is False and r.error == "empty_result_without_reason"


def test_success_with_data_but_empty_content_is_allowed():
    """有结构化 data 时 content 可以为空（载荷已在 data 里，不算静默空）。"""
    reg = ToolRegistry()
    _register(reg, "data_only", lambda a: ToolResult(success=True, content="",
                                                     data={"value": 42}))
    r = reg.call("data_only", {})
    assert r.success is True
    assert r.data == {"value": 42}


# ── 2. 不许吞异常 ─────────────────────────────────────────────
def test_handler_exception_is_failure_not_success():
    def boom(_args):
        raise ValueError("deliberate boom")

    reg = ToolRegistry()
    _register(reg, "boom", boom)
    r = reg.call("boom", {})
    assert r.success is False
    assert r.error.startswith("exception:")
    assert "ValueError" in r.content
    assert "空结果" not in r.content  # 异常不能被伪装成空结果


def test_error_prefixed_string_is_failure():
    reg = ToolRegistry()
    _register(reg, "err_prefix", lambda a: "错误：样本不足")
    r = reg.call("err_prefix", {})
    assert r.success is False
    assert r.error == "handler_error"
    assert "样本不足" in r.content


# ── 3. 真实注册表的诚实语义 ───────────────────────────────────
@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    # 审计期不枚举/不打开任何真实设备：空枚举即"无硬件"替身
    import mbdsdr_ai.sdr_backend as _sb
    orig_discover = _sb.SDRBackendManager._discover
    _sb.SDRBackendManager._discover = lambda self: None
    try:
        ag = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original
        _sb.SDRBackendManager._discover = orig_discover
    ag.sdr_manager.backends.clear()
    ag.sdr_manager.active_backend = None
    return ag


def test_unconnected_frequency_query_fails_honestly(agent):
    r = agent.tool_registry.call("sdr_get_frequency", {})
    assert r.success is False
    assert r.error == "not_connected"
    assert "未连接" in r.content
    assert "当前频率:" not in r.content  # 绝不虚构频率


def test_switch_to_unknown_device_fails_honestly(agent):
    r = agent.tool_registry.call("sdr_switch_device", {"device_id": "no_such_device"})
    assert r.success is False
    assert r.error == "device_unavailable"
    assert "切换失败" in r.content


def test_scheduler_enable_missing_task_is_failure(agent):
    r = agent.tool_registry.call("scheduler_enable", {"task_id": "no_such_task"})
    assert r.success is False
    assert r.error == "task_not_found"


def test_evolution_rollback_without_parent_is_failure(agent):
    r = agent.tool_registry.call("evolution_rollback", {})
    assert r.success is False
    assert r.error == "rollback_unavailable"


def test_acars_empty_result_has_reason(agent):
    import math
    audio = [0.3 * math.sin(2 * math.pi * 1200 * i / 12500.0) for i in range(6000)]
    r = agent.tool_registry.call("acars_decode_iq", {"audio": audio,
                                                    "sample_rate": 12500})
    assert r.content.strip() != "[]"
    assert "未检测到" in r.content
    assert "诚实空态" in r.content


def test_sdr_list_devices_never_fabricates(agent):
    r = agent.tool_registry.call("sdr_list_devices", {})
    devices = json.loads(r.content)
    assert devices == []  # MBDSDR_NO_HW 空枚举下不得虚构设备
