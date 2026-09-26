"""三层协议分析单测（移植自 URH AutoInterpretation + awre）。

上游对照：docs/learn/porting_2026_09_27.md §3
"""
import numpy as np

from mbdsdr_ai.protocol_analyzer import ProtocolAnalyzer
from mbdsdr_ai.analysis.protocol_parser import crc16_ccitt


def _ook_modulate(frame: bytes, sps: int = 20) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8))
    samples = np.repeat(bits, sps).astype(float)
    return samples.astype(complex)


def _make_frame(payload: bytes, sync: bytes = bytes([0xD3, 0x91])) -> bytes:
    body = sync + bytes([len(payload)]) + payload
    return body + crc16_ccitt(body).to_bytes(2, "big")


def test_ook_frame_parsed():
    """已知 OOK 信号 → 正确解出帧和字段，CRC 通过。"""
    payload = bytes([0x11, 0x22, 0x33, 0x44])
    frame = _make_frame(payload)
    iq = _ook_modulate(frame, sps=20)
    pa = ProtocolAnalyzer(sync_word=bytes([0xD3, 0x91]),
                          length_offset=0, length_bytes=1)
    frames = pa.analyze(iq, sample_rate=1.0, baud_hint=1.0 / 20)
    assert len(frames) >= 1
    f = frames[0]
    assert f.modulation == "OOK"
    assert f.crc_ok is True
    assert f.fields.get("LENGTH") == 4
    assert f.payload == payload


def test_sync_word_detected():
    """同步字应从比特流中识别出来。"""
    payload = bytes([0xAA, 0xBB])
    frame = _make_frame(payload, sync=bytes([0xD3, 0x91]))
    iq = _ook_modulate(frame, sps=20)
    pa = ProtocolAnalyzer(sync_word=bytes([0xD3, 0x91]),
                          length_offset=0, length_bytes=1)
    frames = pa.analyze(iq, 1.0, baud_hint=1.0 / 20)
    assert frames[0].sync_word.hex() == "d391"


def test_crc_detects_error():
    """CRC 不通过的帧应标记 crc_ok=False。"""
    payload = bytes([0x01, 0x02, 0x03, 0x04])
    frame = bytearray(_make_frame(payload))
    # 翻转 payload 中一个 bit
    frame[5] ^= 0x01
    iq = _ook_modulate(bytes(frame), sps=20)
    pa = ProtocolAnalyzer(sync_word=bytes([0xD3, 0x91]),
                          length_offset=0, length_bytes=1)
    frames = pa.analyze(iq, 1.0, baud_hint=1.0 / 20)
    # 解析仍能切出帧，但 CRC 应失败
    assert any(not f.crc_ok for f in frames)


def test_short_signal_returns_empty():
    """过短信号不应崩溃。"""
    pa = ProtocolAnalyzer()
    out = pa.analyze(np.zeros(10, dtype=complex), 1.0)
    assert out == []
