# SPDX-License-Identifier: MIT
"""AR 只读工具注册（Task 15.1）：位姿融合 / 认定方向 / 解算器状态 / 链路健康。

验四件事
--------
1. 四个工具都进 registry，且都是只读（``write=False``）；
2. 缺依赖时如实报不可用，不装可用；
3. 正常输入能产出可核验的结构（融合位姿、真实星表命中、健康级别）；
4. 非法输入/无站址 → 结构化失败，输出不出现「已对准」这类结论。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_tools import register_sky_tools  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402

NOW = 1_700_000_000.0
#: 织女星在 jd=2461361.0416666665 / 45°N,120°E 的地平坐标（本仓星表实算）。
VEGA = {"az_deg": 300.244, "alt_deg": 25.594, "fov_deg": 10.0,
        "jd": 2461361.0416666665, "lat_deg": 45.0, "lon_deg": 120.0}


class _Observer:
    latitude_deg = 45.0
    longitude_deg = 120.0
    height_m = 200.0


class _NoSiteObserver:
    latitude_deg = None
    longitude_deg = None
    height_m = 0.0


class _FakeAgent:
    def __init__(self, observer=None):
        self.tool_registry = ToolRegistry()
        self.observer = observer if observer is not None else _Observer()
        register_sky_tools(self)


@pytest.fixture(scope="module")
def agent():
    return _FakeAgent()


def _call(agent, name, args):
    """调工具；JSON 结果解析成 dict，失败文本原样放在 _raw。"""
    r = agent.tool_registry.call(name, args)
    try:
        data = json.loads(r.content)
        if isinstance(data, dict):
            data["_tool_success"] = r.success
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return {"ok": False, "_raw": r.content, "_tool_success": r.success}


def _obs(source, az, alt, acc=2.0, t=NOW):
    return {"source": source, "t_utc": t, "az_deg": az, "alt_deg": alt,
            "accuracy_deg": acc}


def test_four_readonly_tools_registered(agent):
    expected = {"sky_pose_fuse", "sky_identify_at",
                "sky_solve_status", "sky_health"}
    for name in expected:
        tool = agent.tool_registry.tools[name]
        assert tool["write"] is False, f"{name} 不应是写工具"
        assert tool["category"] in ("sky", "pointing")


def test_solve_status_reports_unavailable(monkeypatch, agent):
    monkeypatch.setattr("mbdsdr_ai.sky_solve.solver_status",
                        lambda astap_path=None: {"available": False,
                                                 "reason": "未安装 tetra3"})
    out = _call(agent, "sky_solve_status", {})
    assert out["_tool_success"] is True
    assert out["available"] is False
    assert out["name"] == ""
    assert "tetra3" in out["reason"]


def test_pose_fuse_two_sources_valid(agent):
    out = _call(agent, "sky_pose_fuse", {
        "observations": [_obs("imu", 100.0, 45.0, acc=1.0),
                         _obs("camera", 101.0, 44.5, acc=2.0)],
        "now_utc": NOW + 1.0,
    })
    assert out["_tool_success"] is True
    assert out["valid"] is True
    assert out["level"] == "fused"
    assert set(out["sources"]) == {"imu", "camera"}
    assert 100.0 < out["az_deg"] < 101.0
    assert "度" in out["units"]


def test_pose_fuse_rejects_malformed_observations(agent):
    out = _call(agent, "sky_pose_fuse", {
        "observations": [{"source": "imu"}],
        "now_utc": NOW,
    })
    assert out["_tool_success"] is False
    assert out["_raw"].startswith("bad_observations（")


def test_identify_at_matches_real_catalog(agent):
    out = _call(agent, "sky_identify_at", VEGA)
    assert out["_tool_success"] is True
    assert out["matched"] is True
    assert out["stars"], "视场内应有星表恒星"
    assert out["stars"][0]["name"] == "Vega"
    assert out["stars"][0]["angular_distance_deg"] < 0.5


def test_identify_at_without_site_fails():
    agent = _FakeAgent(observer=_NoSiteObserver())
    args = {k: v for k, v in VEGA.items() if k not in ("lat_deg", "lon_deg")}
    out = _call(agent, "sky_identify_at", args)
    assert out["_tool_success"] is False
    assert out["_raw"].startswith("no_site（")


def test_health_reports_level_and_reason(agent):
    out = _call(agent, "sky_health", {
        "observations": [_obs("imu", 100.0, 45.0),
                         _obs("camera", 101.0, 44.5)],
        "now_utc": NOW + 1.0,
    })
    assert out["_tool_success"] is True
    assert out["level"] == "L2"
    assert "多源融合" in out["reason"]


def test_health_degrades_when_camera_has_no_frame(monkeypatch, agent):
    monkeypatch.setattr("mbdsdr_ai.sky_solve.solver_status",
                        lambda astap_path=None: {"available": True,
                                                 "name": "tetra3",
                                                 "solved": True})
    out = _call(agent, "sky_health", {
        "observations": [_obs("imu", 100.0, 45.0),
                         _obs("camera", 101.0, 44.5)],
        "now_utc": NOW,
        "camera_last_frame_utc": NOW - 10.0,
    })
    assert out["_tool_success"] is True
    assert out["camera_no_frame"] is True
    assert out["level"] == "L2", "相机断帧时不得继续报 L3"
    assert "无帧" in out["reason"]


def test_outputs_never_claim_alignment(agent):
    calls = {
        "sky_pose_fuse": {
            "observations": [_obs("imu", 100.0, 45.0)],
            "now_utc": NOW + 1.0},
        "sky_identify_at": VEGA,
        "sky_solve_status": {},
        "sky_health": {
            "observations": [_obs("imu", 100.0, 45.0)],
            "now_utc": NOW + 1.0},
    }
    for name, args in calls.items():
        result = agent.tool_registry.call(name, args)
        assert "已对准" not in result.content, f"{name} 输出出现了对准结论"
