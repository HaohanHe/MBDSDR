# SPDX-License-Identifier: MIT
"""
module_system 单测：注册 / 连接 / 数据流 / AI 推荐链
=====================================================
对应 docs/learn/sdrpp_modules.md 第 1 节。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from mbdsdr_ai.module_system import (
    Module, ModuleType, ModuleRegistry, SignalGraph, Port,
)


# -- 一组测试用模块 ----------------------------------------------------
class FakeSource(Module):
    MODULE_NAME = "fake_source"
    MODULE_TYPE = ModuleType.SOURCE
    MODULE_DESC = "测试用 source"
    MAX_INSTANCES = 1

    def process(self, inputs):
        # source：外部 block 由 run_one_block 注入，这里返回空即可
        return {}


class GainBlock(Module):
    MODULE_NAME = "gain"
    MODULE_TYPE = ModuleType.TOOL
    MODULE_DESC = "增益块"
    MAX_INSTANCES = 0  # 不限

    def process(self, inputs):
        x = inputs["in"]
        g = float(self.config.get("gain", 2.0))
        return {"out": x * g}


class CollectSink(Module):
    MODULE_NAME = "collect_sink"
    MODULE_TYPE = ModuleType.SINK
    MODULE_DESC = "收集 sink"
    MAX_INSTANCES = 0

    def process(self, inputs):
        self.last = inputs["in"]
        return {}


# -- 注册 --------------------------------------------------------------
def test_register_and_list():
    reg = ModuleRegistry()
    reg.register(FakeSource)
    reg.register(GainBlock)
    reg.register(CollectSink)

    types = {m["name"]: m["type"] for m in reg.list()}
    assert types["fake_source"] == ModuleType.SOURCE
    assert types["gain"] == ModuleType.TOOL
    assert types["collect_sink"] == ModuleType.SINK

    # 按类型过滤
    sinks = reg.list(type=ModuleType.SINK)
    assert [s["name"] for s in sinks] == ["collect_sink"]


def test_duplicate_registration_rejected():
    reg = ModuleRegistry()
    reg.register(FakeSource)
    with pytest.raises(ValueError):
        reg.register(FakeSource)


def test_create_instance_and_max_instances():
    reg = ModuleRegistry()
    reg.register(FakeSource)   # MAX_INSTANCES=1
    reg.register(GainBlock)    # 不限
    reg.create("s1", "fake_source")
    with pytest.raises(ValueError):
        reg.create("s2", "fake_source")  # 超上限
    reg.create("g1", "gain")
    reg.create("g2", "gain")  # 不限，可以


def test_nonexistent_module():
    reg = ModuleRegistry()
    with pytest.raises(KeyError):
        reg.create("x", "no_such_module")


# -- 图连接与数据流 ----------------------------------------------------
def test_graph_connect_and_dataflow():
    reg = ModuleRegistry()
    reg.register(FakeSource)
    reg.register(GainBlock)
    reg.register(CollectSink)

    src = reg.create("src", "fake_source")
    g = reg.create("g", "gain", {"gain": 3.0})
    sink = reg.create("sink", "collect_sink")

    graph = SignalGraph(reg)
    graph.connect("src", "out", "g", "in")
    graph.connect("g", "out", "sink", "in")

    graph.start()
    out = graph.run_one_block("src", np.ones(16, dtype=np.complex64))
    graph.stop()

    assert hasattr(sink, "last")
    assert sink.last.dtype == np.complex64
    np.testing.assert_allclose(sink.last, np.full(16, 3.0, dtype=np.complex64))


def test_connect_wrong_port_rejected():
    reg = ModuleRegistry()
    reg.register(FakeSource)
    reg.register(CollectSink)
    reg.create("src", "fake_source")
    reg.create("sink", "collect_sink")
    graph = SignalGraph(reg)
    with pytest.raises(ValueError):
        graph.connect("src", "nope", "sink", "in")
    with pytest.raises(ValueError):
        graph.connect("src", "out", "sink", "nope")


def test_dynamic_load():
    reg = ModuleRegistry()
    # 动态加载并注册一个具体子类
    cls = reg.dynamic_load("tests.test_module_system:GainBlock")
    assert cls.MODULE_NAME == "gain"
    assert reg.get("gain").MODULE_NAME == "gain"


# -- AI 推荐链（增强） -------------------------------------------------
def test_recommend_chain():
    reg = ModuleRegistry()
    # 不注册具体模块也能跑规则
    chain = reg.recommend_chain({"mode_hint": "WFM", "bandwidth_hz": 200e3})
    assert "wfm_demod" in chain
    assert chain[-1] == "audio_out"

    chain = reg.recommend_chain({"bandwidth_hz": 12.5e3})
    assert "nfm_demod" in chain

    chain = reg.recommend_chain({"mode_hint": "CW", "bandwidth_hz": 100})
    assert "cw_demod" in chain

    chain = reg.recommend_chain({"bandwidth_hz": 0})
    assert isinstance(chain, list)
