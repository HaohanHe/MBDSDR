# SPDX-License-Identifier: MIT
"""
Q2：TLE 新鲜度三态（fresh/stale/none）+ 过境预测时间源显式化（确定性离线测试）

不联网、不依赖实时钟：注入固定 now / 固定预测起点。
覆盖：
  - parse_tle_epoch / tle_freshness 三态；
  - predict_passes_report：gnss_dt → time_source="gnss"；否则注入时钟 → "system"；
  - 无 TLE → n_passes=0、tle.status="none"（诚实空态）。

运行：python3 -m pytest mbdsdr_ai/tests/test_tle_freshness_predict.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.new_spacetime_tle import (  # noqa: E402
    parse_tle_epoch,
    tle_freshness,
    TLE_FRESHNESS_MAX_DAYS,
)
from mbdsdr_ai.spacetime_predict import predict_passes_report  # noqa: E402
from mbdsdr_ai.sat_passes import GroundStation  # noqa: E402

# ISS (ZARYA) TLE，历元 24275.5 = 2024-10-01 12:00 UTC。
L1 = ("1 25544U 98067A   24275.50000000  .00016717  00000-0  10270-3 0  9000")
L2 = ("2 25544  51.6400 292.0000 0001333  90.0000 270.0000 15.50000000    000")
TLE = ["ISS (ZARYA)", L1, L2]
CHANGCHUN = GroundStation(43.82, 125.32, 0.0)


def test_parse_epoch():
    ep = parse_tle_epoch(L1)
    assert ep is not None
    assert ep.year == 2024 and ep.month == 10 and ep.day == 1
    assert ep.hour == 12


def test_fresh_stale_none_three_states():
    fresh_now = datetime(2024, 10, 2, 0, 0, 0, tzinfo=timezone.utc)   # age ~0.5d
    f = tle_freshness(L1, now=fresh_now)
    assert f["status"] == "fresh"
    assert f["threshold_days"] == TLE_FRESHNESS_MAX_DAYS
    assert f["age_days"] == 0.5

    stale_now = datetime(2024, 12, 1, 0, 0, 0, tzinfo=timezone.utc)  # age ~61d
    s = tle_freshness(L1, now=stale_now)
    assert s["status"] == "stale"
    assert s["age_days"] > TLE_FRESHNESS_MAX_DAYS

    n = tle_freshness("not a tle line", now=fresh_now)
    assert n["status"] == "none"
    assert n["age_days"] is None and n["epoch_utc"] is None


def test_predict_time_source_system_with_injected_clock():
    fixed = lambda: datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(TLE, CHANGCHUN, hours=4, min_alt=5, clock=fixed)
    assert rep["time_source"] == "system"
    assert rep["prediction_start_utc"] == "2024-10-01T12:00:00Z"
    # TLE 新鲜度随报告带出
    assert rep["tle"]["status"] in ("fresh", "stale")
    assert rep["tle"]["age_days"] is not None
    # 长春站对 ISS 在 4h 内应有过境
    assert rep["n_passes"] >= 1


def test_predict_time_source_gnss():
    g = datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(TLE, CHANGCHUN, hours=4, min_alt=5, gnss_dt=g)
    assert rep["time_source"] == "gnss"
    assert rep["prediction_start_utc"] == "2024-10-01T12:00:00Z"


def test_predict_no_tle_is_honest_empty():
    fixed = lambda: datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report([], CHANGCHUN, hours=4, min_alt=5, clock=fixed)
    assert rep["n_passes"] == 0
    assert rep["passes"] == []
    assert rep["tle"]["status"] == "none"
