# SPDX-License-Identifier: MIT
"""12.2 文件级重组失败统计 + 端到端 CRC —— 判据。

用合成 VCDU 序列注入已知故障，钉住三件事：
  1. 五类失败计数精确等于注入次数，成功成文数不受影响；
  2. 端到端 CRC（组成文件的每个 CP_PDU CRC 全过）对好帧不误报；
  3. 统计能从 ``LRITParser.stats()`` 一路带到 ``ToolResult_goes`` 的返回里。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.goes_lrit import (  # noqa: E402
    H_ANNOTATION,
    H_PRIMARY,
    LRITParser,
    ToolResult_goes,
    crc16_ccitt,
    VCDU_LEN,
)


# --------------------------------------------------------------------------- #
# 合成构造（与 tests/goes_lrit_test.py 同约定：TP_PDU = 6B 头 + 用户数据 + 2B CRC）
# --------------------------------------------------------------------------- #
def build_lrit_file(annotation: str = "GOES16_ABI.lrit",
                    file_type: int = 0,
                    image_data: bytes = b"") -> bytes:
    ann = bytearray([H_ANNOTATION])
    body = annotation.encode("ascii")
    ann += (3 + len(body)).to_bytes(2, "big")
    ann += body
    total_hdr = 16 + len(ann)
    out = bytearray([H_PRIMARY])
    out += (16).to_bytes(2, "big")
    out.append(file_type)
    out += total_hdr.to_bytes(4, "big")
    out += (len(image_data) * 8).to_bytes(8, "big")
    return bytes(out) + bytes(ann) + image_data


def build_tpdu(apid: int, seq_flag: int, seq_count: int,
               user_data: bytes, corrupt_crc: bool = False) -> bytes:
    hdr = bytearray(6)
    hdr[0] = (apid >> 8) & 0x07
    hdr[1] = apid & 0xFF
    hdr[2] = ((seq_flag & 0x03) << 6) | ((seq_count >> 8) & 0x3F)
    hdr[3] = seq_count & 0xFF
    hdr[4] = ((len(user_data) + 2 - 1) >> 8) & 0xFF
    hdr[5] = (len(user_data) + 2 - 1) & 0xFF
    crc = crc16_ccitt(bytes(user_data))
    out = bytes(hdr) + bytes(user_data) + crc.to_bytes(2, "big")
    if corrupt_crc:
        out = out[:-1] + bytes([out[-1] ^ 0xFF])
    return out


def build_vcdu(vcid: int, counter: int, mpdu_payload: bytes,
               fhp: int = 0) -> bytes:
    data = bytearray(VCDU_LEN)
    data[0] = 0x40                     # version=01
    data[1] = vcid & 0x3F
    data[2] = (counter >> 16) & 0xFF
    data[3] = (counter >> 8) & 0xFF
    data[4] = counter & 0xFF
    data[6] = (fhp >> 8) & 0x07
    data[7] = fhp & 0xFF
    data[8:8 + len(mpdu_payload)] = mpdu_payload
    return bytes(data)


def single_packet_vcdu(file_buf: bytes, vcid: int = 10, counter: int = 0,
                       apid: int = 0x100, corrupt_crc: bool = False) -> bytes:
    user = b"\xAA" * 10 + file_buf
    tpdu = build_tpdu(apid, seq_flag=3, seq_count=0,
                      user_data=user, corrupt_crc=corrupt_crc)
    return build_vcdu(vcid, counter, tpdu, fhp=0)


def two_packet_vcdus(file_buf: bytes, vcid: int = 10, counter: int = 0,
                     apid: int = 0x100, split: int = 60,
                     second_take: int | None = None,
                     corrupt_second: bool = False):
    first = b"\xAA" * 10 + file_buf[:split]
    tpdu1 = build_tpdu(apid, 1, 0, first)
    tail = file_buf[split:]
    if second_take is not None:
        tail = tail[:second_take]
    tpdu2 = build_tpdu(apid, 2, 1, tail, corrupt_crc=corrupt_second)
    return [build_vcdu(vcid, counter, tpdu1, fhp=0),
            build_vcdu(vcid, counter + 1, tpdu2, fhp=0)]


_ZERO_FAILURES = ("vcdus_missing", "continuity_breaks", "tpdu_crc_fail",
                  "session_crc_fail", "session_len_mismatch", "files_dropped")


def assert_failures(stats, **expected):
    for key in _ZERO_FAILURES:
        want = expected.get(key, 0)
        assert stats[key] == want, f"{key}: {stats[key]} != {want}"


# --------------------------------------------------------------------------- #
# 1) 好流不误报
# --------------------------------------------------------------------------- #
class TestCleanStream:
    def test_two_single_packet_files(self):
        parser = LRITParser()
        f1 = build_lrit_file("A.lrit", image_data=b"\x01" * 32)
        f2 = build_lrit_file("B.lrit", image_data=b"\x02" * 32)
        files = parser.parse_stream([
            single_packet_vcdu(f1, counter=0, apid=0x100),
            single_packet_vcdu(f2, counter=1, apid=0x101),
        ])
        assert len(files) == 2
        assert files[0] == f1 and files[1] == f2
        stats = parser.stats()
        assert stats["files_ok"] == 2
        assert_failures(stats)

    def test_two_packet_file_delivers_exact_bytes(self):
        parser = LRITParser()
        f = build_lrit_file("CROSS.lrit", image_data=bytes(range(256)))
        files = parser.parse_stream(two_packet_vcdus(f))
        assert files == [f]
        stats = parser.stats()
        assert stats["files_ok"] == 1
        assert_failures(stats)


# --------------------------------------------------------------------------- #
# 2) 五类失败逐项精确
# --------------------------------------------------------------------------- #
class TestFailureCounters:
    def test_continuity_gap_counts_missing_vcdus(self):
        parser = LRITParser()
        f_before = build_lrit_file("BEFORE.lrit", image_data=b"\x11" * 32)
        f_after = build_lrit_file("AFTER.lrit", image_data=b"\x22" * 32)
        files = parser.parse_stream([
            single_packet_vcdu(f_before, counter=0, apid=0x100),
            single_packet_vcdu(f_after, counter=5, apid=0x101),
        ])
        assert len(files) == 2          # 丢帧不牵连其它 APID 的成功文件
        stats = parser.stats()
        assert stats["files_ok"] == 2
        assert_failures(stats, vcdus_missing=4, continuity_breaks=1)

    def test_gap_kills_in_progress_file(self):
        parser = LRITParser()
        f = build_lrit_file("GAPPED.lrit", image_data=b"\x33" * 200)
        part1 = build_tpdu(0x100, 1, 0, b"\xAA" * 10 + f[:60])
        rest = build_tpdu(0x100, 2, 1, f[60:])
        files = parser.parse_stream([
            build_vcdu(10, 0, part1, fhp=0),
            build_vcdu(10, 3, rest, fhp=0),   # 中间丢 2 帧
        ])
        assert files == []
        stats = parser.stats()
        assert stats["files_ok"] == 0
        assert_failures(stats, vcdus_missing=2, continuity_breaks=1,
                        files_dropped=1)

    def test_bad_crc_single_packet(self):
        parser = LRITParser()
        f = build_lrit_file("BAD.lrit", image_data=b"\x44" * 32)
        files = parser.parse_stream([
            single_packet_vcdu(f, corrupt_crc=True)])
        assert files == []
        stats = parser.stats()
        assert stats["files_ok"] == 0
        assert_failures(stats, tpdu_crc_fail=1, files_dropped=1)

    def test_bad_crc_mid_file_kills_whole_file(self):
        parser = LRITParser()
        f = build_lrit_file("MID.lrit", image_data=b"\x55" * 200)
        files = parser.parse_stream(
            two_packet_vcdus(f, split=60, corrupt_second=True))
        assert files == []
        stats = parser.stats()
        assert stats["files_ok"] == 0
        # 端到端 CRC：坏包所在文件整份作废，不是只丢一个包
        assert_failures(stats, tpdu_crc_fail=1, session_crc_fail=1,
                        files_dropped=1)

    def test_length_mismatch(self):
        parser = LRITParser()
        f = build_lrit_file("SHORT.lrit", image_data=b"\x66" * 200)
        files = parser.parse_stream(
            two_packet_vcdus(f, split=60, second_take=10))
        assert files == []
        stats = parser.stats()
        assert stats["files_ok"] == 0
        assert_failures(stats, session_len_mismatch=1, files_dropped=1)

    def test_failures_do_not_taint_later_good_file(self):
        parser = LRITParser()
        bad = build_lrit_file("BAD.lrit", image_data=b"\x77" * 32)
        good = build_lrit_file("GOOD.lrit", image_data=b"\x88" * 32)
        files = parser.parse_stream([
            single_packet_vcdu(bad, counter=0, apid=0x100, corrupt_crc=True),
            single_packet_vcdu(good, counter=1, apid=0x101),
        ])
        assert files == [good]
        stats = parser.stats()
        assert stats["files_ok"] == 1
        assert_failures(stats, tpdu_crc_fail=1, files_dropped=1)


# --------------------------------------------------------------------------- #
# 3) 统计一路带到工具返回
# --------------------------------------------------------------------------- #
class TestToolResultCarriesStats:
    def test_goes_parse_result_has_reassembly(self):
        f = build_lrit_file("TOOL.lrit", image_data=b"\x99" * 32)
        vcdu = single_packet_vcdu(f)
        res = ToolResult_goes({"vcdus": [vcdu.hex()]}, is_hrit=False)
        assert res.success
        assert res.data["reassembly"]["files_ok"] == 1
        payload = json.loads(res.content)
        assert payload["reassembly"]["files_ok"] == 1
