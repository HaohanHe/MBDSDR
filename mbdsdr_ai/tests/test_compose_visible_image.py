# SPDX-License-Identifier: MIT
"""compose_visible_image：LRPT 帧 → RFC 2435 段重组 → 熵解码 → 图像。

合成向量口径与 tests/test_lrpt_iq_chain.py 一致（Pillow 编标准 JPEG 取熵
数据 → RFC 2435 段），但走 meteor_sat.compose_visible_image 的帧入口
（lrpc_build_frame 布局：同步字 + VCID + APID + 段载荷），验证：
  · 多帧多 APID 中正确选流（target_apid / 段数最多）；
  · 完整段 → 真实解出图像（尺寸对得上、非纯色）；
  · 0/1 位流条目与字节条目等价；
  · 诚实空态：无输入/无同步/段不全/坏数据 → None，绝不出半幅图。

红线：合成向量，非真实 OTA（仓内真实样本为零，AGENTS 如实标注）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.meteor_sat import (  # noqa: E402
    _cadu_item_to_bytes,
    compose_visible_image,
    lrpc_build_frame,
)

W, H = 64, 48
Q_FIELD = 32
VCID = 5
APID_VIS = 64
APID_OTHER = 65


def _jpeg_entropy_scan(width: int = W, height: int = H) -> bytes:
    """Pillow 编一张标准 3 分量 JPEG，取 SOS 之后 EOI 之前的熵数据。"""
    from io import BytesIO

    from PIL import Image
    ramp = (np.indices((height, width))[0] * 255 // max(1, height - 1)
            ).astype(np.uint8)
    rgb = np.stack([ramp, (255 - ramp), (ramp // 2)], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    data = buf.getvalue()
    i = data.find(b"\xff\xda")
    assert i > 0, "Pillow 未产出 JPEG"
    seg_len = int.from_bytes(data[i + 2:i + 4], "big")
    start = i + 2 + seg_len
    end = data.find(b"\xff\xd9", start)
    assert end > start, "未找到熵数据"
    return data[start:end]


def _segment(frag_offset: int, payload: bytes, width: int = W,
             height: int = H, q: int = Q_FIELD) -> bytes:
    """RFC 2435 图像段（仓内既有测试同款布局）。"""
    head = bytes([0x00, 0x00, (frag_offset >> 8) & 0xFF, frag_offset & 0xFF])
    if frag_offset == 0:
        dim = bytes([(width >> 8) & 0xFF, width & 0xFF,
                     (height >> 8) & 0xFF, height & 0xFF,
                     0x00, q, 0x08, 0x03])
        return head + dim + payload
    return head + payload


def _scan_segments(scan: bytes | None = None) -> list:
    """熵数据 → 两段（首段带尺寸头）。"""
    if scan is None:
        scan = _jpeg_entropy_scan()
    cut = len(scan) // 2
    return [_segment(0, scan[:cut]), _segment(cut, scan[cut:])]


def _frames_for(segments: list, apid: int = APID_VIS) -> list:
    """图像段 → LRPT 帧字节（lrpc_build_frame 布局）。"""
    return [lrpc_build_frame(VCID, apid, seg) for seg in segments]


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #

class TestItemNormalization:
    def test_bytes_passthrough(self):
        assert _cadu_item_to_bytes(b"\x01\x02") == b"\x01\x02"

    def test_bit_array_packs_msb_first(self):
        bits = np.array([0, 0, 0, 0, 0, 0, 0, 1] * 3, dtype=np.uint8)
        assert _cadu_item_to_bytes(bits) == b"\x01\x01\x01"

    def test_uint8_bytes_kept_as_bytes(self):
        raw = np.frombuffer(b"\x1d\xfc\xdc\xff", dtype=np.uint8)
        assert _cadu_item_to_bytes(raw) == b"\x1d\xfc\xdc\xff"

    def test_as_bits_override(self):
        # 病态输入：全 0/1 的字节帧，调用方显式声明是字节
        arr = np.array([0, 1, 1, 0], dtype=np.uint8)
        assert _cadu_item_to_bytes(arr, as_bits=False) == b"\x00\x01\x01\x00"


# --------------------------------------------------------------------------- #
# 端到端：帧 → 图
# --------------------------------------------------------------------------- #

class TestComposeE2E:
    def test_full_frames_decode_to_image(self):
        """完整两段 → 真实解出图像（尺寸对、非纯色）。"""
        frames = _frames_for(_scan_segments())
        img = compose_visible_image(frames)
        assert img is not None, "完整段必须解出图像"
        assert img.shape[:2] == (H, W), img.shape
        assert img.dtype == np.uint8
        assert float(np.asarray(img).std()) > 5.0, "解出的图不应是纯色"

    def test_bit_array_entries_equal_bytes_entries(self):
        """0/1 位流条目与字节条目必须等价（extract_cadu 产出是位流）。"""
        frames = _frames_for(_scan_segments())
        img_bytes = compose_visible_image(frames)
        bits = [np.unpackbits(np.frombuffer(f, dtype=np.uint8))
                for f in frames]
        img_bits = compose_visible_image(bits)
        assert img_bits is not None
        np.testing.assert_array_equal(img_bytes, img_bits)

    def test_target_apid_selects_stream(self):
        """APID 混流：target_apid 必须选中对应流，不搅混。"""
        segs = _scan_segments()
        # 干扰流：另一 APID 的不可解段
        noise_frames = _frames_for(
            [_segment(0x7FFF, b"\x00" * 4)], apid=APID_OTHER)
        frames = _frames_for(segs, apid=APID_VIS) + noise_frames

        img = compose_visible_image(frames, target_apid=APID_VIS)
        assert img is not None, "显式 target_apid 不应被干扰流拖垮"

        # 目标 APID 不存在 → 诚实 None
        assert compose_visible_image(frames, target_apid=99) is None

    def test_default_picks_largest_apid_stream(self):
        """不指定 target_apid 时取段数最多的 APID。"""
        segs = _scan_segments()               # APID_VIS 2 段
        noise = [_segment(0x7FFF, b"\x00" * 4)]  # APID_OTHER 1 段
        frames = (_frames_for(segs, apid=APID_VIS)
                  + _frames_for(noise, apid=APID_OTHER))
        img = compose_visible_image(frames)
        assert img is not None
        assert img.shape[:2] == (H, W)

    def test_headerless_payloads(self):
        """sync=b\"\" + header_len=0：裸段载荷直接进重组。"""
        img = compose_visible_image(_scan_segments(),
                                    sync=b"", header_len=0)
        assert img is not None
        assert img.shape[:2] == (H, W)


# --------------------------------------------------------------------------- #
# 诚实空态（绝不半幅/伪造）
# --------------------------------------------------------------------------- #

class TestHonestEmpty:
    def test_empty_input_returns_none(self):
        assert compose_visible_image([]) is None

    def test_no_sync_returns_none(self):
        assert compose_visible_image([b"\x00" * 40, b"\xff" * 40]) is None

    def test_incomplete_segments_return_none(self):
        """缺首段/有洞 → None，不得半幅出图。"""
        segs = _scan_segments()
        only_tail = _frames_for([segs[1]], apid=APID_VIS)
        assert compose_visible_image(only_tail) is None

        broken = _frames_for(
            [segs[0], _segment(0x7FFF, b"\x00" * 4)], apid=APID_VIS)
        assert compose_visible_image(broken) is None

    def test_short_frame_returns_none(self):
        """同步字后不足头部长度 → 计入短帧，整体 None。"""
        short = lrpc_build_frame(VCID, APID_VIS, b"")  # 无载荷
        assert compose_visible_image([short]) is None

    def test_garbage_payload_returns_none(self):
        """坏熵数据：重组可能 complete，但熵解码必须诚实失败返回 None。"""
        junk = _frames_for([_segment(0, b"\xde\xad\xbe\xef" * 40)])
        assert compose_visible_image(junk) is None

    def test_none_returned_not_fake_array(self):
        """失败路径返回值必须是 None（不是零图/渐变图冒充）。"""
        out = compose_visible_image([b""])
        assert out is None
