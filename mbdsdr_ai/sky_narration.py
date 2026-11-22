# SPDX-License-Identifier: MIT
"""把 AR 指向状态译成不出现工程术语的口语文本（纯逻辑，无 Qt）。

界面默认不说 ``az`` / ``alt`` / ``vmag`` / ``GMST`` / ``FOV``：方位说「西南」，
高度说「离地平线不太高」，星等只说「很亮/明亮」。本模块只做这一层翻译，
不读时钟、不碰设备、不写盘，输入输出都是调用方手里的既有 dict。

诚实边界
--------
* 位姿不可用（非 Mapping、``valid`` 不为 True、方位/高度非有限）时只给
  「还没对准」，绝不拿别的数值拼一个朝向；
* 目标缺 ``name`` 就跳过；``vmag`` 缺失只省略亮度词，不猜星等；
* 没有可用目标时不追加「附近能看到」，也不编造星名。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, List, Optional, Tuple

__all__ = [
    "direction_word",
    "altitude_phrase",
    "narrate_pose",
    "narrate_targets",
]

#: 八方向按 22.5° 扇区划分，0° 为北、顺时针。
_DIRECTION_NAMES: Tuple[str, ...] = (
    "北", "东北", "东", "东南", "南", "西南", "西", "西北",
)

#: 最多报几颗目标：再多一句口语也读不过来。
_TARGET_LIMIT = 3

_NOT_ALIGNED = "还没对准，先让设备看到星空"
_NO_TARGETS = "附近没有星表里的亮星"


def _finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值/NaN/Inf 返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def direction_word(az_deg: Any) -> str:
    """方位角（度，北=0 东=90）→ 八方向中文。

    边界按 22.5° 扇区：``[22.5, 67.5)`` 是东北，依此类推；非有限值返回
    ``"未知方向"``，不默认成北。
    """
    az = _finite(az_deg)
    if az is None:
        return "未知方向"
    index = int(math.floor(((az % 360.0) + 22.5) / 45.0)) % 8
    return _DIRECTION_NAMES[index]


def altitude_phrase(alt_deg: Any) -> str:
    """地平高度（度）→ 口语短语；非有限值返回 ``"高度未知"``。"""
    alt = _finite(alt_deg)
    if alt is None:
        return "高度未知"
    if alt >= 75.0:
        return "接近头顶"
    if alt >= 45.0:
        return "半空"
    if alt >= 15.0:
        return "离地平线不太高"
    if alt >= 0.0:
        return "贴着地平线"
    return "在地平线以下"


def _brightness_word(vmag: Any) -> str:
    """星等 → 亮度词；缺失/非有限返回空串（调用方省略括号）。"""
    v = _finite(vmag)
    if v is None:
        return ""
    if v <= 0.5:
        return "很亮"
    if v <= 1.5:
        return "明亮"
    if v <= 2.5:
        return "较亮"
    if v <= 3.5:
        return "偏暗"
    return "较暗"


def _ranked_targets(targets: Any) -> List[Tuple[str, Optional[float]]]:
    """按名字去重并按星等从亮到暗排序，取前 ``_TARGET_LIMIT`` 条。

    缺 ``name``（或名字为空）的条目跳过；星等缺失的排在已知亮度之后，
    不参与猜值。
    """
    if targets is None or isinstance(targets, (str, bytes, Mapping)):
        return []
    try:
        items = list(targets)
    except TypeError:
        return []

    ranked: List[Tuple[str, Optional[float]]] = []
    seen: List[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_name = item.get("name")
        if raw_name is None:
            continue
        name = str(raw_name).strip()
        if not name or name in seen:
            continue
        seen.append(name)
        ranked.append((name, _finite(item.get("vmag"))))

    ranked.sort(key=lambda row: (0, row[1]) if row[1] is not None else (1, 0.0))
    return ranked[:_TARGET_LIMIT]


def _targets_sentence(targets: Any) -> str:
    """可用目标的「附近能看到：…」句；没有可用目标返回空串。"""
    ranked = _ranked_targets(targets)
    if not ranked:
        return ""
    parts: List[str] = []
    for name, vmag in ranked:
        word = _brightness_word(vmag)
        parts.append(f"{name}（{word}）" if word else name)
    return "附近能看到：" + "、".join(parts)


def narrate_pose(pose: Any, targets: Any = None, *,
                 level: Optional[str] = None) -> str:
    """把一帧位姿与附近目标译成一句口语。

    Args:
        pose: ``sky_pointing.fuse_poses`` 的返回 dict。
        targets: ``identify_direction`` 的 ``stars`` 列表（行星目标同形）。
        level: ``sky_health.classify_level`` 的 ``L0``–``L4``；``L4`` 时
            明确说「手动接管」。

    Returns:
        不含 ``az`` / ``alt`` / ``vmag`` / ``GMST`` / ``FOV`` 等术语的中文；
        位姿不可用时统一为「还没对准，先让设备看到星空」。
    """
    if not isinstance(pose, Mapping) or pose.get("valid") is not True:
        return _NOT_ALIGNED

    az = _finite(pose.get("az_deg"))
    alt = _finite(pose.get("alt_deg"))
    if az is None or alt is None:
        return _NOT_ALIGNED

    direction = direction_word(az)
    height = altitude_phrase(alt)
    level_text = "" if level is None else str(level).strip().upper()
    if level_text == "L4":
        text = f"你已经手动接管指向，正看向{direction}，{height}"
    else:
        text = f"你正看向{direction}，{height}"

    extra = _targets_sentence(targets)
    return f"{text}。{extra}" if extra else text


def narrate_targets(targets: Any) -> str:
    """单独给目标列表的一句话；没有可用目标时如实说没有。"""
    return _targets_sentence(targets) or _NO_TARGETS
