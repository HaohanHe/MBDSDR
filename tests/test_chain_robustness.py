# SPDX-License-Identifier: MIT
"""tests/test_chain_robustness.py — P4.1 链路健壮性钉扎。

弱信号与突变场景下：诚实降级、不崩、不静默给出错结果。
覆盖三个原子件（receive_pipeline 的 AGC/静噪/重采样同源件）：

  * dsp.AGC：强→弱→强突变有界、静音直通不漂移、max_gain 钳制、reset。
  * AutoSquelch（相对噪声底自动静噪）：纯噪声从不开门、信号开门
    + hang 迟滞不咔哒、持续信号不被学进噪声底、reset 重学。
  * NoiseSquelch（WFM 解调后噪声静噪）：嘶声关门、语音开门、hang。
  * AudioResampler（有状态流式）：窄带↔宽带突变输出有界无 NaN、
    分块流式与一次性结果一致（跨块状态连续）、reset 清历史。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.dsp import AGC  # noqa: E402
from mbdsdr_ai.squelch import AutoSquelch, NoiseSquelch, rms_dbfs  # noqa: E402
from mbdsdr_ai.audio_resampler import AudioResampler  # noqa: E402

FS = 48000.0
BLOCK = 960  # 20 ms


def _tone(level, freq=1000.0, n=BLOCK):
    t = np.arange(n) / FS
    return level * np.sin(2 * np.pi * freq * t)


def _noise(level, n=BLOCK, seed=0):
    rng = np.random.default_rng(seed)
    return level * rng.standard_normal(n)


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


# ---------------------------------------------------------------------------
# AGC
# ---------------------------------------------------------------------------
class TestAgcRobustness:
    def test_step_strong_weak_strong_bounded(self):
        """电平突变 1.0 → 1e-4 → 1.0：输出有界、无 NaN、稳态趋近目标。"""
        agc = AGC(target_level=0.5, attack=0.2, release=0.05, max_gain=100.0)
        stages = [(1.0, 100), (1e-4, 300), (1.0, 100)]
        out_tail = []
        for level, blocks in stages:
            for i in range(blocks):
                y = agc.process(_tone(level) + _noise(level * 0.01, seed=i))
                assert np.all(np.isfinite(y)), "AGC 输出出现 NaN/Inf"
                assert np.max(np.abs(y)) <= 100.0 * 1.0 + 1e-6
                if i >= blocks - 20:
                    out_tail.append(_rms(y))
        # 回到强信号后的稳态：输出 RMS 应贴近 target（平滑收敛带内）
        assert 0.35 < float(np.mean(out_tail)) < 0.65

    def test_weak_stage_converges_to_target(self):
        """弱信号段增益升满后输出贴近 max_gain 上限下的实际电平。"""
        agc = AGC(target_level=0.5, attack=0.2, release=0.05, max_gain=100.0)
        for _ in range(5):
            agc.process(_tone(1.0))
        tail = []
        for i in range(400):
            y = agc.process(_tone(1e-4))
            if i >= 380:
                tail.append(_rms(y))
        # 单音幅度 1e-4 → RMS=7.07e-5；desired=0.5/7.07e-5>max_gain
        # → 钳 100 → 输出 RMS ≈ 7.07e-3
        assert float(np.mean(tail)) == pytest.approx(0.00707, rel=0.15)

    def test_silence_passthrough_no_drift(self):
        """近零输入直通（不触发增益更新），增益不漂移、输出不炸。"""
        agc = AGC(target_level=0.5)
        g0 = agc._current_gain
        for _ in range(50):
            y = agc.process(np.zeros(BLOCK))
            assert np.all(np.isfinite(y)) and np.max(np.abs(y)) == 0.0
        assert agc._current_gain == g0

    def test_reset_restores_unity(self):
        agc = AGC()
        agc.process(_tone(1e-3))
        assert agc._current_gain != 1.0
        agc.reset()
        assert agc._current_gain == 1.0


# ---------------------------------------------------------------------------
# AutoSquelch
# ---------------------------------------------------------------------------
class TestAutoSquelchRobustness:
    def test_pure_noise_never_opens(self):
        """纯噪声（-60±2 dB 缓慢漂移）连续 200 块：门绝不开（不放嘶声）。"""
        sq = AutoSquelch(margin_db=8.0, sample_rate=FS)
        rng = np.random.default_rng(7)
        opens = 0
        for _ in range(200):
            p_db = -60.0 + float(rng.standard_normal())
            opens += int(sq.update(p_db, BLOCK))
        assert opens == 0, f"纯噪声误开 {opens} 块"
        assert sq.floor_db is not None and sq.floor_db < -55.0

    def test_signal_opens_and_hang_closes(self):
        """信号出现开门；消失后 hang 内保持、超时关闭（迟滞不咔哒）。"""
        sq = AutoSquelch(margin_db=8.0, hang_ms=250.0, sample_rate=FS)
        for _ in range(20):
            sq.update(-60.0, BLOCK)          # 学噪声底
        assert not sq.is_open
        assert sq.update(-40.0, BLOCK)       # 信号出现 → 立即开
        assert sq.is_open
        flaps = 0
        prev = True
        # 信号消失：hang 250ms = 12.5 块（20ms/块）内保持开门
        for i in range(20):
            cur = sq.update(-60.0, BLOCK)
            flaps += int(cur != prev)
            prev = cur
        assert not sq.is_open, "hang 超时后必须关闭"
        assert flaps == 1, f"关闭过程出现抖动（{flaps} 次翻转）"

    def test_threshold_jitter_no_flap(self):
        """功率在门限 ±1dB 抖动：开门状态保持（hang 吃抖），不反复横跳。"""
        sq = AutoSquelch(margin_db=8.0, hang_ms=250.0, sample_rate=FS)
        for _ in range(20):
            sq.update(-60.0, BLOCK)
        sq.update(-40.0, BLOCK)              # 开门
        flaps = 0
        prev = True
        for i in range(10):
            cur = sq.update(-51.0 + (-1) ** i, BLOCK)  # 门限 -52 附近抖动
            flaps += int(cur != prev)
            prev = cur
        assert flaps <= 2, f"门限附近抖动引发 {flaps} 次翻转（hang 失效）"

    def test_signal_not_learned_into_floor(self):
        """持续信号期间噪声底冻结——不把电台学进底噪（否则门被顶关）。"""
        sq = AutoSquelch(margin_db=8.0, sample_rate=FS)
        for _ in range(20):
            sq.update(-60.0, BLOCK)
        floor0 = sq.floor_db
        for _ in range(200):
            sq.update(-40.0, BLOCK)          # 持续信号 4 秒
        assert sq.is_open, "持续信号期间门被顶关（噪声底被污染）"
        assert sq.floor_db == pytest.approx(floor0, abs=0.5)

    def test_reset_relearns(self):
        sq = AutoSquelch(sample_rate=FS)
        for _ in range(10):
            sq.update(-50.0, BLOCK)
        sq.reset()
        assert sq.floor_db is None and not sq.is_open


# ---------------------------------------------------------------------------
# NoiseSquelch（WFM 解调后）
# ---------------------------------------------------------------------------
class TestNoiseSquelchRobustness:
    def test_hiss_closes_speech_opens(self):
        """鉴频嘶声（白噪声，频谱平坦）关门；语音带单音开门。"""
        sq = NoiseSquelch(sample_rate=FS, win_ms=200.0, warmup_ms=200.0)
        # 先喂嘶声（ warmup 填满，判定关门）
        hiss_open = 0
        for i in range(20):
            hiss_open += int(sq.process_audio(_noise(0.3, seed=100 + i)))
        assert hiss_open == 0, f"嘶声误开 {hiss_open} 块"
        # 切语音（1kHz 单音+少量噪声）：能量集中语音带 → 开门；
        # 滑动窗含嘶声历史，允许前 5 块收敛，之后必须全开门。
        opens = []
        for i in range(20):
            blk = _tone(0.3) + _noise(0.02, seed=i)
            opens.append(bool(sq.process_audio(blk)))
        assert all(opens[5:]), \
            f"语音稳定后仍有关门块: {opens}"
        assert sum(opens) >= 15, f"语音收敛过慢（仅 {sum(opens)}/20 块）"

    def test_hang_prevents_dropout_flap(self):
        """语音短暂停顿（节目间隙）hang 内不关门。"""
        sq = NoiseSquelch(sample_rate=FS, win_ms=200.0, warmup_ms=200.0,
                          hang_ms=300.0)
        for i in range(15):
            sq.process_audio(_noise(0.3, seed=i))
        for i in range(15):
            sq.process_audio(_tone(0.3))
        assert sq.is_open
        # 停顿 10 块（200ms < hang 300ms）：保持开门
        for i in range(10):
            assert sq.process_audio(_noise(0.3, seed=50 + i)) is True
        # 再停 20 块（400ms > hang）：关门
        for i in range(20):
            cur = sq.process_audio(_noise(0.3, seed=70 + i))
        assert cur is False


# ---------------------------------------------------------------------------
# AudioResampler（有状态流式）
# ---------------------------------------------------------------------------
class TestResamplerBandwidthSteps:
    def test_narrowband_to_wideband_bounded(self):
        """输入带宽突变（1kHz 单音 → 全带噪声 → 单音）：输出有界无 NaN。"""
        rs = AudioResampler(12000.0, 48000.0)
        outs = []
        for i in range(50):
            blk = _tone(0.8, n=240) if i < 25 else _noise(0.8, n=240, seed=i)
            y = rs.process(blk)
            assert np.all(np.isfinite(y))
            # 有界：噪声峰度+滤波过冲的瞬时峰值远小于发散量级
            assert np.max(np.abs(y)) < 4.0, "突变后输出爆幅（发散迹象）"
            assert _rms(y) < 1.2, "突变后输出 RMS 异常"
            outs.append(y)
        for i in range(25, 50):
            y = rs.process(_tone(0.8, n=240))
            assert np.all(np.isfinite(y))
            outs.append(y)
        total_in = 75 * 240
        total_out = sum(len(y) for y in outs)
        assert total_out == pytest.approx(total_in * 4, rel=0.01)

    def test_stream_matches_one_shot(self):
        """分块流式（跨块状态连续）与一次性处理结果一致。"""
        src = np.concatenate([_tone(0.7, n=1200), _noise(0.3, n=1200, seed=3)])
        whole = AudioResampler(12000.0, 48000.0).process(src)
        rs = AudioResampler(12000.0, 48000.0)
        chunks = [rs.process(src[i:i + 240]) for i in range(0, len(src), 240)]
        streamed = np.concatenate(chunks)
        assert len(streamed) == len(whole)
        np.testing.assert_allclose(streamed, whole, atol=1e-4)

    def test_reset_clears_history(self):
        rs = AudioResampler(12000.0, 48000.0)
        rs.process(_tone(0.9, n=240))
        rs.reset()
        y = rs.process(np.zeros(240))
        # reset 后历史清零：零输入输出严格为零（无旧信号拖尾串音）
        assert np.max(np.abs(y)) == 0.0
