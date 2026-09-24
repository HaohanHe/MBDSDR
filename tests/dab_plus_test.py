#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/dab_plus_test.py — dablin 真实移植的往返验证

覆盖:
  1. FIC CRC: 合成 FIB -> CRC 校验通过；破坏 1 bit -> 失败
  2. FIG 1/0 (ensemble 标签): 合成 -> 解析出正确名称/短标签
  3. FIG 0/1 (子信道配置): 合成 EEP-A 子信道 -> 起始 CU/长度/保护/比特率正确
  4. ETI: 合成 6144 字节帧 -> FSYNC 同步 -> MNSC/STC/FIC 层解析正确
  5. 传输模式 I 参数验证

运行: python3 -m pytest tests/dab_plus_test.py -v   （或直接 python3 tests/dab_plus_test.py）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.dab_plus_lite import (  # noqa: E402
    crc16_ccitt,
    crc_fire_code,
    DABParams,
    FICDecoder,
    ETIParser,
    PARAMS,
    dab_fic_decode,
    dab_eti_parse,
    dab_decode_iq,
    dabplus_samplerate,
)


# ---------------------------------------------------------------------------
# 工具：合成一个通过 CRC 的 FIB（32 字节）
# ---------------------------------------------------------------------------
def build_fib(payload_bytes: bytes) -> bytes:
    """把 (<=30) 字节的 FIG 内容填进 32 字节 FIB，尾部 0xFF 填充 + CRC。"""
    assert len(payload_bytes) <= 30
    fib = bytearray(32)
    fib[:len(payload_bytes)] = payload_bytes
    for i in range(len(payload_bytes), 30):
        fib[i] = 0xFF  # 填充，让 FIB 解析循环提前结束 [fic_decoder.cpp:52]
    crc = crc16_ccitt(bytes(fib[:30]))
    fib[30] = (crc >> 8) & 0xFF
    fib[31] = crc & 0xFF
    return bytes(fib)


def build_fig1_ensemble_label(eid: int, label: str, short_mask: int) -> bytes:
    """合成 FIG 1/0 (ensemble 标签)。

    FIB 布局:
      [0]    FIG 长度字节: type=1, len = 1(header)+2(eid)+16(label)+2(mask) = 21
      [1]    FIG1 头: charset=0<<4 | oe=0 | ext=0
      [2..3] EId big-endian
      [4..19] 16 字节标签
      [20..21] 短标签掩码 big-endian
    来源: fic_decoder.cpp:581-644
    """
    raw = label.encode("ascii")[:16].ljust(16, b" ")
    length = 1 + 2 + 16 + 2
    out = bytearray()
    out.append((1 << 5) | length)     # type=1, len
    out.append(0x00)                   # charset=0, oe=0, ext=0
    out.append((eid >> 8) & 0xFF)
    out.append(eid & 0xFF)
    out += raw
    out.append((short_mask >> 8) & 0xFF)
    out.append(short_mask & 0xFF)
    return build_fib(bytes(out))


def build_fig0_1_subchannel(subchid: int, start_cu: int, size_cu: int) -> bytes:
    """合成 FIG 0/1 (EEP-A 子信道)。来源 fic_decoder.cpp:149-206。

    EEP-A option=000, pl=0 -> "EEP 1-A", bitrate = size/12*8。
    """
    length = 1 + 4   # 1 字节 FIG0 头 + 4 字节长表单条
    out = bytearray()
    out.append((0 << 5) | length)     # type=0, len
    out.append(0x01)                   # cn=0 oe=0 pd=0 ext=1
    # 条目字节 0: subchid<<2 | start_high2
    out.append((subchid << 2) | ((start_cu >> 8) & 0x03))
    out.append(start_cu & 0xFF)
    # 字节 2: long(0x80) | option(0<<4) | pl(0<<2) | size_high2
    out.append(0x80 | ((size_cu >> 8) & 0x03))
    out.append(size_cu & 0xFF)
    return build_fib(bytes(out))


# ---------------------------------------------------------------------------
# 工具：合成一个合法的 6144 字节 ETI(NI) 帧
# ---------------------------------------------------------------------------
def build_eti_frame(ficf: bool = True, mid: int = 1, nst: int = 1,
                    scid: int = 0, stl: int = 48,
                    fic_payload: bytes = b"") -> bytes:
    """按 dablin eti_player.cpp 布局合成 ETI 帧并填好两级 CRC。"""
    ficl = 32 if (ficf and mid == 3) else (24 if ficf else 0)
    fic_bytes = ficl * 4

    # fl: 使 MST CRC 区 = FIC 字节 + 子信道字节
    subch_bytes = stl * 8
    mst_crc_len = fic_bytes + subch_bytes
    fl = mst_crc_len // 4 + nst + 1

    frame = bytearray(6144)
    # ERR + FSYNC [eti_player.cpp:25-36]
    frame[0] = 0xFF
    fsync = PARAMS.fsync0
    frame[1] = (fsync >> 16) & 0xFF
    frame[2] = (fsync >> 8) & 0xFF
    frame[3] = fsync & 0xFF

    # MNSC (4 字节) [eti_player.cpp:43-46]
    frame[4] = 0x00
    frame[5] = (0x80 if ficf else 0x00) | (nst & 0x7F)
    frame[6] = ((mid & 0x03) << 3) | ((fl >> 8) & 0x07)
    frame[7] = fl & 0xFF

    # STC: nst 条 x 4 字节 [eti_player.cpp:83-85]
    for i in range(nst):
        frame[8 + i * 4] = (scid << 2) & 0xFC
        frame[8 + i * 4 + 1] = 0x00
        frame[8 + i * 4 + 2] = (stl >> 8) & 0x03
        frame[8 + i * 4 + 3] = stl & 0xFF

    # 头 CRC: 覆盖 frame[4 : 4 + (4+nst*4+2)]
    hdr_crc_len = 4 + nst * 4 + 2
    hdr_crc = crc16_ccitt(bytes(frame[4:4 + hdr_crc_len]))
    frame[4 + hdr_crc_len] = (hdr_crc >> 8) & 0xFF
    frame[4 + hdr_crc_len + 1] = hdr_crc & 0xFF

    # MST 区起始
    subch_offset = 4 + 4 + nst * 4 + 4
    # FIC 数据
    if fic_bytes:
        frame[subch_offset:subch_offset + min(len(fic_payload), fic_bytes)] = \
            fic_payload[:fic_bytes]
    # MST CRC: 覆盖 [subch_offset : subch_offset+mst_crc_len]
    mst_crc = crc16_ccitt(bytes(frame[subch_offset:subch_offset + mst_crc_len]))
    frame[subch_offset + mst_crc_len] = (mst_crc >> 8) & 0xFF
    frame[subch_offset + mst_crc_len + 1] = mst_crc & 0xFF
    return bytes(frame)


# ===========================================================================
# 测试
# ===========================================================================
def test_crc_known_vector():
    """CRC 与标准 CRC-16/CCITT-FALSE 校验值对齐（dablin 末尾取反）。"""
    # 标准 CCITT-FALSE(init=0xFFFF,xorout=0) 对 '123456789' = 0x29B1；
    # dablin final_invert -> 0xD64E。
    assert crc16_ccitt(b"123456789") == 0xD64E


def test_fic_crc_pass_and_fail():
    """合成 FIB CRC 通过；破坏 1 bit 后必须被丢弃。"""
    fib = build_fig1_ensemble_label(0x1234, "MBDSDR DAB+", 0xFFFF)
    stored = (fib[30] << 8) | fib[31]
    assert crc16_ccitt(fib[:30]) == stored, "合成 FIB 的 CRC 应自洽通过"

    dec = FICDecoder()
    dec.process(fib)
    assert dec.discarded_fibs == 0, "CRC 正确的 FIB 不应被丢弃"

    # 破坏 1 bit
    bad = bytearray(fib)
    bad[5] ^= 0x01
    dec2 = FICDecoder()
    dec2.process(bytes(bad))
    assert dec2.discarded_fibs == 1, "破坏 1 bit 后 FIB 必须被 CRC 丢弃"


def test_fig1_ensemble_label():
    """合成 FIG 1/0 ensemble 标签 -> 解析出正确名称与短标签。"""
    fib = build_fig1_ensemble_label(0x1234, "MBDSDR DAB+", 0xF000)
    dec = FICDecoder()
    dec.process(fib)
    assert dec.ensemble.eid == 0x1234, f"EId 解析错误: {dec.ensemble.eid:#06x}"
    assert dec.ensemble.label == "MBDSDR DAB+", f"ensemble 标签错误: {dec.ensemble.label!r}"
    # 短标签掩码 0xF000 -> 高 4 位置位 -> 保留前 4 字符 "MBDS"
    assert dec.ensemble.short_label == "MBDS", \
        f"短标签错误: {dec.ensemble.short_label!r}"


def test_fig0_1_subchannel():
    """合成 FIG 0/1 EEP-A 子信道 -> 起始 CU/长度/保护/比特率正确。"""
    # size=48, EEP-A factor=12 -> bitrate = 48/12*8 = 32 kbps
    fib = build_fig0_1_subchannel(subchid=2, start_cu=0x18, size_cu=48)
    dec = FICDecoder()
    dec.process(fib)
    sc = dec.subchannels[2]
    assert sc.start_cu == 0x18, f"start CU 错误: {sc.start_cu}"
    assert sc.size_cu == 48, f"size CU 错误: {sc.size_cu}"
    assert sc.protection == "EEP 1-A", f"保护级别错误: {sc.protection}"
    assert sc.bitrate_kbps == 32, f"比特率错误: {sc.bitrate_kbps} kbps"


def test_eti_sync_and_layers():
    """合成 ETI 帧 -> 找到 FSYNC -> MNSC/STC/FIC 层解析正确。"""
    # FIC 内容：一个 ensemble 标签 FIB
    fic_fib = build_fig1_ensemble_label(0xABCD, "DABLIN LITE", 0xFFFF)
    # ficl=24 -> 96 字节 FIC = 3 个 FIB；只放 1 个有效 FIB，其余 0（CRC 失败被丢）
    fic_payload = fic_fib + b"\x00" * (96 - len(fic_fib))

    frame = build_eti_frame(ficf=True, mid=1, nst=1, scid=0, stl=48,
                            fic_payload=fic_payload)

    # 同步检测
    pos = ETIParser.find_sync(frame)
    assert pos == 0, f"应在偏移 0 找到 FSYNC，实际 {pos}"

    info = ETIParser().parse(frame)
    assert info.ok, "ETI 帧两级 CRC 应通过"
    assert info.fsync == PARAMS.fsync0
    assert info.nst == 1
    assert info.mid == 1
    assert info.ficf is True
    assert info.ficl == 24, f"ficl 应为 24(mid=1)，实际 {info.ficl}"
    assert info.subchannels[0] == 48 * 8, \
        f"子信道字节数应为 {48*8}，实际 {info.subchannels.get(0)}"

    # FIC 内容应被解析出 ensemble 标签
    fic_out = dab_eti_parse(frame)
    assert fic_out["ok"]
    assert fic_out["fic"]["ensemble"]["eid"] == 0xABCD
    assert fic_out["fic"]["ensemble"]["label"] == "DABLIN LITE"


def test_eti_bad_sync_rejected():
    """破坏 FSYNC 后不应同步。"""
    frame = bytearray(build_eti_frame())
    frame[1] ^= 0xFF  # 破坏 FSYNC
    assert ETIParser.find_sync(bytes(frame)) < 0
    assert ETIParser().parse(bytes(frame)).ok is False


def test_mode_i_params():
    """传输模式 I 常量验证。"""
    p = DABParams()
    assert p.fft_size == 2048
    assert p.cp_us == 246
    assert p.symbol_us == 1000
    assert p.symbols_per_frame == 76
    assert p.eti_frame_bytes == 6144
    assert p.fsync0 == 0x073AB6 and p.fsync1 == 0xF8C549


def test_dabplus_samplerate():
    """DAB+ 超帧格式字节 -> 采样率 (dabplus_decoder.h)。"""
    assert dabplus_samplerate(0x00) == 32   # dac_rate=0,sbr=0 -> 32
    assert dabplus_samplerate(0x40) == 48   # dac_rate=1,sbr=0 -> 48
    assert dabplus_samplerate(0x20) == 16   # dac_rate=0,sbr=1 -> 16
    assert dabplus_samplerate(0x60) == 24   # dac_rate=1,sbr=1 -> 24


def test_highlevel_decode_iq():
    """dab_decode_iq 端到端：加噪声前缀 -> 仍能找到同步并解码 FIC。"""
    fic_fib = build_fig1_ensemble_label(0x0099, "ROUNDTRIP", 0xFFFF)
    fic_payload = fic_fib + b"\x00" * (96 - len(fic_fib))
    frame = build_eti_frame(ficf=True, mid=1, nst=1, scid=0, stl=48,
                            fic_payload=fic_payload)
    stream = b"\xAA\xBB\xCC" + frame   # 前缀杂散字节
    res = dab_decode_iq(stream)
    assert res["ok"]
    assert res["sync_offset"] == 3
    assert res["frame"]["ok"]
    assert res["frame"]["fic"]["ensemble"]["label"] == "ROUNDTRIP"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
        passed += 1
    print(f"\nAll {passed} dab_plus roundtrip tests passed.")
