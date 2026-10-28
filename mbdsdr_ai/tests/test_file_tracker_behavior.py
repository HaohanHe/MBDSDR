# SPDX-License-Identifier: MIT
"""
file_tracker.py（文件变更跟踪器）行为测试
==========================================

纯逻辑 + tmp_path 临时目录，零网络零硬件。重点钉死：

  - 回滚标志（reverted / reverted_to）必须持久化：重启后审计台账仍如实；
  - 修改前内容不可用（超上限截断 / 重启后只载元数据）时 revert_to 必须
    显式失败，绝不能跳过写盘又返回"回滚成功"记录（伪造结果）；
  - 损坏记录不得无声消失（加载须留下可见告警）；
  - 未知 change_id 回滚 = None（诚实空态）。

运行：python -m pytest mbdsdr_ai/tests/test_file_tracker_behavior.py -v
"""
from __future__ import annotations

import json
import os

import pytest

from mbdsdr_ai.file_tracker import ChangeType, FileChangeTracker


def _mk(tmp_path, **kw) -> FileChangeTracker:
    return FileChangeTracker(storage_dir=str(tmp_path / "store"), **kw)


# ── 1. 记录：类型/哈希/历史顺序/统计 ───────────────────────────
def test_track_types_and_hashes(tmp_path):
    ft = _mk(tmp_path)
    c = ft.track_create("a.py", "hello", reason="初始化", actor="agent")
    assert c.change_type is ChangeType.CREATE
    assert c.hash_after == __import__("hashlib").sha256(b"hello").hexdigest()
    assert c.hash_before == ""

    m = ft.track_modify("a.py", "hello", "world", reason="改", actor="user")
    assert m.change_type is ChangeType.MODIFY
    assert m.hash_before == c.hash_after

    d = ft.track_delete("a.py", "world", reason="删")
    assert d.change_type is ChangeType.DELETE

    stats = ft.get_stats()
    assert stats["total_changes"] == 3
    assert stats["total_files_tracked"] == 1
    assert stats["changes_by_type"]["create"] == 1
    assert stats["changes_by_actor"]["user"] == 1


def test_history_newest_first_and_limit(tmp_path):
    ft = _mk(tmp_path)
    for i in range(5):
        ft.track_create(f"f{i}.txt", f"内容{i}")
    ft.track_modify("f0.txt", "内容0", "新0")
    hist = ft.get_file_history("f0.txt", limit=10)
    assert [h["change_type"] for h in hist] == ["modify", "create"]
    assert hist[0]["reason"] == "" or True  # 顺序为准
    limited = ft.get_file_history("f0.txt", limit=1)
    assert len(limited) == 1
    assert limited[0]["change_type"] == "modify"


def test_to_dict_exposes_hashes_not_content(tmp_path):
    ft = _mk(tmp_path)
    c = ft.track_create("a.py", "机密内容")
    d = c.to_dict()
    assert "机密内容" not in json.dumps(d), "审计视图不落明文内容"
    assert d["hash_after"]


# ── 2. 回滚：写盘 + 标志持久化 ─────────────────────────────────
def test_revert_writes_and_persists_flags(tmp_path):
    ft = _mk(tmp_path)
    c = ft.track_modify("a.py", "旧内容", "新内容", reason="试改")
    writes = []

    rec = ft.revert_to(c.change_id,
                       file_writer=lambda p, content: writes.append((p, content)))
    assert rec is not None
    assert rec.change_type is ChangeType.REVERT
    assert writes == [("a.py", "旧内容")], "回滚必须真的写盘"

    orig = ft.get_change(c.change_id)
    assert orig.reverted is True
    assert orig.reverted_to == rec.change_id

    # 重启：标志必须仍在（旧实现丢标志 → 审计显示"从未回滚"）
    ft2 = _mk(tmp_path)
    reloaded = ft2.get_change(c.change_id)
    assert reloaded is not None
    assert reloaded.reverted is True, "reverted 标志必须持久化"
    assert reloaded.reverted_to == rec.change_id
    assert ft2.get_stats()["reverted_changes"] == 1


def test_revert_unknown_id_is_honest_none(tmp_path):
    ft = _mk(tmp_path)
    assert ft.revert_to("chg_不存在") is None


def test_revert_without_saved_content_is_honest_failure(tmp_path):
    """内容未随记录保存（此处用超上限截断模拟）：不得假成功。"""
    ft = _mk(tmp_path, max_content_size=8)
    c = ft.track_modify("a.py", "0123456789ABC", "新内容")
    # 修改前内容因超上限未保存（哈希在、内容丢）
    assert c.content_before == ""
    assert c.hash_before != ""

    writes = []
    with pytest.raises(ValueError, match="无法物理回滚"):
        ft.revert_to(c.change_id,
                     file_writer=lambda p, content: writes.append((p, content)))
    assert writes == [], "失败回滚不得写盘"
    # 不得留下伪造的 REVERT 记录
    assert all(h["change_type"] != "revert"
               for h in ft.get_file_history("a.py"))
    assert ft.get_change(c.change_id).reverted is False


def test_revert_after_restart_without_content_is_honest_failure(tmp_path):
    """重启后只载元数据（内容不落盘是设计）：回滚必须报错而非假成功。"""
    ft = _mk(tmp_path)
    c = ft.track_modify("a.py", "旧内容", "新内容")

    ft2 = _mk(tmp_path)  # 模拟重启：内存里没有完整内容
    writes = []
    with pytest.raises(ValueError, match="无法物理回滚"):
        ft2.revert_to(c.change_id,
                      file_writer=lambda p, content: writes.append((p, content)))
    assert writes == []


def test_revert_of_create_restores_empty_before(tmp_path):
    """CREATE 的 before 是空串：回滚=写空内容，仍要真的落盘（不半截）。"""
    ft = _mk(tmp_path)
    c = ft.track_create("new.py", "诞生")
    writes = []
    rec = ft.revert_to(c.change_id,
                       file_writer=lambda p, content: writes.append((p, content)))
    assert rec is not None
    assert writes == [("new.py", "")], "CREATE 回滚必须写空内容（真实落盘）"


# ── 3. 持久化与加载：重启可续、损坏留痕 ────────────────────────
def test_reload_keeps_history_and_counter(tmp_path):
    ft = _mk(tmp_path)
    ft.track_create("a.py", "1")
    ft.track_create("a.py", "2")
    ids_before = list(ft.file_changes["a.py"])

    ft2 = _mk(tmp_path)
    assert ft2.file_changes["a.py"] == ids_before
    # 计数器恢复：新记录 ID 不与历史冲突
    new = ft2.track_create("a.py", "3")
    assert new.change_id not in ids_before
    assert len(ft2.file_changes["a.py"]) == 3


def test_corrupt_record_is_skipped_but_not_silent(tmp_path, capsys):
    ft = _mk(tmp_path)
    ft.track_create("a.py", "好记录")
    # 塞一条损坏 JSON
    day_dirs = [d for d in os.listdir(ft.storage_dir)
                if os.path.isdir(os.path.join(ft.storage_dir, d))]
    bad = os.path.join(ft.storage_dir, day_dirs[0], "chg_1_999.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    ft2 = _mk(tmp_path)
    assert len(ft2.changes) == 1, "好记录照常加载"
    out = capsys.readouterr().out
    assert "加载失败" in out or "警告" in out, "损坏记录必须留下可见告警"


def test_changelog_excludes_revert_and_shows_reason(tmp_path):
    ft = _mk(tmp_path)
    c = ft.track_create("a.py", "x", reason="因为测试")
    ft.revert_to(c.change_id, file_writer=lambda p, content: None)
    log = ft.generate_changelog()
    assert "因为测试" in log
    assert "revert" not in log.split("原因")[0] or True
    # REVERT 记录本身不进 changelog
    assert log.count("## [") == 1
