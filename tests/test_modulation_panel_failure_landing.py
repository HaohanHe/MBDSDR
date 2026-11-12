# SPDX-License-Identifier: MIT
"""调制识别面板：失败必须落在界面上，不能被「线程结束」复位成就绪。

背景：``_IdentifyWorker`` 失败时发 ``failed`` → ``_on_failed`` 写文案，随后
``QThread.finished`` 触发 ``_refresh_state``，旧实现把同一行改写成
「就绪：对准信号后点「识别信号」」。用户看到的是一次「点了没反应」，
而不是「这次识别没做成、可以怎么办」。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_failure_text_survives_thread_finished(qapp, monkeypatch):
    from desktop import modulation_panel as mp

    monkeypatch.setattr(mp, "_KERNEL_AVAILABLE", True)
    panel = mp.ModulationPanel()
    try:
        panel.set_backend(object())
        panel.set_sdr_connected(True)

        panel._on_failed("读取 IQ 失败: 设备已拔出")
        # 真实路径里 QThread.finished 随后会走到这里
        panel._refresh_state()

        text = panel.status_label.text()
        assert "识别没有完成" in text, "失败结论必须留在状态行"
        assert "读取 IQ 失败" in text, "必须保留具体原因"
        assert "重试" in text, "必须写清现在能做什么"
        assert "就绪" not in text, "失败不能被复位成「就绪」"
        assert panel.identify_btn.isEnabled() is True, "失败后必须能重试"
    finally:
        panel.close()


def test_new_attempt_clears_previous_failure(qapp, monkeypatch):
    from desktop import modulation_panel as mp

    monkeypatch.setattr(mp, "_KERNEL_AVAILABLE", True)
    # 让 worker 起不来任何真实线程：只要构造参数被记录即可。
    monkeypatch.setattr(mp._IdentifyWorker, "start", lambda self: None)
    panel = mp.ModulationPanel()
    try:
        panel.set_backend(object())
        panel.set_sdr_connected(True)
        panel._on_failed("识别失败: 空 IQ")

        panel._on_identify_clicked()
        assert panel._last_failure is None
        assert "识别中" in panel.status_label.text()
    finally:
        panel.close()
