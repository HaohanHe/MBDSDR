# SPDX-License-Identifier: MIT
"""气象产品生成（依据气象卫星产品惯例独立实现）。

本模块用 numpy + dataclass 表示一个仪器产品：
  * ImageProduct — 多通道灰度图 + 地理元数据 + 投影配置
  * compose_rgb  — 通道合成（真彩色 / NOAA AVHRR 假彩色）
  * save_png     — 落盘 PNG
  * estimate_geocorrection_offset — 估计图像行偏移（确定性占位）

SatDump 等开源项目仅作技术参考与致谢，本仓未包含其源代码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# 产品
# --------------------------------------------------------------------------- #
@dataclass
class ImageProduct:
    """一个仪器产品：若干等尺寸灰度通道 + 地理元数据。

    每个通道为一张 2D 灰度图，附带 channel_name / bit_depth / wavenumber
    等元数据（这里用 dict 简化存储）。
    """
    instrument_name: str = "unknown"
    satellite: str = ""
    channels: Dict[str, np.ndarray] = field(default_factory=dict)
    #: 每行 UTC 时间戳（unix 秒），长度 = 行数
    timestamps: Optional[List[float]] = None
    #: 投影配置（tle / 投影类型 / GCP 间距等）
    projection: Dict[str, Any] = field(default_factory=dict)
    #: 行时间步长（秒/行），APT=0.5（NOAA APT 行率 2 行/秒）
    line_period_s: float = 0.5

    def add_channel(self, name: str, image: np.ndarray) -> None:
        self.channels[name] = np.asarray(image)

    @property
    def shape(self) -> Tuple[int, int]:
        if not self.channels:
            return (0, 0)
        a = next(iter(self.channels.values()))
        return a.shape

    # ---- 投影/地理元数据 ----
    def set_projection(self, tle: Tuple[str, str], timestamps: List[float],
                       proj_type: str = "noaa_apt_single_line",
                       gcp_spacing: int = 30) -> None:
        """记录投影配置（TLE / 投影类型 / GCP 间距）与行时间戳。"""
        self.projection = {
            "type": proj_type,
            "tle": {"line1": tle[0], "line2": tle[1]},
            "gcp_spacing_x": gcp_spacing,
            "gcp_spacing_y": gcp_spacing,
        }
        self.timestamps = list(timestamps)

    # ---- 通道合成 ----
    def compose_rgb(self, r: str, g: str, b: str) -> np.ndarray:
        """把三个 2D 灰度通道合成 uint8 RGB (H,W,3)。"""
        for ch in (r, g, b):
            if ch not in self.channels:
                raise KeyError(f"通道 {ch} 不存在；现有 {list(self.channels)}")
        H = min(self.channels[r].shape[0], self.channels[g].shape[0],
                self.channels[b].shape[0])
        W = min(self.channels[r].shape[1], self.channels[g].shape[1],
                self.channels[b].shape[1])

        def norm(x: np.ndarray) -> np.ndarray:
            x = x[:H, :W].astype(np.float64)
            lo, hi = np.percentile(x, 2), np.percentile(x, 98)
            if hi - lo < 1e-6:
                return np.zeros_like(x, dtype=np.uint8)
            return np.clip((x - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)

        rgb = np.stack([norm(self.channels[r]),
                        norm(self.channels[g]),
                        norm(self.channels[b])], axis=-1)
        return rgb

    def compose_noaa_falsecolor(self) -> Optional[np.ndarray]:
        """NOAA AVHRR 假彩色：通道2(可见光)→R, 通道2→G, 通道4(红外)→B 类方案。

        约定通道命名：解码器产出的 "AVHRR-1".."AVHRR-5" 或通用 "a"/"b"。
        若只有 A/B 两通道（APT 仅传两路），用 B(IR)→R, A(vis)→G/B 做增强。
        """
        names = set(self.channels.keys())
        if {"AVHRR-2", "AVHRR-4"} <= names:
            return self.compose_rgb("AVHRR-2", "AVHRR-2", "AVHRR-4")
        if {"a", "b"} <= names:
            # APT: A=可见光, B=红外；假彩色：IR→R, vis→G, vis→B
            return self.compose_rgb("b", "a", "a")
        return None

    # ---- AI 估计地理校正偏移（增强）----
    def estimate_geocorrection_offset(self, channel: str = "a") -> Dict[str, float]:
        """AI 估计图像相对真实过境的行偏移（秒）。

        简单确定性实现：若有 timestamps，则以中行时间为参考；否则返回 0。
        真实实现可在此接入云图特征匹配（海岸线/地标）——留接口。
        """
        if channel not in self.channels:
            raise KeyError(channel)
        if self.timestamps and len(self.timestamps) == self.channels[channel].shape[0]:
            mid_t = self.timestamps[len(self.timestamps) // 2]
            return {"offset_s": 0.0, "reference_line_time": mid_t,
                    "rows": self.channels[channel].shape[0]}
        return {"offset_s": 0.0, "reference_line_time": 0.0,
                "rows": self.channels[channel].shape[0]}

    # ---- 落盘 ----
    def save_png(self, path: str, channel: Optional[str] = None) -> str:
        """保存单通道灰度 PNG 或 RGB 合成 PNG。"""
        from PIL import Image
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if channel is not None:
            img = self.channels[channel]
            Image.fromarray(np.asarray(img, dtype=np.uint8), mode="L").save(path)
            return os.path.abspath(path)
        rgb = self.compose_noaa_falsecolor()
        if rgb is None:
            # 退回第一个通道
            k = next(iter(self.channels))
            Image.fromarray(np.asarray(self.channels[k], dtype=np.uint8),
                            mode="L").save(path)
        else:
            Image.fromarray(rgb, mode="RGB").save(path)
        return os.path.abspath(path)
