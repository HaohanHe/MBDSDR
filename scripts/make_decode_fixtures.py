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
  * 采样率故意高于解码目标（AIS 240k→48k、ADS-B 8M→4M），让 downconvert
    级真做多相抽取，而不是恒等直通。

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
MANIFEST_FILE = "manifest.json"

AIS_FS = 240000.0
AIS_TARGET_FS = 48000.0
ADSB_FS = 8e6
ADSB_TARGET_FS = 4e6

AIS_GAP_BITS = 20               # 帧间静默（HDLC 空闲位）
ADSB_GAP_SAMPLES = 2000         # 帧间静默（8M 下 250µs）

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


def build_all():
    ais_iq, ais_meta = _build_ais()
    adsb_iq, adsb_meta = _build_adsb()
    manifest = {
        "generated_by": "scripts/make_decode_fixtures.py",
        "license": "MIT",
        "honesty": ("synthetic remodulation of known-truth frames with in-repo "
                    "encoders; NOT OTA recordings. Proves stage-by-stage "
                    "byte-exactness, not over-the-air reception performance."),
        "ais": ais_meta,
        "adsb": adsb_meta,
    }
    return {AIS_FILE: _cf32_bytes(ais_iq),
            ADSB_FILE: _cf32_bytes(adsb_iq)}, manifest


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
        print(f"OK: {len(blobs)} 个 cf32 + manifest 与生成器一致")
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
