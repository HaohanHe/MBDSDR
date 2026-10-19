# SPDX-License-Identifier: MIT
"""
工具描述质量门禁（模型体验轴，DSH 四维标准）

设计来源：deepseek-harness 工具描述四维标准——每个工具必须让模型知道
①前置条件（调用前要满足什么）②失败恢复（失败了该做什么/告诉用户什么）
③副作用（会改变什么外部状态）④能力边界（不能做什么/什么算空态）。
模型对工具的全部认知来自 description + parameters，描述缺维 = 模型盲飞。

本文件钉死（防回退 + 棘轮）：

  - 全工具面地板：无空描述、无占位符（TODO/待补充/xxx）、最短描述阈值；
  - 参数 schema 形状合法（type=object + properties 为 dict）；
  - 核心高频工具（设备连接/设置/VFO/录制/数字解码）逐一定制四维要求：
    状态变更型必须四维齐全，纯查询型至少两维；
  - 全工具面四维覆盖率棘轮阈值（每期只升不降，见下方 RATCHET 注释）；
  - 关键诚实契约逐条钉死（如 sdr_list_devices "不要虚构"）。

运行：python3 -m pytest mbdsdr_ai/tests/test_tool_description_quality.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.sdr_backend import SDRBackendManager  # noqa: E402
from mbdsdr_ai.sdr_tools import register_sdr_tools  # noqa: E402
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------- 装配

MIN_DESC_CHARS = 30  # 全工具面描述最短阈值（字符）


def _register_agent():
    """真实注册表 + 无硬件后端管理器（无设备 = 诚实空态，不 mock 硬件）。"""
    class _Agent:
        pass

    ag = _Agent()
    ag.tool_registry = ToolRegistry()
    ag.sdr_manager = SDRBackendManager()
    register_sdr_tools(ag)
    return ag


@pytest.fixture(scope="module")
def agent():
    return _register_agent()


def _descs(agent):
    return {n: t["definition"]["function"].get("description", "") or ""
            for n, t in agent.tool_registry.tools.items()}


# ---------------------------------------------------------------- 四维关键词
# 措辞宽松匹配：写描述时不要求逐字命中，但必须落入这些语义族之一。

_PRE = ["前置条件", "先调用", "需先", "必须先", "连接前", "第一步", "先决",
        "before", "输入契约", "仅对"]
_REC = ["失败恢复", "重试", "若返回", "报错", "恢复", "幂等", "不要反复",
        "不要虚构", "修正后"]
_SIDE = ["副作用", "会关闭", "会修改", "落盘", "写盘", "持续写", "同时停止",
         "会生成", "会清空", "会改变", "会临时改变", "实际发射"]
_BOUND = ["不要虚构", "能力边界", "不支持", "仅支持", "超出", "如实", "空态",
          "不会伪造", "无法", "不会报错", "不会发声"]


def _has(desc: str, keywords) -> bool:
    return any(k in desc for k in keywords)


# ---------------------------------------------------------------- 地板测试

def test_no_empty_or_trivial_description(agent):
    descs = _descs(agent)
    assert descs, "注册表为空——装配失败"
    bad = {n: len(d) for n, d in descs.items() if len(d.strip()) < MIN_DESC_CHARS}
    assert not bad, f"描述不足 {MIN_DESC_CHARS} 字的工具: {bad}"


def test_no_placeholder_descriptions(agent):
    descs = _descs(agent)
    placeholders = ("TODO", "待补充", "待完善", "xxx", "XXX", "占位", "略")
    bad = {n: d[:60] for n, d in descs.items()
           if any(p in d for p in placeholders)}
    assert not bad, f"描述含占位符的工具: {bad}"


def test_every_tool_schema_shape(agent):
    """parameters 必须是合法 JSON Schema 对象形状——模型侧解析的前提。"""
    bad = []
    for name, t in agent.tool_registry.tools.items():
        params = t["definition"]["function"].get("parameters", {})
        if not isinstance(params, dict):
            bad.append((name, "parameters 非 dict"))
            continue
        if params.get("type") != "object":
            bad.append((name, f"type={params.get('type')!r}"))
        props = params.get("properties")
        if props is not None and not isinstance(props, dict):
            bad.append((name, "properties 非 dict"))
    assert not bad, f"schema 形状非法的工具: {bad}"


# ---------------------------------------------------------------- 核心工具定制要求
# 状态变更型：四维齐全（改硬件/改持久化状态/有磁盘或射频后果）。
_STATE_MUTATORS = [
    "sdr_connect", "sdr_disconnect", "sdr_switch_device",
    "sdr_set_frequency", "sdr_set_sample_rate", "sdr_set_gain", "sdr_set_demod",
    "sdr_set_bandwidth", "sdr_set_agc", "sdr_set_squelch",
    "sdr_record_start", "sdr_record_stop",
    "vfo_add", "vfo_remove", "vfo_select",
    "radio_set_mode", "radio_set_frequency", "radio_ptt",
]


@pytest.mark.parametrize("name", _STATE_MUTATORS)
def test_state_mutators_have_all_four_dimensions(agent, name):
    descs = _descs(agent)
    assert name in descs, f"核心工具 {name} 未注册（被误删？）"
    d = descs[name]
    missing = []
    if not _has(d, _PRE):
        missing.append("前置条件")
    if not _has(d, _REC):
        missing.append("失败恢复")
    if not _has(d, _SIDE):
        missing.append("副作用")
    if not _has(d, _BOUND):
        missing.append("能力边界")
    assert not missing, f"{name} 描述缺维度: {missing}（当前: {d[:120]}…）"


# 查询/枚举型：至少两维（前置条件 + 恢复或边界之一），不得凭空态承诺。
_QUERIES = [
    "sdr_list_devices", "sdr_status", "sdr_get_frequency",
    "vfo_list", "sdr_spectrum_analyze", "sdr_recordings_list",
    "pocsag_decode", "pocsag_bch_correct", "dtmf_decode", "zvei_decode",
    "afsk1200_demod", "morse_decode",
]


@pytest.mark.parametrize("name", _QUERIES)
def test_query_tools_have_at_least_two_dimensions(agent, name):
    descs = _descs(agent)
    assert name in descs, f"核心工具 {name} 未注册（被误删？）"
    d = descs[name]
    dims = sum(_has(d, kws) for kws in (_PRE, _REC, _SIDE, _BOUND))
    assert dims >= 2, f"{name} 仅 {dims} 维（需≥2）（当前: {d[:120]}…）"


# ---------------------------------------------------------------- 关键诚实契约
# 逐条钉死：这些措辞是"不虚构"承诺的落点，删改即回归。

def test_honesty_contracts_pinned(agent):
    descs = _descs(agent)
    pins = {
        # 设备枚举空态必须如实——模型最常见的虚构点
        "sdr_list_devices": ["不要虚构", "空态"],
        # 连接失败不得伪造
        "sdr_connect": ["不会伪造", "如实"],
        # 录制磁盘副作用必须显式
        "sdr_record_start": ["写盘", "落盘", "持续写"],
        # PTT 真实发射警告
        "radio_ptt": ["实际发射", "副作用"],
        # IQ 回放采样率必填的失败恢复
        "sdr_open_iq_file": ["sample_rate_hz", "失败恢复"],
    }
    missing = {}
    for name, kws in pins.items():
        d = descs.get(name, "")
        hit = [k for k in kws if k in d]
        if not hit:
            missing[name] = kws
    assert not missing, f"诚实契约描述被削弱: {missing}"


# ---------------------------------------------------------------- 全工具面棘轮
# RATCHET 策略：阈值取当前实测值的约 80%（留波动余量），每期只升不降。
# phase67 实测（147 工具）：前置条件 18.4% / 失败恢复 21.1% /
# 副作用 8.2% / 能力边界 10.2% / 任一维度 29.3%。
# 下一期目标：任一维度 ≥ 45%，届时上调下方阈值。

def test_surface_ratchet(agent):
    descs = _descs(agent)
    tot = len(descs)
    pre = sum(_has(d, _PRE) for d in descs.values())
    rec = sum(_has(d, _REC) for d in descs.values())
    side = sum(_has(d, _SIDE) for d in descs.values())
    bound = sum(_has(d, _BOUND) for d in descs.values())
    anyd = sum(any(_has(d, kws) for kws in (_PRE, _REC, _SIDE, _BOUND))
               for d in descs.values())
    pct = lambda x: 100.0 * x / tot  # noqa: E731
    assert pct(anyd) >= 25.0, f"任一维度覆盖率 {pct(anyd):.1f}% < 25%"
    assert pct(pre) >= 15.0, f"前置条件覆盖率 {pct(pre):.1f}% < 15%"
    assert pct(rec) >= 18.0, f"失败恢复覆盖率 {pct(rec):.1f}% < 18%"
    assert pct(side) >= 6.0, f"副作用覆盖率 {pct(side):.1f}% < 6%"
    assert pct(bound) >= 8.0, f"能力边界覆盖率 {pct(bound):.1f}% < 8%"
