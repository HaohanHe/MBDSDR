# SPDX-License-Identifier: MIT
"""时间显示格式化（叶子模块）。

``time_basis`` 是时间系统的公共入口（闰秒、TLE 历元、时钟守卫、时间窗校验），
但「UTC → 本地 ISO」只是纯显示变换，却被 ``new_spacetime`` / ``sdr_tools``
直接引用。留在 ``time_basis`` 里，等于为一个字符串格式化函数依赖整块时间系统。

本模块不 import 任何 ``mbdsdr_ai`` 模块，任意层都可引用；``time_basis`` 原处
再导出 ``format_local``，既有 ``from .time_basis import format_local`` 的调用方
不受影响。
"""
from datetime import datetime, timezone
from typing import Any, Optional

UTC = timezone.utc


def as_utc(dt: datetime) -> datetime:
    """无时区的 datetime 按 UTC 解释（不按本地时区猜）。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def resolve_tz(tz: Optional[Any] = None) -> Any:
    """显式时区优先；缺省用系统本地时区。"""
    if tz is not None:
        return tz
    return datetime.now().astimezone().tzinfo


def format_local(dt_utc: datetime, tz: Optional[Any] = None) -> str:
    """UTC → 本地时区 ISO 字符串（带偏移，可往返验证）。

    例：``2026-11-04T20:00:00+08:00``。``tz`` 缺省用系统本地时区。
    轨道计算永远用输入 UTC；本函数只做显示变换。
    """
    dt = as_utc(dt_utc).astimezone(resolve_tz(tz))
    return dt.isoformat()
