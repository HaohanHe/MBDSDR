# SPDX-License-Identifier: MIT
"""W69 反证：解码错误码必须有中文标签与补救动作（P1-22）。

改前：`address_not_recovered`（POCSAG/信号弱时最常见）等码没有中文标签，
面板只显示裸 code；整张卡也没有任何「下一步怎么办」——用户看得到失败，
不知道该动哪个旋钮。

改后：标签表覆盖 decode_router 声明的全部 ERR_*，补救表与之一一对应，
`format_decode_result` 在失败时输出 `补救: …` 并回传 remedy 字段；
未登记的新码不许静默，要显式写「未知错误码 X」。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from mbdsdr_ai import decode_router  # noqa: E402
from status_panel import (  # noqa: E402
    DECODE_ERROR_LABELS, DECODE_ERROR_REMEDY, format_decode_result,
)


def _router_codes():
    return sorted(
        v for k, v in vars(decode_router).items()
        if k.startswith("ERR_") and isinstance(v, str))


#: P1-22 点名的漏标码 + 解码卡可能收到的 MCP 侧码。
_EXTRA_CODES = ["address_not_recovered", "not_connected",
                "decoder_not_available"]


def test_router_codes_are_discovered():
    """先钉住枚举口径：没有 ERR_* 就说明取码方式错了，别让下面的用例空转。"""
    codes = _router_codes()
    assert len(codes) >= 10, codes
    assert "nothing_detected" in codes and "not_implemented" in codes


@pytest.mark.parametrize("code", _router_codes() + _EXTRA_CODES)
def test_every_code_has_label_and_remedy(code):
    assert code in DECODE_ERROR_LABELS, f"{code} 没有中文标签，用户只看到裸码"
    assert code in DECODE_ERROR_REMEDY, f"{code} 没有补救动作，用户不知道下一步"
    label = DECODE_ERROR_LABELS[code]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in label), label
    assert len(DECODE_ERROR_REMEDY[code]) >= 8, DECODE_ERROR_REMEDY[code]


@pytest.mark.parametrize("code", _router_codes() + _EXTRA_CODES)
def test_failure_result_carries_remedy(code):
    out = format_decode_result(
        "sdr_decode_test", {"mode": "pocsag", "ok": False,
                            "error_code": code, "error": "x", "num_messages": 0})
    assert out["ok"] is False
    assert out["remedy"] == DECODE_ERROR_REMEDY[code]
    assert "补救: " in out["detail"]
    assert out["summary"].startswith("pocsag · ")


def test_unmapped_code_is_not_silent():
    out = format_decode_result(
        "sdr_decode_test", {"mode": "aprs", "ok": False,
                            "error_code": "brand_new_code", "error": "boom"})
    assert "brand_new_code" in out["remedy"]
    assert "未知错误码" in out["remedy"]
    assert "补救: " in out["detail"]


def test_success_has_no_remedy_noise():
    out = format_decode_result(
        "sdr_decode_aprs", {"mode": "aprs", "ok": True,
                            "output_kind": "messages", "messages": []})
    assert out["remedy"] == ""
    assert "补救" not in out["detail"]


def test_signal_present_no_frame_is_readable():
    """G5 点名的码：必须出现中文原因 + 具体补救，不是裸码。"""
    out = format_decode_result(
        "sdr_decode_acars",
        {"mode": "acars", "ok": False,
         "error_code": "signal_present_no_frame",
         "error": "energy above noise but no frame"})
    assert "有信号" in out["summary"]
    assert "解调" in out["remedy"]
    assert json.dumps(out, ensure_ascii=False)
