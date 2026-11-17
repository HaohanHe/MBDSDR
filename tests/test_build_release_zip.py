# SPDX-License-Identifier: MIT
"""Task 82：可分发便携 zip（``scripts/build_release.py --zip / --zip-only``）。

只验证压包逻辑：临时小目录、不触发 PyInstaller；sha256 与实际文件对拍，
失败路径不得留下半成品。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import build_release  # noqa: E402


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    pkg = dist / "MBDSDR"
    (pkg / "_internal").mkdir(parents=True)
    (pkg / "MBDSDR.exe").write_bytes(b"MZ-fake-exe" * 1000)
    (pkg / "_internal" / "data.bin").write_bytes(b"\x00\x01\x02" * 100)
    (pkg / "empty.txt").write_bytes(b"")
    return dist


def _zip_path(dist):
    return dist / build_release.zip_name(build_release.read_version(), "desktop")


def test_zip_only_contains_every_file_plus_manifest(tmp_path):
    dist = _make_dist(tmp_path)
    assert build_release.main(["--zip-only", "--dist", str(dist)]) == 0

    pkg = dist / "MBDSDR"
    source_count = sum(1 for p in pkg.rglob("*") if p.is_file())
    with zipfile.ZipFile(_zip_path(dist)) as zf:
        names = sorted(zf.namelist())

    assert len(names) == source_count + 1
    assert names == ["MBDSDR/MBDSDR.exe", "MBDSDR/_internal/data.bin",
                     "MBDSDR/empty.txt", "release-manifest.json"]
    with zipfile.ZipFile(_zip_path(dist)) as zf:
        assert zf.read("MBDSDR/MBDSDR.exe") == b"MZ-fake-exe" * 1000
        manifest = json.loads(zf.read("release-manifest.json").decode("utf-8"))
    assert manifest["version"] == build_release.read_version()
    assert manifest["summary"]["file_count"] == source_count


def test_zip_meta_matches_real_file(tmp_path):
    dist = _make_dist(tmp_path)
    assert build_release.main(["--zip-only", "--dist", str(dist)]) == 0

    zip_path = _zip_path(dist)
    meta = json.loads((dist / "release-zip.json").read_text(encoding="utf-8"))
    raw = zip_path.read_bytes()

    assert meta["path"] == str(zip_path)
    assert meta["size"] == len(raw)
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["version"] == build_release.read_version()
    assert meta["build_time"]
    with zipfile.ZipFile(zip_path) as zf:
        assert meta["file_count"] == len(zf.namelist())


def test_zip_meta_and_zip_excluded_from_manifest(tmp_path):
    """补包两次：旧 zip / release-zip.json 不得混进清单本身。"""
    dist = _make_dist(tmp_path)
    assert build_release.main(["--zip-only", "--dist", str(dist)]) == 0
    assert build_release.main(["--zip-only", "--dist", str(dist),
                               "--force"]) == 0

    manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in manifest["files"]]
    assert paths == sorted(paths)
    assert not any(p.endswith(".zip") for p in paths)
    assert "release-zip.json" not in paths


def test_missing_package_dir_is_rejected(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    rc = build_release.main(["--zip-only", "--dist", str(dist)])
    assert rc != 0
    assert not _zip_path(dist).exists()
    assert not (dist / "release-zip.json").exists()


def test_empty_package_dir_is_rejected(tmp_path):
    dist = tmp_path / "dist"
    (dist / "MBDSDR").mkdir(parents=True)
    rc = build_release.main(["--zip-only", "--dist", str(dist)])
    assert rc != 0
    assert not _zip_path(dist).exists()
    assert not (dist / "release-zip.json").exists()
    assert not list(dist.glob("*.tmp"))


def test_existing_zip_needs_force_and_is_kept_intact(tmp_path):
    dist = _make_dist(tmp_path)
    zip_path = _zip_path(dist)
    zip_path.write_bytes(b"someone-elses-artifact")

    assert build_release.main(["--zip-only", "--dist", str(dist)]) != 0
    assert zip_path.read_bytes() == b"someone-elses-artifact"
    assert not (dist / "release-zip.json").exists()
    assert not list(dist.glob("*.tmp"))

    assert build_release.main(["--zip-only", "--dist", str(dist),
                               "--force"]) == 0
    with zipfile.ZipFile(zip_path) as zf:
        assert "release-manifest.json" in zf.namelist()


def test_zip_flag_builds_then_packages(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    calls = []

    def _fake_build(target, out_dir):
        calls.append(target)
        pkg = out_dir / "MBDSDR"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "MBDSDR.exe").write_bytes(b"built")

    monkeypatch.setattr(build_release, "run_build", _fake_build)
    assert build_release.main(["--zip", "--dist", str(dist)]) == 0
    assert calls == ["desktop"]
    with zipfile.ZipFile(_zip_path(dist)) as zf:
        assert zf.namelist() == ["MBDSDR/MBDSDR.exe", "release-manifest.json"]


def test_zip_only_never_calls_build(monkeypatch, tmp_path):
    dist = _make_dist(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("--zip-only 不得触发构建")

    monkeypatch.setattr(build_release, "run_build", _boom)
    assert build_release.main(["--zip-only", "--dist", str(dist)]) == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
