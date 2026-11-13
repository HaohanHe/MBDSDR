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
SAMPLE_SITE = GroundStation(45.0, 120.0, 0.0)


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
    rep = predict_passes_report(TLE, SAMPLE_SITE, hours=4, min_alt=5, clock=fixed)
    assert rep["time_source"] == "system"
    assert rep["prediction_start_utc"] == "2024-10-01T12:00:00Z"
    # TLE 新鲜度随报告带出
    assert rep["tle"]["status"] in ("fresh", "stale")
    assert rep["tle"]["age_days"] is not None
    # 示例站对 ISS 在 4h 内应有过境
    assert rep["n_passes"] >= 1


def test_predict_time_source_gnss():
    g = datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(TLE, SAMPLE_SITE, hours=4, min_alt=5, gnss_dt=g)
    assert rep["time_source"] == "gnss"
    assert rep["prediction_start_utc"] == "2024-10-01T12:00:00Z"


def test_predict_no_tle_is_honest_empty():
    fixed = lambda: datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report([], SAMPLE_SITE, hours=4, min_alt=5, clock=fixed)
    assert rep["n_passes"] == 0
    assert rep["passes"] == []
    assert rep["tle"]["status"] == "none"
    # 无 TLE 是「本来就没有」，不是「算的时候出错」——不得混报为异常。
    assert rep["predict_error"] is None


def test_status_and_tier_share_one_threshold():
    """同一份 payload 里 status 与 tier 必须同源，不许 7 天 vs 24 小时并存。

    反证：旧实现 3 天龄 TLE 会同时给出 status="fresh"（7 天阈值）与
    tier="usable"（24 小时阈值）——自相矛盾。
    """
    three_days_later = datetime(2024, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(
        TLE, SAMPLE_SITE, hours=1, min_alt=5, clock=lambda: three_days_later)
    t = rep["tle"]
    assert t["tier"] == "usable"
    assert t["age_days"] == 3.0
    # 超过 24h 就不再是 fresh；阈值天数也必须与 tier 的 24h 同源。
    assert t["status"] == "stale"
    assert t["threshold_days"] == 1.0


def test_status_fresh_within_24h_same_source():
    six_hours_later = datetime(2024, 10, 1, 18, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(
        TLE, SAMPLE_SITE, hours=1, min_alt=5, clock=lambda: six_hours_later)
    t = rep["tle"]
    assert t["tier"] == "fresh"
    assert t["status"] == "fresh"
    assert t["threshold_days"] == 1.0


def test_future_epoch_is_visible_in_report():
    """历元在将来时年龄按 0 处理，但必须显式标出 epoch_in_future。

    反证：从 predict_passes_report 的 tle 字典删掉 epoch_in_future，
    本用例直接 KeyError 变红；把 flag 恒置 False，本用例第一段变红。
    """
    before_epoch = datetime(2024, 9, 30, 0, 0, 0, tzinfo=timezone.utc)
    rep = predict_passes_report(
        TLE, SAMPLE_SITE, hours=1, min_alt=5, clock=lambda: before_epoch)
    t = rep["tle"]
    assert t["epoch_in_future"] is True
    assert t["age_days"] == 0.0
    assert t["status"] == "fresh"

    after_epoch = datetime(2024, 10, 2, 0, 0, 0, tzinfo=timezone.utc)
    rep2 = predict_passes_report(
        TLE, SAMPLE_SITE, hours=1, min_alt=5, clock=lambda: after_epoch)
    assert rep2["tle"]["epoch_in_future"] is False


def test_predict_exception_preserves_reason(monkeypatch):
    """预测抛异常时必须留下原因，不许压成无来源的 n_passes=0。"""
    import mbdsdr_ai.spacetime_predict as sp

    def _boom(*args, **kwargs):
        raise RuntimeError("sgp4 未安装")

    monkeypatch.setattr(sp, "predict_passes", _boom)
    fixed = lambda: datetime(2024, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    rep = sp.predict_passes_report(TLE, SAMPLE_SITE, hours=1, min_alt=5, clock=fixed)
    assert rep["n_passes"] == 0
    assert rep["passes"] == []
    assert rep["predict_error"] is not None
    assert "RuntimeError" in rep["predict_error"]
    assert "sgp4" in rep["predict_error"]
