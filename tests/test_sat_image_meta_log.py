# SPDX-License-Identifier: MIT
"""SigMF meta 不可用时的采样率回退：界面文案相同，原因必须进日志。

`_iq_sample_rate` 在 meta 缺失或损坏时都退回手动值，状态行统一写
「文件无有效 SigMF meta」。用户看到的是同一句话，但排查时要能分辨是
「文件没带 meta」还是「meta 存在但解析不了」。
"""
import json
import logging
import os
import sys
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import satellite_image_panel as sip  # noqa: E402


def _panel_stub(manual_mhz: float = 1.0):
    """只提供 _iq_sample_rate 用到的那一个控件。"""
    return SimpleNamespace(sr_spin=SimpleNamespace(value=lambda: manual_mhz))


def test_broken_meta_logs_reason_and_keeps_fallback(tmp_path, caplog):
    iq = tmp_path / "capture.sigmf-data"
    iq.write_bytes(b"\x00" * 8)
    (tmp_path / "capture.sigmf-meta").write_text("{broken", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="satellite_image_panel"):
        sr, note = sip.SatelliteImagePanel._iq_sample_rate(_panel_stub(1.5), str(iq))

    assert sr == 1.5e6
    assert "手动输入 1.500 MS/s" in note
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "SigMF meta 不可用" in msg
    assert "JSONDecodeError" in msg
    assert "capture.sigmf-meta" in msg


def test_missing_meta_falls_back_without_fault(caplog):
    """没有 meta 文件＝文件本来没带，属正常回退，不得记成故障。"""
    with caplog.at_level(logging.WARNING, logger="satellite_image_panel"):
        sr, note = sip.SatelliteImagePanel._iq_sample_rate(
            _panel_stub(1.0), "/nonexistent/capture.sigmf-data")

    assert sr == 1.0e6
    assert "文件无有效 SigMF meta" in note
    assert caplog.records == []


def test_valid_meta_wins_without_log(tmp_path, caplog):
    iq = tmp_path / "capture.sigmf-data"
    iq.write_bytes(b"\x00" * 8)
    (tmp_path / "capture.sigmf-meta").write_text(
        json.dumps({"global": {"core:sample_rate": 2400000.0}}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="satellite_image_panel"):
        sr, note = sip.SatelliteImagePanel._iq_sample_rate(_panel_stub(1.0), str(iq))

    assert sr == 2400000.0
    assert "SigMF meta 2.400 MS/s" in note
    assert caplog.records == []
