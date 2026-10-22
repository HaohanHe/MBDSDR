# SPDX-License-Identifier: MIT
"""desktop/mcp_protocol.py 纯逻辑行为测试（Qt 无关）。

覆盖「桌面 AI → 硬件工具」编排的三块策略：
1. 端点解析：显式参数 > 环境变量 > tokens.MCP 默认值（默认值唯一事实源）；
2. 固件 list_tools → MCP schema 归一（不臆造名字/参数）；
3. 转发策略：只转发硬件面（注册名 ∪ 可配置别名表），软件工具返回 None
   不二次转发；别名表可整体替换（可配置而非内联写死）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import mcp_protocol
from tokens import tokens


class TestResolveEndpoint:
    def test_defaults_come_from_tokens(self, monkeypatch):
        monkeypatch.delenv(mcp_protocol.ENV_MCP_HOST, raising=False)
        monkeypatch.delenv(mcp_protocol.ENV_MCP_PORT, raising=False)
        host, port = mcp_protocol.resolve_mcp_endpoint()
        assert host == tokens().MCP["default_host"] == "192.168.4.1"
        assert port == tokens().MCP["default_port"] == 81

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv(mcp_protocol.ENV_MCP_HOST, "10.0.0.5")
        monkeypatch.setenv(mcp_protocol.ENV_MCP_PORT, "82")
        assert mcp_protocol.resolve_mcp_endpoint() == ("10.0.0.5", 82)

    def test_explicit_args_win_over_env(self, monkeypatch):
        monkeypatch.setenv(mcp_protocol.ENV_MCP_HOST, "10.0.0.5")
        monkeypatch.setenv(mcp_protocol.ENV_MCP_PORT, "82")
        assert mcp_protocol.resolve_mcp_endpoint("172.16.0.2", 9000) == \
            ("172.16.0.2", 9000)

    def test_illegal_env_port_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv(mcp_protocol.ENV_MCP_HOST, raising=False)
        monkeypatch.setenv(mcp_protocol.ENV_MCP_PORT, "不是数字")
        assert mcp_protocol.resolve_mcp_endpoint()[1] == tokens().MCP["default_port"]

    def test_out_of_range_port_falls_back(self, monkeypatch):
        monkeypatch.delenv(mcp_protocol.ENV_MCP_PORT, raising=False)
        assert mcp_protocol.resolve_mcp_endpoint(None, 70000)[1] == \
            tokens().MCP["default_port"]


class TestFirmwareSchema:
    def test_firmware_shape_becomes_mcp_input_schema(self):
        tools = [{"name": "tune_fm", "desc": "调谐 FM",
                  "params": {"freq_mhz": "频率 MHz (64-108)"}}]
        out = mcp_protocol.firmware_tools_to_mcp_schema(tools)
        assert len(out) == 1
        t = out[0]
        assert t["name"] == "tune_fm"
        assert t["description"] == "调谐 FM"
        schema = t["inputSchema"]
        assert schema["type"] == "object"
        assert schema["properties"]["freq_mhz"]["type"] == "number"
        assert schema["required"] == ["freq_mhz"]

    def test_non_numeric_param_is_string(self):
        tools = [{"name": "set_mode", "desc": "", "params": {"mode": "解调模式"}}]
        out = mcp_protocol.firmware_tools_to_mcp_schema(tools)
        assert out[0]["inputSchema"]["properties"]["mode"]["type"] == "string"

    def test_nameless_entries_dropped_not_invented(self):
        tools = [{"desc": "无名条目"}, {"name": "get_status", "desc": "状态"}]
        out = mcp_protocol.firmware_tools_to_mcp_schema(tools)
        assert [t["name"] for t in out] == ["get_status"]

    def test_mcp_shaped_entries_pass_through(self):
        tools = [{"name": "x", "description": "d",
                  "inputSchema": {"type": "object", "properties": {"a": {}}}}]
        out = mcp_protocol.firmware_tools_to_mcp_schema(tools)
        assert out[0]["inputSchema"]["properties"] == {"a": {}}


class TestForwardPolicy:
    def test_registered_name_forwards_unchanged(self):
        plan = mcp_protocol.normalize_tool_call(
            "sdr_set_gain", {"gain_db": 20}, registered_names={"sdr_set_gain"})
        assert plan == {"method": "sdr_set_gain", "params": {"gain_db": 20}}

    def test_software_tool_is_not_forwarded(self):
        """软件工具不过二次转发：返回 None，不产生「未知工具」失败噪声。"""
        assert mcp_protocol.normalize_tool_call(
            "workflow_list", {}, registered_names=set()) is None
        assert mcp_protocol.normalize_tool_call(
            "context_query", {}, registered_names={"tune_fm"}) is None

    def test_empty_name_not_forwarded(self):
        assert mcp_protocol.normalize_tool_call("", {}, ()) is None

    def test_legacy_alias_maps_method(self):
        plan = mcp_protocol.normalize_tool_call(
            "tune_fm", {"freq_mhz": 98.5}, registered_names=set())
        assert plan == {"method": "tune_fm", "params": {"freq_mhz": 98.5}}

    def test_alias_params_map_renames_keys(self):
        aliases = {"legacy_tune": {"method": "tune_fm",
                                   "params_map": {"freq": "freq_mhz"}}}
        plan = mcp_protocol.normalize_tool_call(
            "legacy_tune", {"freq": 98.5}, registered_names=set(), aliases=aliases)
        assert plan == {"method": "tune_fm", "params": {"freq_mhz": 98.5}}

    def test_alias_table_is_configurable(self):
        """映射表可整体替换（不再内联写死）：换表后旧名即不再转发。"""
        assert mcp_protocol.normalize_tool_call(
            "tune_fm", {}, registered_names=set(), aliases={}) is None

    def test_default_alias_table_registers_legacy_ui_names(self):
        for name in ("tune_fm", "tune_am", "set_volume", "start_record",
                     "stop_record", "get_version", "reboot", "list_tools",
                     "tune_sdr", "sdr_set_frequency"):
            assert name in mcp_protocol.MCP_TOOL_ALIASES, name

    def test_gate_token_never_forwarded_to_device(self):
        plan = mcp_protocol.normalize_tool_call(
            "tune_fm", {"freq_mhz": 98.5, "arm_manual": True},
            registered_names=set())
        assert plan == {"method": "tune_fm", "params": {"freq_mhz": 98.5}}


class TestManualGate:
    """AGENTS 红线：模型驱动的硬件写操作必走 arm_manual 人工门。"""

    def test_write_tools_detected_conservatively(self):
        for name in ("tune_fm", "set_volume", "start_record", "stop_record",
                     "reboot", "trigger_ota", "sdr_set_frequency"):
            assert mcp_protocol.is_write_tool(name), name

    def test_read_only_tools_not_gated(self):
        for name in ("get_status", "get_gps", "get_imu", "get_version",
                     "check_update", "list_tools", "web_ota_url", ""):
            assert not mcp_protocol.is_write_tool(name), name

    def test_annotate_adds_gate_to_write_tools_only(self):
        tools = [
            {"name": "tune_fm", "description": "调谐 FM",
             "inputSchema": {"type": "object",
                             "properties": {"freq_mhz": {"type": "number"}},
                             "required": ["freq_mhz"]}},
            {"name": "get_status", "description": "状态",
             "inputSchema": {"type": "object", "properties": {}}},
        ]
        out = mcp_protocol.annotate_write_gate(tools)
        write_t, read_t = out[0], out[1]
        assert "arm_manual" in write_t["inputSchema"]["properties"]
        assert "arm_manual" in write_t["inputSchema"]["required"]
        assert "手动门" in write_t["description"]
        assert "arm_manual" not in read_t["inputSchema"]["properties"]
        assert read_t["description"] == "状态"
