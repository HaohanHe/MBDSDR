# SPDX-License-Identifier: MIT
"""#50 PlaybackSource.set_frequency 诚实标注（审计交接单 B 节 P2）。

旧病灶：无条件接受任何值并更新 _freq，status.error 仍为空——docstring
承诺的「回放源：频率为录制时记录值」告警没写，频谱/解码按错误中心频率
解释却无任何提示。既有契约（test_playback.py：接受并更新上报元数据）
保持不变；本批补：非法值拒绝 + 改频时写承诺的标注 + 改回录制值清除标注。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.recorder import IQRecorder
from mbdsdr_ai.playback import PlaybackSource

SR = 240_000.0
FREQ = 100_000_000.0
N = 24_000  # 0.1 秒 @ 240k，够用且快


def _make_source(tmp_path):
    t = np.arange(N) / SR
    tone = (0.5 * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)
    rec = IQRecorder(str(tmp_path / "rec"), sample_rate=SR,
                     center_freq_hz=FREQ, device="TestRTL")
    rec.open()
    rec.write(tone)
    rec.close()
    src = PlaybackSource(rec.data_path, loop=False)
    assert src.connect() is True
    return src


class TestFreqAnnotation:
    def test_mismatch_writes_promised_annotation(self, tmp_path):
        src = _make_source(tmp_path)
        # 既有契约：接受并更新上报元数据（test_playback.py 钉着）
        assert src.set_frequency(145_000_000) is True
        assert src.get_frequency() == 145_000_000
        st = src.get_status()
        # 审计病灶：承诺的标注必须真实写进 status.error
        assert st["error"], "改频后 status.error 不得为空（docstring 承诺的标注）"
        assert "100" in st["error"] and "145" in st["error"], (
            "标注必须同时写明录制真实值与上报值，供频谱/解码判读")
        assert st["error_code"] == "freq_reported_only"
        src.disconnect()

    def test_same_value_no_annotation(self, tmp_path):
        src = _make_source(tmp_path)
        assert src.set_frequency(FREQ) is True
        st = src.get_status()
        assert st["error_code"] != "freq_reported_only"
        src.disconnect()

    def test_annotation_cleared_when_back_to_recorded(self, tmp_path):
        src = _make_source(tmp_path)
        assert src.set_frequency(145_000_000) is True
        assert src.get_status()["error_code"] == "freq_reported_only"
        assert src.set_frequency(FREQ) is True
        st = src.get_status()
        assert st["error_code"] != "freq_reported_only", "改回录制值应清除标注"
        src.disconnect()

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0])
    def test_invalid_rejected_without_pollution(self, tmp_path, bad):
        src = _make_source(tmp_path)
        assert src.set_frequency(bad) is False
        assert src.get_frequency() == FREQ, "非法值不得污染上报频率"
        assert src.get_status()["error"], "拒绝必须写原因"
        src.disconnect()

    def test_not_connected_rejected(self, tmp_path):
        src = PlaybackSource(str(tmp_path / "nope.sigmf-data"))
        assert src.set_frequency(145_000_000) is False

    def test_tolerance_one_hz(self, tmp_path):
        src = _make_source(tmp_path)
        # 与 set_sample_rate 同一份 1 Hz 容差口径：浮点尾差不算改频
        assert src.set_frequency(FREQ + 0.5) is True
        assert src.get_status()["error_code"] != "freq_reported_only"
        assert src.set_frequency(FREQ + 2.0) is True
        assert src.get_status()["error_code"] == "freq_reported_only"
        src.disconnect()