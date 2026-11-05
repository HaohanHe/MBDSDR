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


def test_fy4_raw_iq_reports_unsupported_without_fake_symbol_path(tmp_path):
    path = tmp_path / "fy4_raw.iq"
    path.write_bytes(np.arange(16, dtype=np.complex64).tobytes())
    worker = DecodeWorker({
        "family": "fy4", "satellite": "FY-4A HRIT", "iq_file": str(path),
        "out_png": str(tmp_path / "must_not_exist.png"),
    })
    results = []
    failures = []
    worker.finished.connect(results.append)
    worker.failed.connect(failures.append)

    with mock.patch("desktop.weather_panel.np.unpackbits",
                    side_effect=AssertionError("raw IQ 不得 unpackbits")) as unpackbits, \
            mock.patch("mbdsdr_ai.fengyun_sat.fy4_dvbs2_sync") as decoder, \
            mock.patch("builtins.open", side_effect=AssertionError("不支持路径不应读取 raw IQ")):
        worker.run()

    assert failures == []
    assert len(results) == 1
    result = results[0]
    assert result["ok"] is False
    assert result["frames"] == 0
    assert result["stage"] == "iq_sync"
    assert result["error_code"] == "unsupported_raw_iq"
    assert result["meta"]["raw_iq_supported"] is False
    assert result["meta"]["required_input"] == "已同步 PLFRAME 复数符号"
    assert "定时" in result["error"]
    assert "载波" in result["error"]
    assert "PL 符号同步" in result["error"]
    assert "DATAFIELD→VCDU 重组已实现但未接入本面板" in result["error"]
    assert not (tmp_path / "must_not_exist.png").exists()
    unpackbits.assert_not_called()
    decoder.assert_not_called()
