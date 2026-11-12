#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""frequency_manager 与书签面板共用一份 bookmarks.json（单真值源）。

修复前的两条路径各自读写同一个文件：
  * 书签面板（BookmarkManager）已经会「损坏 → 备份 → 拒绝覆盖」；
  * FrequencyManager 却自己 ``json.load`` + ``json.dump``，读失败就当空列表，
    下一次保存把用户书签整份覆盖成只剩内置项，备份也一并抹掉。

本文件钉住三件事：
  1. 坏文件被 FrequencyManager 保存前一定有备份，且备份字节原样；
  2. 书签面板刚写的条目不会被 FrequencyManager 的下一次保存吃掉；
  3. 把修复前的旧写回路径原样喂给同一组断言，必须断言失败。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.bookmark_manager import Bookmark as StoreBookmark  # noqa: E402
from mbdsdr_ai.bookmark_manager import BookmarkManager  # noqa: E402
from mbdsdr_ai.frequency_manager import FrequencyManager  # noqa: E402

_BAD = '{"bookmarks": [{"name": "半截'


def _backups(path) -> list:
    directory, name = os.path.split(str(path))
    return sorted(
        os.path.join(directory, entry)
        for entry in os.listdir(directory)
        if entry.startswith(name + ".corrupt-")
        and not entry.endswith(".reason.txt"))


def _assert_corrupt_evidence_kept(path, original: str) -> None:
    """坏文件被写回前必须先备份，且备份内容与坏文件逐字节一致。"""
    backups = _backups(path)
    assert backups, "损坏文件没有备份就被整份写回"
    with open(backups[0], encoding="utf-8") as f:
        assert f.read() == original, "备份内容不是损坏时的原样字节"


def test_corrupt_bookmarks_are_backed_up_before_write(tmp_path):
    path = str(tmp_path / "bookmarks.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_BAD)

    fm = FrequencyManager(path)
    assert fm.load_error, "读失败必须留痕，不能静默成「没有书签」"
    assert fm.corrupt_backup and os.path.exists(fm.corrupt_backup)

    fm.add("新台", 100_000_000, "NFM", "测试")

    _assert_corrupt_evidence_kept(path, _BAD)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    assert isinstance(doc, dict), "必须写成 BookmarkManager 的共享格式"
    assert [b["name"] for b in doc["bookmarks"]] == ["新台"]


def test_second_save_does_not_create_an_extra_backup(tmp_path):
    path = str(tmp_path / "bookmarks.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_BAD)

    fm = FrequencyManager(path)
    fm.add("第一个", 100_000_000)
    fm.add("第二个", 200_000_000)

    assert len(_backups(path)) == 1, "二次写盘不许重复造备份"
    _assert_corrupt_evidence_kept(path, _BAD)


def test_panel_bookmark_survives_frequency_manager_save(tmp_path):
    """两个真值源：面板先写，FrequencyManager 再写，两边条目都得在。"""
    BookmarkManager(config_dir=tmp_path).add(
        StoreBookmark(frequency_hz=145_500_000, name="面板写的",
                      modulation="NFM"))

    fm = FrequencyManager(str(tmp_path / "bookmarks.json"))
    fm.add("频率管理器写的", 433_920_000, "NFM")

    names = {b.name for b in BookmarkManager(config_dir=tmp_path).all()}
    assert names == {"面板写的", "频率管理器写的"}


def test_remove_goes_through_the_shared_store(tmp_path):
    panel = BookmarkManager(config_dir=tmp_path)
    panel.add(StoreBookmark(frequency_hz=145_500_000, name="要删的"))
    panel.add(StoreBookmark(frequency_hz=433_920_000, name="保留的"))

    fm = FrequencyManager(str(tmp_path / "bookmarks.json"))
    assert fm.remove("要删的") is True

    names = {b.name for b in BookmarkManager(config_dir=tmp_path).all()}
    assert names == {"保留的"}


class _LegacyFrequencyManager:
    """修复前的写回路径（原样保留，仅用于反证）。

    坏文件 → 空用户列表；add() → 裸列表整份覆盖，没有备份。
    """

    def __init__(self, user_path):
        self.user_path = user_path
        self.users = []
        try:
            with open(user_path, encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            data = []
        if isinstance(data, dict):
            data = data.get("bookmarks", [])
        if isinstance(data, list):
            self.users = [d for d in data if isinstance(d, dict)]

    def add(self, name, freq_hz):
        self.users.append({"name": name, "freq_hz": freq_hz})
        with open(self.user_path, "w", encoding="utf-8") as f:
            json.dump(self.users, f, ensure_ascii=False, indent=2)


def test_legacy_write_back_path_turns_red(tmp_path):
    """反证：旧路径喂给同一组断言，必须炸。"""
    path = str(tmp_path / "bookmarks.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_BAD)

    legacy = _LegacyFrequencyManager(path)
    legacy.add("新台", 100_000_000)

    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(path, _BAD)
