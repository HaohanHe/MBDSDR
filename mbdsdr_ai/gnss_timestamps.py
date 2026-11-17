# SPDX-License-Identifier: MIT
"""
MBDSDR - GNSS 授时 → SigMF captures 对齐（时空链路时间基准）
================================================================

把串口 GNSS（NMEA-0183 GGA/RMC/ZDA）解出的 UTC 时间，对齐写到 SigMF
``captures[0].core:datetime``，并**显式标注时间来源级别与精度**：

- ``mbdsdr:time_source = "pps"``   —— 有 GNSS PPS 秒脉冲对齐；仅此级别可谈
  亚微秒级。
- ``mbdsdr:time_source = "nmea"``  —— 只靠 NMEA 语句（ZDA 整句，或 RMC 的
  日期+时间）授时，无 PPS；精度按 0.1–1 秒级保守表述。
- ``mbdsdr:time_source = "ntp"``    —— ``core:datetime`` 来自 NTP 校准的系统时钟；
  毫秒到几十毫秒级。
- ``mbdsdr:time_source = "none"``   —— 无授时源，``core:datetime`` 退化为本机时钟
  UTC；绝不冒充 GNSS 授时。
- ``mbdsdr:time_source = "unknown"`` —— 来源无法判定，按最保守精度处理。

同一份级别到精度文案的映射由 ``TIME_SOURCE_PRECISION`` 单一维护，并写入
``mbdsdr:time_precision``。旧文件里的 ``"gnss"`` / ``"system"`` 分别视为
``"nmea"`` / ``"none"`` 的旧口径（``TIME_SOURCE_GNSS`` / ``TIME_SOURCE_SYSTEM``
仅作兼容别名）。

红线（与 serial_gnss.py / playback.py 的诚实空态风格一致）：
- 无 GNSS 时不造假坐标/假授时；只退化为系统时间并标注。
- GGA/RMC 的 ``utc_time`` 只是 UTC 一天内的时刻串（``hhmmss.ss``），**本身不含日期**；
  只有 RMC 带 ``ddmmyy`` 日期、ZDA 带完整年月日。仅有 GGA（无 RMC/ZDA）时无法锚定
  日期，本模块诚实返回 None → 上层退回系统时间，绝不"猜一个日期"。
- 时间源可注入（``clock=``），使云内无硬件时仍可做**确定性**测试（固定 NMEA + 固定时钟）。

本模块依据公开 NMEA-0183 字段布局独立实现；复用 ``mbdsdr_ai.serial_gnss.NMEAParser``
做逐句解析（带 XOR 校验和），不重复造解析器。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

try:
    from .serial_gnss import NMEAParser
except Exception:  # pragma: no cover - 包外直接运行时降级
    try:
        from serial_gnss import NMEAParser  # type: ignore
    except Exception:
        NMEAParser = None  # type: ignore


# 时间来源级别（写入 SigMF captures 的 mbdsdr:time_source）
TIME_SOURCE_PPS = "pps"
TIME_SOURCE_NMEA = "nmea"
TIME_SOURCE_NTP = "ntp"
TIME_SOURCE_NONE = "none"
TIME_SOURCE_UNKNOWN = "unknown"

# 旧口径别名："gnss" = 无 PPS 的 NMEA 授时；"system" = 无授时源的本机时钟。
# 仅为读写旧 SigMF 文件/旧调用方保留，不再产生新值。
TIME_SOURCE_GNSS = TIME_SOURCE_NMEA
TIME_SOURCE_SYSTEM = TIME_SOURCE_NONE

TIME_SOURCE_LEVELS = (TIME_SOURCE_PPS, TIME_SOURCE_NMEA, TIME_SOURCE_NTP,
                      TIME_SOURCE_NONE, TIME_SOURCE_UNKNOWN)

_LEGACY_TIME_SOURCES = {"gnss": TIME_SOURCE_NMEA, "system": TIME_SOURCE_NONE}

# 每种来源级别一句人话精度说明（单一事实源，写入 mbdsdr:time_precision）。
# 口径：只有 pps 允许宣称亚微秒级；nmea 无 PPS 只说秒级；none 是本机未校准钟。
TIME_SOURCE_PRECISION = {
    TIME_SOURCE_PPS: "PPS 秒脉冲硬同步：可达亚微秒级（取决于接收机与硬件时戳）",
    TIME_SOURCE_NMEA: "NMEA 整秒授时：约 0.1–1 秒级（无 PPS 对齐）",
    TIME_SOURCE_NTP: "NTP 授时：约 1–50 毫秒级（受网络往返与抖动影响）",
    TIME_SOURCE_NONE: "本机时钟（未授时）：偏差可能达秒级或更大",
    TIME_SOURCE_UNKNOWN: "来源未知：无法判定精度，按秒级或更差保守处理",
}

_TIME_SOURCE_NOTES = {
    TIME_SOURCE_PPS: "core:datetime 由 GNSS PPS 秒脉冲对齐的授时给出",
    TIME_SOURCE_NMEA: "core:datetime 来自 GNSS NMEA (ZDA/RMC) 整秒授时，无 PPS",
    TIME_SOURCE_NTP: "core:datetime 来自 NTP 校准的系统时钟",
    TIME_SOURCE_NONE: "无授时源；core:datetime 来自本机时钟 UTC（未校准）",
    TIME_SOURCE_UNKNOWN: "授时来源无法判定；core:datetime 精度未知",
}


def _default_clock() -> datetime:
    """默认时钟：本机 UTC（tz-aware）。测试时可注入固定时钟。"""
    return datetime.now(timezone.utc)


def to_sigmf_iso(dt: datetime) -> str:
    """把 tz-aware datetime 格式化为 SigMF core:datetime 接受的 ISO-8601 UTC 串。

    形如 ``2026-10-02T07:25:45Z``（秒精度、Z 后缀）。与 cpp/src/dsp/recorder.cpp
    ``QDateTime::currentDateTimeUtc().toString(Qt::ISODate)`` 的秒级 UTC 写法对齐，
    保证 C++ 录制端与 Python 回放/转换端读到同一种格式。
    """
    if dt.tzinfo is None:
        #  naive 时间一律按 UTC 解释（NMEA 时间本身就是 UTC）
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# NMEA 时刻串 → datetime
# ---------------------------------------------------------------------------
def nmea_hhmmss_to_seconds(utc_time: str) -> Optional[float]:
    """``hhmmss.ss`` → 自 UTC 子夜起的秒数。非法/空返回 None。"""
    s = (utc_time or "").strip()
    if not s:
        return None
    try:
        # 允许 "072545" 或 "072545.00"
        if "." in s:
            hh = int(s[0:2]); mm = int(s[2:4]); ss = float(s[4:])
        else:
            hh = int(s[0:2]); mm = int(s[2:4]); ss = float(s[4:])
    except (ValueError, IndexError):
        return None
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0.0 <= ss < 61.0):
        return None
    return hh * 3600.0 + mm * 60.0 + ss


def nmea_ddmmyy_to_date(date_str: str) -> Optional[tuple]:
    """``ddmmyy`` → (year, month, day)。yy>=57 归 1900s，否则 2000s（NASA 惯例）。"""
    s = (date_str or "").strip()
    if len(s) < 6 or not s[:6].isdigit():
        return None
    try:
        dd = int(s[0:2]); mm = int(s[2:4]); yy = int(s[4:6])
    except ValueError:
        return None
    year = (1900 + yy) if yy >= 57 else (2000 + yy)
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    return (year, mm, dd)


def combine_nmea_time_date(utc_time: str, date_str: str) -> Optional[datetime]:
    """RMC 的 ``utc_time``(hhmmss.ss) + ``date``(ddmmyy) → tz-aware UTC datetime。

    任一字段缺失/非法返回 None（绝不猜日期）。
    """
    secs = nmea_hhmmss_to_seconds(utc_time)
    ymd = nmea_ddmmyy_to_date(date_str)
    if secs is None or ymd is None:
        return None
    year, month, day = ymd
    hh = int(secs // 3600)
    mm = int((secs % 3600) // 60)
    whole_s = int(secs)
    micro = int(round((secs - whole_s) * 1_000_000))
    try:
        return datetime(year, month, day, hh, mm, whole_s % 60, micro,
                        tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# NMEA 日志 → GNSS UTC datetime（整段语句流）
# ---------------------------------------------------------------------------
def nmea_log_to_utc_datetime(lines: Iterable[str],
                             parser=None) -> Optional[datetime]:
    """从一段 NMEA 语句流中解出**最新**的 GNSS UTC datetime。

    优先级（精度从高到低）：
      1. ZDA —— 直接给完整年月日时分（含闰秒字段），``datetime_utc``；
      2. RMC —— ``utc_time``(hhmmss.ss) + ``date``(ddmmyy) 组合；
      3. GGA/GLL 只有时刻无日期 —— **无法锚定日期，返回 None**（诚实空态）。

    多帧时取最后一条可用语句（最接近录制时刻）。解析失败/无语句返回 None。
    """
    if NMEAParser is None and parser is None:  # pragma: no cover
        return None
    p = parser or NMEAParser()
    zda_dt: Optional[datetime] = None
    rmc_dt: Optional[datetime] = None
    for raw in lines:
        if not raw:
            continue
        rec = p.parse(raw) if not isinstance(raw, dict) else raw
        if rec is None:
            continue
        st = rec.get("sentence")
        if st == "ZDA":
            dt = rec.get("datetime_utc")
            if isinstance(dt, datetime):
                zda_dt = dt
        elif st == "RMC":
            dt = combine_nmea_time_date(rec.get("utc_time", ""), rec.get("date", ""))
            if dt is not None:
                rmc_dt = dt
    # ZDA 优先；否则 RMC；否则 None（GGA-only 不猜日期）
    return zda_dt or rmc_dt


# ---------------------------------------------------------------------------
# 捕获时刻解析（时间源显式化）
# ---------------------------------------------------------------------------
class CaptureTime:
    """一次 SigMF 捕获的对齐时刻 + 时间来源级别 + 精度（不可变语义快照）。

    字段口径：
      - ``time_source``：来源级别，取值见 ``TIME_SOURCE_LEVELS``
        （``pps`` / ``nmea`` / ``ntp`` / ``none`` / ``unknown``）；
        传入旧口径 ``"gnss"`` / ``"system"`` 会分别归一为 ``"nmea"`` / ``"none"``。
      - ``precision``：该级别的一句人话精度说明，来自 ``TIME_SOURCE_PRECISION``。
      - ``note``：面向人的来源说明。
    """

    def __init__(self, datetime_iso: str, time_source: str, note: str = "",
                 precision: Optional[str] = None):
        self.datetime_iso = datetime_iso
        self.time_source = _LEGACY_TIME_SOURCES.get(time_source, time_source)
        self.precision = (precision or TIME_SOURCE_PRECISION.get(self.time_source)
                          or TIME_SOURCE_PRECISION[TIME_SOURCE_UNKNOWN])
        self.note = note

    def as_capture_fields(self) -> dict:
        """产出应写入 SigMF ``captures[0]`` 的额外字段（除 core:datetime 外）。"""
        return {
            "mbdsdr:time_source": self.time_source,
            "mbdsdr:time_precision": self.precision,
            "mbdsdr:time_note": self.note,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"CaptureTime({self.datetime_iso!r}, source={self.time_source!r}, "
                f"precision={self.precision!r}, note={self.note!r})")


def resolve_capture_datetime(gnss_dt: Optional[datetime] = None,
                             clock: Optional[Callable[[], datetime]] = None,
                             time_source: Optional[str] = None,
                             has_pps: bool = False) -> CaptureTime:
    """决定一次捕获的 ``core:datetime`` 并显式标注来源级别与精度。

    参数:
        gnss_dt: 授时解出的 UTC datetime（GNSS ZDA/RMC，或 NTP 时刻）。
                 给了且合法 → 用它；否则退回 ``clock()``。
        clock:   无 ``gnss_dt`` 时取系统 UTC 的时钟（callable -> tz-aware
                 datetime）。默认本机时钟；测试时注入固定时钟做确定性断言。
        time_source: 显式级别，取值 ``pps`` / ``nmea`` / ``ntp`` / ``none`` /
                 ``unknown``（旧口径 ``gnss`` / ``system`` 归一为 ``nmea`` /
                 ``none``）。``None`` 时自动判定：有 ``gnss_dt`` → ``nmea``；
                 无 → ``none``（本机时钟，诚实标注）。
        has_pps: 是否真有 PPS 秒脉冲对齐。仅在级别为 ``nmea`` 时升级为 ``pps``；
                 没有 PPS 绝不宣称更高精度。

    返回 CaptureTime。``nmea`` / ``pps`` 若没有 ``gnss_dt``，说明调用方并未提供
    该来源的时刻，按 ``unknown`` 处理，不把本机时钟冒充成 GNSS 授时。
    """
    clk = clock or _default_clock
    if time_source is None:
        level = TIME_SOURCE_NMEA if gnss_dt is not None else TIME_SOURCE_NONE
    else:
        level = _LEGACY_TIME_SOURCES.get(time_source, time_source)
        if level not in TIME_SOURCE_LEVELS:
            raise ValueError(
                f"未知时间来源级别 {time_source!r}；允许 {TIME_SOURCE_LEVELS}")
    if has_pps and level == TIME_SOURCE_NMEA:
        level = TIME_SOURCE_PPS
    if level in (TIME_SOURCE_NMEA, TIME_SOURCE_PPS) and gnss_dt is None:
        # 声称 NMEA/PPS 却没有对应时刻：不能拿本机时钟冒充，退为来源未知
        level = TIME_SOURCE_UNKNOWN

    if gnss_dt is not None:
        if not isinstance(gnss_dt, datetime):
            raise TypeError("gnss_dt 必须是 datetime 或 None")
        dt = gnss_dt
    else:
        dt = clk()
    return CaptureTime(
        datetime_iso=to_sigmf_iso(dt),
        time_source=level,
        note=_TIME_SOURCE_NOTES[level],
    )
