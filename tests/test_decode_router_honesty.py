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
import os
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


def test_long_enough_silence_is_empty_not_success(tmp_path):
    """阈值不是「一律失败」：6s 静音够 NAVTEX 一帧，但 0 条不是成功。

    契约（P0-2）：输入无能量 → ok=False / status=empty /
    error_code=nothing_detected；空结果与解码失败、未实现分开。
    """
    wav = _write_wav(tmp_path / "n.wav", np.zeros(72000))
    out = dr.run_digital_mode(wav, "navtex")
    assert out["error_code"] != dr.ERR_INSUFFICIENT_SAMPLES
    assert out["ok"] is False
    assert out["status"] == dr.STATUS_EMPTY
    assert out["error_code"] == dr.ERR_NOTHING_DETECTED
    assert out["signal_present"] is False
    assert out["num_messages"] == 0
    assert "error" not in out


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


def _am_iq(audio, fs, carrier_hz=3000.0, depth=0.7):
    """真实 AM 调制：复载波 + MSK 基带调幅，包络 = 1 + depth·audio/峰值。"""
    base = 1.0 + depth * np.asarray(audio, dtype=np.float64) / np.max(np.abs(audio))
    t = np.arange(base.size) / float(fs)
    return base * np.exp(1j * 2 * np.pi * carrier_hz * t)


class TestAcarsProductionPath:
    """`decode_router._route_acars` 是 CLI 生产入口，上游不得剥掉前导。

    前导若在路由层被裁掉，`ACARSDecoder.decode_audio` 会静默返回 0 帧
    （不报错、不记 crc 失败），因此这里用端到端断言把「必需前导」钉住。

    `.cf32` 用例按**真实 AM 调制**合成 IQ（载波 + MSK 基带调幅），不是
    FM 鉴频的逆运算；同一份 IQ 另跑一条 FM 鉴频对照，预期 0 帧——用来证明
    AM 包络路线确实必要，而不是两条路都能解。
    """

    def test_am_cf32_iq_end_to_end_yields_known_frame(self, tmp_path):
        from mbdsdr_ai.acars_decoder import frame_bits_from_bytes, synthesize_msk_audio

        fs = 48000.0
        audio = synthesize_msk_audio(frame_bits_from_bytes(_acars_over_air()),
                                     fs, 1200)
        cf = _write_cf32(tmp_path / "acars_am.cf32",
                         _am_iq(audio, fs, carrier_hz=3000.0, depth=0.7))

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

    def test_same_am_iq_fails_through_fm_discriminator(self, tmp_path):
        """对照：旧的 FM 鉴频路径吃同一份 AM IQ，必须 0 帧。"""
        from mbdsdr_ai.acars_decoder import ACARSDecoder, frame_bits_from_bytes, synthesize_msk_audio
        from mbdsdr_ai.decoders import _demod_iq_to_audio

        fs = 48000.0
        audio = synthesize_msk_audio(frame_bits_from_bytes(_acars_over_air()),
                                     fs, 1200)
        cf = _write_cf32(tmp_path / "acars_am_ctl.cf32",
                         _am_iq(audio, fs, carrier_hz=3000.0, depth=0.7))

        fm_audio, fm_sr, err = _demod_iq_to_audio(cf, fs, out_sr=12500)

        assert err is None
        assert ACARSDecoder(sample_rate=fm_sr).decode_audio(fm_audio) == []

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


# ---------------------------------------------------------------------------
# 10. 已知合法帧 → 生产入口字段级断言（不只断言「不抛异常 / 非空 / 帧数>0」）
# ---------------------------------------------------------------------------
_SENS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fixtures", "sensitivity")


def _ac12_for(alt_ft):
    """反解 Q=1 的 12bit 气压高度域（decode_ac12_altitude 的逆）。"""
    from mbdsdr_ai.adsb import decode_ac12_altitude

    for ac12 in range(0x1000):
        if ac12 & 0x10 and decode_ac12_altitude(ac12) == alt_ft:
            return ac12
    raise AssertionError(f"无法编码高度 {alt_ft} ft")


def _airborne_position_frame(icao_hex="780ABC", alt_ft=35000, odd=False):
    """构造已知 TC=11 空中位置长帧（高度可解，CPR 仅占位不求位置）。"""
    from mbdsdr_ai.adsb import build_long_frame

    bits = [0] * 56
    bits[0:5] = [(11 >> (4 - i)) & 1 for i in range(5)]      # TC=11
    ac12 = _ac12_for(alt_ft)
    bits[8:20] = [(ac12 >> (11 - i)) & 1 for i in range(12)]  # 气压高度
    bits[21] = 1 if odd else 0                                # F 奇偶标志
    cpr_lat, cpr_lon = 0x0ABCD, 0x12345
    bits[22:39] = [(cpr_lat >> (16 - i)) & 1 for i in range(17)]
    bits[39:56] = [(cpr_lon >> (16 - i)) & 1 for i in range(17)]
    me7 = bytes(int("".join(map(str, bits[i:i + 8])), 2)
                for i in range(0, 56, 8))
    return build_long_frame(0x8D, int(icao_hex, 16), me7)


class TestKnownFrameFields:
    """每条都断言「已知合法帧里的具体字段」，改错字段即红。"""

    def test_pocsag_route_yields_known_address_and_message(self):
        # 真值见 tests/fixtures/sensitivity/manifest.json（address/message/baud）
        out = dr.run_digital_mode(os.path.join(_SENS, "pocsag.wav"), "pocsag",
                                  sample_rate=22050, baud=1200)
        assert out["ok"] is True, out.get("error")
        assert out["num_messages"] == 1
        m = out["messages"][0]
        assert m["address"] == 54321
        assert m["message"].rstrip() == "HELLO"
        assert m["baud"] == 1200

    def test_adsb_route_yields_known_altitude(self, tmp_path):
        from mbdsdr_ai.adsb import modulate_baseband

        fs = 4e6
        iq = modulate_baseband(_airborne_position_frame(alt_ft=35000),
                               fs=fs, lead_us=4.0)
        cf = _write_cf32(tmp_path / "pos.cf32", iq)

        out = dr.run_digital_mode(cf, "adsb", sample_rate=fs)

        assert out["ok"] is True, out.get("error")
        assert out["num_frames"] == 1
        f = out["frames"][0]
        assert f["crc_ok"] is True and f["icao"] == "780ABC"
        assert f["tc"] == 11
        parsed = f["frame"]
        assert parsed["altitude_ft"] == 35000
        assert parsed["cpr"]["cpr_lat"] == 0x0ABCD
        assert parsed["cpr"]["cpr_lon"] == 0x12345

    def test_ais_route_yields_known_type5_static_fields(self, tmp_path):
        from mbdsdr_ai import ais_decoder as ais

        payload = ais.build_type5(
            mmsi=477123456, imo=9876543, callsign="BOAB12",
            shipname="OCEAN STAR", ship_type=70, dim_bow=120, dim_stern=30,
            dim_port=8, dim_starboard=12, fix_type=1, eta_month=11,
            eta_day=4, eta_hour=12, eta_minute=30, draught_m=8.5,
            destination="SHANGHAI", dte=0, ais_version=1)
        iq = ais.ais_modulate_iq(ais.ais_frame_encode(payload), 48000.0)
        cf = _write_cf32(tmp_path / "ais5.cf32", iq)

        out = dr.run_digital_mode(cf, "ais", sample_rate=48000.0)

        assert out["ok"] is True, out.get("error")
        assert out["num_messages"] == 1
        m = out["messages"][0]
        assert m["type"] == 5
        assert m["mmsi"] == 477123456
        assert m["imo"] == 9876543
        assert m["callsign"] == "BOAB12"
        assert m["shipname"] == "OCEAN STAR"
        assert m["destination"] == "SHANGHAI"
        assert m["draught_m"] == pytest.approx(8.5, abs=0.05)


# ---------------------------------------------------------------------------
# 11. 结果分类七态：静音 / 缺前导 / 合规 三条反证互不混
# ---------------------------------------------------------------------------
class TestOutcomeClassification:
    """0 帧不能一律当「没信号」：ACARS 缺前导就是有信号却解不出。

    三条输入分别落到三种判定：
      静音      → empty / nothing_detected（信号不在）
      有信号缺前导 → error / preamble_not_found（输入约定不满足）
      有信号且合规 → decoded（真的解出 1 条）
    """

    def _acars_wav(self, tmp_path, name, bits):
        from mbdsdr_ai.acars_decoder import synthesize_msk_audio

        fs = 12500
        audio = synthesize_msk_audio(bits, fs, 1200)
        return _write_wav(tmp_path / name, audio, sr=fs), fs

    def test_silence_is_empty_not_error(self, tmp_path):
        fs = 12500
        wav = _write_wav(tmp_path / "silence.wav", np.zeros(fs * 2), sr=fs)

        out = dr.run_digital_mode(wav, "acars", sample_rate=fs)

        assert out["ok"] is False
        assert out["status"] == dr.STATUS_EMPTY
        assert out["error_code"] == dr.ERR_NOTHING_DETECTED
        assert out["signal_present"] is False
        assert out["num_messages"] == 0
        assert "error" not in out

    def test_signal_without_preamble_is_preamble_not_found(self, tmp_path):
        from mbdsdr_ai.acars_decoder import build_acars_frame, frame_bits_from_bytes

        # 只发 body（SOH 之后），不带 SYN,SYN,SOH 前导：有信号但约定不符
        body = build_acars_frame(mode="2", reg="N842UA", label="DF",
                                 block_id="6", text="WEATHER 1500Z")
        wav, fs = self._acars_wav(tmp_path, "no_preamble.wav",
                                  frame_bits_from_bytes(body))

        out = dr.run_digital_mode(wav, "acars", sample_rate=fs)

        assert out["ok"] is False
        assert out["status"] == "error"
        assert out["error_code"] == dr.ERR_PREAMBLE_NOT_FOUND
        assert out["signal_present"] is True
        assert out["preamble_hits"] == 0
        assert out["num_messages"] == 0
        assert "SYN,SYN,SOH" in out["error"]

    def test_signal_with_preamble_is_decoded(self, tmp_path):
        from mbdsdr_ai.acars_decoder import frame_bits_from_bytes

        wav, fs = self._acars_wav(tmp_path, "with_preamble.wav",
                                  frame_bits_from_bytes(_acars_over_air()))

        out = dr.run_digital_mode(wav, "acars", sample_rate=fs)

        assert out["ok"] is True
        assert out["status"] == dr.STATUS_DECODED
        assert out["error_code"] == ""
        assert out["preamble_hits"] >= 1
        assert out["num_messages"] == 1
