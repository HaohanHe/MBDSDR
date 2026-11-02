# SPDX-License-Identifier: MIT
"""
tests/test_astu_profile_merge.py
=================================
验证 ASTU-1 遥测工具消费 sat_profiles.json（统一口径）:

  1. 缺省时取剖面值: sps=5@48k / use_viterbi=True / ASM=0x1ACFFC1D /
     g1=0o133 g2=0o171（此前默认 4 与卷积关是错口径）;
  2. 显式参数覆盖剖面;
  3. 不误改 scheme/alpha。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.ccsds_tools import _astu_profile_args  # noqa: E402


def test_profile_defaults():
    a = _astu_profile_args({})
    assert a["sps"] == 5, a
    assert a["use_viterbi"] is True
    assert a["asm_word"] == 0x1ACFFC1D
    assert a["g1"] == 0o133 and a["g2"] == 0o171
    assert a["scheme"] == "bpsk"
    assert abs(a["alpha"] - 0.35) < 1e-9
    assert a["use_descramble"] is True


def test_explicit_args_override_profile():
    a = _astu_profile_args({"sps": 4, "use_viterbi": False,
                            "asm_word": 0xDEADBEEF})
    assert a["sps"] == 4
    assert a["use_viterbi"] is False
    assert a["asm_word"] == 0xDEADBEEF
