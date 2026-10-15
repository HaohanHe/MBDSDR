# SPDX-License-Identifier: MIT
"""set_iq_invert 工具面 + 叠加语义测试（sdrpp IqInvert 缺口收尾）。"""
import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN, _build_tool_table
from mbdsdr_ai.iq_correction import IQCorrector, inject_imbalance


def _tone(n=4096):
    return np.exp(2j * np.pi * 1000.0 * np.arange(n) / 48000.0)


def test_gated_without_arm():
    ts = MCPToolSet()
    out = ts.call_tool("set_iq_invert", {"invert": True})
    assert out["ok"] is False and out["gated"] is True


def test_armed_no_backend_is_honest():
    ts = MCPToolSet()
    out = ts.call_tool("set_iq_invert", {"invert": True, "arm_manual": True})
    assert out["error"] == "sdr_error"
    assert out["outcome"] == "error"


def test_registered_and_domain_mapped():
    assert _TOOL_DOMAIN["set_iq_invert"] == "SDR 调谐/VFO"
    specs = {t["name"]: t for t in _build_tool_table()}
    assert specs["set_iq_invert"].get("write") is True


def test_stacks_with_iq_correction():
    """反相在增益/正交校正之后施加——两者可叠加，边带完全镜像。"""
    c = IQCorrector()
    c.set_imbalance(1.03, 4.0)
    bad = inject_imbalance(_tone(), 1.03, 4.0)
    fixed = c.process(bad)
    c.set_iq_invert(True)
    flipped = c.process(bad)
    fr = np.fft.fftshift(np.fft.fftfreq(4096, 1 / 48000.0))

    def ratio(p):
        return (p[(fr > 900) & (fr < 1100)].sum()
                / p[(fr < -900) & (fr > -1100)].sum())

    pf = np.abs(np.fft.fftshift(np.fft.fft(fixed))) ** 2
    pl = np.abs(np.fft.fftshift(np.fft.fft(flipped))) ** 2
    assert ratio(pf) > 1000        # 校正后主边带在 +1k
    assert ratio(pl) < 0.01        # 反相后完全镜像到 -1k


def test_invert_flag_readback():
    c = IQCorrector()
    assert c._invert is False
    c.set_iq_invert(True)
    assert c._invert is True
    y = c.process(_tone(n=64))
    assert np.allclose(y, np.conjugate(_tone(n=64)))
