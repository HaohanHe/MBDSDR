# SPDX-License-Identifier: MIT
"""mode_registry 的运行时状态唯一持有者
=====================================

`desktop/mode_registry.py` 会被两种上下文各执行一遍：桌面面板的裸导入
（``import mode_registry``）与测试/工具的包式导入
（``import desktop.mode_registry``）。两份模块实例各有自己的模块级全局，
运行时可注册的注册表若留在模块里就会分裂成两份：一侧 ``register()`` 的新模式，
另一侧永远读不到，且不报错（docs/redundancy-register.md §5.10）。

本模块只按裸名导入，是唯一的状态持有者：

  * 两种 ``mode_registry`` 实例执行 ``import mode_registry_store`` 时都命中
    同一个 sys.modules 条目（``desktop/__init__.py`` 已把 desktop/ 垫进
    sys.path），因此共享同一份 ``_runtime``；
  * ``mode_registry`` 对外只暴露 register/get/list_modes 等函数，不导出字典。

禁止以 ``import desktop.mode_registry_store`` 的包名导入本模块——那会再复制
一份状态，等于把同一个缺陷往下挪一层。
"""
from __future__ import annotations

from typing import Any, Dict, List

_runtime: Dict[str, Any] = {}


def init_defaults(defaults: Dict[str, Any]) -> None:
    """灌入内置模式；已存在的键不覆盖（幂等，两份模块实例各调一次）。"""
    for desc in defaults.values():
        _runtime.setdefault(desc.mode.upper(), desc)


def put(desc: Any) -> None:
    """注册/覆盖一个模式描述符。"""
    _runtime[desc.mode.upper()] = desc


def discard(mode: str) -> None:
    """移除一个模式（测试清理用）。"""
    _runtime.pop((mode or "").upper(), None)


def get(mode: str) -> Any:
    """按模式名取描述符，不存在返回 None。"""
    return _runtime.get((mode or "").upper())


def values() -> List[Any]:
    """按注册顺序返回全部描述符的副本。"""
    return list(_runtime.values())
