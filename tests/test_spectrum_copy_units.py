"""文案审计 U-04/U-05 钉扎：频谱单位口径统一。

U-05：绝对读数（悬停 / 峰值提示 / 纵轴）一律带 ``dBFS``；相对量（SNR、
门限、跨度）必须注明基准（本帧中位）。
U-04：峰值门限的标签写明「相对本帧中位」，数值显示 ``+6.0 dB``，
tooltip 给出「中位数 −72 dBFS → 门限 −66 dBFS」的示例。

纯文案/数值断言，不需要硬件；面板用 offscreen 构造。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDoubleSpinBox, QLabel)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_absolute_readouts_use_dbfs_and_relative_notes_basis():
    """绝对量统一 dBFS；SNR 标明「相对本帧中位」，数字与功率差同源。"""
    from spectrum_widget import _fmt_abs_db, _fmt_axis_tick, _fmt_peak_text

    assert _fmt_abs_db(-73.25) == "-73.2 dBFS"          # 悬停
    assert _fmt_axis_tick(-100) == "-100 dBFS"          # 纵轴刻度

    floor_dbfs = -79.5
    power_dbfs = -73.25
    snr_db = power_dbfs - floor_dbfs                    # 口径 = 峰 − 本帧中位
    text = _fmt_peak_text(145.0e6, power_dbfs, snr_db)
    assert "145.000000 MHz" in text
    assert "-73.2 dBFS" in text                          # 绝对量带单位
    assert f"SNR（相对本帧中位）{snr_db:.1f} dB" in text  # 相对量带基准
    assert snr_db == pytest.approx(6.25)


def test_peak_gate_label_and_value_share_median_basis(qapp):
    """U-04：标签/数值/tooltip 同一个基准，+6.0 dB 不是 dBFS。"""
    from spectrum_widget import SpectrumPanel

    panel = SpectrumPanel()
    label = panel.findChild(QLabel, "peakGateLabel")
    spin = panel.findChild(QDoubleSpinBox, "peakGateSpin")
    assert label is not None and spin is not None

    assert "相对本帧中位" in label.text()
    assert "中位数" in label.toolTip()
    # 文案用排版减号 U+2212，断言只钉数字与单位，避免符号字形差异。
    assert "72 dBFS" in label.toolTip() and "66 dBFS" in label.toolTip()

    assert spin.prefix() == "+"
    assert spin.suffix() == " dB"
    spin.setValue(6.0)
    assert spin.value() == pytest.approx(6.0)
    assert spin.text() == "+6.0 dB"
    assert panel._state.peak_rel_db == pytest.approx(6.0)
