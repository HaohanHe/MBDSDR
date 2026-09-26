"""
ACARS 协议合成往返测试
======================

本测试不接收任何真实空中信号，仅在 tests/ 目录内合成 MSK 音频、
加高斯噪声、再解码，验证 acars_protocol.py 的编解码自洽性。

测试用注册号 BA1234、标签 H1 等均为虚构值，不对应任何真实航空器。

参考：
  - repos/acarsdec/acars.c:22-27      控制字符
  - repos/acarsdec/acars.c:246-375    帧同步状态机
  - repos/acarsdec/msk.c:53-63         LSB 先发
  - repos/acarsdec/msk.c:81            中心 1800 Hz
  - repos/libacars/libacars/acars.c:272-385  字段解析
  - repos/libacars/libacars/crc.c:73-115      CRC-16-CCITT
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

# 确保仓库根目录在 sys.path 中（便于直接 pytest 运行）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mbdsdr_ai.acars_protocol import (  # noqa: E402
    acars_encode,
    acars_decode,
    crc16_ccitt,
    build_frame_bytes,
    SYN, SOH, ETX, ETB, DEL, STX,
)


# ─────────────────────────────────────────────────────────────────────
# 工具：按 SNR 加高斯噪声
# ─────────────────────────────────────────────────────────────────────
def _add_gauss_noise(signal: np.ndarray, snr_db: float = 15.0,
                     rng_seed: int = 0) -> np.ndarray:
    """按指定 SNR（dB）给信号加高斯白噪声。"""
    rng = np.random.default_rng(rng_seed)
    sig_p = float(np.mean(signal ** 2))
    noise_p = sig_p / (10.0 ** (snr_db / 10.0))
    noise_std = float(np.sqrt(noise_p))
    return signal + rng.standard_normal(len(signal)) * noise_std


# ─────────────────────────────────────────────────────────────────────
# 用例 1：基本文本消息（2400 bps）
# ─────────────────────────────────────────────────────────────────────
def test_basic_text_roundtrip_2400bps():
    """构造已知消息 -> 编码 -> 加 15dB 噪声 -> 解码，字段应与原文一致。"""
    mode = '2'
    reg = 'BA1234'       # 虚构注册号
    label = 'H1'
    text = 'TEST MESSAGE'
    baud = 2400
    sample_rate = 48000

    audio = acars_encode(mode=mode, reg=reg, label=label, text=text,
                         baud=baud, sample_rate=sample_rate)
    rx = _add_gauss_noise(audio, snr_db=15.0, rng_seed=42)

    msgs = acars_decode(rx, sample_rate=sample_rate, baud=baud)
    assert len(msgs) >= 1, "未解码出任何 ACARS 消息"
    m = msgs[0]
    assert m["crc_ok"] is True, f"CRC 校验未通过: {m}"
    assert m["mode"] == mode
    assert m["reg"].strip() == reg, f"reg 不符: {m['reg']!r}"
    assert m["label"] == label
    assert m["text"] == text, f"text 不符: {m['text']!r}"
    assert m["final_block"] is True


# ─────────────────────────────────────────────────────────────────────
# 用例 2：ETB 多块消息（非最终块）
# ─────────────────────────────────────────────────────────────────────
def test_etb_multi_block_roundtrip():
    """final_block=False 时应使用 ETB(0x97) 结尾，final_block 字段应为 False。"""
    mode = '2'
    reg = 'BA1234'
    label = 'H1'
    text = 'PART ONE'
    baud = 2400
    sample_rate = 48000

    audio = acars_encode(mode=mode, reg=reg, label=label, text=text,
                         baud=baud, sample_rate=sample_rate,
                         final_block=False, block_id='1')
    rx = _add_gauss_noise(audio, snr_db=15.0, rng_seed=7)

    msgs = acars_decode(rx, sample_rate=sample_rate, baud=baud)
    assert len(msgs) >= 1
    m = msgs[0]
    assert m["crc_ok"] is True
    assert m["final_block"] is False, "ETB 块应标记为非最终块"
    assert m["block_id"] == '1'
    assert m["reg"].strip() == reg
    assert m["text"] == text


# ─────────────────────────────────────────────────────────────────────
# 用例 3：1200 bps 兼容
# ─────────────────────────────────────────────────────────────────────
def test_roundtrip_1200bps():
    """同一组字段在 1200 bps 下也应能往返解码。"""
    mode = '2'
    reg = 'BA1234'
    label = 'H1'
    text = 'HELLO 1200'
    baud = 1200
    sample_rate = 48000

    audio = acars_encode(mode=mode, reg=reg, label=label, text=text,
                         baud=baud, sample_rate=sample_rate)
    rx = _add_gauss_noise(audio, snr_db=15.0, rng_seed=99)

    msgs = acars_decode(rx, sample_rate=sample_rate, baud=baud)
    assert len(msgs) >= 1
    m = msgs[0]
    assert m["crc_ok"] is True
    assert m["reg"].strip() == reg
    assert m["label"] == label
    assert m["text"] == text


# ─────────────────────────────────────────────────────────────────────
# 用例 4：CRC-16-CCITT 自检（字节级，不走音频）
# ─────────────────────────────────────────────────────────────────────
def test_crc_self_consistency():
    """build_frame_bytes 产出的帧，对 [SOH后..ETX]+crc 求余数应为 0。

    参考 libacars/acars.c:296-299：crc = la_crc16_ccitt(buf, len, 0);
    crc_ok = (crc == 0)。
    """
    frame = build_frame_bytes(mode='2', reg='BA1234', label='H1',
                             text='CRC CHECK')
    # frame = SYN SYN SOH body
    # body 从第 3 字节开始，去掉末尾 DEL
    body = frame[3:]
    assert body[-1] == DEL
    without_del = body[:-1]
    residue = crc16_ccitt(without_del, 0x0000)
    assert residue == 0, f"CRC 余数应为 0，实际 0x{residue:04x}"


# ─────────────────────────────────────────────────────────────────────
# 用例 5：帧前导 SYN SYN SOH 存在
# ─────────────────────────────────────────────────────────────────────
def test_frame_preamble_present():
    """编码产出的帧字节流应以 SYN SYN SOH 开头。

    参考 repos/acarsdec/acars.c:22-24, 246-301 状态机。
    """
    frame = build_frame_bytes(mode='2', reg='BA1234', label='H1', text='X')
    assert frame[0] == SYN
    assert frame[1] == SYN
    assert frame[2] == SOH
    # ETX/ETB 之后应有 2 字节 CRC + DEL
    assert frame[-1] == DEL
