# SPDX-License-Identifier: MIT
"""D3：TLE 可信时长按当天 Kp/F10.7 只收紧（在 freshness/accuracy 之上）。

判据：
  1. 静日 vs 磁暴日：同一份 TLE、同一时刻，磁暴日的档位更严格（不得更宽松）；
  2. 改修正表某一档系数，有效阈值与档位必须随之变化；
  3. 缺环境输入 / 输入过期 / 超表范围：逐字段回落基线，且显式标
     ``environment.status="uncorrected"``——绝不按最坏值猜；
  4. 结果能说明依据哪一刻、哪个来源的 Kp/F10.7；
  5. 既有四档阈值常量与 TRACKING_MODES 一个字不改。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.environment_facts import (  # noqa: E402
    DEFAULT_TABLE_PATH,
    EnvironmentFact,
    SpaceWeatherCorrectionTable,
)
from mbdsdr_ai.tle_integrity import (  # noqa: E402
    DEAD_STALE_MAX_DAYS,
    EXPIRED_MAX_DAYS,
    FRESH_MAX_HOURS,
    TRACKING_MODES,
    USABLE_MAX_DAYS,
    accuracy,
    accuracy_with_environment,
    freshness,
    freshness_with_environment,
)

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

_TIER_SEVERITY = {"fresh": 0, "usable": 1, "expired": 2, "dead": 3,
                  "unknown": 4}


def _line1_at(age_days: float) -> str:
    """构造历元年龄恰为 age_days 的 TLE line1（列位与真实 TLE 一致）。"""
    epoch = NOW - timedelta(days=age_days)
    jan1 = datetime(epoch.year, 1, 1, tzinfo=timezone.utc)
    doy = (epoch - jan1).total_seconds() / 86400.0 + 1.0
    return (f"1 25544U 98067A   {epoch.year % 100:02d}{doy:012.8f}"
            "  .00016553  00000-0  10270-3 0  2897")


def _fact(name: str, value: float, at: datetime,
          source: str = "NOAA SWPC", max_age_s: float = 6 * 3600.0
          ) -> EnvironmentFact:
    return EnvironmentFact(name=name, value=value, at_utc=at.strftime(UTC_FMT),
                           source=source, max_age_s=max_age_s)


def _correction(kp: float, f107: float, table=None):
    tbl = table or SpaceWeatherCorrectionTable.load()
    return tbl.correction(
        _fact("kp_index", kp, NOW - timedelta(minutes=30)),
        _fact("f107_sfu", f107, NOW - timedelta(hours=12), max_age_s=48 * 3600),
        now=NOW,
    )


# ---------------------------------------------------------------------------
# 1. 缺环境输入 → 未校正基线 + 显式标注
# ---------------------------------------------------------------------------
def test_missing_environment_falls_back_to_baseline():
    line1 = _line1_at(0.5)
    base = freshness(line1, now=NOW)
    env = freshness_with_environment(line1, now=NOW, environment=None)

    assert env["tier"] == base["tier"] == "fresh"
    assert env["label"] == base["label"]
    assert env["pass_eligible"] is base["pass_eligible"] is True
    assert env["tightened"] is False
    corr = env["environment"]
    assert corr["status"] == "uncorrected"
    assert corr["applied"] is False
    assert corr["factor"] == 1.0
    assert "未注入" in corr["note"]
    # 回落时有效阈值必须逐键等于基线（不是「接近」）
    assert env["effective_thresholds"] == corr["effective_thresholds_hours"]
    assert env["effective_thresholds"]["fresh_max_hours"] == FRESH_MAX_HOURS


def test_missing_one_input_is_uncorrected_not_worst_case():
    line1 = _line1_at(20.0)
    tbl = SpaceWeatherCorrectionTable.load()
    # 只给 Kp=9（若按最坏值猜会大幅收紧），但缺 F10.7 → 必须回落基线
    corr = tbl.correction(
        _fact("kp_index", 9.0, NOW - timedelta(minutes=10)), None, now=NOW)
    assert corr.status == "uncorrected" and corr.factor == 1.0
    assert any("f107" in m for m in corr.missing)

    base = freshness(line1, now=NOW)
    env = freshness_with_environment(line1, now=NOW, environment=corr)
    assert env["tier"] == base["tier"]
    assert env["effective_thresholds"]["fresh_max_hours"] == FRESH_MAX_HOURS
    assert env["environment"]["status"] == "uncorrected"


def test_stale_kp_is_uncorrected_not_guessed():
    line1 = _line1_at(20.0)
    tbl = SpaceWeatherCorrectionTable.load()
    corr = tbl.correction(
        _fact("kp_index", 8.0, NOW - timedelta(hours=9)),  # 上限 6h
        _fact("f107_sfu", 250.0, NOW - timedelta(hours=12),
              max_age_s=48 * 3600),
        now=NOW,
    )
    assert corr.status == "uncorrected"
    assert any("过期" in m for m in corr.missing)
    assert freshness_with_environment(
        line1, now=NOW, environment=corr)["tier"] == \
        freshness(line1, now=NOW)["tier"]


def test_out_of_range_input_is_uncorrected():
    line1 = _line1_at(20.0)
    tbl = SpaceWeatherCorrectionTable.load()
    corr = tbl.correction(
        _fact("kp_index", 9.5, NOW - timedelta(minutes=5)),  # 表外 Kp>9
        _fact("f107_sfu", 120.0, NOW - timedelta(hours=12),
              max_age_s=48 * 3600),
        now=NOW,
    )
    assert corr.status == "uncorrected"
    assert any("超出" in m for m in corr.missing)


# ---------------------------------------------------------------------------
# 2. 静日 vs 磁暴日：方向正确、只能更严格
# ---------------------------------------------------------------------------
def test_quiet_vs_storm_tightens_tier():
    line1 = _line1_at(20.0 / 24.0)  # 20h：基线 fresh
    base = freshness(line1, now=NOW)
    assert base["tier"] == "fresh"

    quiet = freshness_with_environment(
        line1, now=NOW, environment=_correction(2.0, 80.0))
    storm = freshness_with_environment(
        line1, now=NOW, environment=_correction(7.0, 180.0))

    assert quiet["tier"] == "fresh"
    assert quiet["tightened"] is False
    assert storm["tier"] == "usable", storm
    assert storm["tightened"] is True
    assert storm["pass_eligible"] is True  # usable 仍可用，但不再算新鲜

    # 有效阈值只小不大，且磁暴日的阈值严格更短
    assert quiet["effective_thresholds"]["fresh_max_hours"] == 24.0
    assert storm["effective_thresholds"]["fresh_max_hours"] < 24.0
    assert (storm["effective_thresholds"]["fresh_max_hours"]
            < quiet["effective_thresholds"]["fresh_max_hours"])


def test_environment_never_extends_thresholds_or_eligibility():
    quiet = _correction(2.0, 80.0)
    storm = _correction(9.0, 350.0)
    for age in (0.0, 0.4, 0.9, 1.1, 3.0, 6.9, 7.1, 20.0, 29.0, 31.0,
                60.0, 89.0, 91.0, 400.0):
        line1 = _line1_at(age)
        base = freshness(line1, now=NOW)
        for corr in (quiet, storm):
            env = freshness_with_environment(line1, now=NOW, environment=corr)
            assert _TIER_SEVERITY[env["tier"]] >= \
                _TIER_SEVERITY[base["tier"]], (age, env["tier"], base["tier"])
            assert not (env["pass_eligible"] and not base["pass_eligible"])
            baseline = env["environment"]["base_thresholds_hours"]
            for key, eff_val in env["effective_thresholds"].items():
                assert eff_val <= baseline[key], (age, key)


def test_unknown_epoch_stays_unknown_even_with_storm():
    env = freshness_with_environment(
        "1 not-a-tle", now=NOW, environment=_correction(9.0, 350.0))
    assert env["tier"] == "unknown"
    assert env["tightened"] is False
    assert env["environment"]["status"] == "corrected"


# ---------------------------------------------------------------------------
# 3. 改表某一档系数 → 结果必须随之变化
# ---------------------------------------------------------------------------
def test_changing_a_bin_factor_changes_result():
    payload = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    original = SpaceWeatherCorrectionTable.from_dict(payload)
    # 命中 kp_bins[2]（max 5.0）与 f107_bins[1]（max 150）
    kp_fact = _fact("kp_index", 4.5, NOW - timedelta(minutes=20))
    f107_fact = _fact("f107_sfu", 120.0, NOW - timedelta(hours=12),
                      max_age_s=48 * 3600)
    before = original.correction(kp_fact, f107_fact, now=NOW)

    payload["kp_bins"][2]["factor"] = 0.2
    modified = SpaceWeatherCorrectionTable.from_dict(payload)
    after = modified.correction(kp_fact, f107_fact, now=NOW)

    assert before.factor == pytest.approx(0.8 * 0.95)
    assert after.factor == pytest.approx(0.2 * 0.95)

    line1 = _line1_at(5.0 / 24.0)  # 5h
    env_before = freshness_with_environment(line1, now=NOW, environment=before)
    env_after = freshness_with_environment(line1, now=NOW, environment=after)
    assert env_before["tier"] == "fresh"
    assert env_after["tier"] == "usable", env_after
    assert env_after["effective_thresholds"]["fresh_max_hours"] != \
        env_before["effective_thresholds"]["fresh_max_hours"]


# ---------------------------------------------------------------------------
# 4. 结果说明「依据哪一刻、哪个来源」
# ---------------------------------------------------------------------------
def test_result_carries_moment_and_source():
    corr = _correction(6.0, 180.0)
    env = freshness_with_environment(_line1_at(2.0), now=NOW, environment=corr)
    info = env["environment"]
    assert info["status"] == "corrected"
    assert info["kp"]["at_utc"] == (NOW - timedelta(minutes=30)).strftime(UTC_FMT)
    # 测量出处（哪一刻、哪个数据源）与档位依据各留一份，互不覆盖
    assert info["inputs"]["kp_index"]["source"] == "NOAA SWPC"
    assert info["inputs"]["kp_index"]["at_utc"] == \
        (NOW - timedelta(minutes=30)).strftime(UTC_FMT)
    assert "NOAA G-scale" in info["kp"]["source"]
    assert info["kp"]["label"] == "G2 地磁暴"
    assert info["f107"]["at_utc"] == (NOW - timedelta(hours=12)).strftime(UTC_FMT)
    assert "经验模型，非实测密度" in info["disclaimer"]
    assert info["factor"] == pytest.approx(0.65 * 0.85)


# ---------------------------------------------------------------------------
# 5. accuracy 层：风险不降低、时刻精度不提高
# ---------------------------------------------------------------------------
def test_accuracy_tightens_with_environment():
    line1 = _line1_at(20.0 / 24.0)  # 20h
    base_acc = accuracy(freshness(line1, now=NOW))
    assert base_acc["risk"] == "low"

    quiet_env = freshness_with_environment(
        line1, now=NOW, environment=_correction(2.0, 80.0))
    quiet_acc = accuracy_with_environment(quiet_env)
    assert quiet_acc["risk"] == base_acc["risk"]
    assert quiet_acc["band"] == base_acc["band"]
    assert quiet_acc["note"] == base_acc["note"]

    storm_env = freshness_with_environment(
        line1, now=NOW, environment=_correction(7.0, 180.0))
    storm_acc = accuracy_with_environment(storm_env)
    order = {"low": 0, "moderate": 1, "high": 2, "unusable": 3}
    assert order[storm_acc["risk"]] > order[base_acc["risk"]]
    assert storm_acc["time_precision_s"] >= base_acc["time_precision_s"]
    assert storm_acc["base_risk"] == "low"
    assert storm_acc["band"] == "usable"


def test_accuracy_uncorrected_equals_baseline():
    line1 = _line1_at(20.0 / 24.0)
    env = freshness_with_environment(line1, now=NOW, environment=None)
    acc = accuracy_with_environment(env)
    base = accuracy(freshness(line1, now=NOW))
    for key in ("risk", "band", "band_label", "time_precision_s", "note",
                "tier", "pass_eligible"):
        assert acc[key] == base[key], key


# ---------------------------------------------------------------------------
# 6. 既有四档阈值与跟踪放行表一个字不改
# ---------------------------------------------------------------------------
def test_existing_thresholds_and_tracking_modes_unchanged():
    assert FRESH_MAX_HOURS == 24.0
    assert USABLE_MAX_DAYS == 7.0
    assert EXPIRED_MAX_DAYS == 30.0
    assert DEAD_STALE_MAX_DAYS == 90.0
    assert TRACKING_MODES["expired"]["gimbal"] == "deny"
    assert TRACKING_MODES["expired"]["tune"] == "confirm"
    assert TRACKING_MODES["dead"]["pass_list"] == "deny"

    # 基线 freshness 的 thresholds 字段仍报原值（环境层不覆盖它）
    base = freshness(_line1_at(20.0), now=NOW)
    assert base["thresholds"] == {
        "fresh_max_hours": 24.0, "usable_max_days": 7.0,
        "expired_max_days": 30.0,
    }
