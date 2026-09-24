"""
fldigi 多模式数字解码往返验证测试
===================================

验证从 fldigi 真实 .cxx 源码移植的编解码器：
  1. Varicode 全可打印字符编码→解码往返
  2. PSK31 文本→Varicode→DBPSK 调制→差分解调→Varicode 解码→文本
  3. ITA-2 字母/数字模式切换正确
  4. RTTY 文本→ITA-2→2FSK→正交检测→ITA-2 解码→文本
  5. MFSK 已知符号序列→调制→非相干 FFT 检测→符号还原
  6. FeldHell 框架可运行（振幅键控调制→包络检波→字符匹配）

运行: python3 -m pytest tests/fldigi_roundtrip.py -v
  或: python3 tests/fldigi_roundtrip.py
"""

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mbdsdr_ai.fldigi_modes import (
    Varicode, ITA2, ITA2_LETTERS, ITA2_FIGURES,
    PSK31Modem, RTTYModem, MFSKModem, FeldHellDecoder,
    FELD_FONT, OliviaMFSK, ThorMode,
)


# ---------------------------------------------------------------------------
# 1. Varicode 全可打印字符往返
# ---------------------------------------------------------------------------
def test_varicode_all_printable():
    """所有 ASCII 可打印字符 (32..126) 编码→解码应还原。
    来源: fldigi src/psk/pskvaricode.cxx:28-288 varicodetab1[]
    """
    for code in range(32, 127):
        ch = chr(code)
        bits = Varicode.encode_char(code)
        # 编解码：加上 "00" 间隔后走完整 decode
        full_bits = bits + Varicode.SEPARATOR
        out = Varicode.decode_bits(full_bits)
        assert out == bytes([code]), \
            f"Varicode roundtrip failed for {ch!r} (0x{code:02x}): bits={bits} dec={out}"
    print("[PASS] Varicode all printable chars roundtrip (32..126)")


def test_varicode_text_roundtrip():
    """多字符文本往返。psk.cxx:2467-2489 tx_char"""
    for text in ["hello", "CQ CQ BI4MIB", "123 !@#", "The quick brown fox"]:
        bits = Varicode.encode_text(text)
        out = Varicode.decode_bits(bits)
        assert out.decode("ascii") == text, \
            f"Varicode text roundtrip failed: {text!r} -> {out!r}"
    print("[PASS] Varicode text roundtrip")


# ---------------------------------------------------------------------------
# 2. PSK31 DBPSK 往返
# ---------------------------------------------------------------------------
def test_psk31_roundtrip():
    """PSK31: 文本→Varicode→DBPSK→差分解调→Varicode→文本。
    来源: psk.cxx:382-387 (31.25 baud), psk.cxx:2252 (差分编码)
    """
    modem = PSK31Modem(sample_rate=8000, carrier_hz=1500.0)
    assert modem.BAUD == 31.25, f"PSK31 baud should be 31.25, got {modem.BAUD}"
    for text in ["Hello", "hello world", "BI4MIB", "CQ CQ CQ", "73!"]:
        wav = modem.modulate(text)
        out = modem.demodulate(wav)
        assert out == text, f"PSK31 roundtrip failed: {text!r} -> {out!r}"
    print("[PASS] PSK31 DBPSK roundtrip")


# ---------------------------------------------------------------------------
# 3. ITA-2 字母/数字切换
# ---------------------------------------------------------------------------
def test_ita2_letter_figure_shift():
    """ITA-2 LTRS/FIGS 切换正确。rtty.cxx:62-79, 1408-1430"""
    # 纯字母
    syms = ITA2.encode_text("HELLO")
    txt = ITA2.decode_symbols(syms)
    assert txt == "HELLO", f"ITA2 letters: {txt!r}"

    # 字母+数字混合（应自动插入 FIGS/LTRS）
    syms = ITA2.encode_text("HELLO 123")
    txt = ITA2.decode_symbols(syms)
    assert txt == "HELLO 123", f"ITA2 mixed: {txt!r}"

    # 数字表项
    assert ITA2_FIGURES[1] == "3"   # rtty.cxx:73
    assert ITA2_LETTERS[1] == "E"   # rtty.cxx:63
    print("[PASS] ITA-2 letter/figure shift")


# ---------------------------------------------------------------------------
# 4. RTTY 往返
# ---------------------------------------------------------------------------
def test_rtty_roundtrip():
    """RTTY: 文本→ITA-2→2FSK(170Hz/45.45baud)→正交检测→ITA-2→文本。
    来源: rtty.cxx:83 SHIFT[3]=170Hz, rtty.cxx:85 BAUD[1]=45.45
    """
    modem = RTTYModem()
    assert abs(modem.shift_hz - 170.0) < 0.1
    assert abs(modem.baud - 45.45) < 0.01
    for text in ["HELLO", "HELLO BI4MIB", "CQ CQ", "123 TEST", "S9 599", "BI4MIB"]:
        wav = modem.modulate(text)
        out = modem.demodulate(wav)
        assert out.strip() == text.strip(), \
            f"RTTY roundtrip failed: {text!r} -> {out!r}"
    print("[PASS] RTTY 2FSK roundtrip")


# ---------------------------------------------------------------------------
# 5. MFSK 符号往返
# ---------------------------------------------------------------------------
def test_mfsk_roundtrip():
    """MFSK: 已知符号→音调波形→非相干 FFT 检测→符号还原。
    来源: mfsk.cxx:291 tonespacing=sr/symlen, mfsk.cxx:209-216 MFSK16
    """
    for mode_name, (symlen, basetone, ntones, sr) in MFSKModem.MODES.items():
        m = MFSKModem(mode_name)
        assert abs(m.tonespace - sr / symlen) < 0.01
        syms = list(range(ntones))
        wav = m.modulate_sequence(syms)
        out = m.demodulate(wav)
        assert out == syms, f"MFSK {mode_name} roundtrip: {out} != {syms}"
    print("[PASS] MFSK8/16/32 non-coherent FFT roundtrip")


# ---------------------------------------------------------------------------
# 6. FeldHell 框架
# ---------------------------------------------------------------------------
def test_feldhell_framework():
    """FeldHell: 文本→7x14 字体 AM→包络检波→字符匹配（best-effort）。
    来源: feld.cxx:154 columnrate=17.5, feld.h:42 COLUMN_LEN=14
    """
    d = FeldHellDecoder()
    assert d.COLUMN_RATE == 17.5
    assert d.COLUMN_LEN == 14
    wav = d.modulate_text("HELLO")
    env = d.demodulate_envelope(wav)
    n_cols = len(env)
    # 5 chars × 14 cols = 70 columns
    assert abs(n_cols - 70) <= 1, f"expected ~70 cols, got {n_cols}"
    # 至少能重建出部分字符
    chars = []
    for cs in range(0, n_cols - d.COLUMN_LEN + 1, d.COLUMN_LEN):
        block = [env[cs + i][0] for i in range(d.COLUMN_LEN)]
        chars.append(d.match_char(block))
    out = "".join(chars)
    assert "H" in out, f"FeldHell should recover at least 'H', got {out!r}"
    print(f"[PASS] FeldHell framework (reconstructed {out!r} from 'HELLO')")


# ---------------------------------------------------------------------------
# 7. Olivia / Thor 参数表
# ---------------------------------------------------------------------------
def test_olivia_thor_params():
    """Olivia/Thor 常量表正确。"""
    assert OliviaMFSK.TONE_SPACING_HZ == 125.0
    assert ThorMode.K == 15
    print("[PASS] Olivia/Thor parameter tables")


if __name__ == "__main__":
    test_varicode_all_printable()
    test_varicode_text_roundtrip()
    test_psk31_roundtrip()
    test_ita2_letter_figure_shift()
    test_rtty_roundtrip()
    test_mfsk_roundtrip()
    test_feldhell_framework()
    test_olivia_thor_params()
    print("\n=== All fldigi roundtrip tests passed ===")
