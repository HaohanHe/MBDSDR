"""TLE 历元解析原语（零内部依赖叶子）
=====================================

``parse_tle_epoch`` 只依赖标准库 ``datetime``，却被 ``time_basis``、
``tle_integrity``、``new_spacetime_tle`` 三方共用。它原先住在
``new_spacetime_tle``，于是两个纯时间/完整性模块为了一个纯解析函数反向
依赖了「TLE 联网拉取」重模块，形成 D1 环。

把原语下沉到这里，原模块（``new_spacetime_tle``）原位再导出，
调用方改为直接引用本叶子——函数对象仍是同一个，行为与 identity 不变。
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

#: 未来历元容差（天）。历元领先本机时钟超过该值即判「时钟或 TLE 可疑」。
#: **唯一事实源**：``time_basis.tle_epoch_report`` 与 ``tle_integrity.freshness``
#: 都从这里取，避免两套阈值把同一份 TLE 判成一真一假。
TLE_FUTURE_TOL_DAYS = 2.0


def _is_leap_year(year: int) -> bool:
    """公历闰年（4 年一闰，百年不闰，四百年再闰）。"""
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def parse_tle_epoch(line1: str) -> Optional[datetime]:
    """从 TLE 第 1 行解析历元（epoch）为 UTC datetime。

    TLE 第 1 行历元占**固定列**第 19–32 列（1 基），格式 ``YYDOO.DDDDDDDD``：
    两位年份 + 一年中的第几天（含小数）。按 Spacetrack Report #3 的列布局取子串，
    不依赖空格分词（intl designator 等字段间空格数不固定）。
    yy>=57 归 1900s，否则 2000s。解析失败返回 None —— 调用方据此标 "none"，绝不猜历元。
    """
    if not line1 or not line1.startswith("1 "):
        return None
    # 固定列：第 19~32 列（0 基 [18:32]）
    if len(line1) < 32:
        return None
    ep = line1[18:32].strip()
    try:
        if len(ep) < 5 or "." not in ep:
            return None
        yy = int(ep[0:2])
        doy = float(ep[2:])
    except (ValueError, IndexError):
        return None
    year = (1900 + yy) if yy >= 57 else (2000 + yy)
    if doy <= 0:
        return None
    # 年积日越界（如 DDD=367）必须判解析失败，不能顺延到下一年冒充历元。
    max_doy = 366.0 if _is_leap_year(year) else 365.0
    if doy >= max_doy + 1.0:
        return None
    # doy=1.0 = 1 月 1 日 00:00；(doy-1) 天后的小数时刻
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(days=(doy - 1.0))
