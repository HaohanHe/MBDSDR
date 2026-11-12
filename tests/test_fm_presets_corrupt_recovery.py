#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""FM 预设损坏恢复：坏文件必须备份、且后续保存不得吞掉旧证据。

与书签那处的差异：预设有模块级状态（``_LAST_ERROR`` / ``_SAVE_BLOCKED``），
不是实例字段。因此这里用 autouse fixture 每个用例重置这三个全局，
并把 ``PRESETS_FILE`` 指到 tmp_path，避免碰到真实的 ``~/.mbdsdr``。

三条反证：
  1. 喂截断 JSON → 生成一个 ``*.corrupt-*`` 备份，字节与原件完全一致；
  2. 坏文件状态下保存一次 → 备份仍在、旧字节没被新写入改写；
  3. 把备份函数换成"只返回路径、不真改名"的假实现 → 上述断言必须红。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.fm_scan as fm  # noqa: E402

CORRUPT = b'{"stations": [{"freq_mhz": 98.0, "name": "half'
EMPTY = {"stations": [], "last_scan": None, "updated_at": None}


@pytest.fixture(autouse=True)
def presets(monkeypatch, tmp_path):
    """把预设文件指到临时目录，并重置模块级损坏状态。"""
    path = tmp_path / "fm_presets.json"
    monkeypatch.setattr(fm, "PRESETS_DIR", str(tmp_path))
    monkeypatch.setattr(fm, "PRESETS_FILE", str(path))
    monkeypatch.setattr(fm, "_LAST_ERROR", None)
    monkeypatch.setattr(fm, "_CORRUPT_BACKUP", None)
    monkeypatch.setattr(fm, "_SAVE_BLOCKED", False)
    return path


def _backups(path):
    return sorted(path.parent.glob(path.name + ".corrupt-*"))


def _assert_backup_intact(presets):
    presets.write_bytes(CORRUPT)
    data = fm.load_presets()
    assert data == EMPTY
    backups = _backups(presets)
    assert len(backups) == 1, "损坏文件没有留下备份"
    assert backups[0].read_bytes() == CORRUPT, "备份内容与原件不一致"
    assert "已备份" in (fm.presets_last_error() or "")
    return backups


def _assert_save_keeps_backup(presets):
    backups = _assert_backup_intact(presets)
    saved_path = fm.save_presets([{"freq_mhz": 98.0, "name": "新台"}])
    assert saved_path == str(presets)
    assert _backups(presets) == backups, "保存后备份消失了"
    assert backups[0].read_bytes() == CORRUPT, "旧证据被新写入吞掉"
    saved = json.loads(presets.read_text(encoding="utf-8"))
    assert [s["freq_mhz"] for s in saved["stations"]] == [98.0]


def test_truncated_file_is_backed_up(presets):
    _assert_backup_intact(presets)


def test_save_after_corrupt_keeps_old_evidence(presets):
    _assert_save_keeps_backup(presets)


def test_without_real_backup_the_check_turns_red(presets, monkeypatch):
    # 假备份：返回一个路径但不真改名 —— 等价于"备份逻辑被去掉"。
    monkeypatch.setattr(
        fm, "_backup_corrupt_presets", lambda p: f"{p}.corrupt-fake")
    with pytest.raises(AssertionError):
        _assert_save_keeps_backup(presets)
