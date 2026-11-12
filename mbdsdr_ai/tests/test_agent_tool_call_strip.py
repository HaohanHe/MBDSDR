# SPDX-License-Identifier: MIT
"""A03：工具调用剥离的两处「非 JSON 就保留原文」必须可归因。

分类结论：**允许的降级（控制流）**，不是真坏。
- 围栏/裸 JSON 解析不了 → 原文不是工具调用 → 保留原文本就是正确行为；
- 但旧实现 `except Exception: pass` 同时吞掉了两类信息：
  1) 模型吐出「看起来像工具调用、实际是坏 JSON」时无任何记录；
  2) name/tool/function 结构异常时抛出的 TypeError/AttributeError
     （如 name 是 dict 时 `in set` 抛 unhashable）也被一并吞掉。

新契约：解析失败 → 保留原文 + 一条 debug 记录；结构异常 → 工具名返回
None（视为非工具调用），不再依赖宽 except 兜底。

反证：删除两处新增的 logger.debug 后，对应用例变红。
"""
import logging
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


class _Registry:
    def get_tool_names(self):
        return ["get_time", "list_satellites"]


class _FakeAgent:
    tool_registry = _Registry()


def _strip(text):
    return MBDSDRAgent._strip_tool_call_text(_FakeAgent(), text)


def test_non_json_fence_is_kept_and_logged(caplog):
    text = "说明文字\n```json\n{ this is not json }\n```"
    with caplog.at_level(logging.DEBUG, logger="mbdsdr_ai.agent"):
        out = _strip(text)

    assert out == text, "非 JSON 围栏应原样保留（不是工具调用）"
    assert any("不是 JSON" in r.getMessage() for r in caplog.records), \
        "保留原文的决定必须留下可查记录"


def test_real_tool_call_fence_is_removed():
    text = '说明\n```json\n{"name": "get_time"}\n```'
    assert _strip(text) == "说明"


def test_non_json_bare_object_is_kept_and_logged(caplog):
    text = "{ not a valid tool call }"
    with caplog.at_level(logging.DEBUG, logger="mbdsdr_ai.agent"):
        out = _strip(text)

    assert out == text
    assert any("裸 JSON 解析失败" in r.getMessage() for r in caplog.records)


def test_bare_tool_call_is_removed():
    assert _strip('{"tool": "get_time"}') == ""


def test_unhashable_tool_name_does_not_crash():
    """name 是对象时旧实现会在 `in set` 抛 TypeError 并被宽 except 吞掉。

    新实现按「不是字符串就不算工具名」处理：不崩、围栏原样保留。
    """
    text = '{"name": {"nested": 1}, "function": "not-a-dict"}'
    assert _strip(text) == text


def test_function_as_string_does_not_crash():
    text = '{"function": "get_time"}'
    assert _strip(text) == text
