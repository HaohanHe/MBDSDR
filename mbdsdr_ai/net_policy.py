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

#: 显式允许联网刷新的环境变量名（默认关闭：离线优先，不静默联网）。
ALLOW_NETWORK_ENV = "MBDSDR_ALLOW_NETWORK"


def network_disabled() -> bool:
    """是否禁网（MBDSDR_OFFLINE=1/true/yes/on）。"""
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def network_allowed() -> bool:
    """是否**显式**允许联网（MBDSDR_ALLOW_NETWORK=1/true/yes/on）。

    默认 False（离线优先）：未显式开启时，按名称解析 TLE 等路径只读本地
    缓存，无缓存即诚实报「离线且无缓存 TLE」，不发起网络、不等超时。
    ``MBDSDR_OFFLINE`` 优先级更高，置位后本开关不生效。
    """
    if network_disabled():
        return False
    return (os.environ.get(ALLOW_NETWORK_ENV, "").strip().lower()
            in ("1", "true", "yes", "on"))
