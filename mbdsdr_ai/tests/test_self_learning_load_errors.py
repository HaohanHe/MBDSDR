# SPDX-License-Identifier: MIT
"""A03：自学习经验/模式加载降级必须可查（第一批）。

契约：坏文件记入 load_errors（kind + path + 原因），好文件照常加载；
空目录（首次运行）不产生降级记录；get_stats() 暴露 load_error_count。

反证：把 _load_experiences / _load_patterns 的 except 改回
`except Exception: pass` 后本文件对应断言变红。
"""
import json
import os
import sys
import time

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.self_learning as sl_mod  # noqa: E402
from mbdsdr_ai.self_learning import (  # noqa: E402
    SelfLearningEngine, Experience, ExperienceType,
)


def test_corrupt_files_are_visible(tmp_path):
    """经验/模式坏文件各记一条 load_errors，好文件照常加载。"""
    exp_dir = tmp_path / "experiences" / "2026-01-01"
    exp_dir.mkdir(parents=True)
    (exp_dir / "bad.json").write_text("{ broken", encoding="utf-8")
    (exp_dir / "exp_1_7.json").write_text(json.dumps({
        "experience_id": "exp_1_7",
        "experience_type": "tool_call",
        "question": "好经验",
        "score": 9,
    }), encoding="utf-8")
    pattern_dir = tmp_path / "patterns"
    pattern_dir.mkdir()
    (pattern_dir / "bad_pattern.json").write_text("{ broken", encoding="utf-8")

    engine = SelfLearningEngine(storage_dir=str(tmp_path))

    kinds = sorted(e["kind"] for e in engine.load_errors)
    assert kinds == ["experience", "pattern"], "两处坏文件都要留下降级记录"
    assert "exp_1_7" in engine.experiences, "坏文件不能阻断好文件的加载"
    assert engine.get_stats()["load_error_count"] == 2


def test_empty_storage_is_not_degradation(tmp_path):
    """空目录 = 首次运行，不得记降级。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    assert engine.load_errors == []
    assert engine.get_stats()["load_error_count"] == 0


# ----------------------------------------------------------------------
# A04：损坏后不得被下一次保存覆盖
# ----------------------------------------------------------------------

def test_corrupt_experience_backed_up_with_reason(tmp_path):
    """A04：首次读到坏经验文件即备份 + 理由，原件移出扫描路径。"""
    exp_dir = tmp_path / "experiences" / "2026-01-01"
    exp_dir.mkdir(parents=True)
    bad = exp_dir / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")

    engine = SelfLearningEngine(storage_dir=str(tmp_path))

    backups = [p for p in exp_dir.glob("bad.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ broken"
    reasons = list(exp_dir.glob("bad.json.corrupt-*.reason.txt"))
    assert len(reasons) == 1 and "JSONDecodeError" in reasons[0].read_text(encoding="utf-8")
    assert not bad.exists(), "原件必须移出扫描路径"
    assert engine.corrupt_backups == [str(backups[0])]


def test_backup_failure_blocks_save_to_same_path(tmp_path, monkeypatch):
    """A04 反证：损坏文件备份失败时，写回同一路径必须被拒绝。"""
    now = time.time()
    date_str = time.strftime("%Y-%m-%d", time.localtime(now))
    exp_dir = tmp_path / "experiences" / date_str
    exp_dir.mkdir(parents=True)
    bad = exp_dir / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(sl_mod, "_backup_corrupt", lambda _p: None)

    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    exp = Experience(experience_id="bad",
                     experience_type=ExperienceType.TOOL_CALL,
                     timestamp=now)
    engine._save_experience(exp)

    assert bad.read_text(encoding="utf-8") == "{ broken", "原件不得被覆盖"
    assert engine.save_warnings, "拒绝写回必须留下可见记录"
    assert engine.get_stats()["save_error_count"] == len(engine.save_warnings)


def test_save_target_corrupted_after_load_is_backed_up(tmp_path):
    """A04：加载之后目标才被外部写坏，保存前也必须先备份。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    exp_dir = tmp_path / "experiences" / "2026-01-01"
    exp_dir.mkdir(parents=True)
    target = exp_dir / "late.json"
    target.write_text("{ broken", encoding="utf-8")

    assert engine._write_json(str(target), {"ok": 1}) is True

    backups = [p for p in exp_dir.glob("late.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ broken"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}


def test_save_target_backup_failure_blocks_write(tmp_path, monkeypatch):
    """A04 反证：保存前发现目标损坏但备份失败 → 一个字节都不能写。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    target = tmp_path / "late.json"
    target.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(sl_mod, "_backup_corrupt", lambda _p: None)

    assert engine._write_json(str(target), {"ok": 1}) is False
    assert target.read_text(encoding="utf-8") == "{ broken"
    assert engine.save_warnings


def test_write_json_missing_race_is_not_corruption(tmp_path, monkeypatch):
    """A04：exists 判真后目标被删，FileNotFoundError 是竞态而非损坏。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    target = os.path.join(str(tmp_path), "race.json")
    real_exists = os.path.exists
    monkeypatch.setattr(
        sl_mod.os.path, "exists",
        lambda p: True if p == target else real_exists(p))

    assert engine._write_json(target, {"ok": 1}) is True
    assert engine.save_warnings == []
    assert engine.corrupt_backups == []
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_missing_file_is_not_corruption(tmp_path):
    """A04：正常新建经验文件不得产生备份或 save_warnings。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    engine.record_experience(
        ExperienceType.TOOL_CALL, "问题", "答案", score=9)

    assert engine.save_warnings == []
    assert engine.corrupt_backups == []
    assert list(tmp_path.rglob("*.corrupt-*")) == []
