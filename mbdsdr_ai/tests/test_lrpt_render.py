# SPDX-License-Identifier: MIT
"""LRPT 云图渲染确定性互测（仿真帧，Python 侧）。"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from mbdsdr_ai.lrpt_render import (
    LRPT_LINE_WIDTH,
    LRPT_SEGMENTS_PER_LINE,
    LRPT_SEGMENT_COLS,
    LRPT_SEGMENT_ROWS,
    APID_TO_CHANNEL,
    LrptSegment,
    LrptVirtualChannel,
    generate_cloud_simulation,
    image_to_segments,
    save_png,
)

# --------------------------------------------------------------------------- #
# 格式常量
# --------------------------------------------------------------------------- #

class TestFormatConstants:
    def test_segment_size(self):
        assert LRPT_SEGMENT_ROWS == 8
        assert LRPT_SEGMENT_COLS == 112
        assert LRPT_SEGMENTS_PER_LINE == 14
        assert LRPT_LINE_WIDTH == 1568  # 112 * 14

    def test_apid_mapping(self):
        assert APID_TO_CHANNEL[64] == 0
        assert APID_TO_CHANNEL[69] == 5
        assert len(APID_TO_CHANNEL) == 6


# --------------------------------------------------------------------------- #
# 仿真云图生成
# --------------------------------------------------------------------------- #

class TestCloudSimulation:
    def test_deterministic(self):
        """同 seed 两次生成一致。"""
        c1 = generate_cloud_simulation(seed=42, n_lines=16)
        c2 = generate_cloud_simulation(seed=42, n_lines=16)
        for ch in c1:
            np.testing.assert_array_equal(c1[ch], c2[ch])

    def test_three_channels(self):
        c = generate_cloud_simulation(seed=0, n_lines=16)
        assert set(c.keys()) == {0, 1, 2}

    def test_shape_and_dtype(self):
        c = generate_cloud_simulation(seed=0, n_lines=16)
        for ch, img in c.items():
            assert img.dtype == np.uint8
            assert img.shape[1] == LRPT_LINE_WIDTH
            assert img.shape[0] == 16 * LRPT_SEGMENT_ROWS
            assert img.min() >= 0
            assert img.max() <= 255


# --------------------------------------------------------------------------- #
# 段 → 图像 round-trip
# --------------------------------------------------------------------------- #

class TestSegmentRoundTrip:
    def test_image_to_segments_to_image(self):
        """已知像素 → 段 → 拼成图像 → 一致。"""
        rng = np.random.default_rng(99)
        img = rng.integers(0, 256, size=(32, LRPT_LINE_WIDTH), dtype=np.uint8)
        segs = image_to_segments(img, channel=0)
        assert len(segs) == (32 // 8) * LRPT_SEGMENTS_PER_LINE

        vc = LrptVirtualChannel(channel=0)
        for s in segs:
            vc.add_segment(s)
        recovered = vc.to_image()
        np.testing.assert_array_equal(img, recovered)

    def test_segment_pixels(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 256, size=(8, LRPT_LINE_WIDTH), dtype=np.uint8)
        segs = image_to_segments(img, channel=1)
        assert len(segs) == LRPT_SEGMENTS_PER_LINE
        for s in segs:
            assert s.pixels.shape == (LRPT_SEGMENT_ROWS, LRPT_SEGMENT_COLS)
            assert s.channel == 1


# --------------------------------------------------------------------------- #
# PNG 渲染
# --------------------------------------------------------------------------- #

class TestPngRender:
    def test_save_png(self):
        """PNG 落盘成功，文件大小 > 0。"""
        img = generate_cloud_simulation(seed=0, n_lines=8)[0]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.png")
            save_png(img, path)
            assert os.path.exists(path)
            assert os.path.getsize(path) > 100

    def test_save_png_resized(self):
        """等比缩放到目标宽度。"""
        img = generate_cloud_simulation(seed=0, n_lines=8)[0]
        with tempfile.TemporaryDirectory() as td:
            for w in [640, 960, 1568]:
                path = os.path.join(td, f"test_{w}.png")
                save_png(img, path, width=w)
                assert os.path.exists(path)

    def test_empty_image_raises(self):
        """空图像 → 抛 ValueError（诚实空态）。"""
        vc = LrptVirtualChannel(channel=0)
        img = vc.to_image()
        with pytest.raises(ValueError):
            save_png(img, "/tmp/should_not_exist.png")


# --------------------------------------------------------------------------- #
# 诚实空态
# --------------------------------------------------------------------------- #

class TestHonestEmpty:
    def test_no_segments_empty_image(self):
        """无段 → 空图像（诚实空态）。"""
        vc = LrptVirtualChannel(channel=0)
        img = vc.to_image()
        assert img.size == 0

    def test_partial_segments_zero_fill(self):
        """缺段 → 该位置填 0（黑），不伪造数据。"""
        vc = LrptVirtualChannel(channel=0)
        # 只加第 0 段，缺其余
        seg = LrptSegment(channel=0, mcu_count=0,
                          pixels=np.full((8, 112), 200, dtype=np.uint8))
        vc.add_segment(seg)
        img = vc.to_image()
        assert img.shape == (8, LRPT_LINE_WIDTH)
        # 前 112 列 = 200，其余 = 0
        assert np.all(img[:, :112] == 200)
        assert np.all(img[:, 112:] == 0)
