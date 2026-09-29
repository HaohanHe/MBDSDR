# SPDX-License-Identifier: MIT
"""
Analogue audio demodulation (pure NumPy).

IQ -> AM (envelope) / FM (phase-difference discriminator) / SSB (BFO sideband).

Receive-chain topology::

    input IQ -> [VFO digital NCO mixer] -> channel band-pass -> squelch (RMS)
             -> AGC -> demodulate -> audio low-pass

Key ordering rules:
  * The squelch must come before the AGC; otherwise with no signal the AGC would
    raise the noise floor to full volume.
  * Small VFO offsets are done in the digital NCO mixer (not by retuning the
    hardware centre frequency); retune only when out of range.
  * Switching mode also resets bandwidth / BFO / squelch threshold.
"""
from __future__ import annotations
import numpy as np
from typing import Dict, Callable, Optional

# AGC and audio-rate constants.  The narrow-band AGC reuses the CAgc-style
# implementation in gqrx_receiver.py.
from mbdsdr_ai.gqrx_receiver import GqrxAGC, AUDIO_RATE as GQRX_AUDIO_RATE


def _lowpass(x: np.ndarray, sr: float, cutoff: float, taps: int = 63) -> np.ndarray:
    n = np.arange(taps) - taps // 2
    h = 2 * cutoff / sr * np.sinc(2 * cutoff / sr * n)
    h *= np.hanning(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


# ---------------------------------------------------------------------------
# Standard receiver tuning constants (IF rate / bandwidth / de-emphasis per mode).
# ---------------------------------------------------------------------------

# De-emphasis time-constant table:
#   {22 us, 50 us, 75 us}.  50 us = EU/CN FM broadcast; 75 us = US FM broadcast.
SDRPP_DEEMP_TAU_US = {"none": 0.0, "22us": 22e-6, "50us": 50e-6, "75us": 75e-6}

# Per-mode IF sample rate / default bandwidth / minimum bandwidth:
#   WFM  IF=250000  defaultBW=150000 minBW=50000
#   NFM  IF=50000   defaultBW=12500  minBW=1000
#   AM   IF=15000   defaultBW=10000  minBW=1000
#   USB  IF=24000   defaultBW=2800   minBW=500  maxBW=IF/2=12000
SDRPP_MODE_PARAMS = {
    #        if_sr      default_bw  min_bw   default_deemph
    "wfm":  (250_000.0, 150_000.0, 50_000.0, "50us"),
    "nfm":  (50_000.0,  12_500.0,  1_000.0,  "none"),
    "am":   (15_000.0,  10_000.0,  1_000.0,  "none"),
    "usb":  (24_000.0,  2_800.0,   500.0,    "none"),
}

# WFM stereo / pilot constants:
#   pilot 19 kHz band-pass 18750..19250
#   audio low-pass 15 kHz, transition 4 kHz
#   RDS sub-carrier 57 kHz, resampled to 5000 Hz
SDRPP_WFM_PILOT_BAND = (18_750.0, 19_250.0)
SDRPP_WFM_AUDIO_LP = 15_000.0
SDRPP_RDS_SUBCARRIER = 57_000.0
SDRPP_RDS_RESAMPLE_RATE = 5_000.0

# Audio (AF) output sample rate: the post-demod audio chain runs at 48000 Hz.
SDRPP_AUDIO_SR = 48_000.0


class DeemphasisFilter:
    """First-order RC de-emphasis low-pass.

        dt = 1/samplerate;  alpha = dt/(tau+dt);
        out[i] = alpha*in[i] + (1-alpha)*out[i-1]
    tau: 75 us (US) / 50 us (EU) / 22 us.  Sample-wise IIR, state kept across
    blocks.
    """

    def __init__(self, tau: float, samplerate: float):
        self.tau = float(tau)
        self.sr = float(samplerate)
        self._last = 0.0
        self._update_alpha()

    def _update_alpha(self) -> None:
        dt = 1.0 / self.sr
        self.alpha = dt / (self.tau + dt)

    def set_tau(self, tau: float) -> None:
        self.tau = float(tau)
        self._update_alpha()

    def set_samplerate(self, sr: float) -> None:
        self.sr = float(sr)
        self._update_alpha()

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.tau <= 0 or len(x) == 0:
            return x
        x = np.asarray(x, dtype=np.float64)
        out = np.empty_like(x)
        prev = self._last
        a = self.alpha
        for i in range(len(x)):
            prev = a * x[i] + (1.0 - a) * prev
            out[i] = prev
        self._last = out[-1]
        return out.astype(np.float32)



# ---------------------------------------------------------------------------
# Narrow-band demodulation chain (stateful, streaming).
# ---------------------------------------------------------------------------

# Per-mode defaults for bandwidth / BFO / squelch threshold, applied together
# when the mode changes.
_MODE_DEFAULTS: Dict[str, Dict[str, float]] = {
    #           bw_hz    bfo_hz   squelch_dbfs
    "am":  dict(bw_hz=10_000.0, bfo_hz=0.0,   sql_db=-40.0),
    "fm":  dict(bw_hz=12_500.0, bfo_hz=0.0,   sql_db=-40.0),
    # wfm default bandwidth 150 kHz.
    "wfm": dict(bw_hz=150_000.0, bfo_hz=0.0,  sql_db=-30.0),
    "usb": dict(bw_hz=2_400.0,  bfo_hz=1_500.0, sql_db=-60.0),
    "lsb": dict(bw_hz=2_400.0,  bfo_hz=-1_500.0, sql_db=-60.0),
    "cw":  dict(bw_hz=500.0,    bfo_hz=700.0,  sql_db=-60.0),
}

# Maximum VFO offset handled by the digital down-converter: +/- sample_rate/4.
# Small offsets are done in the digital mixer; only larger offsets retune the
# hardware centre frequency (avoiding the tens-of-ms hardware retune latency).
_DDC_RANGE_FRAC = 0.25


class _HangAGC:
    """Two-time-constant AGC with a hold/hang mode.

      ATTACK_RISE = 2 ms   (a signal appears; fast gain pull-down)
      DECAY       = ~300 ms (signal gone; slow gain release)
      hang mode   = after a signal drops, hold the gain then release slowly
                    (important for SSB voice)
      The channel filter group delay is approximated here by block-level gain
      smoothing.
    """

    ATTACK_S = 0.002
    DECAY_S = 0.300
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
            # Squelch closed: keep the gain unchanged, return silence.
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
    """Stateful narrow-band demodulation chain.

    Topology::
        channel filter -> meter/squelch -> AGC -> demodulate
    A digital VFO NCO mixer is prepended to translate the tuned frequency to
    baseband.
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

        # Squelch state: initial threshold -150 dBFS = open.
        self.squelch_db = -150.0
        self._sql_open = False
        # Per-sample envelope smoothing alpha = 0.001.
        self._sql_alpha = 0.001
        # Hysteresis: open above threshold, close only below threshold-3 dB.
        self._sql_hyst_db = 3.0
        self.last_signal_power_db = -150.0

        # Narrow-band AGC.
        self._agc = GqrxAGC(self.sample_rate, agc_on=True, use_hang=False,
                            threshold_db=-100, slope=0, decay_ms=500)

        # De-emphasis (tau=0 = off until set_mode selects a profile).
        self.deemph = DeemphasisFilter(0.0, self.sample_rate)

        self.set_mode(mode)

    # ------------------------------------------------------------------
    #  VFO / 频率
    # ------------------------------------------------------------------
    def set_vfo_freq(self, vfo_hz: float) -> Dict:
        """Set the listening frequency.

        If |vfo - hw_center| <= sample_rate/4, only the digital NCO offset is
        changed (no hardware retune); otherwise the hardware centre frequency is
        retuned and the digital offset is cleared.
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
        """Switch demodulation mode and reset bandwidth / BFO / squelch together."""
        mode = (mode or "fm").lower()
        if mode not in _MODE_DEFAULTS:
            raise ValueError(f"未知模式: {mode}（{list(_MODE_DEFAULTS)}）")
        d = _MODE_DEFAULTS[mode]
        self.mode = mode
        self.bw_hz = d["bw_hz"]
        self.bfo_hz = d["bfo_hz"]
        # Reset the squelch threshold to the per-mode default.
        self.squelch_db = d["sql_db"]
        self.audio_bw = min(3_000.0, d["bw_hz"] * 0.4)
        if mode in ("fm", "nfm"):
            self.max_dev = 5_000.0
        elif mode == "wfm":
            # WFM peak deviation = bandwidth/2.
            self.max_dev = self.bw_hz / 2.0
            self.audio_bw = SDRPP_WFM_AUDIO_LP   # 15 kHz
        elif mode == "am":
            self.max_dev = 0.0

        # De-emphasis profile per mode (wfm=50 us; nfm/am/ssb=none).
        mode_key = {"wfm": "wfm", "fm": "nfm", "nfm": "nfm",
                    "am": "am", "usb": "usb", "lsb": "usb"}.get(mode)
        if mode_key and mode_key in SDRPP_MODE_PARAMS:
            deemp_name = SDRPP_MODE_PARAMS[mode_key][3]
            self.deemph.set_tau(SDRPP_DEEMP_TAU_US[deemp_name])
            self.deemph.set_samplerate(self.sample_rate)
            self.deemph_region = deemp_name
        self._agc.reset()
        return {"mode": mode, "bw_hz": self.bw_hz, "bfo_hz": self.bfo_hz,
                "squelch_db": self.squelch_db,
                "deemphasis": getattr(self, "deemph_region", "none")}

    # ------------------------------------------------------------------
    #  静噪
    # ------------------------------------------------------------------
    def set_squelch_db(self, db: float):
        self.squelch_db = float(db)

    def auto_squelch(self) -> float:
        """Auto-squelch = current noise floor + 3 dB margin.

        Clamped so it never exceeds -10 dBFS (protecting against 0 dBFS).
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

        # 3) Squelch (RMS energy detection) -- must come before the AGC.
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

        # 4) AGC (after squelch, before demod).  When squelch is closed the AGC
        #    is not updated (silence must not pump the gain up).
        if sql_open:
            agc_out = self._agc.process(gated)
        else:
            agc_out = np.zeros_like(filt)

        # 5) 解调
        audio = self._demod(agc_out)

        # 6) 音频低通
        audio = _lowpass(audio, self.sample_rate, self.audio_bw) if len(audio) else audio

        # 6b) De-emphasis (first-order RC IIR; tau=0 = passthrough).
        if len(audio) and getattr(self, "deemph", None) is not None \
                and self.deemph.tau > 0:
            audio = self.deemph.process(audio)

        # 6c) Resample audio to the 48 kHz output rate.
        from mbdsdr_ai.gqrx_receiver import _resample_to
        audio = _resample_to(audio, self.sample_rate, GQRX_AUDIO_RATE) if len(audio) else audio

        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 1e-9:
            audio = audio / peak
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
        return {
            "audio": audio.astype(np.float32).tolist(),
            "sample_rate": GQRX_AUDIO_RATE,
            "mode": self.mode,
            "rms_db": round(20 * np.log10(rms + 1e-9), 1),
            "squelch_open": sql_open,
            "signal_power_db": round(self.last_signal_power_db, 1),
        }

    def _nco_mix(self, iq: np.ndarray) -> np.ndarray:
        """Digital down-conversion: move the signal at +_nco_offset Hz to baseband.

        Implemented as complex mixing by exp(-j*2*pi*offset*t), with the phase
        kept continuous across blocks.
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
        elif mode in ("fm", "nfm", "wfm"):
            # Quadrature phase-difference discriminator.
            if len(x) < 2:
                return np.zeros(len(x), dtype=np.float64)
            phase = np.angle(x[1:] * np.conj(x[:-1]))
            # Remove DC after discrimination, otherwise CFO residue becomes hum.
            audio = phase / (2 * np.pi * self.max_dev / self.sample_rate)
            audio = np.concatenate([audio, audio[-1:]])
            audio = audio - np.mean(audio)
        elif mode in ("usb", "lsb", "cw"):
            # BFO beat: move the suppressed carrier into the audio band.
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
