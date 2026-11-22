# SPDX-License-Identifier: MIT
"""S10「三端对读」契约测试：观测档案 ↔ ``mbdsdr.spatial/1`` 包 ↔ 回放 CLI。

夹具是一份**真实落盘的小 SigMF**（cf32_le，4800 样本）+ 含 ``samples`` 的
三事件时间轴；三端分别是：

1. ``ObservationRecord``（档案 JSON，``load_record`` 读回）；
2. ``scripts/export_spatial_bundle.py`` 写出的 ``bundle.json``；
3. ``scripts/replay_observation.py --list`` 的步骤表。

只做对读：不合成帧、不放宽断言——任何一端与另一端不一致就红。
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
from contextlib import redirect_stderr, redirect_stdout

import numpy as np
import pytest

from mbdsdr_ai.observation_record import (
    ObservationRecord,
    load_record,
    product_from_sigmf,
    replay_plan,
)
from mbdsdr_ai.spatial_bundle import SCHEMA

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_EXPORT_CLI = os.path.join(_ROOT, "scripts", "export_spatial_bundle.py")
_REPLAY_CLI = os.path.join(_ROOT, "scripts", "replay_observation.py")

#: 头显 JVM 单测与 Python 共用同一份「未知/缺失状态」bundle 夹具。
_UNKNOWN_FIXTURE = os.path.join(
    _ROOT, "spatial", "app", "src", "test", "resources",
    "bundle_unknown_state.json")
_SPATIAL_TIMELINE_KT = os.path.join(
    _ROOT, "spatial", "app", "src", "main", "java", "com", "mbdsdr",
    "spatial", "data", "TimelineFormat.kt")
_DESKTOP_REPLAY = os.path.join(_ROOT, "desktop", "replay_timeline.py")

#: 夹具规模：4800 样本 @ 48 kHz = 0.1 s。
_N = 4800
_RATE = 48000.0
_FREQ = 100.1e6


def _write_sigmf(tmp_path):
    """造一对真实的小 SigMF 数据/元数据（cf32_le，4800 样本）。"""
    data_path = tmp_path / "fixture_small.sigmf-data"
    meta_path = tmp_path / "fixture_small.sigmf-meta"
    t = np.arange(_N, dtype=np.float64)
    x = (0.5 * np.exp(2j * np.pi * 0.01 * t)
         + 0.2 * np.exp(2j * np.pi * -0.03 * t)).astype(np.complex64)
    x.tofile(str(data_path))
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:version": "1.0.0",
            "core:sample_rate": _RATE,
            "core:frequency": _FREQ,
            "core:dataset": data_path.name,
            "core:total_num_samples": _N,
            "mbdsdr:complete": True,
        },
        "captures": [{
            "core:sample_offset": 0,
            "core:datetime": "2026-01-01T00:00:00Z",
            "mbdsdr:time_source": "none",
        }],
        "annotations": [],
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta_path, data_path


def _timeline():
    """三事件时间轴，每条都带真实 ``samples``（回放无需按墙钟换算）。"""
    return [
        {"utc": "2026-01-01T00:00:00+00:00", "kind": "record_start",
         "detail": {"center_freq_hz": _FREQ, "sample_rate_hz": _RATE},
         "samples": 0},
        {"utc": "2026-01-01T00:00:00.050000+00:00", "kind": "tune",
         "detail": {"from_hz": _FREQ, "to_hz": 98.0e6}, "samples": 2400},
        {"utc": "2026-01-01T00:00:00.100000+00:00", "kind": "record_stop",
         "detail": {"samples": _N}, "samples": _N},
    ]


def _record(**overrides):
    base = dict(
        observation_id="obs-consistency-test",
        started_utc="2026-01-01T00:00:00+00:00",
        time={"source": "system", "precision": "本机时钟（未授时）"},
        site={"source": "gnss", "lat_deg": 43.79, "lon_deg": 126.63,
              "alt_m": None},
        tuning={"center_freq_hz": _FREQ, "sample_rate_hz": _RATE,
                "gain_db": 30.0},
        products=[],
        evidence="real_hw",
        timeline=_timeline(),
    )
    base.update(overrides)
    return ObservationRecord(**base)


def _load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export(tmp_path):
    """跑真实导出 CLI，返回 (bundle, 读回的 record, archive 路径)。"""
    meta_path, _ = _write_sigmf(tmp_path)
    rec = _record(products=[product_from_sigmf(str(meta_path))])
    archive = tmp_path / "obs.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_out"

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _load_script(_EXPORT_CLI, "export_spatial_bundle").main(
            [str(archive), "-o", str(outdir), "--frames", "2", "--bins", "4",
             "--no-stars"])

    assert rc == 0, buf.getvalue()
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    loaded, err = load_record(str(archive))
    assert loaded is not None, err
    return bundle, loaded, archive


def test_bundle_timeline_equals_record_timeline(tmp_path):
    bundle, record, _ = _export(tmp_path)

    assert len(bundle["timeline"]) == 3
    for got, want in zip(bundle["timeline"], record.timeline):
        assert set(got) == set(want)
        assert got == want
    assert [e["kind"] for e in bundle["timeline"]] == \
        ["record_start", "tune", "record_stop"]


def test_bundle_carries_observation_id_and_evidence(tmp_path):
    bundle, record, _ = _export(tmp_path)

    assert bundle["schema"] == SCHEMA
    assert bundle["observation_id"] == record.observation_id
    assert bundle["observation_id"] == "obs-consistency-test"
    assert bundle["evidence"] == record.evidence
    assert bundle["evidence"] == "real_hw"


def test_timeline_count_matches_replay_steps(tmp_path):
    bundle, record, _ = _export(tmp_path)

    steps = replay_plan(record)["steps"]
    assert len(bundle["timeline"]) == len(steps) == 3
    assert [s["kind"] for s in steps] == \
        [e["kind"] for e in bundle["timeline"]]


def test_replay_cli_kinds_match_bundle_and_total_samples(tmp_path):
    bundle, record, archive = _export(tmp_path)

    out = io.StringIO()
    with redirect_stdout(out):
        rc = _load_script(_REPLAY_CLI, "replay_observation").main(
            [str(archive), "--list"])
    assert rc == 0
    lines = out.getvalue().splitlines()
    assert lines[0].split()[:4] == ["idx", "t(s)", "sample", "kind"]

    kinds = []
    for line in lines[1:]:
        if not line.strip() or line.startswith("duration="):
            continue
        kinds.append(line.split()[3])
    assert kinds == [e["kind"] for e in bundle["timeline"]]
    assert kinds == ["record_start", "tune", "record_stop"]

    samples = [p["samples"] for p in record.products
               if p.get("samples") is not None]
    assert bundle["spectrum"]["total_samples"] == sum(samples) == _N


def test_replay_cli_sample_and_at_s_match_bundle_timeline(tmp_path):
    """S6 验证：CLI `--list` 的 sample/at_s 与头显包内 timeline 逐条对齐。

    头显没有 at_s 时按 ``samples / sample_rate_hz`` 换算（与
    ``scripts/replay_observation.py`` 的 replay_plan 同口径）；这里直接读
    CLI 的真实输出逐列比对，不只是数条数。
    """
    bundle, record, archive = _export(tmp_path)

    out = io.StringIO()
    with redirect_stdout(out):
        rc = _load_script(_REPLAY_CLI, "replay_observation").main(
            [str(archive), "--list"])
    assert rc == 0
    lines = out.getvalue().splitlines()
    assert lines[0].split()[:4] == ["idx", "t(s)", "sample", "kind"]
    rows = [line.split() for line in lines[1:]
            if line.strip() and not line.startswith("duration=")]

    rate = bundle["spectrum"]["sample_rate_hz"]
    assert rate == record.tuning["sample_rate_hz"] == _RATE
    assert len(rows) == len(bundle["timeline"]) == 3
    for row, event in zip(rows, bundle["timeline"]):
        assert row[3] == event["kind"]
        assert int(row[2]) == event["samples"]
        # CLI 打印 6 位小数；换算口径必须是 samples / sample_rate_hz。
        assert float(row[1]) == pytest.approx(
            event["samples"] / rate, abs=1e-6)


def test_unknown_state_fixture_matches_export_cli(tmp_path):
    """头显 JVM 测试资源必须与导出 CLI 的真实输出逐键一致（created_utc 除外）。

    夹具故意只含缺失项：无站址、无观测时刻、无 IQ、未知 kind。
    """
    archive = {
        "schema": "mbdsdr.observation/1",
        "observation_id": "obs-spatial-unknown",
        "started_utc": None,
        "time": {},
        "site": {"source": "none"},
        "tuning": {"center_freq_hz": 100.1e6, "sample_rate_hz": 2.4e6,
                   "gain_db": 30.0},
        "products": [],
        "evidence": "unverified",
        "timeline": [
            {"kind": "weird_kind", "samples": None,
             "detail": {"note": "外部事件"}},
            {"kind": "tune", "samples": 2400000,
             "detail": {"from_hz": 100.1e6, "to_hz": 98.0e6}},
        ],
    }
    archive_path = tmp_path / "obs_unknown.observation.json"
    archive_path.write_text(json.dumps(archive, ensure_ascii=False),
                            encoding="utf-8")
    stars_path = tmp_path / "stars.json"
    stars_path.write_text(json.dumps(
        [{"name": "A", "ra_deg": 10.0, "dec_deg": 20.0, "mag": 1.0}]),
        encoding="utf-8")
    outdir = tmp_path / "bundle_unknown"

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = _load_script(_EXPORT_CLI, "export_spatial_bundle").main(
            [str(archive_path), "-o", str(outdir), "--no-spectrum",
             "--stars-json", str(stars_path)])
    assert rc == 0, err.getvalue()

    exported = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    with open(_UNKNOWN_FIXTURE, "r", encoding="utf-8") as f:
        fixture = json.load(f)
    exported.pop("created_utc", None)
    fixture.pop("created_utc", None)
    assert exported == fixture


def test_timeline_labels_match_desktop_single_source():
    """时间轴事件中文名与桌面 `replay_timeline._EVENT_LABELS` 逐字一致。

    这是跨语言钉扎：桌面新增/改写事件名时，头显 `TimelineFormat.LABELS`
    必须同步，否则本用例红。未知 kind 的措辞允许不同（头显原样显示，
    桌面加「未知事件（…）」），但已知 kind 的映射必须完全一致。
    """
    with open(_DESKTOP_REPLAY, "r", encoding="utf-8") as f:
        desktop_tree = ast.parse(f.read())
    desktop = None
    for node in desktop_tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_EVENT_LABELS"
                for t in node.targets):
            desktop = ast.literal_eval(node.value)
            break
    assert desktop, "桌面 replay_timeline 里找不到 _EVENT_LABELS"

    with open(_SPATIAL_TIMELINE_KT, "r", encoding="utf-8") as f:
        kt_text = f.read()
    start = kt_text.index("private val LABELS = mapOf(")
    end = kt_text.index("\n    )", start)
    kotlin = dict(re.findall(r'"([^"]+)"\s+to\s+"([^"]+)"',
                             kt_text[start:end]))

    assert set(kotlin) == set(desktop), (
        f"头显缺少事件名：{sorted(set(desktop) - set(kotlin))}；"
        f"多头显事件名：{sorted(set(kotlin) - set(desktop))}")
    for kind in ("tune", "gain", "decode_hit", "read_gap"):
        assert kotlin[kind] == desktop[kind], (
            f"{kind}: 头显 {kotlin[kind]!r} != 桌面 {desktop[kind]!r}")
    for kind, label in desktop.items():
        assert kotlin[kind] == label, f"{kind}: 中文名不一致"
