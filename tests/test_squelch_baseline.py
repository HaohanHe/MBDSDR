# SPDX-License-Identifier: MIT
"""静噪重做验收（相对噪声基线余量）
==================================

裁定（2026-11-05 二次）：滑杆刻度 -100..-20（默认 -50）的语义是
「相对该模式噪声基线的余量」，不是绝对音频电平。验收矩阵：
6 模式 × {纯噪声, 弱可读, 强信号} × 默认 -50 → 纯噪声关、弱可读开、强信号开；
增益 +6 dB 后读数变化 ≤ 1 dB。

本文件第一条用例就是反证：旧口径（固定音频 RMS -50）在 FM 纯噪声下判「不关」。
"""
import os
import sys
import types

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.squelch import (  # noqa: E402
    DEFAULT_SQUELCH_DBFS,
    BaselineSquelch,
)
from mode_registry import demodulate  # noqa: E402
from receive_pipeline import DemodWorker, SharedDemodConfig  # noqa: E402

SR = 48000.0
N = 384
MODES = ("AM", "USB", "LSB", "NFM", "FM", "CW")
# 弱可读的输入 SNR：FM 有阈值效应，10 dB 时解调音频仍不可读（实测余量 4.5 dB），
# 用 20 dB 代表「刚好可读」；其余模式 10 dB 即清晰可读。
WEAK_SNR = {"AM": 10.0, "USB": 10.0, "LSB": 10.0,
            "NFM": 10.0, "FM": 20.0, "CW": 10.0}
STRONG_SNR = 40.0


def _scene(mode: str, snr: float, i: int, gain: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(11 + i * 7)
    n = N
    t = np.arange(n) / SR
    if mode in ("NFM", "FM"):
        dev = 3000.0 if mode == "NFM" else 20000.0
        phase = 2 * np.pi * dev * np.sin(2 * np.pi * 1000.0 * t) / 1000.0
        sig = gain * np.exp(1j * phase)
    elif mode == "AM":
        sig = gain * (1.0 + 0.4 * np.cos(2 * np.pi * 1000.0 * t))
    elif mode == "USB":
        sig = 0.5 * gain * np.exp(1j * 2 * np.pi * 1000.0 * t)
    elif mode == "LSB":
        sig = 0.5 * gain * np.exp(-1j * 2 * np.pi * 1000.0 * t)
    else:  # CW
        sig = gain * np.exp(1j * 2 * np.pi * 1000.0 * t)
    if snr is None:
        noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) / np.sqrt(2)
        noise = noise * gain * float(np.abs(sig).mean())
        return noise.astype(np.complex64)
    sigma = np.sqrt(10 ** (-snr / 10.0) / 2.0) * gain
    noise = sigma * (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n))
    return (sig + noise).astype(np.complex64)


def _audio(mode: str, snr, i: int, gain: float = 1.0) -> np.ndarray:
    a = demodulate(mode, _scene(mode, snr, i, gain), sample_rate=SR)
    return np.asarray(a, dtype=np.float64).ravel()


def _run(mode: str, snr, frames: int, gain: float = 1.0,
         squelch_db: float = DEFAULT_SQUELCH_DBFS) -> list:
    """按生产口径跑：噪声帧标定，再喂目标场景，返回每帧门状态。"""
    sq = BaselineSquelch(squelch_db=squelch_db, mode=mode, sample_rate=SR,
                         calib_frames=8, hang_ms=0.0)
    for i in range(8):          # 纯噪声标定窗口
        sq.update(_audio(mode, None, i, gain), N)
    return [sq.update(_audio(mode, snr, 20 + i, gain), N)
            for i in range(frames)]


# ---------------------------------------------------------------------------
# 反证转绿：旧口径（音频 RMS ≥ -50）在 FM 纯噪声下开门；新口径应关门
# ---------------------------------------------------------------------------
class _DummySignal:
    def emit(self, *_args):
        pass


def _worker_shell(mode: str, auto: bool):
    w = types.SimpleNamespace(
        _cfg=SharedDemodConfig(mode=mode, squelch_db=DEFAULT_SQUELCH_DBFS,
                               auto_squelch=auto),
        _squelch=BaselineSquelch(squelch_db=DEFAULT_SQUELCH_DBFS, mode=mode,
                                 sample_rate=SR),
        _last_squelch_label="",
        squelch_state=_DummySignal(),
    )
    return w


def test_fm_pure_noise_default_is_closed():
    """纯噪声 FM + 默认 -50：生产路径（DemodWorker._squelch_open）必须关门。"""
    w = _worker_shell("FM", auto=False)
    for i in range(8):          # 标定窗口（期间保持开门）
        DemodWorker._squelch_open(w, _audio("FM", None, i))
    assert DemodWorker._squelch_open(w, _audio("FM", None, 9)) is False


# ---------------------------------------------------------------------------
# 验收矩阵
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
def test_noise_is_closed_at_default(mode):
    states = _run(mode, None, 20)
    assert not any(states), f"{mode} 纯噪声在默认 -50 下不应开门: {states}"


@pytest.mark.parametrize("mode", MODES)
def test_weak_readable_is_open_at_default(mode):
    states = _run(mode, WEAK_SNR[mode], 24)
    assert any(states), f"{mode} 弱可读信号在默认 -50 下应开门: {states}"


@pytest.mark.parametrize("mode", MODES)
def test_strong_signal_is_open_at_default(mode):
    states = _run(mode, STRONG_SNR, 12)
    # 前 2 帧是连续确认窗口（防噪声单帧毛刺），之后必须全开
    assert all(states[2:]), f"{mode} 强信号在默认 -50 下应全开: {states}"


# ---------------------------------------------------------------------------
# 增益 +6 dB：读数是相对余量，不应移动
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", MODES)
def test_gain_step_does_not_move_reading(mode):
    sq = BaselineSquelch(mode=mode, sample_rate=SR, calib_frames=8)
    for i in range(8):
        sq.update(_audio(mode, None, i, 1.0), N)
    for i in range(12):
        sq.update(_audio(mode, WEAK_SNR[mode], 20 + i, 1.0), N)
    m1 = sq.last_margin_db
    sq2 = BaselineSquelch(mode=mode, sample_rate=SR, calib_frames=8)
    for i in range(8):
        sq2.update(_audio(mode, None, i, 2.0), N)
    for i in range(12):
        sq2.update(_audio(mode, WEAK_SNR[mode], 20 + i, 2.0), N)
    m2 = sq2.last_margin_db
    assert abs(m2 - m1) <= 1.0, f"{mode} +6 dB 读数漂移 {m2 - m1:+.2f} dB"


# ---------------------------------------------------------------------------
# 标定状态可见 / 切模式重标
# ---------------------------------------------------------------------------
def test_calibration_failure_is_visible():
    sq = BaselineSquelch(mode="FM", sample_rate=SR, calib_frames=4)
    silent = np.zeros(N)
    for _ in range(6):
        sq.update(silent, N)
    assert sq.calibration_failed
    assert "失败" in sq.state_label()


def test_mode_switch_recalibrates():
    sq = BaselineSquelch(mode="FM", sample_rate=SR, calib_frames=8)
    for i in range(8):
        sq.update(_audio("FM", None, i), N)
    assert sq.floor_db is not None
    sq.set_mode("AM")
    assert sq.floor_db is None and sq.calibrating
