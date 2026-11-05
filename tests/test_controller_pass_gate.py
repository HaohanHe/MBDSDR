# SPDX-License-Identifier: MIT
"""控制器过境工具（MCP 正规面）的门/窗口必须由上层注入，不许写死 10°/24h。

修前红：
- `mbdsdr_ai/core/controller.py:get_satellite_pass` 写死
  `hours=24.0, min_alt=PASS_MIN_ALT_DEG`；
- `mbdsdr_ai/core/mcp_server.py` 的 `get_satellite_pass` schema 只有 `norad`。
于是同一工具调用注入 5° 与 10°，返回条数完全相同，结果里也没有可计数的
`passes_in_window`。

固定用例（离线、不读用户配置）：
- TLE：ISS，epoch 2008-264（确定性）
- 站点：39.9°N / 116.4°E / 0 km
- 基准时刻：2008-09-20 06:00:00 UTC
- 实测：24h 内 5° 门 **6 条**、10° 门 **5 条**；10° 门 72h 内 **14 条**
"""
import os
import sys
import types
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
FIXED_UNIX = FIXED_UTC.timestamp()


def _fixed_controller(monkeypatch, tmp_path):
    """离线控制器：内置 TLE + 固定 TimeEngine + 可信站址。"""
    from mbdsdr_ai.core.controller import SDRController
    import mbdsdr_ai.decoders as decoders
    import mbdsdr_ai.new_spacetime as nst
    import mbdsdr_ai.orbit as orbit

    monkeypatch.setitem(decoders.BUILTIN_TLE, ISS_NAME, (ISS_L1, ISS_L2))

    def _offline(*_a, **_k):
        raise RuntimeError("offline（测试固定用内置 TLE）")

    monkeypatch.setattr(orbit, "fetch_tle_record", _offline)
    monkeypatch.setattr(
        nst, "get_time_engine",
        lambda: types.SimpleNamespace(now_unix=lambda: FIXED_UNIX))

    ctrl = SDRController(backend=None, config_dir=str(tmp_path))
    ctrl.set_ground_station(39.9, 116.4, 0.0, source="user")
    return ctrl


def test_mcp_tool_gate_5_vs_10_counts_differ(monkeypatch, tmp_path):
    """同一个 get_satellite_pass 调用，5° 与 10° 返回的过境条数必须不同。"""
    from mbdsdr_ai.core.mcp_server import MCPToolSet

    ts = MCPToolSet(_fixed_controller(monkeypatch, tmp_path))
    at5 = ts.call_tool("get_satellite_pass",
                       {"norad": 25544, "min_elevation_deg": 5.0,
                        "window_hours": 24})
    at10 = ts.call_tool("get_satellite_pass",
                        {"norad": 25544, "min_elevation_deg": 10.0,
                         "window_hours": 24})

    for out in (at5, at10):
        assert "error" not in out, out
        assert out.get("min_elevation_deg") in (5.0, 10.0), out
        assert out.get("window_hours") == 24.0, out

    n5, n10 = at5["passes_in_window"], at10["passes_in_window"]
    assert n5 != n10, (
        f"设 5° 与设 10° 返回了同样 {n5} 条：控制器仍在用写死的 "
        f"PASS_MIN_ALT_DEG，没有消费注入的峰值门")
    assert (n5, n10) == (6, 5), (
        f"实测应为 5°=6 条、10°=5 条，实际 5°={n5}、10°={n10}")


def test_mcp_tool_window_24h_vs_72h_counts_differ(monkeypatch, tmp_path):
    """同一工具调用，窗口 24h 与 72h 的过境条数必须不同（时间窗不许写死）。"""
    from mbdsdr_ai.core.mcp_server import MCPToolSet

    ts = MCPToolSet(_fixed_controller(monkeypatch, tmp_path))
    at24 = ts.call_tool("get_satellite_pass",
                        {"norad": 25544, "min_elevation_deg": 10.0,
                         "window_hours": 24})
    at72 = ts.call_tool("get_satellite_pass",
                        {"norad": 25544, "min_elevation_deg": 10.0,
                         "window_hours": 72})

    n24, n72 = at24["passes_in_window"], at72["passes_in_window"]
    assert n24 != n72, (
        f"窗口 24h 与 72h 返回了同样 {n24} 条：控制器仍在用写死的 hours=24.0")
    assert (n24, n72) == (5, 14), (
        f"实测应为 24h=5 条、72h=14 条，实际 24h={n24}、72h={n72}")


def test_controller_defaults_come_from_sat_passes(monkeypatch, tmp_path):
    """不注入时回落 sat_passes 单一事实源，而不是控制器里的第二份默认值。"""
    from mbdsdr_ai import sat_passes

    ctrl = _fixed_controller(monkeypatch, tmp_path)
    out = ctrl.get_satellite_pass(25544)
    assert out["min_elevation_deg"] == pytest.approx(sat_passes.PASS_MIN_ALT_DEG)
    assert out["window_hours"] == pytest.approx(sat_passes.PASS_WINDOW_HOURS)
