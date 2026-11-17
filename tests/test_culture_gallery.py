# SPDX-License-Identifier: MIT
"""``scripts/culture_gallery.py`` 的离屏图册契约。

只验证机械事实，不评价画面审美：
  * 选中几套文化就出几张 PNG，且尺寸与 ``--size`` 一致、文件非空；
  * ``index.html`` 写明每套文化的许可、来源链接与「非真实夜空」声明；
  * ``report.json`` 逐包字段完整（id/名称/许可/来源/文件名/字节数/耗时/UTC）；
  * 单个文化失败（未知 id）不拖垮其它文化，失败项被列出且退出码非零。

用子进程跑脚本：图册本身要建 QApplication/离屏窗口，跑在 pytest 进程里
会污染后续 Qt 用例；子进程也能顺带验证「脚本退出无残留」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "culture_gallery.py"

SIZE = (320, 200)
SUBSET = ("japanese", "indian")


def _run(out_dir: Path, *extra: str, timeout: int = 300):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out_dir),
         "--size", f"{SIZE[0]}x{SIZE[1]}", *extra],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


def _png_size(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height, len(data)


def _load_report(out_dir: Path) -> dict:
    path = out_dir / "report.json"
    assert path.is_file(), "缺少 report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_selected_cultures_produce_one_png_each(tmp_path):
    out_dir = tmp_path / "gallery"
    proc = _run(out_dir, "--only", ",".join(SUBSET))
    assert proc.returncode == 0, (
        f"脚本退出码 {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}")

    pngs = sorted(out_dir.glob("*.png"))
    assert len(pngs) == len(SUBSET), (
        f"应出 {len(SUBSET)} 张图，实际 {len(pngs)}: {[p.name for p in pngs]}")
    for pack_id in SUBSET:
        path = out_dir / f"{pack_id}.png"
        assert path.is_file(), f"缺少 {path.name}"
        width, height, size = _png_size(path)
        assert (width, height) == SIZE, f"{path.name} 尺寸 {width}x{height} != {SIZE}"
        assert size > 1024, f"{path.name} 只有 {size} 字节，疑似空图"

    report = _load_report(out_dir)
    assert report["totals"]["selected"] == len(SUBSET)
    assert report["totals"]["ok"] == len(SUBSET)
    assert report["totals"]["failed"] == 0
    assert report["totals"]["total_bytes"] == sum(
        (out_dir / f"{p}.png").stat().st_size for p in SUBSET)
    for row in report["packs"]:
        assert row["file"] == f"{row['id']}.png"
        assert row["bytes"] == (out_dir / row["file"]).stat().st_size
        assert row["width"] == SIZE[0] and row["height"] == SIZE[1]


def test_html_has_license_source_and_disclaimer(tmp_path):
    out_dir = tmp_path / "gallery"
    proc = _run(out_dir, "--only", ",".join(SUBSET))
    assert proc.returncode == 0, proc.stderr

    text = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "非真实夜空" in text
    assert "固定视角" in text
    assert "离屏渲染" in text

    report = _load_report(out_dir)
    for row in report["packs"]:
        assert row["license"] in text, f"HTML 缺 {row['id']} 的许可"
        assert row["source_url"] in text, f"HTML 缺 {row['id']} 的来源链接"
        assert row["display_name"] in text, f"HTML 缺 {row['id']} 的显示名"
        assert f'src="{row["file"]}"' in text, f"HTML 缺 {row['id']} 缩略图"


def test_report_fields_are_complete(tmp_path):
    out_dir = tmp_path / "gallery"
    proc = _run(out_dir, "--only", ",".join(SUBSET))
    assert proc.returncode == 0, proc.stderr

    report = _load_report(out_dir)
    for key in ("utc", "simulated_unix", "station", "view",
                "disclaimer", "totals", "packs", "failures"):
        assert key in report, f"report.json 缺顶层字段 {key!r}"
    assert report["disclaimer"] == "固定视角、离屏渲染、非真实夜空"
    assert report["utc"].endswith("Z")
    assert isinstance(report["simulated_unix"], float)

    for row in report["packs"]:
        for key in ("id", "display_name", "license", "source", "source_url",
                    "file", "bytes", "render_ms", "utc", "status", "stats"):
            assert key in row, f"{row.get('id')} 缺字段 {key!r}"
        assert row["status"] == "ok"
        assert row["bytes"] > 0
        assert row["render_ms"] is not None and row["render_ms"] >= 0
        assert row["utc"] == report["utc"]
        for stat in ("groups", "edges", "names"):
            assert isinstance(row["stats"][stat], int)
            assert row["stats"][stat] > 0


def test_failed_culture_is_listed_without_blocking_others(tmp_path):
    out_dir = tmp_path / "gallery"
    proc = _run(out_dir, "--only", "japanese,does_not_exist")
    assert proc.returncode == 1, (
        f"有失败项时退出码应为 1，实际 {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    report = _load_report(out_dir)
    failures = {row["id"]: row for row in report["failures"]}
    assert "does_not_exist" in failures
    assert "未知天空文化体系" in failures["does_not_exist"]["error"]
    assert report["totals"]["failed"] == 1
    assert report["totals"]["ok"] == 1
    assert (out_dir / "japanese.png").is_file()
