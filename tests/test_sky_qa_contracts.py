# SPDX-License-Identifier: MIT
"""AI 问答与能力探测口径验收（Task 14 / FR-08 / FR-09）。

锁住三件事，全部离线、不碰设备：

1. **光行时口径**：pc → ly 只用一个常量（``PC_TO_LIGHT_YEAR``）；
   ``light_time_text`` 用「光走了 N 年」，与 ``dist_pc`` 同源，不另算一套。
2. **距离缺失**：``dist_pc`` / ``light_years`` 为 null，文案必须说
   「距离未知」；输出里不得出现 ``0 光年`` / ``0 ly`` 这类编造值。
   DSO 目录已有部分距离；缺失时必须明说「未知」，不许补一个数。
3. **能力探测**：缺设备时如实 missing；探测抛异常时保持 unknown，
   不得报 available；探测失败要有中文原因；状态与报告两个工具读同一账本，
   不能一个说可用、一个说不可用。

运行（定向）::

    python -m pytest tests/test_sky_qa_contracts.py -q
"""

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.sky_tools import (  # noqa: E402
    PC_TO_LIGHT_YEAR,
    _fmt_ly,
    _light_years,
    register_sky_tools,
)
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402

#: 固定时刻（UT1 儒略日）。与 test_sky_tools_register 的 2026-11 口径一致，
#: 保证离线、可复现，不依赖当前系统时间。
FIXED_JD = 2461365.0416666665

#: 编造距离的几种常见写法；任何工具输出里都不许出现。
FAKE_ZERO_DISTANCE = ("0 光年", "0.0 光年", "0 ly", "0.0 ly")


class _Observer:
    latitude_deg = 45.0
    longitude_deg = 120.0
    height_m = 200.0


class _FakeAgent:
    """只注册 sky_tools 的假 agent（不碰 MBDSDRAgent 的重依赖）。"""

    def __init__(self):
        self.tool_registry = ToolRegistry()
        self.observer = _Observer()
        register_sky_tools(self)


def _call(agent, name, args):
    """调工具并把 JSON 结果解析成 dict；失败文本原样放在 _raw。"""
    r = agent.tool_registry.call(name, args)
    try:
        data = json.loads(r.content)
        if isinstance(data, dict):
            data["_tool_success"] = r.success
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return {"ok": False, "_raw": r.content, "_tool_success": r.success}


def _catalog_row(hip):
    """按 HIP 取星表行号；找不到返回 None。"""
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    catalog = get_catalog()
    for i, h in enumerate(catalog.hip):
        if int(h) == int(hip):
            return catalog, i
    return catalog, None


def _altaz_for_hip(hip, jd=FIXED_JD):
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    catalog = get_catalog()
    index = None
    for i, h in enumerate(catalog.hip):
        if int(h) == int(hip):
            index = i
            break
    assert index is not None, f"星表里没有 HIP {hip}"
    station = GroundStation(120.0, 45.0, 200.0, "qa_contract")
    vec = j2000_to_altaz(
        vec_from_radec(float(catalog.ra_deg[index]),
                       float(catalog.dec_deg[index])),
        station, float(jd))
    az, alt = azalt_from_vec(vec)
    return float(az), float(alt)


@pytest.fixture(scope="module")
def sky_agent():
    return _FakeAgent()


@pytest.fixture(scope="module")
def cas_view(sky_agent):
    """指向王良二（HIP 2599，星表无有效视差）的 2.3° 目镜视场。"""
    az, alt = _altaz_for_hip(2599)
    return _call(sky_agent, "sky_whats_here", {
        "az_deg": az, "alt_deg": alt, "fov_deg": 2.3, "jd_ut1": FIXED_JD})


@pytest.fixture(scope="module")
def capability_agent():
    """只带能力协商所需属性的假 agent，绑定 MBDSDRAgent 的真实方法。

    这样测的仍是生产线上的注册与探测逻辑，但不构造完整 agent——避免启动期
    扫 SDR，做到离线、无设备。每个用例另重置账本与探测缓存。
    """
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.sky_actions import _reset_sky_bus_for_tests

    class _ContextManager:
        capability_ledger = None

    class _Agent:
        probe_hardware_capabilities = \
            MBDSDRAgent.probe_hardware_capabilities
        _register_sky_negotiation_tools = \
            MBDSDRAgent._register_sky_negotiation_tools

        def __init__(self):
            self.tool_registry = ToolRegistry()
            self.context_manager = _ContextManager()
            self.sky_ledger = None

    _reset_sky_bus_for_tests()
    agent = _Agent()
    agent._register_sky_negotiation_tools()
    yield agent
    _reset_sky_bus_for_tests()


@pytest.fixture
def cap(capability_agent):
    from mbdsdr_ai.sky_negotiation import CapabilityLedger

    agent = capability_agent
    agent.sky_ledger = CapabilityLedger()
    agent.context_manager.capability_ledger = agent.sky_ledger
    agent._capability_probe_at = 0.0
    agent._capability_probe_result = None
    return agent


# ---------------------------------------------------------------------------
# 1. 光行时：单一常量、缺失即 None
# ---------------------------------------------------------------------------
class TestLightTimeContract:
    def test_conversion_has_a_single_constant(self):
        # 3.261563777 是实现里的唯一定义；测试不另造一套常量。
        assert PC_TO_LIGHT_YEAR == pytest.approx(3.261563777)
        assert _light_years(10.0) == pytest.approx(10.0 * PC_TO_LIGHT_YEAR)
        assert _light_years(None) is None
        assert _light_years(0.0) is None
        assert _light_years(float("nan")) is None

    def test_text_is_years_and_missing_says_unknown(self):
        assert _fmt_ly(None) == "距离未知"
        assert _fmt_ly(_light_years(10.0)) == "光走了 32.6 年"
        assert _fmt_ly(_light_years(100.0)) == "光走了 326 年"

    def test_object_lookup_light_time_matches_distance(self, sky_agent):
        data = _call(sky_agent, "sky_object_lookup", {"query": "Vega"})
        assert data["_tool_success"] is True and data["ok"] is True
        assert data["dist_pc"] > 0.0
        # 光行时与距离同源：light_years == dist_pc × 同一个常量
        assert data["light_years"] == pytest.approx(
            data["dist_pc"] * PC_TO_LIGHT_YEAR, rel=1e-9)
        assert data["light_time_text"].startswith("光走了 ")
        assert data["light_time_text"].endswith(" 年")
        assert "秒" not in data["light_time_text"]


# ---------------------------------------------------------------------------
# 2. 距离缺失：null + 「距离未知」，绝不出现 0 光年
# ---------------------------------------------------------------------------
class TestMissingDistanceIsUnknown:
    def test_object_lookup_without_parallax_says_unknown(self, sky_agent):
        # HIP 2599（王良二）在 HYG 里 dist_pc 为 NaN，是真实缺失样本。
        data = _call(sky_agent, "sky_object_lookup", {"query": "HIP 2599"})
        assert data["_tool_success"] is True and data["ok"] is True
        assert data["dist_pc"] is None
        assert data["light_years"] is None
        assert data["light_time_text"] == "距离未知"
        assert "距离未知" in data["hint"]

        dumped = json.dumps(data, ensure_ascii=False)
        for fake in FAKE_ZERO_DISTANCE:
            assert fake not in dumped, f"缺失距离被写成 {fake!r}"

    def test_whats_here_marks_missing_distance_not_zero(self, cas_view):
        assert cas_view["_tool_success"] is True and cas_view["ok"] is True
        stars = {star["hip"]: star for star in cas_view["stars"]}
        assert 2599 in stars, sorted(stars)
        assert stars[2599]["light_years"] is None
        # 扫描输出必须给模型一句可照说的话，而不是只留 null 让它猜。
        assert "距离未知" in cas_view["hint"]

        dumped = json.dumps(cas_view, ensure_ascii=False)
        for fake in FAKE_ZERO_DISTANCE:
            assert fake not in dumped, f"缺失距离被写成 {fake!r}"

    def test_whats_here_light_years_comes_from_catalog_distance(self, cas_view):
        stars = {star["hip"]: star for star in cas_view["stars"]}
        reference = next((star for star in stars.values()
                          if star["light_years"] is not None), None)
        if reference is None:
            pytest.skip("这个视场没有带距离的参照星")
        catalog, row = _catalog_row(reference["hip"])
        assert row is not None, reference["hip"]
        expected = _light_years(float(catalog.dist_pc[row]))
        assert reference["light_years"] == pytest.approx(
            round(expected, 1))
        # 来源可见：模型能说清距离/光行时来自哪份星表。
        assert cas_view["catalog"]["name"] == "HYG v4.1"

    def test_dso_lookup_missing_distance_stays_null(self, sky_agent):
        """DSO 目录已有部分距离；M42 无视差 → 必须 null，不许编 0。"""
        data = _call(sky_agent, "sky_dso_lookup", {"name": "M42"})
        assert data["_tool_success"] is True and data["ok"] is True
        obj = data["objects"][0]
        assert obj["distance_pc"] is None
        assert obj["light_years"] is None
        for key in ("distance_ly", "distance_km"):
            assert key not in obj, f"不许出现第二个距离字段 {key}"

        assert "未知" in data["note"]
        dumped = json.dumps(data, ensure_ascii=False)
        for fake in FAKE_ZERO_DISTANCE:
            assert fake not in dumped, f"DSO 缺失距离被写成 {fake!r}"


# ---------------------------------------------------------------------------
# 3. 能力探测：失败留 unknown，状态与报告同口径
# ---------------------------------------------------------------------------
class TestCapabilityProbeContracts:
    def test_probe_exception_keeps_unknown_with_chinese_reason(
            self, cap, monkeypatch):
        def boom():
            raise RuntimeError("permission denied")

        monkeypatch.setattr("mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices",
                            boom)
        data = _call(cap, "sky_capability_state", {})
        assert data["_tool_success"] is True
        assert data["slots"]["sdr"]["state"] == "unknown"
        assert "sdr" in data["probe"]["skipped"]
        assert data["probe"]["probed"] == []
        assert data["probe"]["written"] == 0
        # 原始异常不吞：模型/用户能看到探测为什么失败。
        assert "permission denied" in data["probe"]["sdr_error"]
        # 失败原因与「skipped ≠ 没硬件」的解释必须是中文。
        assert "没有探测手段" in data["note"]
        assert "不等于没有硬件" in data["note"]

        dumped = json.dumps(data, ensure_ascii=False)
        assert '"state": "available"' not in dumped
        assert "已就绪" not in dumped

    def test_state_and_report_agree_after_probe_failure(
            self, cap, monkeypatch):
        monkeypatch.setattr(
            "mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices",
            lambda: (_ for _ in ()).throw(RuntimeError("no backend")))
        state = _call(cap, "sky_capability_state", {})
        report = _call(cap, "sky_capability_report", {"items": []})
        assert state["slots"]["sdr"]["state"] == "unknown"
        assert report["slots"]["sdr"]["state"] == "unknown"
        assert report["_tool_success"] is True

    def test_state_and_report_agree_when_probe_finds_device(
            self, cap, monkeypatch):
        monkeypatch.setattr("mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices",
                            lambda: [object()])
        state = _call(cap, "sky_capability_state", {})
        assert state["slots"]["sdr"]["state"] == "available"
        assert state["slots"]["sdr"]["evidence"] == "auto_probe:found"
        report = _call(cap, "sky_capability_report", {"items": []})
        assert report["slots"]["sdr"]["state"] == "available"
        assert report["slots"]["sdr"]["evidence"] == "auto_probe:found"

    def test_device_absent_reports_missing_never_available(
            self, cap, monkeypatch):
        monkeypatch.setattr("mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices",
                            lambda: [])
        data = _call(cap, "sky_capability_state", {})
        # 查过、确实没有 → missing（这是真实结论，不是 unknown）。
        assert data["slots"]["sdr"]["state"] == "missing"
        assert data["slots"]["sdr"]["evidence"] == "auto_probe:not_found"
        assert data["slots"]["sdr"]["state"] != "available"


# ---------------------------------------------------------------------------
# 4. 解算器探测：缺库/抛异常都不得说「可用」
# ---------------------------------------------------------------------------
class TestSolverProbeContracts:
    def test_solver_unavailable_says_why(self, sky_agent, monkeypatch):
        monkeypatch.setattr(
            "mbdsdr_ai.sky_solve.solver_status",
            lambda astap_path=None: {
                "available": False,
                "reason": "未安装 tetra3；ASTAP 需外部可执行文件"})
        data = _call(sky_agent, "sky_solve_status", {})
        assert data["_tool_success"] is True
        assert data["available"] is False
        assert "tetra3" in data["reason"]
        dumped = json.dumps(data, ensure_ascii=False)
        assert "已就绪" not in dumped

    def test_solver_probe_exception_is_not_available(self, sky_agent,
                                                     monkeypatch):
        def boom(astap_path=None):
            raise RuntimeError("探测失败")

        monkeypatch.setattr("mbdsdr_ai.sky_solve.solver_status", boom)
        result = sky_agent.tool_registry.call("sky_solve_status", {})
        assert result.success is False
        assert "探测失败" in result.content
        assert "可用" not in result.content
        assert '"available": true' not in result.content
