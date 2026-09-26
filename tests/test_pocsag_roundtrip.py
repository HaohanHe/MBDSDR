"""POCSAG 编码-解码往返测试。

对 512 / 1200 / 2400 三种速率各做一次：构造已知地址+消息 → pocsag_encode
生成 FSK 音频 → 加高斯噪声(SNR~15dB) → pocsag_decode → 断言解出的
address 和 message 与原文一致。

参考: repos/multimon-ng/pocsag.c（状态机）, bch.c（BCH 纠错）,
      gen_pocsag.c（帧结构）
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.pocsag_decoder import pocsag_encode, pocsag_decode  # noqa: E402


SAMPLE_RATE = 22050


def _add_gaussian_noise(audio: np.ndarray,
                        snr_db: float = 15.0,
                        seed: int = 0) -> np.ndarray:
    """向音频加高斯噪声，使 SNR 达到 snr_db。"""
    rng = np.random.default_rng(seed)
    sig_power = float(np.mean(audio ** 2))
    noise_power = sig_power / (10.0 ** (snr_db / 10.0))
    noise = rng.normal(0.0, np.sqrt(noise_power), size=len(audio))
    return (audio + noise.astype(np.float32)).astype(np.float32)


def _find_message(results: list[dict], address: int) -> dict:
    """从解码结果中找到指定地址的消息。"""
    for r in results:
        if r["address"] == address:
            return r
    raise AssertionError(
        f"解码结果中未找到地址 {address}，实际结果: {results}"
    )


def test_numeric_512():
    """512 bps 数字消息往返。"""
    address = 99999
    message = "12345"
    audio = pocsag_encode(address, message, 512, sample_rate=SAMPLE_RATE)
    noisy = _add_gaussian_noise(audio, snr_db=15.0, seed=0)
    results = pocsag_decode(noisy, SAMPLE_RATE, 512)
    hit = _find_message(results, address)
    assert hit["message"].rstrip() == message, \
        f"数字消息不匹配: 期望 {message!r}, 实际 {hit['message']!r}"
    assert hit["baud"] == 512
    assert hit["function"] == 0


def test_alpha_1200():
    """1200 bps 字母消息往返。"""
    address = 54321
    message = "HELLO"
    audio = pocsag_encode(address, message, 1200, sample_rate=SAMPLE_RATE)
    noisy = _add_gaussian_noise(audio, snr_db=15.0, seed=7)
    results = pocsag_decode(noisy, SAMPLE_RATE, 1200)
    hit = _find_message(results, address)
    assert hit["message"].rstrip() == message, \
        f"字母消息不匹配: 期望 {message!r}, 实际 {hit['message']!r}"
    assert hit["baud"] == 1200
    assert hit["function"] == 1


def test_numeric_1200():
    """1200 bps 数字消息往返（覆盖数字+符号）。"""
    address = 12345
    message = "555-1234"
    audio = pocsag_encode(address, message, 1200, sample_rate=SAMPLE_RATE)
    noisy = _add_gaussian_noise(audio, snr_db=15.0, seed=11)
    results = pocsag_decode(noisy, SAMPLE_RATE, 1200)
    hit = _find_message(results, address)
    assert hit["message"].rstrip() == message, \
        f"数字消息不匹配: 期望 {message!r}, 实际 {hit['message']!r}"
    assert hit["baud"] == 1200


def test_alpha_2400():
    """2400 bps 字母消息往返。"""
    address = 77777
    message = "TEST"
    audio = pocsag_encode(address, message, 2400, sample_rate=SAMPLE_RATE)
    noisy = _add_gaussian_noise(audio, snr_db=15.0, seed=13)
    results = pocsag_decode(noisy, SAMPLE_RATE, 2400)
    hit = _find_message(results, address)
    assert hit["message"].rstrip() == message, \
        f"字母消息不匹配: 期望 {message!r}, 实际 {hit['message']!r}"
    assert hit["baud"] == 2400


def test_large_address_21bit():
    """21-bit 大地址（frame=7）往返。

    address=1234567 的低 3 bit 为 7，地址码字放在 batch 最后一帧
    (w15)。早期版本因 bitsync 相位选择偏差导致该码字不可纠，
    解码出 address=None。此测试覆盖该回归。
    """
    address = 1234567
    message = "TEST"
    audio = pocsag_encode(address, message, 1200, sample_rate=SAMPLE_RATE)
    noisy = _add_gaussian_noise(audio, snr_db=15.0, seed=21)
    results = pocsag_decode(noisy, SAMPLE_RATE, 1200)
    hit = _find_message(results, address)
    assert hit["address"] == address, \
        f"大地址不匹配: 期望 {address}, 实际 {hit['address']}"
    assert hit["message"].rstrip() == message, \
        f"消息不匹配: 期望 {message!r}, 实际 {hit['message']!r}"
    assert hit["baud"] == 1200
