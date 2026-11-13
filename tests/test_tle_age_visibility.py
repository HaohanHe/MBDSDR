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

import pytest  # noqa: E402

from mbdsdr_ai.tle_integrity import (  # noqa: E402
    AGE_BAND_LABELS,
    accuracy,
    age_band,
    band_note,
)

ISS_NAME = "ISS (ZARYA)"
# 历元 2008-264（2008-09-20 12:25 UTC）
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"


def _pin_engine(fixed):
    """把全局 TimeEngine 钉在固定时刻（F1 之后预测起点只认 TimeEngine）。"""
    from mbdsdr_ai.new_spacetime import get_time_engine
    get_time_engine().set_unix(fixed.timestamp())


def test_timeline_marks_expired_tle(monkeypatch):
    """1 年旧的 TLE 必须在结果里带 tle_age_days / tier / 人话。"""
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    fixed = datetime(2009, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
    _pin_engine(fixed)

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
    from mbdsdr_ai.new_spacetime_timeline import TimelineEngine

    fixed = datetime(2008, 9, 20, 18, 0, 0, tzinfo=timezone.utc)
    _pin_engine(fixed)

    passes = TimelineEngine().compute_passes(
        39.9, 116.4, 0.0, [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=24, min_elevation=10.0, step_sec=60.0,
    )
    assert passes
    p = passes[0]
    assert p.tle_tier == "fresh"
    assert p.accuracy_risk == "low"
    assert p.tle_age_days is not None and p.tle_age_days < 1.0
    # 采样步长必须随结果走，用户才能分清「星历精度」与「采样精度」
    assert p.sampling_step_s == 60.0


# ---------------------------------------------------------------------------
# 年龄判定表（≤1 天 / ≤7 天 / 7–30 天 / 31–90 天 / >90 天 / 未知）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("age,band,label_key,risk,precision", [
    (0.4, "fresh", "fresh", "low", 1),
    (5.0, "usable", "usable", "moderate", 1),
    (20.0, "expired", "expired", "high", 60),
    (60.0, "stale", "stale", "unusable", 60),
    (365.0, "dead", "dead", "unusable", 60),
    (None, "unknown", "unknown", "unusable", 60),
])
def test_age_band_table(age, band, label_key, risk, precision):
    tier = {"fresh": "fresh", "usable": "usable", "expired": "expired",
            "stale": "dead", "dead": "dead", "unknown": "unknown"}[band]
    acc = accuracy({"tier": tier, "age_days": age})
    assert acc["band"] == band, f"年龄 {age} 档位判错：{acc['band']}"
    assert acc["band_label"] == AGE_BAND_LABELS[label_key]
    assert acc["risk"] == risk
    assert acc["time_precision_s"] == precision
    assert acc["note"] == band_note(band, age), "文案必须来自唯一判定表"


def test_365_days_hits_strongest_warning():
    """过期 365 天必须命中最强警示那句（不是和 31 天共用一句）。"""
    dead = band_note("dead", 365.0)
    stale = band_note("stale", 60.0)
    assert dead != stale
    assert "轨道已严重不可信" in dead
    assert "禁止用于跟踪/调谐" in dead
    assert "必须刷新星历" in dead
    # 60 秒是预测时刻精度，不是卫星位置精度——两个档位都必须写清
    assert "预测时刻精度" in dead and "不是卫星位置精度" in dead
    assert "预测时刻精度" in stale

