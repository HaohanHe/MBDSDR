# SPDX-License-Identifier: MIT
"""桌面扫频诚实性：调谐被拒不得产出「该频段无电台」伪结论。

（原为 docs/pending-patches/2026-11-06-desktop-sweep-truthfulness-test.py.pending，
随补丁 2026-11-06-desktop-sweep-tuning-refusal.md 合并，2026-11-06 移入本目录。）
"""
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from desktop.main_window import MainWindow, _SweepWorker  # noqa: E402


@pytest.fixture(scope="session")
def qtapp():
    """offscreen QApplication 只建一次（与 test_sdr_pipeline.py 同口径）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _RefusingBackend:
    """调谐显式返回 False（驱动异常被吞后的真实表现），记录是否继续读 IQ。"""

    def __init__(self):
        self.reads = 0
        self.status = SimpleNamespace(error="设备拒绝该频率（调谐未落到硬件）")

    def set_frequency(self, hz):
        return False

    def read_samples(self, n):
        self.reads += 1
        return np.zeros(int(n), dtype=np.complex64)


@pytest.mark.usefixtures("qtapp", "isolated_profile")
def test_sweep_never_reports_success_or_moves_marker_when_tuning_refused():
    backend = _RefusingBackend()
    worker = _SweepWorker(backend, 88e6, 108e6, 2e6, 1e6, 0.0, 1024)
    failed, ok = [], []
    worker.failed.connect(failed.append)
    worker.finished_ok.connect(ok.append)

    worker.run()

    assert failed and not ok, "调谐被拒时不得发 finished_ok"
    assert "未在旧频率继续采样" in failed[0]
    assert backend.reads == 0, "拒绝后不得继续在旧频采样"

    # _on_sweep_done：切最强台失败时不得把 marker/频谱基准搬过去
    status_msgs = []
    spectrum = SimpleNamespace(
        set_markers=lambda m: None,
        generator=SimpleNamespace(center_freq_hz=100e6),
    )
    stub = SimpleNamespace(
        _active_sdr_backend=backend,
        spectrum=spectrum,
        ai_panel=SimpleNamespace(_add_system_message=lambda s: None),
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda msg, sev=0: status_msgs.append(msg)),
        _sweep_cleanup=lambda: None,
        _tok=lambda k, d: d,
    )
    result = SimpleNamespace(
        activities=[SimpleNamespace(center_hz=145e6, bandwidth_hz=12.5e3,
                                    peak_db=-40.0)],
        noise_floor_db=-100.0,
        freqs_hz=[88e6, 108e6],
        threshold_db=-60.0,
    )
    MainWindow._on_sweep_done(stub, result)

    assert spectrum.generator.center_freq_hz == 100e6, "调谐失败不得移动频谱基准"
    assert any("未能调谐" in m for m in status_msgs)
