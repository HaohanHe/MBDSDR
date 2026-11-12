# SPDX-License-Identifier: MIT
"""步骤卡投影的两处解析降级必须可归因。

`_parse_args` / `_payload_from_content` 拿不到 JSON 时分别退化为「空参数」
与「原文透传」——这是展示层控制流，不是工具真的收到了空参数。降级本身
允许，但「坏参数」和「本来没参数」在卡片上长得一模一样，所以原因必须
能在日志里查到。
"""
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import ai_steps  # noqa: E402


def test_bad_args_json_falls_back_and_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger="ai_steps"):
        assert ai_steps._parse_args("{not json") == {}

    assert len(caplog.records) == 1
    assert "不是 JSON" in caplog.records[0].getMessage()
    assert "Expecting" in caplog.records[0].getMessage()  # 记录的是解析器给的原因


def test_good_args_json_stays_quiet(caplog):
    with caplog.at_level(logging.DEBUG, logger="ai_steps"):
        assert ai_steps._parse_args('{"freq_mhz": 98.5}') == {"freq_mhz": 98.5}

    assert caplog.records == []


def test_bad_result_content_falls_back_and_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger="ai_steps"):
        assert ai_steps._payload_from_content('{"ok": broken') is None

    assert len(caplog.records) == 1
    assert "声称是 JSON" in caplog.records[0].getMessage()


def test_plain_text_result_is_not_a_degradation(caplog):
    """非 JSON 文本结果本来就是常态（工具直接回自然语言），不得刷日志。"""
    with caplog.at_level(logging.DEBUG, logger="ai_steps"):
        assert ai_steps._payload_from_content("巳调谐到 98.5 MHz") is None

    assert caplog.records == []


def test_bad_args_still_produces_step_card(caplog):
    """降级不得改变投影结果：步骤卡照常出现，只是参数为空。"""
    calls = [{"name": "tune_fm", "arguments": "{broken"}]
    with caplog.at_level(logging.DEBUG, logger="ai_steps"):
        steps = ai_steps.steps_from_chat(calls, [])

    assert len(steps) == 1
    assert steps[0].tool == "tune_fm"
    assert any("不是 JSON" in r.getMessage() for r in caplog.records)
