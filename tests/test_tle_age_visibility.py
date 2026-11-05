# SPDX-License-Identifier: MIT
"""TLE 老化可见性：过境结果必须带年龄/分级，面板必须明说「过期/失效」。

修前红：面板走 TimelineEngine，SatellitePass 完全没有 TLE 年龄字段，
表格与提示里看不出用的是 1 年前的星历；过境结果看起来和新 TLE 一样。
"""
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

ISS_NAME = "ISS (ZARYA)"
# 历元 2008-264（2008-09-20 12:25 UTC）
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"


def _fixed_datetime_class(fixed):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    return _FixedDateTime


def test_timeline_marks_expired_tle(monkeypatch):
    """1 年旧的 TLE 必须在结果里带 tle_age_days / tier / 人话。"""
    import mbdsdr_ai.new_spacetime_timeline as nst
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    fixed = datetime(2009, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class(fixed))

    passes = TimelineEngine().compute_passes(
        39.9, 116.4, 0.0, [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=24, min_elevation=10.0, step_sec=60.0,
    )
    assert passes, "固定夹具应有可见过境"
    p = passes[0]
    assert p.tle_age_days is not None and p.tle_age_days > 350, (
        f"过期 TLE 必须带年龄，实际 {p.tle_age_days}")
    assert p.tle_tier == "dead", f"1 年旧 TLE 应判失效，实际 {p.tle_tier}"
    assert p.tle_tier_label == "失效"
    assert "失效" in p.accuracy_note, f"缺少人话提示：{p.accuracy_note}"
    assert p.accuracy_risk == "unusable"


def test_timeline_does_not_flag_fresh_tle(monkeypatch):
    """新鲜 TLE 不得被误标为过期（否则文案会天天报警）。"""
    import mbdsdr_ai.new_spacetime_timeline as nst
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    fixed = datetime(2008, 9, 20, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(nst, "datetime", _fixed_datetime_class(fixed))

    passes = TimelineEngine().compute_passes(
        39.9, 116.4, 0.0, [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=24, min_elevation=10.0, step_sec=60.0,
    )
    assert passes
    p = passes[0]
    assert p.tle_tier == "fresh"
    assert p.accuracy_risk == "low"
    assert p.tle_age_days is not None and p.tle_age_days < 1.0

