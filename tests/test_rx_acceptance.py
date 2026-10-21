# -*- coding: utf-8 -*-
"""接收验收判据（rx_acceptance）测试：逐项判定 + 总判定 + 门限可覆盖。"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


GOOD = {
    "pointing_error_deg": 0.8,
    "snr_db": 14.0,
    "decode_attempts": 100,
    "decode_successes": 99,
    "lna_nf_db": 0.6,
    "feedline_m": 1.5,
    "system_nf_db": 1.0,
    "dish_offset_cm": 0.5,
}


def test_all_within_thresholds_passes(reg):
    r = reg.call("rx_acceptance", dict(GOOD))
    assert r.success is True, r.content[:120]
    assert r.data["verdict"] == "通过"
    assert r.data["failed_items"] == []
    assert r.data["next_steps"] == []


def test_pointing_beyond_material_tolerance_fails(reg):
    args = dict(GOOD)
    args["pointing_error_deg"] = 3.5
    r = reg.call("rx_acceptance", args)
    assert r.data["verdict"] == "不通过"
    assert "指向偏差" in r.data["failed_items"]
    assert r.data["next_steps"], "失败项应给出处置建议"


def test_low_snr_and_bad_frontend_report_all_failures(reg):
    args = dict(GOOD)
    args["snr_db"] = 3.0
    args["feedline_m"] = 12.0
    args["lna_nf_db"] = 2.0
    r = reg.call("rx_acceptance", args)
    assert r.data["verdict"] == "不通过"
    failed = set(r.data["failed_items"])
    assert {"实测 SNR", "馈线长度", "LNA 噪声系数"} <= failed


def test_low_decode_rate_fails(reg):
    args = dict(GOOD)
    args["decode_attempts"] = 100
    args["decode_successes"] = 40
    r = reg.call("rx_acceptance", args)
    assert r.data["verdict"] == "不通过"
    assert "解码成功率" in r.data["failed_items"]


def test_thresholds_are_overridable(reg):
    """门限可按任务指标覆盖（材料未给的项尤其要能调）。"""
    args = dict(GOOD)
    args["pointing_error_deg"] = 3.0
    assert reg.call("rx_acceptance", args).data["verdict"] == "不通过"
    args["pointing_tol_deg"] = 5.0
    assert reg.call("rx_acceptance", args).data["verdict"] == "通过"


def test_missing_inputs_are_marked_not_judged(reg):
    """缺项记为未提供（不参与判定），不编造通过。"""
    r = reg.call("rx_acceptance", {"snr_db": 12.0})
    assert r.success is True
    assert r.data["verdict"] == "通过"          # 只有 SNR 参与且达标
    unchecked = [c for c in r.data["checks"] if c["pass"] is None]
    assert unchecked, "未提供的项应标为未判定"
    assert all(c["note"] for c in unchecked)


def test_source_is_recorded_per_criterion(reg):
    """每条判据都要标注门限来源（材料口径 / 材料未给→工程常规）。"""
    r = reg.call("rx_acceptance", dict(GOOD))
    for c in r.data["checks"]:
        assert c["source"], c
