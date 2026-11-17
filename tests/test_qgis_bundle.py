# SPDX-License-Identifier: MIT
"""QGIS 一键包：6 个图层 + README 入包、清单哈希、缺文件与覆盖门。

``scripts/export_qgis_bundle.py`` 不生成数据，只把已经落盘的 GeoJSON/QML
打成一个 zip。这里用 tmp 小样本钉住真正会坑人的几件事：

1. zip 是扁平的 6 个图层文件 + ``README.txt``，且 ``testzip()`` 无坏项；
2. 清单里的 size/sha256 与包内实际字节逐项一致；
3. 缺任一输入文件 → 非零退出、列出缺项、提示先跑的脚本，且不产出 zip；
4. 目标 zip 已存在默认拒绝，``--force`` 才覆盖。
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_qgis_bundle.py"

LAYER_FILES = (
    "observing_sites.geojson",
    "ground_track.geojson",
    "footprint.geojson",
    "observing_sites.qml",
    "ground_track.qml",
    "footprint.qml",
)
README = "README.txt"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)


def _populate(src, overrides=None):
    """写 6 个可区分的小样本；返回文件名→字节。"""
    src.mkdir(parents=True)
    files = {}
    for index, name in enumerate(LAYER_FILES):
        data = f"{name} sample #{index}\n".encode("utf-8")
        if overrides and name in overrides:
            data = overrides[name]
        (src / name).write_bytes(data)
        files[name] = data
    return files


class TestBundleContents:
    def test_six_layers_plus_readme_flat_and_readable(self, tmp_path):
        src = tmp_path / "src"
        files = _populate(src)
        out = tmp_path / "out" / "bundle.zip"

        r = _run("--src", str(src), "--out", str(out))
        assert r.returncode == 0, r.stderr

        with zipfile.ZipFile(out) as zf:
            assert zf.testzip() is None, "zip 里有坏项"
            assert zf.namelist() == list(LAYER_FILES) + [README]
            for name, data in files.items():
                assert zf.read(name) == data, f"{name} 内容与源文件不一致"
            assert zf.getinfo(README).file_size > 0, "README 不能为空"
            readme = zf.read(README).decode("utf-8")
        assert "QGIS" in readme
        assert "导入步骤" in readme
        assert "MIT" in readme
        assert "source_url" in readme, "README 要说明数据来源"

        # 打包成功不能留下 .tmp 残片
        assert list(out.parent.glob("*.tmp")) == []

    def test_manifest_hashes_match_archive_members(self, tmp_path):
        src = tmp_path / "src"
        _populate(src)
        out = tmp_path / "out" / "bundle.zip"

        r = _run("--src", str(src), "--out", str(out))
        assert r.returncode == 0, r.stderr

        manifest = json.loads(
            out.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["zip_path"] == str(out.resolve())
        assert manifest["size"] == out.stat().st_size
        assert manifest["sha256"] == _sha256(out.read_bytes())
        assert manifest["generated_utc"].endswith("Z")

        with zipfile.ZipFile(out) as zf:
            entries = {item["name"]: item for item in manifest["files"]}
            assert set(entries) == set(zf.namelist())
            assert len(entries) == len(LAYER_FILES) + 1
            for name in zf.namelist():
                data = zf.read(name)
                assert entries[name]["size"] == len(data)
                assert entries[name]["size"] == zf.getinfo(name).file_size
                assert entries[name]["sha256"] == _sha256(data)


class TestMissingInputs:
    def test_partial_missing_lists_files_and_scripts(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "observing_sites.geojson").write_text("{}", encoding="utf-8")
        (src / "ground_track.qml").write_text("<qgis/>", encoding="utf-8")
        out = tmp_path / "bundle.zip"

        r = _run("--src", str(src), "--out", str(out))
        assert r.returncode != 0
        message = r.stdout + r.stderr
        for name in ("ground_track.geojson", "footprint.geojson",
                     "observing_sites.qml", "footprint.qml"):
            assert name in message, f"缺失项 {name} 没被列出"
        assert "export_geojson.py" in message
        assert "export_qgis_style.py" in message
        assert not out.exists(), "缺文件时不能产出 zip"

    def test_missing_directory_reports_all_six(self, tmp_path):
        out = tmp_path / "bundle.zip"
        r = _run("--src", str(tmp_path / "nowhere"), "--out", str(out))
        assert r.returncode != 0
        message = r.stdout + r.stderr
        for name in LAYER_FILES:
            assert name in message, f"缺失项 {name} 没被列出"
        assert not out.exists()


class TestOverwriteGuard:
    def test_existing_target_refused_without_force(self, tmp_path):
        src = tmp_path / "src"
        _populate(src)
        out = tmp_path / "bundle.zip"
        first = _run("--src", str(src), "--out", str(out))
        assert first.returncode == 0, first.stderr
        before = out.read_bytes()
        manifest_before = out.with_suffix(".json").read_bytes()

        second = _run("--src", str(src), "--out", str(out))
        assert second.returncode != 0
        assert "已存在" in second.stderr
        assert "--force" in second.stderr
        assert out.read_bytes() == before, "拒绝时必须保持原 zip 不变"
        assert out.with_suffix(".json").read_bytes() == manifest_before

    def test_force_replaces_archive_and_manifest(self, tmp_path):
        src = tmp_path / "src"
        _populate(src)
        out = tmp_path / "bundle.zip"
        assert _run("--src", str(src), "--out", str(out)).returncode == 0
        old_sha = _sha256(out.read_bytes())

        new_data = b"footprint.qml replaced\n"
        (src / "footprint.qml").write_bytes(new_data)
        forced = _run("--src", str(src), "--out", str(out), "--force")
        assert forced.returncode == 0, forced.stderr

        with zipfile.ZipFile(out) as zf:
            assert zf.testzip() is None
            assert zf.read("footprint.qml") == new_data

        manifest = json.loads(
            out.with_suffix(".json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["files"]
                     if item["name"] == "footprint.qml")
        assert entry["sha256"] == _sha256(new_data)
        assert manifest["sha256"] == _sha256(out.read_bytes())
        assert manifest["sha256"] != old_sha
