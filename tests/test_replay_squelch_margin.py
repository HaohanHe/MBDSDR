# SPDX-License-Identifier: MIT
"""P-8a：静噪滑杆是「相对噪声基线的余量」，回放路径不许再拿绝对 dBFS 比。

反证（修前 `desktop/main_window.py`）：
  * ``self._squelch_db = -80.0`` 是旧绝对 dBFS 量纲的残留初值；
  * ``_demod_and_play`` 用 ``10*log10(mean|iq|^2)`` 与滑杆值直接比较——
    同一段固定电平的噪声块会被判成「有信号」（全开），而真实信号只因为
    绝对电平低就被关掉，方向正好走反。

本文件在同一段合成 IQ（30 帧噪声 + 30 帧 AM 单音）上对比两种判据的
开门帧数，并钉住：初值引用权威常量、回放门控走 BaselineSquelch 余量。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)

SR = 48000
BLOCK = 4800
NOISE_FRAMES = 30
SIGNAL_FRAMES = 30


def _noise_frame(rng, power_dbfs=-40.0):
    """定电平复噪声：-40 dBFS 在旧判据下高于 -50，会被误判成信号。"""
    amp = 10.0 ** (power_dbfs / 20.0)
    return (amp / np.sqrt(2.0) * (
        rng.standard_normal(BLOCK) + 1j * rng.standard_normal(BLOCK)
    )).astype(np.complex64)


def _signal_frame():
    """AM 单音（1 kHz，调制度 0.6）：带内谱峰，音频存在度高。"""
    t = np.arange(BLOCK) / SR
    env = 1.0 + 0.6 * np.sin(2.0 * np.pi * 1000.0 * t)
    return env.astype(np.complex64)


def _demod(iq):
    from mbdsdr_ai import dsp
    return dsp.demodulate(np.asarray(iq), "AM", sample_rate=SR)


def _old_decision(iq, squelch_db):
    """修前的判据：IQ 绝对功率 dBFS 与滑杆值直接比。"""
    p = float(np.mean(np.abs(iq) ** 2))
    dbfs = 10.0 * float(np.log10(p + 1e-12))
    return dbfs >= float(squelch_db)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    """整模块复用同一个窗口：MainWindow 构造很贵。"""
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        # 测试夹具不静默：断开失败要留日志，便于定位残留状态。
        _LOGGER.exception("MainWindow teardown 失败")


class _CapturePlayer:
    available = True

    def __init__(self):
        self.writes = []
        self.mutes = []

    def set_muted(self, muted):
        self.mutes.append(bool(muted))

    def write(self, audio):
        self.writes.append(np.asarray(audio))


def test_initial_squelch_uses_authoritative_default(window):
    """-80.0 是旧量纲残留：初值必须引用 mbdsdr_ai.squelch 的权威常量。"""
    import inspect

    import main_window as mw
    from mbdsdr_ai.squelch import DEFAULT_SQUELCH_DBFS

    assert window._squelch_db == DEFAULT_SQUELCH_DBFS
    assert window._demod_cfg.squelch_db == DEFAULT_SQUELCH_DBFS
    src = inspect.getsource(mw.MainWindow.__init__)
    assert "self._squelch_db: float = -80.0" not in src, (
        "滑杆是余量口径，初值不许留旧绝对 dBFS 字面量")
    assert "DEFAULT_SQUELCH_DBFS" in src


def test_margin_gate_beats_absolute_dbfs_on_same_iq(window):
    """同一段 IQ：旧判据把噪声全开，新判据噪声关、信号开。"""
    w = window
    w._squelch_db = -50.0
    w._replay_squelch = None
    rng = np.random.default_rng(20261105)

    old_noise_open = 0
    new_noise_open = 0
    for _ in range(NOISE_FRAMES):
        iq = _noise_frame(rng)
        old_noise_open += int(_old_decision(iq, w._squelch_db))
        new_noise_open += int(w._replay_squelch_gate(_demod(iq), "AM"))

    old_signal_open = 0
    new_signal_open = 0
    for _ in range(SIGNAL_FRAMES):
        iq = _signal_frame()
        old_signal_open += int(_old_decision(iq, w._squelch_db))
        new_signal_open += int(w._replay_squelch_gate(_demod(iq), "AM"))

    # 旧判据的病：-40 dBFS 噪声 > -50 → 30/30 全开（把噪声当信号）。
    assert old_noise_open == NOISE_FRAMES
    # 新判据：前 8 帧标定保持打开 + 250ms hang，其余噪声关门。
    assert new_noise_open <= 15, f"噪声帧开门过多：{new_noise_open}"
    # 信号帧只允许「连续 2 帧确认」的 2 帧延迟。
    assert new_signal_open >= SIGNAL_FRAMES - 3, (
        f"信号帧开门过少：{new_signal_open}")
    assert new_signal_open > new_noise_open, (
        f"方向走反：噪声 {new_noise_open} / 信号 {new_signal_open}")


def test_replay_demod_and_play_gates_on_audio(window):
    """产品路径：_demod_and_play 不再按 IQ 绝对电平门控。"""
    w = window
    w._squelch_db = -50.0
    w._replay_squelch = None
    w._demod_cfg.mode = "AM"
    w._last_demod_mode = None
    player = _CapturePlayer()
    w._audio_player = player
    rng = np.random.default_rng(20261105)

    noise_before = len(player.writes)
    for _ in range(NOISE_FRAMES):
        w._demod_and_play(_noise_frame(rng), SR)
    noise_written = len(player.writes) - noise_before

    signal_before = len(player.writes)
    for _ in range(SIGNAL_FRAMES):
        w._demod_and_play(_signal_frame(), SR)
    signal_written = len(player.writes) - signal_before

    assert noise_written <= 15, f"噪声块仍被写声卡：{noise_written}"
    assert signal_written >= SIGNAL_FRAMES - 3, (
        f"信号块被误关：{signal_written}")
    w._audio_player = None



