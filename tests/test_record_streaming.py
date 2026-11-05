# SPDX-License-Identifier: MIT
"""
tests/test_record_streaming.py
===============================
验证长录制流式落盘（不再全程攒数组）:

  1. _record_write_chunk 直写文件、内存 buffer 不再累积;
  2. 字节数/样本数正确, 可经 load_iq 原样读回;
  3. _stop_recording 有流句柄时走 write_sidecar 收尾（不再 concatenate）。
  4. S7：打不开/写失败必须停录并报错，保留已写部分；内存缓冲不得冒充落盘。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from mbdsdr_ai import baseband_io  # noqa: E402


@pytest.fixture()
def window():
    w = MainWindow()
    w._active_sdr_backend = None
    w._recording = False
    yield w
    w._disconnect()
    w.close()


def _prepare_segment(w, path, sr=1000000.0, center=100000000.0):
    """按 _start_recording 的最小前置状态开一段（不含计时/按钮）。"""
    w._record_session_path = str(path)
    w._record_segments = []
    w._record_segment_index = 1
    w._record_rate_changes = []
    w._record_seconds = 0
    w._record_gain_db = 0.0
    w._record_device = "Stub"
    w._record_driver = "StubBackend"
    w._record_start_time = "2026-11-05T00:00:00"
    w._record_timer = None
    w._recording = True
    return w._open_record_segment(
        str(path), sr, center, "2026-11-05T00:00:00", 1)


class _FailAfter:
    """委托真实文件对象，但第 ``ok`` 次之后的 write 抛磁盘满。"""

    def __init__(self, fh, ok=1):
        self._fh = fh
        self._ok = int(ok)

    def write(self, data):
        if self._ok <= 0:
            raise OSError(28, "No space left on device")
        self._ok -= 1
        return self._fh.write(data)

    def close(self):
        self._fh.close()

    def flush(self):
        self._fh.flush()


def test_stream_write_no_accumulation(tmp_path):
    w = MainWindow.__new__(MainWindow)
    path = str(tmp_path / "cap.iq")
    w._recording = True
    w._record_stream_fh = open(path, "wb")
    w._record_samples_total = 0
    w._record_iq_buffer = []
    rng = np.random.default_rng(0)
    for _ in range(3):
        iq = (rng.standard_normal(1024)
              + 1j * rng.standard_normal(1024)).astype(np.complex64)
        w._record_write_chunk(iq)
    assert w._record_iq_buffer == [], "内存 buffer 不应再累积"
    assert w._record_samples_total == 3072
    w._record_stream_fh.close()
    assert os.path.getsize(path) == 3072 * 8
    out = baseband_io.load_iq(path, sample_rate=2048000)
    assert out["samples"] == 3072


def test_stop_recording_uses_sidecar_when_streamed():
    src = open(os.path.join(_ROOT, "desktop", "main_window.py"),
               encoding="utf-8").read()
    i = src.index("def _stop_recording")
    j = src.index("def ", i + 10)
    body = src[i:j]
    # S4 起收尾走统一段落收尾（_finalize_record_segment），sidecar 由它落盘；
    # 这里跟着契约走，仍然钉住「流式路径必须写 sidecar」，不是放宽。
    assert "_finalize_record_segment" in body, (
        "流式收尾必须走统一的段落收尾，不能再自己 concatenate")
    helper = src[src.index("def _finalize_record_segment"):]
    helper = helper[:helper.index("\n    def ", 1)]
    assert "write_sidecar" in helper, "段落收尾必须写 sidecar"
    # 多段会话清单由 _write_session_sidecars 回写（S4 取代了 _stop_recording
    # 里的 concatenate / 手工 sidecar 拼接）
    assert "_write_session_sidecars" in body
    assert "session_duration_s" in body


def test_open_failure_refuses_start_instead_of_buffering(window, tmp_path):
    """S7：文件打不开＝拒绝开始，不许退回内存缓冲假装在录。"""
    path = str(tmp_path / "no_such_dir" / "cap.iq")
    assert _prepare_segment(window, path) is False
    assert window._recording is False, "打不开文件必须停止录音"
    assert window._record_stream_fh is None
    assert window._record_segments == [], "不得记录不存在的段"
    assert window._record_iq_buffer == [], "不得攒内存冒充落盘"
    assert not os.path.exists(path)
    msg = window.statusBar().currentMessage()
    assert "录音文件打不开" in msg
    assert "已写 0 字节" in msg


def test_write_failure_stops_recording_and_keeps_written_bytes(window, tmp_path):
    """S7：写失败＝停录并报错，已写部分保留，sidecar 只记真实样本数。"""
    path = str(tmp_path / "baseband_20261105_000000.iq")
    assert _prepare_segment(window, path) is True
    first = np.ones(1000, dtype=np.complex64)
    window._record_write_chunk(first)
    assert window._record_samples_total == 1000

    window._record_stream_fh = _FailAfter(window._record_stream_fh, ok=0)
    window._record_write_chunk(np.ones(500, dtype=np.complex64))

    assert window._recording is False, "写失败必须停止录音"
    assert window._record_stream_fh is None
    assert window._record_iq_buffer == [], "失败块不得退内存缓冲"
    assert os.path.getsize(path) == 1000 * 8, "已写部分必须保留"
    meta = json.loads((tmp_path / "baseband_20261105_000000.iq.json")
                      .read_text(encoding="utf-8"))
    assert meta["samples"] == 1000, "sidecar 只能记真实落盘样本数"
    out = baseband_io.load_iq(path)
    assert out["samples"] == 1000
    msg = window.statusBar().currentMessage()
    assert "写入录音文件失败" in msg
    assert "已写 8000 字节" in msg


def test_finalize_never_fakes_disk_write_from_memory(window, tmp_path):
    """S7：无流句柄时内存缓冲不得被写成「已落盘」文件。"""
    path = tmp_path / "ghost.iq"
    window._record_file_path = str(path)
    window._record_samples_total = 500
    window._record_sr = 1000000.0
    window._record_center_hz = 100000000.0
    window._record_segment_index = 1
    window._record_stream_fh = None
    window._record_iq_buffer = [np.ones(500, dtype=np.complex64)]

    assert window._finalize_record_segment() is None
    assert not path.exists(), "没写过流就不得创建文件"
    assert window._record_segments == []
    assert window._record_iq_buffer == []
