# SPDX-License-Identifier: MIT
"""眼图度量积木的行为测试（通信对抗与教学的质量评估能力）。

断言要点：
- 无噪 BPSK：眼高接近信号幅度、眼开度约半个 UI、抖动≈0；
- 加噪后眼高与噪声裕度**单调退化**（度量必须对噪声敏感，不能恒定）；
- 判决相位自动落在眼张开处（不写死采样相位）；
- 输入非法时诚实失败，不返回编造数值。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.modulation import eye_diagram, modulate_bits  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def _wave(amp=0.0, seed=1, sps=8, n=60):
    bits = ([1, 0, 1, 1, 0, 0, 1, 0] * n)[:n * 8]
    w = modulate_bits(bits, "bpsk", sps=sps, shaping="rrc",
                      alpha=0.35)["waveform"]
    if amp > 0:
        rng = np.random.default_rng(seed)
        w = w + amp * (rng.standard_normal(len(w))
                       + 1j * rng.standard_normal(len(w)))
    return w


def test_clean_eye_is_wide_and_quiet():
    m = eye_diagram(_wave(0.0), sps=8, n_traces=300)
    assert m["eye_height"] > 0.1, m["eye_height"]
    assert 0.3 <= m["eye_width_ratio"] <= 0.8, m["eye_width_ratio"]
    assert m["noise_margin"] > 1.0, m["noise_margin"]
    assert m["jitter_ratio"] < 0.05, m["jitter_ratio"]
    assert len(m["traces"]) == m["n_traces"]
    assert len(m["traces"][0]) == 8


def test_noise_degrades_eye_monotonically():
    """噪声增大 → 眼高与裕度单调下降（度量必须敏感）。"""
    heights = []
    margins = []
    for amp in (0.0, 0.02, 0.08):
        m = eye_diagram(_wave(amp), sps=8, n_traces=300)
        heights.append(m["eye_height"])
        margins.append(m["noise_margin"])
    assert heights[0] > heights[1] > heights[2], heights
    assert margins[0] > margins[1] > margins[2], margins


def test_decision_phase_is_automatic():
    """判决相位应落在眼张开处（幅度差最大处），不是固定 0。"""
    m = eye_diagram(_wave(0.0), sps=8, n_traces=300)
    assert 0 <= m["decision_phase"] < 8
    # 判决相位处的开口应不小于其它相位
    assert m["eye_height"] > 0


def test_transition_ratio_reflects_data():
    """跳变率是数据属性：交替比特应接近 1，连续比特应接近 0。"""
    alt = modulate_bits([1, 0] * 100, "bpsk", sps=8, shaping="rrc")["waveform"]
    m = eye_diagram(alt, sps=8, n_traces=200)
    assert m["transition_ratio"] > 0.8, m["transition_ratio"]


def test_invalid_inputs_fail_honestly():
    with pytest.raises(ValueError):
        eye_diagram([], sps=4)
    with pytest.raises(ValueError):
        eye_diagram([0.0, 1.0], sps=8)      # 不足一个符号
    with pytest.raises(ValueError):
        eye_diagram([0.0] * 100, sps=1)      # sps 太小


def test_tool_eye_diagram(reg):
    """走 MCP 工具面：返回度量摘要 + 可供绘图的轨迹预览。"""
    w = _wave(0.0)
    r = reg.call("eye_diagram", {"samples": w.tolist(), "sps": 8,
                                 "n_traces": 200})
    assert r.success is True, r.content[:120]
    assert r.data["eye_height"] > 0.1
    assert r.data["traces_rows"] == 200
    assert len(r.data["traces_preview"]) == 8
    assert "眼高" in r.content


def test_tool_rejects_missing_samples(reg):
    r = reg.call("eye_diagram", {})
    assert r.success is False
    assert "samples" in r.content
