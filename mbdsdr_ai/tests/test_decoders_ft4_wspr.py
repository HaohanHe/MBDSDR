# SPDX-License-Identifier: MIT
"""decode_digital_mode 的 FT4/WSPR WAV 入口与可选独立样本复验。

仓内合成样本只证明接线；仓外官方样本存在时才运行跨实现复验，缺失明确
skip，绝不生成替代品。两类测试都不构成 OTA 或弱信号灵敏度验收。
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
import wave

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mbdsdr_ai.decoders import decode_digital_mode  # noqa: E402
from mbdsdr_ai.ft4_codec import build_frame, encode_ft4_tones  # noqa: E402
from mbdsdr_ai.ft4_modem import FRAME_SAMPLES, Ft4Modulator  # noqa: E402
from mbdsdr_ai.ft8_encode import pack77_standard  # noqa: E402
from mbdsdr_ai.wspr_modem import (  # noqa: E402
    REFERENCE_SAMPLE_RATE_HZ,
    REFERENCE_SAMPLES_PER_SYMBOL,
    WSPR_SYMBOL_COUNT,
    encode_audio,
)

FT4_FREQUENCY_SEARCH_HZ = (1420.0, 1580.0)
WSPR_FREQUENCY_GRID = tuple(np.arange(-1.5, 1.51, 0.25))
WSPR_TIME_GRID = tuple(np.arange(0.0, 1.01, 0.25))
FT4_OFFICIAL_ROOT = Path(
    "D:/mbdsdr/reference/wsjtx/967c85a6119110788b0df3782cf796485b2cee14"
)
FT4_OFFICIAL_WAV = FT4_OFFICIAL_ROOT / "samples/FT4/000000_000002.wav"
FT4_OFFICIAL_EVIDENCE = FT4_OFFICIAL_ROOT / "official_ft4_decode_evidence.json"
FT4_OFFICIAL_SHA256 = "d9e91fa04ba138a7b9f41b4103823c77ca1c3a9775101f6b14d60935bcd3813b"
WSPR_OFFICIAL_C2 = Path("D:/mbdsdr/reference/wspr_audio/261102_0000.c2")
WSPR_OFFICIAL_SHA256 = "aafe600e4214e05c15882fffadc8f83b92f5be3cf77c3cc00f78425515ea4046"


def _write_pcm16_wav(path, samples, sample_rate, channels=1):
    values = np.asarray(samples, dtype=np.float64)
    pcm = np.rint(np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2")
    if channels > 1:
        pcm = np.repeat(pcm[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _external_bytes(path):
    if not path.is_file():
        pytest.skip(f"外部独立证据不存在，按约定跳过: {path}")
    return path.read_bytes()


def _write_official_c2_as_real_wav(raw, output_path):
    """C2 float32 I/Q 只做 +80 Hz 混频、取实部及固定 PCM16 映射。"""
    name, wspr_type, _ = struct.unpack_from("<14sid", raw, 0)
    assert name.decode("ascii") == "261102_0000.c2"
    assert wspr_type == 2
    interleaved = np.frombuffer(raw, dtype="<f4", offset=26)
    assert interleaved.size == 45000 * 2
    pairs = interleaved.reshape(45000, 2)
    # 与官方 readc2file 一致：I=buffer[2*i]，Q=-buffer[2*i+1]。
    samples = pairs[:, 0].astype(np.float64) - 1j * pairs[:, 1].astype(np.float64)
    times = np.arange(samples.size, dtype=np.float64) / 375.0
    real = (samples * np.exp(2j * np.pi * 80.0 * times)).real
    # 不做归一化、额外增益或滤波；32767 仅是 [-1,1] 到 PCM16 的固定映射。
    assert float(np.max(np.abs(real))) <= 1.0
    pcm = np.rint(real * 32767.0).astype("<i2")
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(375)
        wav.writeframes(pcm.tobytes())


@pytest.fixture(scope="module")
def synthetic_ft4_audio():
    message = pack77_standard("CQ", "BI1AAA", "OM74")
    frame = build_frame(encode_ft4_tones(message))
    capture = Ft4Modulator().modulate(
        frame, freq_offset_hz=1500.0, time_offset_samples=731,
    )
    return np.concatenate((capture.real, np.zeros(503)))


@pytest.fixture(scope="module")
def synthetic_wspr_audio():
    return encode_audio(
        "BI1AAA", "FN20", 37,
        sample_rate_hz=12000.0,
        center_frequency_hz=1500.75,
        complex_baseband=False,
        amplitude=0.7,
        leading_silence_seconds=0.5,
        trailing_silence_seconds=1.0,
    )


@pytest.mark.parametrize("channels", [1, 2])
def test_ft4_wav_mono_and_stereo_decode_through_real_receiver(
    tmp_path, synthetic_ft4_audio, channels,
):
    path = tmp_path / f"ft4-{channels}ch.wav"
    _write_pcm16_wav(path, synthetic_ft4_audio, 12000, channels)
    result = decode_digital_mode(
        str(path), "ft4", output_dir=str(tmp_path / "out"),
        frequency_search_hz=FT4_FREQUENCY_SEARCH_HZ,
    )

    assert result["ok"] is True, result
    assert result["status"] == "decoded"
    assert result["rejected"] is False
    assert result["found"] is True
    assert result["num_frames"] == result["num_messages"] == 1
    assert result["reject_reason"] == ""
    assert result["parity_ok"] is True
    assert result["crc_ok"] is True
    assert result["text"] == "CQ BI1AAA OM74"
    assert result["messages"][0]["text"] == result["text"]
    assert result["sync"]["synced"] is True
    assert result["frequency_hz"] == pytest.approx(1500.0, abs=0.25)
    assert result["time_offset_seconds"] == pytest.approx(731 / 12000, abs=8 / 12000)
    assert result["audio_sample_rate_hz"] == 12000.0
    assert result["decode_sample_rate_hz"] == 12000.0


@pytest.mark.parametrize("channels", [1, 2])
def test_wspr_wav_mono_and_stereo_decode_on_caller_grid(
    tmp_path, synthetic_wspr_audio, channels,
):
    path = tmp_path / f"wspr-{channels}ch.wav"
    _write_pcm16_wav(path, synthetic_wspr_audio, 12000, channels)
    result = decode_digital_mode(
        str(path), "wspr", output_dir=str(tmp_path / "out"),
        center_frequency_hz=1500.0,
        frequency_offsets_hz=WSPR_FREQUENCY_GRID,
        time_offsets_seconds=WSPR_TIME_GRID,
    )

    assert result["ok"] is True, result
    assert result["status"] == "decoded"
    assert result["rejected"] is False
    assert result["found"] is True
    assert result["num_frames"] == result["num_messages"] == 1
    assert result["reject_reason"] == ""
    assert result["parity_ok"] is None
    assert result["crc_ok"] is None
    message = result["messages"][0]
    assert message["callsign"] == "BI1AAA"
    assert message["grid4"] == "FN20"
    assert message["power_dbm"] == 37
    assert len(message["bits"]) == 50
    assert result["frequency_hz"] == pytest.approx(1500.75)
    assert result["time_offset_seconds"] == pytest.approx(0.5, abs=1 / 375 + 1e-6)
    assert result["sync_metric"] > 0.99
    assert result["audio_sample_rate_hz"] == 12000.0
    assert result["decode_sample_rate_hz"] == REFERENCE_SAMPLE_RATE_HZ


@pytest.mark.parametrize("mode", ["ft4", "wspr"])
def test_narrowband_modes_reject_raw_iq_instead_of_fm_fallback(tmp_path, mode):
    path = tmp_path / f"{mode}.cf32"
    path.write_bytes(np.zeros(16, dtype=np.float32).tobytes())
    result = decode_digital_mode(str(path), mode, output_dir=str(tmp_path / "out"))

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["rejected"] is True
    assert result["reject_reason"] == "unsupported_format"
    assert result["num_frames"] == 0
    assert ".wav" in result["error"]
    assert "宽带 FM 鉴频" in result["error"]
    assert "spectral_peak" not in result


@pytest.mark.parametrize("mode", ["ft4", "wspr"])
def test_narrowband_modes_reject_malformed_wav(tmp_path, mode):
    path = tmp_path / f"broken-{mode}.wav"
    path.write_bytes(b"not a RIFF/WAVE file")
    result = decode_digital_mode(str(path), mode, output_dir=str(tmp_path / "out"))

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["rejected"] is True
    assert result["reject_reason"] == "invalid_wav"
    assert result["num_frames"] == 0
    assert "读取 WAV 失败" in result["error"]


@pytest.mark.parametrize(
    "mode,sample_rate,sample_count,center_hz",
    [
        ("ft4", 12000, FRAME_SAMPLES + 1200, 1500.0),
        (
            "wspr", int(REFERENCE_SAMPLE_RATE_HZ),
            WSPR_SYMBOL_COUNT * REFERENCE_SAMPLES_PER_SYMBOL + 375, 80.0,
        ),
    ],
)
def test_valid_wav_with_silence_is_empty_not_success_or_error(
    tmp_path, mode, sample_rate, sample_count, center_hz,
):
    path = tmp_path / f"silence-{mode}.wav"
    _write_pcm16_wav(path, np.zeros(sample_count), sample_rate)
    result = decode_digital_mode(
        str(path), mode, output_dir=str(tmp_path / "out"),
        center_frequency_hz=center_hz,
    )

    assert result["ok"] is False
    assert result["status"] == "empty"
    assert result["rejected"] is False
    assert result["found"] is False
    assert result["messages"] == []
    assert result["num_frames"] == result["num_messages"] == 0
    assert result["reject_reason"] == "no_signal"
    assert "error" not in result
    if mode == "ft4":
        assert result["parity_ok"] is False
        assert result["crc_ok"] is False
        assert result["frequency_hz"] is None
        assert result["time_offset_seconds"] is None
    else:
        assert result["parity_ok"] is None
        assert result["crc_ok"] is None


def test_wspr_invalid_candidate_grid_is_an_explicit_rejection(tmp_path):
    path = tmp_path / "wspr-invalid-grid.wav"
    size = WSPR_SYMBOL_COUNT * REFERENCE_SAMPLES_PER_SYMBOL
    _write_pcm16_wav(path, np.zeros(size), int(REFERENCE_SAMPLE_RATE_HZ))
    result = decode_digital_mode(
        str(path), "wspr", output_dir=str(tmp_path / "out"),
        center_frequency_hz=80.0, frequency_offsets_hz=[],
    )

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["rejected"] is True
    assert result["reject_reason"] == "invalid_input"
    assert "频偏搜索网格" in result["error"]


def test_ft8_note_describes_internal_capability_without_claiming_wav_wiring(tmp_path):
    path = tmp_path / "ft8-note.wav"
    _write_pcm16_wav(path, np.zeros(12000), 12000)
    result = decode_digital_mode(str(path), "ft8", output_dir=str(tmp_path / "out"))

    assert "仓内已有 FT8" in result["note"]
    assert "LDPC(174,91)" in result["note"]
    assert "本通用入口尚未接线" in result["note"]
    assert "完整解码依赖" not in result["note"]


def test_official_ft4_wav_cross_decode_counts_unique_payloads(tmp_path):
    raw = _external_bytes(FT4_OFFICIAL_WAV)
    assert hashlib.sha256(raw).hexdigest() == FT4_OFFICIAL_SHA256
    evidence = json.loads(_external_bytes(FT4_OFFICIAL_EVIDENCE))
    assert evidence["source"]["sha256"] == FT4_OFFICIAL_SHA256
    expected = evidence["unique_valid_decodes"]

    result = decode_digital_mode(
        str(FT4_OFFICIAL_WAV), "ft4", output_dir=str(tmp_path / "out"),
        frequency_search_hz=(100.0, 3000.0),
    )

    assert result["ok"] is True, result
    assert result["status"] == "decoded"
    assert result["candidate_count"] == evidence["method"]["candidate_count"]
    assert result["accepted_candidate_count"] == evidence["method"]["accepted_payload_count"]
    assert result["num_frames"] == result["num_messages"] == len(expected)
    assert len(result["messages"]) == len(expected)
    assert result["duplicate_payload_count"] == (
        result["accepted_candidate_count"] - len(expected)
    )
    actual_payloads = [
        "".join(str(bit) for bit in message["data_bits"])
        for message in result["messages"]
    ]
    assert actual_payloads == [row["data_bits_hex"] for row in expected]
    assert len(set(actual_payloads)) == len(expected)
    assert [message["text"] for message in result["messages"]] == [
        row["text"] for row in expected
    ]
    assert [message["unpacked"] for message in result["messages"]] == [
        row["unpacked"] for row in expected
    ]
    assert all(len(message["data_bits"]) == 77 for message in result["messages"])
    assert all(message["parity_ok"] is True for message in result["messages"])
    assert all(message["crc_ok"] is True for message in result["messages"])


def test_official_wspr_c2_real_wav_cross_decode_on_caller_grid(tmp_path):
    raw = _external_bytes(WSPR_OFFICIAL_C2)
    assert hashlib.sha256(raw).hexdigest() == WSPR_OFFICIAL_SHA256
    evidence_path = WSPR_OFFICIAL_C2.with_name("cross_decode_result.json")
    evidence = json.loads(_external_bytes(evidence_path))
    assert evidence["source"]["sample_sha256"] == WSPR_OFFICIAL_SHA256
    expected = evidence["decoded"]
    path = tmp_path / "official-wspr-real80.wav"
    _write_official_c2_as_real_wav(raw, path)

    result = decode_digital_mode(
        str(path), "wspr", output_dir=str(tmp_path / "out"),
        center_frequency_hz=80.0,
        frequency_offsets_hz=WSPR_FREQUENCY_GRID,
        time_offsets_seconds=tuple(np.arange(0.0, 1.51, 0.25)),
    )

    assert result["ok"] is True, result
    assert result["status"] == "decoded"
    assert result["num_frames"] == result["num_messages"] == 1
    assert len(result["messages"]) == 1
    message = result["messages"][0]
    assert message["callsign"] == expected["callsign"]
    assert message["grid4"] == expected["grid4"]
    assert message["power_dbm"] == expected["power_dbm"]
    assert len(message["bits"]) == 50
    # 测试转换仅把整个频谱平移 +80 Hz；原证据中心为 +0.75 Hz。
    assert result["frequency_hz"] == pytest.approx(expected["frequency_hz"] + 80.0)
    assert result["time_offset_seconds"] == pytest.approx(
        expected["time_offset_seconds"], abs=1 / 375 + 1e-6,
    )
    assert message["start_sample"] == expected["start_sample"]
    assert result["sync_metric"] > 0.98
    assert result["parity_ok"] is None and result["crc_ok"] is None
    assert hashlib.sha256(WSPR_OFFICIAL_C2.read_bytes()).hexdigest() == WSPR_OFFICIAL_SHA256
