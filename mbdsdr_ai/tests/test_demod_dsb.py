# SPDX-License-Identifier: MIT
"""DSB-SC 乘积检波测试（SDR++ 审计缺口 #1，2026-10-14 销账）。

核心断言两条：
1) 机制：DSB-SC 信号（无载波）上 AM 包络检波只能给出常数（直流），乘积
   检波才能还原双边带音频——这是两种解调的物理分界；
2) 保真：乘积检波输出频谱峰值落在调制音频频率上。
"""
import numpy as np

from mbdsdr_ai.demod_dsb import DemodDSB
from mbdsdr_ai.receive_chain import ReceiveChain


def _tone_iq(amp: float, freq_hz: float, n: int, fs: float) -> np.ndarray:
    """复基带单音（DSB-SC 无载波双边带的等效表示）：A*exp(j*2*pi*f*t)。"""
    t = np.arange(n) / fs
    return amp * np.exp(2j * np.pi * freq_hz * t)


def _dominant_freq(x: np.ndarray, fs: float) -> float:
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1.0 / fs)[int(np.argmax(sp))])


def test_product_detector_recovers_tone():
    fs, n = 15000.0, 16384
    d = DemodDSB(if_sr=fs, bandwidth=10000.0)
    out = d.process(_tone_iq(0.5, 1000.0, n, fs))
    # 丢前 2000 点瞬态
    body = out[2000:]
    peak = _dominant_freq(body, fs)
    assert abs(peak - 1000.0) < 30.0, f"峰值频率 {peak} 不在 1kHz 附近"
    # 乘积检波增益 2：输出幅度 ≈ 2*0.5 = 1.0
    amp = float(np.max(np.abs(body)))
    assert 0.8 < amp < 1.2, f"输出幅度 {amp:.3f} 不符合 2A 预期"


def test_am_envelope_fails_where_dsb_succeeds():
    """机制分界：同一 DSB-SC 信号，包络检波只见常数，乘积检波听见音频。"""
    from mbdsdr_ai.demod_am import DemodAM

    fs, n = 15000.0, 16384
    iq = _tone_iq(0.5, 1000.0, n, fs)

    am = DemodAM(if_sr=fs, bandwidth=10000.0).process(iq)[2000:]
    dsb = DemodDSB(if_sr=fs, bandwidth=10000.0).process(iq)[2000:]

    am_peak = _dominant_freq(am, fs)
    dsb_peak = _dominant_freq(dsb, fs)
    # AM 包络 = 常数 -> 频谱能量集中在 ~0Hz；DSB 峰值在 1kHz
    assert am_peak < 50.0
    assert abs(dsb_peak - 1000.0) < 30.0


def test_state_reset_is_deterministic():
    fs, n = 15000.0, 8192
    iq = _tone_iq(0.5, 1000.0, n, fs)
    d = DemodDSB(if_sr=fs, bandwidth=10000.0)
    a = d.process(iq)
    d.reset()
    b = d.process(iq)
    assert np.allclose(a, b)


def test_zero_input_gives_near_silence():
    d = DemodDSB(if_sr=15000.0, bandwidth=10000.0)
    out = d.process(np.zeros(4096, dtype=complex))
    assert float(np.max(np.abs(out))) < 1e-3


# ---------------------------------------------------------------------------
# 链路接线（ReceiveChain mode=DSB）
# ---------------------------------------------------------------------------

def test_chain_constructs_dsb_and_stays_mono():
    ch = ReceiveChain(fs_in=2_048_000.0, mode="DSB")
    assert ch.mode == "DSB"
    assert ch.is_stereo is False
    ch.set_mode("dsb")                      # 大小写不敏感
    assert ch.mode == "DSB"
    ch.set_mode("AM")                        # 切回后仍可再切回来
    assert ch.mode == "AM"
    ch.set_mode("DSB")
    assert ch.mode == "DSB"


def test_chain_dsb_process_returns_mono_float():
    ch = ReceiveChain(fs_in=2_048_000.0, mode="DSB")
    iq = _tone_iq(0.5, 1000.0, 65536, 2_048_000.0)
    out = ch.process(iq)
    assert out.ndim == 1
    assert out.dtype == np.float32
    assert len(out) > 100


def test_controller_accepts_dsb_mode():
    from mbdsdr_ai.core.controller import VALID_MODES, SDRController
    assert "DSB" in VALID_MODES
    c = SDRController()
    c.set_demod("DSB")
    assert c.get_demod() == "DSB"
    # 非法模式仍被诚实拒绝
    from mbdsdr_ai.core.errors import SDRUserError
    try:
        c.set_demod("NOT_A_MODE")
        assert False, "未知模式必须拒绝"
    except SDRUserError:
        pass
