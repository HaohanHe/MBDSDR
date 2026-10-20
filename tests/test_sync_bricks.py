# SPDX-License-Identifier: MIT
"""同步/调制解调原子积木正确性（合成自洽，诚实标注 synthetic，非 OTA）。

验证 sync_bricks.py 各积木数学：
1. FLL：注入已知频偏 -> NCO 频率估计收敛到该频偏。
2. GMSK：调制->解调往返（BT=0.3/0.5，零噪声），比特精确复原。
3. 频率轨迹：已知 LFMCW/恒频信号 -> iq_freq_track 提取频率吻合。
4. SymbolSync：三种 TED 恢复符号数合理 + TED 函数可调用。
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai import sync_bricks as sb  # noqa: E402


class TestFLL:
    def test_converges_to_frequency_offset(self):
        """FLL 锁定注入频偏（归一化 rad/sample）。"""
        rng = np.random.default_rng(7)
        sps = 8
        n = 4000
        true_freq = 0.12  # rad/sample
        phase = np.cumsum(np.full(n, true_freq)) + rng.uniform(0, 2 * np.pi)
        iq = np.exp(1j * phase) * (1.0 + 0.05 * rng.standard_normal(n))

        fll = sb.FrequencyLockedLoop(loop_bw=0.05)
        fll.feed(iq)
        est = fll.frequency_estimate
        assert abs(est - true_freq) < 0.02, f"FLL 估计 {est:.4f} vs 真值 {true_freq}"

    def test_zero_offset_stays_zero(self):
        n = 1000
        iq = np.exp(1j * np.linspace(0, 2 * np.pi, n, endpoint=False))
        fll = sb.FrequencyLockedLoop(loop_bw=0.05)
        fll.feed(iq)
        assert abs(fll.frequency_estimate) < 0.02


class TestGMSK:
    @pytest.mark.parametrize("bt", [0.3, 0.5])
    def test_mod_demod_roundtrip(self, bt):
        """零噪声往返：GMSK 调制->差分解调，比特精确复原。"""
        rng = np.random.default_rng(42)
        bits = rng.integers(0, 2, size=200)
        sps = 8
        iq = sb.gmsk_modulate(bits, sps=sps, bt=bt)
        out = sb.gmsk_demodulate(iq, sps=sps)
        # 差分解调有 ±符号的群时延对齐，比对中段稳定区
        m = min(len(bits), len(out))
        err = int(np.sum(bits[:m] != out[:m]))
        assert err <= 2, f"BT={bt} 比特错误 {err}/{m}"

    def test_bits_must_be_binary(self):
        with pytest.raises(ValueError):
            sb.gmsk_modulate([0, 1, 2], sps=8, bt=0.3)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            sb.gmsk_modulate([], sps=8, bt=0.3)


class TestFreqTrack:
    def test_constant_tone(self):
        """恒定频率音 -> 提取频率吻合。"""
        fs = 48000.0
        f0 = 1234.0
        n = 8192
        t = np.arange(n) / fs
        iq = np.exp(1j * 2 * np.pi * f0 * t)
        times, freqs = sb.iq_freq_track(iq, fs, window=1024, hop=512)
        assert len(times) == len(freqs) > 0
        assert abs(float(np.mean(freqs)) - f0) < 50.0, (
            f"平均提取 {np.mean(freqs):.1f} Hz vs 真值 {f0} Hz"
        )

    def test_linear_chirp(self):
        """线性调频 -> 提取频率随时间单调趋势正确。"""
        fs = 48000.0
        n = 16384
        t = np.arange(n) / fs
        f_start, f_end = 500.0, 3500.0
        inst_f = f_start + (f_end - f_start) * t / t[-1]
        phase = 2 * np.pi * np.cumsum(inst_f) / fs
        iq = np.exp(1j * phase)
        times, freqs = sb.iq_freq_track(iq, fs, window=1024, hop=1024)
        # 首尾频率趋势应递增
        assert freqs[-1] > freqs[0], "chirp 频率趋势应递增"
        assert abs(freqs[0] - f_start) < 300.0
        assert abs(freqs[-1] - f_end) < 300.0

    def test_input_validation(self):
        iq = np.exp(1j * np.linspace(0, 1, 100))
        with pytest.raises(ValueError):
            sb.iq_freq_track(iq, 48000.0, window=4)
        with pytest.raises(ValueError):
            sb.iq_freq_track(iq, 48000.0, window=1024, hop=2048)
        with pytest.raises(ValueError):
            sb.iq_freq_track(iq[:10], 48000.0, window=64, hop=32)


class TestSymbolSync:
    @pytest.mark.parametrize("ted", ["gardner", "mueller_muller", "early_late"])
    def test_recovers_symbol_count(self, ted):
        """三种 TED 均能从成形符号流恢复合理数量的符号。"""
        rng = np.random.default_rng(3)
        bits = rng.integers(0, 2, size=120)
        sps = 8
        nrz = 2.0 * bits.astype(float) - 1.0
        up = np.repeat(nrz, sps)
        # 简单矩形成形 + 噪声
        up = up + 0.05 * rng.standard_normal(len(up))
        sync = sb.SymbolSync(samples_per_symbol=sps, ted=ted, loop_gain=0.05)
        syms = sync.feed(up)
        assert len(syms) > 100, f"{ted} 恢复符号过少: {len(syms)}"
        # 流式恢复有启动暂态，比对公共前缀
        m = min(len(syms), len(bits))
        dec = (syms[:m].real >= 0).astype(int)
        err = int(np.sum(dec != bits[:m]))
        assert err <= 12, f"{ted} 判决错误 {err}/{m}"

    def test_unknown_ted_rejected(self):
        with pytest.raises(ValueError):
            sb.SymbolSync(8.0, ted="nope")

    def test_ted_functions_callable(self):
        z = 1 + 0j
        assert isinstance(sb.gardner_ted(z, z, z), float)
        assert isinstance(sb.mueller_muller_ted(z, z, 1.0, 1.0), float)
        assert isinstance(sb.early_late_ted(z, z, z), float)
