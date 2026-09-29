# SPDX-License-Identifier: MIT
"""
MBDSDR 扫频器（scanner）
=========================

扫频器设计（SDR++ scanner 仅作技术参考，本仓未包含其源代码）：
  - startFreq/stopFreq/interval 步进扫频
  - 定频驻留 worker 循环 + tuningTime/lingerTime
  - getMaxLevel: freq->bin 映射取窗内最大值
  - findSignal: 按方向步进找超门限频点

MBDSDR 增强：
  * 采用**噪声底中位数 + threshold_db** 自适应门限（而非固定 dB 门限），
    适应噪声底变化。
  * 把连续超阈值 bin **合并为活动段**，输出
    {start_freq, end_freq, peak_freq, peak_db, bandwidth}。
  * 优先级队列：检测到活动段后可回调跳到该频点解调（按段排队）。
  * classify_segment() AI 钩子：按带宽/形状猜信号类型（FM/AM/数字）。

红线：
  * 不伪造硬件 IQ。扫频器接受"采样提供者"回调或直接吃 PSD 数组；
    无提供者时不自己造假数据。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass
class ActiveSegment:
    start_freq: float          # 段起点 Hz
    end_freq: float            # 段终点 Hz
    peak_freq: float           # 段内峰值频率 Hz
    peak_db: float             # 段内峰值功率 dB
    bandwidth: float           # end-start
    # 增强：分类结果（由 classify_segment 填）
    kind: str = "unknown"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "start_freq": self.start_freq,
            "end_freq": self.end_freq,
            "peak_freq": self.peak_freq,
            "peak_db": self.peak_db,
            "bandwidth": self.bandwidth,
            "kind": self.kind,
            "confidence": self.confidence,
        }


# ----------------------------------------------------------------------
# 活动段提取（核心算法，纯函数，确定性）
# ----------------------------------------------------------------------
def extract_active_segments(freqs_hz: np.ndarray,
                            psd_db: np.ndarray,
                            threshold_db: float = 6.0,
                            min_sep_hz: float = 0.0) -> List[ActiveSegment]:
    """从一条 PSD 曲线提取活动段。

    步骤（对齐任务描述）：
      1. 噪声底 = psd_db 的中位数（自适应，替代固定 dB 门限）。
      2. 门限 = noise_floor + threshold_db。
      3. 找所有 psd_db >= 门限的连续 bin 区间。
      4. 每个区间内取峰值 freq/db；算 bandwidth。
      5. 可选：间隔小于 min_sep_hz 的相邻段合并。

    参数:
      freqs_hz: 长度 N 的频点数组（Hz），升序。
      psd_db:   长度 N 的功率谱 dB 值。
      threshold_db: 相对噪声底的 dB 余量（默认 6 dB）。
      min_sep_hz: 两段间隔小于该值则合并（0=不合并）。
    """
    freqs = np.asarray(freqs_hz, dtype=float)
    psd = np.asarray(psd_db, dtype=float)
    if freqs.size != psd.size or freqs.size == 0:
        return []

    noise_floor = float(np.median(psd))
    thresh = noise_floor + threshold_db
    above = psd >= thresh

    segments: List[ActiveSegment] = []
    n = len(freqs)
    i = 0
    while i < n:
        if not above[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and above[j + 1]:
            j += 1
        # 段 [i, j]
        seg_freqs = freqs[i:j + 1]
        seg_psd = psd[i:j + 1]
        peak_idx = int(np.argmax(seg_psd))
        segments.append(ActiveSegment(
            start_freq=float(seg_freqs[0]),
            end_freq=float(seg_freqs[-1]),
            peak_freq=float(seg_freqs[peak_idx]),
            peak_db=float(seg_psd[peak_idx]),
            bandwidth=float(seg_freqs[-1] - seg_freqs[0]),
        ))
        i = j + 1

    # 合并相近段
    if min_sep_hz > 0 and len(segments) > 1:
        merged: List[ActiveSegment] = [segments[0]]
        for seg in segments[1:]:
            prev = merged[-1]
            if seg.start_freq - prev.end_freq <= min_sep_hz:
                # 合并：取更宽区间，峰值取较大者
                end = seg.end_freq
                if seg.peak_db > prev.peak_db:
                    peak_freq, peak_db = seg.peak_freq, seg.peak_db
                else:
                    peak_freq, peak_db = prev.peak_freq, prev.peak_db
                merged[-1] = ActiveSegment(
                    start_freq=prev.start_freq, end_freq=end,
                    peak_freq=peak_freq, peak_db=peak_db,
                    bandwidth=end - prev.start_freq,
                )
            else:
                merged.append(seg)
        segments = merged

    # 分类（增强）
    for seg in segments:
        seg.kind, seg.confidence = classify_segment(seg)
    return segments


# ----------------------------------------------------------------------
# AI 钩子：按段特征猜信号类型（纯规则，可被 LLM 替换）
# ----------------------------------------------------------------------
def classify_segment(seg: ActiveSegment) -> Tuple[str, float]:
    """按带宽粗分：FM 广播 ~200k，AM 航空 ~25k，NFM ~12.5k，CW < 200，其余数字。"""
    bw = seg.bandwidth
    if bw >= 150e3:
        return "WFM", 0.7
    if 8e3 <= bw < 30e3:
        return "NFM", 0.6
    if 10e3 <= bw < 50e3 and seg.peak_db > 0:
        return "AM", 0.5
    if bw < 200:
        return "CW", 0.6
    if bw >= 30e3:
        return "DIG", 0.4
    return "UNKNOWN", 0.2


# ----------------------------------------------------------------------
# 步进扫频器（对齐 scanner/main.cpp 的 worker 循环，但做成可注入、可测试）
# ----------------------------------------------------------------------
class SweepScanner:
    """步进调谐扫频段。

    用法（无硬件时用合成数据测试）：
        scanner = SweepScanner(start_hz=88e6, stop_hz=108e6, step_hz=100e3)
        # 注入一个"调谐后返回该频点附近 PSD"的回调
        scanner.psd_provider = lambda center_hz, bw: (freqs, psd_db)
        segments = scanner.sweep()

    参数：
      start_hz   <- 扫频起始频率
      stop_hz    <- 扫频终止频率
      step_hz    <- 步进间隔
      dwell_samples 每步停留样点数（由 linger 时间 × 采样率折算）
      threshold_db    相对噪声底的 dB 余量（替代固定 level, :282）
    """

    def __init__(self,
                 start_hz: float,
                 stop_hz: float,
                 step_hz: float = 100e3,
                 dwell_samples: int = 4096,
                 threshold_db: float = 6.0,
                 min_sep_hz: float = 0.0):
        if stop_hz <= start_hz:
            raise ValueError("stop_hz 必须 > start_hz")
        if step_hz <= 0:
            raise ValueError("step_hz 必须 > 0")
        self.start_hz = float(start_hz)
        self.stop_hz = float(stop_hz)
        self.step_hz = float(step_hz)
        self.dwell_samples = int(dwell_samples)
        self.threshold_db = float(threshold_db)
        self.min_sep_hz = float(min_sep_hz)
        # 注入：给定中心频点和带宽，返回 (freqs_hz 数组, psd_db 数组)
        self.psd_provider: Optional[Callable[[float, float],
                                             Tuple[np.ndarray, np.ndarray]]] = None
        # 命中回调：找到活动段后调用（类似 receiving=true, current=freq, :250-251）
        self.on_activity: Optional[Callable[[ActiveSegment], None]] = None
        self._queue: List[Tuple[float, int, ActiveSegment]] = []  # 优先级队列

    # ------------------------------------------------------------------
    def sweep(self) -> List[ActiveSegment]:
        """走完整个频段，返回合并后的活动段列表。

        若设置了 psd_provider，每步进一次调一次，把多段 PSD 拼起来再提取。
        """
        if self.psd_provider is None:
            raise RuntimeError("未设置 psd_provider（无硬件时不要造假数据）")

        all_freqs: List[np.ndarray] = []
        all_psd: List[np.ndarray] = []
        center = self.start_hz
        while center <= self.stop_hz + 1e-6:
            freqs, psd = self.psd_provider(center, self.step_hz)
            all_freqs.append(np.asarray(freqs, dtype=float))
            all_psd.append(np.asarray(psd, dtype=float))
            center += self.step_hz

        if not all_freqs:
            return []
        freqs = np.concatenate(all_freqs)
        psd = np.concatenate(all_psd)
        order = np.argsort(freqs)
        freqs = freqs[order]
        psd = psd[order]

        segments = extract_active_segments(
            freqs, psd,
            threshold_db=self.threshold_db,
            min_sep_hz=self.min_sep_hz,
        )

        # 优先级队列：按 peak_db 从高到低排队
        self._queue = []
        for seg in segments:
            heapq.heappush(self._queue, (-seg.peak_db, id(seg), seg))
        # 触发命中回调
        if self.on_activity:
            for seg in segments:
                self.on_activity(seg)
        return segments

    # ------------------------------------------------------------------
    def next_hottest(self) -> Optional[ActiveSegment]:
        """弹出当前信号最强的活动段（优先级队列，对齐 findSignal 命中后跳频）。"""
        if not self._queue:
            return None
        return heapq.heappop(self._queue)[2]
