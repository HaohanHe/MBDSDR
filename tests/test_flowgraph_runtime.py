# SPDX-License-Identifier: MIT
"""Deterministic tests for the thread-per-block FlowGraph scheduler.
非硬件 / NOT HARDWARE: all signals are synthetic."""
import threading
import time

import numpy as np
import pytest

from mbdsdr_ai.flowgraph_runtime import (
    FlowGraph,
    MultiplyConst,
    Scheduler,
    TpbBlock,
    TpbBuffer,
    VectorSink,
    VectorSource,
)


def test_simple_chain_source_gain_sink():
    """源 -> 增益 -> sink 跑通，sink 收到正确乘幅后的样本。"""
    src = VectorSource("src", np.array([1 + 0j, 2 + 0j, 3 + 0j, 4 + 0j], dtype=np.complex64))
    gain = MultiplyConst("gain", c=2.0 + 0j)
    sink = VectorSink("sink")

    fg = FlowGraph(buffer_capacity=64, chunk=4)
    fg.connect(src, 0, gain, 0)
    fg.connect(gain, 0, sink, 0)

    sch = Scheduler(fg)
    sch.run()
    # 等 sink 收集到 >= 16 个样本
    for _ in range(50):
        if len(sink.collected) >= 16:
            break
        time.sleep(0.02)
    sch.stop()
    sch.wait(timeout=2.0)

    assert len(sink.collected) >= 16
    # 样本应是 2*[1,2,3,4,...] = [2,4,6,8,...]
    for i in range(min(16, len(sink.collected))):
        expected = 2 * ((i % 4) + 1)
        assert np.isclose(sink.collected[i].real, expected, atol=1e-6)


def test_cycle_detected():
    """有环的图必须抛 RuntimeError。"""
    a = MultiplyConst("a", 1.0)
    b = MultiplyConst("b", 1.0)
    fg = FlowGraph()
    fg.connect(a, 0, b, 0)
    fg.connect(b, 0, a, 0)  # 环
    with pytest.raises(RuntimeError):
        fg.validate()


def test_unconnected_port_detected():
    """输出端口未连接必须抛 ValueError。"""
    src = VectorSource("src", np.array([1 + 0j]))
    fg = FlowGraph()
    fg.add_block(src)  # src 有 1 个输出但没接
    with pytest.raises(ValueError):
        fg.validate()


def test_partition_independent_chains():
    """两条独立 source->sink 链应被 partition 成两组。"""
    s1 = VectorSource("s1", np.array([1 + 0j]))
    k1 = MultiplyConst("k1", 1.0)
    sk1 = VectorSink("sk1")
    s2 = VectorSource("s2", np.array([2 + 0j]))
    k2 = MultiplyConst("k2", 1.0)
    sk2 = VectorSink("sk2")

    fg = FlowGraph()
    fg.connect(s1, 0, k1, 0)
    fg.connect(k1, 0, sk1, 0)
    fg.connect(s2, 0, k2, 0)
    fg.connect(k2, 0, sk2, 0)

    groups = fg.partition()
    assert len(groups) == 2


def test_backpressure_blocks_when_sink_slow():
    """sink 慢读时，上游 write 阻塞（不丢数据）。"""
    buf = TpbBuffer(capacity=8, dtype=np.complex64)
    # 写 8 个填满
    data = np.ones(8, dtype=np.complex64)
    assert buf.write(data, timeout=1.0) is True
    # 再写 1 个应该阻塞（超时返回 False）
    assert buf.write(np.ones(1, dtype=np.complex64), timeout=0.3) is False
    # 读走 4 个后再写应成功
    got = buf.read(4, timeout=1.0)
    assert got is not None and len(got) == 4
    assert buf.write(np.ones(1, dtype=np.complex64), timeout=1.0) is True


def test_stop_wakes_blocked_reader():
    """stop() 应唤醒阻塞在 read() 上的线程。"""
    buf = TpbBuffer(capacity=8)

    def _reader():
        buf.read(100, timeout=5.0)  # 会阻塞

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    time.sleep(0.2)
    buf.stop()
    t.join(timeout=2.0)
    assert not t.is_alive()
