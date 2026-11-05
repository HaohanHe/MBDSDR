# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""接入层压力断言：10 次断开/重连后资源回基线，首帧不带旧频/旧静噪。

用户要求（2026-11-05 接入层批次）：
  1) 假 backend 做 10 次断开/重连；
  2) 每次断言线程数、句柄数、信号连接数回到基线；
  3) 重连后首帧必须来自新一次调谐（不残留上一轮频率），
     接收链静噪必须是本轮设置的余量值（不残留上一轮链对象）。

两条用例：
  * test_rtlsdr_ten_hotplug_cycles_*：RTLSDRBackend + FakeRtlSdr（USB 热插拔语义）。
  * test_receive_pipeline_ten_rebuilds_*：ReceivePipeline 反复构建/关闭，
    统计 QThread 与 QObject 信号接收者。
"""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
import types
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.sdr_backend import RTLSDRBackend  # noqa: E402
from mbdsdr_ai.squelch import margin_threshold_db  # noqa: E402

from fake_rtl import FakeRtlSdr  # noqa: E402

CYCLES = 10


# ═══════════════════════════════════════════════════════════════════════
# 通用：资源基线
# ═══════════════════════════════════════════════════════════════════════

def _ring_threads() -> list:
    return [t for t in threading.enumerate()
            if t.name == "mbdsdr-ring-producer" and t.is_alive()]


def _wait_until(pred, timeout: float = 3.0, interval: float = 0.01) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


def _pump_until(qapp, pred, timeout: float = 2.0) -> bool:
    """跨线程排队信号只有在主线程跑事件循环才会送达。"""
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(pred())


def _handles() -> int:
    import psutil
    return int(psutil.Process().num_handles())


def _baseline() -> tuple[int, int]:
    gc.collect()
    # 等生产者线程彻底退出，基线才可信（stop() 内部 join 2s）
    assert _wait_until(lambda: not _ring_threads(), 3.0)
    return len(threading.enumerate()), _handles()


# ═══════════════════════════════════════════════════════════════════════
# 1. USB 热插拔：10 次断开/重连
# ═══════════════════════════════════════════════════════════════════════

class _MarkerRtlSdr(FakeRtlSdr):
    """每支棒在基带上打一个与中心频率绑定的单音标记。

    marker_hz = int(freq_mhz) * 1 kHz（100–110 kHz，低于 fs/2），
    首帧用瞬时频率估计就能判断它属于哪一次调谐，而不是上一轮的数据。
    """

    def __init__(self, device_index=0):
        super().__init__(device_index=device_index,
                         noise_amplitude=0.0005)

    def read_samples(self, num_samples: int) -> np.ndarray:
        if self._closed:
            raise OSError("FakeRtlSdr: device closed")
        n = int(num_samples)
        sr = float(self._sample_rate)
        t = (self._global_idx + np.arange(n)) / sr
        self._global_idx += n
        marker_hz = float(int(self._center_freq / 1e6) * 1000)
        iq = np.exp(1j * 2.0 * np.pi * marker_hz * t)
        noise = self._noise_amp * (
            self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))
        return (iq + noise).astype(np.complex64)


def _estimate_hz(iq: np.ndarray, sr: float) -> float:
    step = np.angle(iq[1:] * np.conj(iq[:-1]))
    return float(np.median(step)) * sr / (2.0 * np.pi)


def _install_fake_rtl(monkeypatch):
    devices = []

    def _factory(idx=0):
        dev = _MarkerRtlSdr(device_index=idx)
        devices.append(dev)
        return dev

    fake_mod = types.ModuleType("rtlsdr")
    fake_mod.RtlSdr = _factory
    old = sys.modules.get("rtlsdr")
    sys.modules["rtlsdr"] = fake_mod
    monkeypatch.setattr(
        RTLSDRBackend, "_probe_tuner_type",
        staticmethod(lambda idx: 5), raising=False)
    return devices, old


def test_rtlsdr_ten_hotplug_cycles_keep_threads_handles_and_first_frame(monkeypatch):
    devices, old = _install_fake_rtl(monkeypatch)
    try:
        be = RTLSDRBackend(device_index=0)
        # 预热一轮：把进程级首次初始化排除出基线。
        # 只 connect/disconnect 不够：首次 _ensure_receive_chain() 会让
        # ReceiveChain 内部的 AudioResampler 惰性导入 scipy.signal
        # （firwin），一次性加载约 37–64 个 DLL/模块句柄并常驻进程。
        # 不预热这条路径，第 0 轮会把一次性加载误判成每轮泄漏。
        # 预热后各轮句柄应逐轮持平（见下方 ≤ 基线+2 的断言）。
        be.status.frequency_hz = 100e6
        assert be.connect() is True
        assert be._ensure_receive_chain() is not None
        be.disconnect()
        base_threads, base_handles = _baseline()

        for i in range(CYCLES):
            freq_hz = float(100e6 + i * 1e6)
            squelch_db = -50.0 if i % 2 == 0 else -20.0
            be.status.frequency_hz = freq_hz          # 桌面连接前预设的语义
            be.set_squelch(squelch_db)                # 断开时也能记余量口径
            assert be._receive_chain is None, f"第{i}轮：断开后不应残留旧接收链"

            assert be.connect() is True, f"第{i}轮 connect 失败"
            # connect 里按 status 的**本轮**频率重新调谐，生产者随后才启动
            assert be._sdr.center_freq == int(freq_hz)
            assert _wait_until(lambda: be._ring_reader.available() >= 4096, 2.0)

            # 首帧：瞬时频率必须落在本轮的 marker，而不是上一轮的
            block = be.read_samples(4096)
            assert block is not None and len(block) == 4096
            got_hz = _estimate_hz(block, float(be.status.sample_rate_hz))
            want_hz = float(int(freq_hz / 1e6) * 1000)
            assert abs(got_hz - want_hz) < 1000.0, (
                f"第{i}轮首帧残留旧频率：估计 {got_hz:.0f} Hz，本轮 {want_hz:.0f} Hz")
            assert be.status.frequency_hz == freq_hz

            # 静噪：接收链必须是按本轮余量新建的，不是复用上一轮对象
            chain = be._ensure_receive_chain()
            assert chain is not None
            assert chain._squelch is not None
            assert chain._squelch.squelch_db == squelch_db
            assert chain._squelch.margin_threshold_db == pytest.approx(
                margin_threshold_db(squelch_db))
            assert be.status.squelch_threshold_dbfs is None

            # 断开 → 每轮资源回基线
            be.disconnect()
            assert _wait_until(lambda: not _ring_threads(), 3.0), \
                f"第{i}轮断开后仍有生产者线程：{_ring_threads()}"
            gc.collect()
            threads_now = len(threading.enumerate())
            handles_now = _handles()
            assert threads_now == base_threads, (
                f"第{i}轮线程数 {threads_now} != 基线 {base_threads}")
            assert handles_now <= base_handles + 2, (
                f"第{i}轮句柄数 {handles_now} 超出基线 {base_handles}+2")
            assert be._ring_reader is None
            assert be._sdr is None

        # 末轮结束后再确认一次（防止最后一轮藏在循环尾部）
        assert len(devices) == 1 + CYCLES
        assert all(d.closed for d in devices), "旧棒句柄必须全部 close"
    finally:
        if old is not None:
            sys.modules["rtlsdr"] = old
        else:
            sys.modules.pop("rtlsdr", None)


# ═══════════════════════════════════════════════════════════════════════
# 2. 接收流水线：10 次重建，线程 / 句柄 / 信号连接回基线
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _StubGen:
    def __init__(self):
        self.pushed = 0

    def push_iq(self, iq, center, sr):
        self.pushed += 1


class _StubVfo:
    def process(self, iq):
        return np.zeros(64, dtype=np.float32)


class _StubPlayer:
    available = False

    def write(self, audio):
        pass

    def set_muted(self, m):
        pass


class _FrameSink:
    """长期存在的 UI 侧接收者：统计它被旧流水线触发的次数。"""

    def __init__(self):
        from PySide6.QtCore import QObject

        class _Q(QObject):
            def __init__(self, outer):
                super().__init__()
                self.outer = outer

            def on_frame(self, iq, sr):
                self.outer.frames += 1

        self.frames = 0
        self.obj = _Q(self)

    def reset(self):
        self.frames = 0


def _build_pipeline(read_fn, sink):
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    pipe = ReceivePipeline(
        read_fn=read_fn, sr=2.4e6, gen=_StubGen(),
        center_getter=lambda: 100e6,
        main_vfo_dsp=_StubVfo(), vfo2_dsp=None,
        player=_StubPlayer(), config=SharedDemodConfig(),
        on_record_iq=lambda iq: None,
    )
    pipe.frame_ready.connect(sink.obj.on_frame)
    return pipe


def test_receive_pipeline_ten_rebuilds_signal_connections_return_to_baseline(qapp):
    # PySide6 的 QObject.receivers() 对 Python 槽恒返回 0（实测），
    # 因此用两个可数事实替代：① 每轮 frame_ready 真的送达 UI 槽；
    # ② 关闭后旧 pipeline 弱引用全部死亡 → 它持有的信号连接随之销毁。
    sink = _FrameSink()
    base_threads, base_handles = _baseline()

    alive_refs = []
    for i in range(CYCLES):
        t0 = time.time()
        pipe = _build_pipeline(
            lambda n: np.ones(n, dtype=np.complex64), sink)
        pipe.start()
        sink.reset()
        assert _pump_until(qapp, lambda: sink.frames > 0, 2.0), \
            f"第{i}轮流水线未产出首帧"
        # 同一时刻只允许 1 条活动流水线（活动信号连接数基线=1 条链路）
        assert sum(1 for r in alive_refs if r() is not None) == 0, \
            f"第{i}轮启动前已存在未销毁的旧流水线"

        pipe.shutdown()
        frames_at_shutdown = sink.frames
        ref = weakref.ref(pipe)
        alive_refs.append(ref)
        del pipe
        gc.collect()
        # 关闭后旧流水线不再是活对象 → 它持有的信号连接随之销毁（不累积）
        assert ref() is None, f"第{i}轮关闭后流水线对象仍被引用，信号连接残留"
        # 关闭后旧流水线不得再往 UI 送帧（残留连接的行为证据）
        _pump_until(qapp, lambda: False, 0.05)
        assert sink.frames == frames_at_shutdown, \
            f"第{i}轮关闭后仍收到旧流水线帧（{sink.frames}）"

        gc.collect()
        threads_now = len(threading.enumerate())
        handles_now = _handles()
        assert threads_now == base_threads, (
            f"第{i}轮 QThread 未回收：{threads_now} != 基线 {base_threads}")
        assert handles_now <= base_handles + 2, (
            f"第{i}轮句柄数 {handles_now} 超出基线 {base_handles}+2")
        assert time.time() - t0 < 6.0

    dead = [r for r in alive_refs if r() is None]
    assert len(dead) == CYCLES, "存在未销毁的旧流水线（信号连接会累积）"
