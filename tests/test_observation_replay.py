# SPDX-License-Identifier: MIT
"""档案读回与回放上下文（P1-08 的可重演契约）。"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.observation_record import (  # noqa: E402
    ObservationRecord,
    load_record,
    replay_context,
)


def _rec(**over):
    base = dict(
        observation_id="obs-r1",
        started_utc="2026-11-20T00:00:00+00:00",
        ended_utc="2026-11-20T00:00:02+00:00",
        time={"source": "gnss", "time_source": "nmea"},
        site={"source": "gnss", "lat_deg": 43.7, "lon_deg": 126.6,
              "alt_m": None},
        device={"driver": "rtlsdr", "name": "RTL-SDR #0"},
        tuning={"center_freq_hz": 100_100_000.0,
                "sample_rate_hz": 2_400_000.0, "gain_db": 20.0},
        products=[],
        evidence="real_hw",
        timeline=[{"utc": "2026-11-20T00:00:00+00:00",
                   "kind": "record_start", "detail": {}},
                  {"utc": "2026-11-20T00:00:02+00:00",
                   "kind": "record_stop", "detail": {"samples": 8}}],
    )
    base.update(over)
    return ObservationRecord(**base)


def _make_iq(tmp_path, name="cap"):
    data = tmp_path / f"{name}.sigmf-data"
    data.write_bytes(b"\x00" * 64)
    meta = tmp_path / f"{name}.sigmf-meta"
    meta.write_text("{}", encoding="utf-8")
    return str(meta), str(data)


# ---------------------------------------------------------------------------
# 读回
# ---------------------------------------------------------------------------

def test_load_record_roundtrip(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(products=[{"path": meta, "kind": "sigmf-meta"}])
    p = tmp_path / "a.json"
    ok, err = rec.write_json(str(p))
    assert ok, err

    got, err = load_record(str(p))
    assert err is None
    assert got.observation_id == "obs-r1"
    assert got.timeline[0]["kind"] == "record_start"
    assert got.site["source"] == "gnss"


def test_load_record_rejects_unknown_schema(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"schema": "mbdsdr.observation/99",
                             "observation_id": "x"}), encoding="utf-8")
    got, err = load_record(str(p))
    assert got is None
    assert "schema 不匹配" in err


# ---------------------------------------------------------------------------
# 回放上下文
# ---------------------------------------------------------------------------

def test_replay_context_ready_when_complete(tmp_path):
    meta, _data = _make_iq(tmp_path)
    rec = _rec(products=[{"path": meta, "kind": "sigmf-meta"}])

    ctx = replay_context(rec)
    assert ctx["can_replay"] is True, ctx["blockers"]
    assert ctx["at_utc"] == "2026-11-20T00:00:00+00:00"
    assert ctx["data_paths"] == [_data]
    assert ctx["tuning"]["center_freq_hz"] == 100_100_000.0


def test_replay_context_lists_every_blocker(tmp_path):
    rec = _rec(site={"source": "none"}, tuning={}, products=[])
    ctx = replay_context(rec)

    assert ctx["can_replay"] is False
    joined = " | ".join(ctx["blockers"])
    assert "没有可用站址" in joined
    assert "没有中心频率" in joined
    assert "没有可回放的 IQ 数据文件" in joined


def test_replay_context_falls_back_to_timeline_utc(tmp_path):
    meta, _ = _make_iq(tmp_path)
    rec = _rec(started_utc=None, products=[{"path": meta}])
    ctx = replay_context(rec)
    assert ctx["at_utc"] == "2026-11-20T00:00:00+00:00"


def test_replay_context_reports_missing_data_file(tmp_path):
    meta, data = _make_iq(tmp_path)
    os.remove(data)
    rec = _rec(products=[{"path": meta}])
    ctx = replay_context(rec)
    assert ctx["can_replay"] is False
    assert any("数据文件不存在" in b for b in ctx["blockers"])
