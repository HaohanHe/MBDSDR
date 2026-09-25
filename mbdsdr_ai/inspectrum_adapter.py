"""
MBDSDR AI - inspectrum 真实源码移植适配器
==========================================
inspectrum_adapter.py

把 inspectrum (https://github.com/miek/inspectrum) 的频谱游标测量引擎从 C++/Qt
移植为纯 numpy。inspectrum 的核心是「一对游标」：
  * 垂直游标（时间轴）：两个样本位置 → 持续时间 / 周期 / 占空比
  * 水平游标（频率轴）：两个 bin 位置 → 频率差 / 带宽

坐标换算（来源）：
  * src/plotview.cpp:526-527  startTime = sampleRange.min / sampleRate
                               stopTime  = sampleRange.max / sampleRate
  * src/spectrogramplot.cpp:94 bwPerPixel = sampleRate / plotHeight
                               （即每 bin 频率分辨率 = sampleRate / fft_size）
  * src/util.h  range_t.length() = maximum - minimum  （游标区间长度）

覆盖：
  * 带宽测量（含 -3dB 半功率带宽）
  * 频率测量（游标对 → ΔHz）
  * 周期测量（脉冲到达间隔均值）
  * 占空比测量（高电平时间 / 周期）
纯 numpy，不引入 Qt。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 游标测量引擎
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class CursorMeasurement:
    """一次游标测量结果。"""
    value: float
    unit: str
    cursor_left: float = 0.0
    cursor_right: float = 0.0
    extra: Dict[str, Any] = None


class InspectrumMeasurer:
    """inspectrum 风格频谱/时间游标测量引擎。

    sample_rate : 采样率 Hz（plotview.cpp:526 time = sample / sampleRate）
    fft_size    : FFT 点数（spectrogramplot.cpp:94 bin 分辨率 = sampleRate/fft_size）
    """

    def __init__(self, sample_rate: float, fft_size: int = 1024):
        self.sample_rate = float(sample_rate)
        self.fft_size = int(fft_size)

    # ── 频率轴：bin ↔ Hz ──────────────────────────────────────────────
    def bin_to_hz(self, bin_idx: float) -> float:
        """bin 索引 → 频率偏移 Hz。spectrogramplot.cpp:94 bwPerPixel。"""
        return (bin_idx - self.fft_size / 2.0) * (self.sample_rate / self.fft_size)

    def hz_to_bin(self, freq_offset: float) -> float:
        return freq_offset / (self.sample_rate / self.fft_size) + self.fft_size / 2.0

    # ── 时间轴：样本 ↔ 秒 ────────────────────────────────────────────
    def samples_to_seconds(self, n_samples: float) -> float:
        """plotview.cpp:526  time = sampleRange / sampleRate。"""
        return n_samples / self.sample_rate

    # ── 游标对 → 区间长度（util.h range_t.length()）──────────────────
    def measure_cursor_range(self, left: float, right: float, axis: str) -> CursorMeasurement:
        """一对游标 → 区间长度。

        axis="freq" : left/right 为 bin，返回 Hz 差
        axis="time" : left/right 为样本，返回秒差
        """
        length = abs(right - left)  # util.h range_t.length() = max - min
        if axis == "freq":
            return CursorMeasurement(value=length * self.sample_rate / self.fft_size,
                                     unit="Hz", cursor_left=left, cursor_right=right)
        elif axis == "time":
            return CursorMeasurement(value=length / self.sample_rate,
                                     unit="s", cursor_left=left, cursor_right=right)
        else:
            raise ValueError(f"axis 必须是 freq 或 time，得到 {axis}")

    # ── -3dB 半功率带宽 ──────────────────────────────────────────────
    def measure_bandwidth_half_power(self, freqs_hz: np.ndarray,
                                     powers_db: np.ndarray,
                                     peak_bin: int,
                                     level_db: float = -3.0) -> Dict[str, float]:
        """测量半功率带宽（峰值下降 level_db 的左右频率点间距）。

        在峰值两侧向两侧扫描，直到功率比峰值低 level_db dB。
        """
        powers_db = np.asarray(powers_db, dtype=float)
        peak = float(powers_db[peak_bin])
        threshold = peak + level_db  # level_db=-3 → 比峰低 3dB
        n = len(powers_db)

        # 向左
        left_bin = peak_bin
        while left_bin > 0 and powers_db[left_bin] > threshold:
            left_bin -= 1
        # 向右
        right_bin = peak_bin
        while right_bin < n - 1 and powers_db[right_bin] > threshold:
            right_bin += 1

        bw_hz = abs(float(freqs_hz[right_bin] - freqs_hz[left_bin]))
        return {
            "bandwidth_hz": bw_hz,
            "left_freq_hz": float(freqs_hz[left_bin]),
            "right_freq_hz": float(freqs_hz[right_bin]),
            "peak_freq_hz": float(freqs_hz[peak_bin]),
            "peak_power_db": peak,
            "level_db": level_db,
        }

    # ── 周期测量：脉冲到达时间 → 周期 ───────────────────────────────
    def measure_period(self, pulse_starts_samples: Sequence[float]) -> Dict[str, float]:
        """从一系列脉冲起始样本位置估计周期（平均到达间隔）。

        inspectrum 垂直游标测相邻两脉冲间距即周期；这里取均值更稳健。
        """
        starts = np.asarray(pulse_starts_samples, dtype=float)
        if len(starts) < 2:
            return {"period_s": 0.0, "num_pulses": len(starts),
                    "note": "脉冲数<2，无法测周期"}
        intervals = np.diff(np.sort(starts))
        period_samples = float(np.mean(intervals))
        return {
            "period_s": period_samples / self.sample_rate,
            "period_samples": period_samples,
            "period_hz": self.sample_rate / period_samples if period_samples > 0 else 0.0,
            "num_pulses": len(starts),
            "jitter_samples": float(np.std(intervals)),
        }

    # ── 占空比测量 ───────────────────────────────────────────────────
    def measure_duty_cycle(self, on_samples: float, period_samples: float) -> Dict[str, float]:
        """占空比 = 高电平时间 / 周期。"""
        if period_samples <= 0:
            return {"duty_cycle": 0.0, "duty_percent": 0.0}
        duty = on_samples / period_samples
        return {"duty_cycle": float(duty), "duty_percent": float(duty * 100),
                "on_time_s": on_samples / self.sample_rate,
                "period_s": period_samples / self.sample_rate}


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_inspectrum_tools(registry) -> None:
    """把 inspectrum 游标测量工具注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _measure_bandwidth(args):
        """对一段频谱功率数组测 -3dB 带宽。"""
        freqs = args.get("frequencies")
        powers = args.get("powers_db")
        sr = float(args.get("sample_rate", 1e6))
        fft_size = int(args.get("fft_size", 1024))
        if not isinstance(freqs, list) or not isinstance(powers, list):
            return ToolResult(False, "frequencies 和 powers_db 都必须是列表")
        try:
            f = np.asarray(freqs, dtype=float)
            p = np.asarray(powers, dtype=float)
            peak_bin = int(np.argmax(p))
            m = InspectrumMeasurer(sr, fft_size)
            out = m.measure_bandwidth_half_power(f, p, peak_bin,
                                                  level_db=float(args.get("level_db", -3.0)))
            out["source"] = "inspectrum spectrogramplot.cpp:94 / plotview.cpp:526"
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"带宽测量失败: {e}")

    def _measure_period(args):
        """从脉冲起始样本位置测周期。"""
        starts = args.get("pulse_starts_samples")
        sr = float(args.get("sample_rate", 1e6))
        if not isinstance(starts, list):
            return ToolResult(False, "pulse_starts_samples 必须是样本位置列表")
        try:
            m = InspectrumMeasurer(sr)
            out = m.measure_period(starts)
            out["source"] = "inspectrum plotview.cpp:526 垂直游标测周期"
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"周期测量失败: {e}")

    def _measure_duty(args):
        on = float(args.get("on_samples", 0))
        period = float(args.get("period_samples", 0))
        sr = float(args.get("sample_rate", 1e6))
        try:
            m = InspectrumMeasurer(sr)
            out = m.measure_duty_cycle(on, period)
            out["source"] = "inspectrum util.h range_t.length()"
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"占空比测量失败: {e}")

    registry.register(
        name="inspectrum_measure_bandwidth",
        description=("inspectrum 半功率带宽测量：给频率轴+功率(dB)数组，"
                     "在峰值两侧找下降 level_db(默认-3dB) 的点，返回带宽 Hz。"),
        parameters={
            "type": "object",
            "properties": {
                "frequencies": {"type": "array", "items": {"type": "number"}},
                "powers_db": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 1e6},
                "fft_size": {"type": "integer", "default": 1024},
                "level_db": {"type": "number", "default": -3.0},
            },
            "required": ["frequencies", "powers_db"],
        },
        handler=_measure_bandwidth,
        category="sdr_spectrum",
    )
    registry.register(
        name="inspectrum_measure_period",
        description=("inspectrum 垂直游标测周期：给一串脉冲起始样本位置，"
                     "返回平均周期(秒)与抖动。"),
        parameters={
            "type": "object",
            "properties": {
                "pulse_starts_samples": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": 1e6},
            },
            "required": ["pulse_starts_samples"],
        },
        handler=_measure_period,
        category="sdr_spectrum",
    )
    registry.register(
        name="inspectrum_measure_duty_cycle",
        description=("inspectrum 占空比测量：高电平时长 / 周期。"),
        parameters={
            "type": "object",
            "properties": {
                "on_samples": {"type": "number"},
                "period_samples": {"type": "number"},
                "sample_rate": {"type": "number", "default": 1e6},
            },
            "required": ["on_samples", "period_samples"],
        },
        handler=_measure_duty,
        category="sdr_spectrum",
    )
