#!/usr/bin/env python3
"""
tests/test_vor_roundtrip.py
===========================
MBDSDR VOR（甚高频全向信标）编解码往返测试。

所有信号均由 mbdsdr_ai.vor_decoder.vor_encode 本地合成，不读取任何
真实射频/录音文件；测试用的方位角与呼号均为虚构值，不对应任何真实
VOR 台站。

覆盖：
  1. test_bearing_cardinal_angles   —— 0/90/180/270° 加高斯噪声往返，误差 <5°
  2. test_bearing_intermediate      —— 45/135/225/315° 斜方位往返
  3. test_morse_identifier          —— 呼号 "VOR" 莫尔斯识别往返
  4. test_morse_alphanumeric        —— 字母数字混合呼号往返
  5. test_decode_no_signal          —— 静音输入应返回 bearing=None、不抛异常

运行：python3 -m pytest tests/test_vor_roundtrip.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.vor_decoder import vor_encode, vor_decode

FS = 44100  # 测试采样率（合成信号，非真实硬件采样率）


def _bearing_error(measured: float, expected: float) -> float:
    """计算圆周角度误差（0–180 度）。"""
    d = abs((measured - expected + 180.0) % 360.0 - 180.0)
    return d


def _encode_with_noise(bearing: float, morse: str, snr_db: float = 15.0,
                       seed: int = 0) -> np.ndarray:
    """合成 VOR 信号并加高斯噪声，模拟低信噪比接收。"""
    sig = vor_encode(bearing, morse, FS)
    rng = np.random.default_rng(seed)
    sig_power = np.mean(sig ** 2)
    noise_power = sig_power / (10.0 ** (snr_db / 10.0))
    return sig + rng.normal(0.0, np.sqrt(noise_power), len(sig))


def test_bearing_cardinal_angles():
    """四个正方位（0/90/180/270°）加噪往返，误差应小于 5°。"""
    for bearing in (0.0, 90.0, 180.0, 270.0):
        audio = _encode_with_noise(bearing, "VOR", snr_db=15.0, seed=int(bearing))
        out = vor_decode(audio, FS)
        assert out["bearing"] is not None, f"方位未检出: bearing={bearing}"
        err = _bearing_error(out["bearing"], bearing)
        assert err < 5.0, f"bearing={bearing}° 实测 {out['bearing']:.2f}°，误差 {err:.2f}°"


def test_bearing_intermediate():
    """斜方位 45/135/225/315° 往返，验证相位差在整个圆周上线性正确。"""
    for bearing in (45.0, 135.0, 225.0, 315.0):
        audio = _encode_with_noise(bearing, "TEST", snr_db=20.0, seed=int(bearing) + 1)
        out = vor_decode(audio, FS)
        assert out["bearing"] is not None
        err = _bearing_error(out["bearing"], bearing)
        assert err < 5.0, f"bearing={bearing}° 实测 {out['bearing']:.2f}°，误差 {err:.2f}°"


def test_morse_identifier():
    """呼号 "VOR" 的莫尔斯识别音应被正确还原。"""
    audio = vor_encode(90.0, "VOR", FS)
    out = vor_decode(audio, FS)
    assert out["morse_code"] == "VOR", f"莫尔斯识别不匹配: {out['morse_code']!r}"


def test_morse_alphanumeric():
    """字母数字混合呼号（含数字）莫尔斯往返。"""
    audio = vor_encode(180.0, "AB12", FS)
    out = vor_decode(audio, FS)
    assert out["morse_code"] == "AB12", f"莫尔斯识别不匹配: {out['morse_code']!r}"


def test_decode_no_signal():
    """纯静音输入不应抛异常，bearing 应为 None。"""
    quiet = np.zeros(int(FS * 1.0), dtype=np.float32)
    out = vor_decode(quiet, FS)
    assert out["bearing"] is None
    assert out["morse_code"] == ""
    assert 0.0 <= out["confidence"] <= 1.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
