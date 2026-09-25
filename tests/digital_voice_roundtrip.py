#!/usr/bin/env python3
"""
tests/digital_voice_roundtrip.py — 数字语音/集群无线电往返测试。

覆盖：
  1. M17 CRC16 往返（已知数据 -> CRC -> 追加 FCS -> 二次校验余数 0）
     已知向量: 'A' -> 0x206E, '123456789' -> 0x772B (m17-cxx-demod CRC16Test.cpp)
  2. M17 卷积码编解码往返（K=5, rate 1/2, g1=0o31, g2=0o27, Viterbi 硬判决）
  3. M17 4FSK 调制解调位流往返（bit -> 符号 -> 48ksps 复数 FSK -> 鉴频 -> bit）
  4. P25 NID 解码（NAC=0x3F5, DUID=LDU1=5 编码 -> BCH 校验通过 -> 还原 NAC/DUID）
  5. sdrtrunk 信道状态机切换（grant -> CALL, affiliation, 非法转换被拒）
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np

from mbdsdr_ai.m17_adapter import (
    M17CRC16, m17_crc16, m17_conv_encode, m17_viterbi_decode,
    m17_bits_to_symbols, m17_symbols_to_bits,
    m17_fsk_modulate, m17_fsk_demodulate,
    m17_build_lsf, m17_verify_lsf, M17_SYMBOL_RATE, M17_FREQ_DEVIATION,
)
from mbdsdr_ai.op25_adapter import (
    p25_encode_nid, p25_decode_nid, P25_DUID_LDU1, P25_DUID_NAMES,
)
from mbdsdr_ai.sdrtrunk_adapter import (
    TrunkingSystem, ChannelState, _ALLOWED,
)


# ═══════════════════════════════════════════════════════════════════════
#  1. M17 CRC16 往返
# ═══════════════════════════════════════════════════════════════════════
def test_m17_crc_known_vectors():
    """已知向量来自 m17-cxx-demod tests/CRC16Test.cpp:27-47。"""
    assert m17_crc16(b"A") == 0x206E, f"'A' -> 0x{m17_crc16(b'A'):04X} != 0x206E"
    assert m17_crc16(b"123456789") == 0x772B, \
        f"'123456789' -> 0x{m17_crc16(b'123456789'):04X} != 0x772B"
    print("  M17 CRC 已知向量: 'A'=0x206E, '123456789'=0x772B OK")


def test_m17_crc_roundtrip():
    """任意数据 -> CRC -> 追加 FCS -> 二次校验余数必须为 0。"""
    for data in (b"", b"\x00", b"\xff" * 16, b"hello m17", bytes(range(256))):
        c = M17CRC16()
        c.update(data)
        fcs = c.digest()
        c2 = M17CRC16()
        c2.update(data + fcs.to_bytes(2, "big"))
        residual = c2.digest()
        assert residual == 0, f"data={data[:16]!r} residual=0x{residual:04X}"
    print("  M17 CRC 往返: 5 组数据追加 FCS 后余数均为 0 OK")


# ═══════════════════════════════════════════════════════════════════════
#  2. M17 卷积码编解码往返
# ═══════════════════════════════════════════════════════════════════════
def test_m17_conv_roundtrip():
    """随机 bit -> conv_encode -> Viterbi 硬判决译码 -> 应完全还原。"""
    for n_bits in (8, 16, 32, 64, 128):
        rng = np.random.default_rng(n_bits)
        info = rng.integers(0, 2, size=n_bits).tolist()
        # 打包成字节
        info_bytes = bytearray((n_bits + 7) // 8)
        for i, b in enumerate(info):
            if b:
                info_bytes[i // 8] |= 1 << (7 - (i % 8))
        coded = m17_conv_encode(bytes(info_bytes))
        coded_bits = [(coded[i // 8] >> (7 - (i % 8))) & 1
                      for i in range(len(coded) * 8)]
        decoded = m17_viterbi_decode(coded_bits, n_bits)
        errs = sum(a ^ b for a, b in zip(decoded, info))
        assert errs == 0, f"n_bits={n_bits} errors={errs}"
    print("  M17 卷积码: 8/16/32/64/128 bit 无噪往返全对 OK")


# ═══════════════════════════════════════════════════════════════════════
#  3. M17 4FSK 调制解调位流往返
# ═══════════════════════════════════════════════════════════════════════
def test_m17_4fsk_roundtrip():
    """bit -> dibit -> 4FSK 符号 -> 复数基带 -> 鉴频 -> 硬判决 -> bit。"""
    for n_bits, seed in ((96, 1), (192, 2), (384, 3)):
        rng = np.random.default_rng(seed)
        bits = rng.integers(0, 2, size=n_bits).tolist()
        syms = m17_bits_to_symbols(bits)
        iq = m17_fsk_modulate(syms, sample_rate=48000.0,
                              symbol_rate=M17_SYMBOL_RATE,
                              deviation=M17_FREQ_DEVIATION)
        rx_syms = m17_fsk_demodulate(iq, sample_rate=48000.0,
                                     symbol_rate=M17_SYMBOL_RATE,
                                     deviation=M17_FREQ_DEVIATION)
        m = min(len(syms), len(rx_syms))
        rx_bits = m17_symbols_to_bits(rx_syms[:m])
        errs = sum(a ^ b for a, b in zip(bits[:2 * m], rx_bits))
        assert errs == 0, f"n_bits={n_bits} bit_errors={errs}"
    print("  M17 4FSK: 96/192/384 bit 无噪往返全对 OK")


# ═══════════════════════════════════════════════════════════════════════
#  4. P25 NID 解码
# ═══════════════════════════════════════════════════════════════════════
def test_p25_nid_decode():
    """NAC=0x3F5, DUID=LDU1(5) -> encode -> decode 应还原且 BCH 校验通过。"""
    for nac, duid in ((0x3F5, P25_DUID_LDU1),
                      (0xABC, 0),
                      (0x000, 7),
                      (0xFFF, 15)):
        word = p25_encode_nid(nac, duid)
        nac2, duid2, ok = p25_decode_nid(word)
        assert ok, f"NAC=0x{nac:03X} DUID={duid} BCH 校验失败"
        assert nac2 == nac, f"NAC 不匹配: {nac2:03X} != {nac:03X}"
        assert duid2 == duid, f"DUID 不匹配: {duid2} != {duid}"
    # 已知模式：NAC=0x3F5 DUID=5 的字应能被解码为 LDU1
    word = p25_encode_nid(0x3F5, P25_DUID_LDU1)
    nac, duid, ok = p25_decode_nid(word)
    assert ok and nac == 0x3F5 and duid == P25_DUID_LDU1
    print(f"  P25 NID: 0x{word:016X} -> NAC=0x{nac:03X} DUID={duid}({P25_DUID_NAMES[duid]}) OK")


# ═══════════════════════════════════════════════════════════════════════
#  5. sdrtrunk 信道状态机切换
# ═══════════════════════════════════════════════════════════════════════
def test_sdrtrunk_state_machine():
    """grant -> CALL, affiliation, group_update, 非法转换被拒。"""
    sys = TrunkingSystem("test", control_frequency=851000000)
    # 初始控制信道应为 CONTROL
    assert sys.control_channel.state == ChannelState.CONTROL
    # 收到 grant -> 业务信道 CALL
    ch = sys.on_channel_grant(tg=1234, source=1001, freq=852000000, channel_id=1)
    assert ch.state == ChannelState.CALL, ch.state
    # 两个单位 affiliation
    sys.on_affiliation(tg=1234, unit=1001)
    sys.on_affiliation(tg=1234, unit=1002)
    assert sys.talkgroups[1234].affiliations == [1001, 1002]
    # group update
    sys.on_group_update(tg=1234, source=1001, freq=852000000)
    # 非法转换：CONTROL -> CALL 应被拒（State.java:72-81）
    ch2 = TrunkedChannel_ = None
    from mbdsdr_ai.sdrtrunk_adapter import TrunkedChannel
    ctrl = TrunkedChannel(0, 851000000, is_control=True)
    ctrl.set_state(ChannelState.CONTROL)
    assert not ctrl.set_state(ChannelState.CALL), "CONTROL->CALL 应被拒"
    # CALL -> FADE 合法
    assert ch.set_state(ChannelState.FADE)
    # FADE -> TEARDOWN 合法
    assert ch.set_state(ChannelState.TEARDOWN)
    # TEARDOWN -> RESET 合法
    assert ch.set_state(ChannelState.RESET)
    # RESET -> IDLE 合法
    assert ch.set_state(ChannelState.IDLE)
    # 快照
    snap = sys.snapshot()
    assert "traffic_channels" in snap
    print("  sdrtrunk: grant->CALL, 2 affiliation, CONTROL->CALL 被拒, CALL->FADE->TEARDOWN->RESET->IDLE OK")


# ═══════════════════════════════════════════════════════════════════════
#  附加：M17 LSF 构造校验
# ═══════════════════════════════════════════════════════════════════════
def test_m17_lsf():
    lsf = m17_build_lsf("W9GL", "")
    ok, src, dst = m17_verify_lsf(lsf)
    assert ok, "LSF CRC 校验失败"
    assert src == "W9GL", f"src={src}"
    assert dst == "BROADCAST", f"dst={dst}"
    print(f"  M17 LSF: src={src} dst={dst} CRC OK")


# ═══════════════════════════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("Digital Voice / Trunking Roundtrip Tests")
    print("=" * 60)
    results = []

    tests = [
        ("M17 CRC16 known vectors", test_m17_crc_known_vectors),
        ("M17 CRC16 roundtrip",    test_m17_crc_roundtrip),
        ("M17 convolutional codec roundtrip", test_m17_conv_roundtrip),
        ("M17 4FSK mod/demod roundtrip", test_m17_4fsk_roundtrip),
        ("P25 NID decode",         test_p25_nid_decode),
        ("sdrtrunk channel state machine", test_sdrtrunk_state_machine),
        ("M17 LSF build/verify",    test_m17_lsf),
    ]
    for name, fn in tests:
        print(f"\n[ {name} ]")
        try:
            fn()
            results.append((name, True))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  FAIL: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n  Total: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
