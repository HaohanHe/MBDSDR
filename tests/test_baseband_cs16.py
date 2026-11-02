# SPDX-License-Identifier: MIT
"""
tests/test_baseband_cs16.py
============================
验证 .cs16 回放格式修复（此前被当 float32 读=误码）:

  1. int16 I/Q 交错文件按 cs16 正确解析并归一化到 [-1,1];
  2. 同一字节流按 cf32 读出的值与 cs16 不同（证明修复前的 bug 真实存在）;
  3. 带 sidecar format=cs16 的元数据路径同样生效。
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import baseband_io  # noqa: E402


def test_cs16_decodes_int16_pairs(tmp_path):
    p = tmp_path / "cap.cs16"
    raw = np.array([16384, -16384, 8192, -8192], dtype=np.int16)
    raw.tofile(str(p))
    out = baseband_io.load_iq(str(p), sample_rate=2.4e6)
    assert "error" not in out, out.get("error")
    iq = np.asarray(out["iq"])
    assert len(iq) == 2
    assert abs(iq[0] - (0.5 - 0.5j)) < 1e-6, iq[0]
    assert abs(iq[1] - (0.25 - 0.25j)) < 1e-6, iq[1]
    # 修复前（float32 误读）语义不同: 按 float32 读应得非 0.5 的值
    raw32 = np.fromfile(str(p), dtype=np.float32)
    assert abs(float(raw32[0]) - 0.5) > 1e-3


def test_cs16_sidecar_format(tmp_path):
    import json
    p = tmp_path / "cap2.cs16"
    np.array([32767, -32768, 0, 16384], dtype=np.int16).tofile(str(p))
    with open(str(p) + ".json", "w", encoding="utf-8") as fh:
        json.dump({"format": "cs16", "sample_rate": 2.4e6}, fh)
    out = baseband_io.load_iq(str(p))
    iq = np.asarray(out["iq"])
    assert abs(iq[0].real - 32767 / 32768.0) < 1e-6
    assert abs(iq[0].imag - (-1.0)) < 1e-6
    assert abs(iq[1].imag - 0.5) < 1e-6
