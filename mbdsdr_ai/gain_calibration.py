# SPDX-License-Identifier: MIT
"""
增益校准表（Gain Calibration Table）
======================================

对照 SDR++ 各 source 模块的增益/频响校准（source_modules/spectran_source/src/main.cpp、
bladerf_source、hackrf_source 里的 gain stage 表）：不同频率下接收机真实增益
不同，需要一张"频率 → 增益偏移"表来补偿频响。

本模块是纯数据层：
- 频率-增益偏移表（分段线性插值）
- JSON 持久化
- :meth:`apply` 把校准偏移加到测得功率上
- AI 增强：:meth:`auto_calibrate` 从扫频测量结果（频响曲线）自动生成校准表

红线：校准表只补偿"已知频响偏差"，不凭空造读数；合成信号仅单测用。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GainCalibrationTable:
    """频率-增益偏移校准表。

    ``points`` 是 [(freq_hz, offset_db), ...]，按 freq 升序。
    offset_db 表示：在该频率测得的功率比理想值偏低 offset_db，需要加上。
    （即 apply 时返回 measured + offset_db(freq)）。
    """

    points: List[Tuple[float, float]] = field(default_factory=list)
    device: str = ""                # 设备名（"hackrf"/"plutosdr"/...）
    note: str = ""

    # ------------------------------------------------------------------
    def add_point(self, freq_hz: float, offset_db: float) -> None:
        """插入一个校准点并保持升序。"""
        self.points.append((float(freq_hz), float(offset_db)))
        self.points.sort(key=lambda p: p[0])

    def offset_at(self, freq_hz: float) -> float:
        """分段线性插值取频率处的增益偏移；表外夹紧端点。"""
        if not self.points:
            return 0.0
        freqs = np.array([p[0] for p in self.points])
        offs = np.array([p[1] for p in self.points])
        return float(np.interp(float(freq_hz), freqs, offs))

    def apply(self, measured_db: float, freq_hz: float) -> float:
        """把测得功率 dB 加上该频率处的校准偏移。"""
        return float(measured_db) + self.offset_at(freq_hz)

    # ------------------------------------------------------------------
    # JSON 持久化
    # ------------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps({
            "device": self.device,
            "note": self.note,
            "points": [[f, o] for (f, o) in self.points],
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "GainCalibrationTable":
        d = json.loads(text)
        tbl = cls(device=d.get("device", ""), note=d.get("note", ""))
        for f, o in d.get("points", []):
            tbl.add_point(f, o)
        return tbl

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @classmethod
    def load(cls, path: str) -> "GainCalibrationTable":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(fh.read())

    # ------------------------------------------------------------------
    # AI 增强：从扫频频响自动生成校准表
    # ------------------------------------------------------------------
    @classmethod
    def auto_calibrate(cls,
                       freqs_hz: np.ndarray,
                       measured_db: np.ndarray,
                       ref_level_db: float = 0.0,
                       device: str = "auto") -> "GainCalibrationTable":
        """AI 自动校准：用扫频测得的频响曲线反推增益偏移表。

        假设参考功率是 ``ref_level_db``（理想平坦响应）。对每个测量频率点，
        offset_db = ref_level_db - measured_db（即测低了多少就补多少）。
        """
        freqs_hz = np.asarray(freqs_hz, dtype=np.float64).ravel()
        measured_db = np.asarray(measured_db, dtype=np.float64).ravel()
        if freqs_hz.size != measured_db.size:
            raise ValueError("freqs and measured_db length mismatch")
        tbl = cls(device=device, note="auto-generated from sweep")
        for f, m in zip(freqs_hz.tolist(), measured_db.tolist()):
            tbl.add_point(f, ref_level_db - m)
        return tbl
