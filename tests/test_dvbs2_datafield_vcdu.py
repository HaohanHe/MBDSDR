# SPDX-License-Identifier: MIT
"""tests/test_dvbs2_datafield_vcdu.py — DVB-S2 DATA FIELD→VCDU 重组钉扎（P5.1）。

标准原文出处（2026-11-04 亲验本机留存 PDF，不凭记忆）：
  * ETSI EN 302 307-1 V1.4.1（`standards/etsi-en-302-307-1/
    en_30230701v010401p.pdf` 第 19-20 页 §5.1.6）：BBHEADER 10 字节 80 bit，
    字段顺序 MATYPE(2B)/UPL(2B)/DFL(2B)/SYNC(1B)/SYNCD(2B)/CRC-8(1B)；
    SYNCD =「distance in bits from the beginning of the DATA FIELD and the
    first UP from this frame (first bit of the CRC-8)」，「SYNCD = 0D means
    that the first USER PACKET is aligned to the DATA FIELD」，65535D 表示
    「no UP starts in the DATA FIELD」。
  * 同标准 §5.1.4：BBHEADER CRC-8 覆盖前 9 字节，poly g(X)=
    X^8+X^7+X^6+X^4+X^2+1（0xD5），MSB-first，初值全零；CRC-8/DVB-S2 检查值
    "123456789" = 0xBC。
  * 同标准 §5.1.4/图 2：SYNCD 指向的是「首个完整 UP 的第一个 CRC-8 比特」。
    本仓把 VCDU 帧当作 UP 等价物（GCS 直灌场景），属**建模假设**，已在
    `dvbs2_datafield` 模块头写明，测试按该假设钉扎。
  * CCSDS TM Space Data Link Protocol（132.0-B）：VCDU 主头 6B 位布局
    （version(2)|scid(8)|vcid(6)|counter(24)|replay(1)|reserved(7)）、
    version=0b01、填充 VCID=63、ASM=0x1ACFFC1D。

钉扎层级：
  * 合法序列重组：编码侧 `file_to_vcdus` 互逆对拍（跨 DATA FIELD 切分）；
  * BBHEADER 真链：`feed_bbframe` 走 CRC-8 校验 + DFL 截取 + SYNCD 定位；
  * ASM 硬锁对齐（含跨字段边界）、SYNCD=65535 整段丢弃的诚实口径；
  * 帧计数器跳变如实计数（抽帧不脑补）、填充帧/非法 version/CRC 失败丢弃；
  * 无声明对齐只认「两帧主头同时合法」的弱证据并标记 assumed_aligned。

真样本缺口（诚实写明）：本文件全部为合成链自洽金样，尚无 DVB-S2
OTA 真录 IQ 走到 VCDU 层的样本。最小采样条件：卫星下行符号率级复基带
.cf32 ≥ 3 个 PLFRAME，且一帧 FEC 可解（QPSK 1/2、CFO ≪ 符号率/26）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import fengyun_sat as fy  # noqa: E402
from mbdsdr_ai.dvbs2_datafield import (  # noqa: E402
    ASM, SYNCD_NO_UP, DatafieldVcduReassembler, VCDU_FILL_VCID, _crc16_x25,
)
from mbdsdr_ai.fengyun_sat import file_to_vcdus  # noqa: E402

VCDU_LEN = 892


def _frame(scid=0x20, vcid=1, counter=0, version=1, payload=b"\xAB") -> bytes:
    """手工构造一帧 VCDU（主头 6B + 载荷），version 可指定。"""
    f = bytearray(VCDU_LEN)
    f[0] = ((version & 0x3) << 6) | ((scid >> 2) & 0x3F)
    f[1] = ((scid & 0x03) << 6) | (vcid & 0x3F)
    f[2] = (counter >> 16) & 0xFF
    f[3] = (counter >> 8) & 0xFF
    f[4] = counter & 0xFF
    f[5] = 0
    f[6:6 + len(payload)] = payload
    return bytes(f)


def _bbframe_bits(payload: bytes, syncd_bits: int = 0) -> np.ndarray:
    """按 ETSI §5.1.6 造 10B BBHEADER + DATA FIELD 的 BBFRAME 比特流。"""
    header = bytearray(10)
    header[0] = 0x40             # TS/GS=01、SIS、CCM、ISSYI/NPD=0、RO=00
    header[1] = 0
    header[2:4] = (188 * 8).to_bytes(2, "big")     # UPL
    header[4:6] = (len(payload) * 8).to_bytes(2, "big")   # DFL
    header[6] = 0                # SYNC
    header[7:9] = syncd_bits.to_bytes(2, "big")    # SYNCD
    header[9] = fy.dvbs2_bb_crc8(header[:9])
    return np.concatenate([
        np.unpackbits(np.frombuffer(bytes(header), dtype=np.uint8)),
        np.unpackbits(np.frombuffer(payload, dtype=np.uint8)),
    ])


# ---------------------------------------------------------------------------
# 协议锚点（标准原文核对值）
# ---------------------------------------------------------------------------
class TestProtocolAnchors:
    def test_header_fields_bit_exact(self):
        """CCSDS 主头位布局：version/scid/vcid/counter 逐位金样。"""
        r = DatafieldVcduReassembler()
        frames = r.feed(_frame(scid=0xAB, vcid=7, counter=0x234567),
                        syncd_bits=0)
        assert len(frames) == 1
        f = frames[0]
        assert f.version == 1
        assert f.spacecraft_id == 0xAB
        assert f.vcid == 7
        assert f.counter == 0x234567
        assert f.replay is False
        assert f.crc_ok is None

    def test_asm_value_anchor(self):
        assert ASM.hex() == "1acffc1d"      # CCSDS 同步字（与 LRPT 同值）

    def test_fill_vcid_anchor(self):
        assert VCDU_FILL_VCID == 63          # CCSDS 填充虚拟信道

    def test_syncd_no_up_anchor(self):
        assert SYNCD_NO_UP == 65535          # ETSI §5.1.6 原文

    def test_crc16_x25_check_value(self):
        """CRC-16/X.25 标准检查值（帧尾 FHEC 复用 ax25 同族实现）。"""
        assert _crc16_x25(b"123456789") == 0x906E

    def test_bb_crc8_check_value(self):
        """CRC-8/DVB-S2 标准检查值（单一来源 fengyun_sat，防复制漂移）。"""
        assert fy.dvbs2_bb_crc8(b"123456789") == 0xBC


# ---------------------------------------------------------------------------
# 合法序列重组
# ---------------------------------------------------------------------------
class TestValidSequence:
    def test_encoded_frames_roundtrip_across_chunked_datafields(self):
        """file_to_vcdus 造连续流 → 按 4016B（非 892 整数倍）切分喂入 →
        逐字节重组，计数连续。编码侧按 CCSDS AOS 造 version=1 帧，
        用默认（严格）重组器即可收。"""
        payload = bytes((i * 37) & 0xFF for i in range(4000))
        vcdus = file_to_vcdus(payload, scid=0x20, vcid=1)
        assert len(vcdus) >= 3
        assert all(((f[0] >> 6) & 0x3) == 1 for f in vcdus)
        stream = b"".join(vcdus)
        r = DatafieldVcduReassembler()
        out = []
        for i in range(0, len(stream), 4016):   # 跨 DATA FIELD 切分
            out += r.feed(stream[i:i + 4016])
        rep = r.flush()
        assert rep["frames"] == len(vcdus)
        assert rep["counter_jumps"] == 0
        assert rep["version_bad"] == 0
        assert [f.raw for f in out] == list(vcdus)
        assert [f.counter for f in out] == list(range(len(vcdus)))
        # 裸流无 ASM、无 SYNCD 声明 → 两帧弱证据假设对齐（如实标记）
        assert rep["alignment_basis"] == "assumed"
        assert rep["assumed_aligned"] is True
        assert rep["asm_locks"] == 0

    def test_asm_locked_alignment(self):
        """流前缀垃圾 + ASM（跨两个 DATA FIELD 喂入）→ ASM 硬锁对齐。"""
        body = _frame(counter=7) + _frame(counter=8)
        stream = b"\xDE\xAD\xBE" + ASM + body
        r = DatafieldVcduReassembler()
        out = r.feed(stream[:6])          # 第一个字段切在 ASM 中间
        assert out == []
        out += r.feed(stream[6:])
        rep = r.flush()
        assert rep["asm_locks"] == 1
        assert rep["alignment_basis"] == "asm"
        assert rep["assumed_aligned"] is False
        # 丢弃 = 3B 垃圾 + 4B ASM（框架开销非载荷）
        assert rep["bytes_dropped"] == 7
        assert len(out) == 2
        assert [f.counter for f in out] == [7, 8]

    def test_syncd_skip(self):
        """SYNCD=192bit：首段跳过 24B 后落在帧起点（标准声明对齐）。"""
        stream = b"\x00" * 24 + _frame(counter=1) + _frame(counter=2)
        r = DatafieldVcduReassembler()
        out = r.feed(stream, syncd_bits=24 * 8)
        assert len(out) == 2
        rep = r.flush()
        assert rep["bytes_dropped"] == 24
        assert rep["alignment_basis"] == "syncd"
        assert rep["assumed_aligned"] is False

    def test_bbframe_chain_end_to_end(self):
        """BBHEADER(80bit, CRC-8) + DATA FIELD → feed_bbframe 出 3 帧。"""
        payload = b"".join(_frame(counter=i) for i in range(3))
        r = DatafieldVcduReassembler()
        out = r.feed_bbframe(_bbframe_bits(payload, syncd_bits=0))
        rep = r.flush()
        assert [f.counter for f in out] == [0, 1, 2]
        assert rep["alignment_basis"] == "syncd"
        assert rep["residue_bytes"] == 0

    def test_bbframe_bad_crc8_raises_honestly(self):
        bits = _bbframe_bits(_frame(counter=1))
        bits[72:72 + 8] = np.zeros(8, dtype=np.uint8)   # 抹掉 CRC-8 字段
        with pytest.raises(ValueError, match="CRC-8"):
            DatafieldVcduReassembler().feed_bbframe(bits)

    def test_bbframe_syncd_no_up_drops_whole_field(self):
        """SYNCD=65535：本字段无帧起点 → 整段计入丢弃，不对齐；
        下一字段按自己的 SYNCD=0 正常出帧。"""
        junk = b"\x11" * (VCDU_LEN + 10)
        r = DatafieldVcduReassembler()
        assert r.feed_bbframe(_bbframe_bits(junk, SYNCD_NO_UP)) == []
        assert r.flush()["bytes_dropped"] == len(junk)
        good = b"".join(_frame(counter=i) for i in (5, 6))
        out = r.feed_bbframe(_bbframe_bits(good, syncd_bits=0))
        rep = r.flush()
        assert [f.counter for f in out] == [5, 6]
        assert rep["alignment_basis"] == "syncd"

    def test_bbframe_non_byte_dfl_rejected(self):
        """DFL 非整字节 → 字节级 VCDU 无法对齐，诚实抛错。"""
        bits = _bbframe_bits(_frame(counter=1))
        bad = np.append(bits, np.zeros(4, dtype=np.uint8))
        header = bytearray(np.packbits(bits[:80]).tobytes())
        header[4:6] = ((len(_frame()) * 8) + 4).to_bytes(2, "big")
        header[9] = fy.dvbs2_bb_crc8(header[:9])
        bad[:80] = np.unpackbits(np.frombuffer(bytes(header), dtype=np.uint8))
        with pytest.raises(ValueError, match="非整字节"):
            DatafieldVcduReassembler().feed_bbframe(bad)


# ---------------------------------------------------------------------------
# 帧计数器跳变 / 填充帧 / 非法头 / CRC
# ---------------------------------------------------------------------------
class TestIntegrity:
    def test_counter_jump_counted_not_invented(self):
        """抽掉中间一帧 → counter_jumps=1，其余帧照交（不脑补丢帧）。"""
        frames = [_frame(counter=i) for i in (1, 2, 4, 5)]
        r = DatafieldVcduReassembler()
        out = r.feed(b"".join(frames), syncd_bits=0)
        assert len(out) == 4
        assert r.flush()["counter_jumps"] == 1
        assert [f.counter for f in out] == [1, 2, 4, 5]

    def test_fill_frame_dropped(self):
        r = DatafieldVcduReassembler()
        out = r.feed(_frame(vcid=VCDU_FILL_VCID, counter=1)
                     + _frame(counter=2), syncd_bits=0)
        rep = r.flush()
        assert rep["fill_dropped"] == 1
        assert len(out) == 1 and out[0].counter == 2

    def test_bad_version_dropped(self):
        r = DatafieldVcduReassembler()       # 默认只收 version=1
        out = r.feed(_frame(version=2, counter=1) + _frame(counter=2),
                     syncd_bits=0)
        rep = r.flush()
        assert rep["version_bad"] == 1
        assert len(out) == 1 and out[0].counter == 2

    def test_crc_pass_and_fail(self):
        good = _frame(counter=1)
        body, crc = good[:-2], _crc16_x25(good[:-2])
        good = body + crc.to_bytes(2, "big")
        bad = bytearray(good)
        bad[-1] ^= 0x01                       # 翻转 CRC 末位 → 失败
        r = DatafieldVcduReassembler(expect_crc=True)
        out = r.feed(bytes(bad) + good, syncd_bits=0)
        rep = r.flush()
        assert rep["crc_failed"] == 1
        assert len(out) == 1
        assert out[0].crc_ok is True

    def test_replay_flag_parsed(self):
        f = bytearray(_frame(counter=3))
        f[5] = 0x80                           # replay flag = 1
        r = DatafieldVcduReassembler()
        out = r.feed(bytes(f), syncd_bits=0)
        assert out[0].replay is True

    def test_counter_wrap_24bit_ok(self):
        """24bit 计数器回绕（0xFFFFFF → 0）不算跳变。"""
        r = DatafieldVcduReassembler()
        out = r.feed(_frame(counter=0xFFFFFF) + _frame(counter=0),
                     syncd_bits=0)
        assert len(out) == 2
        assert r.flush()["counter_jumps"] == 0


# ---------------------------------------------------------------------------
# 输入纪律
# ---------------------------------------------------------------------------
class TestInputDiscipline:
    def test_residue_reported(self):
        r = DatafieldVcduReassembler()
        r.feed(_frame(counter=1) + b"\x01\x02\x03", syncd_bits=0)
        assert r.flush()["residue_bytes"] == 3

    def test_type_error_on_non_bytes(self):
        r = DatafieldVcduReassembler()
        with pytest.raises(TypeError):
            r.feed("not-bytes")

    def test_non_byte_syncd_rejected(self):
        """SYNCD 非整字节：字节级 VCDU 无法落在半字节，诚实抛错。"""
        with pytest.raises(ValueError, match="非整字节"):
            DatafieldVcduReassembler().feed(
                _frame(counter=1), syncd_bits=12)

    def test_syncd_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="越界"):
            DatafieldVcduReassembler().feed(
                _frame(counter=1), syncd_bits=70000)

    def test_no_claim_before_evidence(self):
        """未对齐且无证据时不吐帧（单帧不够判对齐）。"""
        r = DatafieldVcduReassembler()
        assert r.feed(_frame(counter=1)) == []
        rep = r.flush()
        assert rep["aligned"] is False and rep["frames"] == 0
