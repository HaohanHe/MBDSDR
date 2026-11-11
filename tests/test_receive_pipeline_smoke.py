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
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QThread  # noqa: E402

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


def _thread_names(threads) -> str:
    return ", ".join(t.objectName() or type(t).__name__ for t in threads)


def _shutdown_and_join(pipe, timeout_ms: int = 4000) -> None:
    """关停后等后台回收线程结束（shutdown 本身不再阻塞 GUI 线程）。

    断言是**硬**的：超时、``stale_threads`` 非空、或还有线程在跑，都必须让
    测试红。**回收线程结束 ≠ 线程已死**——超预算的线程会被记进
    ``stale_threads`` 继续跑，只判前者就是把「同步卡 8 秒」换成「安静地留
    野线程」，测试照样绿。预算按最坏值给：4×200 ms（下游）+ 2500 ms（reader）
    = 3300 ms，取 4000 ms 留余量。
    """
    pipe.shutdown()
    assert pipe.wait_for_shutdown(timeout_ms), (
        f"关停未在 {timeout_ms} ms 内收干净："
        f"stale={_thread_names(pipe.stale_threads)}，"
        f"still_alive={_thread_names(pipe.alive_threads)}")
    assert pipe.stale_threads == [], (
        f"关停超预算：{_thread_names(pipe.stale_threads)}")
    assert pipe.alive_threads == [], (
        f"线程仍在运行：{_thread_names(pipe.alive_threads)}")


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
    # shutdown 只请求关停（GUI 线程上限 0 ms），join 交给后台回收线程；
    # 契约不变：不许留野线程。所以等回收结束再断言，而不是假定同步完成。
    # 注意 wait_for_shutdown 只在「回收线程结束 **且** 无线程存活」时返回 True。
    assert pipe.wait_for_shutdown(4000), (
        f"关停未收干净：stale={_thread_names(pipe.stale_threads)}，"
        f"still_alive={_thread_names(pipe.alive_threads)}")
    assert pipe.stale_threads == [], (
        f"关停超预算：{_thread_names(pipe.stale_threads)}")
    # 线程必须收干净（不许留野线程）
    for t in (pipe._reader, pipe._fft, pipe._demod1, pipe._demod2, pipe._record):
        if t is not None:
            assert not t.isRunning(), f"{t.__class__.__name__} 未回收"
    # 幂等：再关一次只唤醒，不重复起回收线程
    reaper = pipe._reaper
    pipe.shutdown()
    assert pipe._reaper is reaper


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
    _shutdown_and_join(pipe)
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
    _shutdown_and_join(pipe)
    assert not pipe._reader.isRunning()


def test_double_start_is_idempotent(qapp):
    from PySide6.QtWidgets import QApplication
    pipe, *_ = _build(qapp)
    pipe.start()
    pipe.start()   # 不许起两套线程
    QApplication.processEvents()
    time.sleep(0.1)
    _shutdown_and_join(pipe)
    assert not pipe._reader.isRunning()


class _StubbornThread(QThread):
    """无视停止信号的工作线程（证伪用：让关停检测真的红）。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("StubbornThread")
        self._release = threading.Event()

    def run(self):
        self._release.wait(30)

    def shutdown(self):
        pass    # 故意不理会停止请求

    def release(self):
        self._release.set()


def test_stuck_thread_makes_shutdown_wait_fail(qapp):
    """1 个线程无视停止信号时，等待机制必须红，而不是等够 3 秒就放行。

    反向钉扎「安静地留野线程」：``wait_for_shutdown`` 若只判回收线程存活，
    超预算的线程会被记进 ``stale_threads`` 后测试照样绿。这里把一个真正
    卡死的线程塞进流水线，要求 ① ``wait_for_shutdown`` 返回 False、
    ② ``stale_threads`` 有它、③ 关停 helper 抛 AssertionError。
    """
    from PySide6.QtWidgets import QApplication
    pipe, *_ = _build(qapp)
    pipe.start()
    assert _wait(lambda: pipe._fft.isRunning(),
                 pump=lambda: QApplication.processEvents()), "FFT 线程未启动"

    stuck = _StubbornThread()
    stuck.start()
    real_fft = pipe._fft
    pipe._fft = stuck          # 让回收线程盯上一个真正卡死的线程
    try:
        pipe.shutdown()
        # 回收线程会在下游 200 ms 预算后放弃并记账
        assert pipe.wait_for_shutdown(2000) is False, (
            "有线程卡死，wait_for_shutdown 却报成功——把野线程静默放行了")
        assert stuck in pipe.stale_threads, (
            f"卡死线程未记账：{_thread_names(pipe.stale_threads)}")
        assert stuck in pipe.alive_threads
        with pytest.raises(AssertionError):
            _shutdown_and_join(pipe, timeout_ms=200)
    finally:
        pipe._fft = real_fft
        stuck.release()
        assert stuck.wait(5000), "证伪线程自身没能回收，测试留下野线程"
    assert not stuck.isRunning()
