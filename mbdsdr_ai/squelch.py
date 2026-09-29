# SPDX-License-Identifier: MIT
"""
静噪门控（Squelch）—— 移动平均 RMS + attack/decay/hang
=====================================================

门控位置：在 AGC **之前**。对照 gqrx/src/receivers/nbrx.cpp:77-78：

    connect(filter, 0, meter, 0);   // 测 RSSI
    connect(filter, 0, sql, 0);     // 静噪门控（IF 域）
    connect(sql,   0, agc, 0);      // AGC 只处理过门的信号

GQRX 用 gr::analog::simple_squelch_cc(threshold, alpha)：
    - threshold：信号低于此 dB 就静音
    - alpha：门控平滑系数（attack/decay）

本实现：
- RMS 用移动平均（时间常数 attack/decay），不是瞬时单块——避免毛刺误触发。
- hang time：信号掉到门限以下后，门仍保持打开 hang_ms 毫秒才关闭，
  防止语音尾音/信号衰落时门来回咔哒开/关（对讲机静噪的标准做法）。
- process(block) 返回门控后的块；门关闭时输出零（可被 audio_out 的 5ms
  ramp 进一步抹平咔哒）。

用法::

    sql = Squelch(threshold_db=-50.0, hang_ms=200.0)
    for if_block in iq_stream:
        rms_db = compute_rms_db(if_block)
        gated = sql.apply(if_block, rms_db)   # 门关时全零
        # gated 送 AGC → 解调
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def rms_dbfs(x: np.ndarray) -> float:
    """计算一块信号的 RMS 功率 dBFS（满量程 1.0 为 0 dBFS）。

    对照 gqrx/src/dsp/rx_meter_c.cc 的 get_level_db()：
        level = 10*log10(mean(|x|^2))

    复数信号测真实功率 mean(I^2+Q^2)，不虚部丢弃。
    """
    x = np.asarray(x)
    if x.size == 0:
        return -150.0
    if np.iscomplexobj(x):
        xf = x.astype(np.complex128).ravel()
        p = float(np.mean(xf.real * xf.real + xf.imag * xf.imag))
    else:
        xf = x.astype(np.float64).ravel()
        p = float(np.mean(xf * xf))
    if p <= 0:
        return -150.0
    return 10.0 * np.log10(p)


@dataclass
class SquelchState:
    """静噪门内部状态（便于测试/调试）。"""
    gate_open: bool = False        # 当前门是否打开（声音可通过）
    smoothed_db: float = -150.0    # 平滑后的 RMS dBFS
    hang_remaining_ms: float = 0.0 # 剩余 hang 时间（信号刚消失时倒计时）


class Squelch:
    """RMS 静噪门控。

    Parameters
    ----------
    threshold_db : float
        打开门的 RMS dBFS 门限。信号 RMS 高于此值开门，低于此值进入 hang。
        默认 -50 dBFS（对照 gqrx nbrx.cpp:48 simple_squelch_cc 阈值）。
    hang_ms : float
        信号掉到门限以下后，门仍保持打开的毫秒数。防止语音间隙门反复开关。
        对照对讲机/接收机静噪的 hang time 惯例（100-500ms）。
    attack_ms : float
        开门时间常数（信号突现后，平滑 RMS 追上真实值的时间）。默认 5ms。
    decay_ms : float
        关门时间常数（信号消失后，平滑 RMS 下滑的时间）。默认 50ms。
    sample_rate : float
        输入块采样率（用于把 ms 时间常数换算成块数）。
    """

    def __init__(self, threshold_db: float = -50.0,
                 hang_ms: float = 200.0,
                 attack_ms: float = 5.0,
                 decay_ms: float = 50.0,
                 sample_rate: float = 48000.0):
        self.threshold_db = float(threshold_db)
        self.hang_ms = float(hang_ms)
        self.attack_ms = float(attack_ms)
        self.decay_ms = float(decay_ms)
        self.sample_rate = float(sample_rate)
        self.state = SquelchState()

    # ------------------------------------------------------------------
    # 内部：把 ms 时间常数换算成一阶 IIR 系数
    # ------------------------------------------------------------------
    @staticmethod
    def _tau_to_alpha(tau_ms: float, block_samples: int,
                      sample_rate: float) -> float:
        """一阶指数平滑系数 alpha：y[n] = alpha*x[n] + (1-alpha)*y[n-1]。

        tau_ms 是时间常数；block_samples 是每块样本数。
        alpha = 1 - exp(-block_duration / tau)。
        """
        if tau_ms <= 0:
            return 1.0
        block_s = block_samples / max(sample_rate, 1e-9)
        tau_s = tau_ms / 1000.0
        return float(1.0 - np.exp(-block_s / tau_s))

    def reset(self) -> None:
        """复位门状态（切频率/切 VFO 后调用）。"""
        self.state = SquelchState()

    # ------------------------------------------------------------------
    # 推一块 RMS 测量，更新门状态
    # ------------------------------------------------------------------
    def push_rms_db(self, rms_db: float, block_samples: int) -> bool:
        """喂入一块的 RMS dBFS，返回该块结束时门是否打开。

        门控逻辑：
        1. 用 attack/decay 系数平滑 rms_db（信号上升用快 attack，下降用慢 decay）。
        2. 若平滑 RMS > threshold → 立即开门（不等到 hang 倒计时）。
        3. 若平滑 RMS < threshold：
           - 门已开 → 进入 hang：hang_remaining 倒计时；hang 内仍开门。
           - hang 耗尽 → 关门。
        """
        s = self.state
        rms_db = float(rms_db)

        # 选 attack 或 decay 系数（信号上升快、下降慢）
        if rms_db > s.smoothed_db:
            alpha = self._tau_to_alpha(self.attack_ms, block_samples,
                                       self.sample_rate)
        else:
            alpha = self._tau_to_alpha(self.decay_ms, block_samples,
                                       self.sample_rate)
        s.smoothed_db = alpha * rms_db + (1.0 - alpha) * s.smoothed_db

        # 块时长 ms，用于 hang 倒计时
        block_ms = 1000.0 * block_samples / max(self.sample_rate, 1e-9)

        if s.smoothed_db >= self.threshold_db:
            # 信号在门限以上：开门，重置 hang 计数器
            s.gate_open = True
            s.hang_remaining_ms = self.hang_ms
        else:
            # 信号在门限以下：若门开着，hang 倒计时
            if s.gate_open:
                s.hang_remaining_ms -= block_ms
                if s.hang_remaining_ms <= 0.0:
                    s.gate_open = False
        return s.gate_open

    # ------------------------------------------------------------------
    # 便捷：直接喂音频块
    # ------------------------------------------------------------------
    def apply(self, block: np.ndarray,
              rms_db: Optional[float] = None) -> np.ndarray:
        """对一块信号应用静噪门控。

        Parameters
        ----------
        block : np.ndarray
            实数或复数信号块（IF 域或音频域均可）。
        rms_db : float, optional
            外部预算好的 RMS dBFS；不给则本函数自己算。

        Returns
        -------
        np.ndarray
            门打开时原样返回 block；门关闭时返回全零块。
        """
        block = np.asarray(block)
        n = block.shape[0] if block.ndim >= 1 else 1
        if rms_db is None:
            rms_db = rms_dbfs(block)
        open_now = self.push_rms_db(rms_db, n)
        if open_now:
            return block
        return np.zeros_like(block)

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.state.gate_open


class AutoSquelch:
    """相对噪声底的自动静噪（不依赖固定 dBFS 门限）。

    固定门限在不同增益/采样率/电台下会失准：信道噪声功率本身可能高于门限，
    于是无信号时门也常开，持续放鉴频嘶声。本类持续估计信道噪声功率底，
    只有当功率高出噪声底 ``margin_db`` 以上才判定有载波并开门。

    噪声底用非对称一阶 IIR 跟踪：
      - 功率低于当前底（纯噪声段）→ 快速下拉（``floor_down``）；
      - 功率高于当前底（可能是信号）→ 极慢上抬（``floor_up``），避免把
        持续信号误学进噪声底。
    带 hang：载波消失后保持开门 ``hang_ms``，避免语音字间隙门反复咔哒。

    用法::

        ax = AutoSquelch(margin_db=8.0, sample_rate=48000.0)
        for block in channel_stream:
            p_db = rms_dbfs(block)
            if ax.update(p_db, len(block)):
                play(block)        # 门开
            # 门关 → 静音
    """

    def __init__(self,
                 margin_db: float = 8.0,
                 hang_ms: float = 250.0,
                 sample_rate: float = 48000.0,
                 floor_down: float = 0.25,
                 floor_up: float = 0.003,
                 init_db: Optional[float] = None) -> None:
        self.margin_db = float(margin_db)
        self.hang_ms = float(hang_ms)
        self.sample_rate = float(sample_rate)
        self._floor_down = float(floor_down)
        self._floor_up = float(floor_up)
        self._floor_db: Optional[float] = (None if init_db is None
                                           else float(init_db))
        self._open: bool = False
        self._hang_remaining_ms: float = 0.0

    def reset(self) -> None:
        """复位噪声底估计与门状态（切频率/切 VFO 后调用）。"""
        self._floor_db = None
        self._open = False
        self._hang_remaining_ms = 0.0

    def update(self, power_db: float, block_samples: int) -> bool:
        """喂入一块信道功率 dBFS，返回该块门是否打开。"""
        power_db = float(power_db)
        if self._floor_db is None:
            # 首块直接以当前功率为底（开机即落在噪声上）
            self._floor_db = power_db
        elif not self._open:
            # 仅在门关闭（判定为噪声）时学习噪声底；门开着（有载波）时冻结，
            # 否则连续信号会被慢速上抬的底噪"学"进去，最终把门顶关。
            if power_db < self._floor_db:
                self._floor_db += self._floor_down * (power_db - self._floor_db)
            else:
                self._floor_db += self._floor_up * (power_db - self._floor_db)

        threshold = self._floor_db + self.margin_db
        block_ms = 1000.0 * block_samples / max(self.sample_rate, 1e-9)
        if power_db >= threshold:
            self._open = True
            self._hang_remaining_ms = self.hang_ms
        elif self._open:
            self._hang_remaining_ms -= block_ms
            if self._hang_remaining_ms <= 0.0:
                self._open = False
        return self._open

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def floor_db(self) -> Optional[float]:
        return self._floor_db

    @property
    def threshold_db(self) -> Optional[float]:
        return (self._floor_db + self.margin_db) if self._floor_db is not None else None


class NoiseSquelch:
    """解调后噪声静噪（适用于广播 WFM 这类连续载波）。

    连续载波用"功率高于噪声底"判据会失败：一开机就落在电台上，或底噪慢跟踪
    会把持续电台学进噪声底。改从**解调后音频**的频谱形态判断：
      - 无电台 → 鉴频嘶声，高频(6–12k)能量与语音带(0.3–3k)相当，频谱平坦
        （平坦度接近 1）；
      - 有电台 → 能量集中在语音/音乐带，高频嘶声被压制，频谱呈低平坦度。
    判据（满足其一即有信号）：中/高能带比 ≥ ``ratio_thr``，或平坦度 ≤ ``flat_thr``。

    实测门限（FC0012 @2.048M，0.5s 窗）：空频 ratio≈2.8/flat≈0.47，
    电台 ratio≈23~100/flat≈0.05~0.15，故 ratio_thr=6、flat_thr=0.30 留足余量。
    带 hang 避免节目短暂停顿关门。
    """

    def __init__(self,
                 sample_rate: float = 48000.0,
                 win_ms: float = 500.0,
                 ratio_thr: float = 6.0,
                 flat_thr: float = 0.30,
                 hang_ms: float = 400.0,
                 warmup_ms: float = 500.0) -> None:
        self.sample_rate = float(sample_rate)
        self.win_len = max(64, int(round(sample_rate * win_ms / 1000.0)))
        self.ratio_thr = float(ratio_thr)
        self.flat_thr = float(flat_thr)
        self.hang_ms = float(hang_ms)
        self.warmup_len = int(round(sample_rate * warmup_ms / 1000.0))
        self._ring = np.zeros(self.win_len, dtype=np.float64)
        self._filled = 0
        self._open = False
        self._hang_remaining_ms = 0.0

    def reset(self) -> None:
        self._ring[:] = 0.0
        self._filled = 0
        self._open = False
        self._hang_remaining_ms = 0.0

    @staticmethod
    def _band_energy(x: np.ndarray, sr: float, lo: float, hi: float) -> float:
        n = len(x)
        if n < 16:
            return 0.0
        X = np.abs(np.fft.rfft(x * np.hanning(n)))
        f = np.fft.rfftfreq(n, 1.0 / sr)
        hi = min(hi, sr / 2.0 - 1.0)
        m = (f >= lo) & (f <= hi)
        if not m.any():
            return 0.0
        return float(np.sum(X[m] ** 2) / n)

    @staticmethod
    def _flatness(x: np.ndarray, sr: float, lo: float = 300.0,
                  hi: float = 8000.0) -> float:
        n = len(x)
        if n < 16:
            return 1.0
        X = np.abs(np.fft.rfft(x * np.hanning(n)))
        f = np.fft.rfftfreq(n, 1.0 / sr)
        hi = min(hi, sr / 2.0 - 1.0)
        m = (f >= lo) & (f <= hi)
        if not m.any():
            return 1.0
        P = X[m] ** 2 + 1e-12
        return float(np.exp(np.mean(np.log(P))) / np.mean(P))

    def process_audio(self, mono_block: np.ndarray) -> bool:
        """喂入一块单声道解调音频，返回门是否打开。"""
        m = np.asarray(mono_block, dtype=np.float64).ravel()
        L = len(m)
        if L == 0:
            return self._open
        if L >= self.win_len:
            self._ring[:] = m[-self.win_len:]
            self._filled = self.win_len
        else:
            self._ring = np.roll(self._ring, -L)
            self._ring[-L:] = m
            self._filled = min(self.win_len, self._filled + L)

        block_ms = 1000.0 * L / max(self.sample_rate, 1e-9)
        if self._filled < self.warmup_len:
            return self._open
        seg = self._ring if self._filled >= self.win_len else self._ring[:self._filled]
        e_mid = self._band_energy(seg, self.sample_rate, 300.0, 3000.0)
        e_hi = self._band_energy(seg, self.sample_rate, 6000.0, 12000.0)
        ratio = e_mid / (e_hi + 1e-9)
        fl = self._flatness(seg, self.sample_rate)
        is_signal = (ratio >= self.ratio_thr) or (fl <= self.flat_thr)
        if is_signal:
            self._open = True
            self._hang_remaining_ms = self.hang_ms
        elif self._open:
            self._hang_remaining_ms -= block_ms
            if self._hang_remaining_ms <= 0.0:
                self._open = False
        return self._open

    @property
    def is_open(self) -> bool:
        return self._open
