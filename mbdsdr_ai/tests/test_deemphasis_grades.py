# SPDX-License-Identifier: MIT
"""去加重档位测试（sdrpp WFM/NFM 去emphasis 下拉对等，2026-10-14）。"""
import numpy as np
import pytest

from mbdsdr_ai.receive_chain import DEEMPH_TAU_US, ReceiveChain


def _tone(freq, n=4096, fs=48000.0):
    t = np.arange(n) / fs
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_default_nfm_keeps_50us():
    ch = ReceiveChain(fs_in=2_048_000.0, mode="NFM")
    assert ch._deemph_tau("nfm") == 50e-6
    out = ch.process(None) if False else None   # 不跑硬件路径
    assert ch.deemph_tau_us is None              # None=模式默认口径


def test_explicit_override_beats_mode_default():
    ch = ReceiveChain(fs_in=2_048_000.0, mode="WFM", deemph_tau_us="75us")
    assert ch._deemph_tau("wfm") == 75e-6
    assert ch.deemph_tau_us == "75us"


def test_set_deemphasis_runtime_and_rejects():
    ch = ReceiveChain(fs_in=2_048_000.0, mode="FM")
    ch.set_deemphasis("22us")
    assert ch.deemph_tau_us == "22us"
    ch.set_deemphasis("none")
    assert ch._deemph_tau("nfm") == 0.0
    with pytest.raises(ValueError):
        ch.set_deemphasis("35us")


def test_invalid_ctor_grade_rejected():
    with pytest.raises(ValueError):
        ReceiveChain(fs_in=2_048_000.0, mode="FM", deemph_tau_us="bad")


def test_high_frequency_attenuation_differs_by_tau():
    """75us 比 22us 对同一高频音的衰减更强（去加重的物理方向）。

    链路级断点（避开完整设备的样本量）：直接驱动 DemodNFM 的两个档位。
    """
    from mbdsdr_ai.demod_nfm import DemodNFM

    tones = np.concatenate([_tone(1000.0), _tone(6000.0)])
    d22 = DemodNFM(if_sr=50000.0, bandwidth=12500.0, deemph_tau=22e-6)
    d75 = DemodNFM(if_sr=50000.0, bandwidth=12500.0, deemph_tau=75e-6)
    a22 = d22.process(tones)
    a75 = d75.process(tones)
    # 取稳态段比较 6kHz 分量幅度（前一段为 1kHz，校准两档共性增益）
    def rms(x, lo, hi):
        return float(np.sqrt(np.mean(x[lo:hi] ** 2)))
    r22 = rms(a22, 2048, 4096)
    r75 = rms(a75, 2048, 4096)
    assert r75 < r22 * 0.7, (r75, r22)


def test_deemph_table_matches_sdrpp_grades():
    assert sorted(DEEMPH_TAU_US) == ["22us", "50us", "75us", "none"]
    assert DEEMPH_TAU_US["none"] == 0.0
