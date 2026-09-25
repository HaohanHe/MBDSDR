"""
MBDSDR AI - Universal Radio Hacker (URH) 真实源码移植适配器
==========================================================
urh_adapter.py

把 URH (https://github.com/jopohl/urh) 的「采样 → 符号 → 位」判决核心从 Cython
忠实移植为纯 numpy，去除 PyQt 依赖。每处常量/算法标注 源文件:行号。

覆盖：
  * 调制（复 IQ 生成）：ASK / FSK / PSK / GFSK
      - signal_functions.pyx:81  __modulate()
      - Modulator.py:215         modulate()
  * 正交解调（IQ → 实值基带）：ASK 包络 / FSK 相位差分 / PSK Costas 环
      - signal_functions.pyx:333 afp_demod()
      - signal_functions.pyx:252 costa_demod()
  * 位/符号切片（实值基带 → 0/1）：多电平状态判决 + 脉冲长度聚合
      - signal_functions.pyx:392 grab_pulse_lens()
      - signal_functions.pyx:380 get_center_thresholds()
  * 同步字检测（比特流相关）
      - URH awre / ProtocolAnalyzer

红线：纯 numpy；不 import PyQt；不 import urh 包本身。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── URH 常量（来源 signal_functions.pyx）─────────────────────────────────
# signal_functions.pyx:31  NOISE_FSK_PSK = -4.0
# signal_functions.pyx:32  NOISE_ASK = 0.0
NOISE_FSK_PSK = -4.0
NOISE_ASK = 0.0

# Modulator.py:20  MODULATION_TYPES = ["ASK", "FSK", "PSK", "GFSK", "OQPSK"]
MODULATION_TYPES = ("ASK", "FSK", "PSK", "GFSK", "OQPSK")


# ═══════════════════════════════════════════════════════════════════════
# 调制器（bits → 复 IQ）—— 移植 signal_functions.pyx:81 __modulate
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class URHModulator:
    """URH 调制器。默认参数对齐 Modulator.py:29-48。

    carrier_freq_hz : Modulator.py:30  = 40e3
    carrier_amplitude : Modulator.py:31 = 1
    samples_per_symbol : Modulator.py:34 = 100
    gauss_bt : Modulator.py:43 = 0.5
    """

    modulation_type: str = "ASK"
    samples_per_symbol: int = 100
    sample_rate: float = 1_000_000.0
    carrier_freq_hz: float = 40_000.0
    carrier_amplitude: float = 1.0
    carrier_phase_deg: float = 0.0
    bits_per_symbol: int = 1
    #: 各符号对应的参数（ASK:幅度%; FSK:频率Hz; PSK:相位度）
    parameters: Optional[List[float]] = None
    gauss_bt: float = 0.5

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = self._default_parameters()

    # Modulator.py:257 get_default_parameters
    def _default_parameters(self) -> List[float]:
        order = 2 ** self.bits_per_symbol
        if self.modulation_type == "ASK":
            return list(np.linspace(0, 100, order).astype(float))
        if self.modulation_type == "FSK":
            return [(i + 1) * self.carrier_freq_hz / order for i in range(order)]
        if self.modulation_type == "PSK":
            step = 360.0 / order
            return list(np.arange(step / 2, 360, step) - 180)
        # GFSK 同 FSK
        return [(i + 1) * self.carrier_freq_hz / order for i in range(order)]

    # signal_functions.pyx:228 gauss_fir —— GFSK 高斯滤波器
    def _gauss_fir(self) -> np.ndarray:
        bt = self.gauss_bt
        sps = self.samples_per_symbol
        k = np.arange(-sps, sps + 1, dtype=np.float32)
        ts = sps / self.sample_rate
        # 来源 signal_functions.pyx:241-242
        h = (np.sqrt(2 * np.pi / np.log(2)) * bt / ts *
             np.exp(-((np.sqrt(2) * np.pi / np.sqrt(np.log(2)) * bt * k / sps) ** 2)))
        return h / h.sum()

    def modulate(self, bits: Sequence[int]) -> np.ndarray:
        """把 0/1 比特序列调制成复 IQ（返回 complex64）。

        移植 signal_functions.pyx:81 __modulate 的核心循环。
        """
        bits = np.asarray(bits, dtype=np.uint8)
        bps = self.bits_per_symbol
        order = 2 ** bps
        n_sym = len(bits) // bps
        if n_sym == 0:
            return np.zeros(0, dtype=np.complex64)

        sps = self.samples_per_symbol
        total = n_sym * sps
        t = np.arange(total, dtype=np.float64) / self.sample_rate
        out = np.zeros(total, dtype=np.complex128)

        params = np.asarray(self.parameters, dtype=np.float64)
        # 把每 bps 比特组索引成 symbol index
        sym_idx = np.zeros(n_sym, dtype=np.int64)
        for s in range(n_sym):
            v = 0
            for b in range(bps):
                v = (v << 1) | int(bits[s * bps + b])
            sym_idx[s] = v

        if self.modulation_type == "ASK":
            # signal_functions.pyx:147-150  a = parameters[index]
            amps = params[sym_idx] / 100.0 * self.carrier_amplitude
            # 每符号重复 sps 次
            amp_w = np.repeat(amps, sps)
            phase = 2 * np.pi * self.carrier_freq_hz * t + np.deg2rad(self.carrier_phase_deg)
            out = amp_w * np.exp(1j * phase)

        elif self.modulation_type == "FSK":
            # signal_functions.pyx:151-153  f = parameters[index]
            freqs = params[sym_idx]
            freq_w = np.repeat(freqs, sps)
            # 相位连续积分（防止频率跳变处相位尖刺）
            phase = np.cumsum(2 * np.pi * freq_w / self.sample_rate)
            out = self.carrier_amplitude * np.exp(1j * phase)

        elif self.modulation_type == "GFSK":
            # signal_functions.pyx:196 get_gauss_filtered_freqs_phases
            freqs = params[sym_idx]
            freq_w = np.repeat(freqs, sps).astype(np.float32)
            fir = self._gauss_fir()
            freq_w = np.convolve(freq_w, fir, mode="same")
            phase = np.cumsum(2 * np.pi * freq_w / self.sample_rate)
            out = self.carrier_amplitude * np.exp(1j * phase)

        elif self.modulation_type == "PSK":
            # signal_functions.pyx:155-156  phi = parameters[index] (度→弧度)
            phases = np.deg2rad(params[sym_idx])
            phase_w = np.repeat(phases, sps)
            carrier = 2 * np.pi * self.carrier_freq_hz * t + np.deg2rad(self.carrier_phase_deg)
            out = self.carrier_amplitude * np.exp(1j * (carrier + phase_w))
        else:
            raise ValueError(f"暂不支持的调制: {self.modulation_type}")

        return out.astype(np.complex64)


# ═══════════════════════════════════════════════════════════════════════
# 正交解调（IQ → 实值基带）—— 移植 signal_functions.pyx:333 afp_demod
# ═══════════════════════════════════════════════════════════════════════
def costa_demod(samples: np.ndarray, bandwidth: float = 0.1) -> np.ndarray:
    """Costas 环解调 BPSK/QPSK。移植 signal_functions.pyx:252。"""
    samples = np.asarray(samples, dtype=complex)
    n = len(samples)
    out = np.zeros(n, dtype=np.float32)
    if n < 2:
        return out
    damping = np.sqrt(2.0) / 2.0
    # signal_functions.pyx:253-254
    alpha = (4 * damping * bandwidth) / (1 + 2 * damping * bandwidth + bandwidth ** 2)
    beta = (4 * bandwidth ** 2) / (1 + 2 * damping * bandwidth + bandwidth ** 2)

    costa_freq = 0.0
    costa_phase = 1.5
    for i in range(1, n):
        c = samples[i]
        nco = np.cos(-costa_phase) + 1j * np.sin(-costa_phase)
        z = nco * c
        # BPSK (loop_order=2): error = imag * real
        err = float(z.imag * z.real)
        err = max(-1.0, min(1.0, err))
        costa_freq += beta * err
        costa_phase += costa_freq + alpha * err
        costa_phase = (costa_phase + np.pi) % (2 * np.pi) - np.pi
        costa_freq = max(-1.0, min(1.0, costa_freq))
        out[i] = z.real
    return out


def afp_demod(samples: np.ndarray, mod_type: str = "ASK",
              noise_mag: float = 0.0) -> np.ndarray:
    """正交幅度/频率/相位解调。移植 signal_functions.pyx:333。

    ASK → |c|（包络）; FSK → atan2(conj(prev)*cur)（相位差分）; PSK → Costas。
    """
    samples = np.asarray(samples, dtype=complex)
    n = len(samples)
    if n <= 2:
        return np.zeros(n, dtype=np.float32)
    out = np.zeros(n, dtype=np.float32)
    noise_sqrd = noise_mag * noise_mag
    max_mag = np.sqrt(2.0)

    if mod_type == "PSK":
        return costa_demod(samples)

    out[0] = NOISE_ASK if mod_type == "ASK" else NOISE_FSK_PSK
    for i in range(1, n):
        mag2 = float(samples[i].real ** 2 + samples[i].imag ** 2)
        if noise_sqrd > 0 and mag2 <= noise_sqrd:
            out[i] = NOISE_ASK if mod_type == "ASK" else NOISE_FSK_PSK
            continue
        if mod_type == "ASK":
            # signal_functions.pyx:372
            out[i] = np.sqrt(mag2) / max_mag
        elif mod_type in ("FSK", "GFSK"):
            # signal_functions.pyx:375-376  atan2(conj(prev)*cur)
            prev = samples[i - 1]
            z = np.conj(prev) * samples[i]
            out[i] = np.angle(z)
        else:
            out[i] = np.sqrt(mag2) / max_mag
    return out


# ═══════════════════════════════════════════════════════════════════════
# 位切片 —— 移植 signal_functions.pyx:380 get_center_thresholds + :392 grab_pulse_lens
# ═══════════════════════════════════════════════════════════════════════
def get_center_thresholds(center: float, spacing: float, order: int) -> np.ndarray:
    """计算多电平判决阈值（相邻符号中心中点）。signal_functions.pyx:380。"""
    if order <= 1:
        return np.zeros(0)
    n = order // 2
    res = np.empty(order - 1, dtype=np.float64)
    for i in range(n):
        res[i] = center - (n - (i + 1)) * spacing
    for i in range(n, order - 1):
        res[i] = center + (i + 1 - n) * spacing
    return res


def grab_pulse_lens(baseband: np.ndarray, center: float, tolerance: int,
                    mod_type: str, samples_per_symbol: int,
                    bits_per_symbol: int = 1, center_spacing: float = 0.1
                    ) -> np.ndarray:
    """实值基带 → 脉冲段数组 [state, length]。移植 signal_functions.pyx:392。

    简化（无 PyQt、向量化判决）：把每个样本按最近阈值归类成 state，
    再把连续同 state 段聚合成 [state, length]。
    """
    order = 2 ** bits_per_symbol
    thresholds = get_center_thresholds(center, center_spacing, order)
    s = np.asarray(baseband, dtype=np.float64)
    NOISE = NOISE_ASK if mod_type == "ASK" else NOISE_FSK_PSK

    # 每样本归类到 state 0..order-1
    state = np.full(len(s), order - 1, dtype=np.int64)
    for k in range(order - 1):
        state[s <= thresholds[k]] = k
    # 噪声样本标记为 -1（PAUSE_STATE = -1，signal_functions.pyx:28）
    state[s == NOISE] = -1

    # 聚合连续同 state
    if len(state) == 0:
        return np.zeros((0, 2), dtype=np.int64)
    changes = np.where(np.diff(state) != 0)[0] + 1
    bounds = np.concatenate([[0], changes, [len(state)]])
    pulses = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        pulses.append((int(state[a]), int(b - a)))
    return np.array(pulses, dtype=np.int64)


def slice_bits_from_pulses(pulses: np.ndarray, samples_per_symbol: int) -> np.ndarray:
    """把 [state, length] 脉冲按 samples_per_symbol 量化成比特。

    每个脉冲长度 / sps = 该 state 持续多少个符号；state 本身即符号索引。
    """
    bits: List[int] = []
    for state, length in pulses:
        if state < 0:  # pause
            continue
        n_sym = max(1, int(round(length / max(1, samples_per_symbol))))
        bits.extend([int(state)] * n_sym)
    return np.asarray(bits, dtype=np.uint8)


def sync_word_correlate(bits: np.ndarray, sync_word: Sequence[int],
                        tolerance: int = 0) -> List[int]:
    """比特流同步字相关（汉明距离）。返回匹配起始位置。"""
    sw = np.asarray(sync_word, dtype=np.int8)
    bits = np.asarray(bits, dtype=np.int8)
    if len(sw) == 0 or len(bits) < len(sw):
        return []
    hits = []
    for i in range(len(bits) - len(sw) + 1):
        if int(np.count_nonzero(bits[i:i + len(sw)] != sw)) <= tolerance:
            hits.append(i)
    return hits


# ═══════════════════════════════════════════════════════════════════════
# 高层一键解码：IQ → 比特
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class URHDecoder:
    """URH 风格一键解码：IQ → afp_demod → pulses → bits。"""

    modulation_type: str = "ASK"
    samples_per_symbol: int = 100
    bits_per_symbol: int = 1
    noise_mag: float = 0.0

    def decode(self, iq: np.ndarray) -> np.ndarray:
        """IQ → afp_demod → 中点采样判决 → 比特。

        当 samples_per_symbol 已知时，URH 在每个符号中点采样判决
        （对应 Signal.py 里 digitize 的中点采样），比脉冲长度聚合更稳健。
        grab_pulse_lens 仍保留作为忠实移植的脉冲分析工具。
        """
        bb = afp_demod(iq, mod_type=self.modulation_type, noise_mag=self.noise_mag)
        sps = self.samples_per_symbol
        if len(bb) < sps:
            return np.zeros(0, dtype=np.uint8)
        n_sym = len(bb) // sps

        # 判决阈值：把样本按中位数劈成高低两半，再取两半均值的中点。
        # 这等价于 URH 的 center=符号电平中点（get_center_thresholds 的中点），
        # 比直接用中位数稳健（位数不均/前导 0 不影响）。
        med = float(np.median(bb))
        low = bb[bb <= med]
        high = bb[bb > med]
        if len(low) > 0 and len(high) > 0:
            threshold = float((np.mean(low) + np.mean(high)) / 2.0)
        else:
            threshold = med

        bits = np.zeros(n_sym, dtype=np.uint8)
        lo_frac, hi_frac = 0.4, 0.6   # 符号中心 ±10% 窗，抗边沿
        for k in range(n_sym):
            lo = k * sps + int(sps * lo_frac)
            hi = k * sps + int(sps * hi_frac)
            level = float(np.mean(bb[lo:hi])) if hi > lo else float(bb[k * sps])
            bits[k] = 1 if level > threshold else 0
        return bits


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_urh_tools(registry) -> None:
    """把 URH 调制/解调/符号判决工具注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _urh_modulate(args):
        """已知比特流 → URH 调制复 IQ。"""
        bits = args.get("bits")
        if not isinstance(bits, list):
            return ToolResult(False, "bits 必须是 0/1 列表")
        try:
            mod = URHModulator(
                modulation_type=str(args.get("modulation", "ASK")),
                samples_per_symbol=int(args.get("samples_per_symbol", 100)),
                sample_rate=float(args.get("sample_rate", 1e6)),
                carrier_freq_hz=float(args.get("carrier_freq_hz", 40e3)),
                bits_per_symbol=int(args.get("bits_per_symbol", 1)),
            )
            iq = mod.modulate(bits)
            data = {"num_samples": int(len(iq)),
                    "complex_iq": [float(iq.real[0]), float(iq.imag[0])],
                    "modulation": mod.modulation_type,
                    "source": "urh signal_functions.pyx:81 __modulate"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"URH 调制失败: {e}")

    def _urh_demodulate(args):
        """复 IQ → URH 正交解调实值基带（位层前一步）。"""
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表（交错 [i0,q0,i1,q1...]）")
        try:
            arr = np.asarray(iq, dtype=float)
            if arr.size % 2 == 0:
                c = arr[0::2] + 1j * arr[1::2]
            else:
                c = arr.astype(complex)
            bb = afp_demod(c, mod_type=str(args.get("modulation", "ASK")))
            data = {"num_samples": int(len(bb)),
                    "first_values": [float(x) for x in bb[:8]],
                    "source": "urh signal_functions.pyx:333 afp_demod"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"URH 解调失败: {e}")

    def _urh_detect_sync(args):
        """比特流中检测同步字位置。"""
        bits = args.get("bits")
        sw = args.get("sync_word")
        if not isinstance(bits, list) or not isinstance(sw, list):
            return ToolResult(False, "bits 和 sync_word 都必须是 0/1 列表")
        try:
            hits = sync_word_correlate(np.asarray(bits), sw,
                                       tolerance=int(args.get("tolerance", 0)))
            data = {"positions": hits, "num_hits": len(hits),
                    "source": "urh awre / ProtocolAnalyzer.py"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"同步字检测失败: {e}")

    registry.register(
        name="urh_modulate",
        description=("URH 真实调制器移植：输入 0/1 比特流，按 ASK/FSK/PSK/GFSK "
                     "生成复 IQ。默认 samples_per_symbol=100。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"}},
                "modulation": {"type": "string", "enum": list(MODULATION_TYPES), "default": "ASK"},
                "samples_per_symbol": {"type": "integer", "default": 100},
                "sample_rate": {"type": "number", "default": 1e6},
                "carrier_freq_hz": {"type": "number", "default": 40e3},
                "bits_per_symbol": {"type": "integer", "default": 1},
            },
            "required": ["bits"],
        },
        handler=_urh_modulate,
        category="sdr_protocol",
    )
    registry.register(
        name="urh_demodulate",
        description=("URH afp_demod 移植：复 IQ → 实值基带（ASK 包络 / FSK 相位差分 / "
                     "PSK Costas 环）。这是位层判决前的正交解调。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "modulation": {"type": "string", "enum": list(MODULATION_TYPES), "default": "ASK"},
            },
            "required": ["iq"],
        },
        handler=_urh_demodulate,
        category="sdr_protocol",
    )
    registry.register(
        name="urh_sync_detect",
        description=("URH 同步字相关：在 0/1 比特流里按汉明距离找同步字位置。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"}},
                "sync_word": {"type": "array", "items": {"type": "integer"}},
                "tolerance": {"type": "integer", "default": 0},
            },
            "required": ["bits", "sync_word"],
        },
        handler=_urh_detect_sync,
        category="sdr_protocol",
    )
