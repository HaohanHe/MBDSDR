# SPDX-License-Identifier: MIT
"""桌面端「文件 → 导出头显观测包」：壳函数行为。

壳只负责选路径与把 CLI 结果原样带回来，导出逻辑必须与 CLI 同一份实现；
这里用真实小 SigMF 档案跑一遍，确认产物落地且失败情形不谎报。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from mbdsdr_ai.observation_record import ObservationRecord, product_from_sigmf
from mbdsdr_ai.spatial_bundle import SCHEMA

from desktop.spatial_export import default_out_dir, export_spatial_bundle

_N = 4800
_RATE = 1.024e6
_FREQ = 100e6


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


def _archive(tmp_path):
    meta_path, _ = _write_sigmf(tmp_path)
    rec = ObservationRecord(
        observation_id="obs-desktop-spatial-export",
        started_utc="2026-01-01T00:00:00+00:00",
        time={"source": "system", "precision": "本机时钟（未授时）"},
        site={"source": "manual", "lat_deg": 0.0, "lon_deg": 0.0,
              "alt_m": None},
        tuning={"center_freq_hz": _FREQ, "sample_rate_hz": _RATE,
                "gain_db": 30.0},
        products=[product_from_sigmf(str(meta_path))],
        evidence="real_hw",
        timeline=[
            {"utc": "2026-01-01T00:00:00+00:00", "kind": "record_start",
             "detail": {"center_freq_hz": _FREQ, "sample_rate_hz": _RATE},
             "samples": 0},
            {"utc": "2026-01-01T00:00:00.100000+00:00", "kind": "record_stop",
             "detail": {"samples": _N}, "samples": _N},
        ],
    )
    archive = tmp_path / "obs.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    return archive


def test_exports_bundle_through_same_cli(tmp_path):
    archive = _archive(tmp_path)
    out_dir = tmp_path / "bundle_out"

    ok, message = export_spatial_bundle(str(archive), str(out_dir))

    assert ok, message
    bundle_path = out_dir / "bundle.json"
    assert bundle_path.is_file()
    assert str(bundle_path) in message

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["schema"] == SCHEMA
    assert bundle["observation_id"] == "obs-desktop-spatial-export"
    # 壳不额外塞数据：时间轴与档案逐条一致。
    assert [e["kind"] for e in bundle["timeline"]] == [
        "record_start", "record_stop"]


def test_missing_archive_reports_reason(tmp_path):
    ok, message = export_spatial_bundle(
        str(tmp_path / "nope.observation.json"), str(tmp_path / "out"))

    assert ok is False
    assert "不存在" in message
    assert not (tmp_path / "out" / "bundle.json").exists()


def test_bad_archive_does_not_claim_success(tmp_path):
    """坏 JSON 走 CLI 的失败分支：必须回原文，不能谎报已导出。"""
    bad = tmp_path / "broken.observation.json"
    bad.write_text("{not json", encoding="utf-8")
    out_dir = tmp_path / "out"

    ok, message = export_spatial_bundle(str(bad), str(out_dir))

    assert ok is False
    assert message.strip()
    assert "已导出" not in message
    assert not (out_dir / "bundle.json").exists()


def test_default_out_dir_is_sibling_folder(tmp_path):
    archive = tmp_path / "obs-1.observation.json"
    archive.write_text("{}", encoding="utf-8")

    assert default_out_dir(str(archive)) == str(tmp_path / "obs-1_spatial")
