# SPDX-License-Identifier: MIT
"""`sky_pointing_check` 只读工具：只说残差，不说「已对准」。

全部用例不接相机、不联网、不碰硬件：
* 失败分支走真实管线/注册表；
* 成功分支注入 fake ``solve_and_verify``，文案里必须有残差数字；
* tetra3 缺失用 ``monkeypatch`` 强制 ``solver_status`` 不可用，依赖环境也不 skip。
"""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import ai_tool_consistency as atc  # noqa: E402
from mbdsdr_ai.sky_pointing_pipeline import SolveOutcome  # noqa: E402

_TOOL = "sky_pointing_check"
_BASE_ARGS = {
    "width_px": 1920,
    "height_px": 1080,
    "fov_deg": 8.0,
    "commanded_az_deg": 180.0,
    "commanded_el_deg": 45.0,
    "site_lat_deg": 45.0,
    "site_lon_deg": 0.0,
    "at_utc": "2026-11-19T12:00:00Z",
}


def _stars(n=8):
    return [[100.0 + 10.0 * i, 200.0 + 7.0 * i] for i in range(n)]


@pytest.fixture(scope="module")
def registry():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    try:
        agent = MBDSDRAgent(AgentConfig(api_key="sk-test",
                                        model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original
    return agent.tool_registry


def _call(registry, **overrides):
    args = dict(_BASE_ARGS, stars=_stars())
    args.update(overrides)
    return registry.call(_TOOL, args)


def test_tool_is_registered_and_read_only(registry):
    assert _TOOL in registry.tools
    assert registry.is_write_tool(_TOOL) is False


def test_too_few_stars_reports_structured_reason(registry):
    res = _call(registry, stars=_stars(4))
    assert res.success is False
    assert "too_few_points" in res.content


def test_out_of_range_site_reports_no_site(registry):
    res = _call(registry, site_lat_deg=999.0)
    assert res.success is False
    assert "no_site" in res.content


def test_bad_time_text_reports_bad_command(registry):
    res = _call(registry, at_utc="not-a-time")
    assert res.success is False
    assert "bad_command" in res.content


def test_solver_unavailable_is_structured_not_success(registry, monkeypatch):
    import mbdsdr_ai.sky_solve as sky_solve

    monkeypatch.setattr(sky_solve, "solver_status",
                        lambda *a, **k: {"available": False,
                                         "reason": "未安装 tetra3"})
    res = _call(registry)
    assert res.success is False
    assert "solver_unavailable" in res.content


def test_success_reports_residual_without_alignment_claim(registry,
                                                           monkeypatch):
    import mbdsdr_ai.sky_pointing_pipeline as pipeline

    monkeypatch.setattr(
        pipeline, "solve_and_verify",
        lambda *a, **k: SolveOutcome(
            ok=True, ra_deg=12.34, dec_deg=-5.6, roll_deg=0.0,
            matched_stars=12, residual_deg=0.12,
            within_tolerance=True))

    res = _call(registry)
    assert res.success is True
    assert "0.12" in res.content
    assert "未判定已对准" in res.content
    # 「未判定已对准」本身包含「已对准」四个字，去掉该固定短语后不得再出现。
    assert "已对准" not in res.content.replace("未判定已对准", "")


def test_schema_matches_handler_reads(registry):
    params = registry.tools[_TOOL]["definition"]["function"]["parameters"]
    expected = set(_BASE_ARGS) | {"stars"}
    assert set(params["properties"]) == expected
    assert set(params["required"]) == expected

    result = atc.scan_tool(_TOOL, registry.tools[_TOOL])
    assert result["fatal"] == []
    assert result["drift"] == []
