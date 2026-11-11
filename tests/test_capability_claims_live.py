"""能力表声明 ↔ 实跑对照：每条「支持的输入格式/采样率」至少一条真解码用例。

为什么不写「零填充桩也能过」：
  `test_digital_modes_capability.py` 只校验声明结构；这里每条断言都必须
  真的解出东西（`ok=True` 且无 `decode_error`）。声明了却解不出，就该改声明
  或改实现，不许留「表里有、代码没有」。

全部输入都是仓内合成 fixture（`provenance: self-generated`，NOT OTA）。
"""

from __future__ import annotations

import math
import os
import sys
import wave

import numpy as np
import pytest
from scipy.signal import resample_poly

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.decode_router import run_digital_mode  # noqa: E402

SENS = os.path.join(ROOT, "tests", "fixtures", "sensitivity")
PIPE = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")


def _read_cf32(path: str) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.float32)
    return raw[0::2] + 1j * raw[1::2]


def _write_cf32(path: str, iq: np.ndarray) -> None:
    view = np.empty(iq.size * 2, dtype=np.float32)
    view[0::2] = iq.real
    view[1::2] = iq.imag
    view.tofile(path)


def _write_wav(path: str, audio: np.ndarray, fs: int) -> None:
    a = np.asarray(audio, dtype=np.float64)
    peak = float(np.max(np.abs(a))) or 1.0
    pcm = (np.clip(a / peak * 0.9, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(pcm.tobytes())


def _read_wav(path: str):
    with wave.open(path, "rb") as w:
        fs = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0, fs


def _cf32_to_passband_wav(cf32_path: str, fs_in: float, fs_out: int,
                          center_hz: float, out_path: str) -> None:
    """复基带 → 上采样到 fs_out → 搬移到 center_hz → 取实部存 PCM16。"""
    iq = _read_cf32(cf32_path)
    up, down = int(fs_out), int(round(fs_in))
    g = math.gcd(up, down)
    z = resample_poly(iq, up // g, down // g)
    n = np.arange(z.size)
    _write_wav(out_path, np.real(z * np.exp(2j * np.pi * center_hz * n / fs_out)),
               fs_out)


def _ok(result: dict, mode: str) -> None:
    assert result.get("ok") is True, (
        f"{mode} 声明支持该输入，实测未解出：{result.get('reason') or result}")
    assert not result.get("decode_error"), (
        f"{mode} 解出但带 decode_error={result.get('decode_error')!r}")


def test_ft4_wav_input_is_proven(tmp_path):
    """FT4 声明 .wav 实音频；把仓内 IQ fixture 搬到 1500 Hz 存 wav 必须解出。"""
    wav = str(tmp_path / "ft4.wav")
    _cf32_to_passband_wav(os.path.join(SENS, "ft4.cf32"), 12000.0, 12000, 1500.0,
                          wav)
    r = run_digital_mode(wav, "ft4", sample_rate=12000.0)
    _ok(r, "ft4")
    texts = [m.get("text") for m in r.get("messages", [])]
    assert "CQ BI1AAA OM74" in texts, f"FT4 wav 解出的文本不对: {texts}"


def test_wspr_wav_input_is_proven(tmp_path):
    """WSPR 声明 .wav 实音频 12 kHz；375 Hz 复基带上搬后必须解出。"""
    wav = str(tmp_path / "wspr.wav")
    _cf32_to_passband_wav(os.path.join(SENS, "wspr.cf32"), 375.0, 12000, 1500.0,
                          wav)
    r = run_digital_mode(wav, "wspr", sample_rate=12000.0,
                         center_frequency_hz=1500.0)
    _ok(r, "wspr")
    calls = [m.get("callsign") for m in r.get("messages", [])]
    assert "BI1AAA" in calls, f"WSPR wav 解出的呼号不对: {calls}"


@pytest.mark.parametrize("mode,fixture,rate", [
    ("pocsag", "pocsag.wav", 22050.0),
    ("acars", None, 48000.0),
    ("navtex", "navtex.wav", 12000.0),
])
def test_audio_modes_declared_rate_is_proven(tmp_path, mode, fixture, rate):
    """三个音频模式声明「任意 fs」，至少要在入库 fixture 的采样率上真解出。"""
    if mode == "acars":
        # 入库的 sensitivity/acars.wav 是 acars_protocol 的 2400 bd 波形，
        # 超出生产路由声明的 1200 bd 能力（此前 0 帧被当成 ok=True 才「通过」）。
        # 这里按生产协议合成 1200 bd、带 SYN,SYN,SOH 前导的帧，真正证明采样率支持。
        from mbdsdr_ai.acars_decoder import (SOH, SYN, build_acars_frame,
                                             frame_bits_from_bytes,
                                             synthesize_msk_audio)
        body = build_acars_frame(mode="2", reg="BA1234", label="H1",
                                 block_id="1", text="TEST MESSAGE")
        bits = frame_bits_from_bytes(bytes([SYN, SYN, SOH]) + body)
        audio = synthesize_msk_audio(bits, rate, 1200)
        path = str(tmp_path / "acars_1200.wav")
        _write_wav(path, audio, int(rate))
    else:
        path = os.path.join(SENS, fixture)
    kwargs = {}
    if mode == "pocsag":
        # 入库 fixture 只有 0.93s，比能力表声明的最短 1152 bit（0.96s @1200bd）
        # 略短；补尾零到声明门槛以上，证明的是「格式/采样率支持」而非「更短也能解」。
        audio, _ = _read_wav(path)
        need = int(math.ceil(1152 / 1200.0 * rate)) + int(rate * 0.05)
        if audio.size < need:
            audio = np.concatenate([audio, np.zeros(need - audio.size)])
        path = str(tmp_path / "pocsag_padded.wav")
        _write_wav(path, audio, int(rate))
        kwargs["baud"] = 1200
    r = run_digital_mode(path, mode, sample_rate=rate, **kwargs)
    _ok(r, mode)
    assert r.get("audio_sample_rate_hz") in (None, rate), (
        f"{mode} 实际读到的采样率与声明不符: {r.get('audio_sample_rate_hz')}")


@pytest.mark.parametrize("target_fs", [8000, 44100, 48000])
def test_aprs_wav_any_rate_is_proven(tmp_path, target_fs):
    """APRS 声明「.wav 任意 fs ≥ 4800Hz（8k/44.1k/48k）」，逐档必须解出 2 帧。"""
    audio48, fs48 = _read_wav(os.path.join(PIPE, "aprs_two_frames_48000.wav"))
    g = math.gcd(target_fs, fs48)
    audio = resample_poly(audio48, target_fs // g, fs48 // g)
    path = str(tmp_path / f"aprs_{target_fs}.wav")
    _write_wav(path, audio, target_fs)
    r = run_digital_mode(path, "aprs", sample_rate=float(target_fs))
    _ok(r, "aprs")
    assert int(r.get("num_frames") or 0) >= 2, r


def test_aprs_cf32_min_declared_rate_is_proven(tmp_path):
    """APRS 声明 .cf32 fs ≥ 16000Hz；96k fixture 带限降到 16k 必须仍解出 2 帧。"""
    iq = _read_cf32(os.path.join(PIPE, "aprs_two_frames.cf32"))
    g = math.gcd(96000, 16000)
    iq16 = resample_poly(iq, 16000 // g, 96000 // g)
    path = str(tmp_path / "aprs_16000.cf32")
    _write_cf32(path, iq16)
    r = run_digital_mode(path, "aprs", sample_rate=16000.0)
    _ok(r, "aprs")
    assert int(r.get("num_frames") or 0) >= 2, r


def test_ais_declared_rate_is_proven():
    """AIS 声明 fs 为 9600 整数倍且 ≥19200；入库 240k fixture 必须解出帧。"""
    r = run_digital_mode(os.path.join(PIPE, "ais_two_frames.cf32"), "ais",
                         sample_rate=240000.0)
    _ok(r, "ais")
    assert int(r.get("num_messages") or 0) >= 1, r


def test_adsb_declared_rate_is_proven():
    """ADS-B 声明 fs ≥2MHz 且 1MHz 整数倍；入库 8M fixture 必须多帧 CRC 通过。"""
    r = run_digital_mode(os.path.join(PIPE, "adsb_three_frames.cf32"), "adsb",
                         sample_rate=8_000_000.0)
    _ok(r, "adsb")
    assert int(r.get("num_frames") or 0) >= 1, r
    assert int(r.get("decode_fs_hz") or 0) == 8_000_000, (
        f"直通档不该换采样率: {r.get('decode_fs_hz')}")
