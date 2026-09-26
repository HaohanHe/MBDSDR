#!/usr/bin/env python3
"""
VFO offset 端到端 DDC 测试
==========================

目的
----
证明 mbdsdr_ai.dsp.VFO 的 ``offset`` 参数真的驱动了数字下变频（DDC）搬频，
而不是恒为 0。重构前的主循环里 offset 链路可能是"接了但没生效"——本测试
从信号源到解调输出整条链路上钉死这个行为。

信号模型
--------
用 tests/fake_rtl.FakeRtlSdr 生成一段窄带 FM 复基带 IQ：

    IQ_baseband(t) = exp(j · φ(t)),
    φ(t) = -(deviation/mod_freq) · cos(2π·mod_freq·t)
    瞬时频偏 = deviation · sin(2π·mod_freq·t)

    mod_freq   = 1000 Hz（调制音）
    deviation = 300 Hz（窄带 FM，β=0.3，卡森带宽 ≈ 2·(300+1000)=2.6 kHz）

FakeRtlSdr 加了 signal_offset_hz 参数：吐基带前用 NCO
    IQ(t) = IQ_baseband(t) · exp(j · 2π·signal_offset_hz · t)
把"在 DC 的电台"搬到 +100 kHz 处。这模拟真实硬件：RTL-SDR tune 到中心
频率（DC），但"想听的台"落在中心右侧 +100 kHz。VFO offset=+100 kHz
应当用反向 NCO 把它搬回 DC。

为什么 deviation 用 300 Hz 而不是广播 FM 的 75 kHz：
    VFO 带宽只有几 kHz。75 kHz 频偏的 WFM 信号会瞬间频偏甩出通带，鉴频
    输出被滤波器削成断续噪声，无法稳定测 1 kHz 谱峰。窄带 FM（300 Hz）
    能量全在通带内，数学自洽，同样能钉死"搬频→鉴频→1 kHz 音"链路。

实测基线（本机 scipy 1.17，in_sr=2.048M，out_sr=48k，up=3/down=128）
----------------------------------------------------------------------
  offset=+100k, bw=48k(无 VFO LPF)：
    复输出载波峰在 0 Hz（~+40 dB），FM 边带在 ±1 kHz（~+24 dB）。
  offset=0, bw=48k：
    复输出全频带是噪声（~-48 dB），DC 处没有峰——
    scipy resample_poly 的抗混叠滤波器本就把偏离 DC 的输入信号压成噪声。
  这意味着对照实验不需要额外设计：offset 对了信号就在 DC，offset 错了
  信号就消失。本测试所有断言都落在这个实测基线上，留足容差。

测试矩阵
--------
1. test_offset_100k_centers_signal_at_dc
   宽带宽（不挂 VFO LPF）下，offset=+100k 的复输出谱峰在 DC，DC 带内
   功率占主导。
2. test_offset_zero_has_no_dc_peak
   同样输入，offset=0 的复输出 DC 处没有峰（噪声级）。
3. test_centered_vs_uncentered_dc_power_ratio
   两者 DC 带内功率差 >20 dB——直接量化"搬频生效 vs 没生效"。
4. test_correct_offset_demods_1khz_tone
   窄带 VFO（bw=3600），offset=+100k 鉴频出 1 kHz 调制音（谱峰 900~1100 Hz）。
5. test_zero_offset_control_has_no_1khz_tone
   同样输入，offset=0 鉴频谱峰不在 1 kHz（噪声/残余，随机频）。
6. test_complex_band_power_20db_diff
   窄带 VFO 复输出在 ±[900,1100] Hz 边带带内功率，offset=+100k 比
   offset=0 高 >20 dB。
7. test_vfo_manager_push_offset_chain
   VfoManager.add → bind_dsp → push_offset(100000) 完整链路。
8. test_two_parallel_vfos_lock_onto_different_stations
   同一段组合 IQ 里塞两个台（+100k / -150k），两个 VFO 各自把自己的
   台搬回 DC，互不串扰。

红线：不依赖真实硬件/声卡/射频源；FakeRtl 只在 tests/ 使用；固定种子。
"""

import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (REPO_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import dsp
from mbdsdr_ai.vfo_manager import VfoManager
from fake_rtl import FakeRtlSdr  # tests/ 专属双棒，绝不进 mbdsdr_ai/desktop/


# ═══════════════════════════════════════════════════════
# 公共参数
# ═══════════════════════════════════════════════════════
IN_SR = 2_048_000          # FakeRtlSdr 默认采样率
OUT_SR = 48_000            # VFO 输出 / 音频采样率
MOD_FREQ = 1000.0          # 调制音
DEVIATION = 300.0          # 窄带 FM 频偏（通带内）
SIGNAL_OFFSET = 100_000.0  # 电台相对中心频率的位置（+100 kHz）
NARROW_BW = 3600.0         # 窄带 VFO 带宽（LPF 截止 1800 Hz）
# 读 1 秒 = 2_048_000 输入样本 → ≈48000 输出样本，FFT 分辨率 1 Hz
N_READ = 2_048_000


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def _make_fake(signal_offset_hz: float = SIGNAL_OFFSET,
               deviation: float = DEVIATION) -> FakeRtlSdr:
    """构造一支吐窄带 FM 的假棒；signal_offset_hz 控制电台离 DC 多远。"""
    return FakeRtlSdr(
        fm_carrier_hz=98.5e6,
        mod_freq=MOD_FREQ,
        deviation=deviation,
        noise_amplitude=0.01,
        signal_offset_hz=signal_offset_hz,
        seed=20260926,
    )


def _complex_power_db(iq: np.ndarray, sr: float, lo_hz: float, hi_hz: float) -> float:
    """复 IQ 在 [lo_hz, hi_hz] 内的平均功率（dBFS，相对单位幅度）。"""
    x = np.asarray(iq, dtype=np.complex128)
    n = x.size
    spec = np.abs(np.fft.fftshift(np.fft.fft(x))) ** 2 / n
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sr))
    mask = (freqs >= lo_hz) & (freqs <= hi_hz)
    p = float(np.mean(spec[mask])) if mask.any() else 0.0
    return 10.0 * np.log10(p + 1e-12)


def _complex_peak(iq: np.ndarray, sr: float):
    """复 IQ 双边带 FFT：返回 (峰值频率 Hz, 峰值幅度)。"""
    x = np.asarray(iq, dtype=np.complex128)
    n = x.size
    win = np.hanning(n)
    spec = np.abs(np.fft.fftshift(np.fft.fft(x * win)))
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sr))
    idx = int(np.argmax(spec))
    return float(freqs[idx]), float(spec[idx])


def _audio_peak_freq(audio: np.ndarray, sr: float) -> float:
    """实音频 rFFT：返回最大谱峰频率（跳过 DC bin）。"""
    a = np.asarray(audio, dtype=np.float64)
    n = a.size
    if n < 8:
        return 0.0
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(a * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    idx = int(np.argmax(spec[1:]) + 1)
    return float(freqs[idx])


def _run_vfo(offset: float, bandwidth: float):
    """读 1 秒假棒 IQ，过 VFO，返回 (复输出 IQ)。"""
    fake = _make_fake(signal_offset_hz=SIGNAL_OFFSET)
    iq = fake.read_samples(N_READ)
    fake.close()
    vfo = dsp.VFO(IN_SR, OUT_SR, bandwidth=bandwidth, offset=offset)
    return vfo.process(iq)


# ═══════════════════════════════════════════════════════
# TC1~3: 复频域直接看 DDC 把信号搬到了哪（宽带宽，不挂 VFO LPF）
# ═══════════════════════════════════════════════════════

class TestTC1ComplexDomainCentering:
    """宽带宽 VFO（bandwidth=out_sr → filter_needed=False，不挂 LPF）。

    offset=+100k：NCO 把 +100k 信号搬到 DC，复输出谱峰在 0 Hz。
    offset=0   ：信号留在 +100k，被 resample_poly 的抗混叠滤波器压成噪声，
                 复输出 DC 处没有峰。
    """

    def test_offset_100k_centers_signal_at_dc(self):
        out = _run_vfo(offset=SIGNAL_OFFSET, bandwidth=OUT_SR)
        peak_freq, _ = _complex_peak(out, OUT_SR)
        assert abs(peak_freq) <= 200.0, (
            f"offset=+100k 时 VFO 输出谱峰在 {peak_freq:.1f} Hz，"
            f"应在 DC 附近（|f|<=200 Hz）——DDC 搬频没生效？")
        # DC 带内功率应占主导（实测 ~40 dB，噪声 ~-48 dB）
        dc_db = _complex_power_db(out, OUT_SR, -200.0, 200.0)
        assert dc_db > -10.0, (
            f"DC 带内功率 {dc_db:.1f} dB 过低（>-10 dB 才对），"
            f"DC 处没有强信号")

    def test_offset_zero_has_no_dc_peak(self):
        out = _run_vfo(offset=0.0, bandwidth=OUT_SR)
        dc_db = _complex_power_db(out, OUT_SR, -200.0, 200.0)
        # offset=0：信号没被搬到 DC，DC 处只有噪声（实测 ~-48 dB）
        assert dc_db < -20.0, (
            f"offset=0 时 DC 带内功率 {dc_db:.1f} dB 偏高（应<-20 dB），"
            f"说明信号没被压成噪声——对照基线错了")

    def test_centered_vs_uncentered_dc_power_ratio(self):
        out_on = _run_vfo(offset=SIGNAL_OFFSET, bandwidth=OUT_SR)
        out_off = _run_vfo(offset=0.0, bandwidth=OUT_SR)
        p_on = _complex_power_db(out_on, OUT_SR, -200.0, 200.0)
        p_off = _complex_power_db(out_off, OUT_SR, -200.0, 200.0)
        diff = p_on - p_off
        assert diff > 20.0, (
            f"DC 带内功率：offset=+100k {p_on:.1f} dB vs "
            f"offset=0 {p_off:.1f} dB，差值仅 {diff:.1f} dB（应>20 dB）——"
            f"offset 参数可能没接到 NCO 上")


# ═══════════════════════════════════════════════════════
# TC4~6: 窄带 VFO + 鉴频
# ═══════════════════════════════════════════════════════

class TestTC2NarrowBandDemod:
    """窄带 VFO（bw=3600，LPF 截止 1800 Hz）。

    offset=+100k：信号搬到 DC，落在通带内 → 鉴频出 1 kHz 调制音。
    offset=0   ：信号被压成噪声 → 鉴频输出是噪声，谱峰不在 1 kHz。
    """

    def _demod(self, offset: float):
        out = _run_vfo(offset=offset, bandwidth=NARROW_BW)
        audio = dsp.fm_demod(out, deviation=DEVIATION, sample_rate=OUT_SR)
        return out, audio

    def test_correct_offset_demods_1khz_tone(self):
        out, audio = self._demod(offset=SIGNAL_OFFSET)
        assert audio.size > 0, "解调输出为空"
        peak = _audio_peak_freq(audio, OUT_SR)
        assert 900.0 <= peak <= 1100.0, (
            f"offset=+100k 解调音频谱峰在 {peak:.1f} Hz，"
            f"应落在调制音 1000 Hz（900~1100 Hz）——搬频后没鉴出调制音")

    def test_zero_offset_control_has_no_1khz_tone(self):
        out, audio = self._demod(offset=0.0)
        peak = _audio_peak_freq(audio, OUT_SR)
        assert not (900.0 <= peak <= 1100.0), (
            f"offset=0（不搬频）时解调音频谱峰居然在 {peak:.1f} Hz，"
            f"落在 1 kHz 附近——对照实验不成立")

    def test_complex_band_power_20db_diff(self):
        out_on, _ = self._demod(offset=SIGNAL_OFFSET)
        out_off, _ = self._demod(offset=0.0)
        # FM 边带对称分布在 ±mod_freq 处，两侧一起算
        band_on = max(_complex_power_db(out_on, OUT_SR, 900.0, 1100.0),
                      _complex_power_db(out_on, OUT_SR, -1100.0, -900.0))
        band_off = max(_complex_power_db(out_off, OUT_SR, 900.0, 1100.0),
                       _complex_power_db(out_off, OUT_SR, -1100.0, -900.0))
        diff = band_on - band_off
        assert diff > 20.0, (
            f"±1 kHz 边带带内功率：offset=+100k {band_on:.1f} dB vs "
            f"offset=0 {band_off:.1f} dB，差值仅 {diff:.1f} dB（应>20 dB）")


# ═══════════════════════════════════════════════════════
# TC7: VfoManager → bind_dsp → push_offset 完整链路
# ═══════════════════════════════════════════════════════

class TestTC3VfoManagerChain:
    """证明从配置层 VfoManager 到 DSP VFO 的调用链通：
    构造时 offset=0，push_offset(100000) 之后才真的 set_offset。"""

    def test_push_offset_reaches_dsp_vfo_and_demod_works(self):
        mgr = VfoManager()
        v = mgr.add(center_hz=98.5e6, bw_hz=NARROW_BW, mode="FM",
                    inherit_last=False)

        # DSP 层 VFO 先以 offset=0 构造（模拟主循环建链时的初始状态）
        dsp_vfo = dsp.VFO(IN_SR, OUT_SR, bandwidth=NARROW_BW, offset=0.0)
        bound = mgr.bind_dsp(v.vfo_id, dsp_vfo, output_stream=None)
        assert bound, "bind_dsp 失败"
        assert dsp_vfo._offset == pytest.approx(0.0)

        # 现在才把用户调谐的 +100k 推下去
        ok = mgr.push_offset(v.vfo_id, SIGNAL_OFFSET)
        assert ok, "push_offset 返回 False（未绑定或无 set_offset 方法）"
        assert dsp_vfo._offset == pytest.approx(SIGNAL_OFFSET), (
            f"push_offset 后 dsp_vfo._offset={dsp_vfo._offset}，"
            f"应为 {SIGNAL_OFFSET}——set_offset 没被调到")

        # 走一遍真实 process→demod，确认 1 kHz 音回来了
        fake = _make_fake(signal_offset_hz=SIGNAL_OFFSET)
        iq = fake.read_samples(N_READ)
        out = dsp_vfo.process(iq)
        fake.close()
        audio = dsp.fm_demod(out, deviation=DEVIATION, sample_rate=OUT_SR)
        peak = _audio_peak_freq(audio, OUT_SR)
        assert 900.0 <= peak <= 1100.0, (
            f"VfoManager 链路解调音频谱峰在 {peak:.1f} Hz，"
            f"应在 1 kHz（900~1100 Hz）——push_offset 没真生效")

    def test_push_offset_without_bind_returns_false(self):
        mgr = VfoManager()
        v = mgr.add(center_hz=98.5e6, bw_hz=NARROW_BW, mode="FM",
                    inherit_last=False)
        assert mgr.push_offset(v.vfo_id, 100_000.0) is False


# ═══════════════════════════════════════════════════════
# TC8: 双 VFO 并行，各自对准不同偏移
# ═══════════════════════════════════════════════════════

class TestTC4DualParallelVFO:
    """同一段组合 IQ 里塞两个台（+100k 和 -150k），两个 VFO 各 process 一遍。
    宽带宽（不挂 LPF），直接看各自输出复谱里 DC 处有没有峰。"""

    def _combo_iq(self):
        """读一段 DC 处的窄带 FM，手动 NCO 拆成两个台叠加。"""
        fake = _make_fake(signal_offset_hz=0.0)
        base = fake.read_samples(N_READ)
        fake.close()
        n = base.size
        t = np.arange(n) / IN_SR
        station_a = base * np.exp(1j * 2.0 * np.pi * 100_000.0 * t)    # +100k
        station_b = base * np.exp(1j * 2.0 * np.pi * (-150_000.0) * t)  # -150k
        return (station_a + station_b).astype(np.complex64)

    def _dc_is_peak(self, iq: np.ndarray) -> bool:
        """输出复 IQ 的 DC 带内功率是否为全频带主峰。"""
        dc = _complex_power_db(iq, OUT_SR, -200.0, 200.0)
        # 非 DC 参考带：±[2k,6k]（信号被搬偏后会落在这里被压成噪声）
        ref = max(
            _complex_power_db(iq, OUT_SR, 2000.0, 6000.0),
            _complex_power_db(iq, OUT_SR, -6000.0, -2000.0),
        )
        return dc > ref

    def test_each_vfo_centers_its_own_station(self):
        combo = self._combo_iq()
        vfo_a = dsp.VFO(IN_SR, OUT_SR, bandwidth=OUT_SR, offset=100_000.0)
        vfo_b = dsp.VFO(IN_SR, OUT_SR, bandwidth=OUT_SR, offset=-150_000.0)

        out_a = vfo_a.process(combo)   # 应把 +100k 台搬到 DC
        out_b = vfo_b.process(combo)   # 应把 -150k 台搬到 DC

        assert self._dc_is_peak(out_a), (
            "VFO_A(offset=+100k) 输出复谱 DC 处不是主峰——"
            "+100k 台没被搬回 DC")
        assert self._dc_is_peak(out_b), (
            "VFO_B(offset=-150k) 输出复谱 DC 处不是主峰——"
            "-150k 台没被搬回 DC")

    def test_zero_offset_control_fails_to_center(self):
        """对照：offset=0 的 VFO 处理同一段组合 IQ，DC 处不该是主峰。"""
        combo = self._combo_iq()
        vfo_idle = dsp.VFO(IN_SR, OUT_SR, bandwidth=OUT_SR, offset=0.0)
        out_idle = vfo_idle.process(combo)
        assert not self._dc_is_peak(out_idle), (
            "offset=0 的 VFO 输出 DC 处居然是主峰——对照基线错了")


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("Test")]
    failed = 0
    for cls_name, cls in fns:
        inst = cls()
        for m in sorted(dir(inst)):
            if m.startswith("test_"):
                try:
                    getattr(inst, m)()
                    print(f"PASS {cls_name}::{m}")
                except Exception as e:
                    failed += 1
                    print(f"FAIL {cls_name}::{m}: {e!r}")
    sys.exit(1 if failed else 0)
