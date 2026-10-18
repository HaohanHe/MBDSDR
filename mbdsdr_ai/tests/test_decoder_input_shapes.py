# SPDX-License-Identifier: MIT
"""解码器输入形状校验测试（冒烟找出的标量绕过检查类崩溃销账）。

覆盖：标量/字符串输入诚实拒绝（不崩溃），正常数组照常解码。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.multimon_decoders import (DTMFDecoder, ZVEIDecoder,
                                         AFSK1200Demod)
from mbdsdr_ai.minimodem_adapter import (bell103_demodulate_text,
                                         fsk_demodulate,
                                         minimodem_decode_audio)


def test_bell103_rejects_scalar():
    with pytest.raises(ValueError):
        bell103_demodulate_text(1)


def test_fsk_rejects_scalar():
    with pytest.raises(ValueError):
        fsk_demodulate(1)


def test_minimodem_rejects_string():
    with pytest.raises(ValueError):
        minimodem_decode_audio("noise")


def test_fsk_accepts_array():
    audio = np.zeros(4800, dtype=np.float64)
    res = fsk_demodulate(audio, sample_rate=48000)
    assert "bits" in res and "confidence" in res


def _reg_call(tool_fn, args):
    """直接调工具函数，收到字典形状错误信封。"""
    return tool_fn(args)


def test_dtmf_handler_shape_guard():
    """通过 registry 真实调用 dtmf_decode：标量输入 -> 诚实失败而非崩溃。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    from mbdsdr_ai import multimon_decoders as md
    reg = ToolRegistry()
    md.register_multimon_tools(reg)
    res = reg.call("dtmf_decode", {"samples": 1, "sample_rate": 8000})
    assert res.success is False
    assert "数字数组" in (res.content or "")
