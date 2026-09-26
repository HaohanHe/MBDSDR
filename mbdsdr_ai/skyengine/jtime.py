"""
mbdsdr_ai/skyengine/jtime.py — 时间内核 (JDAY)
================================================

对照 Stellarium web engine:
  - observer.tt 是 MJD (儒略日, 天); MJD2date/date2MJD (pre.js:92-98)。
  - 时间流动 (navigation.c:51-55):
        tt += dt * time_speed / 86400
    time_speed 单位 "秒/现实秒"; /86400 转成天。time_speed=0 暂停,
    负值倒退, 大值星空快转。

本模块提供:
  - unix_seconds <-> Julian Date 互转
  - gmst_deg (IAU 1982, 度)
  - TimeKernel: 持当前 JD, 按 time_speed 推进 (供渲染星图)
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass

J2000: float = 2451545.0
_UNIX_EPOCH_JD: float = 2440587.5      # 1970-01-01 00:00 UTC (pre.js:92-98)


def unix_to_jd(unix_s: float) -> float:
    """Unix 秒 -> 儒略日 (UTC)。date2MJD 逆运算。"""
    return unix_s / 86400.0 + _UNIX_EPOCH_JD


def jd_to_unix(jd: float) -> float:
    """儒略日 -> Unix 秒 (UTC)。MJD2date 逆运算。"""
    return (jd - _UNIX_EPOCH_JD) * 86400.0


def gmst_deg(jd_ut1: float) -> float:
    """格林尼治平恒星时 GMST (度)。IAU 1982 展开式。

    与 mbdsdr_ai.orbit._gmst_days 同式 (gpredict ThetaG_JD), 输出度。
    """
    t = (jd_ut1 - J2000) / 36525.0
    gmst_sec = (67310.54841
                + (876600.0 * 3600.0 + 8640184.812866) * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    return (gmst_sec % 86400.0) / 240.0 % 360.0


@dataclass
class TimeKernel:
    """天图时间内核。

    Attributes:
        jd:    当前儒略日 (UTC)。
        speed: 时间流速 (秒/现实秒)。0=暂停, 1=实时, 60=1分/秒, 负=倒退。
               对照 navigation.c:52 time_speed。
    """

    jd: float = 0.0
    speed: float = 1.0

    def __post_init__(self) -> None:
        if self.jd <= 0.0:
            self.jd = unix_to_jd(_time.time())

    def advance(self, real_dt_s: float) -> None:
        """按现实经过 real_dt_s 秒推进内部时间 (navigation.c:51-55)。"""
        self.jd += real_dt_s * self.speed / 86400.0

    def set_now(self) -> None:
        self.jd = unix_to_jd(_time.time())

    def unix(self) -> float:
        return jd_to_unix(self.jd)
