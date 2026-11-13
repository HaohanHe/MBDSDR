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

活跃态由本模块的 ``claim_active`` 统一判定（读占位文件心跳 + TTL）；闸与
按需审计都调它，不再各写一份。本模块原先只管「释放之后是否还在窗口内」，
2026-11-13 把活跃判定也收进来，原因是审计一度自写 TTL 比较，与闸的边界
（``==`` 算不算过期）相反——同一时刻会给出「有主 / 无主」两个答案。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

#: 释放窗口（秒）：同一 owner 在窗口内释放过的文件，仍要求 §6.1 行。
#: 「先 release 再提交」不应把行要求一起释放掉。
RELEASE_WINDOW_S = 3600.0


def claim_active(payload: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
    """占位文件判活的**唯一**口径：``heartbeat_at + ttl_min`` 未过期。

    这条判据同时被闸（``check_claim_coordination.active_claims``）和按需
    审计（``audit_unclaimed_changes``）使用。两边各写一份的后果不是数字
    略有出入，而是「闸说有主、审计说无主」——同一时刻两条真值源，谁都能
    引一条来支持自己的结论。所以只在这里实现，两侧都 import 本函数。

    判据只看心跳（``heartbeat_at`` 缺失时取 ``acquired_at``）；``pid`` 不参与
    （claim 是一次性 CLI，pid 默认就是死的，见 ``file_claim`` 模块文档）。
    边界取开区间左端：``heartbeat + ttl == now`` 视为**已过期**。
    """
    if not isinstance(payload, dict):
        return False
    now = time.time() if now is None else now
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or 0.0)
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    return heartbeat + ttl_s > now


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
