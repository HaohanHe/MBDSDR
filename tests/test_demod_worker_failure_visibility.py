# SPDX-License-Identifier: MIT
"""
DemodWorker 失败可见性契约（A：接收链三处静默收口）
====================================================

口径：
  - 可预期单帧异常（坏数据/设备拔出）→ 限流 warning + demod_failed 上报 + 跳过本帧；
  - 编程错误（TypeError/AttributeError 等）→ 上抛，绝不当成"这一帧坏了"吞掉；
  - 线程边界（QThread.run）不许把异常抛出去：捕获后 log.exception + 上报 + 停线程。

三条红测试：
  1. 可预期异常：恰好 1 条 warning、1 次 demod_failed，之后线程继续跑（20 块同理）；
  2. 编程错误：helper 层原样上抛；run() 捕获后干净退出，不上抛到线程外；
  3. 连发 20 块坏数据只出 1 条 warning（2 秒限流真正生效，不刷屏）。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _FakeStream:
    """按预置块序列喂给 DemodWorker，耗尽后返回 n=-1 让 run() 正常退出。"""

    def __init__(self, blocks):
        self._blocks = list(blocks)
        self._i = 0

    def read(self):
        if self._i >= len(self._blocks):
            return None, -1
        buf = self._blocks[self._i]
        self._i += 1
        return buf, len(buf)

    def flush(self):
        pass

    def stop_reader(self):
        pass


class _FakeVfo:
    """VFO stub：按调用序号抛指定异常，其余返回一段可解调的复数 IQ。"""

    def __init__(self, errors=None, size=256):
        self._errors = dict(errors or {})
        self._size = int(size)
        self.calls = 0

    def process(self, iq):
        idx = self.calls
        self.calls += 1
        exc = self._errors.get(idx)
        if exc is not None:
            raise exc
        return np.full(self._size, 0.1 + 0.0j, dtype=np.complex64)


class _FakePlayer:
    available = True

    def __init__(self):
        self.writes = 0
        self.muted = []

    def write(self, audio):
        self.writes += 1

    def set_muted(self, m):
        self.muted.append(bool(m))


def _make_worker(blocks, vfo, player, mode="FM"):
    from receive_pipeline import DemodWorker, SharedDemodConfig
    return DemodWorker(
        _FakeStream(blocks), vfo, player,
        SharedDemodConfig(mode=mode), sr=2.4e6,
    )


def _failures(worker):
    got = []
    worker.demod_failed.connect(lambda msg: got.append(msg))
    return got


def _warning_msgs(caplog, keyword):
    return [r for r in caplog.records
            if r.name.endswith("receive_pipeline")
            and r.levelno >= logging.WARNING
            and keyword in r.getMessage()]


def test_expected_frame_error_reports_once_and_keeps_running(qapp, caplog):
    """可预期异常：1 条 warning + 1 次上报，下一块继续正常处理。"""
    caplog.set_level(logging.WARNING, logger="receive_pipeline")
    vfo = _FakeVfo(errors={0: ValueError("坏块")})
    player = _FakePlayer()
    worker = _make_worker([np.zeros(1024, dtype=np.complex64)] * 2, vfo, player)
    fails = _failures(worker)

    worker.run()   # 直接调 run()，同步执行，不真开线程

    assert vfo.calls == 2, "坏帧之后必须继续处理下一块，不能整线程退出"
    assert player.writes == 1, "第二块必须真正写到声卡"
    assert len(fails) == 1, f"恰好上报一次，实际 {fails}"
    assert "VFO 处理失败" in fails[0] and "坏块" in fails[0]
    assert len(_warning_msgs(caplog, "VFO 处理失败")) == 1, (
        "可预期异常必须有 warning，不许静默跳过")
    assert worker._running is False, "数据流耗尽后线程应正常结束"


def test_programming_error_raises_from_helper(qapp):
    """编程错误不许被当成坏帧：helper 层原样上抛。"""
    vfo = _FakeVfo(errors={0: TypeError("脚本错误")})
    worker = _make_worker([], vfo, _FakePlayer())

    with pytest.raises(TypeError):
        worker._vfo_ddc(np.zeros(1024, dtype=np.complex64))
    assert vfo.calls == 1


def test_programming_error_stops_thread_without_escaping(qapp, caplog):
    """run() 边界捕获编程错误：log.exception + 上报 + 停线程，绝不抛到线程外。"""
    caplog.set_level(logging.WARNING, logger="receive_pipeline")
    vfo = _FakeVfo(errors={0: TypeError("脚本错误")})
    player = _FakePlayer()
    worker = _make_worker([np.zeros(1024, dtype=np.complex64)] * 3, vfo, player)
    fails = _failures(worker)

    worker.run()   # 不许抛

    assert vfo.calls == 1, "编程错误后必须停止，不再继续消费数据"
    assert len(fails) == 1 and "内部错误" in fails[0], f"实际 {fails}"
    assert worker._running is False


def test_twenty_bad_blocks_yield_single_warning(qapp, caplog):
    """限流生效：20 块坏数据只留 1 条 warning（拔设备不刷屏）。"""
    caplog.set_level(logging.WARNING, logger="receive_pipeline")
    vfo = _FakeVfo(errors={i: ValueError("坏块") for i in range(20)})
    player = _FakePlayer()
    worker = _make_worker([np.zeros(1024, dtype=np.complex64)] * 20, vfo, player)
    fails = _failures(worker)

    worker.run()

    assert vfo.calls == 20, "每一块都要尝试，坏块不能改变循环节奏"
    assert worker._running is False
    assert len(fails) == 1, f"2 秒限流内只允许上报一次，实际 {fails}"
    assert len(_warning_msgs(caplog, "VFO 处理失败")) == 1, (
        "2 秒内 20 块坏数据只许 1 条 warning")
