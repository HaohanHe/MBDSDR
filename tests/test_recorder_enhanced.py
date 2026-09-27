"""recorder_enhanced 确定性测试：命名模板、电平表、时长。"""
import os
from datetime import datetime

import numpy as np
import pytest

from mbdsdr_ai.recorder_enhanced import (
    EnhancedAudioRecorder, make_audio_filename,
)


def test_filename_template():
    p = make_audio_filename(145_000_000, "fm", "/tmp/rec",
                            timestamp=datetime(2026, 9, 27, 12, 30, 15))
    assert p == "/tmp/rec/145000000_FM_20260927_123015.wav"


def test_level_and_duration(tmp_path):
    rec = EnhancedAudioRecorder.from_params(
        freq_hz=98_000_000, mode="WFM", out_dir=str(tmp_path),
        timestamp=datetime(2026, 9, 27, 12, 0, 0))
    assert rec.open() is True
    # 文件名按模板
    assert os.path.basename(rec.path) == "98000000_WFM_20260927_120000.wav"

    sr = 48000
    # 写 0.1 秒单位幅度正弦（RMS=1/sqrt2 ≈ -3 dBFS）
    t = np.arange(sr // 10) / sr
    tone = 1.0 * np.sin(2 * np.pi * 440 * t)
    n = rec.write(tone)
    assert n == sr // 10
    level = rec.get_record_level()
    assert level == pytest.approx(-3.0, abs=1.0)   # dBFS
    assert rec.get_record_duration() == pytest.approx(0.1, abs=1e-3)

    rec.close()
    assert os.path.exists(rec.path)


def test_silence_level_minus_inf(tmp_path):
    rec = EnhancedAudioRecorder(str(tmp_path / "s.wav"))
    rec.open()
    rec.write(np.zeros(1000, dtype=np.float32))
    assert rec.get_record_level() == float("-inf")
    rec.close()
