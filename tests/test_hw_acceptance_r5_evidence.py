# SPDX-License-Identifier: MIT
"""R5 验收证据快照的两层判据。

第一层：跑一次后快照目录齐全（TLE 原文+来源+历元、UTC、站址、窗口/步长、
门限、逐条过境 AOS/LOS/峰值/误差），缺一项即红。
第二层：只用快照就能**独立复算**出同一批结果，复算与实跑一致。

全部离线：``mbdsdr_ai.orbit.fetch_tle_record`` 被替换成读仓库内的真实 TLE
文件，不联网、不碰用户 TLE 缓存。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HW_DIR = REPO_ROOT / "scripts" / "hw_acceptance"
for _p in (str(REPO_ROOT), str(HW_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ring5_satellite_passes as r5  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402

TLE_FILE = REPO_ROOT / "experiments" / "satellite_rx" / "data" / "tle_noaa.txt"
RECOMPUTE = HW_DIR / "recompute_r5_evidence.py"


def _offline_records() -> dict:
    """仓库内真实 TLE（NOAA-15/18/19 + METEOR-M 2）→ 记录字典。"""
    lines = [ln.strip() for ln in TLE_FILE.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    recs = {}
    for i in range(0, len(lines) - 2, 3):
        name, line1, line2 = lines[i], lines[i + 1], lines[i + 2]
        catnr = int(line1[2:7])
        recs[catnr] = {
            "catnr": catnr,
            "name": name,
            "line1": line1,
            "line2": line2,
            "source": "offline:experiments/satellite_rx/data/tle_noaa.txt",
            "fetched_at": 0.0,
            "refresh_status": "cache",
            "refresh_error": "",
        }
    assert recs, "离线 TLE 文件解析为空"
    return recs


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    """真实跑一次 R5（TLE 来自仓库文件），把快照目录留给各用例检查。"""
    root = tmp_path_factory.mktemp("r5_evidence")
    records = _offline_records()
    real_fetch = orbit.fetch_tle_record

    def fake_fetch(catnr, max_age_hours=24.0, now=None):
        rec = records.get(int(catnr))
        if rec is None:
            raise orbit.TLEUnavailableError(f"测试环境未提供 catnr={catnr} 的 TLE")
        return dict(rec)

    old_env = os.environ.get(r5.EVIDENCE_ENV)
    os.environ[r5.EVIDENCE_ENV] = str(root)
    orbit.fetch_tle_record = fake_fetch
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = r5.run(45.0, 120.0, 200.0)
    finally:
        orbit.fetch_tle_record = real_fetch
        if old_env is None:
            os.environ.pop(r5.EVIDENCE_ENV, None)
        else:
            os.environ[r5.EVIDENCE_ENV] = old_env

    evidence = pathlib.Path(res["metrics"]["evidence_dir"])
    return {"dir": evidence, "root": root, "res": res, "stdout": buf.getvalue()}


def _load(snapshot):
    d = snapshot["dir"]
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads((d / "passes.json").read_text(encoding="utf-8"))
    tle_txt = (d / "tle.txt").read_text(encoding="utf-8")
    return man, payload, tle_txt


# ---------------------------------------------------------------- 第一层
def test_snapshot_written_and_complete(snapshot):
    d = snapshot["dir"]
    assert d.is_dir()
    assert d.parent == snapshot["root"]
    assert sorted(p.name for p in d.iterdir()) == list(r5.EVIDENCE_FILES)
    assert r5.verify_evidence(d) == []

    man, payload, tle_txt = _load(snapshot)
    # 本次实跑的参数快照
    assert man["window_hours"] == 24.0
    assert man["min_elevation_deg"] == 5.0
    assert man["params"]["coarse_step_s"] == 60.0
    assert man["params"]["fine_step_s"] == 2.0
    assert man["params"]["bisect_tol_s"] == 0.5
    assert man["station"] == {"lat": 45.0, "lon": 120.0,
                              "alt_m": 200.0, "alt_km": 0.2}
    assert man["run_utc"].endswith("Z")

    # TLE 原文 + 来源 + 历元，且原文确实出现在 tle.txt 里
    assert man["tle"], "非 SKIP 运行必须记录 TLE"
    for catnr, meta in man["tle"].items():
        assert meta["source"]
        assert meta["epoch_utc"]
        assert meta["line1"] in tle_txt and meta["line2"] in tle_txt
        assert f"catnr={catnr}" in tle_txt and meta["source"] in tle_txt

    # 结果摘要：每条过境都有 AOS/LOS/峰值
    assert payload["passes"]
    for row in payload["passes"]:
        assert row["satellite"]
        assert row["rise_utc"].endswith("Z")
        assert row["culmination_utc"].endswith("Z")
        assert row["set_utc"].endswith("Z")
        assert row["max_elevation"] >= 5.0
        assert "elevation_error_deg" in row and "rise_error_s" in row
    assert payload["findings"], "目标星误差摘要缺失"


def test_run_prints_and_returns_snapshot_path(snapshot):
    path = str(snapshot["dir"])
    assert f"[R5] 证据快照：{path}" in snapshot["stdout"]
    assert snapshot["res"]["artifacts"][-1] == path
    assert snapshot["res"]["metrics"]["evidence_dir"] == path


def test_missing_file_makes_snapshot_red(snapshot, tmp_path):
    d = tmp_path / "copy"
    shutil.copytree(snapshot["dir"], d)
    (d / "passes.json").unlink()
    problems = r5.verify_evidence(d)
    assert any("passes.json" in p for p in problems)


def test_tle_text_must_match_manifest(snapshot, tmp_path):
    d = tmp_path / "copy"
    shutil.copytree(snapshot["dir"], d)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    line = man["tle"][sorted(man["tle"], key=int)[0]]["line1"]
    (d / "tle.txt").write_text(
        (d / "tle.txt").read_text(encoding="utf-8").replace(line, ""),
        encoding="utf-8")
    problems = r5.verify_evidence(d)
    assert any("未出现在 tle.txt" in p for p in problems)


def test_tampered_result_makes_snapshot_red(snapshot, tmp_path):
    d = tmp_path / "copy"
    shutil.copytree(snapshot["dir"], d)
    payload = json.loads((d / "passes.json").read_text(encoding="utf-8"))
    payload["passes"][0]["max_elevation"] = 1.0  # 低于 5° 门限
    (d / "passes.json").write_text(json.dumps(payload, ensure_ascii=False),
                                   encoding="utf-8")
    problems = r5.verify_evidence(d)
    assert any("max_elevation" in p for p in problems)


def test_pass_status_without_recorded_tle_is_rejected(tmp_path):
    """实时取 TLE 却不在快照里记录 → 写快照直接抛错（不许静默通过）。"""
    with pytest.raises(ValueError):
        r5.write_evidence(0.0, (45.0, 120.0, 200.0), {}, {}, [], {}, [], [],
                          status="PASS")


def test_tle_record_without_source_or_lines_is_rejected(tmp_path):
    rec = dict(_offline_records()[33591])
    bad_source = dict(rec, source="")
    with pytest.raises(ValueError):
        r5.write_evidence(0.0, (45.0, 120.0, 200.0), {33591: bad_source}, {},
                          [], {}, [], [], status="FAIL")
    bad_lines = dict(rec, line2="")
    with pytest.raises(ValueError):
        r5.write_evidence(0.0, (45.0, 120.0, 200.0), {33591: bad_lines}, {},
                          [], {}, [], [], status="FAIL")


# ---------------------------------------------------------------- 第二层
def test_recompute_from_snapshot_matches(snapshot):
    proc = subprocess.run(
        [sys.executable, str(RECOMPUTE), str(snapshot["dir"])],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[PASS]" in proc.stdout
    assert "复算与实跑一致" in proc.stdout


def test_recompute_detects_tampered_snapshot(snapshot, tmp_path):
    d = tmp_path / "tampered"
    shutil.copytree(snapshot["dir"], d)
    payload = json.loads((d / "passes.json").read_text(encoding="utf-8"))
    payload["passes"][0]["max_elevation"] = round(
        payload["passes"][0]["max_elevation"] + 0.5, 1)
    (d / "passes.json").write_text(json.dumps(payload, ensure_ascii=False),
                                   encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(RECOMPUTE), str(d)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1
    assert "max_elevation" in proc.stdout


def test_recompute_refuses_frozen_param_drift(snapshot, tmp_path):
    """快照里门限被改（例如 5°→4°）→ 复算拒认，不能靠改快照把判定放宽。"""
    d = tmp_path / "gate"
    shutil.copytree(snapshot["dir"], d)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    man["min_elevation_deg"] = 4.0
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False),
                                     encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(RECOMPUTE), str(d)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1
    assert "门限" in proc.stdout
