# -*- coding: utf-8 -*-
"""P14 参数空间鲁棒性守卫（CI 抽样口径）。

全量轰炸是手动的：``python scripts/tool_param_fuzz.py``。
本文件只做两件事：

1. **共享校验回归锁**：NaN/±Inf、1e300、负数采样率/频率、空数字数组、
   NaN 数组元素、整数数组混浮点——这些非法值必须在注册表层被拒绝，
   不能被 handler 当成合法值静默执行（典型例：NaN 频率被当成 100 MHz 调）。
2. **抽样轰炸**：对账本里的一小组一类工具跑真实对抗调用，断言没有
   崩溃（异常逃逸）/挂死/静默接受/无码失败。

离线、无硬件、静音、offscreen；不联网。
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402

from mbdsdr_ai.tool_registry import (  # noqa: E402
    ToolRegistry, ToolResult, validate_arguments,
)

#: 抽样工具：覆盖「数值频率类」「数组/字节类」「坐标类」三种输入形态。
SAMPLE_TOOLS = (
    "cantenna_design", "dish_gain", "fe_link_budget", "geo_pointing",
    "pointing_tolerance", "acars_parse_message", "dab_fic_decode",
    "coord_ecef_to_eci",
)


def _schema(**props):
    return {"type": "object", "properties": props, "required": list(props)}


# ── 1. 共享校验回归锁 ───────────────────────────────────────────
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_number_is_rejected(bad):
    problems = validate_arguments(
        {"type": "object",
         "properties": {"frequency_hz": {"type": "number"}},
         "required": ["frequency_hz"]},
        {"frequency_hz": bad})
    assert problems and problems[0]["error"] == "param_out_of_range"
    assert "有限数值" in problems[0]["message"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_integer_does_not_raise(bad):
    """整数参数收到 NaN/Inf 时，validate_arguments 不能抛 int(nan) 异常。"""
    problems = validate_arguments(
        {"type": "object",
         "properties": {"n": {"type": "integer"}},
         "required": ["n"]},
        {"n": bad})
    assert problems and problems[0]["error"] == "param_out_of_range"


def test_nan_string_is_not_coerced_into_valid_number():
    problems = validate_arguments(
        {"type": "object",
         "properties": {"frequency_hz": {"type": "number"}},
         "required": ["frequency_hz"]},
        {"frequency_hz": "NaN"})
    assert problems and problems[0]["error"] == "param_out_of_range"


def test_huge_number_is_rejected():
    problems = validate_arguments(
        {"type": "object",
         "properties": {"frequency_hz": {"type": "number"}},
         "required": ["frequency_hz"]},
        {"frequency_hz": 1e300})
    assert problems and problems[0]["error"] == "param_out_of_range"
    assert "上界" in problems[0]["message"]


@pytest.mark.parametrize("name,value", [
    ("sample_rate", -1024000),
    ("sample_rate_hz", 0),
    ("bandwidth_hz", -1),
    ("frequency_hz", -100e6),
    ("count", -1),
    ("duration_s", -1),
    ("rate", -1),
    ("decimation", -1),
    ("max_iter", -1),
    ("limit", -1),
])
def test_semantically_non_negative_param_rejects_negative(name, value):
    problems = validate_arguments(
        {"type": "object", "properties": {name: {"type": "number"}},
         "required": [name]},
        {name: value})
    assert problems and problems[0]["error"] == "param_out_of_range"


@pytest.mark.parametrize("name", ["sample_rate", "baud", "bandwidth_hz",
                                  "frequency_hz", "rate"])
def test_tiny_rate_is_rejected(name):
    """1e-300 的速率/频率会让采样点数爆量，必须按非法值拒绝。"""
    problems = validate_arguments(
        {"type": "object", "properties": {name: {"type": "number"}},
         "required": [name]},
        {name: 1e-300})
    assert problems and problems[0]["error"] == "param_out_of_range"
    assert "速率" in problems[0]["message"]


def test_negative_offsets_and_gains_still_allowed():
    """offset/doppler/gain 允许为负，共享校验不能误伤。"""
    schema = {"type": "object", "properties": {
        "offset_hz": {"type": "number"},
        "doppler_hz": {"type": "number"},
        "gain_db": {"type": "number"},
    }, "required": ["offset_hz", "doppler_hz", "gain_db"]}
    assert validate_arguments(schema, {"offset_hz": -1000,
                                       "doppler_hz": -250,
                                       "gain_db": -12}) == []


def test_required_numeric_array_cannot_be_empty():
    problems = validate_arguments(
        {"type": "object",
         "properties": {"iq": {"type": "array", "items": {"type": "number"}}},
         "required": ["iq"]},
        {"iq": []})
    assert problems and problems[0]["error"] == "param_out_of_range"
    assert "不能为空" in problems[0]["message"]


def test_numeric_array_nan_element_is_rejected():
    problems = validate_arguments(
        {"type": "object",
         "properties": {"iq": {"type": "array", "items": {"type": "number"}}},
         "required": ["iq"]},
        {"iq": [0.5, float("nan"), -0.5]})
    assert problems and problems[0]["error"] == "param_type_mismatch"
    assert "NaN" in problems[0]["message"]


def test_integer_array_rejects_float_items():
    """bytes()/索引运算吃不下浮点项，必须在注册表层拒，不能等 handler 抛。"""
    problems = validate_arguments(
        {"type": "object",
         "properties": {"data": {"type": "array", "items": {"type": "integer"}}},
         "required": ["data"]},
        {"data": [0.5, 1.0]})
    assert problems and problems[0]["error"] == "param_type_mismatch"


def test_valid_numeric_args_still_pass():
    schema = {"type": "object", "properties": {
        "frequency_hz": {"type": "number"},
        "sample_rate": {"type": "number"},
        "iq": {"type": "array", "items": {"type": "number"}},
    }, "required": ["frequency_hz", "sample_rate", "iq"]}
    assert validate_arguments(schema, {
        "frequency_hz": 100e6, "sample_rate": 2.4e6,
        "iq": [0.5, -0.5] * 4}) == []


# ── 2. 注册表层：非法值不得进入 handler ──────────────────────────
def _registry_with(handler, props, required=None):
    reg = ToolRegistry()
    reg.register(
        name="probe_tool", description="P14 守卫探针",
        parameters={"type": "object", "properties": props,
                    "required": required or list(props)},
        handler=handler, category="test")
    return reg


def test_nan_frequency_never_reaches_handler():
    """典型静默接受：NaN 频率被当成 100 MHz 调——必须在 handler 之前拒绝。"""
    called = []

    def handler(args):
        called.append(args)
        return ToolResult(success=True, content="已调谐到 100 MHz")

    reg = _registry_with(handler, {"frequency_hz": {"type": "number"}})
    for bad in (float("nan"), float("inf"), "NaN", 1e300, -100e6):
        r = reg.call("probe_tool", {"frequency_hz": bad})
        assert r.success is False
        assert r.error == "param_out_of_range"
        assert r.content.strip()
    assert called == []  # handler 一次都没被调用


def test_failure_without_error_code_is_structured():
    """handler 只回失败正文、不给码时，注册表统一补 handler_error。"""
    reg = _registry_with(
        lambda a: {"success": False, "content": "样本为空，未执行"},
        {"x": {"type": "number"}})
    r = reg.call("probe_tool", {"x": 1})
    assert r.success is False
    assert r.error == "handler_error"
    assert r.content.strip()


# ── 3. 抽样轰炸（全量手动跑 scripts/tool_param_fuzz.py）───────────
@pytest.fixture(scope="module")
def fuzz_sample():
    import tool_param_fuzz as fuzz

    names = fuzz._first_class_names() or []
    sample = [n for n in SAMPLE_TOOLS if n in names] or list(SAMPLE_TOOLS)
    return fuzz.run_fuzz(only=sample, timeout=3.0)


def test_sample_has_no_crash_hang_or_silent_accept(fuzz_sample):
    assert len(fuzz_sample) >= 20, "抽样调用数过少，用例生成可能失效"
    bad = [r for r in fuzz_sample
           if r["class"] in ("CRASH", "HANG", "SILENT", "UNSTRUCT")]
    assert not bad, "\n".join(
        f"[{r['class']}] {r['tool']} {r['case']}: {r['detail']}" for r in bad)


def test_typical_silent_accept_lock(fuzz_sample):
    """cantenna_design 的 NaN 频率曾被当成合法频率算出结果——回归锁。"""
    rec = [r for r in fuzz_sample
           if r["tool"] == "cantenna_design" and r["case"] == "numeric_nan"]
    assert rec, "抽样里缺少 cantenna_design 的 NaN 频率用例"
    assert rec[0]["class"] == "OK"
    assert "param_out_of_range" in rec[0]["detail"]


def test_long_text_roundtrip_is_bounded():
    """P13 挂死收口：10 万字符文本必须秒回结构化错误，不许跑 DSP 挂死。

    实测（见 docs/code-guards.md）：feldhell 1 万字符约 24s、2 万约 65s，
    psk31/rtty 10 万字符均超 60s。上限 1 万字符后，超限路径不做调制解调。
    """
    import time

    from mbdsdr_ai.fldigi_modes import register_fldigi_modes_tools

    reg = ToolRegistry()
    register_fldigi_modes_tools(reg)
    for name in ("feldhell_decode", "psk31_decode", "rtty_decode"):
        started = time.perf_counter()
        res = reg.call(name, {"text": "A" * 100_000})
        elapsed = time.perf_counter() - started
        assert res.success is False, name
        assert res.error == "param_out_of_range", f"{name}: {res.error!r}"
        assert elapsed < 5.0, f"{name} 仍可能挂死：{elapsed:.2f}s"
