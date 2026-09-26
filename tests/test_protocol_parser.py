"""ProtocolParser 单测。

合成含同步字 + 长度 + CRC-16/CCITT 的数据包，验证解析正确。
对照 URH ``awre/engines/LengthEngine.py`` 与 ``util/GenericCRC.py:49``。
"""
import numpy as np

from mbdsdr_ai.analysis.protocol_parser import (
    ProtocolParser,
    ProtocolField,
    FieldType,
    crc16_ccitt,
    bytes_to_bits,
    infer_field_boundaries,
)


def _build_packet(sync: bytes, data: bytes) -> bytes:
    """构造 packet: sync + length(1B=data_len) + data + CRC16(2B)。"""
    length = bytes([len(data)])
    payload = sync + length + data
    crc = crc16_ccitt(payload)
    return payload + crc.to_bytes(2, "big")


def test_crc16_ccitt_known_vector():
    """CRC-16/CCITT-FALSE 已知测试向量。"""
    # "123456789" 的 CRC-16/CCITT-FALSE = 0x29B1
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_parse_sync_length_data_crc():
    sync = bytes([0xAA, 0xAA])
    data = bytes([0x01, 0x02, 0x03, 0x04])
    packet = _build_packet(sync, data)
    bits = bytes_to_bits(packet)

    parser = ProtocolParser(sync_word=sync, length_offset=0, length_field_bytes=1)
    msgs = parser.parse(bits)
    assert len(msgs) >= 1
    msg = msgs[0]
    assert msg.valid_checksum is True

    names = [f.name for f in msg.fields]
    assert "SYNC" in names
    assert "LENGTH" in names
    assert "DATA" in names
    assert "CRC" in names

    length_field = msg.field("LENGTH")
    assert length_field.value == len(data)

    data_field = msg.field("DATA")
    assert data_field.value == int.from_bytes(data, "big")


def test_crc_false_detected():
    """篡改数据后 CRC 应失败。"""
    sync = bytes([0xAA, 0xAA])
    data = bytes([0x01, 0x02, 0x03, 0x04])
    packet = bytearray(_build_packet(sync, data))
    # 篡改一个数据字节
    packet[4] ^= 0xFF
    bits = bytes_to_bits(bytes(packet))

    parser = ProtocolParser(sync_word=sync, length_offset=0, length_field_bytes=1)
    msgs = parser.parse(bits)
    assert len(msgs) >= 1
    assert msgs[0].valid_checksum is False


def test_multiple_packets():
    """比特流中包含多个包，应全部解析。"""
    sync = bytes([0xAA, 0xAA])
    p1 = _build_packet(sync, bytes([0x01, 0x02]))
    p2 = _build_packet(sync, bytes([0x03, 0x04, 0x05]))
    stream = np.concatenate([bytes_to_bytes(p1) if False else bytes_to_bits(p1),
                             bytes_to_bits(p2)])

    parser = ProtocolParser(sync_word=sync, length_offset=0, length_field_bytes=1)
    msgs = parser.parse(stream)
    assert len(msgs) >= 2
    for m in msgs:
        assert m.valid_checksum is True


def test_field_types():
    sync = bytes([0xAA, 0xAA])
    packet = _build_packet(sync, bytes([0x01, 0x02]))
    bits = bytes_to_bits(packet)
    parser = ProtocolParser(sync_word=sync, length_offset=0, length_field_bytes=1)
    msg = parser.parse(bits)[0]

    assert msg.field("SYNC").type == FieldType.SYNC
    assert msg.field("LENGTH").type == FieldType.LENGTH
    assert msg.field("DATA").type == FieldType.DATA
    assert msg.field("CRC").type == FieldType.CHECKSUM


def test_auto_detect_sync():
    """无已知同步字时，自动检测重复 16-bit 模式。"""
    sync = bytes([0xAA, 0xAA])
    p1 = _build_packet(sync, bytes([0x01, 0x02]))
    p2 = _build_packet(sync, bytes([0x03, 0x04]))
    stream = np.concatenate([bytes_to_bits(p1), bytes_to_bits(p2)])

    parser = ProtocolParser(sync_word=None, length_offset=0, length_field_bytes=1)
    msgs = parser.parse(stream)
    # 至少应解析出一个包
    assert len(msgs) >= 1


def test_infer_field_boundaries():
    """AI 增强：熵分析找字段边界。"""
    # 构造 3 条消息，前 16 bit 同步字固定，后面随机
    rng = np.random.RandomState(0)
    sync_bits = np.array([1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 0])
    msgs = []
    for _ in range(5):
        rest = rng.randint(0, 2, 32)
        msgs.append(np.concatenate([sync_bits, rest]))
    arr = np.array(msgs)

    boundaries = infer_field_boundaries(arr)
    # 在第 16 bit 附近应有一个边界（同步字→数据）
    near_sync = any(abs(b[0] - 16) <= 4 for b in boundaries)
    assert near_sync, f"未在 16bit 附近找到边界: {boundaries}"
