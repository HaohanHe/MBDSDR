# SPDX-License-Identifier: MIT
"""ADS-B 两实现（adsb.py 完整版 / adsb_lite.py 精简版）交叉一致性回归。

背景：两份实现长期并存（lite 版被 sdr_tools / desktop 面板实际使用，完整版
提供 CPR 位置解算）。历史问题：
  1) lite 版门限在 MAD=0（无噪合成信号）时退回 std，被 OOK 脉冲本身抬高，
     恰好踩中其 docstring 声称要避免的"门限顶到脉冲之上"失效模式 → 漏检；
  2) 两份实现的帧构造若各自演进，会出现字节级分叉。

本文件把"两套实现对同一输入给出一致结果"钉死，并覆盖无噪这一退化输入。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import adsb, adsb_lite  # noqa: E402


def _id_frame(callsign="BA1234", icao=0x484000):
    """构造一帧 DF17 / TC=1 机载识别长报文。"""
    me = bytearray(7)
    me[0] = 1 << 3                      # TC=1
    me[1:7] = adsb.encode_callsign(callsign)
    return adsb.build_long_frame(0x8D, icao, bytes(me))


def test_frame_builders_are_byte_identical():
    """两份实现的帧构造必须逐字节相同（禁止各自演进分叉）。"""
    me = bytearray(7)
    me[0] = 1 << 3
    me[1:7] = adsb.encode_callsign("BA1234")
    a = adsb.build_long_frame(0x8D, 0x484000, bytes(me))
    b = adsb_lite.build_long_frame(0x8D, 0x484000, bytes(me))
    assert a == b
    assert len(a) == 14


@pytest.mark.parametrize("noise_std", [0.0, 0.003, 0.01, 0.03, 0.06])
def test_both_paths_agree_across_noise(noise_std):
    """同一 IQ 输入，两套实现必须解出同一架次同一呼号（crc_ok 一致）。"""
    frame = _id_frame()
    iq = adsb_lite.synthesize_modes_iq([frame], sample_rate=2_000_000,
                                        noise_std=noise_std)
    full = adsb.decode_baseband(iq, fs=2_000_000)
    lite = adsb_lite.decode_adsb(iq, sample_rate=2_000_000)
    frames = lite.get("frames") or []
    assert full.get("found") is True
    assert full.get("crc_ok") is True
    assert lite.get("crc_ok"), f"lite 未解出有效帧 (noise={noise_std})"
    assert len(frames) >= 1
    d = frames[0] if isinstance(frames[0], dict) else frames[0].to_dict()
    assert d.get("callsign") == full.get("callsign")
    assert d.get("icao") == full.get("icao")
    assert d.get("callsign") == "BA1234"


def test_noiseless_case_is_not_dropped():
    """无噪合成信号（MAD=0）必须仍能解出——防门限退化回归。"""
    frame = _id_frame()
    iq = adsb_lite.synthesize_modes_iq([frame], sample_rate=2_000_000,
                                        noise_std=0.0)
    lite = adsb_lite.decode_adsb(iq, sample_rate=2_000_000)
    assert lite.get("crc_ok"), "无噪输入被漏检（门限退化回归）"
    frames = lite.get("frames") or []
    d = frames[0] if isinstance(frames[0], dict) else frames[0].to_dict()
    assert d.get("callsign") == "BA1234"
