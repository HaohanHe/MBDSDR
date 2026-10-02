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

2. **TLE 新鲜度联动**：复用 ``new_spacetime_tle.tle_freshness``（统一阈值
   ``TLE_FRESHNESS_MAX_DAYS``），在预测报告里报告历元、年龄（天）与
   fresh/stale/none 三态。stale 仍出预报，但显式告警精度下降；none 不出假预报。

红线：无 TLE / 历元无法解析时返回空过境 + ``status="none"``，绝不伪造过境。
本模块只是 :mod:`mbdsdr_ai.sat_passes.predict_passes` 的薄包装，轨道传播仍走
skyfield/SGP4，不另写传播器。
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

__all__ = ["predict_passes_report", "TLE_FRESHNESS_MAX_DAYS"]


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
          "tle": {"status": "fresh"|"stale"|"none",
                  "epoch_utc": ..., "age_days": ..., "threshold_days": ...},
          "n_passes": int,
          "passes": [ {"rise_utc":..., "set_utc":..., "max_alt":...,
                       "rise_az":..., "set_az":..., "duration_s":...}, ... ],
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

    # 3) 过境预测（坏 TLE → predict_passes 返回 []）
    try:
        passes = predict_passes(tle_lines, ground_station,
                                start_time=start_time, hours=hours, min_alt=min_alt)
    except Exception:
        passes = []

    pass_rows: List[Dict] = []
    for p in passes:
        pass_rows.append({
            "rise_utc": p.rise_time.utc_strftime("%Y-%m-%dT%H:%M:%SZ"),
            "set_utc": p.set_time.utc_strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max_alt_time_utc": p.max_alt_time.utc_strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max_alt_deg": round(float(p.max_alt), 2),
            "rise_az_deg": round(float(p.rise_az), 1),
            "set_az_deg": round(float(p.set_az), 1),
            "duration_s": round(float(p.duration), 1),
            "sat_name": p.sat_name,
        })

    return {
        "time_source": time_source,
        "prediction_start_utc": start_time.utc_strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tle": fresh,
        "n_passes": len(pass_rows),
        "passes": pass_rows,
    }
