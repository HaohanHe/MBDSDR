"""
GK-2A JPEG2000 真解码测试
=========================

验证 mbdsdr_ai/gk2a_lrit.py 新增的 decode_jpeg2000()：
  - Pillow + libopenjp2 后端，对合法 .jp2 字节流真解码
  - 8-bit / 16-bit 灰度无损往返
  - 损坏/空数据优雅返回 None，不崩溃
  - 集成到 GK2AImageAssembler：JP2 段真解码；非 JP2 段回退无压缩直通
    并在 GK2AImage.compression 标注 "none[回退]"

运行: python3 -m pytest tests/test_gk2a_jp2.py -v
"""

import io
import os
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gk2a_lrit import (  # noqa: E402
    GK2AImageAssembler,
    build_gk2a_lrit_file,
    decode_jpeg2000,
)


def _encode_jp2_lossless(arr: np.ndarray) -> bytes:
    """把 numpy 灰度图无损编码为 .jp2 字节流（openjpeg 后端）。"""
    if arr.dtype == np.uint16:
        mode = "I;16"
    else:
        mode = "L"
    im = Image.fromarray(arr.astype(np.uint8) if mode == "L" else arr, mode=mode)
    buf = io.BytesIO()
    im.save(buf, format="JPEG2000", quality_mode="lossless")
    return buf.getvalue()


def _gradient_uint8(h: int = 256, w: int = 256) -> np.ndarray:
    x = np.linspace(0, 255, w, dtype=np.uint8)
    return np.repeat(x[None, :], h, axis=0)


class TestDecodeJPEG2000Unit(unittest.TestCase):
    """decode_jpeg2000 单元测试。"""

    def test_jp2_roundtrip_uint8(self):
        """8-bit 灰度：JP2 无损编码→解码，shape/像素完全一致。"""
        rng = np.random.default_rng(2026)
        src = rng.integers(0, 256, size=(256, 256), dtype=np.uint8)
        jp2 = _encode_jp2_lossless(src)
        out = decode_jpeg2000(jp2)
        self.assertIsNotNone(out, "合法 .jp2 流必须解出数组")
        self.assertEqual(out.shape, src.shape)
        self.assertEqual(out.dtype, np.uint8)
        self.assertTrue(np.array_equal(out, src),
                        "无损 JP2 往返后像素必须完全一致")

    def test_jp2_roundtrip_uint16(self):
        """16-bit 灰度：GK-2A 10/16-bit 真实位深保留为 uint16。"""
        rng = np.random.default_rng(7)
        src = rng.integers(0, 65536, size=(128, 128), dtype=np.uint16)
        jp2 = _encode_jp2_lossless(src)
        out = decode_jpeg2000(jp2)
        self.assertIsNotNone(out)
        self.assertEqual(out.shape, src.shape)
        self.assertEqual(out.dtype, np.uint16,
                         "16-bit JP2 必须解为 uint16，不能降级到 uint8")
        self.assertTrue(np.array_equal(out, src),
                        "无损 16-bit JP2 往返后像素必须完全一致")

    def test_decode_invalid_data_returns_none(self):
        """损坏字节（随机噪声）→ None，不抛异常。"""
        rng = np.random.default_rng(99)
        garbage = rng.integers(0, 256, size=4096, dtype=np.uint8).tobytes()
        self.assertIsNone(decode_jpeg2000(garbage))

    def test_decode_empty_returns_none(self):
        """空数据 → None。"""
        self.assertIsNone(decode_jpeg2000(b""))
        self.assertIsNone(decode_jpeg2000(bytes()))

    def test_decode_truncated_jp2_returns_none(self):
        """合法 JP2 头部被截断 → None（验证异常被吞掉，不崩溃）。"""
        src = _gradient_uint8(64, 64)
        jp2 = _encode_jp2_lossless(src)
        truncated = jp2[: len(jp2) // 2]  # 砍掉一半
        # 截断后 openjpeg 大概率报错；即使极少数情况下能解出部分，
        # 也只断言"不崩溃"——这里主要验证异常路径不抛。
        try:
            out = decode_jpeg2000(truncated)
        except Exception as e:  # pragma: no cover
            self.fail(f"截断 JP2 不应抛异常: {e!r}")
        self.assertIsNone(out)


class TestGK2APipelineIntegration(unittest.TestCase):
    """端到端：GK-2A 段组装器对 JP2 净荷的真解码 + 回退路径。"""

    def test_gk2a_pipeline_with_jp2_payload(self):
        """构造含合法 .jp2 净荷的 GK-2A 单段文件 → 组装出图，像素与源一致。"""
        src = _gradient_uint8(64, 128)  # 64 行 x 128 列单段
        jp2 = _encode_jp2_lossless(src)
        # compression_flag=1 模拟 GK-2A 真实下行的 JPEG2000 段
        fbuf = build_gk2a_lrit_file(
            src, line_nb=1, total_segments=1, image_seq=1,
            payload=jp2, compression_flag=1, bits_per_pixel=8,
        )
        asm = GK2AImageAssembler()
        img = asm.add_file(fbuf)
        self.assertIsNotNone(img, "单段整图应立即组装成功")
        self.assertEqual(img.width, 128)
        self.assertEqual(img.height, 64)
        self.assertEqual(img.pixels.shape, (64, 128))
        self.assertEqual(img.compression, "jpeg2000",
                         "JP2 真解码成功必须标注 compression=jpeg2000")
        self.assertTrue(np.array_equal(img.pixels, src),
                        "JP2 无损往返后整图像素应与源完全一致")

    def test_fallback_to_raw_when_not_jp2(self):
        """净荷是裸像素（非 JP2 魔数）→ 回退无压缩直通，标注 none[回退]。"""
        src = _gradient_uint8(32, 64)
        # 不传 payload → build_gk2a_lrit_file 写裸 uint8 像素（compression_flag=0）
        fbuf = build_gk2a_lrit_file(src, line_nb=1, total_segments=1, image_seq=2)
        asm = GK2AImageAssembler()
        img = asm.add_file(fbuf)
        self.assertIsNotNone(img)
        self.assertEqual(img.pixels.shape, (32, 64))
        self.assertTrue(np.array_equal(img.pixels, src))
        self.assertEqual(img.compression, "none[回退]",
                         "裸像素直通必须标注 none[回退]，不能假装 jpeg2000")

    def test_fallback_on_corrupt_payload(self):
        """净荷是损坏字节（既不是合法 JP2 也不是裸像素宽度对齐）→ 不崩溃。

        实际 GK-2A 中：JP2 解码失败后会按 uint8 直通尝试；即便直通产生的
        行数不完整，也只是输出一张偏小的画布，绝不能抛异常。
        """
        # 用一个与 src 宽高不匹配的"假图像"头 + 随机字节净荷
        fake_hdr_img = np.zeros((16, 32), dtype=np.uint8)
        garbage = os.urandom(500)  # 既不是合法 JP2，也不是 16*32=512 字节整图
        fbuf = build_gk2a_lrit_file(
            fake_hdr_img, line_nb=1, total_segments=1, image_seq=3,
            payload=garbage, compression_flag=1,
        )
        asm = GK2AImageAssembler()
        # 不抛异常即可；输出应是回退直通路径产生的画布
        img = asm.add_file(fbuf)
        self.assertIsNotNone(img)
        self.assertEqual(img.compression, "none[回退]")

    def test_multisegment_jp2_assembly(self):
        """两段 JP2（line_nb=1 与 line_nb=33）拼成 64 行整图。"""
        seg0 = _gradient_uint8(32, 64)            # 行 1..32
        seg1 = (255 - _gradient_uint8(32, 64)).astype(np.uint8)  # 行 33..64
        asm = GK2AImageAssembler()
        # 第 1 段
        f0 = build_gk2a_lrit_file(seg0, line_nb=1, total_segments=2, image_seq=9,
                                  payload=_encode_jp2_lossless(seg0),
                                  compression_flag=1)
        self.assertIsNone(asm.add_file(f0))  # 段未齐
        # 第 2 段
        f1 = build_gk2a_lrit_file(seg1, line_nb=33, total_segments=2, image_seq=9,
                                  payload=_encode_jp2_lossless(seg1),
                                  compression_flag=1)
        img = asm.add_file(f1)
        self.assertIsNotNone(img)
        self.assertEqual(img.height, 64)
        self.assertEqual(img.width, 64)
        self.assertEqual(img.compression, "jpeg2000")
        self.assertTrue(np.array_equal(img.pixels[:32, :], seg0))
        self.assertTrue(np.array_equal(img.pixels[32:, :], seg1))


if __name__ == "__main__":
    unittest.main()
