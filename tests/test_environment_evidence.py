# SPDX-License-Identifier: MIT
"""D4：环境证据快照落盘 + 只读复算（同一时点可重算，缺项即红）。

判据：
  1. 同一份快照隔一段时间再跑，结果逐字段一致（复算只看 captured_at_utc，
     不看「现在」）；
  2. 缺环境输入不是崩溃，也不是回填典型值：对应层显式 uncorrected 并写明
     缺什么，复算仍可对拍通过；
  3. 记录被改动 / 结构缺项：复算必失败（缺项即红）；
  4. 修正表系数随快照固化——改磁盘上的表不影响旧快照复算。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.environment_evidence import (  # noqa: E402
    EVIDENCE_SCHEMA,
    capture,
    load,
    replay,
    save,
)
from mbdsdr_ai.environment_facts import (  # noqa: E402
    DEFAULT_TABLE_PATH,
    FACT_F107,
    FACT_HUMIDITY,
    FACT_KP_INDEX,
    FACT_PRESSURE,
    FACT_TEMPERATURE,
    EnvironmentFact,
    EnvironmentSnapshot,
    SpaceWeatherCorrectionTable,
)
from mbdsdr_ai.gnss_environment import IonosphereParams  # noqa: E402

REF = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

STATION = {"lat_deg": 31.2, "lon_deg": 121.5, "height_m": 50.0}
SATS = [{"az_deg": 30.0, "el_deg": 45.0}, {"az_deg": 120.0, "el_deg": 15.0}]
T_GPST = 432000.0


def _iso(dt: datetime) -> str:
    return dt.strftime(UTC_FMT)


def _line1_at(ref: datetime, age_days: float) -> str:
    epoch = ref - timedelta(days=age_days)
    jan1 = datetime(epoch.year, 1, 1, tzinfo=timezone.utc)
    doy = (epoch - jan1).total_seconds() / 86400.0 + 1.0
    return (f"1 25544U 98067A   {epoch.year % 100:02d}{doy:012.8f}"
            "  .00016553  00000-0  10270-3 0  2897")


def _snapshot(ref: datetime, kp=7.0, f107=180.0, weather=True):
    facts = [
        EnvironmentFact(name=FACT_KP_INDEX, value=kp, unit="Kp",
                        at_utc=_iso(ref - timedelta(minutes=30)),
                        source="NOAA SWPC", max_age_s=6 * 3600),
        EnvironmentFact(name=FACT_F107, value=f107, unit="sfu",
                        at_utc=_iso(ref - timedelta(hours=12)),
                        source="NOAA SWPC", max_age_s=48 * 3600),
    ]
    if weather:
        at = _iso(ref - timedelta(minutes=10))
        facts += [
            EnvironmentFact(name=FACT_PRESSURE, value=1013.25, unit="hPa",
                            at_utc=at, source="station-baro", max_age_s=3600),
            EnvironmentFact(name=FACT_TEMPERATURE, value=20.0, unit="degC",
                            at_utc=at, source="station-thermo", max_age_s=3600),
            EnvironmentFact(name=FACT_HUMIDITY, value=60.0, unit="pct",
                            at_utc=at, source="station-hygro", max_age_s=3600),
        ]
    return EnvironmentSnapshot.capture(facts, captured_at=ref)


def _iono(ref: datetime) -> IonosphereParams:
    return IonosphereParams(
        alpha=(1.1e-8, 1.5e-8, -5.9e-8, -5.9e-8),
        beta=(8.8e4, -1.9e5, 1.3e5, -1.3e5),
        at_utc=_iso(ref - timedelta(hours=1)),
        source="GPS LNAV subframe 4",
    )


def _evidence(ref=REF, **kwargs):
    return capture(
        _snapshot(ref, **{k: kwargs.pop(k) for k in list(kwargs)
                          if k in ("kp", "f107", "weather")}),
        tle_line1=_line1_at(ref, 20.0 / 24.0),
        ionosphere=_iono(ref),
        station=STATION,
        satellites=SATS,
        t_gpst_s=T_GPST,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. 落盘 → 只读复算，逐字段一致
# ---------------------------------------------------------------------------
def test_save_load_replay_roundtrip(tmp_path):
    ev = _evidence()
    assert ev["schema"] == EVIDENCE_SCHEMA
    assert ev["result"]["correction"]["status"] == "corrected"
    assert ev["result"]["gnss"]["status"] == "corrected"
    assert ev["result"]["tle"]["freshness"]["tightened"] is True
    assert ev["status"]["complete"] is True

    path = save(tmp_path / "env.json", ev)
    problems, summary = replay(load(path))
    assert problems == [], problems
    assert REF.strftime(UTC_FMT) in summary


def test_replay_uses_captured_time_not_wall_clock(tmp_path):
    """年初的旧快照在今天复算仍按当时时刻判定（Kp 不会因时间流逝变 stale）。"""
    past = datetime(2026, 1, 5, 0, 0, 0, tzinfo=timezone.utc)
    ev = _evidence(ref=past)
    assert ev["result"]["correction"]["status"] == "corrected"
    assert ev["result"]["gnss"]["ionosphere"]["status"] == "known"
    path = save(tmp_path / "past.json", ev)
    problems, _ = replay(load(path))
    assert problems == [], problems


# ---------------------------------------------------------------------------
# 2. 缺环境输入：显式 uncorrected，不回填、不崩溃
# ---------------------------------------------------------------------------
def test_missing_space_weather_is_labeled_not_failed(tmp_path):
    snap = EnvironmentSnapshot.capture(
        [EnvironmentFact(name=FACT_KP_INDEX, value=7.0, unit="Kp",
                         at_utc=_iso(REF - timedelta(minutes=10)),
                         source="NOAA SWPC", max_age_s=6 * 3600)],
        captured_at=REF)
    ev = capture(snap, tle_line1=_line1_at(REF, 0.5))
    assert ev["result"]["correction"]["status"] == "uncorrected"
    assert ev["result"]["correction"]["factor"] == 1.0
    assert any("space_weather" in item
               for item in ev["status"]["uncorrected"])
    assert any("f107" in item for item in ev["status"]["uncorrected"])
    # 缺项必须可复算（复算的是「已知 + 显式未校正」，不是猜一个值）
    path = save(tmp_path / "missing.json", ev)
    problems, _ = replay(load(path))
    assert problems == [], problems


def test_missing_weather_marks_gnss_partial(tmp_path):
    snap = _snapshot(REF, weather=False)
    ev = capture(snap, ionosphere=_iono(REF), station=STATION,
                 satellites=SATS, t_gpst_s=T_GPST)
    budget = ev["result"]["gnss"]
    assert budget["status"] == "partial"
    assert budget["residual_sigma_m"] is None
    assert budget["troposphere"]["inputs"][FACT_PRESSURE]["status"] == "unknown"
    assert any("gnss" in item for item in ev["status"]["uncorrected"])
    problems, _ = replay(load(save(tmp_path / "partial.json", ev)))
    assert problems == [], problems


# ---------------------------------------------------------------------------
# 3. 被改动 / 结构缺项：复算必失败
# ---------------------------------------------------------------------------
def test_tampered_result_fails_replay(tmp_path):
    ev = _evidence()
    ev["result"]["tle"]["freshness"]["tier"] = "fresh"
    path = save(tmp_path / "tampered.json", ev)
    problems, _ = replay(load(path))
    assert any("result.tle.freshness.tier" in p for p in problems), problems


def test_missing_required_key_fails_replay(tmp_path):
    ev = _evidence()
    del ev["inputs"]
    path = save(tmp_path / "broken.json", ev)
    problems, summary = replay(load(path))
    assert any("缺必需字段 inputs" in p for p in problems), problems
    assert "缺项即红" in summary


def test_capture_requires_reference_time():
    snap = EnvironmentSnapshot(facts={}, captured_at_utc=None)
    with pytest.raises(ValueError, match="captured_at_utc"):
        capture(snap)


def test_cli_replay_exit_codes(tmp_path):
    cli = os.path.join(_ROOT, "scripts", "replay_environment_evidence.py")
    good = save(tmp_path / "good.json", _evidence())
    ev = _evidence()
    ev["result"]["correction"]["status"] = "uncorrected"
    bad = save(tmp_path / "bad.json", ev)

    ok = subprocess.run([sys.executable, cli, str(good)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "[PASS]" in ok.stdout

    fail = subprocess.run([sys.executable, cli, str(bad)],
                          capture_output=True, text=True)
    assert fail.returncode == 1
    assert "[FAIL]" in fail.stdout


# ---------------------------------------------------------------------------
# 4. 修正表系数随快照固化
# ---------------------------------------------------------------------------
def test_embedded_table_freezes_coefficients(tmp_path):
    payload = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    payload["kp_bins"][2]["factor"] = 0.2
    custom = SpaceWeatherCorrectionTable.from_dict(payload)

    ev = capture(_snapshot(REF, kp=4.5), tle_line1=_line1_at(REF, 5.0 / 24.0),
                 table=custom)
    assert ev["inputs"]["correction_table"]["kp_bins"][2]["factor"] == 0.2
    assert ev["result"]["correction"]["factor"] == pytest.approx(0.2 * 0.85)

    problems, _ = replay(load(save(tmp_path / "custom.json", ev)))
    assert problems == [], problems
    # 磁盘上的默认表没被改动：复算用的是快照里的 0.2，不是 0.8
    default = SpaceWeatherCorrectionTable.load()
    assert default.kp_bins[2].factor == 0.8
