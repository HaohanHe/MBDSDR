# SPDX-License-Identifier: MIT
"""四端状态对拍：CLI 与 Web/控制器共享同一状态快照。

只比较稳定字段（连接状态/频率/采样率/解调）；CPU、内存等随时间/环境
变化的字段不参与对拍。全程使用 debug 调试源，不启动真实硬件。
"""
from __future__ import annotations

import json
import os
import urllib.request

import pytest

from mbdsdr_ai.core import SDRController
from mbdsdr_ai.core.cli import run
from mbdsdr_ai.web.server import WebServer

_FREQ = 123_456_789.0
_SAMPLE_RATE = 1_024_000.0
_DEMOD = "NFM"


def _get_json(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_json(text: str) -> dict:
    start = text.find("{")
    assert start >= 0, f"CLI 输出中没有 JSON 状态：{text!r}"
    return json.loads(text[start:])


@pytest.fixture
def ctrl(tmp_path):
    c = SDRController(config_dir=str(tmp_path))
    c.connect("debug")
    c.set_frequency(_FREQ)
    c.set_demod(_DEMOD)
    c.set_sample_rate(_SAMPLE_RATE)
    yield c
    try:
        c.shutdown()
    except Exception:
        pass


def test_cli_and_web_report_same_state(ctrl, capsys):
    server = WebServer(host="127.0.0.1", port=0, backend=ctrl)
    server.start()
    try:
        web = _get_json(f"http://127.0.0.1:{server.port}/api/status")
        controller = ctrl.get_status().to_dict()

        rc = run([
            "--connect", "debug",
            "--freq", str(_FREQ),
            "--demod", _DEMOD,
            "--sample-rate", str(_SAMPLE_RATE),
            "--status",
        ], controller=ctrl)
        assert rc == 0
        cli = _extract_json(capsys.readouterr().out)
    finally:
        server.stop()

    # 共有稳定字段：连接状态 / 频率 / 采样率 / 解调
    assert web["connected"] is True
    assert cli["connected"] is True
    assert controller["connected"] is True

    assert web["center_freq_hz"] == pytest.approx(cli["frequency_hz"])
    assert web["center_freq_hz"] == pytest.approx(controller["frequency_hz"])
    assert web["samp_rate_hz"] == pytest.approx(cli["sample_rate_hz"])
    assert web["samp_rate_hz"] == pytest.approx(controller["sample_rate_hz"])
    assert web["mode"] == cli["demod"] == controller["demod"] == _DEMOD
