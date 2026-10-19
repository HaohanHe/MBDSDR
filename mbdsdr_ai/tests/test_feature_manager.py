# SPDX-License-Identifier: MIT
"""
feature_manager.py 特性插件管理器——确定性离线测试

不联网、无硬件；用本地 Feature 子类：
  - register/unregister/get 生命周期；重名注册覆盖；
  - start 未注册名返回 False（不炸）；
  - start/stop 状态机 idle→running→idle；stop 只对已启动的生效；
  - start 抛出异常 → 返回 False 且状态置 error（错误不吞）；
  - start_all/stop_all；
  - broadcast 只推给运行中的特性；process 抛异常 → 该特性置 error，
    不波及其他特性；
  - describe 含 feature_type/state/consumes。

运行：python3 -m pytest mbdsdr_ai/tests/test_feature_manager.py -v
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.feature_manager import (  # noqa: E402
    Feature,
    FeatureManager,
    FeatureState,
)


class DummyFeature(Feature):
    feature_type = "dummy"
    consumes = ["audio"]

    def __init__(self, name: str = "dummy"):
        super().__init__(name)
        self.received: List[Any] = []

    def process(self, data: Any) -> Any:
        self.received.append(data)
        return data


class BoomFeature(Feature):
    feature_type = "boom"

    def start(self) -> None:
        raise RuntimeError("start exploded")

    def process(self, data: Any) -> Any:
        raise RuntimeError("process exploded")


# ---------------------------------------------------------------- 注册表

def test_register_get_unregister():
    mgr = FeatureManager()
    f = DummyFeature("sat-track")
    mgr.register(f)
    assert mgr.get("sat-track") is f
    assert [d["name"] for d in mgr.list_features()] == ["sat-track"]
    mgr.unregister("sat-track")
    assert mgr.get("sat-track") is None
    mgr.unregister("sat-track")                        # 幂等删除不炸


def test_register_same_name_replaces():
    mgr = FeatureManager()
    mgr.register(DummyFeature("x"))
    f2 = DummyFeature("x")
    mgr.register(f2)
    assert mgr.get("x") is f2
    assert len(mgr.list_features()) == 1


# ---------------------------------------------------------------- 启停状态机

def test_start_unknown_returns_false():
    mgr = FeatureManager()
    assert mgr.start("nope") is False


def test_start_stop_state_transitions():
    mgr = FeatureManager()
    f = DummyFeature("s")
    mgr.register(f)
    assert f.state == FeatureState.IDLE
    assert mgr.start("s") is True
    assert f.state == FeatureState.RUNNING
    mgr.stop("s")
    assert f.state == FeatureState.IDLE


def test_stop_only_when_started():
    """未启动就 stop：不调 instance.stop（幂等由 started 标志保证）。"""
    mgr = FeatureManager()

    class Counting(DummyFeature):
        def __init__(self):
            super().__init__("c")
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1
            super().stop()

    f = Counting()
    mgr.register(f)
    mgr.stop("c")                                      # 未启动：不应调 stop
    assert f.stops == 0
    mgr.start("c")
    mgr.stop("c")
    assert f.stops == 1


def test_start_exception_sets_error_state():
    mgr = FeatureManager()
    mgr.register(BoomFeature("b"))
    assert mgr.start("b") is False                     # 异常不吞，显式 False
    f = mgr.get("b")
    assert f.state == FeatureState.ERROR
    assert "exploded" in f._error


def test_start_all_stop_all():
    mgr = FeatureManager()
    for name in ("a", "b", "c"):
        mgr.register(DummyFeature(name))
    mgr.start_all()
    assert all(d["state"] == FeatureState.RUNNING
               for d in mgr.list_features())
    mgr.stop_all()
    assert all(d["state"] == FeatureState.IDLE
               for d in mgr.list_features())


# ---------------------------------------------------------------- broadcast

def test_broadcast_only_to_running():
    mgr = FeatureManager()
    f1 = DummyFeature("f1")
    f2 = DummyFeature("f2")
    mgr.register(f1)
    mgr.register(f2)
    mgr.start("f1")                                    # 只启动 f1
    mgr.broadcast({"frame": 1})
    assert f1.received == [{"frame": 1}]
    assert f2.received == []


def test_broadcast_process_exception_isolated():
    """一个特性 process 炸了只置自己 error，不波及其他。"""
    mgr = FeatureManager()
    ok = DummyFeature("ok")
    boom = BoomFeature("boom")
    mgr.register(ok)
    mgr.register(boom)
    mgr.start("ok")
    mgr.start("boom")
    mgr.broadcast({"frame": 2})                        # 不抛
    assert ok.state == FeatureState.RUNNING
    assert ok.received == [{"frame": 2}]
    assert boom.state == FeatureState.ERROR


# ---------------------------------------------------------------- describe

def test_describe_includes_feature_fields():
    f = DummyFeature("d")
    d: Dict[str, Any] = f.describe()
    assert d["name"] == "d"
    assert d["feature_type"] == "dummy"
    assert d["consumes"] == ["audio"]
    assert d["state"] == FeatureState.IDLE
