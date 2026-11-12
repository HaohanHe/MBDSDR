# SPDX-License-Identifier: MIT
"""气象面板 FY-4 raw IQ 输入必须诚实拒绝，不得按字节伪造 PL 帧。"""
import os
import sys
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

from desktop.weather_panel import DecodeWorker  # noqa: E402


def test_fy4_raw_iq_goes_through_real_ccsds_chain(tmp_path):
    """FY-4 raw IQ 现在走真实经典 CCSDS 链路；垃圾输入必须诚实失败。

    2026-11-06 变更：面板原先直接回绝「raw IQ 不支持」（stage=iq_sync /
    error_code=unsupported_raw_iq），但库层 `decode_fy4_iq_to_image` 早在
    tests/test_fy4_iq_chain.py 里就有 bpsk/qpsk 逐像素往返。面板说没有、
    库里有，属于断线。现在真接：允许读 IQ、允许解调。

    仍然钉死的红线：
      * 不得调用 DVB-S2 的 PL 符号同步（那是另一条路，本分支不冒充）；
      * 垃圾输入必须 ok=False、frames=0、带 stage 与原因；
      * 失败时不得留下半截图。
    """
    path = tmp_path / "fy4_raw.iq"
    path.write_bytes(np.arange(16, dtype=np.complex64).tobytes())
    png = tmp_path / "must_not_exist.png"
    worker = DecodeWorker({
        "family": "fy4", "satellite": "FY-4A HRIT", "iq_file": str(path),
        "out_png": str(png),
    })
    results = []
    failures = []
    worker.finished.connect(results.append)
    worker.failed.connect(failures.append)

    with mock.patch("mbdsdr_ai.fengyun_sat.fy4_dvbs2_sync") as dvbs2:
        worker.run()

    assert not dvbs2.called, "经典 CCSDS 链路不得调用 DVB-S2 的 PL 符号同步"
    assert failures == []
    assert len(results) == 1
    result = results[0]
    assert result["ok"] is False
    assert result["frames"] == 0
    assert result["stage"] in ("frame_sync", "decode", "demod", "segment_assembly")
    assert result["meta"]["raw_iq_supported"] is True
    assert result["meta"].get("attempts"), "两轮尝试的诊断要留下，便于排查"
    assert not png.exists(), "失败时不得留下半截图"


def test_status_line_frequency_uses_readouts():
    """A01：状态行频率读数走 readouts.format_frequency_hz，不就地拼「MHz」。

    输入框是用户可改的 MHz 字符串：是数值就按 1 Hz 口径（6 位小数 MHz）
    格式化，不降精度（145.5125 → 145.512500 MHz）；不是数值原样保留，
    不被静默吞掉，整行结构（频率 | 符号率 | 调制 | 频段 | 帧数）不变。
    """
    import readouts
    from desktop.weather_panel import WeatherPanel

    panel = WeatherPanel()
    try:
        panel.sat_combo.setCurrentText("GOES-16 HRIT (75.2°W)")
        panel.freq_edit.setText("145.5125")
        panel._update_status()

        expected = readouts.format_frequency_hz(145.5125e6)
        assert expected == "145.512500 MHz"
        text = panel.status_label.text()
        assert text.startswith(expected + " | "), text
        assert text.count("MHz") == 1, "频率单位只由 readouts 产生一次"
        assert "ksps" in text and "帧/段" in text, "整行信息结构不变"

        panel.freq_edit.setText("不是数字")
        panel._update_status()
        assert panel.status_label.text().startswith("不是数字 MHz | ")
    finally:
        panel.close()
