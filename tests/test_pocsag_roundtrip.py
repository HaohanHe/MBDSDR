# SPDX-License-Identifier: MIT
"""POCSAG 编码-解码往返测试。

对 512 / 1200 / 2400 三种速率各做一次：构造已知地址+消息 → pocsag_encode
生成 FSK 音频 → 加高斯噪声(SNR~15dB) → pocsag_decode → 断言解出的
address 和 message 与原文一致。

依据 CCIR Radiopaging Code No.1 公开标准：
帧结构/BCH(31,21) 纠错/状态机。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.pocsag_decoder import pocsag_encode, pocsag_decode  # noqa: E402
import mbdsdr_ai.pocsag_decoder as P  # noqa: E402


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


# ─────────────────────────────────────────────────────────────────────
# 真实 POCSAG 调制：NFM FSK（±4.5kHz）→ FM 鉴频 → NRZ 基带
#
# 上面的往返测试用的是仓内 pocsag_encode 的 1200/2400 Hz AFSK 音调口径；
# 真实 VHF 寻呼是直接 FSK，鉴频后是 NRZ 方波，没有音调。下面用真实调制
# 合成 IQ 走 .cf32 路由端到端，并给出「同一份 IQ 喂旧音调前端必失败」的
# 对照，防止把「音调自洽」当成「真实信号可解」。
# ─────────────────────────────────────────────────────────────────────

def _known_bits(address: int, message: str):
    """重建 pocsag_encode 的已知位流（供真实 FSK IQ 合成使用）。"""
    fn = P._detect_function(message)
    msg_cws = P._encode_message(message, fn)
    bits = [1 if (i & 1) == 0 else 0 for i in range(P.PREAMBLE_BITS)]
    frame_pos = address & 7
    sent = False
    idx = 0
    while True:
        for i in range(31, -1, -1):
            bits.append((P.POCSAG_SYNC >> i) & 1)
        for frame in range(8):
            for cw in range(2):
                if not sent and frame == frame_pos and cw == 0:
                    codeword = P._build_address_codeword(address, fn)
                    sent = True
                elif sent and idx < len(msg_cws):
                    codeword = msg_cws[idx]
                    idx += 1
                else:
                    codeword = P.POCSAG_IDLE
                for i in range(31, -1, -1):
                    bits.append((codeword >> i) & 1)
        if sent and idx >= len(msg_cws):
            return bits


def _real_nfm_iq(bits, fs: float, baud: int, deviation_hz: float = 4500.0,
                 carrier_offset_hz: float = 0.0, snr_db: float = None,
                 seed: int = 0) -> np.ndarray:
    """合成真实 NFM-FSK IQ（连续相位 ±deviation，不是 FM 鉴频的逆运算）。"""
    spb = fs / baud
    n = int(round(len(bits) * spb))
    bit_idx = np.clip((np.arange(n) / spb).astype(int), 0, len(bits) - 1)
    nrz = np.where(np.asarray(bits)[bit_idx] == 0, 1.0, -1.0)
    t = np.arange(n) / fs
    phase = 2 * np.pi * deviation_hz * np.cumsum(nrz) / fs
    iq = np.exp(1j * (phase + 2 * np.pi * carrier_offset_hz * t))
    if snr_db is not None:
        rng = np.random.default_rng(seed)
        p = float(np.mean(np.abs(iq) ** 2)) / (10.0 ** (snr_db / 10.0))
        iq = iq + (rng.normal(0.0, np.sqrt(p), n)
                   + 1j * rng.normal(0.0, np.sqrt(p), n))
    return iq


def _write_cf32(path, iq: np.ndarray) -> None:
    inter = np.empty(len(iq) * 2, dtype=np.float32)
    inter[0::2] = iq.real.astype(np.float32)
    inter[1::2] = iq.imag.astype(np.float32)
    inter.tofile(str(path))


def test_real_nfm_iq_route_yields_known_message(tmp_path):
    """真实 NFM-FSK IQ（fs=48k、±4.5kHz 频偏、SNR 20dB）→ .cf32 路由 → 已知消息。"""
    from mbdsdr_ai.decode_router import run_digital_mode

    fs = 48000.0
    path = tmp_path / "pocsag_nfm.cf32"
    _write_cf32(path, _real_nfm_iq(_known_bits(54321, "HELLO"), fs, 1200,
                                   snr_db=20.0))
    out = run_digital_mode(str(path), "pocsag", sample_rate=fs, baud=1200)

    assert out["ok"] is True, out
    assert out.get("error_code") in (None, ""), out
    assert out["num_messages"] == 1, out
    hit = out["messages"][0]
    assert hit["address"] == 54321, out
    assert hit["message"].rstrip() == "HELLO", out


def test_real_nfm_iq_survives_carrier_offset(tmp_path):
    """收机频偏（+100Hz）→ 鉴频输出带直流 → 基带切片仍能解。"""
    from mbdsdr_ai.decode_router import run_digital_mode

    fs = 48000.0
    path = tmp_path / "pocsag_nfm_offset.cf32"
    _write_cf32(path, _real_nfm_iq(_known_bits(99999, "12345"), fs, 1200,
                                   carrier_offset_hz=100.0))
    out = run_digital_mode(str(path), "pocsag", sample_rate=fs, baud=1200)

    assert out["ok"] is True, out
    assert out["num_messages"] == 1, out
    hit = out["messages"][0]
    assert hit["address"] == 99999, out
    assert hit["message"].rstrip() == "12345", out


def test_same_real_iq_fails_through_afsk_tone_frontend(tmp_path):
    """对照：同一份真实 IQ 走旧的 1200/2400 Hz 音调前端，必得 0 条。

    这条证明 `.cf32` 路由必须显式走 NRZ 基带（baseband=True），
    不是「两条路都能解」的重复测试。
    """
    from mbdsdr_ai.decoders import _demod_iq_to_audio

    fs = 48000.0
    path = tmp_path / "pocsag_nfm.cf32"
    _write_cf32(path, _real_nfm_iq(_known_bits(54321, "HELLO"), fs, 1200))
    audio, a_sr, err = _demod_iq_to_audio(str(path), fs)

    assert err is None, err
    assert pocsag_decode(audio, a_sr, 1200) == []       # 旧音调假设：失败
    hit = pocsag_decode(audio, a_sr, 1200, baseband=True)
    assert len(hit) == 1 and hit[0]["address"] == 54321
