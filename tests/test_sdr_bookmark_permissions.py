#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""sdr_bookmark_add 的可预期写盘失败必须诚实失败，不许崩到审计里。

缺陷现场（A/B 后可用性审计抓到的唯一 BROKEN）：
``bookmarks.json`` 被占用/只读时，``BookmarkManager.save`` 抛
``PermissionError: [Errno 13] Permission denied``，异常一路穿出工具 handler，
``ToolResult`` 变成 ``exception:PermissionError``——模型只看到「调用异常」。

本文件钉住四件事：
  1. 写盘抛 PermissionError → success=False，正文含真实 errno 与「只读/占用」
     提示，且磁盘上没有产生写成功副作用；
  2. 正常写入仍然成功（tmp_path，不碰真实用户目录）；
  3. 损坏 JSON 的既有语义不变：备份原件、按空列表启动、新增仍可保存；
  4. 文件不存在时照旧新建，不报错。
"""
import errno
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.bookmark_manager import BookmarkManager  # noqa: E402
from mbdsdr_ai.frequency_manager import FrequencyManager  # noqa: E402
from mbdsdr_ai.sdr_tools import _bookmark_add  # noqa: E402

_CORRUPT = b'{"bookmarks": [{"frequency_hz": 145000000, "name": "half'


def _backups(directory):
    return sorted(p for p in directory.glob("bookmarks.json.corrupt-*")
                  if not p.name.endswith(".reason.txt"))


def test_permission_error_becomes_structured_failure(tmp_path, monkeypatch):
    path = tmp_path / "bookmarks.json"
    path.write_text(json.dumps({"tag_colors": {}, "bookmarks": []}),
                    encoding="utf-8")
    before = path.read_bytes()
    fm = FrequencyManager(str(path))

    def deny_write(self, *a, **k):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(Path, "write_text", deny_write)

    res = _bookmark_add(fm, {"name": "本地中继", "freq_mhz": 438.5})

    assert res.success is False, "写盘失败不许被包装成成功"
    assert res.content
    assert "写入书签失败" in res.content
    assert "只读" in res.content or "占用" in res.content, res.content
    assert str(errno.EACCES) in res.content, "正文必须带真实 errno"
    assert "Permission denied" in res.content, "正文必须带真实原因"
    # 无写成功副作用：原文件字节未变，书签列表里也没有新条目
    assert path.read_bytes() == before, "失败后磁盘文件被改动了"
    stored = [b.name for b in BookmarkManager(config_dir=tmp_path).all()]
    assert "本地中继" not in stored


def test_normal_add_still_succeeds(tmp_path):
    path = tmp_path / "bookmarks.json"
    fm = FrequencyManager(str(path))
    assert Path(fm.user_path).parent == tmp_path, "本用例不许碰真实用户目录"

    res = _bookmark_add(fm, {"name": "本地中继", "freq_mhz": 438.5,
                             "mode": "nfm", "bandwidth_khz": 12.5,
                             "note": "权限回归"})

    assert res.success is True
    assert "已保存书签" in res.content
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [b["name"] for b in doc["bookmarks"]] == ["本地中继"]
    assert doc["bookmarks"][0]["frequency_hz"] == 438_500_000


def test_corrupt_json_semantics_unchanged(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_bytes(_CORRUPT)
    fm = FrequencyManager(str(path))
    assert fm.load_error, "读失败必须留痕，不能静默成「没有书签」"

    res = _bookmark_add(fm, {"name": "坏文件后新增", "freq_mhz": 145.5})

    assert res.success is True
    backups = _backups(tmp_path)
    assert len(backups) == 1, "损坏文件必须备份且只备份一次"
    assert backups[0].read_bytes() == _CORRUPT, "备份内容必须是损坏时的原样字节"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [b["name"] for b in doc["bookmarks"]] == ["坏文件后新增"]


def test_missing_file_is_created_as_before(tmp_path):
    path = tmp_path / "bookmarks.json"
    assert not path.exists()
    fm = FrequencyManager(str(path))
    assert fm.load_error == ""

    res = _bookmark_add(fm, {"name": "首个书签", "freq_mhz": 100.0})

    assert res.success is True
    assert path.exists(), "文件不存在时应照旧新建，而不是报错"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [b["name"] for b in doc["bookmarks"]] == ["首个书签"]
