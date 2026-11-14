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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional, Tuple

UTC = timezone.utc

#: 系统时区三种可区分状态。
TZ_STATE_SYSTEM_RULES = "system-rules"    # 拿到带夏令时规则的时区
TZ_STATE_FIXED_OFFSET = "fixed-offset"    # 系统时区不可发现，退到冻结偏移（降级）
TZ_STATE_UNAVAILABLE = "unavailable"      # 连当前偏移都取不到

#: 降级时给用户看的固定说明（显示/跨日边界/夏令时切换处都用这一句）。
DEGRADED_TZ_NOTE = "当前为固定偏移，不随夏令时调整（系统时区规则不可发现）"
UNAVAILABLE_TZ_NOTE = "无法确定系统时区，本地时间按 UTC 显示"


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


class _FixedOffsetTZ(tzinfo):
    """降级时区：冻结「此刻偏移」，并把「没有 DST 规则」写在对象上。

    ``datetime.timezone`` 是不可改的 C 类型，挂不上标记；这里用一个只做转发的
    ``tzinfo`` 子类包一层。它仍可直接喂给 ``astimezone`` / ``replace(tzinfo=)``，
    调用点无需改签名，而降级事实可以被 :func:`tz_degradation` 查出来。
    """

    mbdsdr_state = TZ_STATE_FIXED_OFFSET
    mbdsdr_degraded = True

    def __init__(self, offset: timedelta, name: str, reason: str) -> None:
        self._offset = offset
        self._name = name
        self.mbdsdr_reason = reason

    def utcoffset(self, dt) -> timedelta:
        return self._offset

    def dst(self, dt) -> timedelta:
        return timedelta(0)

    def tzname(self, dt) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"<_FixedOffsetTZ {self._name} offset={self._offset}>"


@dataclass(frozen=True)
class TZResolution:
    """一次时区解析的结果：时区本体 + 来源 + 是否降级 + 给用户看/给判据用的说明。"""

    tz: Any
    state: str
    source: str
    degraded: bool
    reason: str = ""
    note: str = ""


def _probe_system_tz() -> Tuple[Optional[Any], str]:
    """尝试拿到**带 DST 规则**的系统时区；返回 ``(tz, source)``，失败返回 ``(None, 原因)``。"""
    if sys.platform == "win32":
        try:
            from dateutil.tz import tzwinlocal

            return tzwinlocal(), "tzwinlocal"
        except Exception as exc:  # 缺 dateutil / Windows API 不可用
            return None, f"tzwinlocal 不可用：{exc}"
    tz = _posix_zone_from_files()
    if tz is not None:
        return tz, f"zoneinfo:{getattr(tz, 'key', '?')}"
    try:
        from dateutil.tz import tzlocal

        return tzlocal(), "tzlocal"
    except Exception as exc:
        return None, f"tzlocal 不可用：{exc}"


def _current_fixed_offset() -> Optional[_FixedOffsetTZ]:
    """兜底：只取「此刻偏移」，不做任何 DST 推断；取不到返回 None。"""
    local = datetime.now().astimezone()
    off = local.utcoffset()
    if off is None:
        return None
    return _FixedOffsetTZ(off, local.tzname() or "local", "系统时区规则不可发现")


def resolve_tz_info(tz: Optional[Any] = None) -> TZResolution:
    """解析本地时区并**明确交代来源与降级状态**。

    三种可区分情形：

    * ``system-rules``：拿到了 ``zoneinfo`` / ``tzwinlocal`` / ``tzlocal``，
      跨夏令时的换算可信，``degraded=False``、``note=""``；
    * ``fixed-offset``：系统时区不可发现，退到冻结偏移。``degraded=True``，
      ``note`` 为「当前为固定偏移，不随夏令时调整…」——调用方必须能据此提示用户；
    * ``unavailable``：连当前偏移都取不到，按 UTC 处理，``degraded=True``。

    显式传入的 ``tz`` 记作 ``state="explicit"``：那是调用方自己的选择，不算降级。
    """
    if tz is not None:
        return TZResolution(tz=tz, state="explicit", source="explicit", degraded=False)

    found, source = _probe_system_tz()
    if found is not None:
        return TZResolution(
            tz=found, state=TZ_STATE_SYSTEM_RULES, source=source, degraded=False)

    fallback = _current_fixed_offset()
    if fallback is None:
        return TZResolution(
            tz=UTC, state=TZ_STATE_UNAVAILABLE, source="none", degraded=True,
            reason=source, note=UNAVAILABLE_TZ_NOTE)
    return TZResolution(
        tz=fallback, state=TZ_STATE_FIXED_OFFSET, source="fixed-offset", degraded=True,
        reason=source, note=DEGRADED_TZ_NOTE)


def tz_degradation(tz: Any) -> Dict[str, Any]:
    """查询**任意**时区对象是否降级。

    返回 ``{"state", "degraded", "source", "note", "reason"}``。优先读时区对象
    自己带的标记（``_FixedOffsetTZ``）；其次按类型判断 ``zoneinfo`` /
    ``tzwinlocal`` / ``tzlocal`` 为带规则；其余（例如调用方显式传的
    ``datetime.timezone``）记作 ``explicit``，不算降级。
    """
    state = getattr(tz, "mbdsdr_state", "")
    if state:
        return {
            "state": state,
            "degraded": bool(getattr(tz, "mbdsdr_degraded", False)),
            "source": "fixed-offset" if state == TZ_STATE_FIXED_OFFSET else state,
            "note": DEGRADED_TZ_NOTE if state == TZ_STATE_FIXED_OFFSET else "",
            "reason": str(getattr(tz, "mbdsdr_reason", "")),
        }
    name = f"{type(tz).__module__}.{type(tz).__name__}"
    if hasattr(tz, "key") or "tzwinlocal" in name or "tzlocal" in name:
        return {"state": TZ_STATE_SYSTEM_RULES, "degraded": False,
                "source": name, "note": "", "reason": ""}
    return {"state": "explicit", "degraded": False, "source": name,
            "note": "", "reason": ""}


def local_time_note(tz: Optional[Any] = None) -> str:
    """本地时间呈现点要贴的说明：降级时给用户可读句子，正常时返回空串。"""
    return resolve_tz_info(tz).note


def resolve_tz(tz: Optional[Any] = None) -> Any:
    """显式时区优先；缺省返回**携带夏令时规则**的系统时区。

    旧实现 ``datetime.now().astimezone().tzinfo`` 是冻结了「此刻偏移」的
    ``datetime.timezone``：在跨 DST 的时区里，换算半年后的时刻会差一小时。
    现在 Windows 用 ``dateutil.tz.tzwinlocal``（读系统时区规则），POSIX 用
    ``zoneinfo``（``TZ`` / ``/etc/timezone`` / ``/etc/localtime``）。

    返回值仍是 ``tzinfo``（可直接喂给 ``astimezone``）；降级信息不靠猜类型，
    用 :func:`tz_degradation` 查询，或直接读返回对象上的 ``mbdsdr_degraded``。
    """
    if tz is not None:
        return tz
    return resolve_tz_info().tz


def format_local(dt_utc: datetime, tz: Optional[Any] = None) -> str:
    """UTC → 本地时区 ISO 字符串（带偏移，可往返验证）。

    例：``2026-11-04T20:00:00+08:00``。``tz`` 缺省用系统本地时区。
    轨道计算永远用输入 UTC；本函数只做显示变换。
    """
    dt = as_utc(dt_utc).astimezone(resolve_tz(tz))
    return dt.isoformat()
