"""2026-11-10 音频过载 / 性能档 / 显式收听 的契约测试。

背景：用户实听反馈「间歇性有正常声音，大多过载」。量化实测：
旧音量映射 pct/100*2.0 在 72% 给 gain=1.44，20.8% 样本被硬 clip；
100% 时 58.3%。本文件把这些回归钉住，不许再退回去。
"""
import numpy as np
import pytest

from mbdsdr_ai.audio_out import (
    AudioPlayer, soft_limit, volume_pct_to_gain,
)
from mbdsdr_ai.dsp import WFMReceiver, wfm_broadcast_demod


# ---------------------------------------------------------------- 音量曲线
def test_volume_gain_never_exceeds_unity():
    """任何滑杆位置都不得放大到 1.0 以上（>1 必然硬削波）。"""
    for pct in (0, 1, 25, 48, 72, 90, 100, 150, -10):
        g = volume_pct_to_gain(pct)
        assert 0.0 <= g <= 1.0, f"{pct}% -> {g}"


def test_volume_gain_is_perceptual_square_like_sdrpp():
    """对照 SDR++ volume.h:14 powf(volume, 2)：平方律，非旧线性 ×2。"""
    assert volume_pct_to_gain(100) == pytest.approx(1.0)
    assert volume_pct_to_gain(72) == pytest.approx(0.72 ** 2, abs=1e-6)
    assert volume_pct_to_gain(48) < 0.25
    # 旧的线性 ×2 在 72% 会给 1.44；这里必须明显小于 1
    assert volume_pct_to_gain(72) < 0.6


def test_volume_gain_monotonic():
    vals = [volume_pct_to_gain(p) for p in range(0, 101, 5)]
    assert all(a <= b for a, b in zip(vals, vals[1:]))


# ---------------------------------------------------------------- 软限幅
def test_soft_limit_leaves_normal_samples_untouched():
    a = np.array([0.0, 0.2, -0.5, 0.89, 0.9], dtype=np.float32)
    out = soft_limit(a)
    np.testing.assert_allclose(out, a, atol=1e-7)


def test_soft_limit_compresses_instead_of_clipping_flat():
    """硬 clip 会把 1.2/2.0/3.0 全切成同一个 1.0（方波）；软限幅必须仍可分。"""
    a = np.array([0.95, 1.2, 2.0, 3.0], dtype=np.float32)
    out = soft_limit(a)
    assert np.all(np.abs(out) <= 1.0)
    assert out[0] < out[1] < out[2] < out[3], out
    assert np.all(np.diff(out) > 0), "不能压平成方波"


def test_soft_limit_is_continuous_at_threshold():
    eps = 1e-4
    below = float(soft_limit(np.array([0.9 - eps], dtype=np.float32))[0])
    above = float(soft_limit(np.array([0.9 + eps], dtype=np.float32))[0])
    assert abs(above - below) < 1e-3


def test_soft_limit_keeps_sign():
    a = np.array([-3.0, -1.2, 1.2, 3.0], dtype=np.float32)
    out = soft_limit(a)
    assert out[0] < 0 and out[1] < 0 and out[2] > 0 and out[3] > 0


# ---------------------------------------------------------------- 写卡不再过载
def test_write_applies_soft_limit_not_hard_clip(monkeypatch):
    """AudioPlayer.write 有数据时不能把 1.2/2.0 切成同一个值。"""
    p = AudioPlayer(sample_rate=48000)
    p.available = True
    p._stream = object()          # 绕过真实声卡，只测数据通路
    block = np.array([0.5, 0.9, 1.2, 2.0], dtype=np.float32)
    assert p.write(block, sr=None) == 4
    with p._lock:
        queued = np.concatenate(list(p._queue))
    assert np.all(np.abs(queued) <= 1.0)
    assert queued[2] < queued[3], "硬 clip 会把两个越界样本压成同一个值"


def test_write_respects_perceptual_gain():
    p = AudioPlayer(sample_rate=48000, gain=volume_pct_to_gain(72))
    assert p.gain < 1.0
    p.set_gain(volume_pct_to_gain(100))
    assert p.gain == pytest.approx(1.0)


# ---------------------------------------------------------------- 有状态 WFM
def _noise_block(n=16384, seed=1):
    rng = np.random.default_rng(seed)
    return (rng.normal(0, .02, n) + 1j * rng.normal(0, .02, n)).astype(np.complex64)


def test_stateless_demod_amplifies_silence_to_full_scale():
    """旧路径：纯噪声块也被归一化到 0.95 峰值（病征记录，不是期望行为）。"""
    out = wfm_broadcast_demod(_noise_block(), 240_000, audio_sr=48_000)
    assert float(np.max(np.abs(out))) == pytest.approx(0.95, abs=1e-3)


def test_stateful_receiver_keeps_noise_quiet():
    """新路径：噪声块保持安静，不随每块峰值被拉满。"""
    rx = WFMReceiver(240_000, audio_sr=48_000)
    outs = [rx.process(_noise_block(seed=i)) for i in range(6)]
    out = np.concatenate(outs)
    assert float(np.max(np.abs(out))) < 0.95
    assert float(np.sqrt(np.mean(out ** 2))) < 0.35
    # 软限幅兜底，永不越界
    assert float(np.max(np.abs(out))) <= 1.0


# ---------------------------------------------------------------- 性能档
def test_perf_profiles_use_only_real_device_rates():
    """档位采样率必须是 RTL 后端真支持的离散档，否则界面会「选了又跳回去」。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop"))
    import perf_profile as pp
    for key, conf in pp.PROFILES.items():
        assert pp.is_supported_rate(conf["sample_rate_hz"]), key
        assert conf["gnss_poll_ms"] >= 200
        assert conf["spectrum_interval_ms"] >= 30
    assert pp.DEFAULT_PROFILE == "eco"
    assert pp.sample_rate_for("eco") == 1_024_000
    assert pp.profile("nonexistent") is pp.PROFILES["eco"]


def test_perf_default_rate_is_not_kernel_baseline():
    """桌面默认必须比内核 2.4 MS/s 基准低（用户：电脑没那么强大，得让路）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop"))
    import perf_profile as pp
    from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ
    assert pp.sample_rate_for(pp.DEFAULT_PROFILE) < DEFAULT_SAMPLE_RATE_HZ
    # 内核常量本身不动：真机验收 R4 / ADS-B 依赖它
    assert DEFAULT_SAMPLE_RATE_HZ == 2_400_000
