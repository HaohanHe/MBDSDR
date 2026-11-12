# SPDX-License-Identifier: MIT
"""损坏证据（``*.corrupt-*.reason.txt``）四条反证。

1. 备份成功、理由写入失败 → 备份仍在、程序不崩、只记一行日志；
   并断言顺序是「先备份、后写理由」（写理由时备份必须已存在）。
2. 目录不存在 → 跳过不报错；目录存在但读不了 → 日志必须写明
   「未检查 <dir>（原因）」，不许静默。
3. 7 组备份、其中 2 组超过 30 天 → 清点报 2 个可清理，14 个文件全在
   （只读，不删）。
4. 启动日志落点全不可写 → 不崩、不额外造文件、退化成内存缓冲。

本文件不导入 PySide6，可在没有 QApplication 的进程里直接跑。
"""
import logging
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.bookmark_manager as bm  # noqa: E402
import mbdsdr_ai.corrupt_evidence as ce  # noqa: E402
from desktop import startup_paths as sp  # noqa: E402

CORRUPT = b'{"bookmarks": [{"frequency_hz": 145000000, "name": "half'


# ---------------------------------------------------------------------------
# 1. 备份成功、理由写入失败
# ---------------------------------------------------------------------------
def test_reason_write_failure_keeps_backup_and_does_not_crash(
        tmp_path, monkeypatch, caplog):
    path = tmp_path / "bookmarks.json"
    path.write_bytes(CORRUPT)
    seen = {}

    def failing_write(backup, reason, logger=None):
        # 顺序证据：写理由的那一刻，备份必须已经在磁盘上。
        seen["backup_exists_at_write"] = os.path.exists(backup)
        os.makedirs(backup + ce.REASON_SUFFIX)   # 让真实写入撞上目录
        return real_write(backup, reason, logger)

    real_write = ce.write_reason
    monkeypatch.setattr(bm, "write_reason", failing_write)

    with caplog.at_level(logging.WARNING):
        mgr = bm.BookmarkManager(config_dir=tmp_path)   # 不许抛

    assert seen["backup_exists_at_write"] is True, "理由先于备份写入了"
    assert mgr.corrupt_backup and os.path.exists(mgr.corrupt_backup)
    with open(mgr.corrupt_backup, "rb") as f:
        assert f.read() == CORRUPT, "备份内容被破坏"
    assert "损坏理由写入失败" in caplog.text

    # 写理由失败不影响后续新增；备份仍在。
    mgr.add(bm.Bookmark(frequency_hz=145_500_000, name="新的"))
    assert os.path.exists(mgr.corrupt_backup)


def test_reason_file_is_written_after_successful_backup(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_bytes(CORRUPT)
    mgr = bm.BookmarkManager(config_dir=tmp_path)
    reason_files = list(tmp_path.glob("*.reason.txt"))
    assert len(reason_files) == 1, "备份成功却没有写理由文件"
    text = reason_files[0].read_text(encoding="utf-8")
    assert "书签文件读取失败" in text
    assert text.count("\n") == 1, "理由文件必须是单行"


# ---------------------------------------------------------------------------
# 2. 目录不存在 / 读不了
# ---------------------------------------------------------------------------
def test_missing_dir_skipped_and_unreadable_dir_reported(tmp_path, monkeypatch):
    missing = tmp_path / "not-there"
    locked = tmp_path / "locked"
    locked.mkdir()
    real_listdir = os.listdir

    def fake_listdir(target):
        if os.path.abspath(str(target)) == os.path.abspath(str(locked)):
            raise PermissionError(13, "Access denied")
        return real_listdir(target)

    monkeypatch.setattr(ce.os, "listdir", fake_listdir)
    report = ce.scan_corruption_evidence([str(missing), str(locked)])

    assert report["total"] == 0
    assert [item["dir"] for item in report["unreadable"]] == [str(locked)]
    assert f"未检查 {locked}" in report["message"]
    assert "Access denied" in report["message"]

    lines = []
    ce.corruption_startup_report([str(missing), str(locked)],
                                 append_line=lines.append)
    assert lines and "未检查" in lines[-1]


# ---------------------------------------------------------------------------
# 3. 超期只清点，不删除
# ---------------------------------------------------------------------------
def test_stale_backups_are_counted_not_deleted(tmp_path):
    now = time.time()
    for i in range(7):
        backup = tmp_path / f"bookmarks.json.corrupt-2026010{i}-120000"
        backup.write_bytes(b"stale-evidence")
        reason = tmp_path / (backup.name + ce.REASON_SUFFIX)
        reason.write_text("boom\n", encoding="utf-8")
        age = 40 if i < 2 else 1          # 前 2 组超过 30 天
        stamp = (now - age * 86400,) * 2
        os.utime(backup, stamp)
        os.utime(reason, stamp)

    report = ce.scan_corruption_evidence([str(tmp_path)], now=now)

    assert report["stale"] == 2
    assert report["total"] == 7
    assert "2 个超过 30 天的损坏备份" in report["message"]
    assert "可手动清理" in report["message"]
    assert len(os.listdir(tmp_path)) == 14, "清点过程删除了文件"


# ---------------------------------------------------------------------------
# 4. 日志落点全不可写 → 内存缓冲，不造文件
# ---------------------------------------------------------------------------
def test_unwritable_log_falls_back_to_memory_buffer(tmp_path, monkeypatch):
    backup = tmp_path / "fm_presets.json.corrupt-20261112-210000"
    backup.write_bytes(b"evidence")
    (tmp_path / (backup.name + ce.REASON_SUFFIX)).write_text(
        "boom\n", encoding="utf-8")
    before = sorted(os.listdir(tmp_path))

    monkeypatch.setattr(sp, "startup_log_path", lambda: "")
    report = ce.corruption_startup_report(
        [str(tmp_path)], append_line=sp.append_startup_log)

    assert report["message"]
    assert sp.buffered_log()[-1].startswith("[配置] ")
    assert "已备份" in sp.buffered_log()[-1]
    assert sorted(os.listdir(tmp_path)) == before, "无持久落点时仍造了文件"


def test_default_scan_directories_covers_bookmarks_and_settings():
    base = os.path.join(os.path.expanduser("~"), ".mbdsdr")
    dirs = ce.default_scan_directories("/data/config/desktop_settings.json")
    assert dirs[0] == base
    assert "/data/config" in dirs
    # 已知数据子目录：清点只做一级 listdir，不加这几项会漏掉
    # version_store / guardian / workflow_engine 的损坏备份。
    for sub in ("evolution", "guardian_snapshots", "workflows"):
        assert os.path.join(base, sub) in dirs
    # 配置路径只有文件名（无目录）时不额外加一个空目录。
    no_dir = ce.default_scan_directories("desktop_settings.json")
    assert no_dir[0] == base
    assert "" not in no_dir


def test_version_store_backup_is_reported_from_evolution_dir(
        tmp_path, monkeypatch):
    """version_store 的索引备份在 ~/.mbdsdr/evolution 下，启动清点必须看到。"""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    evolution = tmp_path / ".mbdsdr" / "evolution"
    evolution.mkdir(parents=True)
    backup = evolution / "versions.json.corrupt-20261112-221000"
    backup.write_text("{}", encoding="utf-8")
    ce.write_reason(str(backup), "版本索引损坏：测试", None)

    lines = []
    report = ce.corruption_startup_report(
        ce.default_scan_directories(str(tmp_path / "config" / "settings.json")),
        append_line=lines.append)

    assert report["total"] == 1
    assert str(backup) + ce.REASON_SUFFIX in report["recent"]
    assert lines and "已备份" in lines[0], lines
