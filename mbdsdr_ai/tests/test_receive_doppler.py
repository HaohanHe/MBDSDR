# SPDX-License-Identifier: MIT
"""接收链接入多普勒补偿的接线测试（残余偏移 / 开关 / 无目标 / 切换 / 失败）。

这些用例只证明一件事：补偿**真的长在接收链里**（抽取之前的基带 NCO），
而不是只有 ``remove_doppler_shift`` 在验证脚本里被调用。口径：

  * 已知频偏合成信号 → 补偿后残余偏移可量化（CW BFO 差拍出音频峰）；
  * 无目标 → 补偿量恒 0，读数明确写「无目标，未补偿」，输出与未补偿逐点一致；
  * 目标切换 → 频移按 ramp 渐变、相位跨块连续（无频跳 / 无爆音）；
  * 预测失败 → 抛 ``DopplerCompensationError``，SDRBackend 走
    ``doppler_compensation_failed`` 且本块音频跳过（不按 0 假装补偿）。

无硬件、无网络：全部合成 IQ，``ReceiveChain`` 直接跑。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.doppler_compensation import (DopplerCompensationError,
                                            DopplerCompensationStage)
from mbdsdr_ai.receive_chain import ReceiveChain
from mbdsdr_ai.sdr_backend import SDRBackend

FS = 48000.0


class _FakeSource:
    """最小预测源：只 duck-type 出接收链补偿级会调的方法。"""

    def __init__(self, **state):
        self._st = {"target_name": "", "enabled": False, "valid": False,
                    "doppler_hz": None, "application": "baseband",
                    "backend_comp_active": False, "reason": "未绑定卫星目标"}
        self._st.update(state)
        self.refreshes = 0

    def reading(self) -> dict:
        return dict(self._st)

    def set_enabled(self, on: bool) -> bool:
        self._st["enabled"] = bool(on)
        return True

    def set_application(self, application: str) -> bool:
        self._st["application"] = str(application)
        return True

    def refresh(self) -> bool:
        self.refreshes += 1
        return True


def _tone(n: int, f_hz: float, fs: float = FS) -> np.ndarray:
    return np.exp(2j * np.pi * f_hz * np.arange(n) / fs)


def _audio_peak_hz(audio: np.ndarray, fs: float = FS) -> float:
    """取稳态段频谱峰（跳过补偿 ramp / 滤波器瞬态）。"""
    a = np.asarray(audio, dtype=np.float64)
    a = a[len(a) // 2:]
    if a.size == 0:
        return float("nan")
    spec = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    freqs = np.fft.rfftfreq(a.size, 1.0 / fs)
    return float(freqs[int(np.argmax(spec))])


def _inst_freq_hz(y: np.ndarray, fs: float = FS) -> np.ndarray:
    """由相邻样本相位差估计瞬时频移（y = exp(-jφ) → 取负号还原 +f）。"""
    d = np.angle(y[1:] * np.conj(y[:-1]))
    return -np.unwrap(d) * fs / (2.0 * np.pi)


# ---------------------------------------------------------------------------
# 1) 已知频偏：残余偏移可量化
# ---------------------------------------------------------------------------
def test_receive_chain_removes_known_offset():
    """合成 +340 Hz 频偏信号，预测源报 300 Hz → 残余 +40 Hz。

    CW 解调用 800 Hz BFO 差拍，音频峰 = BFO + 残余 → 840 Hz。
    未接补偿时该音会被 200 Hz CW 信道滤波器挡掉/衰减，所以峰能证明
    「NCO 在抽取之前先把偏移搬回去了」。
    """
    chain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)
    src = _FakeSource(target_name="TEST-1", enabled=True, valid=True,
                      doppler_hz=300.0)
    chain.bind_doppler_source(src)
    assert chain.set_doppler_compensation(True) is True

    audio = chain.process(_tone(int(FS * 2), 340.0))
    peak = _audio_peak_hz(audio)
    assert peak == pytest.approx(840.0, abs=5.0), f"残余应约 +40 Hz，实测峰 {peak}"

    st = chain.doppler_status()
    assert st["enabled"] is True
    assert st["active"] is True
    assert st["applied_hz"] == pytest.approx(300.0, abs=1.0)


def test_receive_chain_full_compensation_lands_on_bfo():
    """预测源报的偏移与信号实际偏移一致时，残余 ≈ 0（音频峰回到 800 Hz）。"""
    chain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)
    src = _FakeSource(target_name="TEST-1", enabled=True, valid=True,
                      doppler_hz=340.0)
    chain.bind_doppler_source(src)
    assert chain.set_doppler_compensation(True) is True

    peak = _audio_peak_hz(chain.process(_tone(int(FS * 2), 340.0)))
    assert peak == pytest.approx(800.0, abs=5.0), f"实测峰 {peak}"


# ---------------------------------------------------------------------------
# 2) 无目标：补偿 0 + 明确标注
# ---------------------------------------------------------------------------
def test_unbound_chain_reads_zero_with_explicit_reason():
    chain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)
    st = chain.doppler_status()
    assert st["enabled"] is False and st["active"] is False
    assert st["applied_hz"] == 0.0
    assert "无目标，未补偿" in st["reason"]


def test_no_target_enabled_but_not_compensating_and_output_identity():
    """开启补偿但目标无效：仍 0 补偿，输出与未接补偿的链逐点一致。"""
    plain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)

    chain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)
    src = _FakeSource(enabled=True, valid=False, doppler_hz=None,
                      target_name="")
    chain.bind_doppler_source(src)
    assert chain.set_doppler_compensation(True) is True

    x = _tone(4096, 0.0)
    a = chain.process(x)
    b = plain.process(x)
    st = chain.doppler_status()
    assert st["enabled"] is True and st["active"] is False
    assert st["applied_hz"] == 0.0
    assert "无目标，未补偿" in st["reason"]
    assert np.array_equal(a, b), "零补偿必须严格直通（不改相位、不改样本）"


def test_backend_side_compensation_blocks_enable():
    """后端（C++ 1Hz ZOH/基带）已补偿时拒绝开启，并给冲突原因（禁止双倍偏移）。"""
    src = _FakeSource(backend_comp_active=True, enabled=True, valid=True,
                      doppler_hz=500.0)
    stage = DopplerCompensationStage(src)
    assert stage.set_enabled(True) is False
    st = stage.status()
    assert st["enabled"] is False
    assert st["applied_hz"] == 0.0
    assert "双倍偏移" in st["reason"] or "防止" in st["reason"]


# ---------------------------------------------------------------------------
# 3) 目标切换：渐变 + 相位连续（无频跳 / 无爆音）
# ---------------------------------------------------------------------------
def test_target_switch_ramps_without_frequency_or_phase_jump():
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=400.0)
    stage = DopplerCompensationStage(src, ramp_s=0.020)
    assert stage.set_enabled(True) is True

    block = int(0.1 * FS)                       # 0.1 s / 块
    x = np.ones(block, dtype=np.complex128)
    y1 = stage.process(x, FS)
    assert stage.status()["applied_hz"] == pytest.approx(400.0, abs=1e-6)

    src._st["doppler_hz"] = -400.0               # 目标切换
    # 先喂一小块：确认渐变仍在进行（settling），再喂完剩余
    y2a = stage.process(x[:256], FS)
    assert stage.status()["settling"] is True
    assert abs(stage.status()["applied_hz"]) < 400.0     # 还没跳到位
    y2b = stage.process(x[256:], FS)
    st = stage.status()
    assert st["settling"] is False               # 整块结束后渐变完成
    assert st["applied_hz"] == pytest.approx(-400.0, abs=1e-6)

    y = np.concatenate([y1, y2a, y2b])
    inst = _inst_freq_hz(y)
    # 边界瞬时频移连续：无频跳（相邻两点差 ≤ 1 Hz）
    assert abs(inst[block] - inst[block - 1]) < 1.0
    assert inst[block - 1] == pytest.approx(400.0, abs=2.0)
    # 渐变约 ramp_s = 960 样本；正→负过零点应在切换后约 480 样本处
    after = inst[block:]
    zero = np.where(after < 0.0)[0]
    assert zero.size > 0
    assert 400 < zero[0] < 560, f"渐变过零位置异常：{zero[0]}"
    # 单样本相位差不超过 2π·400/fs（阶跃/爆音会显著超过）
    dphi = np.abs(np.angle(y[1:] * np.conj(y[:-1])))
    assert float(dphi.max()) < 2.0 * np.pi * 400.0 / FS * 1.05


def test_switch_off_glides_to_zero_and_keeps_phase_continuous():
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=500.0)
    stage = DopplerCompensationStage(src, ramp_s=0.020)
    assert stage.set_enabled(True) is True
    block = int(0.1 * FS)
    x = np.ones(block, dtype=np.complex128)
    y1 = stage.process(x, FS)
    assert stage.status()["applied_hz"] == pytest.approx(500.0, abs=1e-6)

    stage.set_enabled(False)
    y2 = stage.process(x, FS)
    st = stage.status()
    assert st["enabled"] is False
    assert st["applied_hz"] == pytest.approx(0.0, abs=1e-6)
    assert "补偿关闭" in st["reason"]

    y = np.concatenate([y1, y2])
    # 关闭后相位保留（无相位复位），边界仍连续
    dphi = np.abs(np.angle(y[1:] * np.conj(y[:-1])))
    assert abs(_inst_freq_hz(y)[block] - _inst_freq_hz(y)[block - 1]) < 15.0
    assert float(dphi.max()) < 2.0 * np.pi * 500.0 / FS * 1.05


# ---------------------------------------------------------------------------
# 4) 失败：抛错而不是静默按 0；后端读数/错误码如实
# ---------------------------------------------------------------------------
def test_prediction_exception_raises_compensation_error():
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=100.0)
    stage = DopplerCompensationStage(src, source_refresh_s=0.0)
    assert stage.set_enabled(True) is True

    def _boom():
        raise RuntimeError("TLE 服务挂了（stub）")

    src.reading = _boom
    with pytest.raises(DopplerCompensationError):
        stage.process(np.ones(256, dtype=np.complex128), FS)
    assert "多普勒补偿失败" in stage.status()["error"]


def test_non_finite_prediction_is_refused_not_zeroed():
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=100.0)
    stage = DopplerCompensationStage(src, source_refresh_s=0.0)
    assert stage.set_enabled(True) is True
    src._st["doppler_hz"] = float("nan")
    with pytest.raises(DopplerCompensationError):
        stage.process(np.ones(256, dtype=np.complex128), FS)


def test_backend_skips_block_and_reports_error_code(monkeypatch):
    """SDRBackend 遇补偿失败：本块音频 None、读数带错误、status.error 有话术。"""
    backend = SDRBackend(object())
    backend.status.connected = True
    backend.status.sample_rate_hz = FS
    backend.status.demod_mode = "CW"

    chain = ReceiveChain(fs_in=FS, mode="CW", squelch_db=-150.0)
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=100.0)
    chain.bind_doppler_source(src)
    chain.set_doppler_compensation(True)
    backend._receive_chain = chain

    def _boom():
        raise RuntimeError("预测源异常（stub）")

    src.reading = _boom
    monkeypatch.setattr(backend, "read_samples",
                        lambda n: np.ones(4096, dtype=np.complex128))

    assert backend.read_audio(4096) is None
    st = backend.get_doppler_compensation_status()
    assert "多普勒补偿失败" in st["error"]
    assert "多普勒补偿失败" in backend.status.error
    assert "未按 0 补偿" in backend.status.error
