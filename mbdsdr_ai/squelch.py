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
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    if x.size == 0:
        return -150.0
    p = float(np.mean(x * x))
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
