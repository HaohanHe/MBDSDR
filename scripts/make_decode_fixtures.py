"""scripts/make_decode_fixtures.py — 生成解码流水线 e2e 的随仓 IQ fixture。

用途：给 `tests/test_decode_pipeline_e2e.py` 提供**入库**的 .cf32 输入，
避免测试依赖仓外绝对路径或运行时下载。

诚实声明（写进 manifest，测试会校验字面量）：
  * 这些样本是「已知真值帧经仓内编码器重新调制」的 **synthetic** 波形，
    **不是 OTA 实录**（not OTA）；它们证明流水线各级与已知内容逐字节一致，
    不证明空口接收性能。
  * AIS 帧来自 `mbdsdr_ai.ais_decoder.build_type123/build_type5`；
    ADS-B 帧取自本仓真值参考 `tests/golden/dump1090_modes1_reference.txt`
    （dump1090 输出，CRC 已通过），再用 `mbdsdr_ai.adsb.modulate_baseband`
    重新调制。
  * APRS 帧由 `mbdsdr_ai.ax25` 的 AX.25 编码器构造（含 1 个带 digipeater
    路径），经 Bell 202 AFSK 1200bd 调制后再做 NFM（dev≈3kHz）搬移到复基带。
  * 采样率故意高于解码目标（AIS 240k→48k、ADS-B 8M→4M、APRS 96k→48k），
    让 downconvert 级真做多相抽取/鉴频，而不是恒等直通。
  * 三条 APRS 失败 fixture（噪声、截断、CRC 坏帧）也由本生成器产出，并在
    生成时用 `decode_pipeline.run_aprs_pipeline` 验证原因码。
  * APRS 的 `.wav` 入口 fixture（16-bit PCM 单声道，44.1k/48k）由**同一段
    cf32 IQ** 鉴频出的音频重采样、量化而来；生成时用
    `run_aprs_wav_pipeline` 反校验两帧逐字节与 cf32 一致。

用法：
    python scripts/make_decode_fixtures.py           # 生成/覆盖
    python scripts/make_decode_fixtures.py --check   # 只校验磁盘内容是否一致
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

OUT_DIR = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")
AIS_FILE = "ais_two_frames.cf32"
ADSB_FILE = "adsb_three_frames.cf32"
APRS_FILE = "aprs_two_frames.cf32"
APRS_WAV_44100_FILE = "aprs_two_frames_44100.wav"
APRS_WAV_48000_FILE = "aprs_two_frames_48000.wav"
APRS_NOISE_FILE = "aprs_noise.cf32"
APRS_TRUNCATED_FILE = "aprs_truncated.cf32"
APRS_CRC_FAIL_FILE = "aprs_crc_fail.cf32"
MANIFEST_FILE = "manifest.json"

AIS_FS = 240000.0
AIS_TARGET_FS = 48000.0
ADSB_FS = 8e6
ADSB_TARGET_FS = 4e6
APRS_FS = 96000.0
APRS_TARGET_FS = 48000.0
APRS_DEVIATION_HZ = 3000.0       # NFM 频偏（Bell 202 音调叠在其上）

AIS_GAP_BITS = 20               # 帧间静默（HDLC 空闲位）
ADSB_GAP_SAMPLES = 2000         # 帧间静默（8M 下 250µs）
APRS_GAP_SECONDS = 0.05         # 帧间静默（60 bit）
APRS_NOISE_SECONDS = 0.6

# 取自 tests/golden/dump1090_modes1_reference.txt 的 14 字节 DF17/18 帧
# （dump1090 已判定 CRC ok），只换行不换内容。
ADSB_FRAMES_HEX = [
    "8f4d2023587f345e35837e2218b2",
    "8d4d2023991094ad487c14fc9e3d",
    "8f4d20232004d0f4cb1820000d24",
]


def _cf32_bytes(iq: np.ndarray) -> bytes:
    arr = np.empty(iq.size * 2, dtype=np.float32)
    arr[0::2] = np.real(iq)
    arr[1::2] = np.imag(iq)
    return arr.tobytes()


def _build_ais():
    from mbdsdr_ai.ais_decoder import (ais_frame_encode, ais_modulate_iq,
                                       bits_to_bytes, build_type123, build_type5)

    p1 = build_type123(mmsi=366000001, lat_deg=37.0, lon_deg=-122.0,
                       sog_knots=10.0, cog_deg=45.0, heading=90, msg_type=1)
    p2 = build_type5(mmsi=366000002, imo=9876543, callsign="BI1AAA",
                     shipname="TEST SHIP")
    w1 = ais_frame_encode(p1)
    w2 = ais_frame_encode(p2)
    # 先在目标速率 48k 上按连续比特流调制（共享首尾标志 + 20 bit 静默），
    # 再整段 5 倍插值到 240k。这样 fixture 是「更高速率承载的带限波形」，
    # 而不是让 GMSK 脉冲在 25 样本/符号下另起一套形状——后者回抽后会
    # 丢掉第二帧（实测：直接 240k 调制 → 48k 只剩 1 帧）。
    wire = list(w1) + [0] * AIS_GAP_BITS + list(w2)
    base = ais_modulate_iq(wire, AIS_TARGET_FS)
    from scipy.signal import resample_poly
    iq = resample_poly(base, 5, 1, window=("kaiser", 5.0))

    fields = []
    for payload in (p1, p2):
        from mbdsdr_ai.ais_decoder import parse_payload
        d = parse_payload(payload)
        fields.append({"mmsi": d["mmsi"], "type": d["type"],
                       "callsign": d.get("callsign")})
    return iq, {
        "file": AIS_FILE,
        "fs_hz": AIS_FS,
        "target_fs_hz": AIS_TARGET_FS,
        "frame_count": 2,
        "samples": int(iq.size),
        "expected_payloads_hex": [bits_to_bytes(p).hex() for p in (p1, p2)],
        "expected_fields": fields,
    }


def _build_adsb():
    from mbdsdr_ai.adsb import (decode_frame, modulate_baseband)

    frames = [bytes.fromhex(h) for h in ADSB_FRAMES_HEX]
    parts, gap = [], np.zeros(ADSB_GAP_SAMPLES, dtype=np.complex128)
    for i, fr in enumerate(frames):
        parts.append(modulate_baseband(fr, fs=ADSB_FS, lead_us=0.0))
        if i != len(frames) - 1:
            parts.append(gap)
    iq = np.concatenate(parts)

    expected = []
    for fr in frames:
        d = decode_frame(fr)
        if d is None:
            raise RuntimeError(f"参考帧无法解析: {fr.hex()}")
        expected.append({"icao": d.icao_hex, "callsign": d.callsign,
                         "df": d.df, "tc": d.tc})
    return iq, {
        "file": ADSB_FILE,
        "fs_hz": ADSB_FS,
        "target_fs_hz": ADSB_TARGET_FS,
        "frame_count": len(frames),
        "samples": int(iq.size),
        "expected_frames_hex": [f.hex() for f in frames],
        "expected_frames": expected,
    }


def _nfm_iq(audio: np.ndarray, fs: float, deviation: float) -> np.ndarray:
    """把 AFSK 音频 FM 调制到复基带（瞬时频偏 = deviation × 归一化音频）。"""
    a = np.asarray(audio, dtype=np.float64)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak > 0:
        a = a / peak
    phase = np.cumsum(2.0 * np.pi * deviation * a / fs)
    return np.exp(1j * phase)


def _aprs_frames():
    from mbdsdr_ai.ax25 import (APRSPacket, APRSPosition,
                                build_aprs_position_frame)

    # 帧 1：带两级 digipeater 路径（WIDE1-1, WIDE2-2）
    f1 = build_aprs_position_frame("BI1AAA-1", 37.0, -122.0,
                                   comment="synthetic ax25 one",
                                   digipeaters=["WIDE1-1", "WIDE2-2"])
    # 帧 2：无 digipeater 路径（直接用 APRSPacket 构造，避开默认 WIDE2-2）
    pos2 = APRSPosition(latitude=39.9, longitude=116.4,
                        comment="synthetic ax25 two")
    packet2 = APRSPacket(source="BI1BBB", source_ssid=9,
                         destination="APRS", dest_ssid=0,
                         digipeaters=[], data_type="!",
                         payload=pos2.encode()[1:], position=pos2)
    return [f1, packet2.to_ax25_frame()]


def _aprs_frame_meta(frame) -> dict:
    return {
        "source": frame.source,
        "source_ssid": int(frame.source_ssid),
        "destination": frame.destination,
        "dest_ssid": int(frame.dest_ssid),
        "digipeaters": [[c, int(s), bool(r)] for c, s, r in frame.digipeaters],
        "control": int(frame.control),
        "pid": int(frame.pid),
        "info_hex": frame.info.hex(),
        "frame_hex": frame.to_bytes().hex(),
    }


def _afsk_audio_from_nrzi(modem, nrzi) -> np.ndarray:
    """按已编码的 NRZI 电平生成 Bell 202 音频（供故意坏 CRC 的 fixture 用）。"""
    spb = int(round(modem.samples_per_bit))
    freq = np.repeat(np.where(np.asarray(nrzi) == 1, modem.mark_freq,
                              modem.space_freq), spb)
    phase = np.cumsum(2.0 * np.pi * freq / modem.sample_rate)
    return np.sin(phase).astype(np.float32)


def _verify_aprs_blob(name: str, data: bytes, fs: float,
                      expected_reason=None, expected_frames=None) -> None:
    """生成时用真实流水线校验 fixture，避免「生成了但解不出」。"""
    import tempfile
    from mbdsdr_ai.decode_pipeline import run_aprs_pipeline

    fd, tmp = tempfile.mkstemp(suffix=".cf32")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        r = run_aprs_pipeline(tmp, fs=fs, target_fs=APRS_TARGET_FS)
    finally:
        os.unlink(tmp)

    if expected_reason is not None:
        if r["ok"] or r["reason"] != expected_reason or r["content"] != {}:
            raise RuntimeError(
                f"{name}: 期望失败 reason={expected_reason}，实际 "
                f"ok={r['ok']} reason={r['reason']}")
        return
    if not r["ok"] or r["num_frames"] != len(expected_frames):
        raise RuntimeError(
            f"{name}: 期望 {len(expected_frames)} 帧，实际 ok={r['ok']} "
            f"num_frames={r['num_frames']} reason={r['reason']}")
    for got, exp in zip(r["content"]["frames"], expected_frames):
        for key in ("source", "source_ssid", "destination", "dest_ssid",
                    "digipeaters", "info_hex", "frame_hex"):
            if got[key] != exp[key]:
                raise RuntimeError(f"{name}: 字段 {key} 不一致 "
                                   f"got={got[key]!r} exp={exp[key]!r}")


def _build_aprs():
    from mbdsdr_ai.ax25 import AFSKModem

    frames = _aprs_frames()
    modem = AFSKModem(sample_rate=APRS_FS)
    parts = []
    for i, frame in enumerate(frames):
        parts.append(modem.modulate(frame, preamble_bytes=20, postamble_bytes=4))
        if i != len(frames) - 1:
            parts.append(np.zeros(int(APRS_GAP_SECONDS * APRS_FS),
                                  dtype=np.float32))
    audio = np.concatenate(parts)
    iq = _nfm_iq(audio, APRS_FS, APRS_DEVIATION_HZ)
    return iq, {
        "file": APRS_FILE,
        "fs_hz": APRS_FS,
        "target_fs_hz": APRS_TARGET_FS,
        "frame_count": len(frames),
        "samples": int(iq.size),
        "signal": ("Bell 202 AFSK 1200bd (mark 1200Hz / space 2200Hz) "
                   f"NFM dev≈{APRS_DEVIATION_HZ:g}Hz"),
        "frames": [_aprs_frame_meta(f) for f in frames],
        "honesty": "synthetic remodulation; NOT OTA",
    }


def _build_aprs_failures():
    """三条失败 fixture：噪声无帧 / 帧中途截断 / 完整帧但 CRC 坏。"""
    from mbdsdr_ai.ax25 import AFSKModem, hdlc_bit_stuff

    modem = AFSKModem(sample_rate=APRS_FS)
    frames = _aprs_frames()
    out = {}

    # 1) 噪声：0.6s 确定性白噪声，生成器验证解调位流里没有 HDLC 0x7E 标志。
    noise_meta = {
        "file": APRS_NOISE_FILE,
        "fs_hz": APRS_FS,
        "target_fs_hz": APRS_TARGET_FS,
        "expected_reason": "nothing_detected",
        "expected_stage": "sync",
        "note": "noise-only segment；生成器逐 seed 校验无 HDLC 标志",
    }
    for seed in range(2000):
        rng = np.random.default_rng(seed)
        audio = rng.standard_normal(int(APRS_NOISE_SECONDS * APRS_FS))
        iq = _nfm_iq(audio, APRS_FS, APRS_DEVIATION_HZ)
        data = _cf32_bytes(iq)
        try:
            _verify_aprs_blob(APRS_NOISE_FILE, data, APRS_FS,
                              expected_reason="nothing_detected")
        except RuntimeError:
            continue
        noise_meta.update({"samples": int(iq.size), "seed": seed})
        out[APRS_NOISE_FILE] = (iq, noise_meta)
        break
    else:
        raise RuntimeError("找不到无 HDLC 标志的噪声 seed")

    # 2) 截断：把有效帧音频截到 55%，没有结束标志。
    full = modem.modulate(frames[0], preamble_bytes=20, postamble_bytes=4)
    cut = full[:int(len(full) * 0.55)]
    iq = _nfm_iq(cut, APRS_FS, APRS_DEVIATION_HZ)
    out[APRS_TRUNCATED_FILE] = (iq, {
        "file": APRS_TRUNCATED_FILE,
        "fs_hz": APRS_FS,
        "target_fs_hz": APRS_TARGET_FS,
        "expected_reason": "truncated_frame",
        "expected_stage": "frame",
        "note": "有效帧音频截到 55%，缺结束 0x7E 标志",
        "samples": int(iq.size),
    })

    # 3) CRC 失败：帧内容改 1 字节但保留原 FCS 与首尾标志。
    raw = bytearray(frames[0].to_bytes())
    raw[20] ^= 0x55
    stuffed = hdlc_bit_stuff(bytes(raw))
    wire = bytes([0x7E]) * 20 + stuffed + bytes([0x7E]) * 4
    audio = _afsk_audio_from_nrzi(modem, modem.encode_nrzi(wire))
    iq = _nfm_iq(audio, APRS_FS, APRS_DEVIATION_HZ)
    out[APRS_CRC_FAIL_FILE] = (iq, {
        "file": APRS_CRC_FAIL_FILE,
        "fs_hz": APRS_FS,
        "target_fs_hz": APRS_TARGET_FS,
        "expected_reason": "crc_failed",
        "expected_stage": "frame",
        "note": "完整帧但 info 字节翻转、FCS 不匹配",
        "samples": int(iq.size),
    })

    for name, (iq, meta) in out.items():
        _verify_aprs_blob(name, _cf32_bytes(iq), APRS_FS,
                          expected_reason=meta["expected_reason"])
    return out


def _fm_discriminate_audio(iq: np.ndarray, fs: float) -> np.ndarray:
    """从 NFM 复基带还原音频（与 decode_pipeline 的 downconvert 同一算法）。"""
    from scipy.signal import butter, sosfiltfilt

    z = np.asarray(iq, dtype=np.complex128)
    z = z - z.mean()
    inst = np.angle(z[1:] * np.conj(z[:-1])) * (fs / (2.0 * np.pi))
    sos = butter(4, min(4500.0, fs / 2.0 - 1.0) / (fs / 2.0),
                 btype="low", output="sos")
    return sosfiltfilt(sos, inst)


def _resample_audio(audio: np.ndarray, src: float, dst: float) -> np.ndarray:
    from math import gcd
    from scipy.signal import resample_poly

    s, d = int(round(src)), int(round(dst))
    g = gcd(s, d)
    return resample_poly(np.asarray(audio, dtype=np.float64),
                         d // g, s // g, window=("kaiser", 5.0))


def _wav_pcm16_bytes(audio: np.ndarray, sample_rate: float) -> bytes:
    """单声道 16-bit PCM WAV（峰值归一到 90% 满量程，避免削顶）。"""
    import io
    import wave

    a = np.asarray(audio, dtype=np.float64)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak > 0:
        a = a / peak * (32767.0 * 0.9)
    pcm = np.clip(np.round(a), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(sample_rate)))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _build_aprs_wavs(aprs_iq: np.ndarray, aprs_meta: dict):
    """从 aprs_two_frames.cf32 的同一 IQ 生成 44.1k/48k 的 16-bit PCM wav。"""
    audio96 = _fm_discriminate_audio(aprs_iq, APRS_FS)
    out = {}
    for name, sr in ((APRS_WAV_44100_FILE, 44100.0),
                     (APRS_WAV_48000_FILE, 48000.0)):
        audio = _resample_audio(audio96, APRS_FS, sr)
        data = _wav_pcm16_bytes(audio, sr)
        meta = {
            "file": name,
            "sample_rate_hz": sr,
            "encoding": "PCM 16-bit mono",
            "frame_count": aprs_meta["frame_count"],
            "frames": aprs_meta["frames"],
            "generation": ("从 aprs_two_frames.cf32 复基带做 FM 鉴频 + 4.5kHz "
                           f"低通，再重采样到 {sr:g}Hz 并量化为 16-bit PCM"),
            "honesty": "synthetic remodulation; NOT OTA",
        }
        out[name] = (data, meta)
    return out


def _verify_aprs_wav_blob(name: str, data: bytes, expected_frames) -> None:
    import tempfile
    from mbdsdr_ai.decode_pipeline import run_aprs_wav_pipeline

    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        r = run_aprs_wav_pipeline(tmp)
    finally:
        os.unlink(tmp)
    if not r["ok"] or r["num_frames"] != len(expected_frames):
        raise RuntimeError(
            f"{name}: 期望 {len(expected_frames)} 帧，实际 ok={r['ok']} "
            f"num_frames={r['num_frames']} reason={r['reason']}")
    for got, exp in zip(r["content"]["frames"], expected_frames):
        for key in ("source", "source_ssid", "destination", "dest_ssid",
                    "digipeaters", "info_hex", "frame_hex"):
            if got[key] != exp[key]:
                raise RuntimeError(f"{name}: 字段 {key} 不一致 "
                                   f"got={got[key]!r} exp={exp[key]!r}")


def build_all():
    ais_iq, ais_meta = _build_ais()
    adsb_iq, adsb_meta = _build_adsb()
    aprs_iq, aprs_meta = _build_aprs()
    _verify_aprs_blob(APRS_FILE, _cf32_bytes(aprs_iq), APRS_FS,
                      expected_frames=aprs_meta["frames"])
    aprs_wavs = _build_aprs_wavs(aprs_iq, aprs_meta)
    for name, (data, meta) in aprs_wavs.items():
        _verify_aprs_wav_blob(name, data, meta["frames"])
    aprs_failures = _build_aprs_failures()
    manifest = {
        "generated_by": "scripts/make_decode_fixtures.py",
        "license": "MIT",
        "honesty": ("synthetic remodulation of known-truth frames with in-repo "
                    "encoders; NOT OTA recordings. Proves stage-by-stage "
                    "byte-exactness, not over-the-air reception performance."),
        "ais": ais_meta,
        "adsb": adsb_meta,
        "aprs": aprs_meta,
        "aprs_wav": {
            "source": APRS_FILE,
            "generation": ("FM discriminator of the same cf32 IQ + 4.5kHz lowpass, "
                           "resampled to 44.1k/48k and quantized to 16-bit PCM mono"),
            "files": [meta for _data, meta in aprs_wavs.values()],
        },
        "aprs_failures": {name: meta for name, (_iq, meta)
                          in aprs_failures.items()},
    }
    blobs = {AIS_FILE: _cf32_bytes(ais_iq),
             ADSB_FILE: _cf32_bytes(adsb_iq),
             APRS_FILE: _cf32_bytes(aprs_iq)}
    for name, (data, _meta) in aprs_wavs.items():
        blobs[name] = data
    for name, (iq, _meta) in aprs_failures.items():
        blobs[name] = _cf32_bytes(iq)
    return blobs, manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验解码流水线 e2e fixture")
    ap.add_argument("--check", action="store_true",
                    help="只校验磁盘 fixture 与重新生成结果一致（不写盘）")
    args = ap.parse_args(argv)

    blobs, manifest = build_all()
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        failures = []
        for name, data in blobs.items():
            p = os.path.join(OUT_DIR, name)
            if not os.path.exists(p):
                failures.append(f"缺失 {name}")
                continue
            if open(p, "rb").read() != data:
                failures.append(f"内容不一致 {name}")
        p = os.path.join(OUT_DIR, MANIFEST_FILE)
        if not os.path.exists(p) or open(p, encoding="utf-8").read() != manifest_text:
            failures.append(f"内容不一致 {MANIFEST_FILE}")
        if failures:
            print("FAIL: " + "; ".join(failures))
            return 1
        print(f"OK: {len(blobs)} 个 fixture（cf32/wav）+ manifest 与生成器一致")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    for name, data in blobs.items():
        with open(os.path.join(OUT_DIR, name), "wb") as f:
            f.write(data)
        print(f"write {name}: {len(data)} bytes")
    with open(os.path.join(OUT_DIR, MANIFEST_FILE), "w", encoding="utf-8") as f:
        f.write(manifest_text)
    print(f"write {MANIFEST_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
