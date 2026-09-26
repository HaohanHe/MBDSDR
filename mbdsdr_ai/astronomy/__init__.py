"""
mbdsdr_ai/astronomy/ — 天文子包
================================

历史上 ``mbdsdr_ai/astronomy.py`` 是一个单文件模块。本任务按上游补齐行星历表、
DSO 目录、大气折射三个新模块，物理上放在 ``astronomy/`` 目录下。

Python 中同名 package 会 shadow 同名 ``.py``，因此本 ``__init__.py``：

1. 用 importlib 按文件路径把旧的 ``../astronomy.py`` 重新加载为
   ``mbdsdr_ai._astronomy_legacy``，并把所有公共名字 re-export 到本包命名空间，
   保证 ``from mbdsdr_ai.astronomy import Observer, unix_to_jd, ...`` 不破坏
   （``mbdsdr_ai/__init__.py:69``、``agent.py:53``、既有测试都依赖这条路径）。
2. 暴露新子模块：:mod:`planets`、:mod:`dso`、:mod:`refraction`。

**不修改旧 astronomy.py 一行代码**，仅做桥接。
"""

from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys as _sys

_pkg_dir = _os.path.dirname(__file__)
_legacy_path = _os.path.normpath(_os.path.join(_pkg_dir, "..", "astronomy.py"))
_legacy_modname = "mbdsdr_ai._astronomy_legacy"

if _os.path.isfile(_legacy_path) and _legacy_modname not in _sys.modules:
    _spec = _ilu.spec_from_file_location(_legacy_modname, _legacy_path)
    if _spec is not None and _spec.loader is not None:
        _mod = _ilu.module_from_spec(_spec)
        _mod.__package__ = "mbdsdr_ai"
        _sys.modules[_legacy_modname] = _mod
        _spec.loader.exec_module(_mod)
        for _k, _v in vars(_mod).items():
            if not _k.startswith("_"):
                globals()[_k] = _v

from . import refraction  # noqa: E402,F401
from . import planets  # noqa: E402,F401
from . import dso  # noqa: E402,F401

__all__ = ["refraction", "planets", "dso"]
