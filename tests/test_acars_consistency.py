# SPDX-License-Identifier: MIT
"""ACARS 编解码一致性回归（钉死"合成→解码"必须 crc_ok 通过）。

背景：仓内曾并存两份 ACARS 实现（acars_protocol 自包含版与 acars_decoder
状态机版），其中状态机版对**自己合成**的信号自环不通（crc_ok=False），
而既有测试只覆盖了自包含版——于是这条缺陷长期不被发现。本文件把
「公开入口必须能解出 crc_ok=True 的帧」钉死，两条路径都必须满足。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.acars_decoder import acars_decode_iq  # noqa: E402
from mbdsdr_ai.acars_protocol import acars_decode, acars_encode  # noqa: E402


def _noisy(audio, snr_db=15.0, seed=42):
    rng = np.random.default_rng(seed)
    p = float(np.mean(audio ** 2))
    return audio + rng.standard_normal(len(audio)) * np.sqrt(
        p / (10.0 ** (snr_db / 10.0)))


def test_canonical_entry_decodes_synthesized_frame():
    """公开入口 acars_decode_iq 必须能解出本模块合成的帧（crc_ok=True）。"""
    audio = acars_encode("2", "BA1234", "H1", "TEST MESSAGE",
                         baud=2400, sample_rate=48000)
    msgs = acars_decode_iq(_noisy(audio), sample_rate=48000, baud_rate=2400)
    assert len(msgs) >= 1
    m = msgs[0]
    assert m["crc_ok"] is True, m
    assert m["reg"].strip() == "BA1234"
    assert m["label"] == "H1"
    assert m["text"] == "TEST MESSAGE"


def test_two_entrypoints_agree():
    """两条公开路径（acars_protocol.acars_decode 与 acars_decoder.acars_decode_iq）
    对同一音频必须给出一致结果（唯一实现，禁止行为分叉）。"""
    audio = acars_encode("2", "BA1234", "H1", "AGREE ME",
                         baud=2400, sample_rate=48000)
    rx = _noisy(audio, snr_db=15.0, seed=7)
    a = acars_decode(rx, sample_rate=48000, baud=2400)
    b = acars_decode_iq(rx, sample_rate=48000, baud_rate=2400)
    assert len(a) == len(b) >= 1
    for x, y in zip(a, b):
        assert x["crc_ok"] == y["crc_ok"]
        assert x["text"] == y["text"]
        assert x["reg"].strip() == y["reg"].strip()


def test_etb_multiblock_flag_survives():
    """非最终块（ETB）必须仍被标为 final_block=False。"""
    audio = acars_encode("2", "BA1234", "H1", "PART ONE", baud=2400,
                         sample_rate=48000, final_block=False, block_id="1")
    msgs = acars_decode_iq(_noisy(audio, seed=3), sample_rate=48000,
                           baud_rate=2400)
    assert len(msgs) >= 1
    assert msgs[0]["crc_ok"] is True
    assert msgs[0]["final_block"] is False
    assert msgs[0]["block_id"] == "1"


def test_1200bps_still_works():
    """1200 bps 兼容路径同样必须能解出并 crc_ok。"""
    audio = acars_encode("2", "BA1234", "H1", "SLOW", baud=1200,
                         sample_rate=48000)
    msgs = acars_decode_iq(_noisy(audio, snr_db=15.0, seed=11),
                           sample_rate=48000, baud_rate=1200)
    assert len(msgs) >= 1
    assert msgs[0]["crc_ok"] is True
    assert msgs[0]["text"] == "SLOW"
