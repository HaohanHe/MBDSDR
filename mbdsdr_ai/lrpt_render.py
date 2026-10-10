# SPDX-License-Identifier: MIT
"""LRPT 云图渲染（仿真帧，Python 侧最小集）。

本模块依据 SatDump meteor_support 图像解码机制（file:line 证据见 lrpt-render.md）
自述重写，纯 NumPy + Pillow。

图像格式（MSU-MR LRPT，Meteor-M2）：
  - APID 64..69 → 通道 0..5（lrpt_msumr_reader.cpp:37-48）
  - 每段 (Segment) = 8 行 × 112 列 = 896 像素灰度（segment.cpp:165）
  - 每行 = 14 段横向拼接 = 1568 列 × 8 行（lrpt_msumr_reader.cpp:198-201）
  - 整幅图 = 多行纵向堆叠

红线：仿真数据，非真实云图；Huffman+IDCT 压缩留待后续；MIT。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

__all__ = [
    "LRPT_SEGMENT_COLS",
    "LRPT_SEGMENT_ROWS",
    "LRPT_SEGMENTS_PER_LINE",
    "LRPT_LINE_WIDTH",
    "APID_TO_CHANNEL",
    "LrptSegment",
    "LrptVirtualChannel",
    "generate_cloud_simulation",
    "save_png",
]

# --------------------------------------------------------------------------- #
# 格式常量（证据 lrpt-render.md §2）
# --------------------------------------------------------------------------- #
LRPT_SEGMENT_ROWS: int = 8          # 每段 8 行（segment.cpp:165）
LRPT_SEGMENT_COLS: int = 112        # 每段 112 列（14 块 × 8 像素，segment.cpp:165）
LRPT_SEGMENTS_PER_LINE: int = 14    # 每行 14 段（lrpt_msumr_reader.cpp:198）
LRPT_LINE_WIDTH: int = LRPT_SEGMENT_COLS * LRPT_SEGMENTS_PER_LINE  # = 1568

#: APID → 通道号（lrpt_msumr_reader.cpp:37-48）
APID_TO_CHANNEL: Dict[int, int] = {
    64: 0,
    65: 1,
    66: 2,
    67: 3,
    68: 4,
    69: 5,
}


# --------------------------------------------------------------------------- #
# 段 / 虚拟通道数据结构
# --------------------------------------------------------------------------- #
@dataclass
class LrptSegment:
    """LRPT 图像段（简化：直接存像素，不走 Huffman+IDCT）。"""
    channel: int
    mcu_count: int          # MCUN / 14 → 行号（lrpt_msumr_reader.cpp:59）
    pixels: np.ndarray      # shape (8, 112) uint8

    @property
    def valid(self) -> bool:
        return self.pixels.size > 0


@dataclass
class LrptVirtualChannel:
    """一个通道（APID）的所有段缓存。"""
    channel: int
    segments: Dict[int, LrptSegment] = field(default_factory=dict)

    def add_segment(self, seg: LrptSegment) -> None:
        self.segments[seg.mcu_count] = seg

    def to_image(self) -> np.ndarray:
        """把所有段拼成灰度图 (H, W) uint8。"""
        if not self.segments:
            return np.empty((0, LRPT_LINE_WIDTH), dtype=np.uint8)
        # mcu_count 是全局段索引（0..N），除以 14 得行号
        mcu_keys = list(self.segments.keys())
        min_row = min(mcu_keys) // LRPT_SEGMENTS_PER_LINE
        max_row = max(mcu_keys) // LRPT_SEGMENTS_PER_LINE
        n_rows = max_row - min_row + 1
        height = n_rows * LRPT_SEGMENT_ROWS
        img = np.zeros((height, LRPT_LINE_WIDTH), dtype=np.uint8)
        for row_offset in range(n_rows):
            row_idx = min_row + row_offset
            for seg_idx in range(LRPT_SEGMENTS_PER_LINE):
                seg_key = row_idx * LRPT_SEGMENTS_PER_LINE + seg_idx
                if seg_key in self.segments:
                    seg = self.segments[seg_key]
                    col_offset = seg_idx * LRPT_SEGMENT_COLS
                    img[row_offset * LRPT_SEGMENT_ROWS:
                        (row_offset + 1) * LRPT_SEGMENT_ROWS,
                        col_offset:col_offset + LRPT_SEGMENT_COLS] = seg.pixels
        return img


# --------------------------------------------------------------------------- #
# 仿真云图生成（确定性 seed）
# --------------------------------------------------------------------------- #
def generate_cloud_simulation(seed: int = 42,
                              n_lines: int = 32) -> Dict[int, np.ndarray]:
    """生成确定性仿真云图（灰度渐变 + 类云块）。

    诚实声明：合成数据，非真实 LRPT 云图；仅用于验证渲染管线。

    参数:
        seed:     RNG 种子（固定可复现）。
        n_lines:  行数（每行 8 像素高）。

    返回:
        {channel: image_array} —— channel 0/1/2 三个通道，shape (H, 1568)。
    """
    rng = np.random.default_rng(seed)
    height = n_lines * LRPT_SEGMENT_ROWS
    width = LRPT_LINE_WIDTH
    channels = {}

    for ch in range(3):  # 通道 0/1/2
        # 基础渐变：从上到下由暗到亮（模拟昼夜/海况）
        base = np.linspace(40, 200, height, dtype=np.float32)
        base = np.tile(base[:, None], (1, width))

        # 叠加类云块（高斯斑）
        n_clouds = 20 + ch * 5
        for _ in range(n_clouds):
            cx = rng.integers(0, width)
            cy = rng.integers(0, height)
            sigma = rng.integers(20, 80)
            intensity = rng.integers(30, 100)
            y, x = np.ogrid[:height, :width]
            cloud = intensity * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2))
            base += cloud

        # 通道 2（IR）偏暗
        if ch == 2:
            base = base * 0.6

        img = np.clip(base, 0, 255).astype(np.uint8)
        channels[ch] = img

    return channels


# --------------------------------------------------------------------------- #
# 渲染工具
# --------------------------------------------------------------------------- #
def save_png(img: np.ndarray, path: str, width: Optional[int] = None) -> str:
    """保存灰度图为 PNG。

    参数:
        img:    (H, W) uint8 灰度图。
        path:   输出路径。
        width:  可选目标宽度（等比缩放）。

    返回:
        实际保存路径。
    """
    if img.size == 0:
        raise ValueError("空图像不能保存 PNG（诚实空态）")
    pil_img = Image.fromarray(img, mode="L")
    if width is not None and width != img.shape[1]:
        ratio = width / img.shape[1]
        new_height = int(img.shape[0] * ratio)
        pil_img = pil_img.resize((width, new_height), Image.NEAREST)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pil_img.save(path)
    return path


def image_to_segments(img: np.ndarray, channel: int) -> List[LrptSegment]:
    """把整幅灰度图拆成 LRPT 段列表（逆操作：像素 → 段）。

    用于 round-trip 测试：已知像素 → 段 → 拼成图像 → 一致。
    """
    h, w = img.shape
    segments = []
    n_lines = h // LRPT_SEGMENT_ROWS
    for line in range(n_lines):
        for seg_idx in range(LRPT_SEGMENTS_PER_LINE):
            row_start = line * LRPT_SEGMENT_ROWS
            col_start = seg_idx * LRPT_SEGMENT_COLS
            if col_start + LRPT_SEGMENT_COLS > w:
                break
            pixels = img[row_start:row_start + LRPT_SEGMENT_ROWS,
                         col_start:col_start + LRPT_SEGMENT_COLS]
            mcu_count = line * LRPT_SEGMENTS_PER_LINE + seg_idx
            segments.append(LrptSegment(channel=channel, mcu_count=mcu_count,
                                        pixels=pixels.copy()))
    return segments
