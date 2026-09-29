# SPDX-License-Identifier: MIT
"""METEOR LRPT 链单测（合成向量，非硬件 / NOT HARDWARE）。

依据 METEOR LRPT 公开格式与 CCSDS 标准。
"""
import numpy as np

from mbdsdr_ai.satellite.meteor_lrpt import (
    MeteorLRPTChain,
    qpsk_modulate,
    qpsk_demodulate,
    LRPT_ASYNC_WORD,
)


def test_sync_word_constant():
    """同步字应为 SatDump 非差分 QPSK 值。"""
    assert LRPT_ASYNC_WORD.hex() == "fca2b63db00d9794"


def test_qpsk_roundtrip():
    """QPSK 调制/硬判决解调自洽。"""
    rng = np.random.default_rng(0)
    data = bytes(rng.integers(0, 256, 100).tolist())
    syms = qpsk_modulate(data)
    out = qpsk_demodulate(syms)
    assert out == data


def test_lrpt_clean_roundtrip():
    """编码→QPSK→解调解扰→RS 解码，数据完全恢复。"""
    chain = MeteorLRPTChain()
    data = bytes(range(223))
    cw = chain.encode_block(data)
    syms = qpsk_modulate(cw)
    rx = qpsk_demodulate(syms)
    fr = chain.decode_block(rx[:255])
    assert fr.data == data
    assert fr.nerrors == 0


def test_lrpt_corrects_errors():
    """QPSK 解调后注入的字节错误可由 RS 纠正。"""
    chain = MeteorLRPTChain()
    rng = np.random.default_rng(7)
    data = bytes(rng.integers(0, 256, 223).tolist())
    cw = chain.encode_block(data)
    syms = qpsk_modulate(cw)
    # 加噪使部分 QPSK 判决翻转（对应 8 字节错误）
    noisy = syms + 0.3 * (rng.standard_normal(len(syms))
                          + 1j * rng.standard_normal(len(syms)))
    rx = qpsk_demodulate(noisy)
    fr = chain.decode_block(rx[:255])
    # 在 RS 纠错能力内应恢复
    assert fr.data == data, f"nerrors={fr.nerrors}"


def test_lrpt_stream_with_sync():
    """带同步字的连续字节流可被切块解码。"""
    chain = MeteorLRPTChain()
    data = bytes(range(223))
    cw = chain.encode_block(data)
    stream = LRPT_ASYNC_WORD + cw
    frames = chain.decode_stream(stream)
    assert len(frames) >= 1
    assert frames[0].data == data
