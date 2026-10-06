# SPDX-License-Identifier: MIT
"""Phase54 块3：SSDV 接收链工具注册（真实计数 + 无设备诚实空态）。"""
from __future__ import annotations
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestSsdvRxTool:
    def test_register_count_delta(self):
        from mbdsdr_ai.tool_registry import ToolRegistry
        reg = ToolRegistry()
        before = len(reg.tools)
        reg.register_ssdv_rx_tools()
        assert len(reg.tools) == before + 1
        assert "ssdv_ccsds_decode" in reg.tools

    def test_empty_iq_honest_fail(self):
        from mbdsdr_ai.tool_registry import ToolRegistry
        reg = ToolRegistry()
        reg.register_ssdv_rx_tools()
        h = reg.tools["ssdv_ccsds_decode"]["handler"]
        r = h({"iq_path": ""})
        assert r.success is False  # 无设备/无 IQ 诚实空态，不 mock 出图

    def test_real_iq_decode(self, tmp_path):
        from mbdsdr_ai.tool_registry import ToolRegistry
        from mbdsdr_ai.ccsds_ssdv import synthesize_ssdv_ccsds_iq
        img = np.zeros((48, 48, 3), np.uint8)
        img[:, :, 0] = np.linspace(0, 255, 48, dtype=np.uint8)
        tx = synthesize_ssdv_ccsds_iq(img, 48000.0, 4800.0,
                                      callsign="T", image_id=1)
        p = str(tmp_path / "t.iq")
        tx.iq.astype(np.complex64).tofile(p)
        reg = ToolRegistry()
        reg.register_ssdv_rx_tools()
        r = reg.tools["ssdv_ccsds_decode"]["handler"](
            {"iq_path": p, "afc": True, "pll": True})
        assert r.data["mcu"] == r.data["mcu_tot"] == 36
