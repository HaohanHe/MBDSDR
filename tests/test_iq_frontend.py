#!/usr/bin/env python3
"""
IQ 前端校正链测试
=================

验证 mbdsdr_ai.iq_frontend 的三类校正真生效（数值降多少 dB）：
  T1  DC 偏移去除：带 DC 的复数信号 → 中心 DC 分量显著下降
  T2  I/Q 不平衡校正：IQ 不平衡信号 → 镜像功率下降（IRR 改善）
  T3  抗混叠抽取：含超 Nyquist 高频信号 → 抽取后折叠分量被压制
  T4  IQFrontend 链：三环节串联 + 开关切换
  T5  流式多块：跨块状态连续，不出现瞬态断档
  T6  边界：空输入 / factor=1 / 实数输入 / 重置

所有断言用具体 dB 数说话，不接受"看起来差不多"。
"""

import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.iq_frontend import (
    DCBlocker, IQBalanceCorrector, AntiAliasDecimator, IQFrontend, front_end,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _bin_power_db(x: np.ndarray, freq_hz: float, fs: float) -> float:
    """返回信号 x 在 freq_hz 处的 FFT bin 功率（dB，相对 1.0）。"""
    n = len(x)
    window = np.hanning(n)
    X = np.fft.fft(x * window)
    freqs = np.fft.fftfreq(n, 1.0 / fs)
    idx = int(np.argmin(np.abs(freqs - freq_hz)))
    power = np.abs(X[idx]) ** 2 / n
    return 10.0 * np.log10(power + 1e-18)


def _bin_power_abs_db(x: np.ndarray, freq_hz: float, fs: float) -> float:
    """返回 ±freq_hz 处较大的 FFT bin 功率 dB。

    用于混叠检测：抽取后高频分量的镜像可能落在 +f 或 -f，
    取两者较大值避免符号误判。
    """
    return max(_bin_power_db(x, freq_hz, fs),
               _bin_power_db(x, -freq_hz, fs))


def _center_dc_power_db(x: np.ndarray) -> float:
    """返回频谱中心（DC / 0 Hz）bin 的功率 dB。"""
    n = len(x)
    window = np.hanning(n)
    X = np.fft.fft(x * window)
    # fftshift 后中心 bin
    dc_power = np.abs(X[0]) ** 2 / n
    return 10.0 * np.log10(dc_power + 1e-18)


def _make_tone(freq_hz: float, fs: float, n: int,
               amplitude: float = 1.0) -> np.ndarray:
    """生成复数音调信号。"""
    t = np.arange(n) / fs
    return amplitude * np.exp(1j * 2.0 * np.pi * freq_hz * t)


def _make_iq_imbalanced_signal(
    freq_hz: float, fs: float, n: int,
    gain_q: float = 0.8, phase_err_deg: float = 15.0,
    amplitude: float = 1.0,
) -> np.ndarray:
    """构造带 I/Q 不平衡的复数信号。

    理想 IQ: I = cos(ωt), Q = sin(ωt)
    不平衡后: I' = I, Q' = gain_q * (Q*cos(φ) - I*sin(φ))
    这会在 -freq_hz 处产生镜像。
    """
    t = np.arange(n) / fs
    i_ideal = amplitude * np.cos(2.0 * np.pi * freq_hz * t)
    q_ideal = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    phi = np.radians(phase_err_deg)
    q_imbal = gain_q * (q_ideal * np.cos(phi) - i_ideal * np.sin(phi))
    return (i_ideal + 1j * q_imbal).astype(np.complex64)


# ═══════════════════════════════════════════════════════════════════════
# T1: DC 偏移去除
# ═══════════════════════════════════════════════════════════════════════

class TestDCRemoval:
    """DC 偏移去除：复数 IQ 的 DC spike 真去除。"""

    FS = 2_000_000.0
    N = 200_000

    def test_dc_spike_removed(self):
        """带大幅 DC 偏移的信号 → 过 DC blocker → 中心 DC 分量显著下降。"""
        sig = _make_tone(200_000, self.FS, self.N, amplitude=0.5)
        dc_offset = 0.3 + 0.2j  # 大幅直流
        sig_dc = sig + dc_offset

        blocker = DCBlocker(r=0.998)
        out = blocker.process(sig_dc)

        # 跳过初始暂态（前 2000 点 IIR 尚未收敛）
        out_steady = out[2000:]

        dc_before = _center_dc_power_db(sig_dc)
        dc_after = _center_dc_power_db(out_steady)
        reduction = dc_before - dc_after

        print(f"\n[T1-DC] 校正前中心 DC 功率: {dc_before:.1f} dB")
        print(f"[T1-DC] 校正后中心 DC 功率: {dc_after:.1f} dB")
        print(f"[T1-DC] DC 抑制: {reduction:.1f} dB")

        # DC 抑制必须 > 40 dB（中心尖峰基本消失）
        assert reduction > 40.0, (
            f"DC 抑制不足: {reduction:.1f} dB < 40 dB"
        )

    def test_dc_removal_preserves_signal(self):
        """DC 去除不应显著衰减通带内的信号音调。"""
        sig = _make_tone(200_000, self.FS, self.N, amplitude=0.5)
        sig_dc = sig + (0.3 + 0.2j)

        blocker = DCBlocker(r=0.998)
        out = blocker.process(sig_dc)
        out_steady = out[2000:]

        sig_power_before = _bin_power_db(sig, 200_000, self.FS)
        sig_power_after = _bin_power_db(out_steady, 200_000, self.FS)
        loss = sig_power_before - sig_power_after

        print(f"\n[T1-signal] 200kHz 音调校正前: {sig_power_before:.1f} dB")
        print(f"[T1-signal] 200kHz 音调校正后: {sig_power_after:.1f} dB")
        print(f"[T1-signal] 信号损失: {loss:.1f} dB")

        # 200 kHz 远高于 DC blocker 截止（~几百 Hz），损失应 < 1 dB
        assert loss < 1.0, f"DC blocker 过度衰减信号: 损失 {loss:.1f} dB"

    def test_dc_blocker_streaming_continuity(self):
        """逐块处理与整段处理结果一致（跨块状态连续）。"""
        sig = _make_tone(100_000, self.FS, 50_000, amplitude=0.5)
        sig_dc = sig + (0.25 + 0.15j)

        # 整段处理
        blocker_whole = DCBlocker(r=0.998)
        out_whole = blocker_whole.process(sig_dc)

        # 逐块处理（块大小 4096）
        blocker_stream = DCBlocker(r=0.998)
        chunks = []
        block = 4096
        for i in range(0, len(sig_dc), block):
            chunks.append(blocker_stream.process(sig_dc[i:i + block]))
        out_stream = np.concatenate(chunks)

        # 跳过初始暂态后比较
        diff = np.max(np.abs(out_whole[3000:] - out_stream[3000:]))
        print(f"\n[T1-stream] 整段 vs 逐块最大差异: {diff:.2e}")
        assert diff < 1e-4, f"流式处理不连续: 最大差异 {diff:.2e}"

    def test_dc_blocker_reset(self):
        """reset() 后状态清空，处理新信号无旧直流残留。"""
        blocker = DCBlocker(r=0.998)
        sig1 = _make_tone(50_000, self.FS, 10_000) + (0.5 + 0.5j)
        blocker.process(sig1)
        blocker.reset()
        # reset 后处理零信号应输出接近零
        zeros = np.zeros(1000, dtype=np.complex64)
        out = blocker.process(zeros)
        assert np.max(np.abs(out[100:])) < 1e-6, "reset 后仍有残留输出"


# ═══════════════════════════════════════════════════════════════════════
# T2: I/Q 不平衡校正
# ═══════════════════════════════════════════════════════════════════════

class TestIQBalance:
    """I/Q 不平衡校正：镜像抑制比（IRR）显著改善。"""

    FS = 2_000_000.0
    N = 200_000
    TONE_FREQ = 100_000.0  # +100 kHz 信号，镜像在 -100 kHz

    def test_iq_mirror_suppressed(self):
        """IQ 不平衡信号 → 校正后镜像功率显著下降（IRR 改善 > 20 dB）。"""
        sig = _make_iq_imbalanced_signal(
            self.TONE_FREQ, self.FS, self.N,
            gain_q=0.8, phase_err_deg=15.0, amplitude=1.0,
        )

        corrector = IQBalanceCorrector(
            adapt_interval_blocks=1, min_samples=4096)
        out = corrector.process(sig)

        # 信号功率（+100 kHz）和镜像功率（-100 kHz）
        sig_before = _bin_power_db(sig, self.TONE_FREQ, self.FS)
        img_before = _bin_power_db(sig, -self.TONE_FREQ, self.FS)
        irr_before = sig_before - img_before

        sig_after = _bin_power_db(out, self.TONE_FREQ, self.FS)
        img_after = _bin_power_db(out, -self.TONE_FREQ, self.FS)
        irr_after = sig_after - img_after

        print(f"\n[T2-IQ] 校正前: 信号={sig_before:.1f}dB, 镜像={img_before:.1f}dB, "
              f"IRR={irr_before:.1f}dB")
        print(f"[T2-IQ] 校正后: 信号={sig_after:.1f}dB, 镜像={img_after:.1f}dB, "
              f"IRR={irr_after:.1f}dB")
        print(f"[T2-IQ] IRR 改善: {irr_after - irr_before:.1f} dB")

        # IRR 改善必须 > 20 dB
        assert irr_after - irr_before > 20.0, (
            f"IRR 改善不足: {irr_after - irr_before:.1f} dB < 20 dB"
        )
        # 校正后 IRR 应 > 35 dB（镜像基本被压制）
        assert irr_after > 35.0, (
            f"校正后 IRR 不足: {irr_after:.1f} dB < 35 dB"
        )

    def test_iq_corrector_diagnostics(self):
        """诊断信息应报告与注入一致的增益误差和相位误差。"""
        sig = _make_iq_imbalanced_signal(
            self.TONE_FREQ, self.FS, self.N,
            gain_q=0.8, phase_err_deg=15.0, amplitude=1.0,
        )
        corrector = IQBalanceCorrector(min_samples=4096)
        corrector.process(sig)

        diag = corrector.diagnostics
        print(f"\n[T2-diag] 估计增益误差: {diag['iq_gain_error']:.3f} (注入 0.8)")
        print(f"[T2-diag] 估计相位误差: {diag['iq_phase_error_deg']:.1f}° (注入 15°)")
        print(f"[T2-diag] 拟合样本数: {diag['samples_used']}")

        assert diag["fitted"] is True
        assert diag["samples_used"] >= 4096
        # 增益误差估计应接近 0.8（白化后等功率，诊断反映原始不平衡）
        assert 0.7 < diag["iq_gain_error"] < 0.95, (
            f"增益误差估计偏离: {diag['iq_gain_error']:.3f}"
        )
        # 相位误差估计幅度应接近 15°（符号取决于约定，取绝对值）
        assert 10.0 < abs(diag["iq_phase_error_deg"]) < 20.0, (
            f"相位误差估计偏离: {diag['iq_phase_error_deg']:.1f}°"
        )

    def test_iq_corrector_no_balance_needed(self):
        """理想平衡信号 → 校正后信号不变形（白化矩阵接近单位阵）。"""
        sig = _make_tone(self.TONE_FREQ, self.FS, self.N, amplitude=1.0)
        corrector = IQBalanceCorrector(min_samples=4096)
        out = corrector.process(sig)

        sig_before = _bin_power_db(sig, self.TONE_FREQ, self.FS)
        sig_after = _bin_power_db(out, self.TONE_FREQ, self.FS)
        print(f"\n[T2-ideal] 理想信号校正前: {sig_before:.1f} dB")
        print(f"[T2-ideal] 理想信号校正后: {sig_after:.1f} dB")
        # 理想信号白化后等功率归一化（幅度 ×1/√var ≈ +3dB），变化应 < 4 dB
        assert abs(sig_before - sig_after) < 4.0, (
            f"理想信号被过度校正: 变化 {abs(sig_before - sig_after):.1f} dB"
        )

    def test_iq_corrector_adaptive(self):
        """自适应重估：切换不平衡参数后，校正器应跟踪新参数。"""
        # 第一段：gain_q=0.8, phase=15°
        sig1 = _make_iq_imbalanced_signal(
            self.TONE_FREQ, self.FS, 50_000,
            gain_q=0.8, phase_err_deg=15.0, amplitude=1.0,
        )
        # 第二段：gain_q=0.6, phase=25°（更严重不平衡）
        sig2 = _make_iq_imbalanced_signal(
            self.TONE_FREQ, self.FS, 50_000,
            gain_q=0.6, phase_err_deg=25.0, amplitude=1.0,
        )

        corrector = IQBalanceCorrector(
            adapt_interval_blocks=2, min_samples=4096)
        corrector.process(sig1)
        # 重置累积以模拟新环境
        corrector.reset()
        out2 = corrector.process(sig2)

        diag = corrector.diagnostics
        print(f"\n[T2-adapt] 第二段估计增益: {diag['iq_gain_error']:.3f} (注入 0.6)")
        print(f"[T2-adapt] 第二段估计相位: {diag['iq_phase_error_deg']:.1f}° (注入 25°)")

        assert diag["fitted"] is True
        assert 0.5 < diag["iq_gain_error"] < 0.75
        assert 20.0 < abs(diag["iq_phase_error_deg"]) < 30.0


# ═══════════════════════════════════════════════════════════════════════
# T3: 抗混叠抽取
# ═══════════════════════════════════════════════════════════════════════

class TestAntiAliasDecimation:
    """抗混叠抽取：抽取前真有低通，高频折叠分量被压制。"""

    FS = 2_000_000.0
    N = 200_000

    def test_anti_alias_suppresses_alias(self):
        """含超输出 Nyquist 高频的信号 → 4x 抽取后折叠分量被压制 > 30 dB。"""
        D = 4
        fs_out = self.FS / D  # 500 kHz, 输出 Nyquist = 250 kHz

        # 通带内 100 kHz + 高频 450 kHz（> 输出 Nyquist 250 kHz）
        # 450 kHz 抽取后会折叠到 |450k - 500k| = 50 kHz
        sig = (_make_tone(100_000, self.FS, self.N, amplitude=0.5)
               + _make_tone(450_000, self.FS, self.N, amplitude=0.3))

        decimator = AntiAliasDecimator(factor=D)
        out = decimator.process(sig)

        # 输出中 100 kHz 分量（通带）和 50 kHz 折叠分量（±50k 取较大）
        tone_100k = _bin_power_db(out, 100_000, fs_out)
        alias_50k = _bin_power_abs_db(out, 50_000, fs_out)
        suppression = tone_100k - alias_50k

        print(f"\n[T3-alias] 4x 抽取后 100kHz 分量: {tone_100k:.1f} dB")
        print(f"[T3-alias] 4x 抽取后折叠 50kHz 分量: {alias_50k:.1f} dB")
        print(f"[T3-alias] 抗混叠抑制: {suppression:.1f} dB")

        # 抗混叠抑制必须 > 30 dB
        assert suppression > 30.0, (
            f"抗混叠抑制不足: {suppression:.1f} dB < 30 dB"
        )

    def test_decimation_output_length(self):
        """抽取后长度 = ceil(n / factor)。"""
        sig = _make_tone(100_000, self.FS, 100_000, amplitude=0.5)
        for D in [2, 4, 8]:
            decimator = AntiAliasDecimator(factor=D)
            out = decimator.process(sig)
            expected = (len(sig) + D - 1) // D
            assert len(out) == expected, (
                f"D={D}: 期望 {expected}, 实际 {len(out)}"
            )

    def test_decimation_factor_one_passthrough(self):
        """factor=1 时直通，输出与输入完全一致。"""
        sig = _make_tone(100_000, self.FS, 50_000, amplitude=0.5)
        decimator = AntiAliasDecimator(factor=1)
        out = decimator.process(sig)
        assert np.array_equal(out, sig), "factor=1 应直通"

    def test_decimation_preserves_passband(self):
        """通带内信号抽取后频率和幅度基本保留。"""
        D = 4
        fs_out = self.FS / D
        sig = _make_tone(100_000, self.FS, self.N, amplitude=0.5)
        decimator = AntiAliasDecimator(factor=D)
        out = decimator.process(sig)

        # 找输出频谱峰值频率
        n = len(out)
        X = np.abs(np.fft.fft(out * np.hanning(n)))
        freqs = np.fft.fftfreq(n, 1.0 / fs_out)
        peak_idx = np.argmax(X[:n // 2])
        peak_freq = abs(freqs[peak_idx])

        print(f"\n[T3-passband] 抽取后峰值频率: {peak_freq:.1f} Hz (期望 100000)")
        assert abs(peak_freq - 100_000) < 5_000, (
            f"抽取后频率偏移: {peak_freq:.1f} Hz"
        )

    def test_bare_decimation_aliases(self):
        """对照实验：裸抽取（无低通）确实会产生混叠，证明抗混叠滤波必要。"""
        D = 4
        fs_out = self.FS / D
        sig = (_make_tone(100_000, self.FS, self.N, amplitude=0.5)
               + _make_tone(450_000, self.FS, self.N, amplitude=0.3))

        # 裸抽取
        bare = sig[::D]
        bare_alias = _bin_power_abs_db(bare, 50_000, fs_out)
        bare_tone = _bin_power_db(bare, 100_000, fs_out)
        bare_suppression = bare_tone - bare_alias

        # 抗混叠抽取
        decimator = AntiAliasDecimator(factor=D)
        filtered = decimator.process(sig)
        filt_alias = _bin_power_abs_db(filtered, 50_000, fs_out)
        filt_tone = _bin_power_db(filtered, 100_000, fs_out)
        filt_suppression = filt_tone - filt_alias

        print(f"\n[T3-compare] 裸抽取: 信号={bare_tone:.1f}dB, 折叠={bare_alias:.1f}dB, "
              f"抑制={bare_suppression:.1f}dB")
        print(f"[T3-compare] 抗混叠: 信号={filt_tone:.1f}dB, 折叠={filt_alias:.1f}dB, "
              f"抑制={filt_suppression:.1f}dB")

        # 裸抽取的折叠分量应明显高于抗混叠抽取
        assert filt_suppression > bare_suppression + 20.0, (
            "抗混叠抽取未显著优于裸抽取"
        )

    def test_decimation_cutoff_not_2x_error(self):
        """验证截止频率归一化正确（不是 0.5/D 的 2 倍错误）。

        在同一输出频谱内比较通带(150 kHz)与阻带(300 kHz)功率比：
        - 正确截止（~225 kHz）：通带内 150 kHz 远强于阻带 300 kHz，差值 > 30 dB
        - 2 倍错误（截止 ~112 kHz）：150 kHz 已落入阻带，与 300 kHz 差值 < 10 dB
        """
        D = 4
        fs_out = self.FS / D
        # 同时含通带 150 kHz 和阻带 300 kHz 的信号
        sig = (_make_tone(150_000, self.FS, self.N, amplitude=0.5)
               + _make_tone(300_000, self.FS, self.N, amplitude=0.5))

        decimator = AntiAliasDecimator(factor=D)
        out = decimator.process(sig)

        passband_power = _bin_power_db(out, 150_000, fs_out)
        stopband_power = _bin_power_abs_db(out, 300_000, fs_out)
        rejection = passband_power - stopband_power

        print(f"\n[T3-cutoff] 通带 150kHz: {passband_power:.1f} dB, "
              f"阻带 300kHz: {stopband_power:.1f} dB, "
              f"阻带抑制: {rejection:.1f} dB")
        # 正确截止时阻带抑制 > 30 dB；2 倍错误时 150k 也被衰减，抑制 < 10 dB
        assert rejection > 30.0, (
            f"阻带抑制不足 {rejection:.1f}dB — 可能是截止频率 2 倍错误"
        )


# ═══════════════════════════════════════════════════════════════════════
# T4: IQFrontend 链 + 开关
# ═══════════════════════════════════════════════════════════════════════

class TestIQFrontendChain:
    """IQFrontend 完整链：DC → IQ 平衡 → 抽取，可独立开关。"""

    FS = 2_000_000.0
    N = 200_000

    def test_full_chain_corrects_all(self):
        """同时含 DC + IQ 不平衡 + 高频的信号 → 全链校正后全部改善。"""
        t = np.arange(self.N) / self.FS
        # 基础信号
        base = 0.5 * np.exp(1j * 2 * np.pi * 100_000 * t)
        # IQ 不平衡
        i_raw = base.real
        q_raw = base.imag
        phi = np.radians(12.0)
        q_imbal = 0.85 * (q_raw * np.cos(phi) - i_raw * np.sin(phi))
        sig = (i_raw + 1j * q_imbal).astype(np.complex64)
        # 加 DC
        sig = sig + (0.25 + 0.15j)
        # 加高频（用于验证抽取抗混叠）
        sig = sig + 0.2 * np.exp(1j * 2 * np.pi * 450_000 * t)

        fe = IQFrontend(dc_removal=True, iq_balance=True, decimation=4)
        out = fe.process(sig)
        fs_out = self.FS / 4

        # DC 检查
        dc_before = _center_dc_power_db(sig[2000:])
        dc_after = _center_dc_power_db(out[2000:])
        # IQ 镜像检查
        irr_before = (_bin_power_db(sig, 100_000, self.FS)
                      - _bin_power_db(sig, -100_000, self.FS))
        irr_after = (_bin_power_db(out, 100_000, fs_out)
                     - _bin_power_db(out, -100_000, fs_out))
        # 抗混叠检查（折叠分量在 ±50k，取较大值）
        alias_before = _bin_power_abs_db(sig[::4], 50_000, fs_out)
        alias_after = _bin_power_abs_db(out, 50_000, fs_out)

        print(f"\n[T4-chain] DC 抑制: {dc_before - dc_after:.1f} dB")
        print(f"[T4-chain] IRR: {irr_before:.1f} → {irr_after:.1f} dB")
        print(f"[T4-chain] 折叠分量: {alias_before:.1f} → {alias_after:.1f} dB")

        assert dc_before - dc_after > 30.0, "全链 DC 抑制不足"
        assert irr_after > irr_before + 15.0, "全链 IQ 平衡改善不足"
        assert alias_after < alias_before - 20.0, "全链抗混叠不足"

    def test_switch_dc_off(self):
        """关闭 DC 去除后，DC 分量保留。"""
        sig = _make_tone(100_000, self.FS, self.N, amplitude=0.5) + (0.3 + 0.2j)

        fe = IQFrontend(dc_removal=True, iq_balance=False, decimation=1)
        out_on = fe.process(sig)

        fe2 = IQFrontend(dc_removal=False, iq_balance=False, decimation=1)
        out_off = fe2.process(sig)

        dc_on = _center_dc_power_db(out_on[2000:])
        dc_off = _center_dc_power_db(out_off[2000:])
        print(f"\n[T4-switch] DC 开: {dc_on:.1f} dB, DC 关: {dc_off:.1f} dB")
        assert dc_off > dc_on + 20.0, "关闭 DC 去除后 DC 应保留"

    def test_switch_iq_off(self):
        """关闭 IQ 平衡后，镜像保留。"""
        sig = _make_iq_imbalanced_signal(
            100_000, self.FS, self.N, gain_q=0.8, phase_err_deg=15.0)

        fe_on = IQFrontend(dc_removal=False, iq_balance=True, decimation=1)
        out_on = fe_on.process(sig)
        fe_off = IQFrontend(dc_removal=False, iq_balance=False, decimation=1)
        out_off = fe_off.process(sig)

        irr_on = (_bin_power_db(out_on, 100_000, self.FS)
                  - _bin_power_db(out_on, -100_000, self.FS))
        irr_off = (_bin_power_db(out_off, 100_000, self.FS)
                   - _bin_power_db(out_off, -100_000, self.FS))
        print(f"\n[T4-switch] IQ 开 IRR: {irr_on:.1f} dB, IQ 关 IRR: {irr_off:.1f} dB")
        assert irr_on > irr_off + 15.0, "关闭 IQ 平衡后镜像应保留"

    def test_default_all_enabled(self):
        """默认构造：DC 去除和 IQ 平衡默认开。"""
        fe = IQFrontend()
        assert fe.dc_removal_enabled is True
        assert fe.iq_balance_enabled is True
        assert fe.decimation_factor == 1

    def test_runtime_switch_toggle(self):
        """运行时切换开关：set_dc_removal / set_iq_balance 生效且不崩。"""
        sig = _make_tone(100_000, self.FS, 50_000, amplitude=0.5) + (0.3 + 0.2j)
        fe = IQFrontend(dc_removal=True, iq_balance=True, decimation=1)
        fe.process(sig)
        fe.set_dc_removal(False)
        assert fe.dc_removal_enabled is False
        out = fe.process(sig)  # 不应崩
        fe.set_dc_removal(True)
        assert fe.dc_removal_enabled is True
        fe.set_iq_balance(False)
        assert fe.iq_balance_enabled is False
        out2 = fe.process(sig)
        assert out2 is not None

    def test_diagnostics_structure(self):
        """diagnostics 返回完整结构。"""
        fe = IQFrontend()
        sig = _make_iq_imbalanced_signal(
            100_000, self.FS, 50_000, gain_q=0.8, phase_err_deg=15.0)
        fe.process(sig)
        diag = fe.diagnostics
        assert "dc_removal_enabled" in diag
        assert "iq_balance_enabled" in diag
        assert "decimation_factor" in diag
        assert "iq_correction" in diag
        assert isinstance(diag["iq_correction"], dict)


# ═══════════════════════════════════════════════════════════════════════
# T5: 流式多块
# ═══════════════════════════════════════════════════════════════════════

class TestStreaming:
    """流式多块处理：跨块状态连续。"""

    FS = 2_000_000.0

    def test_multi_block_dc_continuity(self):
        """IQFrontend 逐块处理 DC 去除，与整段处理一致。"""
        n = 80_000
        sig = _make_tone(100_000, self.FS, n, amplitude=0.5) + (0.3 + 0.2j)

        fe_whole = IQFrontend(dc_removal=True, iq_balance=False, decimation=1)
        out_whole = fe_whole.process(sig)

        fe_stream = IQFrontend(dc_removal=True, iq_balance=False, decimation=1)
        block = 8192
        chunks = []
        for i in range(0, n, block):
            chunks.append(fe_stream.process(sig[i:i + block]))
        out_stream = np.concatenate(chunks)

        diff = np.max(np.abs(out_whole[5000:] - out_stream[5000:]))
        print(f"\n[T5-stream] 多块 DC 处理最大差异: {diff:.2e}")
        assert diff < 1e-3, f"流式 DC 处理不连续: {diff:.2e}"

    def test_multi_block_with_decimation(self):
        """逐块 + 抽取：输出总长度正确。"""
        n = 80_000
        D = 4
        sig = _make_tone(100_000, self.FS, n, amplitude=0.5)

        fe = IQFrontend(dc_removal=False, iq_balance=False, decimation=D)
        block = 8192
        total_out = 0
        for i in range(0, n, block):
            out = fe.process(sig[i:i + block])
            total_out += len(out)

        expected = (n + D - 1) // D
        print(f"\n[T5-stream] 多块抽取输出: {total_out} (期望 {expected})")
        assert total_out == expected, "多块抽取总长度错误"


# ═══════════════════════════════════════════════════════════════════════
# T6: 边界与鲁棒性
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界条件：空输入、实数输入、重置、front_end 便捷函数。"""

    def test_empty_input(self):
        """空数组输入不崩，返回空。"""
        fe = IQFrontend()
        out = fe.process(np.array([], dtype=np.complex64))
        assert out.size == 0

    def test_none_input(self):
        """None 输入返回 None。"""
        fe = IQFrontend()
        assert fe.process(None) is None

    def test_real_input_dc(self):
        """实数输入 DC blocker 正常工作。"""
        x = np.sin(2 * np.pi * 1000 * np.arange(10000) / 48000) + 0.5
        blocker = DCBlocker(r=0.998)
        out = blocker.process(x)
        residual = np.abs(np.mean(out[2000:]))
        assert residual < 0.01, f"实数 DC 去除残留: {residual:.4f}"

    def test_real_input_iq_passthrough(self):
        """实数输入 IQ 校正器直通（无 I/Q 不平衡概念）。"""
        x = np.sin(2 * np.pi * 1000 * np.arange(10000) / 48000)
        corrector = IQBalanceCorrector()
        out = corrector.process(x)
        assert np.array_equal(out, x), "实数输入应直通"

    def test_reset_clears_state(self):
        """reset() 清空所有子模块状态。"""
        fe = IQFrontend()
        sig = _make_tone(100_000, 2e6, 10000, amplitude=0.5) + (0.3 + 0.2j)
        fe.process(sig)
        fe.reset()
        # reset 后处理零信号应接近零
        zeros = np.zeros(5000, dtype=np.complex64)
        out = fe.process(zeros)
        assert np.max(np.abs(out[500:])) < 1e-3, "reset 后仍有残留"

    def test_front_end_convenience(self):
        """front_end() 便捷函数返回 (校正后 IQ, 诊断)。"""
        sig = _make_tone(100_000, 2e6, 50_000, amplitude=0.5) + (0.3 + 0.2j)
        corrected, diag = front_end(sig, dc_removal=True, iq_balance=False)
        assert corrected is not None
        assert isinstance(diag, dict)
        assert "dc_removal_enabled" in diag
        # DC 应被去除
        residual = np.abs(np.mean(corrected[2000:]))
        assert residual < 0.01, f"front_end DC 残留: {residual:.4f}"

    def test_invalid_decimation_factor(self):
        """非法抽取因子抛 ValueError。"""
        with pytest.raises(ValueError):
            AntiAliasDecimator(factor=0)
        with pytest.raises(ValueError):
            AntiAliasDecimator(factor=-1)

    def test_invalid_dc_pole(self):
        """非法 DC pole 抛 ValueError。"""
        with pytest.raises(ValueError):
            DCBlocker(r=1.0)
        with pytest.raises(ValueError):
            DCBlocker(r=-0.1)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
