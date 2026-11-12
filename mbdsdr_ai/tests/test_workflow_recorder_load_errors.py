# SPDX-License-Identifier: MIT
"""A03：工作流模板加载降级必须可查（第一批）。

契约：坏模板文件记入 load_errors（path + 原因），get_status() 暴露条数。

反证：把 _load_templates 的 except 改回 `except Exception: pass` 后本用例变红。
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.workflow_recorder import WorkflowRecorder  # noqa: E402


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
