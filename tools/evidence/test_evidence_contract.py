#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据包自身的契约测试。

证据工具最容易出的错不是"跑不起来"，而是**跑起来了但产出假证据**：
空帧当截图、失败当通过、跳过当验证。这里把这些都钉住。

覆盖：
  1. 组清单唯一、脚本存在、ota 那组（证据 1+2）必须在包里；
  2. --list 列出全部组；未知组必须报错退出（不许静默跑一部分）；
  3. 结果结构字段齐全；
  4. json_safe 不吃 nan/inf/numpy（否则报告写不出合法 JSON）；
  5. 空帧守卫真的会拒（纯色窗口 → FlatFrame）；
  6. 有内容的窗口能正常出帧且带尺寸/方差；
  7. 任一组 FAIL → 整体退出码 1；
  8. 全部 skipped → 退出码 6（"没验证"不是"通过"）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import run_all  # noqa: E402


# ---------------------------------------------------------------- 结构契约

def test_groups_declared_unique_and_scripts_exist():
    ids = [g[0] for g in run_all.GROUPS]
    assert len(ids) == len(set(ids)), f"组 id 重复：{ids}"
    for gid, title, script, _online in run_all.GROUPS:
        assert title.strip(), f"{gid} 缺标题"
        if script is not None:
            assert (HERE / script).is_file(), f"{gid} 的脚本不存在：{script}"


def test_ota_group_is_in_the_bundle():
    """证据 1+2 由 ota_demo 承担，必须挂在包里，否则"一条命令"是假的。"""
    ids = [g[0] for g in run_all.GROUPS]
    assert "ota" in ids
    assert run_all.OTA_SCRIPT.is_file(), run_all.OTA_SCRIPT


def test_test_suites_group_is_optional_and_qt_free():
    """两条完整套件要十几分钟：默认不许跑，且没装 Qt 的机器也能跑。"""
    ids = [g[0] for g in run_all.GROUPS]
    assert "test_suites" in ids
    assert "test_suites" in run_all.OPTIONAL_GROUPS
    assert "test_suites" in run_all.NO_QT_GROUPS
    assert (HERE / "test_suites.py").is_file()


def test_default_bundle_skips_test_suites_but_flag_includes_it(tmp_path, monkeypatch):
    seen = []

    def fake(gid, title, script, out_dir, online):
        seen.append(gid)
        return common.new_result(gid, title)

    monkeypatch.setattr(run_all, "_run_group", fake)
    assert run_all.main(["--out", str(tmp_path)]) == 0
    assert "test_suites" not in seen, "默认证据包不该被完整测试套件拖慢"

    seen.clear()
    assert run_all.main(["--out", str(tmp_path), "--with-tests"]) == 0
    assert "test_suites" in seen, "--with-tests 必须真的把测试基线组加进来"


def test_result_schema_has_required_keys():
    res = common.new_result("demo", "演示组")
    for key in ("schema", "id", "title", "status", "facts", "frames",
                "notes", "not_verified", "started_at", "duration_s"):
        assert key in res, f"结果缺字段 {key}"
    assert res["status"] == "pass"
    assert res["schema"] == common.RESULT_SCHEMA


def test_json_safe_sanitises_non_json_values():
    import math

    out = common.json_safe({
        "nan": float("nan"),
        "inf": float("inf"),
        "nested": [float("-inf"), {"x": float("nan")}],
        "plain": {"a": 1, "b": "文字", "c": None, "d": True},
    })
    # 必须能真的序列化（nan/inf 不是合法 JSON）
    text = json.dumps(out, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text
    assert out["plain"] == {"a": 1, "b": "文字", "c": None, "d": True}
    assert format(out["nan"]) in ("nan", "-nan")


def test_json_safe_handles_numpy_scalars():
    np = pytest.importorskip("numpy")
    out = common.json_safe({"i": np.int64(7), "f": np.float64(1.5)})
    assert out == {"i": 7, "f": 1.5}
    assert isinstance(out["i"], int) and isinstance(out["f"], float)


# ---------------------------------------------------------------- CLI 契约

def _run_cli(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(HERE / "run_all.py"), *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout)


def test_list_mode_names_every_group():
    proc = _run_cli("--list")
    assert proc.returncode == 0, proc.stderr
    for gid, *_ in run_all.GROUPS:
        assert gid in proc.stdout, f"--list 没列出 {gid}"


def test_unknown_group_is_rejected(tmp_path):
    proc = _run_cli("--groups", "ota,not_a_group", "--out", str(tmp_path))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "未知组" in (proc.stdout + proc.stderr)


def test_failed_group_makes_bundle_fail(tmp_path, monkeypatch):
    def fake_fail(gid, title, script, out_dir, online):
        res = common.new_result(gid, title, status="fail")
        res["notes"].append("人为失败")
        return res

    monkeypatch.setattr(run_all, "_run_group", fake_fail)
    code = run_all.main(["--out", str(tmp_path), "--groups", "architecture"])
    assert code == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["fail"] == 1
    assert report["exit_code"] == 1


def test_all_skipped_is_not_a_pass(tmp_path, monkeypatch):
    def fake_skip(gid, title, script, out_dir, online):
        return run_all._skipped(gid, title, "人为跳过")

    monkeypatch.setattr(run_all, "_run_group", fake_skip)
    code = run_all.main(["--out", str(tmp_path), "--groups", "architecture"])
    assert code == 6, "全 skipped 必须给 6，不能当成 0（通过）"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["skipped"] == 1
    assert report["summary"]["pass"] == 0


# ---------------------------------------------------------------- 空帧守卫

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_flat_frame_guard_rejects_uniform_widget(qapp, tmp_path):
    """纯色窗口 ≈ 没建起来的窗口：必须抛 FlatFrame，不许当证据存下来。"""
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.resize(200, 120)
    w.setStyleSheet("background: #14161c;")
    with pytest.raises(common.FlatFrame):
        common.save_frame(w, tmp_path / "flat.png")
    w.deleteLater()


def test_save_frame_records_size_and_variance(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel, QWidget

    w = QWidget()
    w.resize(320, 200)
    w.setStyleSheet("background: #0b0d12;")
    lab = QLabel("MBDSDR evidence", w)
    lab.setStyleSheet("color: #ffffff; font-size: 22px;")
    lab.move(20, 60)
    w.show()
    qapp.processEvents()
    frame = common.save_frame(w, tmp_path / "frame.png")
    assert Path(frame["path"]).is_file()
    # 高 DPI 下物理像素 = 逻辑像素 × devicePixelRatio（本机 1.5 → 480），
    # 断言按真实比例比，不写死 320，否则换台机器就红。
    ratio = w.devicePixelRatioF()
    assert frame["w"] == round(320 * ratio)
    assert frame["h"] == round(200 * ratio)
    assert frame["bytes"] > 0
    assert frame["std"] > 0.8, "有内容的帧不该被判成空帧"
    w.close()
    w.deleteLater()
