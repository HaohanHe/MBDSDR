"""scripts/make_sensitivity_fixtures.py — 灵敏度基线的合成 fixture 生成器。

为什么要有这个脚本：
  「灵敏度」结论必须能被复跑。拿运行期临时合成的波形去扫 SNR，别人无法
  核对到底测了什么；因此先把**clean 合成信号**落盘入库，再由
  ``scripts/decode_sensitivity.py`` 从盘上读取、只重采样噪声。

诚实声明（写进 manifest，也写进 provenance）：
  * 全部波形由**本仓编码器**合成，``provenance = self-generated``，
    ``honesty = synthetic / NOT OTA``；没有任何一条是空中实录。
  * 合成信号没有多径、没有采样时钟误差、没有邻道干扰；这里的 SNR 是
    「噪声带宽 = fs/2 的每样本平均功率比」，跨模式/跨域不可横比。
  * AIS / ADS-B / APRS 不在此处重复生成：仓内已有
    ``tests/fixtures/decode_pipeline/`` 的同类合成 fixture，灵敏度脚本直接复用。

产物（``tests/fixtures/sensitivity/``，单目录、扁平文件）：
  pocsag.wav  acars.wav  navtex.wav  apt.wav   （音频域，PCM16 单声道）
  ft4.cf32    wspr.cf32  lrpt.cf32              （复基带，交错 float32）
  manifest.json   provenance.json

复跑：
    python scripts/make_sensitivity_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import wave
from datetime import date

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "sensitivity")
TODAY = date(2026, 11, 6).isoformat()

# 固定合成真值（解码侧按同样字段比对）。
POCSAG_TRUTH = {"address": 54321, "message": "HELLO", "baud": 1200}
ACARS_TRUTH = {"mode": "2", "reg": "BA1234", "label": "H1",
               "text": "TEST MESSAGE", "baud": 2400}
NAVTEX_TRUTH = {"b1": "C", "b2": "A", "serial": "12",
                "text": "SENSITIVITY BASELINE TEST MESSAGE",
                # build_message 会在正文后补报文尾 'N'（EOT），解码器原样保留；
                # 真值同时登记「正文」与「clean 波形实际解码文本」，避免拿后者
                # 冒充前者、也避免比对时偷摸 strip 掉真正的差异。
                "text_decoded": "\r\nSENSITIVITY BASELINE TEST MESSAGE\r\nN"}
APT_LINES = 20
# N 行图能对齐的只有 N-1 段（每段以同步脉冲开头，末行后没有下一个同步），
# 这是 APT 帧结构本身决定的，不是解码丢行。
APT_LINES_DECODABLE = APT_LINES - 1
FT4_TRUTH = {"text": "CQ BI1AAA OM74"}
WSPR_TRUTH = {"callsign": "BI1AAA", "grid": "OM74", "power_dbm": 10}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_wav(path: str, audio, fs: int) -> None:
    a = np.asarray(audio, dtype=np.float64)
    a = a - float(np.mean(a))
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak <= 0.0:
        raise ValueError(f"{os.path.basename(path)} 合成结果为空")
    pcm = np.clip(a / peak * 0.9, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        w.writeframes(pcm16.tobytes())


def _write_cf32(path: str, iq) -> None:
    z = np.asarray(iq, dtype=np.complex64)
    view = np.empty(z.size * 2, dtype=np.float32)
    view[0::2] = z.real
    view[1::2] = z.imag
    view.tofile(path)


def build_pocsag() -> dict:
    from mbdsdr_ai.pocsag_decoder import pocsag_encode

    fs = 22050
    audio = pocsag_encode(POCSAG_TRUTH["address"], POCSAG_TRUTH["message"],
                          POCSAG_TRUTH["baud"], sample_rate=fs)
    path = os.path.join(FIXTURE_DIR, "pocsag.wav")
    _write_wav(path, audio, fs)
    return {
        "file": "pocsag.wav", "domain": "audio", "fs_hz": fs,
        "samples": int(np.size(audio)), "truth": POCSAG_TRUTH,
        "condition": f"FSK 音频 {POCSAG_TRUTH['baud']} bd，地址 "
                     f"{POCSAG_TRUTH['address']} / {POCSAG_TRUTH['message']}",
        "generation": "mbdsdr_ai.pocsag_decoder.pocsag_encode",
    }


def build_acars() -> dict:
    from mbdsdr_ai.acars_protocol import acars_encode

    fs = 48000
    audio = acars_encode(ACARS_TRUTH["mode"], reg=ACARS_TRUTH["reg"],
                         label=ACARS_TRUTH["label"],
                         text=ACARS_TRUTH["text"],
                         baud=ACARS_TRUTH["baud"], sample_rate=fs)
    path = os.path.join(FIXTURE_DIR, "acars.wav")
    _write_wav(path, audio, fs)
    return {
        "file": "acars.wav", "domain": "audio", "fs_hz": fs,
        "samples": int(np.size(audio)), "truth": dict(ACARS_TRUTH),
        "condition": f"MSK 音频 {ACARS_TRUTH['baud']} bd（中心 1800 Hz），1 帧",
        "generation": "mbdsdr_ai.acars_protocol.acars_encode",
    }


def build_navtex() -> dict:
    from mbdsdr_ai import navtex_decoder as nv

    fs = 12000
    audio = nv.navtex_encode_audio(NAVTEX_TRUTH["b1"], NAVTEX_TRUTH["b2"],
                                   NAVTEX_TRUTH["serial"], NAVTEX_TRUTH["text"],
                                   float(fs))
    path = os.path.join(FIXTURE_DIR, "navtex.wav")
    _write_wav(path, audio, fs)
    return {
        "file": "navtex.wav", "domain": "audio", "fs_hz": fs,
        "samples": int(np.size(audio)), "truth": dict(NAVTEX_TRUTH),
        "condition": "SITOR-B / CCIR 476，100 bd，中心 1000 Hz，单条 ZCZC 报文",
        "generation": "mbdsdr_ai.navtex_decoder.navtex_encode_audio",
    }


def build_apt() -> dict:
    from mbdsdr_ai.noaa_apt_lite import (synthesize_apt_audio,
                                         synthesize_test_images)

    fs = 24000
    img_a, img_b = synthesize_test_images(n_lines=APT_LINES)
    audio = synthesize_apt_audio(img_a, img_b, fs=fs,
                                 rng=np.random.default_rng(123))
    path = os.path.join(FIXTURE_DIR, "apt.wav")
    _write_wav(path, audio, fs)
    return {
        "file": "apt.wav", "domain": "audio", "fs_hz": fs,
        "samples": int(np.size(audio)),
        "truth": {"n_lines": APT_LINES,
                  "n_lines_decodable": APT_LINES_DECODABLE,
                  "b_band_std_min": 30.0},
        "condition": f"APT AM 2400 Hz 副载波，{APT_LINES} 行（A=水平灰阶/B=竖条带）",
        "generation": "mbdsdr_ai.noaa_apt_lite.synthesize_apt_audio",
    }


def build_ft4() -> dict:
    from mbdsdr_ai.ft4_codec import build_frame, encode_ft4_tones
    from mbdsdr_ai.ft4_modem import FS_HZ, Ft4Modulator
    from mbdsdr_ai.ft8_encode import pack77_standard

    call1, call2, grid = FT4_TRUTH["text"].split()
    message77 = pack77_standard(call1, call2, grid)
    frame = np.asarray(build_frame(encode_ft4_tones(message77)), dtype=np.int8)
    iq = Ft4Modulator().modulate(frame)
    path = os.path.join(FIXTURE_DIR, "ft4.cf32")
    _write_cf32(path, iq)
    return {
        "file": "ft4.cf32", "domain": "iq", "fs_hz": FS_HZ,
        "samples": int(iq.size), "truth": dict(FT4_TRUTH),
        "condition": "4-FSK GFSK（BT=1.0，12 kS/s，105 符号/5.04 s）",
        "generation": "mbdsdr_ai.ft4_modem.Ft4Modulator.modulate",
    }


def build_wspr() -> dict:
    from mbdsdr_ai.wspr_modem import (REFERENCE_SAMPLE_RATE_HZ,
                                      encode_audio)

    fs = float(REFERENCE_SAMPLE_RATE_HZ)      # 375 Hz，256 样本/符号
    audio = encode_audio(WSPR_TRUTH["callsign"], WSPR_TRUTH["grid"],
                         WSPR_TRUTH["power_dbm"], sample_rate_hz=fs,
                         center_frequency_hz=0.0, complex_baseband=True)
    path = os.path.join(FIXTURE_DIR, "wspr.cf32")
    _write_cf32(path, audio)
    return {
        "file": "wspr.cf32", "domain": "iq", "fs_hz": fs,
        "samples": int(np.size(audio)), "truth": dict(WSPR_TRUTH),
        "condition": "WSPR Type 1，4-FSK 162 符号 / 110.592 s，375 Hz 复基带",
        "generation": "mbdsdr_ai.wspr_modem.encode_audio",
    }


def build_lrpt() -> dict:
    from mbdsdr_ai.lrpt_modem import FS_HZ, LrptModulator

    mod = LrptModulator()
    frame_bits = np.asarray(mod.build_frame_bits(), dtype=np.uint8)
    iq = mod.modulate(frame_bits)
    payload = np.packbits(frame_bits[64:]).tobytes()
    path = os.path.join(FIXTURE_DIR, "lrpt.cf32")
    _write_cf32(path, iq)
    return {
        "file": "lrpt.cf32", "domain": "iq", "fs_hz": FS_HZ,
        "samples": int(iq.size),
        "truth": {"payload_sha256": hashlib.sha256(payload).hexdigest(),
                  "payload_bytes": len(payload),
                  "frame_bits": int(frame_bits.size)},
        "condition": "LRPT QPSK/Costas，72 kSym/s，64 bit 同步 + 2048 bit 载荷，sps=4",
        "generation": "mbdsdr_ai.lrpt_modem.LrptModulator.modulate",
    }


BUILDERS = {
    "pocsag": build_pocsag,
    "acars": build_acars,
    "navtex": build_navtex,
    "apt": build_apt,
    "ft4": build_ft4,
    "wspr": build_wspr,
    "lrpt": build_lrpt,
}

# 复用既有合成 fixture 的模式（不重复造波形，只在 manifest 里登记关系）。
REUSED = {
    "ais": {
        "fixture_dir": "tests/fixtures/decode_pipeline",
        "file": "ais_two_frames.cf32",
        "note": "仓内既有合成 fixture；manifest 见 decode_pipeline/manifest.json",
    },
    "adsb": {
        "fixture_dir": "tests/fixtures/decode_pipeline",
        "file": "adsb_three_frames.cf32",
        "note": "仓内既有合成 fixture；manifest 见 decode_pipeline/manifest.json",
    },
    "aprs": {
        "fixture_dir": "tests/fixtures/decode_pipeline",
        "file": "aprs_two_frames_48000.wav",
        "note": "仓内既有合成 fixture；manifest 见 decode_pipeline/manifest.json",
    },
}


def main() -> int:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    entries = {}
    for mode, builder in BUILDERS.items():
        meta = builder()
        meta["sha256"] = _sha256(os.path.join(FIXTURE_DIR, meta["file"]))
        entries[mode] = meta
        print(f"[fixture] {mode}: {meta['file']} "
              f"{os.path.getsize(os.path.join(FIXTURE_DIR, meta['file']))} B")

    manifest = {
        "schema": "mbdsdr.sensitivity.fixtures/1",
        "directory": "tests/fixtures/sensitivity",
        "provenance": "self-generated",
        "honesty": "synthetic / NOT OTA",
        "generated_by": "scripts/make_sensitivity_fixtures.py",
        "regenerate": "python scripts/make_sensitivity_fixtures.py",
        "bootstrap": ("本目录已随仓入库，clone 后即可读。当前 "
                      "scripts/decode_sensitivity.py 的六个模式只用 "
                      "tests/fixtures/decode_pipeline 与仓内编码器；本目录的 "
                      "navtex/apt/wspr/lrpt 波形是给灵敏度扩模式预留的合成用例，"
                      "尚未被脚本消费。需要从零重建时，在仓库根依次执行 "
                      "`python scripts/make_sensitivity_fixtures.py`、"
                      "`python scripts/decode_sensitivity.py`。"),
        "date": TODAY,
        "note": ("全部为仓内编码器合成的 clean 信号；没有任何一条是空中实录。"
                 "SNR 口径由 scripts/decode_sensitivity.py 的 MODE_BASIS 自述，"
                 "跨模式不可横比。"),
        "reused": REUSED,
        "modes": entries,
    }
    manifest_path = os.path.join(FIXTURE_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")

    files = sorted(entries) + ["manifest.json"]
    provenance = {
        "schema": "mbdsdr.provenance/1",
        "directory": "tests/fixtures/sensitivity",
        "provenance": "self-generated",
        "date": TODAY,
        "note": ("合成灵敏度基线 fixture。由 scripts/make_sensitivity_fixtures.py "
                 "生成，NOT OTA；逐文件 SHA-256 见 files。"),
        "files": {},
    }
    for mode, meta in entries.items():
        provenance["files"][meta["file"]] = {
            "provenance": "self-generated",
            "source_name": meta["generation"],
            "sha256": meta["sha256"],
            "derivation": (f"python scripts/make_sensitivity_fixtures.py；"
                           f"{meta['condition']}；synthetic / NOT OTA"),
            "date": TODAY,
        }
    provenance["files"]["manifest.json"] = {
        "provenance": "self-generated",
        "source_name": "scripts/make_sensitivity_fixtures.py",
        "sha256": _sha256(manifest_path),
        "derivation": "脚本写出的 fixture 清单（合成真值与 SHA-256）",
        "date": TODAY,
    }
    prov_path = os.path.join(FIXTURE_DIR, "provenance.json")
    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")

    print(f"[manifest] {manifest_path}")
    print(f"[provenance] {prov_path}")
    print(f"[files] {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
