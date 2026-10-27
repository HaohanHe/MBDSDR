# SPDX-License-Identifier: MIT
"""diff_encode / diff_decode 工具面测试：注册 + 往返自洽 + 诚实拒绝。

往返语义（DifferentialEncoder，实现级测试在根 tests/test_fec.py）：
输出与输入等长，参考符号 0 仅作异或种子，decode(encode(x)) == x。
工具面必须把相同契约通过 data.bits 程序化载荷交付。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_diff_codec_tools_registered(reg):
    names = set(reg.get_tool_names())
    assert "diff_encode" in names
    assert "diff_decode" in names


@pytest.mark.parametrize("order", [2, 4])
def test_diff_round_trip_via_registry(reg, order):
    bits = [1, 0, 1, 1, 0, 0, 1, 0] * 4   # 长度 32，是 1 与 2 的倍数
    enc = reg.call("diff_encode", {"bits": bits, "order": order})
    assert enc.success is True, enc.content[:160]
    assert enc.data.get("bits"), "载荷必须在 data.bits"
    assert len(enc.data["bits"]) == len(bits), \
        "差分编码输出必须与输入等长（不追加参考比特）"
    dec = reg.call("diff_decode", {"bits": enc.data["bits"], "order": order})
    assert dec.success is True, dec.content[:160]
    assert dec.data.get("bits") == bits, \
        f"order={order}: decode(encode(x)) 必须还原原序列"


def test_empty_bits_rejected_honestly(reg):
    r = reg.call("diff_encode", {"bits": []})
    assert r.success is False
    assert r.content, "空输入必须显式拒绝并说明（诚实空态）"


def test_non_binary_bits_rejected_honestly(reg):
    r = reg.call("diff_encode", {"bits": [0, 2, 1, 0]})
    assert r.success is False
    assert "0/1" in r.content, r.content[:160]


def test_bad_length_rejected_honestly(reg):
    # order=4 时长度必须是每符号比特数（2）的倍数
    r = reg.call("diff_decode", {"bits": [1, 0, 1], "order": 4})
    assert r.success is False
    assert "倍数" in r.content, r.content[:160]
