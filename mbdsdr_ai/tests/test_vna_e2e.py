# SPDX-License-Identifier: MIT
"""VNA 主链路端到端冒烟：采标 -> 求盒 -> 校正 -> 分析 -> 报告 -> 落盘。

一条测试把第十六批前全部环节串过一遍（合成 VNA 误差项 + 电缆阶跃
DUT），任何环节语义漂移都会在这里炸——比各单元测试的并集更接近
"软件合格"的证据。

calio 落盘在 Python 面直接走 vna_calio（工具面无写文件权限设计）。
"""
import json
import os

import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.vna_cal import ErrorBox
from mbdsdr_ai.vna_calio import load_error_box, save_error_box

F = np.linspace(1e6, 1001e6, 401)
Z0 = 50.0
VF = 0.66
C = 299792458.0


def _pairs(z):
    return [[v.real, v.imag] for v in z]


def _simulate(box, g):
    g = np.asarray(g, dtype=np.complex128)
    return box.e00 + box.t * g / (1 - box.e11 * g)


def _read(out):
    if "spill_path" in out:
        with open(out["spill_path"], encoding="utf-8") as fh:
            return json.load(fh)
    return out


def test_full_vna_chain_end_to_end(tmp_path):
    ts = MCPToolSet()
    # 1) 合成一台有误差项的 VNA + 一根 2m/75Ω 阶跃电缆 DUT
    box = ErrorBox(e00=np.full(len(F), 0.06 + 0.02j),
                   t=np.full(len(F), 0.97 - 0.02j),
                   e11=np.full(len(F), 0.12 - 0.04j))
    rho = (75.0 - Z0) / (75.0 + Z0)
    beta = 2 * np.pi * F / (VF * C)
    dut = rho * np.exp(-2j * beta * 2.0)

    # 2) 三件标准 + DUT 全走误差盒（"采标"）
    meas = {
        k: _pairs(_simulate(box, g)) for k, g in
        (("meas_open", np.ones(len(F))),
         ("meas_short", -np.ones(len(F))),
         ("meas_load", np.zeros(len(F))),
         ("dut", dut))}

    # 3) 求误差盒 -> 校正 DUT
    solved = _read(ts.call_tool("vna_solve_error_box", {
        "freqs_hz": F.tolist(), **{k: meas[k] for k in
                                   ("meas_open", "meas_short", "meas_load")}}))
    corrected = _read(ts.call_tool("vna_apply_calibration", {
        "s11": meas["dut"], "e00": solved["e00"],
        "t": solved["t"], "e11": solved["e11"]}))
    corrected = np.array([complex(r, i) for r, i in corrected])
    assert np.max(np.abs(corrected - dut)) < 1e-9      # 校正链无损

    # 4) 一键分析（校正后数据）：TDR 长度必须回到 2m
    rep = ts.call_tool("vna_analyze_sweep", {
        "freqs_hz": F.tolist(), "s11": _pairs(corrected), "cable": "RG-58"})
    assert rep["tdr"]["length_m"] == pytest.approx(2.0, rel=0.1)
    assert rep["tdr"]["vf"] == pytest.approx(0.66)
    assert rep["vswr_max"][1] > 1.1                   # 失配可见

    # 5) 报告 PNG 落盘 + 元数据
    png_path = tmp_path / "report.png"
    out = ts.call_tool("export_vna_report", {
        "freqs_hz": F.tolist(), "s11": _pairs(corrected),
        "path": str(png_path), "arm_manual": True})
    assert out.get("ok") is True and png_path.exists()
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # 6) 误差盒落盘 -> 重新加载 -> 再校正仍一致（跨会话复用语义）
    box_path = tmp_path / "box.json"
    save_error_box(str(box_path), F,
                   ErrorBox(e00=np.array([complex(r, i) for r, i in solved["e00"]]),
                            t=np.array([complex(r, i) for r, i in solved["t"]]),
                            e11=np.array([complex(r, i) for r, i in solved["e11"]])))
    f2, box2 = load_error_box(str(box_path))
    assert np.allclose(f2, F)
    rec2 = _read(ts.call_tool("vna_apply_calibration", {
        "s11": meas["dut"], "e00": _pairs(box2.e00),
        "t": _pairs(box2.t), "e11": _pairs(box2.e11)}))
    rec2 = np.array([complex(r, i) for r, i in rec2])
    assert np.max(np.abs(rec2 - dut)) < 1e-9
