# SPDX-License-Identifier: MIT
"""FT4 位、符号与软能量编解码。

依据公开协议资料独立实现，复用仓内 FT8 机制。协议事实取自 2020 年 QEX
《FT4 and FT8 Protocols》PDF 第 2、3、10 页：FT4 与 FT8 共用 77+14 CRC
和 LDPC(174,91)，但 FT4 在 CRC 前白化 77 位消息，接收端在 CRC 通过后
逆白化。两个码字位经 Gray 映射成为一个 4 音数据符号；87 个数据符号与
四组同步符号及首尾 ramp 组成 105 符号帧。

本模块只覆盖位/符号/软能量层，不做完整音频同步、GFSK 波形生成或音频
解调。CRC、LDPC 图、BP 译码和 77 位消息解包均复用仓内既有实现。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from mbdsdr_ai import ft8_ldpc
from mbdsdr_ai.ft8_decode import check_crc14
from mbdsdr_ai.ft8_encode import add_crc14, ldpc_encode_174_91
from mbdsdr_ai.ft8_unpack import unpack77

MESSAGE_BITS = 77
INFO_BITS = 91
CODEWORD_BITS = 174
DATA_SYMBOLS = 87
FRAME_SYMBOLS = 105

# QEX PDF 第 10 页给出的 77 位序列，按原文四行依次连接。
WHITENING_BITS = tuple(
    int(bit)
    for bit in (
        "0100101001011110100010"
        "0110110100101100001000"
        "1010011110010101010110"
        "11111000101"
    )
)
assert len(WHITENING_BITS) == MESSAGE_BITS

# QEX PDF 第 2 页表 3：00→0、01→1、11→2、10→3。
GRAY_MAP = (0, 1, 3, 2)
_INV_GRAY = tuple(GRAY_MAP.index(tone) for tone in range(4))

# QEX PDF 第 3 页：{R,S1,Ma,S2,Mb,S3,Mc,S4,R}。
SYNC_SEQUENCES = (
    (0, 1, 3, 2),
    (1, 0, 2, 3),
    (2, 3, 1, 0),
    (3, 2, 0, 1),
)
RAMP_TONE = 0
DATA_POSITIONS = (
    tuple(range(5, 34)) + tuple(range(38, 67)) + tuple(range(71, 100))
)
assert len(DATA_POSITIONS) == DATA_SYMBOLS

__all__ = [
    "MESSAGE_BITS",
    "INFO_BITS",
    "CODEWORD_BITS",
    "DATA_SYMBOLS",
    "FRAME_SYMBOLS",
    "WHITENING_BITS",
    "GRAY_MAP",
    "SYNC_SEQUENCES",
    "RAMP_TONE",
    "DATA_POSITIONS",
    "whiten_message",
    "unwhiten_message",
    "message_to_codeword",
    "codeword_to_data_tones",
    "encode_ft4_tones",
    "build_frame",
    "extract_data_tones",
    "soft_tones_to_llr",
    "codeword_parity_ok",
    "decode_ft4_payload",
]


def _numeric_array(values, name: str) -> np.ndarray:
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值数组") from exc
    if not bool(np.isfinite(array).all()):
        raise ValueError(f"{name} 不得包含 NaN 或 Inf")
    return array


def _bits(values, length: int, name: str) -> list[int]:
    array = _numeric_array(values, name)
    if array.shape != (length,):
        raise ValueError(f"{name} 形状必须为 ({length},)，实际为 {array.shape}")
    if not bool(((array == 0.0) | (array == 1.0)).all()):
        raise ValueError(f"{name} 只能包含 0 或 1")
    return array.astype(np.int8).tolist()


def _tones(values, length: int, name: str) -> list[int]:
    array = _numeric_array(values, name)
    if array.shape != (length,):
        raise ValueError(f"{name} 形状必须为 ({length},)，实际为 {array.shape}")
    if not bool((array == np.floor(array)).all()) or not bool(
        ((array >= 0.0) & (array <= 3.0)).all()
    ):
        raise ValueError(f"{name} 只能包含整数音调 0、1、2、3")
    return array.astype(np.int8).tolist()


def _energies(values) -> np.ndarray:
    array = _numeric_array(values, "软能量")
    if array.shape != (DATA_SYMBOLS, 4):
        raise ValueError(
            f"软能量形状必须为 ({DATA_SYMBOLS}, 4)，实际为 {array.shape}"
        )
    if bool((array < 0.0).any()):
        raise ValueError("软能量必须非负")
    return array


def whiten_message(message77: Sequence[int]) -> list[int]:
    """在 CRC/FEC 前对原始 77 位消息逐位异或官方白化序列。"""
    bits = _bits(message77, MESSAGE_BITS, "消息位")
    return [bit ^ mask for bit, mask in zip(bits, WHITENING_BITS)]


def unwhiten_message(whitened77: Sequence[int]) -> list[int]:
    """CRC 通过后逆白化；异或同一序列即恢复原始 77 位消息。"""
    bits = _bits(whitened77, MESSAGE_BITS, "白化消息位")
    return [bit ^ mask for bit, mask in zip(bits, WHITENING_BITS)]


def message_to_codeword(message77: Sequence[int]) -> list[int]:
    """原始 77 位消息经白化、CRC14、LDPC(174,91) 编成 174 位码字。"""
    whitened = whiten_message(message77)
    return ldpc_encode_174_91(add_crc14(whitened))


def codeword_to_data_tones(codeword174: Sequence[int]) -> list[int]:
    """174 位码字按 MSB→LSB 两位一组映射为 87 个 FT4 数据音调。"""
    bits = _bits(codeword174, CODEWORD_BITS, "码字")
    tones = []
    for index in range(DATA_SYMBOLS):
        word = (bits[2 * index] << 1) | bits[2 * index + 1]
        tones.append(GRAY_MAP[word])
    return tones


def encode_ft4_tones(message77: Sequence[int]) -> list[int]:
    """原始 77 位消息 → 87 个数据音调（不含同步和 ramp）。"""
    return codeword_to_data_tones(message_to_codeword(message77))


def build_frame(data_tones: Sequence[int]) -> list[int]:
    """87 个数据音调 → {R,S1,Ma,S2,Mb,S3,Mc,S4,R} 共 105 音调。"""
    tones = _tones(data_tones, DATA_SYMBOLS, "数据音调")
    return [
        RAMP_TONE,
        *SYNC_SEQUENCES[0],
        *tones[0:29],
        *SYNC_SEQUENCES[1],
        *tones[29:58],
        *SYNC_SEQUENCES[2],
        *tones[58:87],
        *SYNC_SEQUENCES[3],
        RAMP_TONE,
    ]


def extract_data_tones(frame: Sequence[int]) -> list[int]:
    """按协议位置从已对齐的 105 音调帧取出 87 个数据音调。"""
    tones = _tones(frame, FRAME_SYMBOLS, "帧音调")
    return [tones[position] for position in DATA_POSITIONS]


def soft_tones_to_llr(tone_energies: Sequence[Sequence[float]]) -> list[float]:
    """87×4 非负软能量 → 174 个 max-log LLR（正值偏向 bit 0）。

    音调先经逆 Gray 映射还原二进位组；每个位分别比较该位为 0 和 1
    的音调最大软能量。该函数不做音频同步，也不把软能量当作已通过 CRC
    的判决结果。
    """
    energies = _energies(tone_energies)
    llr: list[float] = []
    for symbol in range(DATA_SYMBOLS):
        for mask in (2, 1):
            zero_tones = [tone for tone in range(4) if not (_INV_GRAY[tone] & mask)]
            one_tones = [tone for tone in range(4) if _INV_GRAY[tone] & mask]
            energy0 = max(float(energies[symbol, tone]) for tone in zero_tones)
            energy1 = max(float(energies[symbol, tone]) for tone in one_tones)
            llr.append(energy0 - energy1)
    return llr


def codeword_parity_ok(codeword174: Sequence[int]) -> bool:
    """用既有 LDPC 图检查全部 83 个奇偶方程，不复制校验矩阵。"""
    bits = _bits(codeword174, CODEWORD_BITS, "码字")
    check_vars, _ = ft8_ldpc._parse_graph()
    return all(sum(bits[variable] for variable in variables) % 2 == 0
               for variables in check_vars)


def _failure(reason: str) -> dict:
    return {
        "ok": False,
        "rejected": True,
        "reject_reason": reason,
        "data_bits": None,
        "unpacked": None,
        "text": None,
        "codeword": None,
        "iters": 0,
        "parity_ok": False,
        "crc_ok": False,
    }


def decode_ft4_payload(tone_energies: Sequence[Sequence[float]],
                       max_iter: int = 30) -> dict:
    """软能量经 LDPC BP、奇偶/CRC 拒绝门、逆白化后解包 77 位消息。

    只有奇偶校验和 CRC14 均通过时 ``ok`` 才为真。合法但当前解包器不支持
    的消息类型仍保留已验证位，``text`` 为 None；不会编造文本。全零能量、
    未收敛码字和 CRC 错误均诚实拒绝。输入形状、有限性、非负性和取值严格
    校验，非法输入抛出 ValueError。
    """
    if isinstance(max_iter, bool) or not isinstance(max_iter, (int, np.integer)):
        raise ValueError("max_iter 必须是正整数")
    if max_iter < 1:
        raise ValueError("max_iter 必须是正整数")
    energies = _energies(tone_energies)
    if not bool((energies > 0.0).any()):
        return _failure("no_energy")

    llr = soft_tones_to_llr(energies)
    codeword, iterations = ft8_ldpc.ldpc_bp_decode(llr, max_iter=int(max_iter))
    parity_ok = codeword_parity_ok(codeword)
    crc_ok = bool(parity_ok and check_crc14(codeword[:INFO_BITS]))
    result = {
        "ok": False,
        "rejected": True,
        "reject_reason": "",
        "data_bits": None,
        "unpacked": None,
        "text": None,
        "codeword": codeword,
        "iters": iterations,
        "parity_ok": parity_ok,
        "crc_ok": crc_ok,
    }
    if not parity_ok:
        result["reject_reason"] = "ldpc_parity"
        return result
    if not crc_ok:
        result["reject_reason"] = "crc14"
        return result

    data_bits = unwhiten_message(codeword[:MESSAGE_BITS])
    unpacked = unpack77(data_bits)
    result.update({
        "ok": True,
        "rejected": False,
        "data_bits": data_bits,
        "unpacked": unpacked,
        "text": unpacked.get("text"),
    })
    return result
