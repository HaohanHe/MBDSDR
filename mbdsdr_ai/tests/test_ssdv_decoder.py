# SPDX-License-Identifier: MIT
"""fsphil SSDV 256 字节包解码/编码核心模块级测试（干净室，仅公开协议）。

覆盖：
  - 包识别/同步状态机（前导 0x55 容忍）
  - 15 字节头解析（callsign base-40、w/h、flags、mcu id/offset）
  - RS(255,223) 纠错：t=16 内全恢复，17 误码交 CRC32 把关
  - CRC32 校验失败诚实丢弃
  - 往返：numpy 图 -> 包 -> Pillow 可打开 JPEG，尺寸/MCU 正确
  - 丢包 -> 缺失 MCU 列表诚实报告，不造假
  - No-FEC 模式
  - callsign 由参数传入，不内置任何呼号
"""
import io
import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mbdsdr_ai.ssdv_decoder import (  # noqa: E402
    PACKET_LEN,
    SsdvDecoder,
    SsdvEncoder,
    SsdvImage,
    TYPE_NOFEC,
    TYPE_NORMAL,
    base40_to_callsign,
    callsign_to_base40,
)


# --------------------------------------------------------------------------
# callsign base-40
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cs", ["BI4TST", "A", "ABCDE", "9"])
def test_callsign_roundtrip(cs):
    code = callsign_to_base40(cs)
    assert 0 <= code < (1 << 32)
    assert base40_to_callsign(code) == cs


def test_no_builtin_callsign():
    """模块源码不得硬编码任何呼号常量。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "ssdv_decoder.py")).read()
    head = src.split("class SsdvEncoder")[0]
    for bad in ("BI4MIB", "BI4TST", "DEFAULT_CALLSIGN"):
        assert bad not in head


# --------------------------------------------------------------------------
# 往返
# --------------------------------------------------------------------------
def _make_img(seed=1, w=32, h=32):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3)).astype(np.uint8)


@pytest.mark.parametrize("mcu_mode", [0, 3])
def test_roundtrip_opens_jpeg(mcu_mode):
    img = _make_img()
    enc = SsdvEncoder(callsign="BI4TST", quality=4, mcu_mode=mcu_mode)
    pkts = enc.encode_image(img)
    assert pkts, "至少产出一个包"
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for raw in pkts:
        for p in dec.feed(raw):
            if p is not None:
                imgset.add(p)
    res = imgset.build()
    assert not res.empty
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)
    assert res.missing_mcus == []
    assert len(res.received_mcus) == res.mcu_count


def test_packet_structure():
    enc = SsdvEncoder(callsign="BI4TST", quality=4, mcu_mode=3)
    pkts = enc.encode_image(_make_img())
    p = pkts[0]
    assert len(p) == PACKET_LEN == 256
    assert p[0] == 0x55           # sync
    assert p[1] == TYPE_NORMAL    # type
    code = (p[2] << 24) | (p[3] << 16) | (p[4] << 8) | p[5]
    assert base40_to_callsign(code) == "BI4TST"
    assert p[6] == 0              # image_id
    assert p[9] == 32 >> 4        # width/16
    assert p[10] == 32 >> 4       # height/16


# --------------------------------------------------------------------------
# RS 纠错
# --------------------------------------------------------------------------
def test_rs_corrects_16_errors():
    img = _make_img()
    enc = SsdvEncoder(callsign="X", quality=4, mcu_mode=3)
    pkts = enc.encode_image(img)
    p = bytearray(pkts[2])
    rng = np.random.default_rng(0)
    positions = rng.choice(range(1, 255), 16, replace=False)
    for x in positions:
        p[x] ^= int(rng.integers(1, 256))
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for i, raw in enumerate(pkts):
        if i == 2:
            raw = bytes(p)
        for q in dec.feed(raw):
            if q is not None:
                imgset.add(q)
    res = imgset.build()
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)
    assert res.missing_mcus == []


def test_rs_uncorrectable_dropped():
    """17 误码超 t=16 -> RS 失败 -> CRC 失败 -> 包被丢 -> 诚实报缺失 MCU。"""
    img = _make_img()
    enc = SsdvEncoder(callsign="X", quality=4, mcu_mode=3)
    pkts = enc.encode_image(img)
    p = bytearray(pkts[2])
    rng = np.random.default_rng(3)
    positions = rng.choice(range(1, 255), 17, replace=False)
    for x in positions:
        p[x] ^= int(rng.integers(1, 256))
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for i, raw in enumerate(pkts):
        if i == 2:
            raw = bytes(p)
        for q in dec.feed(raw):
            if q is not None:
                imgset.add(q)
    res = imgset.build()
    assert res.missing_mcus, "超纠错能力应诚实报缺失 MCU"
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)


# --------------------------------------------------------------------------
# 丢包
# --------------------------------------------------------------------------
def test_lost_packet_reported():
    img = _make_img()
    enc = SsdvEncoder(callsign="X", quality=4, mcu_mode=3)
    pkts = enc.encode_image(img)
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for i, raw in enumerate(pkts):
        if i == 1:        # 整包丢失
            continue
        for q in dec.feed(raw):
            if q is not None:
                imgset.add(q)
    res = imgset.build()
    assert res.missing_mcus, "丢包应报告缺失 MCU"
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)


# --------------------------------------------------------------------------
# No-FEC 模式
# --------------------------------------------------------------------------
def test_nofec_mode():
    img = _make_img()
    enc = SsdvEncoder(callsign="X", quality=4, mcu_mode=3, pkt_type=TYPE_NOFEC)
    pkts = enc.encode_image(img)
    assert all(p[1] == TYPE_NOFEC for p in pkts)
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for raw in pkts:
        for q in dec.feed(raw):
            if q is not None:
                imgset.add(q)
    res = imgset.build()
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)
    assert res.missing_mcus == []


# --------------------------------------------------------------------------
# 同步状态机：前导噪声/0x55
# --------------------------------------------------------------------------
def test_sync_resync_with_preamble():
    enc = SsdvEncoder(callsign="X", quality=4, mcu_mode=3)
    pkts = enc.encode_image(_make_img())
    dec = SsdvDecoder()
    imgset = SsdvImage(0)
    for raw in pkts:
        stream = b"\x55\x55\x55\x00\xFF" + raw
        for p in dec.feed(stream):
            if p is not None:
                imgset.add(p)
    res = imgset.build()
    assert res.missing_mcus == []
    im = Image.open(io.BytesIO(res.jpeg))
    im.load()
    assert im.size == (32, 32)


# --------------------------------------------------------------------------
# 空态
# --------------------------------------------------------------------------
def test_empty_input():
    dec = SsdvDecoder()
    assert dec.feed(b"") == []
    imgset = SsdvImage(0)
    res = imgset.build()
    assert res.empty
    assert res.jpeg == b""
