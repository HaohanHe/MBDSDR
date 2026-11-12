# SPDX-License-Identifier: MIT
"""A03：工作流模板加载降级必须可查（第一批）。

契约：坏模板文件记入 load_errors（path + 原因），get_status() 暴露条数。

反证：把 _load_templates 的 except 改回 `except Exception: pass` 后本用例变红。
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.workflow_recorder as wf_mod  # noqa: E402
from mbdsdr_ai.workflow_recorder import (  # noqa: E402
    WorkflowRecorder, WorkflowTemplate,
)


def test_corrupt_template_is_visible(tmp_path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "bad.json").write_text("{ broken", encoding="utf-8")

    rec = WorkflowRecorder(storage_dir=str(tmp_path))

    assert len(rec.load_errors) == 1
    assert rec.load_errors[0]["path"].endswith("bad.json")
    assert "JSONDecodeError" in rec.load_errors[0]["error"]
    assert rec.get_status()["load_error_count"] == 1


def test_empty_template_dir_is_not_degradation(tmp_path):
    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    assert rec.load_errors == []
    assert rec.get_status()["load_error_count"] == 0


# ----------------------------------------------------------------------
# A04：损坏后不得被下一次保存覆盖
# ----------------------------------------------------------------------

def test_corrupt_template_backed_up_with_reason(tmp_path):
    """A04：首次读到坏模板即备份 + 理由，原件移出扫描路径。"""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    bad = template_dir / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")

    rec = WorkflowRecorder(storage_dir=str(tmp_path))

    backups = [p for p in template_dir.glob("bad.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ broken"
    reasons = list(template_dir.glob("bad.json.corrupt-*.reason.txt"))
    assert len(reasons) == 1 and "JSONDecodeError" in reasons[0].read_text(encoding="utf-8")
    assert not bad.exists()
    assert rec.corrupt_backups == [str(backups[0])]


def test_backup_failure_blocks_save_to_same_template(tmp_path, monkeypatch):
    """A04 反证：备份失败时，写回同一模板路径必须被拒绝。"""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    bad = template_dir / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(wf_mod, "_backup_corrupt", lambda _p: None)

    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    rec._save_template(WorkflowTemplate(template_id="bad", name="新模板"))

    assert bad.read_text(encoding="utf-8") == "{ broken", "原件不得被覆盖"
    assert rec.save_warnings
    assert rec.get_status()["save_error_count"] == len(rec.save_warnings)


def test_save_target_corrupted_after_load_is_backed_up(tmp_path):
    """A04：加载之后目标才被外部写坏，保存前也必须先备份。"""
    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    target = template_dir / "late.json"
    target.write_text("{ broken", encoding="utf-8")

    assert rec._write_json(str(target), {"ok": 1}) is True

    backups = [p for p in template_dir.glob("late.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ broken"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}


def test_save_target_backup_failure_blocks_write(tmp_path, monkeypatch):
    """A04 反证：保存前发现目标损坏但备份失败 → 一个字节都不能写。"""
    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    target = tmp_path / "late.json"
    target.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(wf_mod, "_backup_corrupt", lambda _p: None)

    assert rec._write_json(str(target), {"ok": 1}) is False
    assert target.read_text(encoding="utf-8") == "{ broken"
    assert rec.save_warnings


def test_write_json_missing_race_is_not_corruption(tmp_path, monkeypatch):
    """A04：exists 判真后目标被删，FileNotFoundError 是竞态而非损坏。"""
    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    target = os.path.join(str(tmp_path), "race.json")
    real_exists = os.path.exists
    monkeypatch.setattr(
        wf_mod.os.path, "exists",
        lambda p: True if p == target else real_exists(p))

    assert rec._write_json(target, {"ok": 1}) is True
    assert rec.save_warnings == []
    assert rec.corrupt_backups == []
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_missing_template_save_is_not_corruption(tmp_path):
    """A04：正常新建模板不得产生备份或 save_warnings。"""
    rec = WorkflowRecorder(storage_dir=str(tmp_path))
    rec._save_template(WorkflowTemplate(template_id="fresh", name="新模板"))

    assert rec.save_warnings == []
    assert rec.corrupt_backups == []
    assert list(tmp_path.rglob("*.corrupt-*")) == []
