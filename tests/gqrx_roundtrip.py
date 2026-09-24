"""
GQRX 真实移植 - AGC / IQ校正 / 接收机管道 往返验证
====================================================

对照 GQRX 真实源码（repos/gqrx/src/...）逐项验证 mbdsdr_ai/gqrx_receiver.py：
  1. AGC 稳态：已知幅度阶跃 → 输出 RMS 稳定在 GQRX 目标电平 OUTSCALE=0.7
     （来源: gqrx src/dsp/agc_impl.cpp:66）
  2. AGC 攻击/释放：信号变强后攻击时间(2ms)内压增益；
     信号变弱后释放时间(50ms)量级内恢复（来源 agc_impl.cpp:56,63）
  3. IQ 校正：带直流偏移的信号 → 单极点 IIR(tau=1s) 收敛后均值≈0
     （来源 correct_iq_cc.cpp:47, receiver.cpp:118）
  4. 接收机管道：合成 IQ → FM/AM/SSB 解调 → 48kHz 音频不崩溃
     （来源 nbrx.cpp:73-79, receiver.cpp:64）

所有常量来源见 gqrx_receiver.py 内联注释。
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gqrx_receiver import (  # noqa: E402
    GqrxAGC, IQCorrector, GQRXReceiver,
    PREF_QUAD_RATE_NB, PREF_QUAD_RATE_WFM, AUDIO_RATE, GQRX_FM_MAXDEV,
)


SR = PREF_QUAD_RATE_NB  # AGC 挂在 96kHz，与 nbrx.cpp:47 一致


class TestAGCSteadyState(unittest.TestCase):
    """已知幅度阶跃 → AGC 输出稳定在目标电平 OUTSCALE=0.7。"""

    def test_weak_and_strong_both_settle_to_target(self):
        """弱(0.1)与强(0.9)输入，稳态 RMS 都应收敛到 ~0.7。"""
        agc = GqrxAGC(SR, threshold_db=-100, decay_ms=500)
        # 预热 0.3s 让内部 18ms 峰值窗/平均器收敛
        agc.process(np.full(int(0.3 * SR), 0.1, dtype=complex))
        out_weak = agc.process(np.full(int(0.3 * SR), 0.1, dtype=complex))
        rms_weak = float(np.sqrt(np.mean(np.abs(out_weak[10000:]) ** 2)))

        agc.process(np.full(int(0.3 * SR), 0.9, dtype=complex))
        out_strong = agc.process(np.full(int(0.3 * SR), 0.9, dtype=complex))
        rms_strong = float(np.sqrt(np.mean(np.abs(out_strong[10000:]) ** 2)))

        self.assertAlmostEqual(rms_weak, 0.7, delta=0.15,
                               msg=f"弱信号稳态 RMS 应≈0.7, 实得 {rms_weak:.3f}")
        self.assertAlmostEqual(rms_strong, 0.7, delta=0.15,
                               msg=f"强信号稳态 RMS 应≈0.7, 实得 {rms_strong:.3f}")

    def test_output_not_clipped(self):
        """最大增益不应把输出打出爆炸电平（延迟线+限幅）。"""
        agc = GqrxAGC(SR)
        agc.process(np.full(int(0.1 * SR), 0.01, dtype=complex))  # 极弱
        out = agc.process(np.full(int(0.2 * SR), 0.01, dtype=complex))
        steady = out[5000:]
        self.assertLess(np.max(np.abs(steady)), 2.0,
                        "稳态输出不应超过 2x 满幅")


class TestAGCAttackRelease(unittest.TestCase):
    """攻击时间(2ms)/释放时间(50ms)量级行为。"""

    def test_attack_is_fast(self):
        """信号突然变强后，输出应在 ~数 ms 内从放大态压回目标。"""
        agc = GqrxAGC(SR, threshold_db=-100, decay_ms=500)
        # 先在弱信号上稳态（增益高）
        agc.process(np.full(int(0.5 * SR), 0.1, dtype=complex))
        # 阶跃到强信号
        out = agc.process(np.full(int(0.05 * SR), 0.9, dtype=complex))
        # 分 2ms 块看 RMS 收敛到 0.7±20% 的时间
        win = int(0.002 * SR)
        settled_ms = None
        for k in range(0, len(out) - win, win):
            r = float(np.sqrt(np.mean(np.abs(out[k:k + win]) ** 2)))
            if abs(r - 0.7) < 0.15:
                settled_ms = k / SR * 1000
                break
        self.assertIsNotNone(settled_ms, "强信号阶跃后应收敛到目标电平")
        # 攻击时间常数 2ms（agc_impl.cpp:56），容差给到 20ms
        self.assertLessEqual(settled_ms, 20.0,
                             f"攻击应在 ~2ms 量级收敛, 实得 {settled_ms:.1f}ms")

    def test_release_is_slower_than_attack(self):
        """信号变弱后：hang 期保持增益(输出被压)，之后慢释放回升。

        用 decay=100ms（hang=100ms）让 hang 在观测窗内结束，
        对应 agc_impl.cpp:63 RELEASE_TIMECONST=50ms 慢释放。
        """
        agc = GqrxAGC(SR, threshold_db=-100, decay_ms=100, use_hang=True)
        # 强信号稳态
        agc.process(np.full(int(0.5 * SR), 0.9, dtype=complex))
        # 阶跃到弱信号
        out = agc.process(np.full(int(0.5 * SR), 0.05, dtype=complex))
        w = int(0.02 * SR)
        rms_hang = float(np.sqrt(np.mean(np.abs(out[3 * w:4 * w]) ** 2)))     # ~60ms hang 内
        rms_late = float(np.sqrt(np.mean(np.abs(out[15 * w:16 * w]) ** 2)))    # ~300ms 释放后
        # hang 期增益被压住（弱信号输出很小，不噪声 rush）
        self.assertLess(rms_hang, 0.15, f"hang 期应保持低输出, 实得 {rms_hang:.3f}")
        # hang 结束后慢释放，输出逐步回升向 0.7
        self.assertGreater(rms_late, rms_hang + 0.1,
                           f"释放后输出应回升: {rms_hang:.3f} -> {rms_late:.3f}")


class TestIQCorrection(unittest.TestCase):
    """直流偏移自适应消除（correct_iq_cc.cpp:47, tau=1s）。"""

    def test_dc_offset_removed(self):
        """给 IQ 加固定 DC 偏移，收敛后均值应远小于偏移量。"""
        sr = PREF_QUAD_RATE_NB
        rng = np.random.default_rng(0)
        # 8 秒信号，让 tau=1s 的 IIR 充分收敛
        iq = (rng.standard_normal(int(8 * sr))
              + 1j * rng.standard_normal(int(8 * sr))
              + complex(0.3, -0.2))
        cor = IQCorrector(sr, dc_tau=1.0)
        out = cor.process(iq)
        # 取后段稳态
        tail = out[int(4 * sr):]
        self.assertLess(abs(tail.real.mean()), 0.03,
                        f"I 直流残留应≈0, 实得 {tail.real.mean():.4f}")
        self.assertLess(abs(tail.imag.mean()), 0.03,
                        f"Q 直流残留应≈0, 实得 {tail.imag.mean():.4f}")

    def test_dc_offset_estimate_converges(self):
        """估计出的 DC 偏移应接近真实注入值。"""
        sr = PREF_QUAD_RATE_NB
        rng = np.random.default_rng(1)
        iq = (rng.standard_normal(int(5 * sr)) + 1j * rng.standard_normal(int(5 * sr))
              + complex(0.25, 0.1))
        cor = IQCorrector(sr, dc_tau=1.0)
        cor.process(iq)
        self.assertAlmostEqual(cor.dc_offset.real, 0.25, delta=0.05)
        self.assertAlmostEqual(cor.dc_offset.imag, 0.10, delta=0.05)


class TestReceiverPipeline(unittest.TestCase):
    """合成 IQ → 接收机管道 → 48kHz 音频不崩溃。"""

    def test_fm_pipeline_outputs_48k_audio(self):
        """合成 FM（1kHz 调制）→ 解调 → 音频长度按 48k 重采样，不抛异常。"""
        sr = PREF_QUAD_RATE_NB
        t = np.arange(sr) / sr
        fdev = GQRX_FM_MAXDEV["nfm"]  # 5000Hz, nbrx.cpp:52
        phase = 2 * np.pi * fdev * 0.5 * np.cumsum(np.sin(2 * np.pi * 1000 * t)) / sr
        iq = np.exp(1j * phase)
        rx = GQRXReceiver(sample_rate=sr, mode="nfm")
        r = rx.process(iq)
        self.assertEqual(r["audio_rate"], AUDIO_RATE)
        self.assertEqual(len(r["audio"]), int(sr * AUDIO_RATE / sr))  # 48000
        self.assertTrue(np.all(np.isfinite(r["audio"])))

    def test_all_modes_run_no_crash(self):
        """fm/nfm/wfm/am/usb/lsb/cw 全部跑通，输出有限。"""
        for mode in GQRXReceiver.SUPPORTED_MODES:
            sr = PREF_QUAD_RATE_WFM if mode == "wfm" else PREF_QUAD_RATE_NB
            t = np.arange(int(sr)) / sr
            if mode in ("fm", "nfm", "wfm"):
                md = GQRX_FM_MAXDEV[mode]
                iq = np.exp(1j * 2 * np.pi * md * 0.5
                            * np.cumsum(np.sin(2 * np.pi * 1000 * t)) / sr)
            else:
                iq = np.exp(1j * 2 * np.pi * 1000 * t) * 0.5
            rx = GQRXReceiver(sample_rate=sr, mode=mode)
            r = rx.process(iq)
            self.assertEqual(r["audio_rate"], AUDIO_RATE, f"{mode} 音频应为48k")
            self.assertTrue(np.all(np.isfinite(r["audio"])), f"{mode} 音频应有限")

    def test_fm_demod_recovers_tone(self):
        """1kHz 音调调制 → 鉴频后频谱峰应落在 ~1kHz。"""
        sr = PREF_QUAD_RATE_NB
        t = np.arange(sr) / sr
        fdev = GQRX_FM_MAXDEV["nfm"]
        phase = 2 * np.pi * fdev * np.cumsum(np.sin(2 * np.pi * 1000 * t)) / sr
        iq = np.exp(1j * phase)
        rx = GQRXReceiver(sample_rate=sr, mode="nfm")
        r = rx.process(iq)
        a = np.array(r["audio"])
        sp = np.abs(np.fft.rfft(a))
        fr = np.fft.rfftfreq(len(a), 1 / AUDIO_RATE)
        peak = fr[5:][np.argmax(sp[5:])]
        self.assertAlmostEqual(peak, 1000.0, delta=200.0,
                               msg=f"应解出 1kHz 音调, 实得 {peak:.0f}Hz")


class TestToolRegistryRegistration(unittest.TestCase):
    """三个工具能注册进 ToolRegistry 且可调用。"""

    def test_register_and_invoke(self):
        from mbdsdr_ai.tool_registry import ToolRegistry
        from mbdsdr_ai.gqrx_receiver import register_gqrx_receiver_tools
        reg = ToolRegistry()
        register_gqrx_receiver_tools(reg)
        for name in ("agc_process", "iq_correct", "gqrx_receiver_create"):
            self.assertIn(name, reg.tools, f"{name} 应已注册")
        # 调一次 gqrx_receiver_create
        iq = (np.random.default_rng(0).standard_normal(int(0.1 * SR))
              + 1j * np.random.default_rng(0).standard_normal(int(0.1 * SR)))
        res = reg.tools["agc_process"]["handler"]({"iq": iq.tolist(), "sample_rate": SR})
        self.assertTrue(res.success, res.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
