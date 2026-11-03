# SPDX-License-Identifier: MIT
"""FY-4 DVB-S2 符号级链接与 FY-3 AHRPT 测试（合成向量，非硬件）。

FY-4 测试构造 BBFRAME→BB 加扰→BCH/LDPC→QPSK PLFRAME，再验证真实接收
顺序 PL 解扰→软 LLR→LDPC/BCH→BB 解扰→BBHEADER。

缺口状态（2026-11-04 P3.1 更新）：
  * ~~raw IQ 同步~~ —— 已补：dvbs2_pl.find_sof_candidates（非相干滑动
    相关 + NMS，ETSI §5.5.2.1 原文核对 SOF=18D2E82），端到端钉扎见
    tests/test_dvbs2_sof_sync.py（raw 流→SOF→解帧→FECFRAME 逐位一致）；
  * DATAFIELD→VCDU 重组仍未实现；
  * 真实 OTA IQ 样本未获得（最小采样条件见 test_dvbs2_sof_sync.py 头）。

运行: py -3.14 -m pytest tests/test_fengyun_dvbs2.py -q
"""

import os
import sys
import unittest
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import fengyun_sat as fy  # noqa: E402
from mbdsdr_ai.dvbs2_bch import bch_parameters, encode_dvbs2_fec  # noqa: E402
from mbdsdr_ai.dvbs2_pl import (  # noqa: E402
    PLHEADER_SYMBOLS,
    SOF_BITS,
    build_plframe,
    encode_qpsk_plframe,
    map_qpsk_fecframe,
    modulate_plheader,
    plframe_layout,
)
from mbdsdr_ai.fengyun_sat import (  # noqa: E402
    FY3_AHRPT_ASM,
    FY3_AHRPT_CADU_LEN,
    fy3_ahrpt_sync,
    fy3_descramble,
    fy3_extract_channels,
)


def _bbframe(frame_type: str, rate: str, dfl: int = 104) -> np.ndarray:
    parameters = bch_parameters(frame_type, rate)
    bits = np.random.default_rng(420).integers(
        0, 2, parameters["K_bch"], dtype=np.uint8)
    header = bytearray(10)
    header[0] = 0x40  # TS/GS=01、SIS、CCM、ISSYI/NPD=0、RO=00
    header[1] = 0
    header[2:4] = (188 * 8).to_bytes(2, "big")
    header[4:6] = dfl.to_bytes(2, "big")
    header[6] = 0x47
    header[7:9] = (0).to_bytes(2, "big")
    header[9] = fy.dvbs2_bb_crc8(header[:9])
    bits[:80] = np.unpackbits(np.frombuffer(header, dtype=np.uint8))
    bits[80:80 + dfl] = (np.arange(dfl) % 3 == 0).astype(np.uint8)
    return bits


def _pl_symbols(bbframe: np.ndarray, frame_type: str, rate: str,
                pilots: bool = False, scrambling_code: int = 0) -> np.ndarray:
    modcod = 4 if rate == "1/2" else 1
    fecframe = encode_dvbs2_fec(fy.descramble_dvbs2(bbframe), frame_type, rate)
    return encode_qpsk_plframe(
        fecframe, modcod, frame_type, pilots, scrambling_code).symbols


class TestDvbS2SymbolReceiver:
    @pytest.mark.parametrize(
        "frame_type,pilots,expected",
        [("normal", False, 32490), ("normal", True, 33282),
         ("short", False, 8190), ("short", True, 8370)],
    )
    def test_plframe_extraction_uses_symbol_layout(self, frame_type, pilots, expected):
        modcod = 4 if frame_type == "normal" else 1
        frame = encode_qpsk_plframe(
            np.zeros(64800 if frame_type == "normal" else 16200, dtype=np.uint8),
            modcod, frame_type, pilots)
        stream = np.concatenate((np.ones(7, dtype=np.complex64) * (1 + 1j), frame.symbols))
        extracted = fy.extract_plframe(stream, 7, frame_type, modcod=modcod, pilots=pilots)
        assert frame.layout.plframe_symbols == expected
        assert len(extracted) == expected
        assert extracted.dtype == np.complex128
        np.testing.assert_array_equal(extracted, frame.symbols)
        with pytest.raises(ValueError, match="符号流不足"):
            fy.extract_plframe(extracted[:-1], 0, frame_type, modcod=modcod, pilots=pilots)

    def test_qpsk_soft_llr_order_and_scale(self):
        bits = np.tile(np.array([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.uint8), 2025)
        assert len(bits) == 16200
        symbols = map_qpsk_fecframe(bits, "short")
        llr = fy.demap_qpsk_soft(symbols, "short")
        assert llr.shape == (16200,)
        np.testing.assert_allclose(llr, 1.0 - 2.0 * bits)
        np.testing.assert_array_equal((llr < 0).astype(np.uint8), bits)
        with pytest.raises(ValueError, match="需 8100"):
            fy.demap_qpsk_soft(symbols[:-1], "short")

    def test_bbheader_crc8_and_fields(self):
        # CRC-8/DVB-S2 标准检查向量：poly=0xD5、init=x00、无反射、xorout=x00。
        assert fy.dvbs2_bb_crc8(b"123456789") == 0xBC
        bbframe = _bbframe("short", "1/4", dfl=104)
        header = fy.parse_bbheader(fy.descramble_dvbs2(fy.descramble_dvbs2(bbframe)))
        assert header["matype"] == b"\x40\x00"
        assert header["ts_gs"] == 1
        assert header["sis_mis"] == 0
        assert header["input_stream_identifier"] is None
        assert header["upl_bits"] == 188 * 8
        assert header["dfl_bits"] == 104
        assert header["sync"] == 0x47
        assert header["syncd_bits"] == 0
        assert header["crc8_valid"] is True
        damaged = bbframe.copy()
        damaged[79] ^= 1
        with pytest.raises(ValueError, match="CRC-8"):
            fy.parse_bbheader(damaged)

    @pytest.mark.parametrize("frame_type,rate,pilots", [
        ("normal", "1/2", False), ("normal", "1/2", True),
        ("short", "1/4", False), ("short", "1/4", True),
    ])
    def test_real_symbol_to_bbframe_roundtrip(self, frame_type, rate, pilots):
        bbframe = _bbframe(frame_type, rate)
        code = 0x123 if pilots else 0
        symbols = _pl_symbols(bbframe, frame_type, rate, pilots, code)
        prefix = np.arange(11, dtype=np.float64) * (0.01 + 0.02j) + 0.1 + 0.1j
        stream = np.concatenate((prefix.astype(np.complex64), symbols,
                                 np.array([0.2 - 0.1j, 0.1 + 0.3j])))
        frames = fy.fy4_dvbs2_sync(stream, scrambling_code=code)
        assert len(frames) == 1
        result = frames[0]
        assert result["success"] is True
        assert result["pos"] == len(prefix)
        assert result["frame_type"] == frame_type
        assert result["pilots"] is pilots
        assert result["pls"].mode.modulation == "QPSK"
        assert result["layout"].plframe_symbols == len(symbols)
        assert result["fec"]["ldpc"]["iterations"] == 0
        np.testing.assert_array_equal(result["bbframe"], bbframe)
        assert result["bbheader"]["crc8_valid"] is True
        np.testing.assert_array_equal(result["datafield"], bbframe[80:184])

    def test_real_call_order_and_stream_consumption(self):
        bbframe = _bbframe("short", "1/4")
        symbols = _pl_symbols(bbframe, "short", "1/4")
        stream = np.concatenate((np.array([0.2 + 0.3j]), symbols, symbols))
        events = []
        names = ("parse_plframe", "demap_qpsk_soft", "decode_dvbs2_fec",
                 "descramble_dvbs2", "parse_bbheader")
        originals = {name: getattr(fy, name) for name in names}

        def observed(name):
            def wrapper(*args, **kwargs):
                events.append(name)
                return originals[name](*args, **kwargs)
            return wrapper

        patchers = [mock.patch.object(fy, name, side_effect=observed(name)) for name in names]
        for patcher in patchers:
            patcher.start()
        try:
            frames = fy.fy4_dvbs2_sync(stream)
        finally:
            for patcher in patchers:
                patcher.stop()
        assert len(frames) == 2
        assert [frame["pos"] for frame in frames] == [1, 1 + len(symbols)]
        assert events == list(names) * 2
        for frame in frames:
            np.testing.assert_array_equal(frame["bbframe"], bbframe)

    def test_fec_failure_never_descrambles_or_guesses_header(self):
        # 非合法 FECFRAME 的交替比特产生已知的 LDPC 不收敛诊断，不用 mock 成功。
        fecframe = (np.arange(16200) % 2).astype(np.uint8)
        symbols = encode_qpsk_plframe(fecframe, 1, "short").symbols
        with mock.patch.object(fy, "descramble_dvbs2", wraps=fy.descramble_dvbs2) as descramble:
            frames = fy.fy4_dvbs2_sync(symbols, max_iterations=1)
        assert len(frames) == 1
        result = frames[0]
        assert result["success"] is False
        assert result["stage"] == "fec"
        assert result["fec"]["reason"] == "ldpc_max_iterations"
        assert "bbframe" not in result
        assert "bbheader" not in result
        assert "datafield" not in result
        descramble.assert_not_called()

    def test_bad_bbheader_crc_is_not_a_successful_datafield(self):
        bbframe = _bbframe("short", "1/4")
        bbframe[79] ^= 1
        symbols = _pl_symbols(bbframe, "short", "1/4")
        result = fy.fy4_dvbs2_sync(symbols)[0]
        assert result["success"] is False
        assert result["stage"] == "bbheader"
        assert "CRC-8" in result["error"]
        np.testing.assert_array_equal(result["bbframe"], bbframe)
        assert "bbheader" not in result
        assert "datafield" not in result

    def test_higher_modulation_rejected_before_soft_mapping_or_fec(self):
        layout = plframe_layout(12, "normal", False)
        rng = np.random.default_rng(123)
        xfecframe = rng.normal(size=layout.xfecframe_symbols) + 1j * rng.normal(
            size=layout.xfecframe_symbols)
        symbols = build_plframe(xfecframe, 12, "normal").symbols
        with mock.patch.object(fy, "demap_qpsk_soft") as demap, \
                mock.patch.object(fy, "decode_dvbs2_fec") as fec:
            with pytest.raises(NotImplementedError, match="8PSK.*解交织"):
                fy.fy4_dvbs2_sync(symbols)
        demap.assert_not_called()
        fec.assert_not_called()

    @pytest.mark.parametrize("bad_input", [
        np.zeros(8190, dtype=np.uint8),
        np.zeros(8190, dtype=np.float64),
        np.zeros((2, 4), dtype=np.complex64),
        np.array([1 + 0j, np.nan + 0j]),
        np.array([1 + 0j, np.inf + 0j]),
    ])
    def test_rejects_bits_real_samples_bad_dimensions_and_nonfinite(self, bad_input):
        with pytest.raises((TypeError, ValueError)):
            fy.fy4_dvbs2_sync(bad_input)

    def test_false_sof_or_truncated_frame_returns_no_frames(self):
        rng = np.random.default_rng(932)
        bad_header = np.concatenate((SOF_BITS, rng.integers(0, 2, 64, dtype=np.uint8)))
        assert fy.fy4_dvbs2_sync(modulate_plheader(bad_header)) == []
        valid = _pl_symbols(_bbframe("short", "1/4"), "short", "1/4")
        assert fy.fy4_dvbs2_sync(valid[:-1]) == []
        assert PLHEADER_SYMBOLS == 90


class TestFy3AHRPT(unittest.TestCase):
    def test_fy3_ahrpt_sync(self):
        """构造含 ASM 的字节流，验证切出 1024B CADU。"""
        asm = FY3_AHRPT_ASM.to_bytes(4, "big")
        cadu1 = asm + bytes(range(256)) * ((FY3_AHRPT_CADU_LEN - 4) // 256 + 1)
        cadu1 = cadu1[:FY3_AHRPT_CADU_LEN]
        cadu2 = asm + bytes(100, ) * (FY3_AHRPT_CADU_LEN - 4)
        stream = b"\x00\x11\x22" + cadu1 + b"\xAA" + cadu2
        frames = fy3_ahrpt_sync(stream)
        self.assertEqual(len(frames), 2)
        self.assertEqual(len(frames[0]), FY3_AHRPT_CADU_LEN)
        self.assertEqual(len(frames[1]), FY3_AHRPT_CADU_LEN)
        self.assertEqual(int.from_bytes(frames[0][:4], "big"), FY3_AHRPT_ASM)

    def test_fy3_descramble(self):
        """CCSDS 解扰往返（PN 自逆）。"""
        rng = np.random.default_rng(4)
        data = bytes(rng.integers(0, 256, size=1020).tolist())
        scrambled = fy3_descramble(data)
        self.assertNotEqual(scrambled, data)
        recovered = fy3_descramble(scrambled)
        self.assertEqual(recovered, data)

    def test_fy3_channel_extraction(self):
        """构造模拟 CADU，验证通道提取 shape 与 VCID。"""
        derand = bytearray(1020)
        derand[0] = 0x00
        derand[1] = 0x05
        payload = np.arange(1014, dtype=np.uint8).tobytes()
        derand[6:] = payload
        tx_data = fy3_descramble(bytes(derand))
        cadu = FY3_AHRPT_ASM.to_bytes(4, "big") + tx_data
        self.assertEqual(len(cadu), FY3_AHRPT_CADU_LEN)
        out = fy3_extract_channels(cadu)
        self.assertEqual(out["vcid"], 5)
        third = 1014 // 3
        self.assertEqual(len(out["ch1"]), third)
        self.assertEqual(len(out["ch2"]), third)
        self.assertEqual(len(out["ch4"]), third)
        np.testing.assert_array_equal(
            out["ch1"], np.frombuffer(payload[0:third], dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
