# SPDX-License-Identifier: MIT
"""CCSDS TC BCH CLTU 纯软件编码。

依据 CCSDS 231.0-B-4 第 3、5、6 节：7 字节信息块使用修改
BCH(63,56)，生成多项式为 x^7 + x^6 + x^2 + 1（0xC5）。本模块只
返回 CLTU 字节，不生成 PLOP acquisition/idle 序列，也不执行发射。
"""

__all__ = ["tc_cltu_encode"]

_START_SEQUENCE = bytes.fromhex("EB90")
_TAIL_SEQUENCE = bytes.fromhex("C5C5C5C5C5C5C579")
_BCH_GENERATOR = 0xC5
_INFORMATION_OCTETS = 7
_FILL_OCTET = 0x55
_MAX_FRAME_OCTETS = 1024
_RANDOMIZER_TAPS = (2, 4, 5, 6, 7, 8)


def _bch_codeword(information: bytes) -> bytes:
    """将一个 7 字节信息块编码为 8 字节修改 BCH 码字。"""
    remainder = 0
    for octet in information:
        for shift in range(7, -1, -1):
            feedback = ((remainder >> 6) & 1) ^ ((octet >> shift) & 1)
            remainder = (remainder << 1) & 0x7F
            if feedback:
                remainder ^= _BCH_GENERATOR & 0x7F

    # 高 7 位为取反后的校验位；最低位 Filler Bit 恒为 0。
    error_control = ((~remainder) & 0x7F) << 1
    return information + bytes((error_control,))


def _randomize_frame(frame_bytes: bytes) -> bytes:
    """使用全 1 初态的 CCSDS 随机器异或帧字节，不处理填充。"""
    bit_count = len(frame_bytes) * 8
    sequence = [1] * 8
    while len(sequence) < bit_count:
        index = len(sequence)
        bit = 0
        for tap in _RANDOMIZER_TAPS:
            bit ^= sequence[index - tap]
        sequence.append(bit)

    mask = bytearray(len(frame_bytes))
    for index in range(len(frame_bytes)):
        offset = index * 8
        mask[index] = sum(
            sequence[offset + bit] << (7 - bit) for bit in range(8)
        )
    return bytes(octet ^ random_octet
                 for octet, random_octet in zip(frame_bytes, mask))


def tc_cltu_encode(frame_bytes: bytes, *, randomize: bool = False) -> bytes:
    """将一个非空 TC 帧编码为带 start/tail 的 BCH CLTU。

    ``frame_bytes`` 必须严格为 ``bytes``，长度为 1 至 1024 字节。
    ``randomize=True`` 时只随机化帧字节；随后分成 7 字节信息块，末块
    以未随机化的 0x55 填充。这与 CCSDS 230.1-G-3 Annex F 的示例一致。
    Start Sequence 和 Tail Sequence 从不随机化。
    """
    if type(frame_bytes) is not bytes:
        raise TypeError("frame_bytes 必须严格为 bytes")
    if type(randomize) is not bool:
        raise TypeError("randomize 必须严格为 bool")
    if not frame_bytes:
        raise ValueError("frame_bytes 不能为空")
    if len(frame_bytes) > _MAX_FRAME_OCTETS:
        raise ValueError("frame_bytes 不能超过 1024 字节")

    encoded_data = _randomize_frame(frame_bytes) if randomize else frame_bytes
    padded_length = (
        (len(encoded_data) + _INFORMATION_OCTETS - 1) // _INFORMATION_OCTETS
    ) * _INFORMATION_OCTETS
    padded = encoded_data + bytes((_FILL_OCTET,)) * (
        padded_length - len(encoded_data)
    )

    codewords = (
        _bch_codeword(padded[offset:offset + _INFORMATION_OCTETS])
        for offset in range(0, len(padded), _INFORMATION_OCTETS)
    )
    return _START_SEQUENCE + b"".join(codewords) + _TAIL_SEQUENCE
