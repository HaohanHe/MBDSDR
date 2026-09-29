# SPDX-License-Identifier: MIT
"""Deterministic tests for the block-stream architecture.
非硬件 / NOT HARDWARE: all signals are synthetic."""
import numpy as np
import pytest

from mbdsdr_ai.block_stream import (
    Block, FlowGraph, MultiplyConst, ProbeSignalStats, StreamTag,
    VectorSink, VectorSource,
)


def _run_chain(src, *blocks, sink, n_samples=24, chunk=4):
    fg = FlowGraph(chunk=chunk)
    prev = src
    for b in blocks:
        fg.connect(prev, 0, b, 0)
        prev = b
    fg.connect(prev, 0, sink, 0)
    fg.setup()
    fg.run(max_samples=n_samples)
    return fg


def test_blocks_connect_data_flows_through():
    """source -> multiply(2) -> sink：样本逐点精确流过。"""
    vec = np.array([1 + 0j, 2 + 0j, 3 + 0j], dtype=np.complex64)
    src = VectorSource("src", vec)
    mul = MultiplyConst("mul", c=2.0)
    snk = VectorSink("snk")
    _run_chain(src, mul, sink=snk, n_samples=12, chunk=4)

    got = np.array(snk.collected)
    # 源循环 [1,2,3]，乘 2
    expected = np.array([1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3], dtype=np.complex64) * 2
    assert np.allclose(got, expected)


def test_topological_sort_order():
    """拓扑排序：source 在前，sink 在后。"""
    src = VectorSource("src", np.array([1 + 0j]))
    mul = MultiplyConst("mul")
    snk = VectorSink("snk")
    fg = FlowGraph()
    fg.connect(src, 0, mul, 0)
    fg.connect(mul, 0, snk, 0)
    order = fg.topological_sort()
    names = [b.name for b in order]
    assert names.index("src") < names.index("mul") < names.index("snk")


def test_loop_detected():
    """A graph with a loop must raise."""
    a = Block("a", 1, 1)
    b = Block("b", 1, 1)
    fg = FlowGraph()
    fg.connect(a, 0, b, 0)
    fg.connect(b, 0, a, 0)
    with pytest.raises(RuntimeError):
        fg.topological_sort()


def test_fanout_one_buffer_multiple_readers():
    """一个 source fan-out 到两个 sink，两个 sink 都收到完整数据。"""
    vec = np.array([1 + 0j, 2 + 0j, 3 + 0j], dtype=np.complex64)
    src = VectorSource("src", vec)
    s1 = VectorSink("s1")
    s2 = VectorSink("s2")
    fg = FlowGraph(chunk=3)
    fg.connect(src, 0, s1, 0)
    fg.connect(src, 0, s2, 0)
    fg.setup()
    fg.run(max_samples=9)
    assert len(s1.collected) == 9
    assert s1.collected == s2.collected


def test_tag_propagation_through_blocks():
    """标签随样本流传播：在 offset 0 贴的 freq 标签沿链传到 sink 输入 reader。"""

    class TagSrc(VectorSource):
        def work(self, inputs, outputs):
            n = super().work(inputs, outputs)
            self.add_output_tag(0, "freq", 100_000_000)
            return n

    src = TagSrc("tsrc", np.array([1 + 0j, 2 + 0j, 3 + 0j], dtype=np.complex64))
    mul = MultiplyConst("mul", c=3.0)
    snk = VectorSink("snk")
    fg = FlowGraph(chunk=3)
    fg.connect(src, 0, mul, 0)
    fg.connect(mul, 0, snk, 0)
    fg.setup()
    fg.run(max_samples=9)

    tags = snk._in_readers[0].tags_in_range(0, 9)
    assert len(tags) == 3  # 每个 chunk 起点一个标签：offset 0,3,6
    offsets = sorted(t.offset for t in tags)
    assert offsets == [0, 3, 6]
    assert all(t.key == "freq" and t.value == 100_000_000 for t in tags)


def test_stream_tag_shifted():
    """StreamTag.shift 平移 offset。"""
    t = StreamTag(offset=10, key="gain", value=20.0, srcid="x")
    t2 = t.shifted(5)
    assert t2.offset == 15
    assert t2.key == "gain" and t2.value == 20.0


def test_ai_auto_tune_suggestion():
    """AI 自动调参：弱信号建议加增益，强信号建议减增益，高峰均比收窄带宽。"""
    probe = ProbeSignalStats("probe")
    fg = FlowGraph()

    weak = type("P", (ProbeSignalStats,), {})("w")
    weak.rms = 0.001
    weak.peak = 0.01
    sug = fg.auto_tune_suggestion(weak)
    assert sug["gain_delta_db"] == 6.0

    strong = type("P", (ProbeSignalStats,), {})("s")
    strong.rms = 0.8
    strong.peak = 1.0
    sug = fg.auto_tune_suggestion(strong)
    assert sug["gain_delta_db"] == -3.0

    papr = type("P", (ProbeSignalStats,), {})("p")
    papr.rms = 0.1
    papr.peak = 2.0  # peak/rms = 20 > 12
    sug = fg.auto_tune_suggestion(papr)
    assert sug["bandwidth_scale"] == 0.8
