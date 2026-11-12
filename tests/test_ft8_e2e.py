# SPDX-License-Identifier: MIT
"""FT8 端到端：合成音频 → ft8_lite.decode_ft8_audio 解出原文。

原为 ``mbdsdr_ai/ft8_costas.py`` 的 ``_self_test``（库内自测）。它必须 import
``ft8_lite``，而 ft8_lite 又依赖 ft8_costas——这条函数体内 import 是
``ft8_costas ↔ ft8_lite`` 两模块 import 环的唯一闭合边。自测迁到 ``tests/``
后库内不再反向依赖（``tests -> mbdsdr_ai`` 是允许方向），环随之消失。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.ft8_costas import _synthesize_ft8_audio  # noqa: E402
from mbdsdr_ai.ft8_lite import decode_ft8_audio  # noqa: E402

SR = 12000
TEXT = "CQ K1ABC OM74"


def test_decode_ft8_audio_recovers_message():
    """真实编码链：79 音调合成 + 3.7Hz 频偏 + 0.5s 时偏 + 噪声。"""
    samples, _true_center = _synthesize_ft8_audio(
        TEXT, center_hz=1500.0, f_offset_hz=3.7, t_offset_s=0.5,
        sigma_noise=0.15, sr=SR, seed=0)
    out = decode_ft8_audio(samples, SR)
    assert out.get("decoded") is True, f"必须解出消息，实际 {out}"
    assert out.get("message") == TEXT, f"必须解出原文，实际 {out.get('message')!r}"
