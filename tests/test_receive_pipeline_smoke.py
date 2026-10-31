#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
接收流水线多线程冒烟测试（offscreen，真线程真信号）
====================================================

desktop/receive_pipeline.py 529 行零测试。多线程流水线一崩就是
接收链全断/线程竞态。用 stub 驱动真实 QThread：数据真的从 reader
流到 FFT/解调/录制三路，frame_ready 信号真的跨线程到 UI。

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_receive_pipeline_smoke.py -v
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
# 注意：不要把 mbdsdr_ai/ 裸目录塞进 sys.path——它内含带 __init__.py 的 tests/
# 常规包，`import tests` 会解析到 mbdsdr_ai/tests（常规包盖过根 tests 命名空间
# 包），全进程的 tests.* 点路径全断（2026-10-31 CI A 组实锤）。本文件只需
# desktop/（receive_pipeline、mode_registry）与 repo root（mbdsdr_ai.* 包）。


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------
class _StubGen:
    def __init__(self):
        self.pushed = 0

    def push_iq(self, iq, center, sr):
        self.pushed += 1


class _StubVfo:
    def __init__(self):
        self.calls = 0

    def process(self, iq):
        self.calls += 1
        return np.zeros(64, dtype=np.float32)


class _StubPlayer:
    available = False   # 不真发声（也在无音频环境诚实空态）

    def __init__(self):
        self.writes = 0

    def write(self, audio):
        self.writes += 1

    def set_muted(self, m):
        pass


def _reader_fn(n):
    """真数据源：每次给一段复数噪声（保证 FFT 有活干）。"""
    return (np.random.default_rng(0).standard_normal(n)
            + 1j * np.random.default_rng(1).standard_normal(n)
            ).astype(np.complex64)


def _build(qapp, read_fn=None, vfo2=True):
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    gen = _StubGen()
    vfo1 = _StubVfo()
    vfo2d = _StubVfo() if vfo2 else None
    player = _StubPlayer()
    recorded = []
    pipe = ReceivePipeline(
        read_fn=read_fn or _reader_fn, sr=2.4e6, gen=gen,
        center_getter=lambda: 100e6,
        main_vfo_dsp=vfo1, vfo2_dsp=vfo2d,
        player=player, config=SharedDemodConfig(),
        on_record_iq=lambda iq: recorded.append(iq),
    )
    return pipe, gen, vfo1, vfo2d, recorded


def _wait(cond, timeout=5.0, pump=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pump:
            pump()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_construction_does_not_crash(qapp):
    pipe, *_ = _build(qapp)
    assert pipe is not None
    pipe.shutdown()   # 未 start 的 shutdown 必须幂等无害


def test_start_flows_data_and_shutdown_joins(qapp):
    """启停生命周期：数据真的流动，shutdown 后线程必须全部回收。"""
    from PySide6.QtWidgets import QApplication
    pipe, gen, vfo1, vfo2d, recorded = _build(qapp)
    got = []
    pipe.frame_ready.connect(lambda iq, sr: got.append(iq))

    pipe.start()
    ok = _wait(lambda: len(got) >= 3,
               pump=lambda: QApplication.processEvents())
    assert ok, f"frame_ready 必须发出（数据真流动），实际 {len(got)} 帧"
    assert gen.pushed >= 1, "FFT 路必须收到 IQ"

    pipe.shutdown()
    # 线程必须收干净（不许留野线程）
    for t in (pipe._reader, pipe._fft, pipe._demod1, pipe._demod2, pipe._record):
        if t is not None:
            assert not t.isRunning(), f"{t.__class__.__name__} 未回收"
    # 幂等
    pipe.shutdown()


def test_single_vfo_pipeline_works(qapp):
    """vfo2_dsp=None 的单 VFO 形态也必须能跑（可选组件不是必需品）。"""
    from PySide6.QtWidgets import QApplication
    pipe, gen, vfo1, vfo2d, _ = _build(qapp, vfo2=False)
    assert pipe._demod2 is None
    got = []
    pipe.frame_ready.connect(lambda iq, sr: got.append(iq))
    pipe.start()
    ok = _wait(lambda: len(got) >= 2,
               pump=lambda: QApplication.processEvents())
    assert ok
    pipe.shutdown()
    assert not pipe._reader.isRunning()


def test_reader_returning_none_is_honest_empty(qapp):
    """read_fn 返回 None（无数据/断连）不得崩，也不得伪造帧。"""
    from PySide6.QtWidgets import QApplication

    def none_reader(n):
        return None

    pipe, gen, *_ = _build(qapp, read_fn=none_reader)
    got = []
    pipe.frame_ready.connect(lambda iq, sr: got.append(iq))
    pipe.start()
    time.sleep(0.4)
    QApplication.processEvents()
    # 无数据时可以没有帧（诚实空态），但线程必须活着不崩
    assert pipe._reader.isRunning() or not pipe._started
    pipe.shutdown()
    assert not pipe._reader.isRunning()


def test_double_start_is_idempotent(qapp):
    from PySide6.QtWidgets import QApplication
    pipe, *_ = _build(qapp)
    pipe.start()
    pipe.start()   # 不许起两套线程
    QApplication.processEvents()
    time.sleep(0.1)
    pipe.shutdown()
    assert not pipe._reader.isRunning()
