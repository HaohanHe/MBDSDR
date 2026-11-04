# SPDX-License-Identifier: MIT
"""sdr_transmit_iq 工具面测试：注册 + 人工门 + 无设备诚实失败。

测试进程是全新的、无任何设备连接——所有武装后的调用都走「未连接」
诚实失败路径，绝不真发射。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


def _tok():
    """外部确认通道（测试）：签发一次性 token（S13 后不再有 arm_manual 直通）。"""
    from mbdsdr_ai.tx_guard import get_tx_gate
    return get_tx_gate().arm(ttl_s=10)


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_registered_and_gate_in_schema(reg):
    assert "sdr_transmit_iq" in set(reg.get_tool_names())
    schema = reg.tools["sdr_transmit_iq"]["definition"]["function"]["parameters"]
    assert "arm_manual" not in schema["properties"], \
        "S13：模型可传参数里不得再有 arm_manual"
    assert "confirm_token" in schema["properties"], \
        "任意 IQ 发射是真实射频写操作，契约必须提供外部确认 token 位"


def test_gate_blocks_unarmed(reg):
    # 显式 arm_manual=False：到达 handler 人工门，返回 gated 信封。
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "iq": [0.1, -0.1] * 50,
                                     "arm_manual": False})
    assert r.success is False
    assert (r.data or {}).get("gated") is True, r.content[:120]


def test_missing_arm_manual_rejected_at_schema_level(reg):
    # 连 arm_manual 都不给：registry 层 required 校验先拦。
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "iq": [0.1, -0.1] * 50})
    assert r.success is False
    assert r.content, "缺人工门参数必须有说明"


def test_armed_without_device_fails_honestly(reg):
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "iq": [0.1, -0.1] * 50,
                                     "confirm_token": _tok()})
    assert r.success is False
    assert "未连接" in r.content, r.content[:160]


def test_amplitude_out_of_range_rejected_honestly(reg):
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "iq": [0.1, -0.1] * 50,
                                     "amplitude": 1.5,
                                     "confirm_token": _tok()})
    assert r.success is False
    assert "(0,1]" in r.content, r.content[:160]


def test_odd_element_count_rejected_honestly(reg):
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "iq": [0.1, -0.1, 0.2],   # 3 个 = 不成对
                                     "confirm_token": _tok()})
    assert r.success is False
    assert "成对" in r.content, r.content[:160]


def test_no_iq_input_rejected_honestly(reg):
    r = reg.call("sdr_transmit_iq", {"frequency_hz": 145.8e6,
                                     "confirm_token": _tok()})
    assert r.success is False
    assert "iq" in r.content.lower(), r.content[:160]
