"""
mbdsdr_ai/feature_manager.py — 特性插件管理器
=============================================

对照 SDRangel ``sdrbase/feature/feature.cpp`` + ``plugins/feature/``：
  - Feature 是"面板级"插件（卫星跟踪 / 地图 / 演示 / AFC / Morse 解码…），
    不直接出 IQ，而是消费信道数据并产出可视化/控制结果。
  - ``FeatureSet.addFeature`` / ``removeFeatureInstance`` 生命周期管理。

本模块复用 :mod:`mbdsdr_ai.plugin_manager` 的 :class:`Plugin` 基类，
新增 Feature 子类（带 ``feature_type`` 标签），提供注册/启动/停止/枚举。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .plugin_manager import Plugin

__all__ = ["Feature", "FeatureManager", "FeatureState"]


class FeatureState:
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class Feature(Plugin):
    """特性插件基类（对照 SDRangel Feature）。"""

    feature_type: str = "generic"
    #: 该特性消费的输入标签（如 "satellite", "map", "audio"）
    consumes: List[str] = []

    def __init__(self, name: str = ""):
        if name:
            self.name = name
        self._state = FeatureState.IDLE

    @property
    def state(self) -> str:
        return self._state

    def start(self) -> None:
        self._state = FeatureState.RUNNING

    def stop(self) -> None:
        self._state = FeatureState.IDLE

    def process(self, data: Any) -> Any:
        """处理一帧信道数据/事件。子类覆写。"""
        return None

    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d["feature_type"] = self.feature_type
        d["state"] = self._state
        d["consumes"] = list(self.consumes)
        return d


@dataclass
class _Entry:
    instance: Feature
    started: bool = False


class FeatureManager:
    """注册 / 启动 / 停止特性插件。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._features: Dict[str, _Entry] = {}

    def register(self, feature: Feature) -> None:
        with self._lock:
            self._features[feature.name] = _Entry(instance=feature)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._features.pop(name, None)

    def start(self, name: str) -> bool:
        with self._lock:
            entry = self._features.get(name)
            if entry is None:
                return False
            try:
                entry.instance.start()
                entry.started = True
                return True
            except Exception as e:  # noqa: BLE001
                entry.instance._state = FeatureState.ERROR
                entry.instance._error = str(e)
                return False

    def stop(self, name: str) -> None:
        with self._lock:
            entry = self._features.get(name)
            if entry and entry.started:
                entry.instance.stop()
                entry.started = False

    def start_all(self) -> None:
        with self._lock:
            for name in list(self._features):
                self.start(name)

    def stop_all(self) -> None:
        with self._lock:
            for name in list(self._features):
                self.stop(name)

    def list_features(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.instance.describe() for e in self._features.values()]

    def get(self, name: str) -> Optional[Feature]:
        with self._lock:
            entry = self._features.get(name)
            return entry.instance if entry else None

    def broadcast(self, data: Any) -> None:
        """向所有运行中的特性推送一帧数据。"""
        with self._lock:
            for entry in self._features.values():
                if entry.started:
                    try:
                        entry.instance.process(data)
                    except Exception:
                        entry.instance._state = FeatureState.ERROR
