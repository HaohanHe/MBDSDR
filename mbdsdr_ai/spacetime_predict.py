# SPDX-License-Identifier: MIT
"""
MBDSDR - 过境预测时间基准显式化 + TLE 新鲜度报告（时空链路）
================================================================

把两件事接到过境预测上（Q2）：

1. **预测时间源显式化**：
   - 给了 GNSS UTC（``gnss_dt``，来自 NMEA ZDA/RMC）→ 用它作为预测起点，
     报告 ``time_source="gnss"``；
   - 否则用本机时钟 UTC（``clock`` 可注入，便于确定性测试）→ ``time_source="system"``。
   预测结果里始终带上时间来源，不偷偷用系统时间冒充 GNSS 授时。

2. **TLE 新鲜度联动**：复用 ``new_spacetime_tle.tle_freshness`` 报告历元与年龄，
   但 ``status`` 三态与 ``tier`` 分级**共用同一阈值源**（``tle_integrity``：
   fresh=≤24h，其余一律 stale，历元不可解析=none）。stale 仍出预报，但显式
   告警精度下降；none 不出假预报。

红线：无 TLE / 历元无法解析时返回空过境 + ``status="none"``，绝不伪造过境。
预测抛异常时保留 ``predict_error`` 原因，不把「依赖缺失/坏 TLE/真无过境」
压成同一个 ``n_passes=0``。本模块只是 :mod:`mbdsdr_ai.sat_passes.predict_passes`
的薄包装，轨道传播仍走 skyfield/SGP4，不另写传播器。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from .sat_passes import (
    GroundStation,
    predict_passes,
    make_timescale,
)
from .new_spacetime_tle import (
    tle_freshness,
    TLE_FRESHNESS_MAX_DAYS,
)
from .tle_integrity import accuracy as _tle_accuracy, freshness as _tle_tier

__all__ = ["predict_passes_report", "TLE_FRESHNESS_MAX_DAYS"]

#: ``tle.status`` 由 ``tle.tier`` 投影而来，两处必须同源（``tle_integrity``）。
#: TODO(FZ-04 后的产品口径)：阈值取 24h 还是 7d 属决策项；无论选哪个，
#: status 与 tier 只能从同一份 thresholds 派生，不许再各写一套天数。
_STATUS_BY_TIER = {
    "fresh": "fresh",      # ≤24h
    "usable": "stale",     # ≤7d：可预报，但已不是「新鲜」
    "expired": "stale",    # 7–30d
    "dead": "stale",       # >30d
    "unknown": "none",     # 历元不可解析
}


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def predict_passes_report(
    tle_lines: Sequence[str],
    ground_station: GroundStation,
    hours: float = 24.0,
    min_alt: float = 10.0,
    gnss_dt: Optional[datetime] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> Dict:
    """预测未来 ``hours`` 小时过境，并显式报告时间源 + TLE 新鲜度。

    参数:
        tle_lines:      TLE 文本（标题行可选）。空/坏 → 返回空过境 + status="none"。
        ground_station: 地面站位置。
        hours/min_alt:  与 sat_passes.predict_passes 同。
        gnss_dt:        GNSS UTC datetime（ZDA/RMC 解出）；给了则作为预测起点。
        clock:          无 GNSS 时取系统 UTC 的时钟（callable -> tz-aware）。可注入。

    返回 dict（机器可读，日志/UI 直接展示）::

        {
          "time_source": "gnss" | "system",
          "prediction_start_utc": "ISO...",
          "tle": {"status": "fresh"|"stale"|"none",   # 与 tier 同源（≤24h=fresh）
                  "tier": "fresh"|"usable"|"expired"|"dead"|"unknown",
                  "epoch_utc": ..., "age_days": ..., "threshold_days": ...},
          "n_passes": int,
          "passes": [ {"rise_utc":..., "set_utc":..., "max_alt":...,
                       "rise_az":..., "set_az":..., "duration_s":...}, ... ],
          "predict_error": None | "ExcType: 原因",
        }
    """
    ts = make_timescale()

    # 1) 解析时间基准
    if gnss_dt is not None:
        if gnss_dt.tzinfo is None:
            gnss_dt = gnss_dt.replace(tzinfo=timezone.utc)
        start_time = ts.from_datetime(gnss_dt.astimezone(timezone.utc))
        time_source = "gnss"
    else:
        clk = clock or _default_clock
        now = clk()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        start_time = ts.from_datetime(now.astimezone(timezone.utc))
        time_source = "system"

    # 2) TLE 新鲜度（取 line1；解析失败 → none）
    line1 = next((l.strip() for l in tle_lines if str(l).startswith("1 ")), "")
    fresh = tle_freshness(line1, now=start_time.utc_datetime())
    tier = _tle_tier(line1, now=start_time.utc_datetime())
    acc = _tle_accuracy(tier)
    fresh = dict(fresh, **{
        # status / threshold_days 必须由 tier 的同一阈值源派生：tle_freshness
        # 自带的 7 天阈值会让 3 天龄 TLE 同时是 status="fresh" 与 tier="usable"。
        "status": _STATUS_BY_TIER[tier["tier"]],
        "threshold_days": tier["thresholds"]["fresh_max_hours"] / 24.0,
        "tier": tier["tier"],
        "label": tier["label"],
        "age_days": tier["age_days"],
        "pass_eligible": tier["pass_eligible"],
        "accuracy": acc,
    })

    # 3) 过境预测（坏 TLE → predict_passes 返回 []）
    # 异常必须留原因：依赖缺失 / 坏数据不能伪装成「真无过境」。
    predict_error: Optional[str] = None
    try:
        passes = predict_passes(tle_lines, ground_station,
                                start_time=start_time, hours=hours, min_alt=min_alt)
    except Exception as exc:
        passes = []
        predict_error = f"{type(exc).__name__}: {exc}"

    # 过期/失效 TLE：时刻降为分钟级（不照常输出精确到秒）
    precise = acc["time_precision_s"] <= 1

    def _fmt(t) -> str:
        iso = t.utc_strftime("%Y-%m-%dT%H:%M:%SZ")
        return iso if precise else iso[:16] + "Z"

    pass_rows: List[Dict] = []
    for p in passes:
        pass_rows.append({
            "rise_utc": _fmt(p.rise_time),
            "set_utc": _fmt(p.set_time),
            "max_alt_time_utc": _fmt(p.max_alt_time),
            "max_alt_deg": round(float(p.max_alt), 2),
            "rise_az_deg": round(float(p.rise_az), 1),
            "set_az_deg": round(float(p.set_az), 1),
            "duration_s": round(float(p.duration), 1),
            "sat_name": p.sat_name,
            "approximate": not precise,
            "accuracy_risk": acc["risk"],
        })

    return {
        "time_source": time_source,
        "prediction_start_utc": start_time.utc_strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tle": fresh,
        "accuracy": acc,
        "n_passes": len(pass_rows),
        "passes": pass_rows,
        # None = 预测本身没出错（含「真无过境」）；字符串 = 异常类型与原因。
        "predict_error": predict_error,
    }
