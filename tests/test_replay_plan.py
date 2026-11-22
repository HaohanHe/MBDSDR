# SPDX-License-Identifier: MIT
"""时间轴 → 可执行步骤（replay_plan）与回放 CLI 的确定性测试。"""
import importlib.util
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.observation_record import (  # noqa: E402
    ObservationRecord,
    replay_plan,
)

T0 = datetime(2026, 11, 19, 21, 15, 23, tzinfo=timezone.utc)


def _ev(offset_s, kind, detail, *, samples=None):
    ev = {
        "utc": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "kind": kind,
        "detail": dict(detail),
    }
    if samples is not None:
        ev["samples"] = samples
    return ev


def _rec(**over):
    base = dict(
        observation_id="obs-plan",
        started_utc=T0.isoformat(),
        ended_utc=(T0 + timedelta(seconds=2)).isoformat(),
        time={"source": "gnss", "time_source": "nmea"},
        site={"source": "gnss", "lat_deg": 43.7, "lon_deg": 126.6,
              "alt_m": None},
        device={"driver": "rtlsdr", "name": "RTL-SDR #0"},
        tuning={"center_freq_hz": 100_100_000.0,
                "sample_rate_hz": 2_400_000.0, "gain_db": 30.0},
        products=[],
        evidence="real_hw",
        timeline=[],
    )
    base.update(over)
    return ObservationRecord(**base)


def _make_iq(tmp_path, name="cap"):
    data = tmp_path / f"{name}.sigmf-data"
    data.write_bytes(b"\x00" * 64)
    meta = tmp_path / f"{name}.sigmf-meta"
    meta.write_text("{}", encoding="utf-8")
    return str(meta), str(data)


def _write_archive(tmp_path, rec, name="obs.observation.json"):
    p = tmp_path / name
    p.write_text(json.dumps(rec.to_dict(), ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def _load_cli():
    path = os.path.join(_ROOT, "scripts", "replay_observation.py")
    spec = importlib.util.spec_from_file_location("replay_observation_cli",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# replay_plan
# ---------------------------------------------------------------------------

def test_steps_sorted_by_sample_and_state_evolves(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "kind": "sigmf-meta",
                   "samples": 4_800_000}],
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            _ev(0.1, "tune",
                {"from_hz": 100_100_000.0, "to_hz": 98_000_000.0},
                samples=240_000),
            _ev(0.3, "tune",
                {"from_hz": 98_000_000.0, "to_hz": 100_100_000.0},
                samples=720_000),
            # 故意乱序：按样本索引重排后才能得到同一份状态演进
            _ev(0.2, "gain", {"from_db": 30.0, "to_db": 40.0},
                samples=480_000),
        ],
    )

    plan = replay_plan(rec)
    assert plan["can_replay"] is True, plan["blockers"]
    assert plan["sample_rate_hz"] == 2_400_000.0
    assert plan["total_samples"] == 4_800_000
    assert plan["duration_s"] == 2.0

    samples = [s["sample"] for s in plan["steps"]]
    assert samples == sorted(samples)
    assert samples == [0, 240_000, 480_000, 720_000]
    assert [s["kind"] for s in plan["steps"]] == [
        "record_start", "tune", "gain", "tune"]
    assert all(s["index_source"] == "samples" for s in plan["steps"])
    assert [round(s["at_s"], 6) for s in plan["steps"]] == [
        0.0, 0.1, 0.2, 0.3]

    states = [s["state"] for s in plan["steps"]]
    assert states[0]["center_freq_hz"] == 100_100_000.0
    assert states[0]["gain_db"] == 30.0
    assert states[1]["center_freq_hz"] == 98_000_000.0
    assert states[1]["gain_db"] == 30.0
    assert states[2]["center_freq_hz"] == 98_000_000.0
    assert states[2]["gain_db"] == 40.0
    assert states[3]["center_freq_hz"] == 100_100_000.0
    assert states[3]["gain_db"] == 40.0
    assert states[3]["sample_rate_hz"] == 2_400_000.0


def test_missing_samples_uses_wall_clock(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "samples": 4_800_000}],
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            _ev(0.5, "tune", {"to_hz": 98_000_000.0}),   # 无 samples
        ],
    )

    plan = replay_plan(rec)
    step = plan["steps"][1]
    assert step["index_source"] == "wall"
    assert step["sample"] == 1_200_000
    assert abs(step["at_s"] - 0.5) <= 1e-6


def test_event_without_samples_or_utc_is_skipped(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "samples": 4_800_000}],
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            {"kind": "tune", "detail": {"to_hz": 98_000_000.0}},
        ],
    )

    plan = replay_plan(rec)
    assert plan["skipped_events"] == 1
    assert len(plan["steps"]) == 1


def test_event_beyond_recording_is_a_blocker(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "samples": 4800}],
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            _ev(0.004, "tune", {"to_hz": 98_000_000.0}, samples=9600),
        ],
    )

    plan = replay_plan(rec)
    assert plan["can_replay"] is False
    assert any("超出录制长度" in b for b in plan["blockers"])


def test_missing_sample_rate_degrades_to_zero_time(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "samples": 4_800_000}],
        tuning={"center_freq_hz": 100_100_000.0, "gain_db": 30.0},
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            _ev(0.1, "tune", {"to_hz": 98_000_000.0}, samples=240_000),
        ],
    )

    plan = replay_plan(rec)
    assert plan["sample_rate_hz"] is None
    assert plan["duration_s"] is None
    assert all(s["at_s"] == 0.0 for s in plan["steps"])
    assert any("缺少采样率" in b for b in plan["blockers"])


def test_empty_timeline_is_a_blocker(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(products=[{"path": meta, "samples": 4_800_000}], timeline=[])

    plan = replay_plan(rec)
    assert plan["steps"] == []
    assert plan["can_replay"] is False
    assert any("时间轴为空" in b for b in plan["blockers"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_list_prints_steps_and_duration(tmp_path, capsys):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(
        products=[{"path": meta, "kind": "sigmf-meta",
                   "samples": 4_800_000}],
        timeline=[
            _ev(0.0, "record_start", {}, samples=0),
            _ev(0.1, "tune", {"to_hz": 98_000_000.0}, samples=240_000),
            _ev(0.2, "gain", {"to_db": 40.0}, samples=480_000),
            _ev(2.0, "record_stop", {"samples": 4_800_000},
                samples=4_800_000),
        ],
    )
    archive = _write_archive(tmp_path, rec)

    rc = _load_cli().main([archive, "--list"])
    out = capsys.readouterr().out

    assert rc == 0, out
    for kind in ("record_start", "tune", "gain", "record_stop"):
        assert kind in out
    assert "duration=" in out
    assert "freq=98.000MHz" in out
    assert "gain=40.0dB" in out


def test_cli_read_reports_rms_dbfs(tmp_path, capsys):
    n = 4800
    t = np.arange(n, dtype=np.float64) / 2_400_000.0
    tone = (0.5 * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)
    data = tmp_path / "read.sigmf-data"
    tone.tofile(str(data))
    rate = 2_400_000.0
    meta = tmp_path / "read.sigmf-meta"
    meta.write_text(json.dumps({
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": rate,
            "core:frequency": 100_100_000.0,
            "core:total_num_samples": n,
            "core:dataset": "read.sigmf-data",
        },
        "captures": [{"core:sample_start": 0,
                      "core:datetime": T0.isoformat()}],
        "annotations": [],
    }), encoding="utf-8")

    rec = _rec(
        products=[{"path": str(meta), "kind": "sigmf-meta", "samples": n}],
        timeline=[_ev(0.0, "record_start", {}, samples=0)],
    )
    archive = _write_archive(tmp_path, rec)

    rc = _load_cli().main([archive, "--at", "0", "--read", "0.05"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert "rms_dbfs" in out
    token = [p for p in out.split() if p.startswith("rms_dbfs=")][0]
    got = float(token.split("=", 1)[1])
    block = np.fromfile(str(data), dtype=np.complex64)[:n]
    expected = 20.0 * math.log10(
        float(np.sqrt(np.mean(np.abs(block) ** 2))) + 1e-12)
    assert abs(got - expected) <= 0.01
