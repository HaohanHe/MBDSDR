"""
调试信号源 DebugSource：合成基带测试信号，接口兼容 SDRBackend
================================================================

对照上游：
- SDR++ noise_source / signal_generator 模块：在基带注入可配置噪声/单音/扫频，
  用于在无硬件时测试解调/频谱/静噪链路。
- GQRX src/dsp/sniffer_f.cpp：测试信号注入点（VFO 输出后接合成信号）。

与 :mod:`mbdsdr_ai.signal_generator` 的分工：
- signal_generator.py 是**一次性批量**生成器（一次吐整段数组，供单测/校准）。
- 本文件的 :class:`DebugSource` 是**流式**、状态保持的信号源，read_samples(n)
  可反复调用并连续无缝衔接，且包装成与 SDRBackend 兼容的接口
  （read_samples/set_frequency/set_sample_rate/get_status），可直接替换真实
  后端做 UI / DSP 链路调试。

红线（绝不越界）：
- get_status() 的 device 字段恒含 "Debug"，device_label 恒为"调试信号源"，
  绝不冒充真实硬件设备。
- 默认不接 UI（仅开发调试手动启用）；输出是合成激励，不是接收读数。
- 所有随机源用可注入 numpy.random.Generator（固定 seed 可复现）。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

VALID_SIGNAL_TYPES = ("tone", "noise", "sweep", "am", "fm")


class DebugSource:
    """流式合成调试信号源（SDRBackend 兼容接口子集）。

    Parameters
    ----------
    sample_rate : float
        基带采样率（Hz），默认 48000。
    seed : int
        噪声随机种子，固定即复现。
    """

    def __init__(self, sample_rate: float = 48_000.0, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

        self._connected = False
        self._sample_rate = float(sample_rate)
        # 上报的"中心频率"——纯元数据（调试源不真变频），但按 SDRBackend 接口暴露
        self._freq_hz: float = 100_000_000.0
        self._gain_db: float = 0.0
        self._agc: bool = True

        # 信号参数
        self._signal_type: str = "tone"
        self._amplitude: float = 0.5           # 线性幅度 (0..1)
        self._tone_freq_hz: float = 1000.0     # tone/am/fm 载波偏移
        self._sweep_start_hz: float = -5000.0
        self._sweep_end_hz: float = 5000.0
        self._sweep_rate_hz_s: float = 10000.0  # Hz/s
        self._mod_freq_hz: float = 1000.0       # AM/FM 调制音
        self._fm_deviation_hz: float = 5000.0   # FM 频偏

        # 流式状态：已输出样本数（连续相位积分用，保证跨块无缝）
        self._n_elapsed: int = 0
        self._samples_read: int = 0

    # ------------------------------------------------------------------
    # 生命周期（SDRBackend 兼容）
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        self._connected = True
        logger.info("DebugSource 已启用（调试信号源，非真实设备）: type=%s fs=%.0f",
                    self._signal_type, self._sample_rate)
        return True

    def disconnect(self) -> None:
        self._connected = False

    # ------------------------------------------------------------------
    # 参数配置
    # ------------------------------------------------------------------
    def set_signal_type(self, stype: str) -> bool:
        st = (stype or "").lower()
        if st not in VALID_SIGNAL_TYPES:
            logger.warning("DebugSource 未知信号类型 %r（支持 %s）",
                           stype, VALID_SIGNAL_TYPES)
            return False
        self._signal_type = st
        return True

    def get_signal_type(self) -> str:
        return self._signal_type

    def set_frequency(self, freq_hz: float) -> bool:
        """上报的中心频率元数据（调试源不真调硬件）。同时作为 tone 偏移。"""
        if not self._connected:
            return False
        self._freq_hz = float(freq_hz)
        # 也把单音偏移设为该值（若用户把它当单音频率用）
        self._tone_freq_hz = float(freq_hz)
        return True

    def get_frequency(self) -> float:
        return self._freq_hz

    def set_tone_frequency(self, offset_hz: float) -> None:
        self._tone_freq_hz = float(offset_hz)

    def set_amplitude(self, x: float) -> None:
        """线性幅度 (0..1)，自动 clip。"""
        self._amplitude = float(max(0.0, min(1.0, x)))

    def get_amplitude(self) -> float:
        return self._amplitude

    def set_sweep(self, start_hz: float, end_hz: float,
                  rate_hz_s: float) -> None:
        self._sweep_start_hz = float(start_hz)
        self._sweep_end_hz = float(end_hz)
        self._sweep_rate_hz_s = float(rate_hz_s)

    def set_modulation(self, mod_freq_hz: float,
                       fm_deviation_hz: float = 5000.0) -> None:
        self._mod_freq_hz = float(mod_freq_hz)
        self._fm_deviation_hz = float(fm_deviation_hz)

    def set_sample_rate(self, rate_hz: float) -> bool:
        if rate_hz <= 0:
            return False
        self._sample_rate = float(rate_hz)
        return True

    def get_sample_rate(self) -> float:
        return self._sample_rate

    def set_gain(self, gain_db: float) -> bool:
        self._gain_db = float(gain_db)
        return True

    def get_gain(self) -> float:
        return self._gain_db

    def set_agc(self, enabled: bool) -> bool:
        self._agc = bool(enabled)
        return True

    # ------------------------------------------------------------------
    # 核心：流式 read_samples
    # ------------------------------------------------------------------
    def read_samples(self, n: int) -> Optional[np.ndarray]:
        if not self._connected or n <= 0:
            return None
        fs = self._sample_rate
        k = np.arange(n, dtype=np.float64)
        # 本块样本对应的绝对时间（秒），保证跨块相位连续
        t = (self._n_elapsed + k) / fs
        amp = self._amplitude
        st = self._signal_type

        if st == "tone":
            out = amp * np.exp(2j * math.pi * self._tone_freq_hz * t)
        elif st == "noise":
            sigma = amp / math.sqrt(2.0)
            i = self._rng.normal(0.0, sigma, n)
            q = self._rng.normal(0.0, sigma, n)
            out = i + 1j * q
        elif st == "sweep":
            # 瞬时频率 f(t)=f0+rate*t（线性扫频，loop 由消费方决定是否重启）
            f0 = self._sweep_start_hz
            rate = self._sweep_rate_hz_s
            phase = 2.0 * math.pi * (f0 * t + 0.5 * rate * t * t)
            out = amp * np.exp(1j * phase)
        elif st == "am":
            # AM: 载波 * (1 + m*cos(2π fm t))，m=0.5
            carrier = np.exp(2j * math.pi * self._tone_freq_hz * t)
            mod = 1.0 + 0.5 * np.cos(2.0 * math.pi * self._mod_freq_hz * t)
            out = amp * mod * carrier
        elif st == "fm":
            # FM: 相位 = 2π fc t + (deviation/fm) sin(2π fm t)
            fc = self._tone_freq_hz
            fm = self._mod_freq_hz
            dev = self._fm_deviation_hz
            beta = dev / fm if fm > 0 else 0.0
            phase = 2.0 * math.pi * fc * t + beta * np.sin(2.0 * math.pi * fm * t)
            out = amp * np.exp(1j * phase)
        else:  # pragma: no cover - set_signal_type 已拦截
            out = np.zeros(n, dtype=np.complex128)

        self._n_elapsed += int(n)
        self._samples_read += int(n)
        return out.astype(np.complex64, copy=False)

    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        # 红线：device 恒含 "Debug"，device_label 显式"调试信号源"
        return {
            "connected": self._connected,
            "device": "DebugSignalGenerator",
            "device_label": "调试信号源（合成信号，非真实设备）",
            "is_debug_source": True,
            "frequency_hz": self._freq_hz,
            "sample_rate_hz": self._sample_rate,
            "gain_db": self._gain_db,
            "agc_enabled": self._agc,
            "signal_type": self._signal_type,
            "amplitude": self._amplitude,
            "samples_read": self._samples_read,
            "error": "",
        }

    # ------------------------------------------------------------------
    # AI 增强：根据待测模块自动选信号类型（包装 SignalGenerator.auto_pick）
    # ------------------------------------------------------------------
    def auto_pick_for(self, target_module: str) -> str:
        """根据待测模块名自动选信号类型并设置，返回选中的类型字符串。

        复用 signal_generator.SignalGenerator.auto_pick 的映射思想：
        解调/AM/FM/CW → tone；squelch/静噪 → noise；spectrum/sweep/fft → sweep；
        agc/gain → noise；调制链路 → fm/am。
        """
        from .signal_generator import SignalGenerator
        spec = SignalGenerator.auto_pick(target_module, self._sample_rate)
        st = spec.kind
        if st == "square":
            st = "tone"
        self.set_signal_type(st)
        if st == "tone":
            self.set_tone_frequency(spec.freq_hz or 1000.0)
        elif st == "sweep":
            self.set_sweep(spec.freq_hz, spec.end_freq_hz, 10000.0)
        logger.info("DebugSource auto-pick 模块 %r → 信号类型 %r",
                    target_module, st)
        return st
