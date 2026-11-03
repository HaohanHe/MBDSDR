# SPDX-License-Identifier: MIT
"""tests/test_navtex_decoder.py — NAVTEX/SITOR-B（CCIR 476）编解码钉扎。

钉扎层级：
  * CCIR 476 码表封闭性（数学性质金样）：全表 35 码点恰为所有「7 bit
    含 4 个 mark」的组合（C(7,4)=35），且与公开锚点 bit 串逐一相等
    （G/H/L/N/NUL/α/β/RQ，来自维基 SITOR 词条）
  * 文本↔码流往返（自动 LTRS/FIGS shift、CR/LF/SP）
  * SITOR-B 分集：4+4 块结构、单副本恒比破坏纠错、双副本破坏诚实 BLK
  * 报文帧：build→parse 字段往返（B1/B2/B3B4/正文、shift 切换）
  * 100bd FSK 音频端到端（含亚符号定时偏移鲁棒性）
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import navtex_decoder as nv


# ---------------------------------------------------------------------------
# CCIR 476 码表（数学封闭性 + 公开锚点）
# ---------------------------------------------------------------------------
class TestCcir476Table:
    def test_constant_ratio_all_entries(self):
        codes = set(nv.CCIR476_LETTERS.values()) | {
            nv.CODE_LTRS, nv.CODE_FIGS, nv.CODE_CR, nv.CODE_LF,
            nv.CODE_SP, nv.CODE_BLK, nv.IDLE_ALPHA, nv.IDLE_BETA,
            nv.REPEAT_RQ,
        }
        assert len(codes) == 35                      # 26 字母 + 6 控制 + 3 SITOR
        for c in codes:
            assert nv.is_valid_code(c), f"码 {c:#04x} 破坏恒比"

    def test_table_is_exactly_all_4of7_codes(self):
        """封闭性金样：本表 35 码 == 全部 C(7,4)=35 个恒比组合，不多不少。"""
        all_valid = {c for c in range(128) if bin(c).count("1") == 4}
        assert len(all_valid) == 35
        mine = set(nv.CCIR476_LETTERS.values()) | {
            nv.CODE_LTRS, nv.CODE_FIGS, nv.CODE_CR, nv.CODE_LF,
            nv.CODE_SP, nv.CODE_BLK, nv.IDLE_ALPHA, nv.IDLE_BETA,
            nv.REPEAT_RQ,
        }
        assert mine == all_valid

    def test_public_anchor_bitstrings(self):
        # 维基 SITOR 词条 bit 串锚点（MSB first）
        assert nv.CCIR476_LETTERS["G"] == 0b0110101
        assert nv.CCIR476_LETTERS["H"] == 0b1101001
        assert nv.CCIR476_LETTERS["L"] == 0b1100101
        assert nv.CCIR476_LETTERS["N"] == 0b1011001
        assert nv.CODE_BLK == 0b1101010
        assert nv.IDLE_ALPHA == 0b0001111
        assert nv.IDLE_BETA == 0b0110011
        assert nv.REPEAT_RQ == 0b1100110

    def test_char_code_roundtrip_both_shifts(self):
        for ch, code in nv.CCIR476_LETTERS.items():
            assert nv.char_to_code(ch, figures=False) == code
            assert nv.code_to_char(code, figures=False) == ch
        for fig in "3!&#8().,90\"5':-?/":
            code = nv.char_to_code(fig, figures=True)
            assert code is not None
            assert nv.code_to_char(code, figures=True) == fig


# ---------------------------------------------------------------------------
# 文本 ↔ 码流（shift 状态机）
# ---------------------------------------------------------------------------
class TestTextCoding:
    def test_roundtrip_mixed(self):
        text = "GALE WARNING NE 35 KT"
        codes = nv.encode_text(text)
        assert nv.CODE_FIGS in codes and nv.CODE_LTRS in codes  # 数字触发 shift
        back, dropped = nv.decode_text(codes)
        assert back == text
        assert dropped == 0

    def test_roundtrip_letters_only(self):
        text = "NOW IS THE TIME FOR ALL GOOD MEN"
        codes = nv.encode_text(text)
        assert nv.CODE_FIGS not in codes
        back, _ = nv.decode_text(codes)
        assert back == text

    def test_shift_codes_emit_no_char(self):
        back, dropped = nv.decode_text([nv.CODE_LTRS, nv.CODE_FIGS])
        assert back == "" and dropped == 0


# ---------------------------------------------------------------------------
# SITOR-B 分集（4+4 块，滞后 4 字符）
# ---------------------------------------------------------------------------
class TestDiversity:
    def test_encode_structure(self):
        codes = [10, 20, 30, 40, 50]
        wire = nv.diversity_encode(codes)
        assert len(wire) == 16
        # 每个字符恰两遍、第二遍滞后 4 字符
        assert wire[:4] == codes[:4] and wire[4:8] == codes[:4]
        assert wire[8:12] == [50, nv.IDLE_ALPHA, nv.IDLE_ALPHA, nv.IDLE_ALPHA]
        assert wire[12:16] == wire[8:12]

    def test_clean_roundtrip(self):
        codes = nv.encode_text("ZCZC QA21 TEST")
        wire = nv.diversity_encode(codes)
        back, corrected, lost = nv.diversity_decode(wire)
        assert back[:len(codes)] == codes
        assert corrected == 0 and lost == 0

    def test_single_copy_damage_corrected(self):
        codes = nv.encode_text("AB12")
        wire = nv.diversity_encode(codes)
        wire[1] ^= 0x01                   # 破坏直接副本恒比（可能仍合法则换码）
        if nv.is_valid_code(wire[1]):
            wire[1] = 0x00                # 强制非法（0 个 mark）
        back, corrected, lost = nv.diversity_decode(wire)
        assert back[1] == codes[1]        # 重复副本顶上
        assert corrected == 1 and lost == 0

    def test_both_copies_damaged_honest(self):
        codes = nv.encode_text("AB12")
        wire = nv.diversity_encode(codes)
        wire[0] = 0x00
        wire[4] = 0x00                    # 双副本全毁
        back, corrected, lost = nv.diversity_decode(wire)
        assert back[0] == nv.CODE_BLK     # 诚实占位，不编造字符
        assert lost == 1


# ---------------------------------------------------------------------------
# 报文帧组装与解析
# ---------------------------------------------------------------------------
class TestMessageFrame:
    def test_build_parse_roundtrip(self):
        codes = nv.build_message("Q", "A", "21",
                                 "GALE WARNING NE 35 KT")
        msg = nv.parse_message_codes(
            codes[nv.PHASING_CHARS:])     # 去掉定相段解析
        assert msg is not None
        assert msg["b1"] == "Q"
        assert msg["b2"] == "A"
        assert msg["serial"] == "21"
        assert "GALE WARNING NE 35 KT" in msg["text"]

    def test_parse_missing_zczc_returns_none(self):
        assert nv.parse_message_codes(nv.encode_text("HELLO WORLD")) is None

    def test_phasing_prefix_alternates(self):
        codes = nv.build_message("A", "B", "00", "X")
        phasing = codes[:nv.PHASING_CHARS]
        assert all(c in (nv.IDLE_ALPHA, nv.REPEAT_RQ) for c in phasing)
        assert len(set(phasing)) == 2


# ---------------------------------------------------------------------------
# FSK 调制解调与端到端
# ---------------------------------------------------------------------------
class TestFskModem:
    def test_bits_roundtrip(self):
        fs = 12000.0
        bits = [int(b) for b in np.random.default_rng(5).integers(0, 2, 400)]
        audio = nv.fsk_modulate(bits, fs)
        out = nv.fsk_demodulate(audio, fs)
        # 定时恢复负责符号级对齐；帧起点对齐是上层（码流搜索）职责——
        # 允许整体移位 ≤2 符号，但重叠区不许有一位错误。
        best = 0.0
        for shift in range(-2, 3):
            a = out if shift >= 0 else out[-shift:]
            b = bits[shift:] if shift >= 0 else bits
            m = min(len(a), len(b))
            if m:
                score = sum(x == y for x, y in zip(a[:m], b[:m])) / m
                best = max(best, score)
        assert best == 1.0

    def test_subsymbol_timing_offset(self):
        """音频前补 7 个样本（<1 bit 时间）模拟定时偏移，定时扫描须恢复。"""
        fs = 12000.0
        codes = nv.encode_text("ZCZC")
        bits = nv.codes_to_bits(codes)
        audio = nv.fsk_modulate(bits, fs)
        shifted = np.concatenate([np.zeros(7), audio])
        out = nv.fsk_demodulate(shifted, fs)
        # 允许对齐搜索消化偏移：恢复出的码流含全部原码
        recovered = set()
        for align in range(7):
            recovered.update(nv.bits_to_codes(out, align))
        assert set(codes) <= recovered

    def test_audio_end_to_end(self):
        fs = 12000.0
        audio = nv.navtex_encode_audio("N", "A", "07",
                                       "GALE WARNING NE 35 KT", fs)
        out = nv.navtex_decode_audio(audio, fs)
        assert out["phasing_ok"] is True
        assert out["valid_code_ratio"] > 0.99
        assert out["num_messages"] == 1
        msg = out["messages"][0]
        assert msg["b1"] == "N"
        assert msg["b2"] == "A"
        assert msg["serial"] == "07"
        assert "GALE WARNING NE 35 KT" in msg["text"]

    def test_no_signal_honest_empty(self):
        fs = 12000.0
        silence = np.zeros(int(fs * 2.0))
        out = nv.navtex_decode_audio(silence, fs)
        assert out["num_messages"] == 0
        assert out["phasing_ok"] is False


# ---------------------------------------------------------------------------
# decode_digital_mode 路由（真入口，不吃假数据）
# ---------------------------------------------------------------------------
class TestDigitalModeRouting:
    def test_navtex_route_wav(self, tmp_path):
        import wave
        from mbdsdr_ai.decoders import decode_digital_mode
        fs = 12000
        audio = nv.navtex_encode_audio("C", "A", "12",
                                       "STORM WARNING S 45 KT", float(fs))
        pcm = (audio / max(1e-9, np.max(np.abs(audio))) * 32767
               ).astype(np.int16)
        wav_path = tmp_path / "navtex.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(fs)
            wf.writeframes(pcm.tobytes())
        r = decode_digital_mode(str(wav_path), "navtex",
                                output_dir=str(tmp_path / "out"))
        assert r.get("num_messages") == 1
        assert r["messages"][0]["b1"] == "C"
        assert r["messages"][0]["serial"] == "12"
        assert "STORM WARNING S 45 KT" in r["messages"][0]["text"]
        assert r["phasing_ok"] is True

    def test_navtex_route_rejects_cf32(self, tmp_path):
        from mbdsdr_ai.decoders import decode_digital_mode
        cf32 = tmp_path / "x.cf32"
        cf32.write_bytes(b"\x00" * 16)
        r = decode_digital_mode(str(cf32), "navtex",
                                output_dir=str(tmp_path / "out"))
        assert "error" in r and "wav" in r["error"]
