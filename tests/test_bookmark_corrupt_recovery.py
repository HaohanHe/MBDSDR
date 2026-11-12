#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""书签损坏恢复：坏文件必须备份、且后续新增不得吞掉旧证据。

三条反证：
  1. 喂截断 JSON → 生成一个 ``*.corrupt-*`` 备份，字节与原件完全一致；
  2. 坏文件状态下新增一条书签 → 备份仍在、旧字节没被新写入改写；
  3. 把备份函数换成"只返回路径、不真改名"的假实现 → 上述断言必须红。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.bookmark_manager as bm_mod  # noqa: E402
from mbdsdr_ai.bookmark_manager import Bookmark, BookmarkManager  # noqa: E402

CORRUPT = b'{"bookmarks": [{"frequency_hz": 145000000, "name": "half'


def _backups(path):
    # 理由文件 ``*.reason.txt`` 与备份同名同前缀，清点备份时要排除。
    return sorted(p for p in path.parent.glob(path.name + ".corrupt-*")
                  if not p.name.endswith(".reason.txt"))


def _corrupt_bookmarks(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "bookmarks.json"
    path.write_bytes(CORRUPT)
    return path, BookmarkManager(config_dir=root)


def _assert_backup_intact(root):
    path, mgr = _corrupt_bookmarks(root)
    assert mgr.all() == []
    backups = _backups(path)
    assert len(backups) == 1, "损坏文件没有留下备份"
    assert backups[0].read_bytes() == CORRUPT, "备份内容与原件不一致"
    assert "已备份" in (mgr.last_error or "")
    return path, mgr, backups


def _assert_add_keeps_backup(root):
    path, mgr, backups = _assert_backup_intact(root)
    mgr.add(Bookmark(frequency_hz=145_500_000, name="新台"))
    assert _backups(path) == backups, "新增书签后备份消失了"
    assert backups[0].read_bytes() == CORRUPT, "旧证据被新写入吞掉"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [b["frequency_hz"] for b in data["bookmarks"]] == [145_500_000]


def test_truncated_file_is_backed_up(tmp_path):
    _assert_backup_intact(tmp_path)


def test_add_after_corrupt_keeps_old_evidence(tmp_path):
    _assert_add_keeps_backup(tmp_path)


def test_without_real_backup_the_check_turns_red(tmp_path, monkeypatch):
    # 假备份：返回一个路径但不真改名 —— 等价于"备份逻辑被去掉"。
    monkeypatch.setattr(
        bm_mod, "_backup_corrupt_bookmarks",
        lambda p: f"{p}.corrupt-fake")
    with pytest.raises(AssertionError):
        _assert_add_keeps_backup(tmp_path / "mutation")
