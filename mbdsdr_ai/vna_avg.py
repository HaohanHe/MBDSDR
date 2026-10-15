# SPDX-License-Identifier: MIT
"""扫间平均 + 历史回溯（NanoVNA-App 机制审计落地第四批）。

上游机制（types.h MAX_HISTORY=256 / Graphs.cpp fetchSamples）：每个频点
保留最多 256 帧历史，平均长度取 2 的幂（timeAverageLevel），默认按
**幅度²与相位分别平均**再合成（比 IQ 直接平均更抗相位漂移），可选 IQ
直平均；history_position 支持回放任意历史偏移。

本实现（SweepAverager）：
- ring buffer 深度固定 max_depth（默认 256，上游口径）；
- push(sweep) 入帧并返回当前平均结果；平均长度 avg_len 必须是 2 的幂
  且 <= 已入帧数，否则显式 ValueError（不静默截断）；
- mode="mp"（默认）：平均 |Z|² 与 unwrap 后相位，再合成 √(|Z|²̄)·e^{jφ̄}；
  mode="iq"：I、Q 分别平均；
- history(offset=0) 回放最近第 offset 帧（0=最新），越界显式 IndexError。

内存口径：max_depth × n_points × 16B（complex128）。10000 点 × 256 帧
≈ 41MB——是上游 float 方案的 2 倍，用内存换实现简洁，docstring 登记。

诚实边界：跨 ±π 相位跳变靠 unwrap 连续性处理；若单帧内相位本身不连续
（如扫频段拼接），"mp" 模式的相位平均会引入伪影——调用方应改用 "iq"。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

__all__ = ["SweepAverager"]


class SweepAverager:
    """扫间平均器（环形历史缓冲）。接口见模块 docstring。"""

    def __init__(self, n_points: int, max_depth: int = 256,
                 mode: str = "mp") -> None:
        if n_points < 1:
            raise ValueError(f"n_points 须为正，收到 {n_points}")
        if max_depth < 1:
            raise ValueError(f"max_depth 须为正，收到 {max_depth}")
        if mode not in ("mp", "iq"):
            raise ValueError(f"mode 须为 'mp'/'iq'，收到 {mode!r}")
        self.n_points = n_points
        self.max_depth = max_depth
        self.mode = mode
        self._buf = np.zeros((max_depth, n_points), dtype=np.complex128)
        self._count = 0          # 已入帧总数（可超过 max_depth，环形回绕）
        self._prev_phase: Optional[np.ndarray] = None

    def __len__(self) -> int:
        return min(self._count, self.max_depth)

    def push(self, sweep, avg_len: int = 1) -> np.ndarray:
        """入一帧并返回最近 avg_len 帧的平均（avg_len 为 2 的幂）。"""
        s = np.asarray(sweep, dtype=np.complex128)
        if s.shape != (self.n_points,):
            raise ValueError(
                f"帧形状 {s.shape} != ({self.n_points},)")
        self._buf[self._count % self.max_depth] = s
        self._count += 1
        return self.average(avg_len)

    def _frames(self, avg_len: int) -> np.ndarray:
        if avg_len < 1 or avg_len & (avg_len - 1):
            raise ValueError(f"avg_len 必须是 2 的幂，收到 {avg_len}")
        avail = len(self)
        if avg_len > avail:
            raise ValueError(
                f"avg_len {avg_len} 超过已入帧数 {avail}（诚实报错不截断）")
        newest = self._count % self.max_depth            # 下一写入位 = 最旧帧
        idx = (newest - avg_len + np.arange(avg_len)) % self.max_depth
        return self._buf[idx]

    def average(self, avg_len: int = 1) -> np.ndarray:
        """当前最近 avg_len 帧的平均（不写入新帧）。"""
        frames = self._frames(avg_len)
        if self.mode == "iq":
            return frames.mean(axis=0)
        mag2 = (np.abs(frames) ** 2).mean(axis=0)
        ph = np.unwrap(np.angle(frames), axis=0).mean(axis=0)
        return np.sqrt(mag2) * np.exp(1j * ph)

    def history(self, offset: int = 0) -> np.ndarray:
        """回放最近第 offset 帧（0=最新入帧）。越界 IndexError。"""
        if offset < 0 or offset >= len(self):
            raise IndexError(
                f"offset {offset} 超出已入帧数 {len(self)}")
        pos = (self._count - 1 - offset) % self.max_depth
        return self._buf[pos].copy()
