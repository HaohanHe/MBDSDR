#!/usr/bin/env python3
"""
tests/minimodem_test.py
=======================
minimodem 真实移植 (mbdsdr_ai/minimodem_adapter.py) 的验证测试。

覆盖：
  1. FSK 调制→加噪(10dB)→解调 往返，比特正确率 >95%
  2. Bell 103 (300bps, mark 1270 / space 1070)
  3. Bell 202 (1200bps, mark 1200 / space 2200)
  4. ASCII 8N1 帧：起始位+8数据位+停止位编解码
  5. Baudot(ITA-2) 字母/数字换档往返正确
  6. mark/space 常量与 minimodem.c 源码一致

运行：python3 -m pytest tests/minimodem_test.py -v
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.minimodem_adapter import (  # noqa: E402
    FSKModem,
    ASCIIFrame,
    BaudotCodec,
    baudot_encode,
    baudot_decode,
    fsk_modulate,
    fsk_demodulate,
    minimodem_decode_audio,
)


# ---------------------------------------------------------------------
# 工具：加高斯噪声到指定 SNR(dB)
# ---------------------------------------------------------------------
def add_noise(signal: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    sig_p = np.mean(signal ** 2)
    noise_p = sig_p / (10.0 ** (snr_db / 10.0))
    return signal + rng.normal(0.0, np.sqrt(noise_p), size=len(signal))


# ---------------------------------------------------------------------
# 6. 常量与源码一致（来源: minimodem/src/minimodem.c:900-921）
# ---------------------------------------------------------------------
def test_constants_bell103_bell202():
    m103 = FSKModem(baud=300)
    # Bell 103: mark=1270, space=1070  (minimodem.c:913,917,919)
    assert m103.f_mark == pytest.approx(1270.0)
    assert m103.f_space == pytest.approx(1070.0)

    m202 = FSKModem(baud=1200)
    # Bell 202: mark=1200, space=2200 (minimodem.c:902,906,908)
    assert m202.f_mark == pytest.approx(1200.0)
    assert m202.f_space == pytest.approx(2200.0)


# ---------------------------------------------------------------------
# 4. ASCII 8N1 帧
# ---------------------------------------------------------------------
def test_ascii_frame_encode_decode():
    fr = ASCIIFrame()
    # 'A' = 0x41 → LSB first bits: 1,0,0,0,0,0,1,0
    frame = fr.encode_byte(0x41)
    # start=0, b0..b7, stop=1  (minimodem.c:96,103,110)
    assert frame[0] == 0            # start bit = space
    assert frame[-1] == 1           # stop bit = mark
    assert frame[1:9] == [1, 0, 0, 0, 0, 0, 1, 0]
    assert fr.decode_frame(frame) == 0x41

    for b in range(256):
        assert fr.decode_frame(fr.encode_byte(b)) == b


# ---------------------------------------------------------------------
# 1. FSK 往返，SNR=10dB，比特正确率 >95%
# ---------------------------------------------------------------------
def test_fsk_roundtrip_snr10():
    rng = np.random.default_rng(42)
    modem = FSKModem(baud=300)
    n_bits = 500
    tx_bits = rng.integers(0, 2, size=n_bits).tolist()
    audio = modem.modulate_bits(tx_bits)
    noisy = add_noise(audio, snr_db=10.0, rng=rng)
    rx_bits, conf = modem.demodulate_bits(noisy, n_bits=n_bits)
    # 丢弃末尾不足一帧的碎片
    k = min(len(tx_bits), len(rx_bits))
    ber = np.mean(np.array(tx_bits[:k]) != np.array(rx_bits[:k]))
    acc = 1.0 - ber
    assert acc > 0.95, f"bit accuracy {acc:.3f} <= 0.95 (conf={conf:.2f})"


# ---------------------------------------------------------------------
# 2. Bell 103 (300 bps) 端到端文本
# ---------------------------------------------------------------------
def test_bell103_text_roundtrip():
    fr = ASCIIFrame()
    modem = FSKModem(baud=300)   # Bell 103
    text = "HELLO BI4MIB 123"
    bits = fr.encode_bytes(text.encode("ascii"))
    audio = modem.modulate_bits(bits)
    rng = np.random.default_rng(7)
    noisy = add_noise(audio, snr_db=12.0, rng=rng)
    rx, conf = modem.demodulate_bits(noisy, n_bits=len(bits))
    out = fr.decode_bits(rx)
    assert out.decode("ascii", errors="replace").startswith("HELLO BI4MIB")


# ---------------------------------------------------------------------
# 3. Bell 202 (1200 bps) 端到端文本
# ---------------------------------------------------------------------
def test_bell202_text_roundtrip():
    fr = ASCIIFrame()
    modem = FSKModem(baud=1200)  # Bell 202
    text = "ABC 202"
    bits = fr.encode_bytes(text.encode("ascii"))
    audio = modem.modulate_bits(bits)
    rng = np.random.default_rng(11)
    noisy = add_noise(audio, snr_db=12.0, rng=rng)
    rx, conf = modem.demodulate_bits(noisy, n_bits=len(bits))
    out = fr.decode_bits(rx)
    assert out.decode("ascii", errors="replace").startswith("ABC 202")


# ---------------------------------------------------------------------
# 5. Baudot (ITA-2) 字母/数字换档
# ---------------------------------------------------------------------
def test_baudot_letters_figures():
    # 纯字母串
    bc = BaudotCodec()
    words = bc.encode_string("HELLO")
    back = BaudotCodec().decode_words(words)
    assert back == "HELLO"

    # 字母→数字→字母换档：应自动插入 FIGS/LTRS
    words2 = BaudotCodec().encode_string("AB12CD")
    back2 = BaudotCodec().decode_words(words2)
    assert back2 == "AB12CD"

    # 换档字确实出现：数字前必有 FIGS(0x1b)
    assert 0x1B in words2, "数字前应出现 FIGS 换档字"
    # uso：空格后回到字母态
    words3 = BaudotCodec().encode_string("A 1")
    back3 = BaudotCodec().decode_words(words3)
    assert back3 == "A 1"

    # 模块级便捷函数
    w = baudot_encode("TEST")
    assert baudot_decode(w) == "TEST"


# ---------------------------------------------------------------------
# 模块级函数 & minimodem_decode_audio 集成
# ---------------------------------------------------------------------
def test_module_level_functions():
    bits = [0, 1, 1, 0, 1, 0, 0, 1]
    audio = fsk_modulate(bits, baud=300)
    res = fsk_demodulate(audio, baud=300, n_bits=len(bits))
    assert res["bits"] == bits
    assert res["mark_freq"] == pytest.approx(1270.0)

    fr = ASCIIFrame()
    bits2 = fr.encode_bytes(b"OK")
    audio2 = FSKModem(baud=300).modulate_bits(bits2)
    out = minimodem_decode_audio(audio2, baud=300, codec="ascii")
    assert out["text"].startswith("OK")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
