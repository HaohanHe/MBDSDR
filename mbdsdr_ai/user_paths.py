# SPDX-License-Identifier: MIT
"""用户目录的单一来源（叶子模块）。

「缓存根 = ``MBDSDR_CACHE_DIR`` 优先，否则 ``~/.mbdsdr/cache``」这条规则
曾在 ``amr`` 与 ``sat_links_fetch`` 里各写一遍。两处字面相同，但同一台
机器上只要有一处的环境变量优先级或目录名改掉，两个模块就会读写不同位置，
且都不会报错——属于同一事实的多个真值源。

本模块只做路径推导，不 import 任何 ``mbdsdr_ai`` 模块，任意层都可引用；
需要「拿不到用户目录就如实返回 None」的调用方直接判空，不要自己兜底拼路径。
"""
from __future__ import annotations

import os
from typing import Optional

#: 把缓存隔离到临时目录的官方环境变量（测试/嵌入方使用）。
CACHE_DIR_ENV = "MBDSDR_CACHE_DIR"

#: 用户数据根目录名（位于用户主目录下）。
DATA_DIR_NAME = ".mbdsdr"

#: 缓存子目录名（位于用户数据根目录下）。
CACHE_DIR_NAME = "cache"

__all__ = [
    "CACHE_DIR_ENV",
    "CACHE_DIR_NAME",
    "DATA_DIR_NAME",
    "user_home",
    "user_cache_dir",
]


def user_home() -> Optional[str]:
    """用户主目录；``expanduser`` 取不到时返回 None（不把 ``"~"`` 当路径用）。"""
    home = os.path.expanduser("~")
    return home if home and home != "~" else None


def user_cache_dir() -> Optional[str]:
    """缓存根目录：``MBDSDR_CACHE_DIR`` 优先，否则 ``~/.mbdsdr/cache``。

    取不到用户目录且未显式指定环境变量时返回 None，由调用方决定是否放弃落盘。
    """
    base = os.environ.get(CACHE_DIR_ENV)
    if base:
        return base
    home = user_home()
    if home is None:
        return None
    return os.path.join(home, DATA_DIR_NAME, CACHE_DIR_NAME)
