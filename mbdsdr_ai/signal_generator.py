# SPDX-License-Identifier: MIT
"""
测试信号发生器（Signal Generator / Noise Source）
====================================================

仅用于**单测与校准**的合成基带信号源。红线：本模块输出**不接 UI、不当真实
接收读数**——它是测试激励，不是设备采集。

对照上游：
- SDR++ 社区 noise_source 模块（misc_modules/demo_module）：在基带注入可配置
  噪声/单音，用于测试解调链路是否工作。本文件把它做成纯数据层。
- SDR++ core/src/dsp/math/：复数正弦/噪声生成的向量约定（complex_t = I+IQ）。
- GQRX src/dsp/sniffer_f.cpp：参考信号注入点（在 VFO 输出后接测试信号）。

支持的信号类型：
- ``"noise"``   复高斯白噪声（I/Q 独立，给定功率 dBFS）
- ``"tone"``    复单音（在 baseband 中心附近的指定偏移频率）
- ``"sweep"``   线性调频扫频（chirp），记录起/止频
- ``"square"``  方波（实数 ±1，可载波调制）

AI 增强：
- :meth:`SignalGenerator.auto_pick` 根据"待测模块名"自动选择信号类型与参数
  （例如测解调 → 单音+噪声；测扫频链路 → chirp；测静噪 → 噪声底+单音）。

确定性：所有随机源都用可注入的 ``numpy.random.Generator``，传固定 seed 即可
复现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# 工具：dBFS ↔ 线性幅度
# ---------------------------------------------------------------------------
def dbfs_to_amplitude(dbfs: float) -> float:
    """给定 RMS dBFS，返回复信号的归一化 RMS 幅度（满量程 1.0 = 0 dBFS）。

    对复噪声：I、Q 各自功率 = 总功率/2，std(I)=std(Q)=amp/sqrt(2)。
    """
    return float(10.0 ** (dbfs / 20.0))


# ---------------------------------------------------------------------------
# 参数容器
# ---------------------------------------------------------------------------
@dataclass
class SignalSpec:
    """一段测试信号的规格。"""

    kind: str                       # "noise" | "tone" | "sweep" | "square"
    duration_s: float = 1.0         # 时长
    sample_rate: float = 48000.0    # 采样率
    power_dbfs: float = -20.0       # RMS 功率
    # tone / sweep 专用
    freq_hz: float = 0.0            # tone: 偏移频率；sweep: 起始频率
    end_freq_hz: float = 0.0        # sweep 终止频率
    # square 专用
    duty: float = 0.5               # 占空比
    # 元信息
    label: str = ""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------
class SignalGenerator:
    """确定性测试信号发生器。

    Parameters
    ----------
    seed : int, optional
        随机种子。传固定值保证可复现；None 则每次构造独立。
    """

    def __init__(self, seed: Optional[int] = 42) -> None:
        self._rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # 基本信号
    # ------------------------------------------------------------------
    def noise(self, duration_s: float, sample_rate: float,
              power_dbfs: float = -20.0) -> np.ndarray:
        """复高斯白噪声。

        复噪声 I/Q 独立同分布 N(0, sigma^2)，总功率 E[|z|^2] = 2 sigma^2。
        令总功率 = amp^2（amp = dbfs_to_amplitude(power_dbfs)），则
        sigma = amp / sqrt(2)。
        """
        n = int(round(duration_s * sample_rate))
        amp = dbfs_to_amplitude(power_dbfs)
        sigma = amp / math.sqrt(2.0)
        i = self._rng.normal(0.0, sigma, n)
        q = self._rng.normal(0.0, sigma, n)
        return (i + 1j * q).astype(np.complex64)

    def tone(self, duration_s: float, sample_rate: float,
             freq_hz: float = 1000.0,
             power_dbfs: float = -20.0) -> np.ndarray:
        """复单音 exp(2j pi f t)。"""
        n = int(round(duration_s * sample_rate))
        t = np.arange(n, dtype=np.float64) / sample_rate
        amp = dbfs_to_amplitude(power_dbfs)
        # 复正弦 RMS = amp（|exp(jw)|=1）
        return (amp * np.exp(2j * math.pi * freq_hz * t)).astype(np.complex64)

    def sweep(self, duration_s: float, sample_rate: float,
              start_freq_hz: float = -5000.0,
              end_freq_hz: float = 5000.0,
              power_dbfs: float = -20.0) -> np.ndarray:
        """线性扫频（chirp），瞬时频率从 start 线性扫到 end。

        相位积分：phi(t) = 2pi * (f0*t + (f1-f0)/(2T) * t^2)。
        """
        n = int(round(duration_s * sample_rate))
        t = np.arange(n, dtype=np.float64) / sample_rate
        T = duration_s
        f0, f1 = start_freq_hz, end_freq_hz
        phase = 2.0 * math.pi * (f0 * t + (f1 - f0) / (2.0 * T) * t * t)
        amp = dbfs_to_amplitude(power_dbfs)
        return (amp * np.exp(1j * phase)).astype(np.complex64)

    def square(self, duration_s: float, sample_rate: float,
               freq_hz: float = 1000.0,
               duty: float = 0.5,
               power_dbfs: float = -20.0) -> np.ndarray:
        """实数方波（±1），再乘幅度。可当 AM 调制源用。"""
        n = int(round(duration_s * sample_rate))
        t = np.arange(n, dtype=np.float64) / sample_rate
        # 相位 [0,1)，< duty 取 +1，否则 -1
        phase = (freq_hz * t) % 1.0
        sq = np.where(phase < duty, 1.0, -1.0).astype(np.float64)
        amp = dbfs_to_amplitude(power_dbfs)
        return (amp * sq).astype(np.complex64)

    # ------------------------------------------------------------------
    # 按规格生成
    # ------------------------------------------------------------------
    def generate(self, spec: SignalSpec) -> np.ndarray:
        """按 SignalSpec 分发到具体生成器。"""
        kind = (spec.kind or "").lower()
        if kind == "noise":
            return self.noise(spec.duration_s, spec.sample_rate, spec.power_dbfs)
        if kind == "tone":
            return self.tone(spec.duration_s, spec.sample_rate,
                             spec.freq_hz, spec.power_dbfs)
        if kind == "sweep":
            return self.sweep(spec.duration_s, spec.sample_rate,
                              spec.freq_hz, spec.end_freq_hz, spec.power_dbfs)
        if kind == "square":
            return self.square(spec.duration_s, spec.sample_rate,
                               spec.freq_hz, spec.duty, spec.power_dbfs)
        raise ValueError(f"unknown signal kind: {spec.kind!r}")

    # ------------------------------------------------------------------
    # AI 增强：按待测模块自动选信号
    # ------------------------------------------------------------------
    @staticmethod
    def auto_pick(target_module: str,
                  sample_rate: float = 48000.0) -> SignalSpec:
        """根据"待测模块名"自动推荐测试信号规格。

        映射表（关键词小写匹配）：
        - 含 ``"squelch"`` / ``"sql"``     → 噪声底 + 单音（见 generate_with_noise_floor）
        - 含 ``"demod"`` / ``"am"`` / ``"fm"`` / ``"ssb"`` / ``"cw"`` → 单音
        - 含 ``"sweep"`` / ``"channelizer"`` / ``"fft"`` / ``"spectrum"`` → 扫频
        - 含 ``"agc"`` / ``"gain"``        → 噪声（平缓功率变化）
        - 其他                             → 默认单音
        """
        m = (target_module or "").lower()
        if any(k in m for k in ("squelch", "sql", "s_meter", "smeter")):
            return SignalSpec(kind="noise", duration_s=1.0,
                              sample_rate=sample_rate, power_dbfs=-60.0,
                              label="auto:squelch-noise-floor")
        if any(k in m for k in ("sweep", "channelizer", "fft", "spectrum",
                                "accumulator")):
            return SignalSpec(kind="sweep", duration_s=1.0,
                              sample_rate=sample_rate,
                              freq_hz=-sample_rate / 4.0,
                              end_freq_hz=sample_rate / 4.0,
                              power_dbfs=-30.0,
                              label="auto:spectrum-sweep")
        if any(k in m for k in ("agc", "gain", "calib")):
            return SignalSpec(kind="noise", duration_s=1.0,
                              sample_rate=sample_rate, power_dbfs=-40.0,
                              label="auto:agc-noise")
        # 默认：单音（解调/CW/AM/FM 通用）
        return SignalSpec(kind="tone", duration_s=1.0,
                          sample_rate=sample_rate, freq_hz=1000.0,
                          power_dbfs=-20.0, label="auto:tone")

    # ------------------------------------------------------------------
    # 复合：噪声底上叠加单音（测静噪/S-meter 信噪比用）
    # ------------------------------------------------------------------
    def tone_on_noise(self, duration_s: float, sample_rate: float,
                      tone_freq_hz: float = 1000.0,
                      tone_power_dbfs: float = -30.0,
                      noise_power_dbfs: float = -60.0) -> np.ndarray:
        """在噪声底上叠加单音，返回 z = tone + noise。"""
        t = self.tone(duration_s, sample_rate, tone_freq_hz, tone_power_dbfs)
        n = self.noise(duration_s, sample_rate, noise_power_dbfs)
        return (t + n).astype(np.complex64)
