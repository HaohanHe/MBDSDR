# SPDX-License-Identifier: MIT
"""时间基准统一层：UTC / 闰秒 / TLE 历元 / 系统时钟跳变。

P11 背景（错一秒，过境全废）：

  * 所有轨道计算口径只能是 **UTC**；本地时区仅是显示变换，必须可逆可验证。
  * TLE 历元是 UTC，两位年份按 Spacetrack 惯例折叠：``YY >= 57`` → 1900s，
    否则 2000s。历元不合法（如 DDD > 366）必须判解析失败，绝不猜。
  * 闰秒只影响 UTC↔GPS/TT 的换算，不改变 TLE 历元语义（TLE 历元不含闰秒
    跳变）。SGP4 输入是 UTC 对应的儒略日，skyfield 在 UTC→TT 时自行查表。
  * 系统时钟可能被 NTP 校正或用户手改：用 ``time.monotonic()`` 作参考检测
    墙钟回跳/跳变，并对外给出告警；回跳时禁止把时间差当成负时长。

本模块不依赖 skyfield/sgp4，可在无卫星依赖的环境单独测试。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .tle_epoch import TLE_FUTURE_TOL_DAYS, parse_tle_epoch
except ImportError:  # 旧脚本以扁平模块方式 import time_basis
    from tle_epoch import TLE_FUTURE_TOL_DAYS, parse_tle_epoch

# 显示格式化是叶子能力（time_format 不依赖任何 mbdsdr_ai 模块），放在本模块
# 会让「只要一个 ISO 字符串」的调用方拖上整块时间系统。原处再导出，保持
# `from .time_basis import format_local` 的既有调用方不变。
from .time_format import as_utc as _as_utc, format_local, resolve_tz as _resolve_tz

__all__ = [
    "UTC",
    "LEAP_SECOND_TABLE",
    "TLE_EPOCH_YEAR_PIVOT",
    "leap_seconds_at",
    "utc_to_gps_seconds",
    "gps_seconds_to_utc",
    "tle_epoch_utc",
    "tle_epoch_report",
    "format_local",
    "local_to_utc",
    "validate_pass_window",
    "ClockGuard",
    "get_clock_guard",
    "reset_clock_guard",
]

UTC = timezone.utc

# TLE 两位年份折叠分界：YY >= 57 视为 19YY，否则 20YY（Spacetrack Report #3）。
TLE_EPOCH_YEAR_PIVOT = 57

# GPS-UTC 闰秒累积表（生效日 00:00 UTC → 之后的累积值）。
# 1980-01-06（GPS 历元）之前为 0；2017-01-01 之后未再插入闰秒。
LEAP_SECOND_TABLE: Tuple[Tuple[datetime, int], ...] = (
    (datetime(1981, 7, 1, tzinfo=UTC), 1),
    (datetime(1982, 7, 1, tzinfo=UTC), 2),
    (datetime(1983, 7, 1, tzinfo=UTC), 3),
    (datetime(1985, 7, 1, tzinfo=UTC), 4),
    (datetime(1988, 1, 1, tzinfo=UTC), 5),
    (datetime(1990, 1, 1, tzinfo=UTC), 6),
    (datetime(1991, 1, 1, tzinfo=UTC), 7),
    (datetime(1992, 7, 1, tzinfo=UTC), 8),
    (datetime(1993, 7, 1, tzinfo=UTC), 9),
    (datetime(1994, 7, 1, tzinfo=UTC), 10),
    (datetime(1996, 1, 1, tzinfo=UTC), 11),
    (datetime(1997, 7, 1, tzinfo=UTC), 12),
    (datetime(1999, 1, 1, tzinfo=UTC), 13),
    (datetime(2006, 1, 1, tzinfo=UTC), 14),
    (datetime(2009, 1, 1, tzinfo=UTC), 15),
    (datetime(2012, 7, 1, tzinfo=UTC), 16),
    (datetime(2015, 7, 1, tzinfo=UTC), 17),
    (datetime(2017, 1, 1, tzinfo=UTC), 18),
)

_GPS_EPOCH = datetime(1980, 1, 6, tzinfo=UTC)


def leap_seconds_at(dt_utc: datetime) -> int:
    """返回某 UTC 时刻的 GPS-UTC 累积闰秒（1980 前为 0，2017 后为 18）。"""
    dt = _as_utc(dt_utc)
    value = 0
    for effective, cumulative in LEAP_SECOND_TABLE:
        if dt >= effective:
            value = cumulative
        else:
            break
    return value


def utc_to_gps_seconds(dt_utc: datetime) -> float:
    """UTC → GPS 时间秒（自 1980-01-06 00:00 UTC 起，含闰秒累积）。"""
    dt = _as_utc(dt_utc)
    base = (dt - _GPS_EPOCH).total_seconds()
    return base + float(leap_seconds_at(dt))


def gps_seconds_to_utc(gps_seconds: float) -> datetime:
    """GPS 时间秒 → UTC（按该时刻的闰秒累积回退）。"""
    # 先按无闰秒粗算，再用该近似时刻的闰秒修正一次；闰秒跳变边界外足够。
    approx = _GPS_EPOCH + timedelta(seconds=float(gps_seconds))
    leap = leap_seconds_at(approx)
    return _GPS_EPOCH + timedelta(seconds=float(gps_seconds) - leap)


def tle_epoch_utc(line1: str) -> Optional[datetime]:
    """TLE line1 → UTC datetime（固定列 19–32，非法历元返回 None）。"""
    return parse_tle_epoch(line1)


def tle_epoch_report(line1: str, now: Optional[datetime] = None,
                     future_tol_days: float = TLE_FUTURE_TOL_DAYS) -> Dict[str, Any]:
    """TLE 历元解析报告：年份规则、年龄、是否在将来、是否可信。

    ``now`` 默认系统 UTC。返回::

        {"ok": bool, "epoch_utc": ISO|None, "year": int|None,
         "two_digit_year": int|None, "year_rule": str,
         "age_days": float|None, "epoch_in_future": bool,
         "plausible": bool, "reason": str}

    ``plausible=False`` 表示历元虽可解析但明显异常（超过 ``future_tol_days``
    的将来/几十年前），调用方应如实标注而不是当成精确历元。
    """
    ref = _as_utc(now) if now is not None else datetime.now(UTC)
    raw = str(line1 or "")[18:32].strip() if len(str(line1 or "")) >= 32 else ""
    two_digit: Optional[int] = None
    year: Optional[int] = None
    rule = ""
    if len(raw) >= 2 and raw[:2].isdigit():
        two_digit = int(raw[:2])
        year = (1900 + two_digit) if two_digit >= TLE_EPOCH_YEAR_PIVOT else (2000 + two_digit)
        rule = ("YY>=57 → 19YY" if two_digit >= TLE_EPOCH_YEAR_PIVOT
                else "YY<57 → 20YY")
    epoch = parse_tle_epoch(line1)
    if epoch is None:
        return {
            "ok": False, "epoch_utc": None, "year": year,
            "two_digit_year": two_digit, "year_rule": rule,
            "age_days": None, "epoch_in_future": False,
            "plausible": False, "reason": "TLE 历元无法解析（缺列/坏值/越界）",
        }
    age_days = (ref - epoch).total_seconds() / 86400.0
    in_future = age_days < 0.0
    plausible = not (in_future and abs(age_days) > future_tol_days)
    reason = "历元正常"
    if in_future and not plausible:
        reason = f"历元在 {abs(age_days):.1f} 天后的将来，时钟或 TLE 可疑"
    elif in_future:
        reason = "历元略早于本机时钟（将来 < 2 天，需核对系统时间）"
    elif age_days > 3650.0:
        plausible = False
        reason = f"历元已是 {age_days / 365.25:.0f} 年前，疑似两位年份折叠错误"
    return {
        "ok": True,
        "epoch_utc": epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": year,
        "two_digit_year": two_digit,
        "year_rule": rule,
        "age_days": round(age_days, 3),
        "epoch_in_future": in_future,
        "plausible": plausible,
        "reason": reason,
    }


def local_to_utc(dt_local: datetime, tz: Optional[Any] = None,
                 *, fold: Optional[int] = None) -> datetime:
    """本地时区 datetime → UTC。无时区的输入按 ``tz``（缺省系统本地）解释。

    夏令时切换处的本地时刻有两种**不可静默处理**的情况：

    * **歧义时刻**（回拨，同一墙上时间出现两次）：必须由调用方用 ``fold=0``
      （第一次）或 ``fold=1``（第二次）明确选一次，否则抛 ``ValueError``；
      也可在输入上 ``dt_local.replace(fold=1)`` 表达同样意图。
    * **不存在时刻**（前拨，墙上时间被跳过）：任何映射都会悄悄挪动时刻，
      一律抛 ``ValueError``，由调用方改用切换前/后的真实时刻。

    带时区的输入偏移已确定，按其自带 ``fold`` 直接换算；``fold`` 参数若给出
    则覆盖输入的 ``fold``。
    """
    if dt_local.tzinfo is not None:
        if fold is not None:
            dt_local = dt_local.replace(fold=fold)
        return dt_local.astimezone(UTC)

    if fold not in (None, 0, 1):
        raise ValueError(f"fold 只能是 None/0/1，收到 {fold!r}")
    zone = _resolve_tz(tz)
    first = dt_local.replace(tzinfo=zone, fold=0)
    second = dt_local.replace(tzinfo=zone, fold=1)
    off0, off1 = first.utcoffset(), second.utcoffset()

    if off0 != off1:
        # PEP 495：回拨重叠时 fold=0 是较早（较大）偏移，fold=1 是较晚（较小）偏移；
        # 前拨缺口相反（fold=0 是切换前偏移，fold=1 是切换后偏移）。
        if off0 < off1:
            raise ValueError(
                f"本地时刻 {dt_local.isoformat()} 在时区 {zone} 不存在"
                f"（夏令时前拨跳过该时段，可用偏移 "
                f"{first.astimezone(UTC).isoformat()} 与 "
                f"{second.astimezone(UTC).isoformat()} 之间没有对应时刻）："
                f"请改用切换前或切换后的真实时刻"
            )
        chosen = fold if fold is not None else (1 if dt_local.fold == 1 else None)
        if chosen is None:
            raise ValueError(
                f"本地时刻 {dt_local.isoformat()} 在时区 {zone} 有歧义（夏令时回拨，"
                f"出现两次）：请显式传 fold=0（第一次，UTC "
                f"{first.astimezone(UTC).isoformat()}）或 fold=1（第二次，UTC "
                f"{second.astimezone(UTC).isoformat()}）"
            )
        return dt_local.replace(tzinfo=zone, fold=chosen).astimezone(UTC)

    # 偏移唯一，但仍可能是「前拨被跳过」的不存在时刻：UTC 往返后墙上时间会变。
    roundtrip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if roundtrip != dt_local:
        raise ValueError(
            f"本地时刻 {dt_local.isoformat()} 在时区 {zone} 不存在"
            f"（夏令时前拨跳过该时段，换算后墙上时间是 {roundtrip.isoformat()}）："
            f"请改用切换前或切换后的真实时刻"
        )
    return first.astimezone(UTC)


def validate_pass_window(rise_unix: float, set_unix: float,
                         now_unix: Optional[float] = None) -> Dict[str, Any]:
    """过境时间窗自洽检查：负时长/时钟回跳必须被发现，不许静默输出。

    返回 ``{"ok": bool, "duration_s": float, "issues": [str], "warning": str}``。
    """
    duration = float(set_unix) - float(rise_unix)
    issues: List[str] = []
    if duration < 0.0:
        issues.append("set_before_rise（落下早于升起，疑似系统时钟回跳或输入错乱）")
    elif duration == 0.0:
        issues.append("zero_duration（过境时长为 0）")
    if now_unix is not None and float(now_unix) - float(rise_unix) > 86400.0:
        issues.append("rise_far_in_past（升起时刻早于当前 24h 以上）")
    return {
        "ok": not issues,
        "duration_s": round(duration, 3),
        "issues": issues,
        "warning": "；".join(issues),
    }


@dataclass
class ClockGuard:
    """系统墙钟跳变检测器（以 ``time.monotonic()`` 为参考）。

    - 墙钟回跳（wall_delta < -``backward_tol_s``）→ ``backward_jump``；
    - 墙钟与单调钟差值超过 ``forward_tol_s``（NTP 前跳/用户改时间）→
      ``forward_jump`` / ``backward_adjust``；
    - 其余为 ``ok``。事件与最后状态可随过境/跟踪结果一并对外告警。
    """

    backward_tol_s: float = 1.0
    forward_tol_s: float = 5.0
    _last_wall: Optional[float] = field(default=None, init=False)
    _last_mono: Optional[float] = field(default=None, init=False)
    last_event: Dict[str, Any] = field(default_factory=dict, init=False)

    def observe(self, wall: Optional[float] = None,
                mono: Optional[float] = None) -> Dict[str, Any]:
        """采样一次墙钟/单调钟，返回本次事件（``type`` 为 ok 或跳变类型）。"""
        wall = time.time() if wall is None else float(wall)
        mono = time.monotonic() if mono is None else float(mono)
        event: Dict[str, Any] = {
            "type": "ok", "wall": wall, "mono": mono,
            "wall_delta_s": None, "mono_delta_s": None, "jump_s": 0.0,
        }
        if self._last_wall is not None and self._last_mono is not None:
            dw = wall - self._last_wall
            dm = mono - self._last_mono
            event["wall_delta_s"] = round(dw, 3)
            event["mono_delta_s"] = round(dm, 3)
            if dw < -self.backward_tol_s:
                event["type"] = "backward_jump"
                event["jump_s"] = round(dw, 3)
            elif abs(dw - dm) > self.forward_tol_s:
                event["type"] = ("forward_jump" if dw > dm else "backward_adjust")
                event["jump_s"] = round(dw - dm, 3)
            if event["type"] != "ok":
                event["warning"] = (
                    f"系统时钟{('回跳' if event['type'] == 'backward_jump' else '跳变')}"
                    f" {event['jump_s']:+.1f}s，过境/跟踪时刻需核对")
                self.last_event = dict(event)
        self._last_wall = wall
        self._last_mono = mono
        return event

    def status(self) -> Dict[str, Any]:
        """最近一次跳变状态；无跳变时 ``last_event`` 为空。"""
        return {
            "clock_jump": bool(self.last_event),
            "last_event": dict(self.last_event),
            "warning": self.last_event.get("warning", ""),
        }

    def reset(self) -> None:
        self._last_wall = None
        self._last_mono = None
        self.last_event = {}


_guard: Optional[ClockGuard] = None


def get_clock_guard() -> ClockGuard:
    """全局时钟守卫（进程内单例）。"""
    global _guard
    if _guard is None:
        _guard = ClockGuard()
    return _guard


def reset_clock_guard() -> None:
    """测试/重置用：丢弃全局时钟守卫。"""
    global _guard
    _guard = None
