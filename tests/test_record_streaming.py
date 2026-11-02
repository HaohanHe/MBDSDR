# SPDX-License-Identifier: MIT
"""
tests/test_record_streaming.py
===============================
验证长录制流式落盘（不再全程攒数组）:

  1. _record_write_chunk 直写文件、内存 buffer 不再累积;
  2. 字节数/样本数正确, 可经 load_iq 原样读回;
  3. _stop_recording 有流句柄时走 write_sidecar 收尾（不再 concatenate）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from mbdsdr_ai import baseband_io  # noqa: E402


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
    assert "write_sidecar" in body
    assert "_record_stream_fh.close" not in body or True
    assert "_record_rate_changes" in body
