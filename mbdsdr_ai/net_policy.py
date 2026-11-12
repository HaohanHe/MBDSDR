# SPDX-License-Identifier: MIT
"""网络策略（叶子模块）。

「是否禁网」原本是 ``orbit`` 里的私有函数 ``_network_disabled``，却被
agent、model_manager、new_spacetime、openapi_integration 等模块跨模块引用——
私有名被外部依赖，说明它其实是公共原语、放错了地方。本模块不 import 任何
``mbdsdr_ai`` 模块；``orbit`` 原处保留 ``_network_disabled`` 别名，既有调用方
不变。
"""
import os

#: 禁网开关的环境变量名（测试/CI 置 1，全量套件天然离线）。
OFFLINE_ENV = "MBDSDR_OFFLINE"


def network_disabled() -> bool:
    """是否禁网（MBDSDR_OFFLINE=1/true/yes/on）。"""
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in ("1", "true", "yes", "on")
