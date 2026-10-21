# SPDX-License-Identifier: MIT
"""CCSDS 通用链工具的自环对拍（编码 → 比特 → 解码 → 帧字节）。

这条链是 ASTU-1（阿斯图友谊号，435.400 MHz BPSK 遥测）等 CCSDS TM 体制
任务的能力底座：ASM 同步 → K=7 r=1/2 卷积 → 255 字节 PN 解扰。
本文件用"造帧—解帧"对拍把每一级（ASM/卷积/解扰）与 IQ 通路一起钉住。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.ccsds_tools import (ccsds_decode_bits, ccsds_decode_iq,  # noqa: E402
                                    ccsds_encode_frame)


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def _payload(n=64):
    """确定性载荷（可打印 ASCII，便于预览断言）。"""
    return bytes((i * 7 + 13) % 26 + 65 for i in range(n))


def test_roundtrip_no_convolution():
    """不卷积：信息字节 → ASM+字节 → 解码应原样取回。"""
    data = _payload(32)
    enc = ccsds_encode_frame({"data": list(data), "use_viterbi": False,
                              "use_descramble": False,
                              "info_bits": len(data) * 8})
    dec = ccsds_decode_bits({"bits": enc["bits"], "use_viterbi": False,
                             "use_descramble": False,
                             "frame_bits_after_asm": len(data) * 8})
    assert dec["count"] == 1
    got = bytes.fromhex(dec["frames"][0]["hex"])
    assert got[:len(data)] == data


def test_roundtrip_with_scrambler():
    """开解扰：PN 表 XOR 自反，编码端扰码/解码端解扰后应还原。"""
    data = _payload(32)
    enc = ccsds_encode_frame({"data": list(data), "use_viterbi": False,
                              "use_descramble": True,
                              "info_bits": len(data) * 8})
    dec = ccsds_decode_bits({"bits": enc["bits"], "use_viterbi": False,
                             "use_descramble": True,
                             "frame_bits_after_asm": len(data) * 8})
    assert dec["count"] == 1
    got = bytes.fromhex(dec["frames"][0]["hex"])
    assert got[:len(data)] == data


def test_roundtrip_with_viterbi_and_scrambler():
    """K=7 r=1/2 卷积 + 解扰：全链自环应还原信息字节。"""
    data = _payload(32)
    tail = 6
    enc = ccsds_encode_frame({"data": list(data), "use_viterbi": True,
                              "use_descramble": True,
                              "info_bits": len(data) * 8,
                              "conv_tail_bits": tail})
    frame_bits = (len(data) * 8 + tail) * 2
    dec = ccsds_decode_bits({"bits": enc["bits"], "use_viterbi": True,
                             "use_descramble": True,
                             "frame_bits_after_asm": frame_bits})
    assert dec["count"] == 1
    got = bytes.fromhex(dec["frames"][0]["hex"])
    assert got[:len(data)] == data, f"got {got[:len(data)].hex()}"


def test_iq_path_bpsk():
    """IQ 通路：统一调制内核(BPSK+RRC) → CCSDS 链，应解回同一帧。"""
    from mbdsdr_ai.modulation import modulate_bits
    data = _payload(32)
    enc = ccsds_encode_frame({"data": list(data), "use_viterbi": False,
                              "use_descramble": False,
                              "info_bits": len(data) * 8})
    mod = modulate_bits(enc["bits"], "bpsk", sps=4, shaping="rrc", alpha=0.35)
    dec = ccsds_decode_iq({"iq": mod["waveform"].tolist(), "scheme": "bpsk",
                           "sps": 4, "shaping": "rrc", "alpha": 0.35,
                           "use_viterbi": False, "use_descramble": False,
                           "frame_bits_after_asm": len(data) * 8})
    assert dec["count"] == 1
    got = bytes.fromhex(dec["frames"][0]["hex"])
    assert got[:len(data)] == data


def test_tools_registered(reg):
    for name in ("ccsds_encode_frame", "ccsds_decode_bits", "ccsds_decode_iq"):
        assert name in reg.tools, name


def test_tool_roundtrip(reg):
    """走 MCP 工具面（模型实际调用的那条路）自环。"""
    data = _payload(16)
    e = reg.call("ccsds_encode_frame", {"data": list(data), "use_viterbi": False,
                                         "use_descramble": False,
                                         "info_bits": len(data) * 8})
    assert e.success is True, e.content[:120]
    d = reg.call("ccsds_decode_bits", {"bits": e.data["bits"], "use_viterbi": False,
                                       "use_descramble": False,
                                       "frame_bits_after_asm": len(data) * 8})
    assert d.success is True, d.content[:120]
    got = bytes.fromhex(d.data["frames"][0]["hex"])
    assert got[:len(data)] == data


def test_decode_without_frame_is_honest_empty(reg):
    """无有效帧时诚实返回 0 帧（不伪造）。"""
    d = reg.call("ccsds_decode_bits", {"bits": [1, 0] * 500, "use_viterbi": False,
                                       "use_descramble": False,
                                       "frame_bits_after_asm": 256})
    assert d.success is True
    assert d.data["count"] == 0
