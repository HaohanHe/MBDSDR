"""
goestools GOES LRIT/HRIT 移植验证测试
=====================================

验证 mbdsdr_ai/goes_lrit.py 真实复现 goestools 协议栈：
  - 同步字 0x1ACFFC1D 检测
  - VCDU 892B 字段解析（VCID/SCID/counter）
  - M_PDU 第一头指针 + CCSDS TP_PDU 切包
  - CRC-16/CCITT 校验
  - 按 APID 重组 SessionPDU（跨 VCDU、跨 TP_PDU）
  - LRIT 文件头解析（Primary/Annotation/SegmentIdentification）
  - 图像段重组为完整灰度图

运行: python3 -m pytest tests/goes_lrit_test.py -v
  或: python3 tests/goes_lrit_test.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.goes_lrit import (  # noqa: E402
    SYNC_WORD,
    SYNC_WORD_BYTES,
    FRAME_DATA_BYTES,
    FRAME_BYTES,
    VCDU_LEN,
    LRITParser,
    HRITParser,
    VCDU,
    TransportPDU,
    parse_lrit_headers,
    crc16_ccitt,
    derandomize,
    DERANDOM_TABLE,
    GOESImageDecoder,
    H_PRIMARY,
    H_ANNOTATION,
    H_SEGMENT_ID,
    H_IMAGE_STRUCTURE,
    H_RICE_COMPRESSION,
)


# ---------------------------------------------------------------------------
# 构造辅助：合成 LRIT 文件 / TP_PDU / VCDU
# ---------------------------------------------------------------------------

def build_lrit_primary(file_type: int, total_header_len: int, data_len_bits: int) -> bytes:
    """构造 16 字节 LRIT PrimaryHeader。来源: goestools src/lrit/lrit.cc:164-172"""
    out = bytearray()
    out.append(H_PRIMARY)              # headerType
    out += (16).to_bytes(2, "big")    # headerLength = 16
    out.append(file_type)             # fileType
    out += total_header_len.to_bytes(4, "big")   # totalHeaderLength
    out += data_len_bits.to_bytes(8, "big")     # dataLength (bits)
    assert len(out) == 16
    return bytes(out)


def build_annotation_header(text: str) -> bytes:
    """构造 type=4 AnnotationHeader。来源: lrit.cc:214-220"""
    body = text.encode("ascii")
    out = bytearray()
    out.append(H_ANNOTATION)
    out += (3 + len(body)).to_bytes(2, "big")
    out += body
    return bytes(out)


def build_lrit_file(annotation: str = "GOES16_ABI.lrit",
                    file_type: int = 0,
                    image_data: bytes = b"") -> bytes:
    """组装一个完整 LRIT 文件。"""
    ann = build_annotation_header(annotation)
    total_hdr = 16 + len(ann)
    data_bits = len(image_data) * 8
    return build_lrit_primary(file_type, total_hdr, data_bits) + ann + image_data


def build_tpdu(apid: int, seq_flag: int, seq_count: int,
               user_data: bytes) -> bytes:
    """组装一个 CCSDS TP_PDU：6B 头 + user_data + 2B CRC。

    CRC 覆盖 user_data。来源: transport_pdu.h:59-73, crc.cc:46-52
    """
    hdr = bytearray()
    hdr.append(((0 & 0x07) << 5) | ((0 & 0x01) << 4) | ((0 & 0x01) << 3) |
               ((apid >> 8) & 0x07))
    hdr.append(apid & 0xFF)
    hdr.append(((seq_flag & 0x03) << 6) | ((seq_count >> 8) & 0x3F))
    hdr.append(seq_count & 0xFF)
    # 用户数据 = user_data + 2 字节 CRC；CCSDS length 字段 = 总字节数 - 1
    # 来源: transport_pdu.h:59-68  length() = field + 1
    payload = user_data
    crc = crc16_ccitt(payload)
    length_field = len(payload) + 2 - 1
    hdr += length_field.to_bytes(2, "big")
    return bytes(hdr) + payload + crc.to_bytes(2, "big")


def build_vcdu(vcid: int, counter: int, mpdu_payload: bytes,
               fhp: int = 0, scid: int = 0x10) -> bytes:
    """组装一个 892 字节 VCDU。

    mpdu_payload: M_PDU 区（884B）中 FHP 之后的内容。FHP 指向 mpdu_payload
    在 884B 区中的起点。来源: vcdu.h:17-39, virtual_channel.cc:44-48
    """
    data = bytearray(VCDU_LEN)
    # VCDU 头
    data[0] = ((0 & 0x03) << 6) | ((scid >> 2) & 0x3F)   # version=0, SCID high
    data[1] = ((scid & 0x03) << 6) | (vcid & 0x3F)       # SCID low | VCID
    data[2] = (counter >> 16) & 0xFF
    data[3] = (counter >> 8) & 0xFF
    data[4] = counter & 0xFF
    data[5] = 0
    # M_PDU 头 2 字节：FHP
    data[6] = (fhp >> 8) & 0x07
    data[7] = fhp & 0xFF
    # M_PDU 数据区从 data[8] 开始（即 VCDU.data[2:]）
    data[8:8 + len(mpdu_payload)] = mpdu_payload
    return bytes(data)


def make_single_packet_vcdu(file_buf: bytes, vcid: int = 10, counter: int = 0,
                            apid: int = 0x100) -> bytes:
    """把一个 LRIT 文件包成单个 seqFlag=3 的 TP_PDU，放进一个 VCDU。"""
    # 第一个 TP_PDU 用户数据前 10 字节是垃圾。来源: session_pdu.cc:78-82
    user_data = b"\xAA" * 10 + file_buf
    tpdu = build_tpdu(apid, seq_flag=3, seq_count=0, user_data=user_data)
    return build_vcdu(vcid, counter, tpdu, fhp=0)


class TestLRITFrameParams(unittest.TestCase):
    """参数验证：帧长 1020，同步字 0x1ACFFC1D。"""

    def test_constants(self):
        self.assertEqual(SYNC_WORD, 0x1ACFFC1D)
        self.assertEqual(SYNC_WORD_BYTES, b"\x1a\xcf\xfc\x1d")
        self.assertEqual(FRAME_DATA_BYTES, 1020)
        self.assertEqual(FRAME_BYTES, 1024)
        self.assertEqual(VCDU_LEN, 892)
        self.assertEqual(len(DERANDOM_TABLE), 1020)

    def test_crc16_known_vector(self):
        # CRC-16/CCITT-FALSE 标准校验值
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_derandomize_is_self_inverse(self):
        frame = bytes(range(256)) * 4  # 1024 字节，截 1020
        frame = frame[:1020]
        d = derandomize(frame)
        self.assertEqual(derandomize(d), frame)


class TestSyncWordDetection(unittest.TestCase):
    """同步字检测：含噪声的字节流中找到 0x1ACFFC1D。"""

    def test_find_clean_sync(self):
        noise = os.urandom(200)
        stream = noise + SYNC_WORD_BYTES + os.urandom(500)
        pos = LRITParser.find_sync(stream)
        self.assertEqual(pos, 200)

    def test_find_sync_in_noise(self):
        # 随机噪声中不应误匹配
        noise = os.urandom(4096)
        # 确保噪声本身不含同步字（极小概率；若命中则重试）
        if noise.find(SYNC_WORD_BYTES) >= 0:
            noise = os.urandom(4096)
        pos = LRITParser.find_sync(noise)
        self.assertEqual(pos, -1)

    def test_parse_transport_frame(self):
        payload = os.urandom(1020)
        frame = SYNC_WORD_BYTES + payload
        out = LRITParser.parse_transport_frame(frame)
        self.assertEqual(len(out), 1020)
        # 解扰 = payload XOR table
        self.assertEqual(out, bytes(a ^ b for a, b in zip(payload, DERANDOM_TABLE)))


class TestVCDUParsing(unittest.TestCase):
    """VCDU 字段解析：VCID/SCID/counter。"""

    def test_vcdu_fields(self):
        vcdu = build_vcdu(vcid=17, counter=0x123456, mpdu_payload=b"\x00" * 10)
        v = VCDU.parse(vcdu)
        self.assertEqual(v.vcid, 17)
        self.assertEqual(v.scid, 0x10)
        self.assertEqual(v.counter, 0x123456)
        self.assertEqual(len(v.data), 886)

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            VCDU.parse(b"\x00" * 100)


class TestVirtualChannelReassembly(unittest.TestCase):
    """虚拟信道重组：多 VCDU / 多 TP_PDU → 完整 LRIT 文件。"""

    def test_single_packet_file(self):
        """一个 VCDU 内 seqFlag=3 的完整包。"""
        file_buf = build_lrit_file(annotation="GOES16_CH01.lrit",
                                   image_data=b"\x01\x02\x03\x04")
        vcdu = make_single_packet_vcdu(file_buf, vcid=10, counter=0)
        parser = LRITParser()
        files = parser.feed_vcdu(vcdu)
        self.assertEqual(len(files), 1)
        h = parse_lrit_headers(files[0])
        self.assertEqual(h.file_type, 0)
        self.assertInAnnotation(h, "GOES16_CH01")

    def assertInAnnotation(self, h, needle):
        self.assertIn(needle, h.annotation)

    def test_cross_tpdu_reassembly(self):
        """一个文件拆成 seqFlag=1（首）+ seqFlag=2（末）两个 TP_PDU。"""
        image_data = bytes(range(256)) * 4  # 1024 字节图像数据
        file_buf = build_lrit_file(annotation="GOES16_CROSS.lrit",
                                   image_data=image_data)

        # 第一个 TP_PDU：前 10 垃圾 + 文件前半段
        first_user = b"\xAA" * 10 + file_buf[: len(file_buf) // 2]
        tpdu1 = build_tpdu(0x100, seq_flag=1, seq_count=0, user_data=first_user)
        vcdus = [build_vcdu(10, 0, tpdu1, fhp=0)]

        # 第二个 TP_PDU：文件后半段（无 10 字节前缀）
        second_user = file_buf[len(file_buf) // 2:]
        tpdu2 = build_tpdu(0x100, seq_flag=2, seq_count=1, user_data=second_user)
        vcdus.append(build_vcdu(10, 1, tpdu2, fhp=0))

        parser = LRITParser()
        files = parser.parse_stream(vcdus)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0], file_buf)
        h = parse_lrit_headers(files[0])
        self.assertIn("GOES16_CROSS", h.annotation)

    def test_cross_vcdu_tpdu(self):
        """一个 TP_PDU 跨 VCDU 边界（in_progress 续包）。

        M_PDU 每 VCDU 仅 884B；TP_PDU payload 必须 > 884 才会跨帧。
        """
        image_data = b"\x55" * 1000
        file_buf = build_lrit_file(annotation="GOES16_SPLIT.lrit",
                                   image_data=image_data)
        # 第一个 TP_PDU 完整用户数据
        full_user = b"\xAA" * 10 + file_buf
        tpdu = build_tpdu(0x100, seq_flag=3, seq_count=0, user_data=full_user)
        # v1 装下整个 M_PDU (884B)；剩余进 v2，FHP=2047 无新包
        part1 = tpdu[:884]
        part2 = tpdu[884:]
        v1 = build_vcdu(10, 0, part1, fhp=0)
        v2 = build_vcdu(10, 1, part2, fhp=2047)
        parser = LRITParser()
        files = parser.parse_stream([v1, v2])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0], file_buf)

    def test_fill_vcid_ignored(self):
        """VCID=63 填充信道应被忽略。"""
        file_buf = build_lrit_file()
        vcdu = make_single_packet_vcdu(file_buf, vcid=63)
        parser = LRITParser()
        self.assertEqual(parser.feed_vcdu(vcdu), [])


class TestTPDUParse(unittest.TestCase):
    """CCSDS TP_PDU 头解析。"""

    def test_tpdu_header_fields(self):
        tpdu = build_tpdu(apid=0x345, seq_flag=1, seq_count=1234,
                          user_data=b"\x00" * 50)
        t = TransportPDU.parse_header(tpdu[:6])
        self.assertEqual(t.apid, 0x345)
        self.assertEqual(t.seq_flag, 1)
        self.assertEqual(t.seq_count, 1234)
        self.assertEqual(t.length, 50 + 2)  # user_data + CRC
        # 把 payload 补全后 CRC 应校验通过
        t.payload = bytearray(tpdu[6:])
        self.assertTrue(t.verify_crc())

    def test_bad_crc_drops_packet(self):
        tpdu = bytearray(build_tpdu(0x100, 3, 0, b"\x00" * 20))
        tpdu[-1] ^= 0xFF  # 破坏 CRC
        file_buf = build_lrit_file()
        # 把坏包塞进 VCDU，不应产出文件
        v = build_vcdu(10, 0, bytes(tpdu), fhp=0)
        parser = LRITParser()
        self.assertEqual(parser.feed_vcdu(v), [])


class TestHRITParser(unittest.TestCase):
    """HRIT 与 LRIT 同协议栈，仅符号率不同。"""

    def test_hrit_symbol_rate(self):
        self.assertEqual(HRITParser().symbol_rate, 927000)
        self.assertEqual(LRITParser().symbol_rate, 293883)

    def test_hrit_reassembles(self):
        file_buf = build_lrit_file(annotation="HRIT_IMG.lrit",
                                   image_data=b"\x01" * 100)
        vcdu = make_single_packet_vcdu(file_buf, vcid=20)
        files = HRITParser().feed_vcdu(vcdu)
        self.assertEqual(len(files), 1)
        self.assertIn("HRIT_IMG", parse_lrit_headers(files[0]).annotation)


class TestImageSegmentReassembly(unittest.TestCase):
    """GOESImageDecoder：多段 LRIT 图像 → 完整灰度图。"""

    def _build_segment(self, image_id, seg_no, max_seg, x0, y0,
                       cols, lines, bpp=8):
        """构造一个带 SegmentIdentificationHeader 的图像 LRIT 文件。"""
        hdr = bytearray()
        hdr.append(H_PRIMARY)
        hdr += (16).to_bytes(2, "big")
        hdr.append(0)  # fileType=0 图像
        # totalHeaderLength 稍后填
        img_struct = bytearray()
        img_struct.append(H_IMAGE_STRUCTURE)
        img_struct += (9).to_bytes(2, "big")   # 3 + 1+2+2+1
        img_struct.append(bpp)
        img_struct += cols.to_bytes(2, "big")
        img_struct += lines.to_bytes(2, "big")
        img_struct.append(0)  # compression=0 未压缩

        seg_id = bytearray()
        seg_id.append(H_SEGMENT_ID)
        seg_id += (17).to_bytes(2, "big")      # 3 + 7*2
        seg_id += image_id.to_bytes(2, "big")
        seg_id += seg_no.to_bytes(2, "big")
        seg_id += x0.to_bytes(2, "big")
        seg_id += y0.to_bytes(2, "big")
        seg_id += max_seg.to_bytes(2, "big")
        seg_id += cols.to_bytes(2, "big")   # maxColumn = cols
        seg_id += (lines * max_seg).to_bytes(2, "big")  # maxLine

        total_hdr = 16 + len(img_struct) + len(seg_id)
        out = bytearray()
        out.append(H_PRIMARY)
        out += (16).to_bytes(2, "big")
        out.append(0)
        out += total_hdr.to_bytes(4, "big")
        out += (cols * lines * bpp // 8).to_bytes(8, "big")
        out += img_struct
        out += seg_id
        # 图像数据：每像素 = y0 + seg_no 标记灰度
        pixels = bytes([(y0 + r) & 0xFF for r in range(lines) for _ in range(cols)])
        out += pixels
        return bytes(out)

    def test_two_segments_assemble(self):
        # 整图 2 行 × 4 列；段1 放第 0 行，段2 放第 1 行
        seg1 = self._build_segment(image_id=42, seg_no=1, max_seg=2,
                                  x0=0, y0=0, cols=4, lines=1)
        seg2 = self._build_segment(image_id=42, seg_no=2, max_seg=2,
                                  x0=0, y0=1, cols=4, lines=1)
        dec = GOESImageDecoder()
        self.assertIsNone(dec.add_lrit_file(seg1))   # 段1 未凑齐
        img = dec.add_lrit_file(seg2)
        self.assertIsNotNone(img)
        self.assertEqual(img.width, 4)
        self.assertEqual(img.height, 2)
        # 第 0 行像素全 0，第 1 行像素全 1
        self.assertEqual(list(img.pixels[:4]), [0, 0, 0, 0])
        self.assertEqual(list(img.pixels[4:]), [1, 1, 1, 1])

    def test_ir_grayscale_mapping(self):
        raw = [180, 200, 220, 240, 255]
        g = GOESImageDecoder.ir_to_grayscale(raw, vmin=180, vmax=255)
        self.assertEqual(g[0], 0)
        self.assertEqual(g[-1], 255)
        self.assertTrue(all(0 <= v <= 255 for v in g))


if __name__ == "__main__":
    unittest.main(verbosity=2)
