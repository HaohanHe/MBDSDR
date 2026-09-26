"""YSF/C4FM 数据层解调器合成信号往返测试。

覆盖：
  1. FEC 编解码往返（Viterbi K=5 / Golay(24,12,8) / CRC-CCITT16 / FICH / DCH）
  2. 4FSK 基带解调符号正确率（加噪 >95%）
  3. Data FR 帧端到端基带往返（SNR=15dB，解出 src/dst 呼号一致）
  4. 复数 IQ 端到端往返
  5. 同步字检测（随机前缀+帧+随机后缀中定位）
  6. 无信号零假值（纯噪声/零信号/噪声基带均不解出帧）

参考: repos/MMDVMHost/YSF*.cpp, Golay24128.cpp, CRC.cpp；mbdsdr_ai/dmr_demod.py。
本测试仅验证数据层（成帧/FICH/呼号），不涉及 AMBE/IMBE 语音声码器。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import ysf_demod as ysf  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. FEC 往返
# --------------------------------------------------------------------------- #
class TestFECRoundtrip:
    def test_viterbi_roundtrip(self):
        rng = np.random.default_rng(1)
        # 96 info bits + 4 tail zeros
        info = [int(x) for x in rng.integers(0, 2, 96)]
        enc = ysf.YSFViterbi.encode(info + [0, 0, 0, 0], 100)
        v = ysf.YSFViterbi()
        v.start()
        for i in range(0, 200, 2):
            v.decode(enc[i], enc[i + 1])
        dec = v.chainback(96)
        assert dec == info

    def test_viterbi_corrects_a_few_errors(self):
        rng = np.random.default_rng(2)
        info = [int(x) for x in rng.integers(0, 2, 96)]
        enc = ysf.YSFViterbi.encode(info + [0, 0, 0, 0], 100)
        # 翻转几个码元
        enc2 = enc[:]
        for pos in (10, 50, 100, 150):
            enc2[pos] ^= 1
        v = ysf.YSFViterbi()
        v.start()
        for i in range(0, 200, 2):
            v.decode(enc2[i], enc2[i + 1])
        dec = v.chainback(96)
        assert dec == info

    def test_golay24128_roundtrip(self):
        for info in (0, 1, 0x123, 0x5A5, 0xAAA, 0xFFF, 1234):
            code = ysf.golay24128_encode(info)
            dec, ok = ysf.golay24128_decode(code)
            assert ok, f"info={info:#06x} 未通过校验"
            assert dec == info

    def test_golay24128_corrects_errors(self):
        info = 0x5A5
        code = ysf.golay24128_encode(info)
        # 翻 3 位（Golay(24,12,8) 纠错能力 t=3）
        code ^= (1 << 5) | (1 << 10) | (1 << 20)
        dec, ok = ysf.golay24128_decode(code)
        assert ok
        assert dec == info

    def test_ccitt16_roundtrip(self):
        data = bytearray([0x12, 0x34, 0x56, 0x78, 0x00, 0x00])
        ysf.ccitt16_add(data, 6)
        assert ysf.ccitt16_check(data, 6) is True
        # 破坏一个字节
        data[0] ^= 0xFF
        assert ysf.ccitt16_check(data, 6) is False

    def test_fich_roundtrip(self):
        f = ysf.build_fich6(fi=ysf.YSF_FI_HEADER, dt=ysf.YSF_DT_DATA_FR_MODE,
                            cm=ysf.YSF_CM_GROUP1, dev=True)
        f_expected = bytearray(f)
        ysf.ccitt16_add(f_expected, 6)  # 编码端会在 bytes4,5 写入 CRC
        f25 = ysf.fich_encode(f)
        f6, ok = ysf.fich_decode(f25)
        assert ok, "FICH 解码 CRC 未通过"
        assert bytes(f_expected) == bytes(f6), \
            f"{bytes(f_expected).hex()} != {bytes(f6).hex()}"

    def test_dch_roundtrip(self):
        payload = ysf.callsign_to_bytes("BG7ABC") + ysf.callsign_to_bytes("N0CALL")
        dch = ysf.dch_encode(payload)
        p2, ok = ysf.dch_decode(dch)
        assert ok, "DCH CRC 未通过"
        assert p2 == payload

    def test_callsign_padding(self):
        b = ysf.callsign_to_bytes("BG7ABC")
        assert len(b) == 10
        assert b == b"BG7ABC    "
        assert ysf.bytes_to_callsign(b) == "BG7ABC"


# --------------------------------------------------------------------------- #
# 2. 4FSK 基带解调符号正确率
# --------------------------------------------------------------------------- #
class TestFourFSKDemod:
    def test_4fsk_demod_baseband(self):
        rng = np.random.default_rng(42)
        dibits = rng.integers(0, 4, 200).astype(np.uint8)
        levels = ysf.dibits_to_levels(dibits)
        sps = 10
        taps = ysf.rrc_impulse_response(sps, ysf.RRC_ALPHA_DMR)
        ups = np.zeros(len(levels) * sps)
        ups[::sps] = levels
        shaped = np.convolve(ups, taps, mode="same")
        noise = rng.standard_normal(len(shaped)) * 0.3
        bb = shaped + noise

        demod = ysf.YSFDemodulator(48000)
        out = demod.demod_baseband(bb)
        n = min(len(out), len(dibits))
        acc = float(np.sum(out[:n] == dibits[:n])) / n
        assert acc > 0.95, f"符号正确率 {acc:.3f} 低于 0.95"


# --------------------------------------------------------------------------- #
# 辅助：加噪
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


# --------------------------------------------------------------------------- #
# 3. Data FR 帧基带往返
# --------------------------------------------------------------------------- #
class TestDataFRRoundtrip:
    def test_data_fr_baseband_roundtrip(self):
        frame = ysf.ysf_encode_data_frame("BG7ABC", "N0CALL")
        bb = ysf.ysf_encode_baseband(frame, 48000, 10)
        rng = np.random.default_rng(123)
        bb_n = _add_noise(bb, 15.0, rng)

        demod = ysf.YSFDemodulator(48000)
        dibits = demod.demod_baseband(bb_n)
        framer = ysf.YSFFramer()
        frames = framer.decode_stream(list(dibits))
        assert len(frames) >= 1
        f = frames[0]
        assert f.ok
        assert f.src_callsign == "BG7ABC", f"src={f.src_callsign!r}"
        assert f.dst_callsign == "N0CALL", f"dst={f.dst_callsign!r}"
        assert f.dt == ysf.YSF_DT_DATA_FR_MODE
        assert f.fi == ysf.YSF_FI_HEADER


# --------------------------------------------------------------------------- #
# 4. 复数 IQ 端到端
# --------------------------------------------------------------------------- #
class TestIQRoundtrip:
    def test_ysf_iq_roundtrip(self):
        frame = ysf.ysf_encode_data_frame("BG7ABC", "N0CALL")
        iq = ysf.ysf_encode_iq(frame, 48000, 10)
        rng = np.random.default_rng(99)
        iq_n = _add_noise(iq, 15.0, rng)

        results = ysf.ysf_decode_iq(iq_n, 48000)
        assert len(results) >= 1
        r = results[0]
        assert r["ok"] is True
        assert r["src_callsign"] == "BG7ABC"
        assert r["dst_callsign"] == "N0CALL"
        assert r["dt"] == ysf.YSF_DT_DATA_FR_MODE


# --------------------------------------------------------------------------- #
# 5. 同步字检测
# --------------------------------------------------------------------------- #
class TestSyncDetection:
    def test_sync_detection(self):
        frame = ysf.ysf_encode_data_frame("BG7ABC", "N0CALL")
        dibits = ysf._ysf_frame_to_dibits(frame)
        rng = np.random.default_rng(7)
        prefix = rng.integers(0, 4, 60).astype(np.uint8)
        suffix = rng.integers(0, 4, 60).astype(np.uint8)
        stream = np.concatenate([prefix, dibits, suffix])
        framer = ysf.YSFFramer()
        hits = framer.find_sync(list(stream))
        # 同步字在帧开头（dibit 0），加 prefix 后位置应为 60
        assert any(abs(p - 60) <= 2 for p in hits), f"未在预期位置找到同步字，hits={hits}"

    def test_no_fake_decode_without_signal(self):
        rng = np.random.default_rng(2024)
        # 纯噪声 IQ
        noise_iq = (rng.standard_normal(5000)
                    + 1j * rng.standard_normal(5000)) * 0.3
        res = ysf.ysf_decode_iq(noise_iq, 48000)
        assert res == [], f"纯噪声不应解出帧，得到 {res}"

        # 零信号
        res2 = ysf.ysf_decode_iq(np.zeros(5000, dtype=complex), 48000)
        assert res2 == [], "零信号不应解出帧"

        # 纯噪声基带
        bb_noise = rng.standard_normal(5000) * 0.3
        demod = ysf.YSFDemodulator(48000)
        dibits = demod.demod_baseband(bb_noise)
        framer = ysf.YSFFramer()
        frames = framer.decode_stream(list(dibits))
        ok_frames = [f for f in frames if f.ok]
        assert ok_frames == [], f"噪声基带不应解出有效帧: {ok_frames}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
