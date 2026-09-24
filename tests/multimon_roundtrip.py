"""multimon-ng 数字模式真实往返自测。

对照 multimon-ng 真实 C 源码（repos/multimon-ng）移植后，端到端验证：
  1. POCSAG：同步字 0x7CD215D8 + 地址码字 + 消息码字 -> 解出正确地址与 BCD 数字消息。
  2. POCSAG BCH(31,21,2)：注入 2bit 错误 -> 纠错成功，地址/消息不变。
  3. POCSAG BCH 校验矢量：SYNC/IDLE 码字伴随式为 0；1bit/2bit 可纠。
  4. AFSK1200：Bell-202 调制比特流 -> 正交相关解调 -> 位序列一致。
  5. DTMF：合成 8 个双音 -> Goertzel/正交能量法识别正确按键。
  6. ZVEI-1：合成 5 音调选呼序列 -> 解出 5 位 hex 选呼码。

运行:  python3 tests/multimon_roundtrip.py
退出码 0 全部通过；非 0 有失败。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import multimon_decoders as M  # noqa: E402


# ----------------------------------------------------------------------
#  辅助：把 nibble 列表打包成 POCSAG 消息码字
#  对照 pocsag.c:951-959 的 nibble 累积（正演）
# ----------------------------------------------------------------------
def _nibbles_to_msg_words(nibbles):
    words = []
    for k in range((len(nibbles) + 4) // 5):
        payload = 0
        for j in range(5):
            idx = k * 5 + j
            nb = nibbles[idx] if idx < len(nibbles) else 0
            payload |= nb << (16 - 4 * j)
        words.append(M.pocsag_encode_message_word(payload))
    return words


def _rxword_for_address(addr: int) -> int:
    """选批内位置使 (rxword>>1)&7 == addr&7（对照 pocsag.c:917）。"""
    low = addr & 7
    for r in range(1, 17):
        if (r >> 1) & 7 == low:
            return r
    raise AssertionError("no rxword")


# ======================================================================
#  1. POCSAG 完整往返
# ======================================================================
def test_pocsag_message_roundtrip() -> None:
    # BCD 转换表（pocsag.c:454）
    tbl = "084 2.6]195-3U7["
    text = "12345"
    nibbles = [tbl.index(ch) for ch in text]
    msg_words = _nibbles_to_msg_words(nibbles)

    addr, func = 12345, 0
    addr_cw = M.pocsag_encode_address_word(addr, func)
    rxword = _rxword_for_address(addr)

    batch = [M.POCSAG_SYNC] + [M.POCSAG_IDLE] * 16
    batch[rxword] = addr_cw
    # 消息字紧跟地址字之后
    for j, w in enumerate(msg_words):
        pos = rxword + 1 + j
        if pos <= 16:
            batch[pos] = w

    dec = M.POCSAGDecoder()
    msgs = dec.decode_batch(batch)
    assert msgs, "未解出任何消息"
    m = msgs[0]
    assert m.address == addr, f"地址错: {m.address} != {addr}"
    assert m.function == func, f"功能位错: {m.function} != {func}"
    assert m.numeric.strip() == text, f"数字消息错: {m.numeric!r} != {text!r}"
    print(f"  [ok] POCSAG 往返: addr={m.address} func={m.function} numeric={m.numeric!r}")


# ======================================================================
#  2. POCSAG BCH 2bit 纠错
# ======================================================================
def test_pocsag_bch_2bit_correction() -> None:
    bch = M._POCSAGBCH()
    tbl = "084 2.6]195-3U7["
    nibbles = [tbl.index(ch) for ch in "98765"]
    msg_words = _nibbles_to_msg_words(nibbles)

    addr, func = 7777, 3
    addr_cw = M.pocsag_encode_address_word(addr, func)
    rxword = _rxword_for_address(addr)

    # 对地址码字注入 2bit 错误
    bad_addr = addr_cw ^ (1 << 5) ^ (1 << 20)
    batch = [M.POCSAG_SYNC] + [M.POCSAG_IDLE] * 16
    batch[rxword] = bad_addr
    for j, w in enumerate(msg_words):
        if rxword + 1 + j <= 16:
            batch[rxword + 1 + j] = w

    dec = M.POCSAGDecoder()
    msgs = dec.decode_batch(batch)
    assert dec.corrected_2bit >= 1, f"应纠正 >=1 个 2bit 错误, got {dec.corrected_2bit}"
    assert msgs[0].address == addr, f"纠错后地址错: {msgs[0].address} != {addr}"
    assert msgs[0].numeric.strip() == "98765", repr(msgs[0].numeric)
    print(f"  [ok] POCSAG BCH 2bit 纠错: corrected_2={dec.corrected_2bit}, "
          f"addr={msgs[0].address} numeric={msgs[0].numeric!r}")


# ======================================================================
#  3. POCSAG BCH 校验矢量
# ======================================================================
def test_pocsag_bch_vectors() -> None:
    bch = M._POCSAGBCH()
    # SYNC/IDLE 是合法码字，伴随式应为 0（已在 multimon-ng 中验证）
    for name, cw in (("SYNC", M.POCSAG_SYNC), ("IDLE", M.POCSAG_IDLE)):
        fixed, n = bch.correct(cw)
        assert n == 0 and fixed == cw, f"{name} 应无错"
    # 任意 21bit 数据编码后应能干净还原
    for d in (0, 0x100000, 0x12345, 0x1FFFFF):
        cw = bch.encode(d)
        fixed, n = bch.correct(cw)
        assert n == 0 and fixed == cw
    # 1bit / 2bit 错误必纠
    cw = bch.encode(0xABCDE)
    for errbits in ([7], [3, 25], [1, 30]):
        bad = cw
        for b in errbits:
            bad ^= (1 << b)
        fixed, n = bch.correct(bad)
        assert fixed == cw, f"错误位 {errbits} 未纠对"
        assert n == len(errbits)
    print("  [ok] BCH(31,21) 校验矢量: SYNC/IDLE 伴随=0, 1/2bit 可纠")


# ======================================================================
#  4. AFSK1200 往返
# ======================================================================
def test_afsk1200_roundtrip() -> None:
    af = M.AFSK1200Demod(22050)
    # 前导 01010101 让位同步锁相（对照 demod_afsk12.c:103-108 跳变沿时钟恢复）
    preamble = np.array([1, 0, 1, 0, 1, 0, 1, 0], dtype=np.uint8)
    data = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 0], dtype=np.uint8)
    bits = np.concatenate([preamble, data])
    audio = af.modulate(bits)
    rec = af.demodulate(audio)
    assert len(rec) >= len(data), f"解码比特过少: {len(rec)}"
    # 在数据段窗口内找最佳对齐（前导 8 bit 用于锁相）
    best = 0
    for off in range(0, 10):
        seg = rec[off:off + len(data)]
        m = int(np.sum(seg == data[:len(seg)]))
        best = max(best, m)
    assert best >= len(data) - 1, f"AFSK 匹配过低: {best}/{len(data)}"
    print(f"  [ok] AFSK1200 往返: in={len(bits)}bit(含前导) out={len(rec)}bit "
          f"data段 best={best}/{len(data)}")


# ======================================================================
#  5. DTMF 往返
# ======================================================================
def test_dtmf_roundtrip() -> None:
    dec = M.DTMFDecoder(22050)
    for d in "123A456B789C*0#D":
        audio = dec.encode(d, dur=0.15)
        got = dec.decode(audio)
        assert got == d, f"DTMF {d!r} 解成 {got!r}"
    print("  [ok] DTMF 往返: 16 个按键全部识别正确")


# ======================================================================
#  6. ZVEI-1 往返
# ======================================================================
def test_zvei_roundtrip() -> None:
    z = M.ZVEIDecoder(22050, tone_dur=0.06)
    for code in ("A3F91", "00001", "FEDCB"):
        audio = z.encode(code)
        got = z.decode(audio, n_tones=len(code))
        assert got == code, f"ZVEI {code!r} 解成 {got!r}"
    print("  [ok] ZVEI-1 往返: 5 音调选呼码识别正确")


# ======================================================================
def main() -> int:
    print("== multimon-ng 数字模式真实往返测试 ==")
    test_pocsag_bch_vectors()
    test_pocsag_message_roundtrip()
    test_pocsag_bch_2bit_correction()
    test_afsk1200_roundtrip()
    test_dtmf_roundtrip()
    test_zvei_roundtrip()
    print("== 全部通过 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
