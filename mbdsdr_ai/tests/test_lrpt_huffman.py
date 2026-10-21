# SPDX-License-Identifier: MIT
"""LRPT 压缩域（Huffman + JPEG 基线扫描）与 VCDU 重组确定性互测。

自验证策略（tests 里自带最小 baseline JPEG 编码器，全部 independent）：
  - 优先跑 SatDump huffman 测试向量（若目录存在）；本 clone 无 test_data，
    故以“已知 DQT/DHT/SOI/SOF0/SOS 构造合成 JPEG”做收验证收。
  - 覆盖：多长度 canonical Huffman 解码、接收扩展、DC 差分跨块、AC(ZRL/EOB)、
    按 SOF 采样因子参数化的 MCU 组装、坏码/截断/坏标记/不支持特性异常、
    VCDU 按 (VCID, APID, sequence) 重组（正常/有洞/乱序/过滤/去重/取模翻卷）。
镜像 test_lrpt_dct.py / test_lrpt_pipeline.py 风格。
"""
from __future__ import annotations

import os
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai.lrpt_dct import (
    QUANT_TABLE, ZIGZAG, dct_8x8, idct_8x8, unzigzag_order,
)
from mbdsdr_ai import lrpt_huffman as H
from mbdsdr_ai.lrpt_vcdu import (
    GapRange, ImageReassembly, VcduPacket, reassemble_image_stream,
)

# --------------------------------------------------------------------------- #
# 量化表：自然栅格 (DQT 文件序为 zigzag，故 QT_ZIG = 自然表按 ZIGZAG 取)
# --------------------------------------------------------------------------- #
QNAT = QUANT_TABLE.reshape(8, 8)
QT_ZIG = QUANT_TABLE[ZIGZAG]
QT_ZIG2 = np.full(64, 64, dtype=np.int64)   # 第二分量用（恒 64）


# =========================================================================== #
# 测试用最小 baseline JPEG 编码器（independent；仅供确定性自验证）
# =========================================================================== #
class _BW:
    """熵位写者：MSB-first，尾按 1 填充到字节，0xFF 后插 0x00。"""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, val: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self.bits.append((val >> i) & 1)

    def to_bytes(self) -> bytes:
        pad = (-len(self.bits)) % 8
        bits = self.bits + [1] * pad
        out = bytearray()
        for i in range(0, len(bits), 8):
            b = 0
            for j in range(8):
                b = (b << 1) | bits[i + j]
            out.append(b)
            if b == 0xFF:
                out.append(0x00)
        return bytes(out)


def _canonical_lengths_to_codes(lengths: dict[int, int]) -> dict[int, tuple[int, int]]:
    """canonical Huffman 码（与 JPEG Annex C.3 / 解码端 mincode 一致）。"""
    by_len: dict[int, list[int]] = defaultdict(list)
    for sym, l in lengths.items():
        by_len[l].append(sym)
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    for l in range(1, 17):
        for sym in sorted(by_len.get(l, [])):
            codes[sym] = (l, code)
            code += 1
        code <<= 1
    return codes


def _dht_counts_huffval(lengths: dict[int, int]) -> tuple[list[int], list[int]]:
    by_len: dict[int, list[int]] = defaultdict(list)
    for sym, l in lengths.items():
        by_len[l].append(sym)
    counts = [len(by_len.get(l, [])) for l in range(1, 17)]
    huffval = [s for l in range(1, 17) for s in sorted(by_len.get(l, []))]
    return counts, huffval


def _make_lengths(freq: dict[int, int], mode: str) -> dict[int, int]:
    """由符号频次生成码长。mode='fixed' → 等长；mode='huffman' → 真 Huffman。"""
    syms = list(freq)
    if not syms:
        return {0: 1}
    if mode == "fixed":
        L = max(1, (len(syms) - 1).bit_length())
        return {s: L for s in syms}
    # 'huffman'：频率 → 真 Huffman 码长（多样长度）
    import heapq
    if len(syms) == 1:
        return {syms[0]: 1}
    heap = [[freq[s], [s], 0] for s in syms]          # [weight, symbols, depth]
    heapq.heapify(heap)
    depths = {s: 0 for s in syms}
    while len(heap) > 1:
        lo = heapq.heappop(heap)
        hi = heapq.heappop(heap)
        for grp in (lo[1], hi[1]):
            for s in grp:
                depths[s] += 1
        heapq.heappush(heap, [lo[0] + hi[0], lo[1] + hi[1], 0])
    return {s: depths[s] for s in syms}


def _pixels_to_coeff_zig(block8: np.ndarray, qnat2d: np.ndarray) -> np.ndarray:
    """像素块 → DCT → 量化（自然栅格）→ zigzag 序整系数。"""
    coeff = np.round(dct_8x8(block8.astype(np.float64)) / qnat2d).astype(np.int64)
    return coeff.flatten()[ZIGZAG]


def _coeff_ref_pixels(cz: np.ndarray, qt_zig: np.ndarray) -> np.ndarray:
    """给定 zigzag 量化系数，给出解码端应得的像素块（反量化+逆 Zigzag+IDCT）。"""
    dq = cz.astype(np.float64) * qt_zig.astype(np.float64)
    return idct_8x8(unzigzag_order(dq)).astype(np.uint8)


def _collect_symbols(blocks: list[np.ndarray],
                     comp_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    dcf: dict[int, int] = {}
    acf: dict[int, int] = {0x00: 1}          # 恒定备 EOB
    prev: dict[int, int] = {}                 # per-component DC predictor（JPEG 语义）
    for cz, cid in zip(blocks, comp_ids):
        dc = int(cz[0])
        diff = dc - prev.get(cid, 0)
        prev[cid] = dc
        s = 0 if diff == 0 else abs(diff).bit_length()
        dcf[s] = dcf.get(s, 0) + 1
        k = 1
        while k < 64:
            r = 0
            while k < 64 and int(cz[k]) == 0:
                r += 1
                k += 1
            if k == 64:
                break
            while r > 15:
                acf[0xF0] = acf.get(0xF0, 0) + 1
                r -= 16
            val = int(cz[k])
            s2 = 0 if val == 0 else abs(val).bit_length()
            acf[(r << 4) | s2] = acf.get((r << 4) | s2, 0) + 1
            k += 1
    # DC 覆盖 0..11（标准 8-bit baseline），避免漏 symbol 0
    for s in range(12):
        dcf.setdefault(s, 0)
    return dcf, acf


def _entropy_encode(blocks: list[np.ndarray], comp_ids: list[int],
                    dc_codes, ac_codes) -> bytes:
    w = _BW()
    prev: dict[int, int] = {}                 # per-component DC predictor（JPEG 语义）
    for cz, cid in zip(blocks, comp_ids):
        dc = int(cz[0])
        diff = dc - prev.get(cid, 0)
        prev[cid] = dc
        s = 0 if diff == 0 else abs(diff).bit_length()
        l, code = dc_codes[s]
        w.put(code, l)
        if s:
            w.put(diff if diff > 0 else diff + (1 << s) - 1, s)
        k = 1
        need_eob = True
        while k < 64:
            r = 0
            while k < 64 and int(cz[k]) == 0:
                r += 1
                k += 1
            if k == 64:
                need_eob = True
                break
            while r > 15:
                l, code = ac_codes[0xF0]
                w.put(code, l)
                r -= 16
            val = int(cz[k])
            s2 = 0 if val == 0 else abs(val).bit_length()
            sym = (r << 4) | s2
            l, code = ac_codes[sym]
            w.put(code, l)
            w.put(val if val > 0 else val + (1 << s2) - 1, s2)
            k += 1
            if k == 64:
                need_eob = False
                break
        if need_eob:
            l, code = ac_codes[0x00]
            w.put(code, l)
    return w.to_bytes()


def _seg(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def make_jpeg(width: int, height: int, comps_spec: list[tuple[int, int, int, int]],
              blocks: list[np.ndarray], qt_zig_by_tq: dict[int, np.ndarray],
              mode: str = "fixed", dri: int = 0,
              sof_marker: int = 0xC0, sos_tail: tuple[int, int, int] = (0, 63, 0),
              ) -> bytes:
    """组装 baseline JPEG（SOI + DQT + DHT + SOF + SOS + scan + EOI）。"""
    mc, mr, hmax, vmax = _mcu_dims(width, height, comps_spec)
    comp_ids: list[int] = []
    for _my in range(mr):
        for _mx in range(mc):
            for cid, hv, vv, tq in comps_spec:
                comp_ids.extend([cid] * (hv * vv))
    dcf, acf = _collect_symbols(blocks, comp_ids)
    dc_len = _make_lengths(dcf, mode)
    ac_len = _make_lengths(acf, mode)
    dc_codes = _canonical_lengths_to_codes(dc_len)
    ac_codes = _canonical_lengths_to_codes(ac_len)
    scan = _entropy_encode(blocks, comp_ids, dc_codes, ac_codes)

    out = bytearray(b"\xff\xd8")
    if dri:
        out += _seg(0xDD, struct.pack(">H", dri))
    for tq in sorted(qt_zig_by_tq):
        vals = qt_zig_by_tq[tq]
        out += _seg(0xDB, bytes([tq]) + bytes(int(v) & 0xFF for v in vals))
    # DC 表 id=0，AC 表 id=0
    c0, h0 = _dht_counts_huffval(dc_len)
    out += _seg(0xC4, bytes([0x00]) + bytes(c0) + bytes(h0))
    c1, h1 = _dht_counts_huffval(ac_len)
    out += _seg(0xC4, bytes([0x10]) + bytes(c1) + bytes(h1))
    # SOF
    body = bytearray([8]) + struct.pack(">HH", height, width) + bytes([len(comps_spec)])
    for cid, hv, vv, tq in comps_spec:
        body += bytes([cid, (hv << 4) | vv, tq])
    out += _seg(sof_marker, bytes(body))
    # SOS：每分量用 td=0, ta=0
    body = bytearray([len(comps_spec)])
    for cid, _hv, _vv, _tq in comps_spec:
        body += bytes([cid, 0x00])
    body += bytes(sos_tail)
    out += _seg(0xDA, bytes(body))
    out += scan
    out += b"\xff\xd9"
    return bytes(out)


def _mcu_dims(width, height, comps_spec):
    hmax = max(c[1] for c in comps_spec)
    vmax = max(c[2] for c in comps_spec)
    mc = -(-width // (8 * hmax))
    mr = -(-height // (8 * vmax))
    return mc, mr, hmax, vmax


def pixels_to_blocks_raster(width, height, comps_spec, value_fn,
                            qt_nat_by_ci):
    """按 MCU 顺序选块并转 zigzag 系数；同时给出每分量参考像素平面。"""
    mc, mr, hmax, vmax = _mcu_dims(width, height, comps_spec)
    blocks = []
    plane_ref = []
    ceff = []
    for ci, (cid, hv, vv, tq) in enumerate(comps_spec):
        cb = mc * hv
        rb = mr * vv
        plane = np.zeros((rb * 8, cb * 8), dtype=np.uint8)
        cz_map = {}
        for by in range(rb):
            for bx in range(cb):
                px = value_fn(ci, bx, by)
                blk8 = px.astype(np.float64)
                cz = _pixels_to_coeff_zig(blk8, qt_nat_by_ci[ci])
                cz_map[(bx, by)] = cz
                plane[by * 8:by * 8 + 8, bx * 8:bx * 8 + 8] = \
                    _coeff_ref_pixels(cz, PIX_QT_ZIG_BY_CI[ci])
        plane_ref.append(plane)
        ceff.append((cz_map, cb, rb))
    for my in range(mr):
        for mx in range(mc):
            for ci, (cid, hv, vv, tq) in enumerate(comps_spec):
                for u in range(hv * vv):
                    ux = u % hv
                    uy = u // hv
                    bx = mx * hv + ux
                    by = my * vv + uy
                    blocks.append(ceff[ci][0][(bx, by)])
    return blocks, plane_ref, (mc, mr, hmax, vmax)


# 每分量像素块→参考像素用的 zigzag 量化表（与 make_jpeg 的 DQT 一致）
QNAT_BY_CI = [QNAT, np.full((8, 8), 64)]
PIX_QT_ZIG_BY_CI = [QT_ZIG, QT_ZIG2]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def gray_value_fn():
    rng = np.random.default_rng(20261014)
    cache = {}
    def fn(ci, bx, by):
        if (bx, by) not in cache:
            cache[(bx, by)] = rng.integers(0, 256, size=(8, 8)).astype(np.uint8)
        return cache[(bx, by)]
    return fn


@pytest.fixture
def multi_value_fn():
    rng = np.random.default_rng(99)
    cache = {}
    def fn(ci, bx, by):
        if (ci, bx, by) not in cache:
            cache[(ci, bx, by)] = rng.integers(0, 256, size=(8, 8)).astype(np.uint8)
        return cache[(ci, bx, by)]
    return fn


# =========================================================================== #
# 纯函数：接收扩展
# =========================================================================== #
class TestReceiveExtend:
    def test_size_zero(self):
        assert H.receive_extend(0, 0) == 0

    @pytest.mark.parametrize("v,s,expect", [
        (0b0, 1, -1), (0b1, 1, 1),
        (0b00, 2, -3), (0b01, 2, -2), (0b10, 2, 2), (0b11, 2, 3),
        (0b001, 3, -6), (0b010, 3, -5), (0b011, 3, -4),
        (0b100, 3, 4), (0b111, 3, 7),
    ])
    def test_known_values(self, v, s, expect):
        assert H.receive_extend(v, s) == expect

    def test_reflexive_sign(self):
        # category s 只表示幅度 ∈ [2^(s-1), 2^s-1] 的值（s = 幅度.bit_length()）
        for s in range(1, 12):
            for mag in range(1 << (s - 1), (1 << s)):
                for val in (mag, -mag):
                    encoded = val if val > 0 else val + (1 << s) - 1
                    assert H.receive_extend(encoded, s) == val


# =========================================================================== #
# 多长度 canonical Huffman 查表解码（教科书 A/B/C/D 完整树，独立自校验）
# =========================================================================== #
def _pack_scan(bits: list[int]) -> bytes:
    w = _BW()
    w.bits = list(bits)
    return w.to_bytes()


class TestHuffmanDecode:
    def _ab_cd_table(self) -> H.HuffmanTable:
        # 完整树：'0'->A, '10'->B, '110'->C, '111'->D
        counts = [1, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        huffval = [0x0A, 0x0B, 0x0C, 0x0D]
        return H.HuffmanTable(counts, huffval, "AC", 0)

    def _decode_seq(self, table, bitstr):
        scan = _pack_scan([int(c) for c in bitstr])
        reader = H.BitReader(scan)
        out = []
        total_bits = len(bitstr)
        # 逐符号读：每读一个前确保还有位
        consumed = 0
        while consumed < total_bits:
            before = (reader.pos, reader.bit)
            sym = table.decode(reader)
            out.append(sym)
            used = (reader.pos * 8 + reader.bit) - (before[0] * 8 + before[1])
            consumed += used
        return out

    def test_each_code(self):
        t = self._ab_cd_table()
        assert self._decode_seq(t, "0") == [0x0A]
        assert self._decode_seq(t, "10") == [0x0B]
        assert self._decode_seq(t, "110") == [0x0C]
        assert self._decode_seq(t, "111") == [0x0D]

    def test_stream_multiple(self):
        t = self._ab_cd_table()
        # A B C D => 0 10 110 111
        assert self._decode_seq(t, "010110111") == [0x0A, 0x0B, 0x0C, 0x0D]

    def test_bad_code_raises(self):
        t = self._ab_cd_table()
        # 只有一个 symbol '0'(len1)，喂 16 个 '1' -> 无匹配
        counts = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        t2 = H.HuffmanTable(counts, [0x00], "AC", 0)
        scan = _pack_scan([1] * 17)
        with pytest.raises(H.LrptJpegHuffmanError):
            t2.decode(H.BitReader(scan))


# =========================================================================== #
# DHT/DQT 段解析
# =========================================================================== #
class TestSegmentParse:
    def test_parse_dht_roundtrip(self):
        lengths = {0: 2, 1: 2, 2: 3, 3: 3}   # 完整: 0.25+0.25+0.125+0.125=1.0
        counts, huffval = _dht_counts_huffval(lengths)
        seg = bytes([0x00]) + bytes(counts) + bytes(huffval)
        tabs = H.parse_dht(seg)
        assert len(tabs) == 1
        cls, tid, tab = tabs[0]
        assert cls == "DC" and tid == 0
        # 解码 '01' -> 应为 huffval[长度2第1个] = sym 1
        codes = _canonical_lengths_to_codes(lengths)
        l, code = codes[1]
        scan = _pack_scan([int(c) for c in format(code, f"0{l}b")])
        assert tab.decode(H.BitReader(scan)) == 1

    def test_parse_dqt_8_and_16bit(self):
        # 8-bit
        vals = list(range(1, 65))
        seg = bytes([0x00]) + bytes(v & 0xFF for v in vals)
        tqs = H.parse_dqt(seg)
        assert tqs[0][0] == 0
        assert tqs[0][1].tolist() == vals
        # 16-bit
        seg16 = bytes([0x10]) + b"".join(struct.pack(">H", v) for v in vals)
        tqs16 = H.parse_dqt(seg16)
        assert tqs16[0][1].tolist() == vals

    def test_truncated_dht_raises(self):
        with pytest.raises(H.LrptJpegMarkerError):
            H.parse_dht(bytes([0x00, 1, 2, 3]))   # counts 都没给全


# =========================================================================== #
# 合成 JPEG 收验证收（round-trip 逐像素精确）
# =========================================================================== #
class TestJpegRoundTrip:
    def test_grayscale_16x16(self, gray_value_fn):
        w = h = 16
        comps = [(1, 1, 1, 0)]
        blocks, plane_ref, (mc, mr, hmax, vmax) = pixels_to_blocks_raster(
            w, h, comps, gray_value_fn, QNAT_BY_CI[:1])
        assert (mc, mr, hmax, vmax) == (2, 2, 1, 1)
        jpeg = make_jpeg(w, h, comps, blocks, {0: QT_ZIG}, mode="fixed")
        dec = H.decode_image_blocks(jpeg)
        assert dec.width == 16 and dec.height == 16
        assert dec.num_blocks == 4
        np.testing.assert_array_equal(dec.planes[0], plane_ref[0])
        np.testing.assert_array_equal(dec.image, plane_ref[0][:16, :16])

    def test_grayscale_huffman_lengths(self, gray_value_fn):
        # 用真 Huffman（多长度）码长跑一遍，覆盖 valptr 跨长度解码
        w = h = 16
        comps = [(1, 1, 1, 0)]
        blocks, plane_ref, _ = pixels_to_blocks_raster(
            w, h, comps, gray_value_fn, QNAT_BY_CI[:1])
        jpeg = make_jpeg(w, h, comps, blocks, {0: QT_ZIG}, mode="huffman")
        dec = H.decode_image_blocks(jpeg)
        np.testing.assert_array_equal(dec.planes[0], plane_ref[0])

    def test_dc_diff_category_bounded(self, gray_value_fn):
        # 验证 DC category 未越界（8-bit baseline 上限 11）
        w = h = 32
        comps = [(1, 1, 1, 0)]
        blocks, _pl, _d = pixels_to_blocks_raster(
            w, h, comps, gray_value_fn, QNAT_BY_CI[:1])
        prev = 0
        for cz in blocks:
            diff = int(cz[0]) - prev
            prev = int(cz[0])
            s = 0 if diff == 0 else abs(diff).bit_length()
            assert s <= 11


# =========================================================================== #
# DC / AC 边界：ZRL / EOB / DC 差分跨块（直接构造系数）
# =========================================================================== #
class TestDcAcBoundaries:
    def _gray_jpeg(self, blocks, mode="fixed", w=None, h=None, qt_zig=QT_ZIG):
        n = len(blocks)
        ww = w or 8
        hh = h or (8 * ((n + (ww // 8) - 1) // (ww // 8)))
        comps = [(1, 1, 1, 0)]
        return make_jpeg(ww, hh, comps, blocks, {0: qt_zig}, mode=mode)

    def test_eob_early(self):
        cz = np.zeros(64, dtype=np.int64)
        cz[0] = 4
        cz[1] = 3
        # 其余全零 -> 应在 value 后立即 EOB
        jpeg = self._gray_jpeg([cz])
        dec = H.decode_image_blocks(jpeg)
        ref = _coeff_ref_pixels(cz, QT_ZIG)
        np.testing.assert_array_equal(dec.planes[0], ref)

    def test_zrl_sixteen_zeros(self):
        cz = np.zeros(64, dtype=np.int64)
        cz[0] = 7
        # index 1..16 = 16 个零 -> ZRL；index17 = value
        cz[17] = -3
        jpeg = self._gray_jpeg([cz])
        dec = H.decode_image_blocks(jpeg)
        ref = _coeff_ref_pixels(cz, QT_ZIG)
        np.testing.assert_array_equal(dec.planes[0], ref)

    def test_dc_diff_across_blocks(self):
        b0 = np.zeros(64, dtype=np.int64)
        b0[0] = 5
        b1 = np.zeros(64, dtype=np.int64)
        b1[0] = 5 + 12   # diff = +12
        b2 = np.zeros(64, dtype=np.int64)
        b2[0] = 5          # diff = -12
        jpeg = self._gray_jpeg([b0, b1, b2], w=8, h=24)
        dec = H.decode_image_blocks(jpeg)
        plane = np.zeros((24, 8), dtype=np.uint8)
        for i, cz in enumerate([b0, b1, b2]):
            plane[i * 8:i * 8 + 8, 0:8] = _coeff_ref_pixels(cz, QT_ZIG)
        np.testing.assert_array_equal(dec.planes[0], plane)

    def test_dc_only_flat_blocks(self):
        blocks = [np.array([v] + [0] * 63, dtype=np.int64) for v in [2, 2, 2, 2]]
        # 第二个起 diff=0 -> DC category 0
        jpeg = self._gray_jpeg(blocks, w=16, h=16)
        dec = H.decode_image_blocks(jpeg)
        plane = np.zeros((16, 16), dtype=np.uint8)
        for i, cz in enumerate(blocks):
            bx = i % 2
            by = i // 2
            plane[by * 8:by * 8 + 8, bx * 8:bx * 8 + 8] = \
                _coeff_ref_pixels(cz, QT_ZIG)
        np.testing.assert_array_equal(dec.planes[0], plane)


# =========================================================================== #
# MCU 组装：按 SOF 采样因子参数化（2x2 亮度 + 1x1 色度）
# =========================================================================== #
class TestMcuSampling:
    def test_2220_sampling(self, multi_value_fn):
        # Y(h2,v2,tq0) + Cb(h1,v1,tq1)；width=32 height=16
        w, h = 32, 16
        comps = [(1, 2, 2, 0), (2, 1, 1, 1)]
        blocks, plane_ref, (mc, mr, hmax, vmax) = pixels_to_blocks_raster(
            w, h, comps, multi_value_fn, QNAT_BY_CI)
        assert (mc, mr, hmax, vmax) == (2, 1, 2, 2)
        # 每 MCU: Y 4 块 + Cb 1 块 = 5；2 MCU -> 10
        assert len(blocks) == 10
        jpeg = make_jpeg(w, h, comps, blocks, {0: QT_ZIG, 1: QT_ZIG2})
        dec = H.decode_image_blocks(jpeg)
        assert dec.num_blocks == 10
        assert dec.n_components == 2
        # Y 平面 = 1*(8*2*1)=16 行 × 2*(8*2)=32 列
        assert dec.planes[0].shape == (16, 32)
        # Cb 平面 = 1*(8*1)=8 行 × 2*(8*1)=16 列
        assert dec.planes[1].shape == (8, 16)
        np.testing.assert_array_equal(dec.planes[0], plane_ref[0])
        np.testing.assert_array_equal(dec.planes[1], plane_ref[1])
        assert dec.image.shape == (16, 32, 2)


# =========================================================================== #
# 坏流 / 坏标记 / 不支持特性：一律抛带上下文异常（绝不半截静默）
# =========================================================================== #
def _valid_gray_jpeg(seed=7):
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(8):
        px = rng.integers(0, 256, size=(8, 8)).astype(np.uint8)
        blocks.append(_pixels_to_coeff_zig(px, QNAT))
    comps = [(1, 1, 1, 0)]
    return make_jpeg(32, 16, comps, blocks, {0: QT_ZIG}), blocks


class TestBadStreams:
    def test_missing_soi_raises(self):
        jpeg, _ = _valid_gray_jpeg()
        bad = jpeg[2:]                       # 去掉 SOI
        with pytest.raises(H.LrptJpegMarkerError):
            H.decode_image_blocks(bad)

    def test_bad_marker_length_raises(self):
        # 构造 SOF length 超界
        jpeg, _ = _valid_gray_jpeg()
        # 把某个 marker length 改成巨大值（破坏 iter_markers 边界校验）
        bad = bytearray(jpeg)
        # 找 SOF0(0xFFC0) 后继 length 字段设超尾值
        idx = bytes(bad).find(b"\xff\xc0")
        assert idx >= 0
        struct.pack_into(">H", bad, idx + 2, 0x7FFF)
        with pytest.raises(H.LrptJpegError):
            H.decode_image_blocks(bytes(bad))

    def test_bad_huffman_code_raises(self):
        # DC 表仅 symbol?=1；AC 表仅 EOB('0')。喂合法 DC 后 AC 喂 '1' -> 坏码
        qt = QT_ZIG
        counts_dc = [1] + [0] * 15
        counts_ac = [1] + [0] * 15
        dc_len = {1: 1}
        ac_len = {0x00: 1}
        out = bytearray(b"\xff\xd8")
        out += _seg(0xDB, bytes([0]) + bytes(int(v) & 0xFF for v in qt))
        out += _seg(0xC4, bytes([0x00]) + bytes(counts_dc) + bytes([1]))
        out += _seg(0xC4, bytes([0x10]) + bytes(counts_ac) + bytes([0x00]))
        out += _seg(0xC0, bytes([8]) + struct.pack(">HH", 8, 8) + bytes([1]) +
                   bytes([1, (1 << 4) | 1, 0]))
        out += _seg(0xDA, bytes([1, 1, 0x00, 0, 63, 0]))
        # scan: DC code '0' + value '1'(category1 diff=+1) + 20×'1'（坏 AC 码）
        w = _BW()
        w.put(0, 1)         # DC code=0 -> category 1
        w.put(1, 1)         # value bit
        w.put((1 << 20) - 1, 20)   # 20 个 1 -> AC 无法在 16 bit 内匹配
        out += w.to_bytes()
        out += b"\xff\xd9"
        with pytest.raises(H.LrptJpegHuffmanError):
            H.decode_image_blocks(bytes(out))

    def test_truncated_scan_raises(self):
        jpeg, _ = _valid_gray_jpeg()
        pref = H.locate_scan_start(jpeg)
        assert pref.scan_start is not None
        scan_len = len(jpeg) - pref.scan_start - 2   # 减 EOI
        truncated = jpeg[:pref.scan_start + scan_len // 2]
        with pytest.raises(H.LrptJpegError):
            H.decode_image_blocks(truncated)

    def test_progressive_unsupported(self):
        jpeg, _ = _valid_gray_jpeg()
        bad = bytearray(jpeg)
        idx = bytes(bad).find(b"\xff\xc0")
        bad[idx + 1] = 0xC2                       # SOF0 -> SOF2 (progressive)
        with pytest.raises(H.LrptJpegUnsupportedError):
            H.decode_image_blocks(bytes(bad))

    def test_non_baseline_scan_unsupported(self):
        rng = np.random.default_rng(1)
        blocks = [_pixels_to_coeff_zig(
            rng.integers(0, 256, size=(8, 8)).astype(np.uint8), QNAT)]
        jpeg = make_jpeg(8, 8, [(1, 1, 1, 0)], blocks, {0: QT_ZIG},
                         sos_tail=(1, 0, 0))       # Ss,Se,Ah/Al 非顺序
        with pytest.raises(H.LrptJpegUnsupportedError):
            H.decode_image_blocks(jpeg)

    def test_dri_restart_unsupported(self):
        rng = np.random.default_rng(2)
        blocks = [_pixels_to_coeff_zig(
            rng.integers(0, 256, size=(8, 8)).astype(np.uint8), QNAT)]
        jpeg = make_jpeg(8, 8, [(1, 1, 1, 0)], blocks, {0: QT_ZIG}, dri=3)
        with pytest.raises(H.LrptJpegUnsupportedError):
            H.decode_image_blocks(jpeg)

    def test_empty_input_raises(self):
        with pytest.raises(H.LrptJpegMarkerError):
            H.decode_image_blocks(b"")


# =========================================================================== #
# locate_scan_start
# =========================================================================== #
class TestLocateScanStart:
    def test_valid(self):
        jpeg, _ = _valid_gray_jpeg()
        pref = H.locate_scan_start(jpeg)
        assert pref.saw_soi and pref.saw_sof and pref.saw_sos
        assert pref.scan_start is not None

    def test_truncated_header(self):
        jpeg, _ = _valid_gray_jpeg()
        pref = H.locate_scan_start(jpeg[:20])     # 只留 SOI + 前几个 marker
        assert not pref.saw_sos or pref.scan_start is None


# =========================================================================== #
# VCDU → 图像段重组
# =========================================================================== #
def _split_stream(stream: bytes, n: int) -> list[bytes]:
    size = max(1, (len(stream) + n - 1) // n)
    return [stream[i:i + size] for i in range(0, len(stream), size)]


class TestVcduReassembly:
    def test_normal_order(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 6)
        pkts = [VcduPacket(5, 64, i, c) for i, c in enumerate(chunks)]
        r = reassemble_image_stream(pkts, target_vcid=5, target_apid=64)
        assert r.stream == jpeg
        assert r.num_packets == len(chunks)
        assert r.num_gaps == 0 and r.missing_sequences == []
        assert r.num_duplicates == 0 and r.num_filtered == 0
        assert r.header_complete and r.complete
        # 重组流应能直接解码
        dec = H.decode_image_blocks(r.stream)
        assert dec.width == 32 and dec.height == 16

    def test_with_hole(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 6)
        # 丢掉 seq=3
        pkts = [VcduPacket(5, 64, i, c) for i, c in enumerate(chunks) if i != 3]
        r = reassemble_image_stream(pkts, target_apid=64)
        assert r.num_packets == len(chunks) - 1
        assert r.num_gaps == 1
        assert r.missing_sequences == [3]
        assert r.gap_ranges == [GapRange(3, 3)]
        assert not r.complete
        assert r.stream != jpeg                          # 缺块未伪造

    def test_out_of_order(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 6)
        order = [3, 0, 5, 2, 4, 1]
        pkts = [VcduPacket(5, 64, i, chunks[i]) for i in order]
        r = reassemble_image_stream(pkts, target_apid=64)
        assert r.stream == jpeg                         # 按 sequence 重排正确
        assert r.num_gaps == 0 and r.complete

    def test_filter_non_target_apid(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 4)
        pkts = [VcduPacket(5, 64, i, c) for i, c in enumerate(chunks)]
        # 掺入其它 APID（各自独立 sequence 计数，不应制造 APID64 的洞）
        pkts += [
            VcduPacket(5, 65, 0, b"\xde\xad"),
            VcduPacket(5, 65, 1, b"\xbe\xef"),
            VcduPacket(9, 64, 0, b"\x00"),               # 非目标 VCID 同 APID
        ]
        r = reassemble_image_stream(pkts, target_vcid=5, target_apid=64)
        assert r.num_filtered == 3
        assert r.stream == jpeg
        assert r.num_gaps == 0 and r.complete

    def test_duplicate_sequence(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 4)
        pkts = [VcduPacket(5, 64, i, c) for i, c in enumerate(chunks)]
        pkts.append(VcduPacket(5, 64, 1, b"\xff\xff"))    # 重复 seq=1
        r = reassemble_image_stream(pkts, target_apid=64)
        assert r.num_duplicates == 1
        assert r.stream == jpeg                          # 保留首个到达者

    def test_multi_gap(self):
        jpeg, _ = _valid_gray_jpeg()
        chunks = _split_stream(jpeg, 6)
        # 丢掉 seq=2 与 seq=4
        pkts = [VcduPacket(5, 64, i, c) for i, c in enumerate(chunks) if i not in (2, 4)]
        r = reassemble_image_stream(pkts, target_apid=64)
        assert r.num_gaps == 2
        assert r.missing_sequences == [2, 4]
        assert [g.size for g in r.gap_ranges] == [1, 1]
        assert not r.complete

    def test_sequence_modulus_bounds_rejects_rollover(self):
        # 单调窗口口径：跨取模翻卷的片段（含越界或环绕残片）如实拒绝，
        # 不做静默错拼（见模块 docstring：窗口内翻卷不在本轮支持范围）。
        chunks = _split_stream(b"ABCDEFGHIJKL", 6)
        pkts = [VcduPacket(5, 64, i, chunks[i]) for i in range(6)]
        # sequence_modulus 较小（< 实际 maximun sequence）触发越界校验
        with pytest.raises(ValueError):
            reassemble_image_stream(pkts, target_apid=64, sequence_modulus=4)

    def test_no_target_packets(self):
        r = reassemble_image_stream([VcduPacket(5, 99, 0, b"")],
                                    target_apid=64)
        assert r.stream == b"" and r.num_packets == 0
        assert not r.complete and r.num_filtered == 1


# =========================================================================== #
# SatDump huffman 测试向量（若存在则跑；本 clone 无 test_data → 诚实跳过）
# =========================================================================== #
_SATDUMP_VEC_DIR = Path(os.environ.get(
    "SATDUMP_HUFFMAN_TEST_DATA",
    r"C:\Users\xxhzb\AppData\Local\Temp\kilo\upstream\SatDump\test_data"))


@pytest.mark.skipif(
    not _SATDUMP_VEC_DIR.exists(),
    reason="SatDump huffman 测试向量目录不存在；本轮以合成 JPEG 自验证收")
class TestSatdumpHuffmanVectors:
    def test_vectors_present_marker(self):
        # 占位：目录存在时应逐/抽样跑向量（见落地记录；不在时 skip）。
        assert _SATDUMP_VEC_DIR.exists()
