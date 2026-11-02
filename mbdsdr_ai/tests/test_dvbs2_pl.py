# SPDX-License-Identifier: MIT
"""DVB-S2 PL 层定向测试；期望值直接转录自 ETSI 表，而非调用实现推算。"""

from __future__ import annotations

import os
import sys
import unittest
from functools import lru_cache

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai import dvbs2_pl as pl


# Table 12 + Tables 9/10 + Table 5b 的独立转录。
EXPECTED_MODCODS = (
    (1, "QPSK", "1/4", 2, None, None, True),
    (2, "QPSK", "1/3", 2, None, None, True),
    (3, "QPSK", "2/5", 2, None, None, True),
    (4, "QPSK", "1/2", 2, None, None, True),
    (5, "QPSK", "3/5", 2, None, None, True),
    (6, "QPSK", "2/3", 2, None, None, True),
    (7, "QPSK", "3/4", 2, None, None, True),
    (8, "QPSK", "4/5", 2, None, None, True),
    (9, "QPSK", "5/6", 2, None, None, True),
    (10, "QPSK", "8/9", 2, None, None, True),
    (11, "QPSK", "9/10", 2, None, None, False),
    (12, "8PSK", "3/5", 3, None, None, True),
    (13, "8PSK", "2/3", 3, None, None, True),
    (14, "8PSK", "3/4", 3, None, None, True),
    (15, "8PSK", "5/6", 3, None, None, True),
    (16, "8PSK", "8/9", 3, None, None, True),
    (17, "8PSK", "9/10", 3, None, None, False),
    (18, "16APSK", "2/3", 4, 3.15, None, True),
    (19, "16APSK", "3/4", 4, 2.85, None, True),
    (20, "16APSK", "4/5", 4, 2.75, None, True),
    (21, "16APSK", "5/6", 4, 2.70, None, True),
    (22, "16APSK", "8/9", 4, 2.60, None, True),
    (23, "16APSK", "9/10", 4, 2.57, None, False),
    (24, "32APSK", "3/4", 5, 2.84, 5.27, True),
    (25, "32APSK", "4/5", 5, 2.72, 4.87, True),
    (26, "32APSK", "5/6", 5, 2.64, 4.64, True),
    (27, "32APSK", "8/9", 5, 2.54, 4.33, True),
    (28, "32APSK", "9/10", 5, 2.53, 4.30, False),
)

# (modulation, frame_type, pilots): (XFECFRAME symbols, PLFRAME symbols, pilot blocks)
# Table 11 的 S 与图 13 的 90(S+1)+36*floor((S-1)/16)。
EXPECTED_LENGTHS = {
    ("QPSK", "normal", False): (32400, 32490, 0),
    ("QPSK", "normal", True): (32400, 33282, 22),
    ("QPSK", "short", False): (8100, 8190, 0),
    ("QPSK", "short", True): (8100, 8370, 5),
    ("8PSK", "normal", False): (21600, 21690, 0),
    ("8PSK", "normal", True): (21600, 22194, 14),
    ("8PSK", "short", False): (5400, 5490, 0),
    ("8PSK", "short", True): (5400, 5598, 3),
    ("16APSK", "normal", False): (16200, 16290, 0),
    ("16APSK", "normal", True): (16200, 16686, 11),
    ("16APSK", "short", False): (4050, 4140, 0),
    ("16APSK", "short", True): (4050, 4212, 2),
    ("32APSK", "normal", False): (12960, 13050, 0),
    ("32APSK", "normal", True): (12960, 13338, 8),
    ("32APSK", "short", False): (3240, 3330, 0),
    ("32APSK", "short", True): (3240, 3402, 2),
}
REPRESENTATIVE_MODCOD = {"QPSK": 4, "8PSK": 12, "16APSK": 18, "32APSK": 24}


def _reference_pls(index: int) -> np.ndarray:
    """不经过产品代码的 Figure 13a/13b 位运算参考实现。"""
    rows = (
        "01010101010101010101010101010101",
        "00110011001100110011001100110011",
        "00001111000011110000111100001111",
        "00000000111111110000000011111111",
        "00000000000000001111111111111111",
        "11111111111111111111111111111111",
    )
    b = [(index >> shift) & 1 for shift in range(6, -1, -1)]
    y = [0] * 32
    for row, enabled in zip(rows, b[:6]):
        if enabled:
            y = [old ^ int(bit) for old, bit in zip(y, row)]
    out = []
    for value in y:
        out.extend((value, value ^ b[6]))
    mask = "0111000110011101100000111100100101010011010000100010110111111010"
    return np.array([value ^ int(bit) for value, bit in zip(out, mask)], dtype=np.uint8)


@lru_cache(maxsize=1)
def _reference_gold() -> tuple[list[int], list[int]]:
    period = (1 << 18) - 1
    x = [0] * period
    y = [1] * period
    x[0] = 1
    for i in range(period - 18):
        x[i + 18] = x[i + 7] ^ x[i]
        y[i + 18] = y[i + 10] ^ y[i + 7] ^ y[i + 5] ^ y[i]
    return x, y


def _reference_scrambling(length: int, code: int) -> np.ndarray:
    period = (1 << 18) - 1
    x, y = _reference_gold()
    lookup = np.array([1 + 0j, 1j, -1 + 0j, -1j])
    rotations = []
    for i in range(length):
        z0 = x[(i + code) % period] ^ y[i]
        shifted = i + 131072
        z1 = x[(shifted + code) % period] ^ y[shifted]
        rotations.append(2 * z1 + z0)
    return lookup[np.array(rotations, dtype=np.int64)]


class ModcodAndLayoutTests(unittest.TestCase):
    def test_complete_modcod_table_matches_independent_transcription(self):
        self.assertEqual(tuple(pl.MODCOD_TABLE), tuple(range(1, 29)))
        for expected in EXPECTED_MODCODS:
            modcod, modulation, rate, bits, gamma1, gamma2, short = expected
            with self.subTest(modcod=modcod):
                mode = pl.get_modcod(modcod)
                self.assertEqual(mode.modulation, modulation)
                self.assertEqual(mode.code_rate, rate)
                self.assertEqual(mode.bits_per_symbol, bits)
                self.assertEqual(mode.gamma1, gamma1)
                self.assertEqual(mode.gamma2, gamma2)
                self.assertEqual(mode.short_supported, short)

    def test_table8_interleaver_dimensions(self):
        expected = {
            "QPSK": (None, None, None),
            "8PSK": (21600, 5400, 3),
            "16APSK": (16200, 4050, 4),
            "32APSK": (12960, 3240, 5),
        }
        for modulation, modcod in REPRESENTATIVE_MODCOD.items():
            with self.subTest(modulation=modulation):
                mode = pl.get_modcod(modcod)
                actual = (
                    mode.interleaver_rows_normal,
                    mode.interleaver_rows_short,
                    mode.interleaver_columns,
                )
                self.assertEqual(actual, expected[modulation])

    def test_all_modulation_normal_short_pilot_lengths(self):
        for key, expected in EXPECTED_LENGTHS.items():
            modulation, frame_type, pilots = key
            with self.subTest(modulation=modulation, frame_type=frame_type, pilots=pilots):
                modcod = REPRESENTATIVE_MODCOD[modulation]
                layout = pl.plframe_layout(modcod, frame_type, pilots)
                actual = (
                    layout.xfecframe_symbols,
                    layout.plframe_symbols,
                    layout.pilot_blocks,
                )
                self.assertEqual(actual, expected)
                self.assertEqual(pl.fecframe_symbol_count(modcod, frame_type), expected[0])
                self.assertEqual(pl.plframe_symbol_count(modcod, frame_type, pilots), expected[1])

    def test_sof_data_pilot_layout_is_contiguous_and_exact(self):
        for modcod in (4, 12, 18, 24):
            for frame_type in ("normal", "short"):
                for pilots in (False, True):
                    with self.subTest(modcod=modcod, frame_type=frame_type, pilots=pilots):
                        layout = pl.plframe_layout(modcod, frame_type, pilots)
                        cursor = 0
                        data_slots = 0
                        pilot_starts = []
                        for segment in layout.segments:
                            self.assertEqual(segment.start, cursor)
                            cursor = segment.stop
                            if segment.kind == "header":
                                self.assertEqual((segment.start, segment.length), (0, 90))
                            elif segment.kind == "data":
                                data_slots += 1
                                self.assertEqual(segment.length, 90)
                            else:
                                self.assertEqual(segment.length, 36)
                                self.assertEqual(data_slots % 16, 0)
                                self.assertNotEqual(data_slots, layout.data_slots)
                                pilot_starts.append(segment.start)
                        self.assertEqual(cursor, layout.plframe_symbols)
                        self.assertEqual(data_slots, layout.data_slots)
                        self.assertEqual(layout.segments[-1].kind, "data")
                        self.assertEqual(layout.pilot_starts, tuple(pilot_starts))
                        expected_starts = tuple(1530 + 1476 * i for i in range(layout.pilot_blocks))
                        self.assertEqual(layout.pilot_starts, expected_starts)

    def test_invalid_modcod_and_short_nine_tenths_are_rejected(self):
        for modcod in (0, 29, 30, 31, -1, 32):
            with self.subTest(modcod=modcod), self.assertRaises(ValueError):
                pl.plframe_layout(modcod, "normal")
        for modcod in (11, 17, 23, 28):
            with self.subTest(modcod=modcod), self.assertRaises(ValueError):
                pl.plframe_layout(modcod, "short")
        with self.assertRaises(ValueError):
            pl.plframe_layout(4, "NORMAL")
        with self.assertRaises(TypeError):
            pl.plframe_layout(4, "normal", 1)


class PLSTests(unittest.TestCase):
    def test_known_codeword_and_all_valid_type_fields(self):
        expected = "0111000101100010100000110011011001010011101111010010110100000101"
        actual = "".join(str(int(bit)) for bit in pl.encode_pls(4, "normal", False))
        self.assertEqual(actual, expected)
        self.assertEqual(f"{int(actual, 2):016X}", "7162833653BD2D05")
        for modcod in range(1, 29):
            for frame_type in ("normal", "short"):
                if frame_type == "short" and not pl.get_modcod(modcod).short_supported:
                    continue
                for pilots in (False, True):
                    with self.subTest(modcod=modcod, frame_type=frame_type, pilots=pilots):
                        index = (modcod << 2) | (int(frame_type == "short") << 1) | int(pilots)
                        codeword = pl.encode_pls(modcod, frame_type, pilots)
                        np.testing.assert_array_equal(codeword, _reference_pls(index))
                        decoded = pl.decode_pls(codeword)
                        self.assertEqual(decoded.modcod, modcod)
                        self.assertEqual(decoded.frame_type, frame_type)
                        self.assertEqual(decoded.pilots, pilots)
                        self.assertEqual(decoded.distance, 0)
                        self.assertEqual(decoded.threshold, 8)
                        self.assertGreaterEqual(decoded.second_distance, 32)

    def test_reference_codebook_minimum_distance_is_32(self):
        book = np.array([_reference_pls(index) for index in range(128)], dtype=np.uint8)
        minimum = 64
        for left in range(128):
            for right in range(left + 1, 128):
                minimum = min(minimum, int(np.count_nonzero(book[left] != book[right])))
        self.assertEqual(minimum, 32)
        self.assertEqual(pl.PLS_MINIMUM_DISTANCE, 32)

    def test_distance_threshold_corrects_eight_and_rejects_nine(self):
        codeword = pl.encode_pls(7, "normal", True)
        damaged = codeword.copy()
        damaged[:8] ^= 1
        decoded = pl.decode_pls(damaged)
        self.assertEqual((decoded.modcod, decoded.frame_type, decoded.pilots), (7, "normal", True))
        self.assertEqual(decoded.distance, 8)
        damaged[:9] ^= 1  # 前 8 位再翻回，第 9 位翻转，总计 1 位；重新构造 9 位错误
        damaged = codeword.copy()
        damaged[:9] ^= 1
        with self.assertRaises(pl.PLSDecodeError) as caught:
            pl.decode_pls(damaged)
        self.assertEqual(caught.exception.distance, 9)
        self.assertEqual(caught.exception.threshold, 8)
        decoded = pl.decode_pls(damaged, max_distance=9)
        self.assertEqual(decoded.distance, 9)
        with self.assertRaises(ValueError):
            pl.decode_pls(codeword, max_distance=16)

    def test_pure_noise_and_non_data_codewords_are_rejected(self):
        for seed in range(8):
            noise = np.random.default_rng(seed).integers(0, 2, 64, dtype=np.uint8)
            with self.subTest(seed=seed), self.assertRaises(pl.PLSDecodeError):
                pl.decode_pls(noise)
        for index in (0, 29 << 2, 30 << 2, 31 << 2):
            with self.subTest(index=index), self.assertRaises(pl.PLSDecodeError):
                pl.decode_pls(_reference_pls(index))
        with self.assertRaises(pl.PLSDecodeError):
            pl.decode_pls(np.zeros(63, dtype=np.uint8))
        with self.assertRaises(ValueError):
            pl.decode_pls(np.array([0, 1, 2] + [0] * 61))


class HeaderAndScramblerTests(unittest.TestCase):
    def test_sof_and_pi_over_2_bpsk_header(self):
        self.assertEqual(len(pl.SOF_BITS), 26)
        self.assertEqual(int("".join(str(int(bit)) for bit in pl.SOF_BITS), 2), 0x18D2E82)
        bits = pl.build_plheader_bits(4, "normal", False)
        self.assertEqual(len(bits), 90)
        np.testing.assert_array_equal(bits[:26], pl.SOF_BITS)
        symbols = pl.modulate_plheader(bits)
        self.assertEqual(len(symbols), 90)
        amplitude = 1 / np.sqrt(2)
        for i, bit in enumerate(bits):
            level = amplitude * (1 - 2 * int(bit))
            expected = level + 1j * level if i % 2 == 0 else -level + 1j * level
            self.assertEqual(symbols[i], expected)
        np.testing.assert_array_equal(pl.demodulate_plheader(symbols), bits)
        np.testing.assert_allclose(np.abs(symbols), 1.0, atol=1e-14, rtol=0)

    def test_scrambler_against_independent_gold_recurrence(self):
        for code, length in ((0, 256), (1, 96), (262141, 64)):
            with self.subTest(code=code):
                actual = pl.pl_scrambling_sequence(length, code)
                expected = _reference_scrambling(length, code)
                np.testing.assert_array_equal(actual, expected)
                self.assertTrue(set(np.unique(actual)).issubset({1 + 0j, 1j, -1 + 0j, -1j}))
        # n=0 的前 32 个 Rn，另用固定值防止参考实现与产品代码同步漂移。
        rotations = "".join(str({1: 0, 1j: 1, -1: 2, -1j: 3}[complex(value)])
                            for value in pl.pl_scrambling_sequence(32))
        self.assertEqual(rotations, "01111313131313331312222000133323")

    def test_scrambler_rejects_out_of_range_inputs(self):
        for code in (-1, 262142):
            with self.subTest(code=code), self.assertRaises(ValueError):
                pl.pl_scrambling_sequence(1, code)
        with self.assertRaises(ValueError):
            pl.pl_scrambling_sequence(66421)
        with self.assertRaises(ValueError):
            pl.pl_scrambling_sequence(-1)


class PLFrameRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normal_bits = np.random.default_rng(20261102).integers(
            0, 2, 64800, dtype=np.uint8
        )

    def test_qpsk_end_to_end_normal_short_with_and_without_pilots(self):
        expected_lengths = {
            ("normal", False): 32490,
            ("normal", True): 33282,
            ("short", False): 8190,
            ("short", True): 8370,
        }
        for (frame_type, pilots), expected_length in expected_lengths.items():
            with self.subTest(frame_type=frame_type, pilots=pilots):
                bits = self.normal_bits[:16200] if frame_type == "short" else self.normal_bits
                frame = pl.encode_qpsk_plframe(bits, 4, frame_type, pilots, scrambling_code=0)
                self.assertEqual(len(frame.symbols), expected_length)
                np.testing.assert_array_equal(frame.symbols[:90], frame.header_symbols)
                decoded = pl.decode_qpsk_plframe(frame.symbols)
                np.testing.assert_array_equal(decoded.fecframe_bits, bits)
                self.assertEqual(decoded.parsed.consumed_symbols, expected_length)
                self.assertEqual(decoded.parsed.pls.modcod, 4)
                self.assertEqual(decoded.parsed.layout.pilot_blocks, 22 if frame_type == "normal" and pilots else 5 if pilots else 0)
                np.testing.assert_allclose(
                    decoded.parsed.xfecframe_symbols,
                    pl.map_qpsk_fecframe(bits, frame_type),
                    atol=1e-14,
                    rtol=0,
                )
                if pilots:
                    np.testing.assert_allclose(
                        decoded.parsed.pilot_symbols,
                        np.full(len(decoded.parsed.pilot_symbols), (1 + 1j) / np.sqrt(2)),
                        atol=1e-14,
                        rtol=0,
                    )
                    self.assertEqual(len(decoded.parsed.pilot_symbols), 36 * decoded.parsed.layout.pilot_blocks)
                else:
                    self.assertEqual(len(decoded.parsed.pilot_symbols), 0)

    def test_scrambling_excludes_header_and_covers_data_and_pilots(self):
        bits = self.normal_bits[:16200]
        frame = pl.encode_qpsk_plframe(bits, 4, "short", True, scrambling_code=17)
        body = frame.symbols[90:]
        sequence = pl.pl_scrambling_sequence(len(body), 17)
        descrambled = body * np.conj(sequence)
        cursor = 0
        data = pl.map_qpsk_fecframe(bits, "short")
        for segment in frame.layout.segments:
            if segment.kind == "header":
                continue
            start = segment.start - 90
            if segment.kind == "data":
                np.testing.assert_allclose(
                    descrambled[start:segment.stop - 90], data[cursor:cursor + 90],
                    atol=1e-14, rtol=0,
                )
                cursor += 90
            else:
                np.testing.assert_allclose(
                    descrambled[start:segment.stop - 90],
                    np.full(36, (1 + 1j) / np.sqrt(2)), atol=1e-14, rtol=0,
                )
        np.testing.assert_array_equal(frame.symbols[:90], pl.modulate_plheader(frame.header_bits))

    def test_generic_symbol_interface_supports_verified_external_mapping(self):
        # 仅验证通用符号布局，不宣称实现了 8PSK FEC 比特映射。
        count = pl.fecframe_symbol_count(12, "short")
        symbols = np.exp(1j * (np.arange(count) % 8) * np.pi / 4)
        frame = pl.build_plframe(symbols, 12, "short", True)
        parsed = pl.parse_plframe(frame.symbols)
        self.assertEqual(parsed.pls.mode.modulation, "8PSK")
        np.testing.assert_allclose(parsed.xfecframe_symbols, symbols, atol=1e-14, rtol=0)

    def test_stream_slice_and_header_errors(self):
        bits = self.normal_bits[:16200]
        frame = pl.encode_qpsk_plframe(bits, 4, "short", False)
        with self.assertRaises(ValueError):
            pl.parse_plframe(frame.symbols[:-1])
        with self.assertRaises(ValueError):
            pl.parse_plframe(np.concatenate((frame.symbols, [1 + 0j])))
        parsed = pl.parse_plframe(np.concatenate((frame.symbols, [1 + 0j])), allow_trailing=True)
        self.assertEqual(parsed.consumed_symbols, len(frame.symbols))
        damaged = frame.symbols.copy()
        damaged[0] *= -1
        with self.assertRaises(pl.PLSDecodeError):
            pl.parse_plframe(damaged)
        parsed = pl.parse_plframe(damaged, max_sof_distance=1)
        self.assertEqual(parsed.sof_distance, 1)

    def test_illegal_lengths_nonfinite_and_unimplemented_mapping(self):
        with self.assertRaises(ValueError):
            pl.encode_qpsk_plframe(self.normal_bits[:100], 4, "short")
        bad = self.normal_bits[:16200].copy()
        bad[0] = 2
        with self.assertRaises(ValueError):
            pl.encode_qpsk_plframe(bad, 4, "short")
        with self.assertRaises(ValueError):
            pl.build_plframe(np.zeros(10), 4, "short")
        data = np.zeros(8100, dtype=np.complex128)
        data[0] = np.nan
        with self.assertRaises(ValueError):
            pl.build_plframe(data, 4, "short")
        for modcod in (12, 18, 24):
            with self.subTest(modcod=modcod), self.assertRaises(NotImplementedError):
                pl.encode_qpsk_plframe(self.normal_bits, modcod, "normal")


if __name__ == "__main__":
    unittest.main()
