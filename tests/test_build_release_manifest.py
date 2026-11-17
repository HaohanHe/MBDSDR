# SPDX-License-Identifier: MIT
"""Task 60：打包清单可复现（``scripts/build_release.py``）。

只验证清单逻辑：``--dry-run`` 对临时产物目录逐文件算 SHA256 并写
``release-manifest.json``，不触发任何真实构建。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import build_release  # noqa: E402


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "MBDSDR" / "_internal").mkdir(parents=True)
    (dist / "MBDSDR" / "MBDSDR.exe").write_bytes(b"MZ-fake-exe")
    (dist / "MBDSDR" / "_internal" / "data.bin").write_bytes(b"\x00\x01\x02")
    (dist / "MBDSDR" / "empty.txt").write_bytes(b"")
    return dist


def test_dry_run_writes_manifest_with_version_and_hashes(tmp_path):
    dist = _make_dist(tmp_path)
    rc = build_release.main(["--dry-run", "--dist", str(dist)])
    assert rc == 0

    manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["version"] == (build_release.VERSION_FILE
                                   .read_text(encoding="utf-8-sig").strip())
    assert manifest["dry_run"] is True
    assert manifest["target"] == "desktop"
    assert manifest["build_time"]
    assert "git_commit" in manifest  # 可为 None：无 git 时不伪造

    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["MBDSDR/MBDSDR.exe"]["sha256"] == hashlib.sha256(
        b"MZ-fake-exe").hexdigest()
    assert by_path["MBDSDR/empty.txt"]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert by_path["MBDSDR/empty.txt"]["size"] == 0
    assert manifest["summary"]["file_count"] == len(manifest["files"]) == 3
    assert manifest["summary"]["total_bytes"] == sum(f["size"] for f in manifest["files"])


def test_manifest_sorted_and_excludes_itself(tmp_path):
    dist = _make_dist(tmp_path)
    assert build_release.main(["--dry-run", "--dist", str(dist)]) == 0
    # 再跑一次：清单自身不得进入文件列表，顺序保持稳定
    assert build_release.main(["--dry-run", "--dist", str(dist)]) == 0
    manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in manifest["files"]]
    assert paths == sorted(paths)
    assert all(not p.endswith("release-manifest.json") for p in paths)


def test_dry_run_never_calls_build(monkeypatch, tmp_path):
    dist = _make_dist(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("--dry-run 不得触发构建")

    monkeypatch.setattr(build_release, "run_build", _boom)
    assert build_release.main(["--dry-run", "--dist", str(dist)]) == 0


def test_non_dry_run_invokes_build_then_manifests(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    calls = []

    def _fake_build(target, out_dir):
        calls.append((target, out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "artifact.bin").write_bytes(b"built")

    monkeypatch.setattr(build_release, "run_build", _fake_build)
    rc = build_release.main(["--dist", str(dist), "--target", "cpp"])
    assert rc == 0
    assert calls == [("cpp", dist.resolve())]
    manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["path"] == "artifact.bin"
    assert manifest["dry_run"] is False


def test_missing_dist_is_reported_not_faked(tmp_path):
    missing = tmp_path / "nope"
    assert build_release.main(["--dry-run", "--dist", str(missing)]) == 2


def test_manifest_override_path(tmp_path):
    dist = _make_dist(tmp_path)
    out = tmp_path / "elsewhere" / "manifest.json"
    assert build_release.main(["--dry-run", "--dist", str(dist),
                               "--manifest", str(out)]) == 0
    assert out.is_file()
    assert not (dist / "release-manifest.json").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
