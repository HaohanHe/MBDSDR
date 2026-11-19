#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""书签/频率存储的写入失败必须可读、可预测、且不产生半截写入。

上一轮（Task 154）只在 ``sdr_bookmark_add`` 的 handler 里补了 ``OSError``；
同源的 ``FrequencyManager.add/remove``、``BookmarkManager.update``、
``BookmarkManager.import_csv_text`` 共用 ``BookmarkManager.save()``，只读/占用
盘上仍会抛裸 ``PermissionError``。本轮把失败下沉到存储层：``save()`` 捕获
``OSError`` 后抛 ``StoreWriteError``（带 ``path`` / ``reason``），所有写入口
因此拿到同一句人话。

本文件钉住：
  1. 四个写入口（bookmark_add / frequency_remove / frequency_add /
     import_csv_text）+ ``BookmarkManager.update`` 在写盘抛
     ``PermissionError`` 时如实失败，磁盘文件逐字节未变；
  2. 唯一的 ``ToolResult`` 写 handler（``sdr_bookmark_add``）返回
     ``success=False`` 且正文含真实原因与路径；
  3. 正常写入仍成功（tmp_path，不碰真实用户目录）；
  4. 「文件不存在」的读路径语义不变；
  5. ``ValueError``（坏参数）仍按原语义向上抛，不被写失败捕获吞掉。
"""
import errno
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import bookmark_manager as bm_mod  # noqa: E402
from mbdsdr_ai.bookmark_manager import (  # noqa: E402
    BookmarkManager, StoreWriteError)
from mbdsdr_ai.frequency_manager import FrequencyManager  # noqa: E402
from mbdsdr_ai.sdr_tools import _bookmark_add  # noqa: E402


def _deny_write(self, *args, **kwargs):
    """模拟只读/被占用盘：等价于 Windows 只读属性或 POSIX EACCES。"""
    raise PermissionError(errno.EACCES, "Permission denied", str(self))


def _seed(path: Path, name: str = "已存在") -> bytes:
    path.write_text(json.dumps({
        "tag_colors": {},
        "bookmarks": [{"frequency_hz": 145_500_000, "name": name,
                       "modulation": "NFM", "bandwidth_hz": 12_500,
                       "color": "#888888", "tags": [], "group": "",
                       "start_hz": 0.0, "end_hz": 0.0, "note": ""}],
    }, ensure_ascii=False), encoding="utf-8")
    return path.read_bytes()


def _assert_readable(exc: StoreWriteError, path: Path) -> None:
    """失败必须带真实原因与路径，而不是一句无信息量的「写入失败」。"""
    assert isinstance(exc, StoreWriteError)
    assert exc.path == str(path), exc.path
    assert "PermissionError" in exc.reason, exc.reason
    assert "Permission denied" in exc.reason, exc.reason
    assert str(errno.EACCES) in exc.reason, exc.reason
    text = str(exc)
    assert "写入失败" in text and str(path) in text, text


def test_bookmark_add_handler_failure_is_readable(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    before = _seed(path)
    fm = FrequencyManager(str(path))
    monkeypatch.setattr(Path, "write_text", _deny_write)

    res = _bookmark_add(fm, {"name": "本地中继", "freq_mhz": 438.5})

    assert res.success is False, "写盘失败不许被包装成成功"
    assert "写入书签失败" in res.content
    assert "Permission denied" in res.content, res.content
    assert str(errno.EACCES) in res.content, "正文必须带真实 errno"
    assert str(path) in res.content, "正文必须带路径"
    assert "只读" in res.content or "占用" in res.content, res.content
    assert path.read_bytes() == before, "失败后磁盘文件被改动了"


def test_frequency_add_raises_typed_failure(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    before = _seed(path)
    fm = FrequencyManager(str(path))
    monkeypatch.setattr(Path, "write_text", _deny_write)

    with pytest.raises(StoreWriteError) as ei:
        fm.add(name="新频点", freq_hz=438_500_000, mode="NFM")

    _assert_readable(ei.value, path)
    assert path.read_bytes() == before, "失败后磁盘文件被改动了"
    assert "新频点" not in [b.name for b in fm.bookmarks]


def test_frequency_remove_raises_typed_failure(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    before = _seed(path, name="待删频率")
    fm = FrequencyManager(str(path))
    monkeypatch.setattr(Path, "write_text", _deny_write)

    with pytest.raises(StoreWriteError) as ei:
        fm.remove("待删频率")

    _assert_readable(ei.value, path)
    assert path.read_bytes() == before, "删除失败后磁盘文件被改动了"
    assert "待删频率" in [b.name for b in fm.bookmarks], "内存里也不许假装删掉"


def test_bookmark_update_raises_typed_failure(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    before = _seed(path, name="旧名字")
    bm = BookmarkManager(config_dir=tmp_path, bookmarks_path=path)
    monkeypatch.setattr(Path, "write_text", _deny_write)

    with pytest.raises(StoreWriteError) as ei:
        bm.update(145_500_000, name="新名字")

    _assert_readable(ei.value, path)
    assert path.read_bytes() == before, "更新失败后磁盘文件被改动了"


def test_import_csv_text_raises_typed_failure(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    before = _seed(path)
    bm = BookmarkManager(config_dir=tmp_path, bookmarks_path=path)
    monkeypatch.setattr(Path, "write_text", _deny_write)

    with pytest.raises(StoreWriteError) as ei:
        bm.import_csv_text("438500000,导入中继,NFM,12500,#888888")

    _assert_readable(ei.value, path)
    assert path.read_bytes() == before, "导入失败后磁盘文件被改动了"


def test_normal_writes_still_succeed(tmp_path):
    # 四个写入口正常路径都要能落盘：失败下沉不许把成功也拦掉。
    add_path = tmp_path / "add.json"
    fm = FrequencyManager(str(add_path))
    assert _bookmark_add(fm, {"name": "本台中继", "freq_mhz": 438.5}).success
    assert [b.name for b in fm.bookmarks if not b.builtin] == ["本台中继"]
    assert fm.remove("本台中继") is True

    update_path = tmp_path / "update.json"
    _seed(update_path)
    bm = BookmarkManager(config_dir=tmp_path, bookmarks_path=update_path)
    assert bm.update(145_500_000, name="改过的名字") is True

    import_path = tmp_path / "import.json"
    _seed(import_path)
    bm2 = BookmarkManager(config_dir=tmp_path, bookmarks_path=import_path)
    assert bm2.import_csv_text("438500000,导入中继,NFM,12500,#888888") == 1

    doc = json.loads(import_path.read_text(encoding="utf-8"))
    assert "导入中继" in [b["name"] for b in doc["bookmarks"]]


def test_missing_file_read_semantics_unchanged(tmp_path):
    path = tmp_path / "absent.json"
    assert not path.exists()

    bm = BookmarkManager(config_dir=tmp_path, bookmarks_path=path)
    assert bm.load() is False, "文件不存在仍按空书签列表启动"
    assert bm.last_error is None
    assert bm.all() == []

    fm = FrequencyManager(str(path))
    assert fm.load_error == "", "FrequencyManager 不把「文件不存在」当错误"
    assert [b for b in fm.bookmarks if not b.builtin] == []
    assert not path.exists(), "只读不会顺手建文件"


def test_corrupt_backup_failure_does_not_fake_success(tmp_path, monkeypatch):
    """损坏且备份失败时，旧实现 ``save()`` 返回 False 被忽略 → 假成功。

    现在同样抛 StoreWriteError：只读盘上「rename 备份失败 + 写新文件」这一
    最常见组合必须如实失败，坏文件字节一个都不能动。
    """
    path = tmp_path / "bookmarks.json"
    path.write_bytes(b'{"bookmarks": [{"frequency_hz": 145000000, "na')
    before = path.read_bytes()
    monkeypatch.setattr(bm_mod, "backup_corrupt_bookmarks", lambda p: None)
    fm = FrequencyManager(str(path))

    with pytest.raises(StoreWriteError) as ei:
        fm.add(name="不该成功", freq_hz=145_000_000)

    assert "备份失败" in ei.value.reason, ei.value.reason
    assert path.read_bytes() == before, "备份失败后坏文件被改写"
    assert "不该成功" not in [b.name for b in fm.bookmarks]


def test_bad_frequency_value_still_value_error(tmp_path):
    # 写失败只接 StoreWriteError；坏参数仍是 ValueError，不能被一句话吞掉。
    path = tmp_path / "bookmarks.json"
    fm = FrequencyManager(str(path))

    with pytest.raises(ValueError):
        _bookmark_add(fm, {"name": "坏频率", "freq_mhz": "not-a-number"})
