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

from mbdsdr_ai import doppler_link, orbit
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
                    "backend_comp_active": False,
                    "python_stream_owner": True,
                    "reason": "未绑定卫星目标"}
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
    # 诚实读数：后端在施加时 Python 不知道实际值，不许用预测值冒充
    assert st["applied_by"] == "backend"
    assert st["applied_hz"] is None
    assert st["python_applied_hz"] == 0.0
    assert st["predicted_hz"] == pytest.approx(500.0)
    assert "双倍偏移" in st["reason"] or "防止" in st["reason"]


def test_unclaimed_stream_refuses_baseband_by_default():
    """默认保守：没有显式声明「Python 独占 IQ 流」时，基带补偿拒绝开启。

    对应 C++ 桌面/远端 WebSocket 形态：本地没有 SDRBackend，因此没有
    claim；宁可不补偿，也不在可能已被后端补偿的流上再叠一次。
    """
    src = _FakeSource(python_stream_owner=False, enabled=True, valid=True,
                      doppler_hz=500.0)
    stage = DopplerCompensationStage(src)
    assert stage.set_enabled(True) is False
    st = stage.status()
    assert st["enabled"] is False and st["active"] is False
    assert st["applied_by"] == "none"
    assert st["applied_hz"] == 0.0
    assert "未声明 Python 独占 IQ 流" in st["reason"]
    # 跑一段也不许偷偷施加
    y = stage.process(np.ones(1024, dtype=np.complex128), FS)
    assert np.array_equal(y, np.ones(1024, dtype=np.complex128))
    assert stage.status()["python_applied_hz"] == 0.0


def test_backend_takeover_ramps_python_to_zero_leaving_one_times_offset():
    """双施加反证：后端接管后 Python 归零，残余只剩后端的 1 倍偏移。

    合成 500 Hz 偏移信号：Python 先按 500 Hz 补偿到 0；后端随后声明已在
    补偿，Python 侧必须在 ramp 内退回 0（不继续抵消）。最终残余应是
    **500 Hz（1 倍）**，而不是 1000 Hz（双倍）或 0（Python 仍在抵消）。
    """
    src = _FakeSource(target_name="A", enabled=True, valid=True,
                      doppler_hz=500.0)
    stage = DopplerCompensationStage(src, ramp_s=0.020)
    assert stage.set_enabled(True) is True

    block = int(0.1 * FS)
    x = _tone(block, 500.0)
    y1 = stage.process(x, FS)
    # Python 正把 500 Hz 抵消掉：输出近似 DC
    inst1 = _inst_freq_hz(y1)
    assert abs(float(np.median(inst1[-1000:]))) < 5.0

    src._st["backend_comp_active"] = True
    y2 = stage.process(x, FS)   # 后端接管：Python 在 ramp 内退回 0
    st = stage.status()
    assert st["applied_by"] == "backend"
    assert st["applied_hz"] is None
    assert st["python_applied_hz"] == pytest.approx(0.0, abs=1e-6)

    inst2 = _inst_freq_hz(y2)
    residual = abs(float(np.median(inst2[-1000:])))
    assert residual == pytest.approx(500.0, abs=5.0), \
        f"最终残余应为 1 倍（500 Hz），实测 {residual}"


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


# ---------------------------------------------------------------------------
# 5) 真实 TLE 过境全程：多普勒由正扫到负，1 s 步进跑满 10 分钟
# ---------------------------------------------------------------------------
# ISS(25544) 真实 TLE（2024-11-20 历元），测试内嵌、离线传播，不联网。
# 下面这条过境在该 TLE 下是近天顶过境（TCA 仰角 86.3°），10 分钟窗口两端
# 仰角仍 >1.5°（全程都在过境中），多普勒从 +3358 Hz 扫到 −3361 Hz。
_ISS_TLE_L1 = ("1 25544U 98067A   24325.51782528  .00011346  00000+0  20438-3"
               " 0  9998")
_ISS_TLE_L2 = ("2 25544  51.6395 208.5764 0005070  89.6109  37.8554 "
               "15.50253765482866")
_PASS_TCA_UNIX = 1732181540.104192      # 该 TLE 下示例站点 (0,0) 的过境 TCA
_PASS_SITE = (0.0, 0.0, 0.0)            # 中性示例站点（不内置真实城市坐标）
_DOWNLINK_HZ = 145_800_000.0            # 业余卫星 2 m 下行标称
_PASS_FS = 8000.0
_PASS_WARMUP_S = 5                      # 起始 ramp 瞬态预热（不计入残余峰值）
_PASS_BLOCKS = 600                      # 10 分钟，1 s / 块
# 残余峰值阈值 80 Hz。
# 依据：1 s ZOH 的目标更新间隔 × 该过境窗口内实测最大多普勒斜率
# 59.3 Hz/s（SGP4 真轨迹，TCA 近天顶处）= 59.3 Hz 一阶滞后误差上界；
# 20 ms ramp 额外引入 ≤ 斜率×0.02 s ≈ 1.2 Hz；再留 ~30% 余量取整到 80 Hz。
_RESIDUAL_LIMIT_HZ = 80.0


class _OrbitTracker:
    """``DopplerLinkCompensator`` 的最小 tracker 适配：走 orbit 公共 API 真传播。"""

    def __init__(self, when: float = 0.0) -> None:
        self.when = float(when)

    def current_position(self, at_unix=None) -> dict:
        t = self.when if at_unix is None else float(at_unix)
        st = orbit.compute_satellite_state(
            "ISS (ZARYA)", _PASS_SITE[0], _PASS_SITE[1], _PASS_SITE[2], when=t)
        if not st:
            return {"valid": False}
        rr = float(st["range_rate_kms"])
        return {
            "valid": True,
            "elevation": float(st["elevation"]),
            "range_rate_km_s": rr,
            # 与 doppler_correction / DopplerLinkCompensator 同符号：远离为负
            "doppler_hz": -_DOWNLINK_HZ * rr / orbit.C_LIGHT_KMPS,
        }


def test_full_pass_residual_peak_and_no_pop_at_zero_crossing(monkeypatch):
    """真实 TLE 过境：10 分钟 × 1 s 步进，残余峰值 ≤ 80 Hz，零交叉无跳变/爆音。

    与固定 300/340 Hz 用例的区别：这里预测值每秒变一次（真实 ZOH 工况），
    覆盖正→负过零、近天顶最大斜率与出射阶段。判据：

      * 稳态残余峰值 ≤ 80 Hz（推导见上方常量注释）；
      * 逐样本相位差 ≤ 2π·100/fs（无相位断点 = 无爆音）；
      * 零交叉附近块边界瞬时频移连续（< 5 Hz 跳变）；
      * 最终 ``applied_by=python``，Python 实际施加量 ≈ 出射阶段预测值。
    """
    pytest.importorskip("sgp4")
    monkeypatch.setattr(
        orbit, "fetch_tle",
        lambda catnr, max_age_hours=24.0: (_ISS_TLE_L1, _ISS_TLE_L2))

    # 过境全程在窗口内：窗口两端仰角仍 > 0（否则预测会转 invalid，测试失真）
    for dt in (-300.0, -150.0, 0.0, 150.0, 300.0):
        st = orbit.compute_satellite_state(
            "ISS (ZARYA)", _PASS_SITE[0], _PASS_SITE[1], _PASS_SITE[2],
            when=_PASS_TCA_UNIX + dt)
        assert st is not None and st["elevation"] > 0.0, (
            f"窗口 dt={dt:+.0f}s 仰角 {None if st is None else st['elevation']}，"
            "应全程在过境中")

    tracker = _OrbitTracker(_PASS_TCA_UNIX - _PASS_WARMUP_S)
    comp = doppler_link.DopplerLinkCompensator(tracker=tracker)
    # 真实部署声明 + 基带施加点（与 SDRBackend 建链后的组合一致）
    assert comp.claim_python_stream(True) is True
    assert comp.arm_target("ISS (ZARYA)", _DOWNLINK_HZ,
                           at_unix=tracker.when) is True
    assert comp.set_application("baseband") is True

    stage = DopplerCompensationStage(comp, ramp_s=0.020)
    assert stage.set_enabled(True) is True

    fs = _PASS_FS
    n = int(fs)
    t0 = _PASS_TCA_UNIX - _PASS_BLOCKS / 2.0
    phase = 0.0
    residual_peaks = []
    boundary_jumps = []
    applied = []                 # 每块结束时的 Python 实际施加量
    max_dphi = 0.0
    prev_last = None
    crossing = None

    for i in range(-_PASS_WARMUP_S, _PASS_BLOCKS):
        t = t0 + float(i)
        tracker.when = t
        comp.refresh(at_unix=t)
        pred = comp.state.doppler_hz
        assert pred is not None, f"t={t} 预测失效（过境窗口不应越界）"
        nxt = tracker.current_position(at_unix=t + 1.0)["doppler_hz"]

        # 块内线性插值真轨迹，相位跨块连续（物理信号本身不会有断点）
        freq = np.linspace(pred, nxt, n, endpoint=False)
        ph = phase + (2.0 * np.pi / fs) * np.cumsum(freq)
        iq = np.exp(1j * ph)
        phase = float(ph[-1])

        out = stage.process(iq, fs)
        # 真实残余 = 真多普勒 − 施加量；_inst_freq_hz 对 +f 输入返回 -f
        inst = -_inst_freq_hz(out, fs)

        if (pred <= 0.0 < nxt) or (pred >= 0.0 > nxt):
            assert crossing is None, "过境窗口内应只有一次零交叉"
            crossing = i

        if i >= 0:
            # 跳过块首 20 ms ramp（160 样本），量稳态残余
            residual_peaks.append(float(np.max(np.abs(inst[int(0.020 * fs):]))))
            applied.append(float(stage.status()["applied_hz"]))
            dphi = np.abs(np.angle(out[1:] * np.conj(out[:-1])))
            max_dphi = max(max_dphi, float(dphi.max()))
            if prev_last is not None:
                boundary_jumps.append(abs(float(inst[0]) - prev_last))
            prev_last = float(inst[-1])

    assert crossing is not None and 0 <= crossing < _PASS_BLOCKS, \
        f"未找到零交叉：{crossing}"

    peak = max(residual_peaks)
    assert peak <= _RESIDUAL_LIMIT_HZ, (
        f"过境全程残余峰值 {peak:.1f} Hz 超过阈值 {_RESIDUAL_LIMIT_HZ} Hz；"
        f"所述 1 s ZOH 斜率上界约 59 Hz")

    # 无爆音：逐样本相位差有界（残余 ≤ 80 Hz 时不可能出现阶跃）
    assert max_dphi < 2.0 * np.pi * 100.0 / fs, \
        f"逐样本相位差 {max_dphi:.4f} rad 超界（相位断点/爆音）"

    # 零交叉处施加量连续：目标从 + 扫到 −，块间不得跳变
    near = [j for j in range(max(0, crossing - 3), min(len(boundary_jumps),
                                                      crossing + 4))]
    near_jumps = [boundary_jumps[j] for j in near
                  if j < len(boundary_jumps)]
    assert near_jumps and max(near_jumps) < 5.0, \
        f"零交叉附近块边界频移跳变 {near_jumps}"

    st = stage.status()
    assert st["applied_by"] == "python"
    assert st["python_stream_owner"] is True
    assert st["applied_hz"] == pytest.approx(applied[-1], abs=1e-6)
    # 出射阶段预测 ≈ −3361 Hz，Python 实际施加量应跟到同一量级
    assert st["applied_hz"] == pytest.approx(-3361.0, abs=60.0)
