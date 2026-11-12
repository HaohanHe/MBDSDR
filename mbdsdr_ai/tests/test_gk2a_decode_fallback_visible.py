# SPDX-License-Identifier: MIT
"""A03：GK-2A JPEG2000 解码回退必须可归因（第三批）。

回退「无压缩直通」是有意的安全降级（docstring 已声明），
但只标 "none[回退]" 无法区分「格式不支持」与「数据损坏」，必须留原因。

反证：删除 decode_jpeg2000 里新增的 warning 后本用例变红。
"""
import logging
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.gk2a_lrit import decode_jpeg2000  # noqa: E402


def test_garbage_payload_logs_reason(caplog):
    with caplog.at_level(logging.WARNING, logger="mbdsdr_ai.gk2a_lrit"):
        assert decode_jpeg2000(b"\x00\x01garbage") is None

    messages = [r.getMessage() for r in caplog.records]
    assert any("JPEG2000 解码失败" in m for m in messages), "回退必须留下原因"
    assert any("Error" in m for m in messages), "日志必须带异常类型"


def test_empty_payload_is_not_degradation(caplog):
    with caplog.at_level(logging.WARNING, logger="mbdsdr_ai.gk2a_lrit"):
        assert decode_jpeg2000(b"") is None
    assert not [r for r in caplog.records if "JPEG2000" in r.getMessage()]
