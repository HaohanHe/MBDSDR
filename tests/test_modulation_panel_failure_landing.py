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


def test_success_text_survives_thread_finished(qapp, monkeypatch):
    """A2：成功侧的「识别完成」同样必须有保留字段。

    失败侧早有 ``_last_failure`` 兜底；成功侧此前没有对应字段，
    ``QThread.finished`` → ``_refresh_state`` 会把它改写成「就绪」。
    """
    from types import SimpleNamespace

    from desktop import modulation_panel as mp

    monkeypatch.setattr(mp, "_KERNEL_AVAILABLE", True)
    panel = mp.ModulationPanel()
    try:
        panel.set_backend(object())
        panel.set_sdr_connected(True)

        panel._on_done(SimpleNamespace(
            modulation="FM", confidence=0.9, bandwidth=12_500.0,
            symbol_rate_hint=0.0, suggestions=[]))
        # 真实路径里 QThread.finished 随后会走到这里
        panel._refresh_state()

        assert panel.status_label.text() == "识别完成", "成功结论不能被复位成「就绪」"
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


def test_bandwidth_readout_comes_from_readouts(qapp, monkeypatch):
    """A01：带宽读数只有 readouts.format_bandwidth_hz 一个产生点。

    就地 f-string 的旧写法对 500 Hz 显示「0.5 kHz」、对 2.5 MHz 显示
    「2500.0 kHz」、对 0 显示「0.0 kHz」；唯一口径分别是 500 Hz /
    2.500 MHz / --。断言直接与 readouts 的输出比对，改回就地拼装即变红。
    """
    from types import SimpleNamespace

    import readouts
    from desktop import modulation_panel as mp

    monkeypatch.setattr(mp, "_KERNEL_AVAILABLE", True)
    panel = mp.ModulationPanel()
    try:
        for bw in (0.0, 500.0, 2_400.0, 12_500.0, 2_500_000.0):
            panel._on_done(SimpleNamespace(
                modulation="FM", confidence=0.9, bandwidth=bw,
                symbol_rate_hint=0.0, suggestions=[]))
            assert panel.bw_label.text() == readouts.format_bandwidth_hz(
                bw, zero_text="--"), f"bw={bw} 的读数必须来自 readouts"
    finally:
        panel.close()
