"""DMR 解码器合成信号往返测试。

覆盖：
  1. FEC 编解码往返（Golay/QR/RS/BPTC/Hamming）+ 纠错能力
  2. 4FSK 基带解调符号正确率
  3. Voice LC Header 帧端到端（基带路径）：解出 src/dst/slot/cc/flco
  4. 复数 IQ 端到端
  5. 同步字检测（噪声+随机前缀中定位）
  6. 无信号时不假装有呼号/ID

参考: repos/MMDVMHost/*.cpp（协议与 FEC）, op25 fsk4_demod_ff（解调管线）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import dmr_demod as dmr  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. FEC 往返
# --------------------------------------------------------------------------- #
class TestFECRoundtrip:
    def test_golay2087_roundtrip(self):
        for info in (0, 1, 0x37, 0x5A, 0xAA, 0xFF, 123):
            code = dmr.golay2087_encode(info)
            dec, _ = dmr.golay2087_decode(code)
            assert dec == info

    def test_golay2087_corrects_one_error(self):
        info = 0x5A
        code = dmr.golay2087_encode(info)
        code ^= 1 << 5  # 翻一位
        dec, corrected = dmr.golay2087_decode(code)
        assert dec == info
        assert corrected

    def test_qr1676_roundtrip(self):
        for info in (0, 1, 0x2A, 0x3F, 0x7F, 100):
            code = dmr.qr1676_encode(info)
            dec, _ = dmr.qr1676_decode(code)
            assert dec == info

    def test_qr1676_corrects_one_error(self):
        info = 0x47
        code = dmr.qr1676_encode(info)
        code ^= 1 << 4
        dec, corrected = dmr.qr1676_decode(code)
        assert dec == info
        assert corrected

    def test_rs129_roundtrip(self):
        data9 = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0, 0x11]
        p2, p1, p0 = dmr.rs129_encode(data9)
        data12 = list(data9) + [p2, p1, p0]
        assert dmr.rs129_check(data12) is True

    def test_rs129_rejects_corruption(self):
        data9 = [0, 1, 2, 3, 4, 5, 6, 7, 8]
        p2, p1, p0 = dmr.rs129_encode(data9)
        data12 = list(data9) + [p2, p1, p0]
        data12[0] ^= 0xFF  # 破坏一个字节
        assert dmr.rs129_check(data12) is False

    def test_hamming15113_roundtrip(self):
        rng = np.random.default_rng(0)
        for _ in range(20):
            d = [int(x) for x in rng.integers(0, 2, 11)]
            row = d + [0, 0, 0, 0]
            dmr.hamming15113_encode(row)
            # 无错
            assert dmr.hamming15113_decode(row) is False
            assert row[:11] == d
            # 翻一位
            row2 = row[:]
            row2[7] ^= 1
            assert dmr.hamming15113_decode(row2) is True
            assert row2[:11] == d

    def test_hamming1393_roundtrip(self):
        rng = np.random.default_rng(1)
        for _ in range(20):
            d = [int(x) for x in rng.integers(0, 2, 9)]
            col = d + [0, 0, 0, 0]
            dmr.hamming1393_encode(col)
            assert dmr.hamming1393_decode(col) is False
            assert col[:9] == d
            col2 = col[:]
            col2[5] ^= 1
            assert dmr.hamming1393_decode(col2) is True
            assert col2[:9] == d

    def test_bptc19696_roundtrip(self):
        rng = np.random.default_rng(2)
        payload = [int(x) for x in rng.integers(0, 2, 96)]
        raw = dmr.bptc19696_encode(payload)
        dec, _ = dmr.bptc19696_decode(raw)
        assert dec == payload

    def test_bptc19696_corrects_a_few_errors(self):
        rng = np.random.default_rng(3)
        payload = [int(x) for x in rng.integers(0, 2, 96)]
        raw = dmr.bptc19696_encode(payload)
        raw2 = raw[:]
        for pos in (10, 60, 120):
            raw2[pos] ^= 1
        dec, _ = dmr.bptc19696_decode(raw2)
        assert dec == payload


# --------------------------------------------------------------------------- #
# 2. 4FSK 基带解调
# --------------------------------------------------------------------------- #
class TestFourFSKDemod:
    def test_4fsk_demod_baseband(self):
        rng = np.random.default_rng(42)
        # 合成已知 dibit 序列
        dibits = rng.integers(0, 4, 200).astype(np.uint8)
        levels = dmr.dibits_to_levels(dibits)
        sps = 10
        taps = dmr.rrc_impulse_response(sps, dmr.RRC_ALPHA_DMR)
        ups = np.zeros(len(levels) * sps)
        ups[::sps] = levels
        shaped = np.convolve(ups, taps, mode="same")
        # 加高斯噪声
        noise = rng.standard_normal(len(shaped)) * 0.3
        bb = shaped + noise

        demod = dmr.DMRDemodulator(48000)
        out = demod.demod_baseband(bb)
        # 对齐长度（取前 len(dibits) 个）
        n = min(len(out), len(dibits))
        correct = np.sum(out[:n] == dibits[:n])
        acc = correct / n
        assert acc > 0.95, f"符号正确率 {acc:.3f} 低于 0.95"


# --------------------------------------------------------------------------- #
# 3. Voice LC Header 基带往返
# --------------------------------------------------------------------------- #
def _add_noise(signal: np.ndarray, snr_db: float, rng) -> np.ndarray:
    sig_pow = np.mean(np.abs(signal) ** 2)
    noise_std = np.sqrt(sig_pow / (10 ** (snr_db / 10)))
    if np.iscomplexobj(signal):
        noise = (rng.standard_normal(len(signal))
                 + 1j * rng.standard_normal(len(signal))) * noise_std / np.sqrt(2)
    else:
        noise = rng.standard_normal(len(signal)) * noise_std
    return signal + noise


class TestVoiceLCHeaderRoundtrip:
    def test_dmr_voice_lc_header_roundtrip_baseband(self):
        frame = dmr.dmr_encode_voice_lc_header(
            src_id=1234567, dst_id=9, slot=0, color_code=1, flco=0)
        bb = dmr.dmr_encode_baseband(frame, 48000, 10)
        rng = np.random.default_rng(123)
        bb_n = _add_noise(bb, 15.0, rng)

        demod = dmr.DMRDemodulator(48000)
        dibits = demod.demod_baseband(bb_n)
        framer = dmr.DMRFramer()
        frames = framer.decode_stream(list(dibits))
        assert len(frames) >= 1
        f = frames[0]
        assert f.src_id == 1234567, f"src_id={f.src_id}"
        assert f.dst_id == 9, f"dst_id={f.dst_id}"
        assert f.slot == 0
        assert f.color_code == 1
        assert f.flco == 0

    def test_dmr_iq_roundtrip(self):
        frame = dmr.dmr_encode_voice_lc_header(
            src_id=1234567, dst_id=9, slot=0, color_code=1, flco=0)
        iq = dmr.dmr_encode_iq(frame, 48000, 10)
        rng = np.random.default_rng(99)
        iq_n = _add_noise(iq, 15.0, rng)

        results = dmr.dmr_decode_iq(iq_n, 48000)
        assert len(results) >= 1
        r = results[0]
        assert r["src_id"] == 1234567
        assert r["dst_id"] == 9
        assert r["slot"] == 0
        assert r["color_code"] == 1
        assert r["flco"] == 0
        assert r["sync_type"] == "BS_DATA"


# --------------------------------------------------------------------------- #
# 5. 同步检测
# --------------------------------------------------------------------------- #
class TestSyncDetection:
    def test_sync_detection(self):
        frame = dmr.dmr_encode_voice_lc_header(
            src_id=1234567, dst_id=9, slot=0, color_code=1, flco=0)
        dibits = dmr._frame_to_dibits(frame)
        rng = np.random.default_rng(7)
        prefix = rng.integers(0, 4, 60).astype(np.uint8)
        stream = np.concatenate([prefix, dibits, prefix])
        framer = dmr.DMRFramer()
        hits = framer.find_sync(list(stream))
        # 应在 prefix 长度处命中（60 + 同步字内部偏移 54... 即 dibit 60+54=114）
        positions = [h[0] for h in hits]
        assert any(abs(p - (60 + 54)) <= 2 for p in positions), \
            f"未在预期位置找到同步字，hits={hits}"

    def test_no_fake_decode_without_signal(self):
        rng = np.random.default_rng(2024)
        # 纯噪声 IQ
        noise_iq = (rng.standard_normal(5000)
                    + 1j * rng.standard_normal(5000)) * 0.3
        res = dmr.dmr_decode_iq(noise_iq, 48000)
        assert res == [], f"纯噪声不应解出帧，得到 {res}"

        # 零信号
        res2 = dmr.dmr_decode_iq(np.zeros(5000, dtype=complex), 48000)
        assert res2 == [], f"零信号不应解出帧"

        # 纯噪声基带
        bb_noise = rng.standard_normal(5000) * 0.3
        demod = dmr.DMRDemodulator(48000)
        dibits = demod.demod_baseband(bb_noise)
        framer = dmr.DMRFramer()
        frames = framer.decode_stream(list(dibits))
        ok_frames = [f for f in frames if f.ok]
        assert ok_frames == [], f"噪声基带不应解出有效帧: {ok_frames}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
