"""
MBDSDR 模拟音频解调（纯 numpy）
================================
IQ -> AM(包络) / FM(相位差分鉴频) / SSB(BFO 边带) 音频。

GQRX 接收链拓扑对照（repos/gqrx/src/receivers/nbrx.cpp）：

    输入 IQ → [VFO 数字 NCO 混频] → 信道带通 → 静噪(RMS 能量) → AGC → 解调 → 音频低通

关键拓扑决策（见 docs/learn/gqrx.md 第 3/4/5 节）：
  * 静噪必须在 AGC 之前：否则无信号时 AGC 会把噪声底一路增益拉到满音量。
  * VFO 小范围偏移用数字 NCO 混频实现，不调硬件中心频率；越界才重新调谐。
  * 切模式时同时重设带宽 / BFO / 静噪门限。
"""
from __future__ import annotations
import numpy as np
from typing import Dict, Callable, Optional


def _lowpass(x: np.ndarray, sr: float, cutoff: float, taps: int = 63) -> np.ndarray:
    n = np.arange(taps) - taps // 2
    h = 2 * cutoff / sr * np.sinc(2 * cutoff / sr * n)
    h *= np.hanning(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


# ═══════════════════════════════════════════════════════════════════════
#  GQRX 风格窄带解调链（状态化，流式逐块处理）
# ═══════════════════════════════════════════════════════════════════════

# 来源: GQRX mainwindow.cpp:1308-1314 — 切模式时统一重设
#   带通 low/high cut、CW BFO、静噪门限。这里给出各模式默认带宽/BFO/静噪。
_MODE_DEFAULTS: Dict[str, Dict[str, float]] = {
    #           bw_hz    bfo_hz   squelch_dbfs
    "am":  dict(bw_hz=10_000.0, bfo_hz=0.0,   sql_db=-40.0),
    "fm":  dict(bw_hz=12_500.0, bfo_hz=0.0,   sql_db=-40.0),
    "usb": dict(bw_hz=2_400.0,  bfo_hz=1_500.0, sql_db=-60.0),
    "lsb": dict(bw_hz=2_400.0,  bfo_hz=-1_500.0, sql_db=-60.0),
    "cw":  dict(bw_hz=500.0,    bfo_hz=700.0,  sql_db=-60.0),
}

# 数字下变频可用的最大 VFO 偏移：±sample_rate/4。
# 来源: GQRX receiver.cpp:655-661 / docs/learn/gqrx.md §2.3 — 小偏移走 DDC 数字混频，
# 超出这个范围才重新调谐硬件 center_freq（避免每次点击频谱都触发硬件重 tune 的几十 ms 延迟）。
_DDC_RANGE_FRAC = 0.25


class _GqrxAGC:
    """双时间常数 AGC + hang 模式（GQRX CAgc 的精简 numpy 版）。

    来源: GQRX agc_impl.cpp:49-63, 124-190
      ATTACK_RISE   = 0.002 s   (信号出现、需快速压增益)
      DECAY         = ~几百 ms   (信号消失、缓慢放增益)
      hang 模式     = 信号掉落后先保持增益，再慢释放（对话音 SSB 关键）
      15 ms 延迟线 = 补偿信道滤波器群延迟（这里用块级增益平滑近似）。
    """

    # 来源: GQRX agc_impl.cpp:56-57 — attack 2ms；decay 取 300ms（语音档）
    ATTACK_S = 0.002
    DECAY_S = 0.300
    # 来源: GQRX agc_impl.cpp:56-57,261-268 — hang 保持约 100ms 再释放
    HANG_S = 0.100

    def __init__(self, sample_rate: float, target_level: float = 0.5):
        self.sr = float(sample_rate)
        self.target_level = float(target_level)
        self.gain = 1.0
        self._hang_timer = 0.0

    def process(self, iq: np.ndarray, gated: bool = True) -> np.ndarray:
        """处理一段复 IQ。

        gated=False 表示静噪已关断（输入为零）：此时保持当前增益不更新，
        这正是「静噪在 AGC 之前」的意义——无信号时 AGC 不会被零/噪声抽风。
        """
        n = len(iq)
        if n == 0:
            return iq
        if not gated:
            # 来源: GQRX nbrx.cpp:78 — sql 关断后 AGC 输入被静音，不更新增益
            return np.zeros_like(iq)

        level = float(np.sqrt(np.mean(np.abs(iq) ** 2))) + 1e-12
        desired = min(self.target_level / level, 1000.0)
        dt = n / self.sr

        if desired < self.gain:
            # 信号变强 → attack 快速压增益
            alpha = 1.0 - np.exp(-dt / self.ATTACK_S)
            self._hang_timer = 0.0
        else:
            # 信号变弱 → hang 保持后慢 decay 放增益
            self._hang_timer += dt
            if self._hang_timer < self.HANG_S:
                alpha = 0.0
            else:
                alpha = 1.0 - np.exp(-dt / self.DECAY_S)

        self.gain = (1.0 - alpha) * self.gain + alpha * desired
        return iq * self.gain

    def reset(self):
        self.gain = 1.0
        self._hang_timer = 0.0


class NarrowbandReceiver:
    """GQRX nbrx 风格的窄带解调链（状态化）。

    拓扑（来源: GQRX nbrx.cpp:76-79）：
        filter → meter/squelch → AGC → demod
    本类在前面再串一级 VFO 数字 NCO 混频（来源: GQRX receiver.cpp:655-661）。
    """

    def __init__(self,
                 sample_rate: float = 480_000.0,
                 hardware_center_freq: float = 100e6,
                 hw_tune_callback: Optional[Callable[[float], None]] = None,
                 mode: str = "fm"):
        self.sample_rate = float(sample_rate)
        self.hardware_center_freq = float(hardware_center_freq)
        self._hw_tune = hw_tune_callback
        self.hw_tune_call_count = 0

        # VFO 状态
        self.vfo_freq = float(hardware_center_freq)
        self._nco_offset = 0.0          # 数字混频偏移（Hz），不动硬件
        self._nco_phase = 0.0           # NCO 相位连续

        # 解调/链路参数（由 set_mode 统一设置）
        self.mode = "fm"
        self.bw_hz = 12_500.0
        self.bfo_hz = 0.0
        self.audio_bw = 3_000.0
        self.max_dev = 5_000.0

        # 静噪状态（来源: GQRX nbrx.cpp:48 — 初始阈值 -150 dBFS = 全开）
        self.squelch_db = -150.0
        self._sql_open = False
        # 来源: GQRX nbrx.cpp:48 — simple_squelch_cc 的 alpha=0.001（逐样本包络平滑）
        self._sql_alpha = 0.001
        # 门控滞回：高于门限开，低于门限-3dB 才关（防抖动）
        self._sql_hyst_db = 3.0
        self.last_signal_power_db = -150.0

        self._agc = _GqrxAGC(self.sample_rate)

        self.set_mode(mode)

    # ------------------------------------------------------------------
    #  VFO / 频率
    # ------------------------------------------------------------------
    def set_vfo_freq(self, vfo_hz: float) -> Dict:
        """设置收听频率。

        来源: GQRX receiver.cpp:655-661, mainwindow.cpp:1028 —
          若 |vfo - hw_center| ≤ sample_rate/4：只改数字 NCO 偏移，不调硬件；
          否则重新调谐硬件 center_freq，数字偏移归零。
        """
        vfo_hz = float(vfo_hz)
        offset = vfo_hz - self.hardware_center_freq
        max_digital = self.sample_rate * _DDC_RANGE_FRAC

        if abs(offset) <= max_digital:
            # 范围内：数字下变频，不碰硬件
            self._nco_offset = offset
            retuned = False
        else:
            # 越界：把硬件中心频率搬到 VFO，数字偏移清零
            self.hardware_center_freq = vfo_hz
            self._nco_offset = 0.0
            if self._hw_tune is not None:
                self._hw_tune(vfo_hz)
            self.hw_tune_call_count += 1
            retuned = True

        self.vfo_freq = vfo_hz
        return {
            "vfo_hz": vfo_hz,
            "hardware_center_hz": self.hardware_center_freq,
            "digital_offset_hz": self._nco_offset,
            "retuned_hardware": retuned,
        }

    @property
    def digital_offset_hz(self) -> float:
        return self._nco_offset

    # ------------------------------------------------------------------
    #  模式切换（联动带宽 / BFO / 静噪）
    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> Dict:
        """切换解调模式，并按 GQRX 惯例同时重设带宽/BFO/静噪门限。

        来源: GQRX mainwindow.cpp:1308-1314 — 切模式 = 改解调块 + 重设带通
          + 重设 CW BFO + 重设静噪门限（四件套）。
        """
        mode = (mode or "fm").lower()
        if mode not in _MODE_DEFAULTS:
            raise ValueError(f"未知模式: {mode}（{list(_MODE_DEFAULTS)}）")
        d = _MODE_DEFAULTS[mode]
        self.mode = mode
        self.bw_hz = d["bw_hz"]
        self.bfo_hz = d["bfo_hz"]
        # 来源: GQRX mainwindow.cpp:1313 — 切模式后把静噪门限刷成当前档
        self.squelch_db = d["sql_db"]
        self.audio_bw = min(3_000.0, d["bw_hz"] * 0.4)
        if mode == "fm":
            self.max_dev = 5_000.0
        elif mode == "am":
            self.max_dev = 0.0
        self._agc.reset()
        return {"mode": mode, "bw_hz": self.bw_hz, "bfo_hz": self.bfo_hz,
                "squelch_db": self.squelch_db}

    # ------------------------------------------------------------------
    #  静噪
    # ------------------------------------------------------------------
    def set_squelch_db(self, db: float):
        self.squelch_db = float(db)

    def auto_squelch(self) -> float:
        """自动静噪 = 当前噪声底 + 3 dB 裕量。

        来源: GQRX mainwindow.cpp:1460-1468 —
          level = 当前电平 + 3.0 dB；超过 -10 dBFS 时钳回（防 0 dBFS）。
        """
        level = self.last_signal_power_db + 3.0
        if level > -10.0:
            level = -10.0
        self.squelch_db = level
        return level

    # ------------------------------------------------------------------
    #  处理一段 IQ
    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> Dict:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return {"audio": [], "sample_rate": self.sample_rate, "mode": self.mode,
                    "rms_db": -120.0, "squelch_open": False,
                    "signal_power_db": -150.0}

        # 1) VFO 数字 NCO 混频：把 VFO 处的信号搬到基带（不调硬件）
        mixed = self._nco_mix(iq)

        # 2) 信道滤波：按模式带宽低通到 bw/2
        filt = _lowpass(mixed, self.sample_rate, self.bw_hz / 2.0)

        # 3) 静噪（RMS 能量检测）——必须在 AGC 之前
        #    来源: GQRX nbrx.cpp:77-78 — connect(filter, sql) → connect(sql, agc)
        power = float(np.mean(np.abs(filt) ** 2))
        inst_db = 10.0 * np.log10(power + 1e-12)
        # 逐样本 alpha=0.001 的包络平滑换算到本块（N 个样本）
        alpha_blk = 1.0 - (1.0 - self._sql_alpha) ** n
        self.last_signal_power_db = ((1.0 - alpha_blk) * self.last_signal_power_db
                                     + alpha_blk * inst_db)
        # 滞回门控：高于门限开，低于门限-3dB 才关（防门限附近抖动 click）
        if not self._sql_open and self.last_signal_power_db >= self.squelch_db:
            self._sql_open = True
        elif self._sql_open and self.last_signal_power_db < self.squelch_db - self._sql_hyst_db:
            self._sql_open = False
        sql_open = self._sql_open
        gated = filt if sql_open else np.zeros_like(filt)

        # 4) AGC（静噪之后、解调之前）——来源: GQRX nbrx.cpp:78-79
        agc_out = self._agc.process(gated, gated=sql_open)

        # 5) 解调
        audio = self._demod(agc_out)

        # 6) 音频低通
        audio = _lowpass(audio, self.sample_rate, self.audio_bw) if len(audio) else audio

        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 1e-9:
            audio = audio / peak
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
        return {
            "audio": audio.astype(np.float32).tolist(),
            "sample_rate": self.sample_rate,
            "mode": self.mode,
            "rms_db": round(20 * np.log10(rms + 1e-9), 1),
            "squelch_open": sql_open,
            "signal_power_db": round(self.last_signal_power_db, 1),
        }

    def _nco_mix(self, iq: np.ndarray) -> np.ndarray:
        """数字下变频：把 +_nco_offset Hz 处的信号搬到基带。

        来源: GQRX receiver.cpp:658 — ddc->set_center_freq(offset)；
        这里用复数混频 exp(-j*2π*offset*t) 等价实现，相位跨块连续。
        """
        n = len(iq)
        if self._nco_offset == 0.0 or n == 0:
            return iq
        dphi = 2.0 * np.pi * self._nco_offset / self.sample_rate
        phases = self._nco_phase + dphi * np.arange(n)
        self._nco_phase = (self._nco_phase + dphi * n) % (2.0 * np.pi)
        return iq * np.exp(-1j * phases)

    def _demod(self, x: np.ndarray) -> np.ndarray:
        mode = self.mode
        if mode == "am":
            audio = np.abs(x)
            audio = audio - np.mean(audio)
        elif mode == "fm":
            if len(x) < 2:
                return np.zeros(len(x), dtype=np.float64)
            phase = np.angle(x[1:] * np.conj(x[:-1]))
            # 来源: analog_demod 审计修复 — FM 鉴频后必须去直流，否则 CFO 残留成哼声
            audio = phase / (2 * np.pi * self.max_dev / self.sample_rate)
            audio = np.concatenate([audio, audio[-1:]])
            audio = audio - np.mean(audio)
        elif mode in ("usb", "lsb", "cw"):
            # BFO 拍频：把被抑制载波搬到音频（来源: GQRX mainwindow.cpp:1282-1298）
            n = len(x)
            t = np.arange(n) / self.sample_rate
            bfo = np.exp(1j * 2 * np.pi * self.bfo_hz * t)
            audio = (x * bfo).real
        else:
            raise ValueError(f"未知模式: {mode}")
        return audio


# ═══════════════════════════════════════════════════════════════════════
#  向后兼容的一次性解调入口（无状态，供 agent.py 工具直接调用）
# ═══════════════════════════════════════════════════════════════════════

def demod_analog(iq: np.ndarray, sample_rate: float, mode: str = "fm",
                 max_dev: float = 5000.0, audio_bw: float = 3000.0) -> Dict:
    """IQ -> 模拟音频（一次性、无状态）。

    mode: am(包络检波) / fm(相位差分鉴频) / usb / lsb(边带)。
    返回 {audio, sample_rate, mode, rms_db}。
    """
    chain = NarrowbandReceiver(sample_rate=sample_rate, mode=mode)
    # 一次性调用不开静噪（阈值 -150 = 全开），保持旧工具行为一致
    chain.set_squelch_db(-150.0)
    chain.audio_bw = float(audio_bw)
    if mode == "fm":
        chain.max_dev = float(max_dev)
    return chain.process(iq)


if __name__ == "__main__":
    # 自测：合成 FM 信号（音调 400Hz 调制 5kHz 频偏）
    sr = 480000
    t = np.arange(sr) / sr
    mod = 0.4 * np.sin(2 * np.pi * 400 * t)
    phase = 2 * np.pi * 5000 * np.cumsum(mod) / sr
    iq = np.exp(1j * phase)
    r = demod_analog(iq, sr, mode="fm", max_dev=5000)
    a = np.array(r["audio"])
    sp = np.abs(np.fft.rfft(a))
    fr = np.fft.rfftfreq(len(a), 1 / sr)
    peak = fr[np.argmax(sp[10:]) + 10]
    print(f"FM 解调: 模式={r['mode']} 解出音调≈{peak:.0f}Hz (目标400)")
