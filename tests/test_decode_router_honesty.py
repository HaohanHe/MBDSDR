"""解码路由「不许撒谎」的反证用例（P5.2 纵深核查收口）。

每条用例都对应核查清单里的一条「代码在撒谎」；改回旧行为就红：

1. ADS-B 非整数倍/低于 2MHz 的 fs 不再 ok=true（sample_rate_mismatch）；
   默认采样率必须是合法值；解码侧 decode_baseband 也拒绝。
2. ADS-B 声称 frames 且 ≥3 条 DF17：真做多帧扫描（每个前导独立 CRC）。
3. AIS 解码侧拒绝 fs=20000（不静默取整成 sps=2）。
4. POCSAG 非枚举 baud 不再静默降为 512，显式拒绝并列出合法集。
5. Meteor 能力表输出是 symbols/bits（不是 image）；fs<144k / 非 72k
   整数倍显式拒绝。
6. 每个解码器运行期校验「最短一帧」样本量 → insufficient_samples，
   不再 ok=true + 0 条。
7. missing_dependency 有真实 import 失败可达路径。
8. invalid_wav 纳入失败码枚举并在能力表登记。
"""

from __future__ import annotations

import math
import sys
import wave

import numpy as np
import pytest

from mbdsdr_ai import decode_router as dr


# ---------------------------------------------------------------------------
# 小工具：写 cf32 / wav
# ---------------------------------------------------------------------------
def _write_cf32(path, iq):
    inter = np.empty(len(iq) * 2, dtype=np.float32)
    inter[0::2] = np.asarray(iq).real.astype(np.float32)
    inter[1::2] = np.asarray(iq).imag.astype(np.float32)
    inter.tofile(str(path))
    return str(path)


def _write_wav(path, samples, sr=12000):
    data = np.asarray(samples, dtype=np.float64)
    pcm = np.clip(data, -1.0, 1.0) * 32767.0
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(pcm.astype("<i2").tobytes())
    return str(path)


# ---------------------------------------------------------------------------
# 1. ADS-B 采样率：显式失败，默认值合法
# ---------------------------------------------------------------------------
class TestAdsbSampleRate:
    def test_default_sample_rate_is_legal(self):
        fs = dr._default_sample_rate("adsb")
        assert fs >= 2e6
        assert abs(fs / 1e6 - round(fs / 1e6)) < 1e-9

    def test_below_2mhz_is_explicit_failure(self, tmp_path):
        cf = _write_cf32(tmp_path / "a.cf32", np.zeros(64, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "adsb", sample_rate=1e6)
        assert out["ok"] is False
        assert out["error_code"] == dr.ERR_SAMPLE_RATE_MISMATCH
        assert "sps=1" in out["error"]
        assert out["found"] is not True

    def test_non_integer_multiple_is_explicit_failure(self, tmp_path):
        cf = _write_cf32(tmp_path / "a.cf32", np.zeros(64, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "adsb", sample_rate=2.5e6)
        assert out["ok"] is False
        assert out["error_code"] == dr.ERR_SAMPLE_RATE_MISMATCH

    def test_decode_side_also_rejects(self):
        from mbdsdr_ai.adsb import decode_baseband
        with pytest.raises(ValueError) as ei:
            decode_baseband(np.zeros(256, dtype=np.complex128), fs=1e6)
        assert "sps=1" in str(ei.value)


# ---------------------------------------------------------------------------
# 2. ADS-B 多帧扫描：≥3 条 DF17
# ---------------------------------------------------------------------------
class TestAdsbMultiFrame:
    def test_three_frames_are_really_scanned(self, tmp_path):
        from mbdsdr_ai.adsb import build_identification_frame, modulate_baseband

        fs = 4e6
        gap = np.zeros(int(20e-6 * fs), dtype=np.complex128)
        parts = []
        targets = [("780ABC", "CCA123"), ("780ABD", "CES456"),
                   ("780ABE", "CSN789")]
        for icao, cs in targets:
            frame = build_identification_frame(icao, cs)
            parts.extend([modulate_baseband(frame, fs=fs, lead_us=4.0), gap])
        iq = np.concatenate(parts)
        cf = _write_cf32(tmp_path / "three.cf32", iq)

        out = dr.run_digital_mode(cf, "adsb", sample_rate=fs)
        assert out["ok"] is True
        assert out["num_frames"] == 3, out.get("note")
        got = {f["callsign"] for f in out["frames"] if f["crc_ok"]}
        assert got == {cs for _, cs in targets}

    def test_capability_keeps_frames_output(self):
        cap = dr.DIGITAL_MODE_CAPABILITIES["adsb"]
        assert cap.output_kind == "frames"


# ---------------------------------------------------------------------------
# 3. AIS 采样率解码侧校验
# ---------------------------------------------------------------------------
class TestAisSampleRate:
    def test_route_rejects_fs_20000(self, tmp_path):
        cf = _write_cf32(tmp_path / "a.cf32", np.zeros(64, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "ais", sample_rate=20000)
        assert out["ok"] is False
        assert out["error_code"] == dr.ERR_SAMPLE_RATE_MISMATCH
        assert "sps=2" in out["error"]

    def test_decoder_rejects_fs_20000(self):
        from mbdsdr_ai.ais_decoder import ais_demodulate_iq
        with pytest.raises(ValueError) as ei:
            ais_demodulate_iq(np.zeros(512, dtype=np.complex128), 20000)
        assert "sps=2" in str(ei.value)

    def test_valid_multiple_passes_rate_check(self, tmp_path):
        cf = _write_cf32(tmp_path / "a.cf32", np.zeros(5000, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "ais", sample_rate=192000)
        assert out["error_code"] != dr.ERR_SAMPLE_RATE_MISMATCH


# ---------------------------------------------------------------------------
# 4. POCSAG baud：不静默降级
# ---------------------------------------------------------------------------
class TestPocsagBaud:
    def test_non_enum_baud_is_rejected(self, tmp_path):
        wav = _write_wav(tmp_path / "p.wav", np.zeros(30000))
        out = dr.run_digital_mode(wav, "pocsag", baud=1000)
        assert out["ok"] is False
        assert out["error_code"] == "param_not_in_enum"
        assert "512/1200/2400" in out["error"]
        assert out.get("baud") is None

    @pytest.mark.parametrize("baud", [512, 1200, 2400])
    def test_legal_bauds_do_not_hit_enum_code(self, tmp_path, baud):
        # 30000 样本 @12k = 2.5s ≥ 1120 bit / 512 baud 的最短批
        wav = _write_wav(tmp_path / "p.wav", np.zeros(30000))
        out = dr.run_digital_mode(wav, "pocsag", baud=baud)
        assert out["error_code"] != "param_not_in_enum"


# ---------------------------------------------------------------------------
# 5. Meteor：输出 symbols/bits + fs 硬约束
# ---------------------------------------------------------------------------
class TestMeteor:
    def test_capability_declares_symbols_bits(self):
        cap = dr.DIGITAL_MODE_CAPABILITIES["meteor"]
        assert cap.output_kind == "symbols/bits"

    def test_below_144k_rejected(self, tmp_path):
        cf = _write_cf32(tmp_path / "m.cf32", np.zeros(1024, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "meteor", sample_rate=100000)
        assert out["ok"] is False
        assert out["error_code"] == dr.ERR_SAMPLE_RATE_MISMATCH

    def test_non_multiple_of_72k_rejected(self, tmp_path):
        cf = _write_cf32(tmp_path / "m.cf32", np.zeros(1024, dtype=np.complex128))
        out = dr.run_digital_mode(cf, "meteor", sample_rate=200000)
        assert out["error_code"] == dr.ERR_SAMPLE_RATE_MISMATCH

    def test_decoder_side_rejects(self):
        from mbdsdr_ai.meteor_sat import meteor_lrpt_demod
        with pytest.raises(ValueError):
            meteor_lrpt_demod(np.zeros(1024, dtype=np.complex128), 100000)


# ---------------------------------------------------------------------------
# 6. 每个解码器运行期「最短一帧」校验
# ---------------------------------------------------------------------------
_SHORT_CASES = [
    # (mode, ext, fs, 样本数)
    ("adsb", ".cf32", 2e6, 16),
    ("ais", ".cf32", 192000, 100),
    ("meteor", ".cf32", 288000, 7200),
    ("pocsag", ".wav", None, 1000),
    ("acars", ".wav", None, 1000),
    ("navtex", ".wav", None, 1000),
    ("ft4", ".wav", None, 1000),
    ("wspr", ".wav", None, 1000),
]


@pytest.mark.parametrize("mode,ext,fs,n", _SHORT_CASES)
def test_short_input_is_insufficient_samples(tmp_path, mode, ext, fs, n):
    if ext == ".cf32":
        path = _write_cf32(tmp_path / f"{mode}.cf32",
                           np.zeros(n, dtype=np.complex128))
    else:
        path = _write_wav(tmp_path / f"{mode}.wav", np.zeros(n))
    kwargs = {"sample_rate": fs} if fs else {}
    out = dr.run_digital_mode(path, mode, **kwargs)
    assert out["ok"] is False
    assert out["error_code"] == dr.ERR_INSUFFICIENT_SAMPLES
    assert out["actual_samples"] == n
    assert out["required_samples"] > n


def test_long_enough_silence_is_not_insufficient(tmp_path):
    """阈值不是「一律失败」：6s 静音够 NAVTEX 一帧，应正常返回 0 条。"""
    wav = _write_wav(tmp_path / "n.wav", np.zeros(72000))
    out = dr.run_digital_mode(wav, "navtex")
    assert out["error_code"] != dr.ERR_INSUFFICIENT_SAMPLES
    assert out["ok"] is True
    assert out["num_messages"] == 0


# ---------------------------------------------------------------------------
# 7. missing_dependency 真实可达
# ---------------------------------------------------------------------------
def test_missing_dependency_reachable_via_import_subsystem(tmp_path):
    """把模块在 sys.modules 里置 None：真实 import 子系统直接拒绝。

    这不是 monkeypatch 我们的探测函数，而是 Python 自身的「import halted」
    机制——正是环境缺第三方库时的同一条失败路径。
    """
    cf = _write_cf32(tmp_path / "a.cf32", np.zeros(64, dtype=np.complex128))
    saved = sys.modules.get("mbdsdr_ai.adsb")
    sys.modules["mbdsdr_ai.adsb"] = None
    try:
        out = dr.run_digital_mode(cf, "adsb", sample_rate=2e6)
    finally:
        if saved is None:
            sys.modules.pop("mbdsdr_ai.adsb", None)
        else:
            sys.modules["mbdsdr_ai.adsb"] = saved
    assert out["error_code"] == dr.ERR_MISSING_DEPENDENCY
    assert out["missing_dependency"] == "mbdsdr_ai.adsb"


def test_missing_dependency_has_a_real_import_branch(tmp_path, monkeypatch):
    """文件存在但导入失败（缺 numpy/scipy 等）：走 _import_module 分支。"""
    cf = _write_cf32(tmp_path / "a.cf32", np.zeros(64, dtype=np.complex128))
    monkeypatch.setattr(dr, "_find_spec", lambda name: object())
    monkeypatch.setitem(sys.modules, "mbdsdr_ai.adsb", None)
    out = dr.run_digital_mode(cf, "adsb", sample_rate=2e6)
    assert out["error_code"] == dr.ERR_MISSING_DEPENDENCY


# ---------------------------------------------------------------------------
# 8. invalid_wav：纳入枚举 + 能力表登记
# ---------------------------------------------------------------------------
def test_invalid_wav_is_registered_and_reachable(tmp_path):
    from mbdsdr_ai.error_catalog import ERRORS

    assert "invalid_wav" in ERRORS
    for mode in ("ft4", "wspr"):
        assert "invalid_wav" in dr.DIGITAL_MODE_CAPABILITIES[mode].failure_codes

    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF")
    out = dr.run_digital_mode(str(bad), "ft4")
    assert out["ok"] is False
    assert out["error_code"] == "invalid_wav"


def test_all_declared_failure_codes_are_registered():
    """能力表登记的失败码必须都在 error_catalog 里（不许野生码）。"""
    from mbdsdr_ai.error_catalog import ERRORS

    for key, cap in dr.DIGITAL_MODE_CAPABILITIES.items():
        for code in cap.failure_codes:
            assert code in ERRORS, f"{key} 登记了未入册的失败码 {code}"


# ---------------------------------------------------------------------------
# 9. ACARS 生产路由端到端：前导必须原样送到 decode_audio
# ---------------------------------------------------------------------------
def _acars_over_air(text="WEATHER 1500Z WIND 270/15"):
    """构造含 SYN,SYN,SOH 前导的完整空中字节流（build_acars_frame 只有 body）。"""
    from mbdsdr_ai.acars_decoder import SOH, SYN, build_acars_frame

    body = build_acars_frame(mode="2", reg="N842UA", label="DF",
                             block_id="6", text=text)
    return bytes([SYN, SYN, SOH]) + body


class TestAcarsProductionPath:
    """`decode_router._route_acars` 是 CLI 生产入口，上游不得剥掉前导。

    前导若在路由层被裁掉，`ACARSDecoder.decode_audio` 会静默返回 0 帧
    （不报错、不记 crc 失败），因此这里用端到端断言把「必需前导」钉住。

    合成的 IQ 是仓内 `_demod_iq_to_audio`（FM 鉴频）的逆运算，只证明路由
    把前导完整交给 decode_audio；ACARS 实际是 AM 下行，这份合成 IQ 不代表
    真实 AM 空中信号，也不能当作解调质量的对拍证据。频偏取 3000Hz，是因为
    鉴频前的去均值会吃掉过小频偏（±1Hz）的慢相位，那是合成方式问题，不是
    真实录音条件。
    """

    def test_cf32_iq_end_to_end_yields_known_frame(self, tmp_path):
        from mbdsdr_ai.acars_decoder import frame_bits_from_bytes, synthesize_msk_audio

        fs = 12500.0
        audio = synthesize_msk_audio(frame_bits_from_bytes(_acars_over_air()),
                                     fs, 1200)
        iq = np.exp(1j * 2 * np.pi * 3000.0 * np.cumsum(audio) / fs)
        cf = _write_cf32(tmp_path / "acars.cf32", iq)

        out = dr.run_digital_mode(cf, "acars", sample_rate=fs)

        assert out["ok"] is True, out.get("error")
        assert out["num_messages"] == 1
        assert out["crc_ok_count"] == 1
        m = out["messages"][0]
        assert m["crc_ok"] is True
        assert m["reg"].strip() == "N842UA"
        assert m["label"] == "DF"
        assert m["block_id"] == "6"
        assert "WEATHER" in m["text"]

    def test_wav_audio_end_to_end_yields_known_frame(self, tmp_path):
        from mbdsdr_ai.acars_decoder import frame_bits_from_bytes, synthesize_msk_audio

        fs = 12500
        audio = synthesize_msk_audio(frame_bits_from_bytes(_acars_over_air()),
                                     fs, 1200)
        wav = _write_wav(tmp_path / "acars.wav", audio, sr=fs)

        out = dr.run_digital_mode(wav, "acars", sample_rate=fs)

        assert out["ok"] is True, out.get("error")
        assert out["num_messages"] == 1
        m = out["messages"][0]
        assert m["reg"].strip() == "N842UA"
        assert m["label"] == "DF"
        assert "WEATHER" in m["text"]
