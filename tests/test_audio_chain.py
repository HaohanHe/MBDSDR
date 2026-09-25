"""
解调 → 音频队列 链路测试
=========================

用数学上正确的合成调制信号证明整条接收链路通畅：

    合成 IQ → dsp.demodulate() → 音频块 → dsp.audio_to_playback() → AudioPlayer.write()

对照真实 SDR 软件的音频链路：
  - SDR++:  rx_vfo.h:89 process()（xlator→resamp→filter）
            stream.h:43 swap() / :70 read() / :78 flush()
  - GQRX:   receiver.cpp:49 DEFAULT_AUDIO_GAIN=-6.0
            receiver.cpp:122-124 audio_gain0/1 + set_af_gain()
            receiver.cpp:984-995 set_af_gain() dB→线性
            dockrxopt.ui:660 sqlSpinBox 默认 -150 dBFS

本测试不依赖任何真实硬件 / 声卡 / 射频源。沙箱无音频设备时
AudioPlayer 走安全降级路径（write 返回 0 但不崩），这正是要证明的。
"""

import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai import dsp
from mbdsdr_ai.audio_out import AudioPlayer


# ═══════════════════════════════════════════════════════
# 1. 合成信号生成夹具（数学正确，不用随机噪声冒充）
# ═══════════════════════════════════════════════════════

def make_fm_signal(duration_s, sample_rate, mod_freq, deviation):
    """积分相位法生成 FM 信号（载波在 DC）。

        IQ = exp(j * 2π * deviation * ∫ sin(2π*mod_freq*t) dt)
           = exp(j * -(deviation/mod_freq) * cos(2π*mod_freq*t))

    瞬时频偏 = deviation * sin(2π*mod_freq*t)，经正交鉴频后
    输出应在 mod_freq 处出峰。
    """
    n = int(round(duration_s * sample_rate))
    t = np.arange(n) / sample_rate
    phase = -(deviation / mod_freq) * np.cos(2.0 * np.pi * mod_freq * t)
    return np.exp(1j * phase).astype(np.complex64)


def make_am_signal(duration_s, sample_rate, mod_freq, depth=0.5):
    """AM 调幅信号（载波在 DC，载波+边带）。

        IQ = (1 + depth*sin(2π*mod_freq*t)) * exp(j*0)
    包络检波后输出 ∝ sin(2π*mod_freq*t)，峰值在 mod_freq。
    """
    n = int(round(duration_s * sample_rate))
    t = np.arange(n) / sample_rate
    return (1.0 + depth * np.sin(2.0 * np.pi * mod_freq * t)).astype(np.complex64)


def make_ssb_signal(duration_s, sample_rate, tone_freq, mode="USB"):
    """单边带单音信号。
        USB = 单音在 +tone_freq 处: exp(j*2π*tone_freq*t)
        LSB = 单音在 -tone_freq 处: exp(-j*2π*tone_freq*t)
    经 ssb_demod(BFO=1500) 后音频频率 = |tone_freq - 1500|。
    """
    n = int(round(duration_s * sample_rate))
    t = np.arange(n) / sample_rate
    if mode.upper() == "USB":
        return np.exp(1j * 2.0 * np.pi * tone_freq * t).astype(np.complex64)
    else:
        return np.exp(-1j * 2.0 * np.pi * tone_freq * t).astype(np.complex64)


def make_cw_signal(duration_s, sample_rate):
    """CW 等幅载波（键控开状态）：exp(j*0)=1+0j。
    经 cw_demod(BFO=700Hz) 后应出 700Hz 音频音调。
    """
    n = int(round(duration_s * sample_rate))
    return np.ones(n, dtype=np.complex64)


def _rms(x):
    """RMS 能量（空数组返回 0）。复数输入先取模，避免 ComplexWarning。"""
    x = np.asarray(x)
    if x.size == 0:
        return 0.0
    if np.iscomplexobj(x):
        x = np.abs(x)
    x = x.astype(np.float64)
    return float(np.sqrt(np.mean(x * x)))


def _peak_freq(audio, sr):
    """对实音频做加 Hann 窗 rFFT，返回最大谱峰频率（忽略 DC bin）。

    用 Hann 窗减少泄漏，让峰频估计稳定；忽略 DC 是为了避开去直流
    后残留的零频分量。
    """
    a = np.asarray(audio, dtype=np.float64)
    n = a.size
    if n < 8:
        return 0.0
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(a * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    if spec.size <= 2:
        return 0.0
    # 跳过 DC bin（index 0），避免被直流残留锁定
    idx = int(np.argmax(spec[1:]) + 1)
    return float(freqs[idx])


# ═══════════════════════════════════════════════════════
# 2. 各模式解调非空且频率正确
# ═══════════════════════════════════════════════════════

class TestDemodFrequencyCorrectness:
    """对每种模式喂数学正确的调制信号，断言解调输出非空、有能量、
    且频谱峰落在预期音频频率上——这证明鉴频/包络/拍频真切在了
    正确的载波上，而不是吐出常数或噪声。"""

    def test_wfm_demod_has_modulation_peak(self):
        """WFM：240k 采样率，75kHz 频偏，1kHz 调制 → 鉴频输出峰应在 ~1kHz。"""
        sr = 240000
        mod = 1000.0
        iq = make_fm_signal(0.5, sr, mod_freq=mod, deviation=75000.0)
        out = dsp.demodulate(iq, mode="WFM", sample_rate=sr, deviation=75000.0)
        # 输出非空且有能量 = 鉴频链路通了
        assert out.size > 0, "WFM 解调输出为空，鉴频链路断开"
        assert _rms(out) > 1e-4, f"WFM 解调输出 RMS={_rms(out)} 过低，无信号能量"
        peak = _peak_freq(out, sr)
        # 鉴频是线性过程，输出应忠实复现 1kHz 调制音（±10% 容差）
        assert abs(peak - mod) <= 0.10 * mod, \
            f"WFM 鉴频谱峰={peak:.1f}Hz 偏离调制频率 {mod:.0f}Hz 超过 10%"

    def test_nfm_demod_has_modulation_peak(self):
        """NFM：48k 采样率，5kHz 频偏，1kHz 调制 → 峰在 ~1kHz。"""
        sr = 48000
        mod = 1000.0
        iq = make_fm_signal(0.5, sr, mod_freq=mod, deviation=5000.0)
        out = dsp.demodulate(iq, mode="NFM", sample_rate=sr)
        assert out.size > 0, "NFM 解调输出为空"
        assert _rms(out) > 1e-4, f"NFM 输出 RMS={_rms(out)} 过低"
        peak = _peak_freq(out, sr)
        assert abs(peak - mod) <= 0.10 * mod, \
            f"NFM 鉴频谱峰={peak:.1f}Hz 偏离 {mod:.0f}Hz 超过 10%"

    def test_fm_broadcast_demod_has_modulation_peak(self):
        """FM（广播档）：deviation=75k，1kHz 调制 → 峰在 ~1kHz。"""
        sr = 240000
        mod = 1000.0
        iq = make_fm_signal(0.5, sr, mod_freq=mod, deviation=75000.0)
        out = dsp.demodulate(iq, mode="FM", sample_rate=sr, deviation=75000.0)
        assert out.size > 0, "FM 解调输出为空"
        assert _rms(out) > 1e-4
        peak = _peak_freq(out, sr)
        assert abs(peak - mod) <= 0.10 * mod, \
            f"FM 鉴频谱峰={peak:.1f}Hz 偏离 {mod:.0f}Hz 超过 10%"

    def test_am_demod_has_modulation_peak(self):
        """AM：包络检波，1kHz 调制，50% 调幅度 → 输出峰在 ~1kHz。"""
        sr = 48000
        mod = 1000.0
        iq = make_am_signal(0.5, sr, mod_freq=mod, depth=0.5)
        out = dsp.demodulate(iq, mode="AM", sample_rate=sr)
        assert out.size > 0, "AM 解调输出为空"
        assert _rms(out) > 1e-4, f"AM 输出 RMS={_rms(out)} 过低"
        peak = _peak_freq(out, sr)
        # 包络检波应忠实还原调制正弦
        assert abs(peak - mod) <= 0.10 * mod, \
            f"AM 包络谱峰={peak:.1f}Hz 偏离 {mod:.0f}Hz 超过 10%"

    def test_usb_demod_has_audio_tone(self):
        """USB：单音在 +2500Hz，BFO=1500 → 输出音频应在 ~1000Hz。"""
        sr = 48000
        tone = 2500.0
        expected_audio = abs(tone - 1500.0)  # ssb_demod 默认 carrier_offset=1500
        iq = make_ssb_signal(0.5, sr, tone_freq=tone, mode="USB")
        out = dsp.demodulate(iq, mode="USB", sample_rate=sr)
        assert out.size > 0, "USB 解调输出为空"
        assert _rms(out) > 1e-4, f"USB 输出 RMS={_rms(out)} 过低"
        peak = _peak_freq(out, sr)
        # 上边带混频后应落在音频带内 ~1000Hz（±25% 容差，移动平均低通会展宽）
        assert abs(peak - expected_audio) <= 0.25 * expected_audio, \
            f"USB 音频峰={peak:.1f}Hz 偏离预期 {expected_audio:.0f}Hz"

    def test_lsb_demod_has_audio_tone(self):
        """LSB：单音在 -2500Hz，BFO=1500 → 输出音频应在 ~1000Hz。"""
        sr = 48000
        tone = 2500.0
        expected_audio = abs(tone - 1500.0)
        iq = make_ssb_signal(0.5, sr, tone_freq=tone, mode="LSB")
        out = dsp.demodulate(iq, mode="LSB", sample_rate=sr)
        assert out.size > 0, "LSB 解调输出为空"
        assert _rms(out) > 1e-4, f"LSB 输出 RMS={_rms(out)} 过低"
        peak = _peak_freq(out, sr)
        assert abs(peak - expected_audio) <= 0.25 * expected_audio, \
            f"LSB 音频峰={peak:.1f}Hz 偏离预期 {expected_audio:.0f}Hz"

    def test_cw_demod_has_bfo_tone(self):
        """CW：等幅载波，BFO=700Hz → 输出应在 700Hz 处出拍频音调。"""
        sr = 48000
        iq = make_cw_signal(0.5, sr)
        out = dsp.demodulate(iq, mode="CW", sample_rate=sr)
        assert out.size > 0, "CW 解调输出为空"
        assert _rms(out) > 1e-4, f"CW 输出 RMS={_rms(out)} 过低"
        peak = _peak_freq(out, sr)
        # cw_demod 默认 tone_freq=700Hz
        assert abs(peak - 700.0) <= 0.10 * 700.0, \
            f"CW 拍频谱峰={peak:.1f}Hz 偏离 BFO 700Hz 超过 10%"


# ═══════════════════════════════════════════════════════
# 3. 音频重采样到 48k（窄带模式）
# ═══════════════════════════════════════════════════════

class TestAudioResampleTo48k:
    """解调后基带速率各不相同，audio_to_playback() 负责统一降到 48k
    并抗混叠低通。验证输出长度 ∝ duration*48k 且仍有能量。"""

    @pytest.mark.parametrize("mode,in_sr,make_kw", [
        ("NFM", 48000, dict(mod_freq=1000.0, deviation=5000.0)),
        ("AM", 48000, dict(mod_freq=1000.0, depth=0.5)),
        ("USB", 48000, dict(tone_freq=2500.0, mode="USB")),
        ("LSB", 48000, dict(tone_freq=2500.0, mode="LSB")),
        ("CW", 48000, dict()),
    ])
    def test_resampled_length_and_energy(self, mode, in_sr, make_kw):
        duration = 0.5
        if mode in ("NFM",):
            iq = make_fm_signal(duration, in_sr, **make_kw)
        elif mode == "AM":
            iq = make_am_signal(duration, in_sr, **make_kw)
        elif mode in ("USB", "LSB"):
            iq = make_ssb_signal(duration, in_sr, **make_kw)
        else:  # CW
            iq = make_cw_signal(duration, in_sr, **make_kw)

        audio = dsp.demodulate(iq, mode=mode, sample_rate=in_sr)
        # 窄带音频截止 3kHz（NFM/SSB/CW 语音带），输出 48k
        playback = dsp.audio_to_playback(audio, in_sr=in_sr,
                                         cutoff_hz=3000.0, out_sr=48000)
        # 输出非空且有能量 = 重采样链路通了
        assert playback.size > 0, f"{mode} 重采样后输出为空"
        assert _rms(playback) > 1e-4, f"{mode} 重采样后 RMS={_rms(playback)} 过低"
        # 长度应 ≈ duration * out_sr（resample_poly 有少量边缘差，容差 ±10%）
        expected = duration * 48000
        assert abs(playback.size - expected) <= 0.15 * expected, \
            f"{mode} 重采样长度={playback.size} 偏离预期 {expected:.0f} 超过 15%"


# ═══════════════════════════════════════════════════════
# 4. AudioPlayer 队列行为（无硬件安全降级）
# ═══════════════════════════════════════════════════════

class TestAudioPlayerNoHardwareDegradation:
    """沙箱无真实声卡。证明整条链路在没有硬件时不崩：
    start() 失败 → available=False → write() 返回 0 但不抛异常。"""

    def test_start_fails_without_device_then_write_safe(self, monkeypatch):
        player = AudioPlayer(sample_rate=48000, channels=1, gain=0.5)

        # 强制走"打开设备失败"分支：把 OutputStream 换成必抛异常的假类，
        # 这样无论沙箱是否真有声卡，测试都确定性覆盖降级路径。
        import mbdsdr_ai.audio_out as mod
        if mod.sd is not None:
            def _boom(*a, **k):
                raise RuntimeError("no audio device in sandbox")
            monkeypatch.setattr(mod.sd, "OutputStream", _boom, raising=False)

        started = player.start()
        # 无设备 / 打开失败：start 返回 False，available 被置 False
        assert started is False, f"无设备时 start() 应返回 False，实际 {started}"
        assert player.available is False, \
            "无设备时 available 应降级为 False（对照 receiver.cpp 音频 sink 打开失败路径）"

        # write 在无设备时返回 0 但绝不抛异常 —— 这是"链路不崩"的关键
        n = player.write(np.ones(1000, dtype=np.float32))
        assert n == 0, f"无设备时 write 应返回 0，实际 {n}"

        # set_gain 即便无设备也应正常设置（不抛异常）
        player.set_gain(0.8)
        assert abs(player.gain - 0.8) < 1e-6, "set_gain 未生效"
        # 无设备 / 无流 → is_playing 必为 False
        assert player.is_playing is False, "无设备时 is_playing 应为 False"
        player.stop()  # 重复 stop 也应安全

    def test_write_returns_zero_when_stream_not_started(self):
        """即便不调用 start()，write 也应安全返回 0（_stream is None 分支）。"""
        player = AudioPlayer(sample_rate=48000, gain=1.0)
        # available 可能 True（装了 sounddevice），但 _stream 为 None
        player._stream = None
        n = player.write(np.ones(512, dtype=np.float32))
        assert n == 0, f"未 start 时 write 应返回 0，实际 {n}"


# ═══════════════════════════════════════════════════════
# 5. 音量 gain 乘法 + 静噪门控逻辑
# ═══════════════════════════════════════════════════════

def _squelch_gate(iq, threshold_db=-50.0):
    """模拟 _demod_and_play 中的静噪门控：
    计算 IQ 功率 dBFS，低于门限返回 None（静音），高于则原样返回（放行）。

    对照 GQRX dockrxop.cpp:347 setSquelchLevel / dockrxopt.ui:660
    sqlSpinBox 默认 -150 dBFS（即默认开放、不静噪）。
    """
    iq = np.asarray(iq, dtype=np.complex128)
    power = float(np.mean(np.abs(iq) ** 2))
    if power < 1e-12:
        return None
    dbfs = 10.0 * np.log10(power)
    if dbfs < threshold_db:
        return None
    return iq


class TestGainAndSquelch:
    def test_write_applies_gain_multiplication(self):
        """直接验证 write() 内部的 gain 乘法（对照 receiver.cpp:989
        k=pow(10,gain_db/20) 线性增益乘法）。

        通过把 available 置 True、_stream 置一个 dummy 非空对象，
        绕过"无设备返回 0"分支，真实走到 data*gain + clip + 入队。
        """
        player = AudioPlayer(sample_rate=48000, gain=0.5)
        player.available = True
        player._stream = object()  # dummy，使 write 不入降级分支

        sig = np.array([0.5, -0.5, 1.0, -1.0, 0.25], dtype=np.float32)
        n = player.write(sig)

        assert n == sig.size, f"应入队 {sig.size} 个样本，实际 {n}"
        # write 内部：data = data*gain，再 clip 到 [-1,1]
        expected = np.clip(sig * 0.5, -1.0, 1.0).astype(np.float32)
        queued = np.concatenate([np.atleast_1d(b) for b in player._queue])
        np.testing.assert_allclose(queued, expected, atol=1e-6,
                                   err_msg="write 入队数据未按 gain=0.5 缩放+clip")
        player.stop()

    def test_gain_change_affects_subsequent_write(self):
        """set_gain 后再写，幅度应按新 gain 缩放。"""
        player = AudioPlayer(sample_rate=48000, gain=1.0)
        player.available = True
        player._stream = object()
        player.set_gain(0.25)

        sig = np.array([0.4, -0.4], dtype=np.float32)
        player.write(sig)
        expected = np.clip(sig * 0.25, -1.0, 1.0)
        queued = np.concatenate([np.atleast_1d(b) for b in player._queue])
        np.testing.assert_allclose(queued, expected, atol=1e-6)
        player.stop()

    def test_squelch_mutes_low_noise(self):
        """低电平噪声（dBFS ≈ -87 < -50 门限）→ 静噪门控返回 None。"""
        rng = np.random.default_rng(7)
        # 缩放至 dBFS ≈ 10*log10(2*(3e-5)^2) ≈ -87，远低于 -50 门限
        noise = (rng.standard_normal(2000) + 1j * rng.standard_normal(2000)) * 3e-5
        dbfs = 10.0 * np.log10(np.mean(np.abs(noise) ** 2))
        assert dbfs < -80.0, f"夹具噪声 dBFS={dbfs:.1f} 应 < -80"
        assert _squelch_gate(noise, threshold_db=-50.0) is None, \
            "低于静噪门限的噪声应被门控静音（返回 None）"

    def test_squelch_passes_strong_signal(self):
        """高电平信号（dBFS ≈ 0 > -20）→ 门控放行。"""
        strong = make_cw_signal(0.1, 48000)  # 单位幅度载波，|x|^2=1 → dBFS=0
        dbfs = 10.0 * np.log10(np.mean(np.abs(strong) ** 2))
        assert dbfs > -20.0, f"强信号 dBFS={dbfs:.1f} 应 > -20"
        out = _squelch_gate(strong, threshold_db=-50.0)
        assert out is not None, "高于静噪门限的信号应放行"
        assert out.size == strong.size


# ═══════════════════════════════════════════════════════
# 6. 模式切换（同一 IQ 依次切 7 种模式）
# ═══════════════════════════════════════════════════════

class TestModeSwitching:
    def test_same_iq_switches_through_all_modes(self):
        """对同一段宽带合成 IQ 依次用 WFM→NFM→AM→USB→LSB→CW 解调。
        每次输出都非空，且不同模式的 RMS 特征不同——证明切模式真切了
        解调函数（FM 鉴频 / AM 包络 / SSB 混频 / CW 拍频），而不是
        所有模式都返回同一个 FM 鉴频结果。"""
        sr = 240000
        # 用一段宽带 FM 信号（载波 DC，1kHz 调制，75k 频偏）作为公共输入
        iq = make_fm_signal(0.3, sr, mod_freq=1000.0, deviation=75000.0)

        modes = ["WFM", "NFM", "AM", "USB", "LSB", "CW"]
        rms_values = {}
        for m in modes:
            out = dsp.demodulate(iq, mode=m, sample_rate=sr, deviation=75000.0)
            assert out.size > 0, f"切到 {m} 模式时解调输出为空"
            r = _rms(out)
            assert r > 0.0, f"{m} 模式 RMS=0，无输出能量"
            rms_values[m] = r

        # 不同模式对同一段 IQ 的处理物理上不同（鉴频 vs 包络 vs 混频），
        # RMS 不应全部相等。至少要有两种模式的 RMS 明显不同。
        unique_rms = {round(v, 4) for v in rms_values.values()}
        assert len(unique_rms) >= 2, \
            f"所有模式 RMS 几乎相同 {rms_values}，怀疑切模式没真切函数"


# ═══════════════════════════════════════════════════════
# 7. VFO 集成测试（偏移信号下变频到 DC 后鉴频）
# ═══════════════════════════════════════════════════════

class TestVFOIntegration:
    def test_offset_fm_signal_downconverted_then_demodulated(self):
        """构造一个载波在 +10kHz 的 FM 信号，用 VFO(offset=10000) 下变频到 DC，
        再做 FM 鉴频。证明 VFO（xlator→resamp→filter，对照 rx_vfo.h:89-100）
        与解调器串联通畅。"""
        sr = 240000
        duration = 0.5
        mod = 1000.0
        deviation = 5000.0

        t = np.arange(int(duration * sr)) / sr
        # 载波搬到 +10kHz：exp(j*2π*10000*t) * FM 调制相位
        phase = -(deviation / mod) * np.cos(2.0 * np.pi * mod * t)
        iq = np.exp(1j * (2.0 * np.pi * 10000.0 * t + phase)).astype(np.complex64)

        # VFO: 240k → 48k，带宽 12k，offset=+10k（把 +10k 处信号搬到 DC）
        vfo = dsp.VFO(in_samplerate=sr, out_samplerate=48000,
                      bandwidth=12000, offset=10000)
        baseband = vfo.process(iq)

        # VFO 输出非空（下变频 + 重采样链路通）
        assert baseband.size > 0, "VFO process 输出为空"
        assert _rms(baseband) > 1e-4, f"VFO 输出 RMS={_rms(baseband)} 过低"

        # 对 VFO 输出（已在 48k）做 NFM 鉴频
        audio = dsp.fm_demod(baseband, deviation=deviation, sample_rate=48000)
        assert audio.size > 0, "VFO 后 FM 鉴频输出为空"
        assert _rms(audio) > 1e-4, f"VFO+鉴频输出 RMS={_rms(audio)} 过低"

        # 鉴频应仍能看到 1kHz 调制峰（VFO 把信号正确搬到了 DC）
        peak = _peak_freq(audio, 48000)
        assert abs(peak - mod) <= 0.15 * mod, \
            f"VFO 下变频后鉴频谱峰={peak:.1f}Hz 偏离 {mod:.0f}Hz，下变频可能未居中"
