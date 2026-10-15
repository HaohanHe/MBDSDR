# SPDX-License-Identifier: MIT
"""校准工具面测试：solve/apply 闭环 + 注册一致性。"""
import json

import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN
from mbdsdr_ai.vna_cal import ErrorBox

F = np.linspace(1e6, 3e9, 101)


def _read_result(out):
    """大数组结果走 spill 信封（截断防上下文膨胀是既有设计）——读回。"""
    if "spill_path" in out:
        with open(out["spill_path"], encoding="utf-8") as fh:
            return json.load(fh)
    return out


def _pairs(z):
    return [[v.real, v.imag] for v in z]


def _simulate(box: ErrorBox, g):
    g = np.asarray(g, dtype=np.complex128)
    return box.e00 + box.t * g / (1 - box.e11 * g)


def _set():
    return MCPToolSet()


def test_registered_in_vna_cal_domain():
    assert _TOOL_DOMAIN.get("vna_solve_error_box") == "VNA 校准"
    assert _TOOL_DOMAIN.get("vna_apply_calibration") == "VNA 校准"


def test_solve_apply_roundtrip_via_tools():
    """工具面闭环：已知误差项仿真三件标准 -> solve -> 校正 DUT 还原。"""
    box = ErrorBox(e00=np.full(len(F), 0.05 + 0.02j),
                   t=np.full(len(F), 0.98 - 0.01j),
                   e11=np.full(len(F), 0.1 - 0.05j))
    dut = 0.3 * np.exp(1j * np.linspace(0, 4, len(F)))
    ts = _set()
    solved = _read_result(ts.call_tool("vna_solve_error_box", {
        "freqs_hz": F.tolist(),
        "meas_open": _pairs(_simulate(box, np.ones(len(F)))),
        "meas_short": _pairs(_simulate(box, -np.ones(len(F)))),
        "meas_load": _pairs(_simulate(box, np.zeros(len(F))))}))
    assert solved["ideal_kit"] is True
    for k in ("e00", "t", "e11"):
        assert len(solved[k]) == len(F)
        assert np.allclose(np_from(solved[k]),
                           np_from(_pairs(getattr(box, k))), atol=1e-9)
    corrected = _read_result(ts.call_tool("vna_apply_calibration", {
        "s11": _pairs(_simulate(box, dut)),
        "e00": solved["e00"], "t": solved["t"], "e11": solved["e11"]}))
    assert np.max(np.abs(np_from(corrected) - dut)) < 1e-9


def np_from(pairs):
    return np.array([complex(r, i) for r, i in pairs])


def test_nonideal_kit_flag_and_honest_errors():
    ts = _set()
    out = _read_result(ts.call_tool("vna_solve_error_box", {
        "freqs_hz": F.tolist(),
        "meas_open": _pairs(np.ones(len(F))),
        "meas_short": _pairs(-np.ones(len(F))),
        "meas_load": _pairs(np.zeros(len(F))),
        "open_c0_pF": 2.0}))
    assert out["ideal_kit"] is False
    bad = ts.call_tool("vna_solve_error_box", {
        "freqs_hz": F.tolist(),
        "meas_open": _pairs(np.ones(len(F))),
        "meas_short": _pairs(np.ones(10)),     # 长度不齐
        "meas_load": _pairs(np.zeros(len(F)))})
    assert "error" in bad
