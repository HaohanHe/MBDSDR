# SPDX-License-Identifier: MIT
"""FT8 跨实现对拍（Python 侧孪生）：共享 LLR fixture -> 解码文本 == WSJT-X 真值。

与 C++ `cpp/tests/test_ft8_wsjtx_cross.cpp` 读**同一份** fixture
（`cpp/tests/ft8_wsjtx_llr.txt`）：同一输入 -> 两家解码层输出一致。
fixture 溯源：前 5 行 = WSJT-X 2.7.0 官方样本 210703_133430.wav 真实信号
软 LLR（jt9 官方文本为真值，含 -15dB 弱信号）；后 2 行合成（编码器生成，
钉 R 标志与 RRR 特例——真实 14 条全为纯报告/网格，R 路无真实覆盖）。

2026-11-02 对拍战果（本 fixture + 双侧测试逼出的真雷，已修）：
  - `mbdsdr_ai/ft8_codec.unpack77` + `cpp/dsp/ft8_codec`：报告字段把 irpt
    原值当报告数（"R27" 而非 "-08"），RRR/RR73/73/空全没做；
  - `mbdsdr_ai/ft8_unpack.unpack77`：R 标志渲染夹在两个呼号之间
    （"BI1AAA R K1ABC -07"）而非 WSJT-X 官方用户手册的
    "K1ABC G0XYZ R-22" 标准形态（R 挂报告上）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.ft8_codec import unpack77 as unpack77_struct  # noqa: E402
from mbdsdr_ai.ft8_decode import check_crc14  # noqa: E402
from mbdsdr_ai.ft8_ldpc import ldpc_bp_decode  # noqa: E402
from mbdsdr_ai.ft8_unpack import unpack77 as unpack77_text  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "cpp", "tests", "ft8_wsjtx_llr.txt")

EXPECTED_ROWS = 7          # 5 真实 + 2 合成
REAL_ROWS = 5
SYNTH_ROWS = 2


def _load_rows():
    rows = []
    with open(FIXTURE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p1 = line.index(" | ")
            p2 = line.index(" | ", p1 + 3)
            tag = line[:p1]
            expected = line[p1 + 3:p2]
            llr = [float(x) for x in line[p2 + 3:].split()]
            rows.append((tag, expected, llr))
    return rows


@pytest.fixture(scope="module")
def rows():
    assert os.path.exists(FIXTURE), f"共享 fixture 缺失: {FIXTURE}"
    return _load_rows()


def test_fixture_shape(rows):
    """fixture 形状：7 行（5 真 + 2 合成）、每行 174 LLR。"""
    assert len(rows) == EXPECTED_ROWS
    real = [r for r in rows if r[0] != "synthetic"]
    synth = [r for r in rows if r[0] == "synthetic"]
    assert len(real) == REAL_ROWS and len(synth) == SYNTH_ROWS
    for _tag, _exp, llr in rows:
        assert len(llr) == 174


def test_struct_unpack_reconstructs_truth_texts(rows):
    """ft8_codec.unpack77（结构化，C++ 规范源）字段重建 == 真值文本。

    重建规则与 C++ 对拍测试相同：from + ' ' + to + [' ' + exchange]。
    """
    for _tag, expected, llr in rows:
        cw, _it = ldpc_bp_decode(list(llr), max_iter=50)
        info = cw[:91]
        assert check_crc14(info), f"{expected!r}: CRC14 未过"
        parsed = unpack77_struct(list(info[:77]))
        assert parsed is not None, f"{expected!r}: 结构化解包失败"
        got = parsed["from"] + " " + parsed["to"]
        if parsed["exchange"]:
            got += " " + parsed["exchange"]
        assert got == expected, f"结构化路径得 {got!r}，期望 {expected!r}"


def test_text_unpack_renders_wsjtx_texts(rows):
    """ft8_unpack.unpack77（文本渲染）== WSJT-X 真值文本（R 挂报告上）。"""
    for _tag, expected, llr in rows:
        cw, _it = ldpc_bp_decode(list(llr), max_iter=50)
        info = cw[:91]
        d = unpack77_text(list(info[:77]))
        text = d.get("text") if isinstance(d, dict) else None
        assert text == expected, f"文本路径得 {text!r}，期望 {expected!r}"


def test_r_report_text_is_wsjtx_form(rows):
    """R 报告专项：R 必须挂报告上（第三字段），不许夹在呼号间。"""
    r_rows = [r for r in rows if " R-" in " " + r[1] or " R+" in " " + r[1]]
    assert r_rows, "fixture 应含 R 报告行"
    for _tag, expected, _llr in r_rows:
        # WSJT-X 标准形态：<call1> <call2> R±nn——R 在第三个 token 开头
        tokens = expected.split()
        assert tokens[2].startswith("R"), f"{expected!r}: R 应在第三字段"
