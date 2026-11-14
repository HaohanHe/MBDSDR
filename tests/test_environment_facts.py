# SPDX-License-Identifier: MIT
"""环境事实层判据：缺数据即 unknown、只收紧不放宽、改表必变、快照可回放。"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MBDSDR_OFFLINE", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.environment_facts import (  # noqa: E402
    DEFAULT_TABLE_PATH,
    FACT_F107,
    FACT_KP_INDEX,
    CorrectionTableError,
    EnvironmentFact,
    EnvironmentSnapshot,
    SpaceWeatherCorrectionTable,
    load_space_weather_table,
    space_weather_correction,
)

NOW = datetime(2026, 11, 14, 6, 0, 0, tzinfo=timezone.utc)
AT = "2026-11-14T03:00:00Z"


def _kp(value=2.0, at=AT, source="NOAA SWPC Kp") -> EnvironmentFact:
    return EnvironmentFact(FACT_KP_INDEX, value=value, unit="Kp",
                           at_utc=at, source=source, max_age_s=6 * 3600)


def _f107(value=120.0, at=AT, source="NOAA SWPC F10.7") -> EnvironmentFact:
    return EnvironmentFact(FACT_F107, value=value, unit="sfu",
                           at_utc=at, source=source, max_age_s=48 * 3600)


def _snapshot(kp=None, f107=None) -> EnvironmentSnapshot:
    return EnvironmentSnapshot.capture(
        [f for f in (kp, f107) if f is not None], captured_at=NOW)


# ---------------------------------------------------------------------------
# 事实层：缺一不可
# ---------------------------------------------------------------------------
def test_fact_requires_value_time_and_source():
    assert _kp().usable(NOW)
    assert not EnvironmentFact(FACT_KP_INDEX, value=2.0, at_utc=AT).usable(NOW)
    assert not EnvironmentFact(FACT_KP_INDEX, value=2.0, source="x").usable(NOW)
    assert not EnvironmentFact(FACT_KP_INDEX, at_utc=AT, source="x").usable(NOW)
    # 时刻无法解析同样按 unknown，绝不猜
    assert not EnvironmentFact(FACT_KP_INDEX, value=2.0, at_utc="not-a-time",
                                source="x").usable(NOW)


def test_fact_goes_stale_after_max_age():
    old = _kp(at=(NOW - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert old.status(NOW) == "stale"
    assert not old.usable(NOW)
    assert "过期" in old.detail(NOW)


def test_snapshot_roundtrip_recomputes_same_result(tmp_path):
    snap = _snapshot(_kp(3.0), _f107(180.0))
    path = tmp_path / "env.json"
    snap.save(path)

    loaded = EnvironmentSnapshot.load(path)
    assert loaded.captured_at_utc == "2026-11-14T06:00:00Z"
    a = space_weather_correction(snap, now=NOW)
    b = space_weather_correction(loaded, now=NOW)
    assert a.to_dict() == b.to_dict(), "同一快照回放必须得到同一结论"
    assert a.factor < 1.0 and b.factor == a.factor


def test_bridge_from_space_weather_snapshot():
    """复用既有采集快照：成功项进修正，失败项保持 unknown。"""
    payload = {
        "schema": 1,
        "taken_at": "2026-11-14T06:00:00Z",
        "samples": {
            "kp": {"value": 6.0, "observed_at": "2026-11-14T03:00:00Z",
                   "source": "NOAA SWPC planetary K index", "error": ""},
            "f107": {"value": 180.0, "observed_at": "2026-11-13T20:00:00Z",
                     "source": "NOAA SWPC F10.7 cm flux", "error": ""},
        },
    }
    snap = EnvironmentSnapshot.from_space_weather(payload)
    result = space_weather_correction(snap, now=NOW)
    assert result.applied and result.factor < 1.0

    failed = json.loads(json.dumps(payload))
    failed["samples"]["f107"] = {
        "value": None, "observed_at": "", "error": "URLError: timed out"}
    result2 = space_weather_correction(
        EnvironmentSnapshot.from_space_weather(failed), now=NOW)
    assert result2.status == "uncorrected" and result2.factor == 1.0
    assert any("缺测量值" in m for m in result2.missing)


# ---------------------------------------------------------------------------
# 修正表：数据文件驱动、只收紧
# ---------------------------------------------------------------------------
def test_default_table_is_data_driven_and_sourced():
    table = load_space_weather_table()
    assert table.path == str(DEFAULT_TABLE_PATH)
    assert table.model == "empirical"
    assert "非实测密度" in table.disclaimer
    for bins in (table.kp_bins, table.f107_bins):
        assert bins
        for item in bins:
            assert 0.0 < item.factor <= 1.0
            assert item.source.strip(), "每档必须标来源"
            assert item.applicability.strip(), "每档必须标适用条件"
    maxes = [b.max_value for b in table.kp_bins]
    assert maxes == sorted(maxes) and len(set(maxes)) == len(maxes)


def test_quiet_vs_storm_direction_and_magnitude():
    table = load_space_weather_table()
    quiet = table.correction(_kp(2.0), _f107(80.0), now=NOW)
    storm = table.correction(_kp(7.0), _f107(180.0), now=NOW)

    assert quiet.applied and quiet.factor == pytest.approx(1.0)
    assert storm.applied and storm.factor < quiet.factor
    assert storm.tighten_hours(24.0) < quiet.tighten_hours(24.0)
    # 静日不得放宽任何基线阈值
    for base in (24.0, 7 * 24.0, 30 * 24.0, 90 * 24.0):
        assert quiet.tighten_hours(base) <= base
        assert storm.tighten_hours(base) <= base
    assert "经验模型" in storm.note and "非实测密度" in storm.note


def test_missing_environment_falls_back_uncorrected_not_worst_case():
    table = load_space_weather_table()
    only_kp = table.correction(_kp(9.0), None, now=NOW)
    assert only_kp.status == "uncorrected"
    assert only_kp.factor == 1.0
    assert any("未提供" in m for m in only_kp.missing)
    assert "未校正" in only_kp.note
    assert only_kp.tighten_hours(24.0) == pytest.approx(24.0)

    stale_f107 = _f107(at=(NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    stale = table.correction(_kp(9.0), stale_f107, now=NOW)
    assert stale.status == "uncorrected" and stale.factor == 1.0
    assert any("过期" in m for m in stale.missing)

    empty = space_weather_correction(EnvironmentSnapshot(), now=NOW)
    assert empty.status == "uncorrected" and empty.factor == 1.0


def test_out_of_range_is_uncorrected_not_clamped_to_worst_bin():
    table = load_space_weather_table()
    result = table.correction(_kp(2.0), _f107(999.0), now=NOW)
    assert result.status == "uncorrected"
    assert result.factor == 1.0
    assert any("超出" in m for m in result.missing)


def test_changing_one_bin_changes_result(tmp_path):
    payload = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    base = SpaceWeatherCorrectionTable.from_dict(payload)
    # Kp=3.5 命中第二档（max 4.0），改这一档系数必须改变结果
    before = base.correction(_kp(3.5), _f107(120.0), now=NOW).factor

    changed = json.loads(json.dumps(payload))
    changed["kp_bins"][1]["factor"] = 0.42
    table2 = SpaceWeatherCorrectionTable.from_dict(changed)
    after = table2.correction(_kp(3.5), _f107(120.0), now=NOW).factor

    assert before != after, "改表里某一档系数，结果必须随之变化"
    assert after < before, "系数调小只能让可信时长更短"


def test_table_rejects_widening_coefficient():
    payload = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    payload["kp_bins"][0]["factor"] = 1.5
    with pytest.raises(CorrectionTableError):
        SpaceWeatherCorrectionTable.from_dict(payload)


def test_every_combination_only_tightens():
    """穷举档位组合：任何已校正结果的 factor 都 ≤ 1，时长只短不长。"""
    table = load_space_weather_table()
    for kp in (0.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0):
        for f107 in (70.0, 100.0, 150.0, 200.0, 250.0, 300.0, 400.0):
            correction = table.correction(_kp(kp), _f107(f107), now=NOW)
            assert 0.0 < correction.factor <= 1.0
            for base in (24.0, 7 * 24.0, 30 * 24.0, 90 * 24.0):
                assert correction.tighten_hours(base) <= base + 1e-12


def test_effective_thresholds_never_widen():
    base = {"fresh_max_hours": 24.0, "usable_max_days": 168.0}
    quiet = load_space_weather_table().correction(_kp(1.0), _f107(70.0), now=NOW)
    storm = load_space_weather_table().correction(_kp(8.0), _f107(300.0), now=NOW)
    for result in (quiet, storm):
        out = result.effective_thresholds(base)
        for key, value in base.items():
            assert out[key] <= value
