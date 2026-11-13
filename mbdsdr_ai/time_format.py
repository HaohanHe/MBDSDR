# SPDX-License-Identifier: MIT
"""时间显示格式化（叶子模块）。

``time_basis`` 是时间系统的公共入口（闰秒、TLE 历元、时钟守卫、时间窗校验），
但「UTC → 本地 ISO」只是纯显示变换，却被 ``new_spacetime`` / ``sdr_tools``
直接引用。留在 ``time_basis`` 里，等于为一个字符串格式化函数依赖整块时间系统。

本模块不 import 任何 ``mbdsdr_ai`` 模块，任意层都可引用；``time_basis`` 原处
再导出 ``format_local``，既有 ``from .time_basis import format_local`` 的调用方
不受影响。
"""
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

UTC = timezone.utc


def as_utc(dt: datetime) -> datetime:
    """无时区的 datetime 按 UTC 解释（不按本地时区猜）。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _posix_zone_from_files() -> Optional[Any]:
    """POSIX：从 ``TZ`` / ``/etc/timezone`` / ``/etc/localtime`` 找出 IANA 时区名。"""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # Python < 3.9
        return None

    name = os.environ.get("TZ", "").strip().lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass

    try:
        with open("/etc/timezone", "r", encoding="utf-8") as fh:
            name = fh.read().strip()
        if name:
            return ZoneInfo(name)
    except Exception:
        pass

    try:
        resolved = os.path.realpath("/etc/localtime")
        marker = "zoneinfo/"
        idx = resolved.find(marker)
        if idx >= 0:
            return ZoneInfo(resolved[idx + len(marker):])
    except Exception:
        pass
    return None


def _system_tz() -> Any:
    """发现系统时区（带夏令时规则）；只有确实发现不了才退回当前固定偏移。"""
    if sys.platform == "win32":
        try:
            from dateutil.tz import tzwinlocal

            return tzwinlocal()
        except Exception:
            pass
    else:
        tz = _posix_zone_from_files()
        if tz is not None:
            return tz
        try:
            from dateutil.tz import tzlocal

            return tzlocal()
        except Exception:
            pass
    # 兜底：只有「此刻偏移」，没有历史/未来 DST 规则。仅当系统时区不可发现时才会走到。
    return datetime.now().astimezone().tzinfo


def resolve_tz(tz: Optional[Any] = None) -> Any:
    """显式时区优先；缺省返回**携带夏令时规则**的系统时区。

    旧实现 ``datetime.now().astimezone().tzinfo`` 是冻结了「此刻偏移」的
    ``datetime.timezone``：在跨 DST 的时区里，换算半年后的时刻会差一小时。
    现在 Windows 用 ``dateutil.tz.tzwinlocal``（读系统时区规则），POSIX 用
    ``zoneinfo``（``TZ`` / ``/etc/timezone`` / ``/etc/localtime``）。
    """
    if tz is not None:
        return tz
    return _system_tz()


def format_local(dt_utc: datetime, tz: Optional[Any] = None) -> str:
    """UTC → 本地时区 ISO 字符串（带偏移，可往返验证）。

    例：``2026-11-04T20:00:00+08:00``。``tz`` 缺省用系统本地时区。
    轨道计算永远用输入 UTC；本函数只做显示变换。
    """
    dt = as_utc(dt_utc).astimezone(resolve_tz(tz))
    return dt.isoformat()
