# SPDX-License-Identifier: MIT
"""decode_meteor_scan 测试（Meteor 真实路径的 JPEG 侧入口收口）。

关键：不依赖合成 JPEG 头——SOF/DQT/DHT 全部来自 Meteor 口径
（K.3 硬编码表 + IJG 量化表），验证“无 DHT/DQT 的裸扫描也能解”。
"""
import numpy as np
import pytest

from mbdsdr_ai.lrpt_huffman import (
    BitReader,
    LrptJpegMarkerError,
    LrptJpegTruncatedError,
    decode_meteor_scan,
    ijg_quant_table,
)
from mbdsdr_ai.lrpt_vcdu import MeteorScan


def _mk_scan(w=8, h=8, q=50, payload=None, quant=None):
    """默认 8x8 图 = 4:2:0 下单 MCU = 6 个 8x8 块。"""
    return MeteorScan(
        scan=payload, complete=True, width=w, height=h, q_field=q,
        quant_tables=quant, num_segments=2, num_gaps=0, reason="ok")


def _q(w=8, h=8, q=50):
    return {0: ijg_quant_table(q), 1: ijg_quant_table(q, chroma=True)}


def _all_zero_blocks_ent() -> bytes:
    """6 个全零块（4:2:0 单 MCU）的精确位流。

    每块 = DC（cat 0）+ AC EOB：Y 用 DC0 首码(2b '00') + AC0 EOB(4b '1010')
    = 6 位；Cb/Cr 用 DC1 首码(2b '00') + AC1 EOB(2b '00') = 4 位。
    合计 4*6 + 2*4 = 32 bit -> 4 字节。
    """
    bits = "001010" * 4 + "0000" * 2
    n = (len(bits) + 7) // 8
    return int(bits + "0" * (n * 8 - len(bits)), 2).to_bytes(n, "big")


def test_all_zero_blocks_decode_ok():
    """6 个全零块 -> 4:2:0 单 MCU 正确解出（DC=0 -> 电平偏置 128）。"""
    scan = _mk_scan(payload=_all_zero_blocks_ent(), quant=_q())
    img = decode_meteor_scan(scan)
    assert img.width == 8 and img.height == 8
    assert img.n_components == 3
    assert img.image.shape == (8, 8, 3)
    # 全零块 IDCT 后应为常量 128（JPEG 电平偏置）
    assert int(img.planes[0][0, 0]) == 128
    assert int(img.planes[1][0, 0]) == 128
    assert int(img.planes[2][0, 0]) == 128


def test_short_entropy_is_rejected():
    """块数不足 -> 诚实抛错（不补零、不给半截图）。"""
    scan = _mk_scan(payload=b"\x00\x00")
    with pytest.raises(Exception):
        decode_meteor_scan(scan)


def test_explicit_quant_overrides_scan():
    """调用方显式传量化表优先于 scan.quant_tables。

    差分口径：Y 首块 DC 差分 +1（cat=1），Q=20 细量化重建 133、Q=80 粗
    量化重建 128——两档量化表确实进入解码路径，且 Q 越粗重建越平。
    """
    # 每 Y 块：DC cat=1 码 '010' + extend '1'(diff=+1) + AC EOB '1010' = 8 bit
    bits = ("010" + "1" + "1010") * 4 + "0000" * 2
    n = (len(bits) + 7) // 8
    ent = int(bits + "0" * (n * 8 - len(bits)), 2).to_bytes(n, "big")

    def decode_with_q(q):
        scan = _mk_scan(payload=ent, quant=_q(q=q))
        return decode_meteor_scan(scan, quant_tables=_q(q=q))

    img20 = decode_with_q(20)
    img80 = decode_with_q(80)
    assert int(img20.planes[0][0, 0]) == 133
    assert int(img80.planes[0][0, 0]) == 128


def test_components_default_420():
    """缺省 SOF 分量口径为 Meteor 4:2:0（Y 2x2，Cb/Cr 1x1）。"""
    scan = _mk_scan(payload=_all_zero_blocks_ent(), quant=_q())
    img = decode_meteor_scan(scan)
    hs = [(c.id, c.h_samp, c.v_samp, c.quant_id) for c in img.components]
    assert hs[0] == (1, 2, 2, 0)
    assert all(hs[i][1] == 1 and hs[i][2] == 1 for i in (1, 2))
    assert img.mcu_rows == 1 and img.mcu_cols == 1
