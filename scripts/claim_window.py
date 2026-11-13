#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""「仍需 §6.1 行」的唯一判据：活跃 claim 之外的释放窗口。

为什么单独成模块：这条判据同时被两侧使用——

* 闸：``scripts/check_claim_coordination.py --staged``（pre-commit ④ 用它决定
  要不要拒绝提交）；
* 工具：``scripts/append_coordination_row.py``（补登记时用它决定这条路径
  还该不该接受一行）。

两侧若各写一份「60 分钟内本人释放也算数」，只要有一侧先改，就会出现
「闸要求补行、工具拒收」的死锁：提交被拦，照着错误信息粘贴的命令补不上，
人只能 ``--no-verify`` 绕过。所以窗口判定只在 ``window_release`` 一处实现，
两侧都调它，反证用例必须让两侧同时翻转（55 分钟 → 都要求；61 分钟 → 都不要求）。

活跃态由各自的权威来源提供（闸读占位文件心跳，工具读台账 ``current``），
本模块不重复实现那一层；这里只回答「释放之后是否还在窗口内」。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

#: 释放窗口（秒）：同一 owner 在窗口内释放过的文件，仍要求 §6.1 行。
#: 「先 release 再提交」不应把行要求一起释放掉。
RELEASE_WINDOW_S = 3600.0


def window_release(
    entry: Optional[Dict[str, Any]],
    owner: str = "",
    now: Optional[float] = None,
    window_s: float = RELEASE_WINDOW_S,
) -> Optional[Tuple[str, float]]:
    """条目里最近一次释放若在窗口内，返回 ``(释放者 owner, released_at)``。

    * ``entry`` 是台账 ``claims[<规范化路径>]`` 那一条；缺条目/空条目 → ``None``；
    * ``owner`` 为空（提交身份未声明）时按**任一 owner**匹配（fail closed：
      否则「release 完不声明身份」就成了绕过窗口的开关）；
    * 只看 ``history[].released_at``；仍在持有的记录没有 ``released_at``，
      由调用方的活跃分支负责。
    """
    if not isinstance(entry, dict):
        return None
    now = time.time() if now is None else now
    latest: Optional[Tuple[str, float]] = None
    for item in entry.get("history") or []:
        if not isinstance(item, dict):
            continue
        released = item.get("released_at")
        if not isinstance(released, (int, float)):
            continue  # 仍在持有：不是释放记录
        held = str(item.get("owner") or "")
        if owner and held != owner:
            continue
        if latest is None or float(released) > latest[1]:
            latest = (held, float(released))
    if latest is None or now - latest[1] > window_s:
        return None
    return latest
