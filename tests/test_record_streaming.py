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
