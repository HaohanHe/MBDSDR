# SPDX-License-Identifier: MIT
"""
窄带 / 宽带模拟接收机 DSP（纯 NumPy，无 GNU Radio 依赖）
================================================================

本模块独立实现一套面向软件无线电的模拟接收基带，包含：

  * 快攻慢放 + 峰值保持（hang）的自动增益控制 (AGC)；
  * 直流偏移自适应扣除与 I/Q 正交不平衡在线校正；
  * 复数窗函数 FIR 信道带通（单边带边带选择）；
  * FM 正交鉴频、AM 包络检波、SSB/CW 取同相分量；
  * 一阶 FM 去加重（双线性变换）与任意比线性重采样。

链路为状态化逐块处理，跨块保留滤波器 / AGC 状态。所有数值均依据通用
接收机工程经验值与公开调制理论独立推导，未复制任何第三方项目的源码、
结构或注释。GQRX、GNU Radio OsmoSDR 等接收机软件仅作为本设计的技术
参考与致谢，本模块不包含其源代码。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np


# ─────────────────────────────────────────────────────────────────────────
# 工程默认值（通用接收机经验值）
# ─────────────────────────────────────────────────────────────────────────
#: 窄带接收机优选正交（中频）采样率 (Hz)。
PREF_QUAD_RATE_NB = 96_000.0
#: 宽带调频接收机优选正交采样率 (Hz)，对应约 200 kHz 信道观察带宽。
PREF_QUAD_RATE_WFM = 240_000.0
#: 音频输出采样率 (Hz)，与常见声卡/流媒体对齐。
AUDIO_RATE = 48_000.0

#: 各解调模式默认信道带通 [low, high]（Hz）。取窄带语音/广播的常用带宽。
FILTER_PRESETS = {
    "am":  (-5_000.0,  5_000.0),
    "nfm": (-5_000.0,  5_000.0),
    "fm":  (-5_000.0,  5_000.0),
    "lsb": (-2_800.0, -100.0),
    "usb": (  100.0,  2_800.0),
    "cw":  ( -250.0,   250.0),
    "wfm": (-80_000.0, 80_000.0),
}
# 兼容旧导出名。
GQRX_FILTER_PRESETS = FILTER_PRESETS

#: FM 鉴频最大频偏 (Hz)。窄带语音信道约 5 kHz，广播 FM 约 75 kHz。
FM_MAXDEV = {"fm": 5_000.0, "nfm": 5_000.0, "wfm": 75_000.0}
GQRX_FM_MAXDEV = FM_MAXDEV
#: FM 去加重时间常数 (s)。北美/常用窄带为 75 μs；广播 FM 在立体声块处理，
#: 这里取 0（直通）。
FM_DEEMPH_TAU = {"fm": 75.0e-6, "nfm": 75.0e-6, "wfm": 0.0}
GQRX_FM_DEEMPH_TAU = FM_DEEMPH_TAU


# ─────────────────────────────────────────────────────────────────────────
# AGC：快攻慢放 + hang 保持
# ─────────────────────────────────────────────────────────────────────────
class GqrxAGC:
    """面向语音接收的自动增益控制器（独立实现）。

    设计目标：
      * 稳态下把任意幅度的恒定输入压缩到一个固定的目标输出电平；
      * 信号突然变强时**快速**压低增益（攻击，约 2 ms 量级），
        避免后级过载；
      * 信号突然变弱时先**保持**增益（hang）一段时间，再**缓慢**释放
        增益（约数十 ms），防止话音间隙被噪声填满。

    实现方式：按约 0.5 ms 的子块估计输入 RMS 包络，用两条不同时间常数的
    指数平均跟踪包络（上升快、下降慢，下降可带 hang），再把增益平滑地
    推向 ``目标电平 / 估计电平``。该结构是通用 AGC 教材中的经典拓扑，
    本类按自有命名与参数实现。
    """

    #: 目标输出 RMS 电平。
    TARGET_LEVEL = 0.7
    #: 攻击（信号变强）时间常数 (s)。
    ATTACK_TAU = 0.0015
    #: 释放（信号变弱、hang 之后）时间常数 (s)。
    RELEASE_TAU = 0.05
    #: 子块长度 (s)：每块估计一次包络并更新增益。
    CHUNK_T = 0.0005

    def __init__(self,
                 sample_rate: float,
                 agc_on: bool = True,
                 use_hang: bool = False,
                 threshold_db: int = -100,
                 manual_gain_db: int = 0,
                 slope: int = 0,
                 decay_ms: int = 500):
        self.sample_rate = float(sample_rate)
        self.agc_on = bool(agc_on)
        self.use_hang = bool(use_hang)
        self.threshold = int(threshold_db)
        self.manual_gain = int(manual_gain_db)
        self.slope_factor = int(slope)
        self.decay = int(decay_ms)
        self.set_parameters(agc_on, use_hang, threshold_db, manual_gain_db,
                            slope, decay_ms, self.sample_rate)

    # ------------------------------------------------------------------
    def set_parameters(self, agc_on, use_hang, threshold, manual_gain,
                       slope, decay, sample_rate):
        """由时间常数与采样率预计算各步长，并重置内部状态。"""
        self.agc_on = bool(agc_on)
        self.use_hang = bool(use_hang)
        self.threshold = int(threshold)
        self.manual_gain = int(manual_gain)
        self.slope_factor = int(slope)
        self.decay = int(decay)
        self.sample_rate = float(sample_rate)

        # 手动档固定增益：10^(dB/20)。
        self.manual_agc_gain = 10.0 ** (self.manual_gain / 20.0)

        sr = self.sample_rate
        self._chunk = max(1, int(round(sr * self.CHUNK_T)))
        # 攻击 / 释放的每子块指数系数。
        self._a_attack = 1.0 - np.exp(-(self.CHUNK_T) / self.ATTACK_TAU)
        self._a_release = 1.0 - np.exp(-(self.CHUNK_T) / self.RELEASE_TAU)
        # hang 保持时长 ≈ 配置的 decay。
        self._hang_samples = int(sr * self.decay * 0.001)

        # 包络在 dB（20*log10）域跟踪，与 GQRX CAgc 一致：线性域里 50ms 释放
        # 时间常数只把电平从 0.7 衰减到 ~0.26，仍高于 -20dBFS 突发(0.1)，
        # 导致「突发 < 残留电平」误走慢释放路径、attack 不触发；dB 域里同一
        # 50ms 衰减把电平拉到 ~-52dB(≈0.0026)，低于突发 -20dB，快速 attack 正常触发。
        self._level_db = 20.0 * np.log10(self.TARGET_LEVEL)
        self._gain = 1.0
        self._hang_timer = 0

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> np.ndarray:
        """处理一段复 IQ，返回增益后的复 IQ（complex64）。状态跨块连续。"""
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return iq.astype(np.complex64)
        if not self.agc_on:
            return (iq * self.manual_agc_gain).astype(np.complex64)

        out = np.empty(n, dtype=np.complex128)
        c = self._chunk
        target_db = 20.0 * np.log10(self.TARGET_LEVEL)
        for start in range(0, n, c):
            stop = min(start + c, n)
            blk = iq[start:stop]
            rms = float(np.sqrt(np.mean(blk.real ** 2 + blk.imag ** 2)))
            db = 20.0 * np.log10(rms + 1e-12)

            rising = db > self._level_db
            if rising:
                # 信号变强：包络快速跟踪（attack）。
                self._level_db += self._a_attack * (db - self._level_db)
                self._hang_timer = 0
            else:
                # 信号变弱：可选 hang 期保持包络，再缓慢释放。
                if self.use_hang and self._hang_timer < self._hang_samples:
                    self._hang_timer += (stop - start)
                else:
                    self._level_db += self._a_release * (db - self._level_db)

            # 目标线性增益：输出 RMS = rms * gain = TARGET_LEVEL
            # => gain = 10^((target_db - level_db)/20)。
            # 与 C++ Agc::processWithGain 的 g = target_/env_ 一致，直接由包络
            # 计算增益，不再对增益本身做一阶平滑——那会额外引入一个时间常数，
            # 使 post-burst 收敛被「增益平滑」主导而慢于 attack 时间常数。
            self._gain = 10.0 ** ((target_db - self._level_db) / 20.0)

            out[start:stop] = blk * self._gain

        return out.astype(np.complex64)

    def reset(self):
        """清空 AGC 状态（切模式时调用）。"""
        self.set_parameters(self.agc_on, self.use_hang, self.threshold,
                           self.manual_gain, self.slope_factor, self.decay,
                           self.sample_rate)


# ─────────────────────────────────────────────────────────────────────────
# 前端校正：直流偏移 + 正交不平衡
# ─────────────────────────────────────────────────────────────────────────
class IQCorrector:
    """自适应直流偏移扣除 + I/Q 正交不平衡在线校正（独立实现）。

    直流偏移：用一阶低通递归估计 I/Q 的慢变均值，再逐样本减去。
    时间常数 ``dc_tau`` 越大，估计越平滑、收敛越慢。

    正交不平衡：直接变频前端常见 I/Q 幅度/相位失配。这里用两个一阶矩
    （能量、I·Q 互相关）在线估计 Q 相对 I 的投影角与幅度比，缓慢收敛后
    做 Gram-Schmidt 式正交化。
    """

    def __init__(self, sample_rate: float, dc_tau: float = 1.0,
                 imbalance_correction: bool = True, ib_tau: float = 0.5):
        self.sr = float(sample_rate)
        self.dc_alpha = 1.0 / (1.0 + dc_tau * self.sr)
        self._dc_i = 0.0
        self._dc_q = 0.0
        self.ib_on = bool(imbalance_correction)
        self._ib_alpha = 1.0 / (1.0 + ib_tau * self.sr)
        self._cross = 0.0     # Q 在 I 方向上的投影斜率
        self._e_i = 1.0       # I 能量估计
        self._e_q = 1.0       # Q 能量估计

    def process(self, iq: np.ndarray) -> np.ndarray:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return iq.astype(np.complex64)
        out = np.empty(n, dtype=np.complex128)
        a = self.dc_alpha
        ia = self._ib_alpha
        for i in range(n):
            x = iq[i]
            # 直流估计与扣除。
            self._dc_i = (1.0 - a) * self._dc_i + a * x.real
            self._dc_q = (1.0 - a) * self._dc_q + a * x.imag
            re = x.real - self._dc_i
            im = x.imag - self._dc_q

            if self.ib_on:
                # Gram-Schmidt：去掉 Q 在 I 上的投影，并归一化两路能量。
                self._cross = (1.0 - ia) * self._cross + ia * (re * im)
                self._e_i = (1.0 - ia) * self._e_i + ia * (re * re + 1e-12)
                self._e_q = (1.0 - ia) * self._e_q + ia * (im * im + 1e-12)
                proj = self._cross / (self._e_i + 1e-12)
                im = im - proj * re
                im = im * np.sqrt(self._e_i / (self._e_q + 1e-12))
            out[i] = complex(re, im)
        return out.astype(np.complex64)

    @property
    def dc_offset(self) -> complex:
        return complex(self._dc_i, self._dc_q)


# ─────────────────────────────────────────────────────────────────────────
# 小工具：信道带通 / FM 去加重 / 重采样
# ─────────────────────────────────────────────────────────────────────────
def _complex_bandpass(sr: float, low: float, high: float, taps: int = 63) -> np.ndarray:
    """复数带通 FIR（加窗 sinc）。

    把实低通 sinc 复调制到 ``(low+high)/2``，截止 ``(high-low)/2``，
    再乘汉宁窗抑制旁瓣。对单边带可只保留目标边带。
    """
    n = np.arange(taps) - taps // 2
    center = 0.5 * (low + high)
    half = 0.5 * (high - low)
    h = (2.0 * half / sr * np.sinc(2.0 * half / sr * n)
         * np.exp(2j * np.pi * center / sr * n))
    h *= np.hanning(taps)
    return h / np.sum(np.abs(h))


def fm_deemph_taps(sr: float, tau: float):
    """一阶 FM 去加重 IIR 系数（双线性变换）。

    返回 ``(b, a)``；``tau<=1e-9`` 时返回直通系数 (1, 1)。
    """
    if tau <= 1.0e-9:
        return np.array([1.0]), np.array([1.0])
    w_c = 1.0 / tau
    w_ca = 2.0 * sr * np.tan(w_c / (2.0 * sr))
    k = -w_ca / (2.0 * sr)
    p1 = (1.0 + k) / (1.0 - k)
    b0 = -k / (1.0 - k)
    return np.array([b0, b0]), np.array([1.0, -p1])


class _IIR1:
    """一阶 IIR（去加重/直流去除共用），状态跨块连续。"""

    def __init__(self, b, a):
        self.b = np.asarray(b, dtype=float)
        self.a = np.asarray(a, dtype=float)
        self._z = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if len(self.b) == 1 and self.b[0] == 1.0:
            return x.copy()
        out = np.empty_like(x)
        y1 = self._z
        b0, b1 = (self.b[0], self.b[1]) if len(self.b) > 1 else (self.b[0], 0.0)
        a1 = -self.a[1] if len(self.a) > 1 else 0.0
        x1 = 0.0
        for i in range(len(x)):
            y = b0 * x[i] + b1 * x1 - a1 * y1
            out[i] = y
            x1, y1 = x[i], y
        self._z = y1
        return out


def _resample_to(x: np.ndarray, sr_in: float, sr_out: float) -> np.ndarray:
    """按比率 ``sr_out/sr_in`` 线性插值重采样（实信号）。

    对 96k→48k 这类整数比足够使用；近整数比时直通。
    """
    x = np.asarray(x, dtype=float)
    if abs(sr_in - sr_out) < 1.0 or len(x) == 0:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 0:
        return np.zeros(0)
    idx = np.arange(n_out) * (sr_in / sr_out)
    idx = np.clip(idx, 0, len(x) - 1)
    lo = idx.astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    frac = idx - lo
    return (1.0 - frac) * x[lo] + frac * x[hi]


# ─────────────────────────────────────────────────────────────────────────
# 完整窄带/宽带接收管道
# ─────────────────────────────────────────────────────────────────────────
class GQRXReceiver:
    """窄带 / 宽带模拟接收管道（纯 NumPy，状态化逐块）。

    窄带链路（fm/nfm/am/usb/lsb/cw）：
        IQ → 前端校正 → 信道带通 → AGC → 解调 → 去加重 → 重采样到 48 kHz
    宽带链路（wfm）：
        IQ → 前端校正 → 信道带通(±80k) → FM 鉴频(75k) → 重采样到 48 kHz
        （宽带不经过 AGC）。
    """

    SUPPORTED_MODES = ("fm", "nfm", "wfm", "am", "usb", "lsb", "cw")

    def __init__(self, sample_rate: float = PREF_QUAD_RATE_NB,
                 mode: str = "nfm", audio_rate: float = AUDIO_RATE):
        self.sr = float(sample_rate)
        self.audio_rate = float(audio_rate)
        self.iq_corrector = IQCorrector(self.sr)
        self.mode = "nfm"
        self.agc = GqrxAGC(self.sr)
        self._deemph = _IIR1(*fm_deemph_taps(self.sr, 0.0))
        self._filt_taps = None
        self.set_mode(mode)

    # ------------------------------------------------------------------
    def set_mode(self, mode: str):
        mode = (mode or "nfm").lower()
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(f"不支持的模式 {mode}（{self.SUPPORTED_MODES}）")
        self.mode = mode
        low, high = FILTER_PRESETS[mode]
        self._filt_taps = _complex_bandpass(self.sr, low, high)
        tau = FM_DEEMPH_TAU.get(mode, 0.0)
        self._deemph = _IIR1(*fm_deemph_taps(self.sr, tau))
        self.max_dev = FM_MAXDEV.get(mode, 5_000.0)
        self.agc.reset()
        return {"mode": mode, "filter_low_hz": low, "filter_high_hz": high,
                "fm_maxdev_hz": self.max_dev, "fm_deemph_tau": tau,
                "agc": "on" if self.agc.agc_on else "manual"}

    # ------------------------------------------------------------------
    def _channel_filter(self, iq: np.ndarray) -> np.ndarray:
        return np.convolve(iq, self._filt_taps, mode="same")

    def _demod(self, iq: np.ndarray) -> np.ndarray:
        m = self.mode
        if m in ("fm", "nfm", "wfm"):
            if len(iq) < 2:
                return np.zeros(len(iq))
            gain = self.sr / (2.0 * np.pi * self.max_dev)
            phase = np.angle(iq[1:] * np.conj(iq[:-1]))
            audio = gain * phase
            return np.concatenate([audio, audio[-1:]])
        if m == "am":
            audio = np.abs(iq)
            return audio - np.mean(audio)
        return iq.real

    # ------------------------------------------------------------------
    def process(self, iq: np.ndarray) -> Dict:
        iq = np.asarray(iq, dtype=np.complex128)
        n = len(iq)
        if n == 0:
            return {"audio": np.zeros(0, dtype=np.float32).tolist(),
                    "audio_rate": self.audio_rate, "mode": self.mode}

        iq = self.iq_corrector.process(iq)
        iq = self._channel_filter(iq)
        if self.mode != "wfm":
            iq = self.agc.process(iq)
        audio = self._demod(iq)
        if self.mode in ("fm", "nfm"):
            audio = self._deemph.process(audio)
        audio = _resample_to(audio, self.sr, self.audio_rate)

        return {
            "audio": audio.astype(np.float32).tolist(),
            "audio_rate": self.audio_rate,
            "mode": self.mode,
            "dc_offset_i": float(self.iq_corrector.dc_offset.real),
            "dc_offset_q": float(self.iq_corrector.dc_offset.imag),
        }


# ─────────────────────────────────────────────────────────────────────────
# ToolRegistry 注册
# ─────────────────────────────────────────────────────────────────────────
def register_gqrx_receiver_tools(registry) -> None:
    """把 AGC / IQ 校正 / 接收机管道注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _agc_process(args):
        """已知幅度阶跃 → AGC 压到目标电平。"""
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        try:
            arr = np.array(iq, dtype=complex)
            agc = GqrxAGC(sr, agc_on=True, use_hang=bool(args.get("use_hang", False)),
                          threshold_db=int(args.get("threshold_db", -100)),
                          decay_ms=int(args.get("decay_ms", 500)))
            out = agc.process(arr)
            before = float(np.sqrt(np.mean(np.abs(arr) ** 2)))
            after = float(np.sqrt(np.mean(np.abs(out) ** 2)))
            data = {"rms_before": before, "rms_after": after,
                    "out_samples": int(len(out)),
                    "source": "mbdsdr independent receiver AGC"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"AGC 失败: {e}")

    def _iq_correct(args):
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        try:
            arr = np.array(iq, dtype=complex)
            cor = IQCorrector(sr, dc_tau=1.0)
            out = cor.process(arr)
            data = {"mean_before": complex(arr.mean()).real,
                    "dc_offset_i": float(cor.dc_offset.real),
                    "dc_offset_q": float(cor.dc_offset.imag),
                    "mean_after_i": float(out.real.mean()),
                    "mean_after_q": float(out.imag.mean()),
                    "source": "mbdsdr independent IQ front-end correction"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"IQ校正失败: {e}")

    def _gqrx_create(args):
        """创建一个接收机实例并跑一段合成 IQ，返回音频摘要。"""
        sr = float(args.get("sample_rate", PREF_QUAD_RATE_NB))
        mode = str(args.get("mode", "nfm"))
        n = int(args.get("num_samples", 96_000))
        try:
            rx = GQRXReceiver(sample_rate=sr, mode=mode)
            t = np.arange(n) / sr
            if mode in ("fm", "nfm", "wfm"):
                fdev = FM_MAXDEV.get(mode, 5000.0)
                phase = 2 * np.pi * fdev * 0.5 * np.cumsum(np.sin(2 * np.pi * 1000 * t)) / sr
                iq = np.exp(1j * phase)
            else:
                iq = np.exp(1j * 2 * np.pi * 1000 * t) * 0.5
            r = rx.process(iq)
            audio = r.pop("audio")
            data = {**r, "audio_samples": len(audio),
                    "note": "独立实现的接收机管道实例已创建并跑通",
                    "source": "mbdsdr independent receiver pipeline"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"接收机创建失败: {e}")

    registry.register(
        name="agc_process",
        description=("独立实现的快攻慢放 AGC：输入一段复数 IQ，按攻击约 2ms/"
                     "释放约 50ms + 可选 hang，把输出 RMS 稳到目标电平(0.7)。"
                     "返回处理前后 RMS。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"},
                       "description": "交错复 IQ 或复数采样列表"},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
                "use_hang": {"type": "boolean", "default": False},
                "threshold_db": {"type": "integer", "default": -100},
                "decay_ms": {"type": "integer", "default": 500},
            },
            "required": ["iq"],
        },
        handler=_agc_process,
        category="sdr_dsp",
    )

    registry.register(
        name="iq_correct",
        description=("IQ 前端校正：一阶 IIR(tau=1s) 自适应估计并扣除直流偏移，"
                     "Gram-Schmidt 式在线校正 I/Q 正交不平衡。返回校正前后均值与 DC 估计。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
            },
            "required": ["iq"],
        },
        handler=_iq_correct,
        category="sdr_dsp",
    )

    registry.register(
        name="gqrx_receiver_create",
        description=("实例化接收机管道："
                     "IQ校正→信道带通(AM±5k/USB100-2800/LSB-2800~-100/CW±250/WFM±80k)"
                     "→AGC(仅窄带)→FM鉴频(maxdev NFM5k/WFM75k)+75μs去加重→重采样到48kHz。"
                     "mode=fm/nfm/wfm/am/usb/lsb/cw。"),
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(GQRXReceiver.SUPPORTED_MODES)},
                "sample_rate": {"type": "number", "default": PREF_QUAD_RATE_NB},
                "num_samples": {"type": "integer", "default": 96000},
            },
            "required": [],
        },
        handler=_gqrx_create,
        category="sdr_demod",
    )


if __name__ == "__main__":
    # 自测：已知幅度阶跃 → AGC 输出稳定（合成信号，非硬件）。
    sr = 96_000
    t = np.arange(sr) / sr
    sig = np.full(sr, 0.1, dtype=complex)
    sig[sr // 2:] = 0.9
    agc = GqrxAGC(sr, threshold_db=-100, decay_ms=500)
    out = agc.process(sig)
    print(f"AGC: 输入 RMS 前半={np.sqrt(np.mean(np.abs(sig[:sr//2])**2)):.3f} "
          f"后半={np.sqrt(np.mean(np.abs(sig[sr//2:])**2)):.3f}")
    print(f"     输出 RMS 前半={np.sqrt(np.mean(np.abs(out[:sr//2])**2)):.3f} "
          f"后半={np.sqrt(np.mean(np.abs(out[sr//2:])**2)):.3f}")
