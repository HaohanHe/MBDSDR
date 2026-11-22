# SPDX-License-Identifier: MIT
"""把恒星与行星/月球识别结果合并成一份 HUD 目标序列。

``identify_direction``（恒星）与 ``identify_bodies``（行星/月球）各自按
视场识别、各自排序；桌面 AR 指向模式需要的是**一份**按亮度排好、同一
目标不重复的目标表。本模块只做纯数据合并：不查星表、不读时钟、不
import Qt，也不给任何目标补默认星等或角距——缺什么就如实缺着。

去重与排序口径
--------------
* 去重键 ``(kind 或 "star", name)``：同名但 kind 不同（如恒星与月球都
  叫某个名字）保留两条；
* 同键保留更亮（星等更小）的一条；星等相同保留角距更小的一条；
  星等/角距都缺时保留先出现的一条（稳定，不挑"看起来更好"的）；
* 排序：有星等的按升序（亮→暗），缺星等排最后；同星等按角距升序；
  仍相同按 ``name`` 稳定。
"""

from __future__ import annotations

import math
import operator
from collections.abc import Iterable, Mapping
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["merge_targets"]

#: 星等字段：恒星目录用 ``vmag``，行星/月球用 ``magnitude``。
_MAG_KEYS = ("vmag", "magnitude", "mag")

#: 角距字段：两个识别模块的输出同名。
_DIST_KEYS = ("angular_distance_deg",)


def _finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值 / NaN / Inf 返回 None（不补默认值）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _first_finite(target: Mapping[str, Any],
                  keys: Tuple[str, ...]) -> Optional[float]:
    for key in keys:
        value = _finite(target.get(key))
        if value is not None:
            return value
    return None


def _target_name(target: Mapping[str, Any]) -> str:
    value = target.get("name")
    return value.strip() if isinstance(value, str) else ""


def _target_key(target: Mapping[str, Any],
                name: str) -> Tuple[str, str]:
    """去重键：kind 缺失/空白一律按恒星处理，与识别模块口径一致。"""
    raw_kind = target.get("kind")
    kind = str(raw_kind).strip() if raw_kind is not None else ""
    return (kind or "star", name)


def _rank(target: Mapping[str, Any]) -> Tuple[bool, float, bool, float]:
    """「更亮/更近」的比较键；缺值排后，绝不参与排序时冒充 0 等。"""
    mag = _first_finite(target, _MAG_KEYS)
    dist = _first_finite(target, _DIST_KEYS)
    return (mag is None, mag if mag is not None else 0.0,
            dist is None, dist if dist is not None else 0.0)


def merge_targets(*groups: Any, limit: int = 6) -> List[Dict[str, Any]]:
    """合并若干目标序列，去重、按亮度排序并截断。

    Args:
        *groups: 若干目标序列（``identify_direction`` 的 ``stars`` /
            ``identify_bodies`` 的 ``bodies`` 之类的 dict 列表）。非
            ``Mapping`` 项与 ``name`` 为空/空白的项直接跳过；不是可迭代
            容器的组也跳过（不把字符串当目标序列）。
        limit: 最多返回几条，必须是正整数。

    Returns:
        新列表（每项是入参目标的浅拷贝），按「有星等 → 星等升序 → 角距
        升序 → name」排序；不修改入参，也不补任何缺失字段。

    Raises:
        ValueError: ``limit`` 不是正整数（含 bool / 浮点 / 字符串）。
    """
    if isinstance(limit, bool):
        raise ValueError(f"limit 必须是正整数，收到 {limit!r}")
    try:
        limit_n = operator.index(limit)
    except TypeError:
        raise ValueError(f"limit 必须是正整数，收到 {limit!r}") from None
    if limit_n <= 0:
        raise ValueError(f"limit 必须是正整数，收到 {limit!r}")

    selected: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    order: List[Tuple[str, str]] = []
    for group in groups:
        if isinstance(group, (str, bytes, Mapping)) \
                or not isinstance(group, Iterable):
            continue
        for target in group:
            if not isinstance(target, Mapping):
                continue
            name = _target_name(target)
            if not name:
                continue
            key = _target_key(target, name)
            existing = selected.get(key)
            if existing is None:
                selected[key] = target
                order.append(key)
                continue
            # 同键只留更亮（星等更小）的一条；星等相同再比角距；都缺
            # 时 _rank 相等，保留先出现的那条（不替换）。
            if _rank(target) < _rank(existing):
                selected[key] = target

    merged = [dict(selected[key]) for key in order]
    merged.sort(key=lambda t: (*_rank(t), _target_name(t)))
    return merged[:limit_n]
