# -*- coding: utf-8 -*-
"""空结果诊断门禁：工具"成功但结果为空"时必须带事实性说明。

背景：模型无法区分"没收到信号"与"参数没配上"。此前 6 个工具在结果为空时
只回空列表/count=0，模型会误读成"什么都没发生"。现各工具在空结果时给出
note（事实性说明，不是猜测），本测试把该性质钉住。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402

# (工具, 参数, 空结果的判据键)
EMPTY_CASES = [
    ("adsb_decode_iq", {"iq": [0.0] * 64}, "frames"),
    ("ax25_decode", {"hex": "82A0A4A64040E08482626466686103F0"}, "digipeaters"),
    ("sdr_aprs_decode",
     {"hex_data": "82A0A4A64040E08482626466686103F0"}, "packets"),
    ("sdr_kiss_decode",
     {"hex_data": "F082A0A4A64040E084826264666668F0"}, "frames"),
    ("fe_link_budget", {"diameter_m": 1.8, "frequency_mhz": 1690.0}, "findings"),
    ("cantenna_design", {"frequency_mhz": 1690.0,
                          "tank_inner_diameter_mm": 126.0}, "findings"),
]

DIAG_KEYS = ("diagnostic", "hint", "note", "reason", "next_steps", "warning",
             "cause", "explanation", "empty_reason")


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


@pytest.mark.parametrize("tool,args,empty_key", EMPTY_CASES)
def test_empty_result_carries_explanation(reg, tool, args, empty_key):
    r = reg.call(tool, args)
    assert r.success is True, (tool, r.content[:120])
    d = r.data or {}
    v = d.get(empty_key)
    assert v == [] or v == 0, f"{tool}.{empty_key} 不再为空：{v!r}（用例需更新）"
    diag = [k for k in DIAG_KEYS if k in d and d[k]]
    assert diag, f"{tool} 空结果但没有任何说明（{empty_key} 为空）"
    # 说明必须是事实性的具体描述，不是空串或"无"
    text = str(d[diag[0]])
    assert len(text) >= 8, f"{tool} 的说明过短：{text!r}"


def test_notes_are_factual_not_placeholder(reg):
    """说明要给出具体事实（数字/字段名），不是套话。"""
    r = reg.call("adsb_decode_iq", {"iq": [0.0] * 64})
    note = str((r.data or {}).get("note", ""))
    assert "门限" in note or "候选" in note, note

    r2 = reg.call("sdr_kiss_decode",
                  {"hex_data": "F082A0A4A64040E084826264666668F0"})
    note2 = str((r2.data or {}).get("note", ""))
    assert "KISS" in note2 and "FEND" in note2, note2
