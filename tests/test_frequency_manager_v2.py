# SPDX-License-Identifier: MIT
"""
frequency_manager_v2 单测：CRUD / 持久化 / nearest / CSV / 自动分类
====================================================================
对应 docs/learn/sdrpp_modules.md 第 2 节。用临时目录，不碰真实 ~/.mbdsdr。
"""
import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from mbdsdr_ai.frequency_manager_v2 import (
    FrequencyManagerV2, BookmarkV2, DEFAULT_GROUPS,
    UnsupportedBookmarkVersion,
)


@pytest.fixture
def fm(tmp_path):
    p = tmp_path / "bookmarks_v2.json"
    return FrequencyManagerV2(path=str(p))


def test_default_groups_have_no_presets(fm):
    # 红线：默认分组存在但不带任何书签
    keys = {g["key"] for g in fm.list_groups()}
    assert {"aviation", "marine", "ham", "broadcast", "satellite"} <= keys
    for g in fm.list_groups():
        assert fm.list(group=g["key"]) == []


def test_add_remove_update(fm):
    fm.add(BookmarkV2(name="test", freq_hz=100e6, mode="NFM", group="ham"))
    assert len(fm.list()) == 1
    # 重名拒绝
    with pytest.raises(ValueError):
        fm.add(BookmarkV2(name="test", freq_hz=101e6, group="ham"))
    # 更新
    b = fm.update("test", notes="hello", bandwidth_hz=12.5e3)
    assert b is not None and b.notes == "hello" and b.bandwidth_hz == 12.5e3
    # 删除
    assert fm.remove("test") is True
    assert fm.list() == []
    assert fm.remove("nope") is False


def test_persistence_roundtrip(fm, tmp_path):
    fm.add(BookmarkV2(name="a", freq_hz=145e6, mode="FM", group="ham",
                      tags=["local"], notes="n"))
    path = fm.path
    # 新实例读同一文件
    fm2 = FrequencyManagerV2(path=path)
    bms = fm2.list()
    assert len(bms) == 1
    assert bms[0].name == "a" and bms[0].freq_hz == 145e6
    assert bms[0].tags == ["local"]
    # 原子写：文件存在且是合法 JSON
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == 2


def test_nearest(fm):
    fm.add(BookmarkV2(name="a", freq_hz=100e6, group="ham"))
    fm.add(BookmarkV2(name="b", freq_hz=101e6, group="ham"))
    fm.add(BookmarkV2(name="c", freq_hz=200e6, group="broadcast"))

    hit = fm.nearest(100.5e6)
    assert hit is not None
    bm, dist = hit
    assert bm.name == "a"
    assert dist == pytest.approx(0.5e6)

    # max_distance 过滤：170e6 离 c(200e6)=30e6，离 b(101e6)=69e6
    assert fm.nearest(150e6, max_distance=10e6) is None
    hit = fm.nearest(170e6, max_distance=60e6)
    assert hit[0].name == "c"


def test_csv_roundtrip(fm, tmp_path):
    fm.add(BookmarkV2(name="a", freq_hz=100e6, mode="NFM", group="ham",
                      tags=["t1", "t2"]))
    fm.add(BookmarkV2(name="b", freq_hz=101e6, mode="WFM", group="broadcast"))
    csv_path = str(tmp_path / "out.csv")
    n = fm.export_csv(csv_path)
    assert n == 2

    fm2 = FrequencyManagerV2(path=str(tmp_path / "new.json"))
    n2 = fm2.import_csv(csv_path)
    assert n2 == 2
    names = {b.name for b in fm2.list()}
    assert names == {"a", "b"}


def test_classify_signal(fm):
    assert fm.classify_signal(120e6, mode_hint="AM") == "aviation"
    assert fm.classify_signal(98e6, mode_hint="WFM") == "broadcast"
    assert fm.classify_signal(156.8e6) == "marine"
    assert fm.classify_signal(145e6) == "ham"


def test_missing_version_is_v1_migrated_and_rewritten(tmp_path, caplog):
    """没有 version 的旧文件：按 v1 读，读出后补写 version=2。"""
    path = tmp_path / "bookmarks_v2.json"
    path.write_text(json.dumps({
        "groups": {"ham": {"label": "业余", "color": "#43A047", "icon": "antenna"}},
        "bookmarks": [{"name": "old", "freq_hz": 145e6, "group": "ham"}],
    }), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="mbdsdr_ai.frequency_manager_v2"):
        fm = FrequencyManagerV2(path=str(path))

    assert [b.name for b in fm.list()] == ["old"]
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert "缺少 version 字段" in caplog.text
    assert "已补写 version=2" in caplog.text


def test_unsupported_version_is_rejected_and_file_untouched(tmp_path, caplog):
    """version=99：明确拒绝并报错，不猜语义、不改写原文件。"""
    path = tmp_path / "bookmarks_v2.json"
    payload = {
        "version": 99,
        "groups": {"ham": {"label": "业余"}},
        "bookmarks": [{"name": "keep", "freq_hz": 145e6, "group": "ham"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="mbdsdr_ai.frequency_manager_v2"):
        with pytest.raises(UnsupportedBookmarkVersion, match="version=99"):
            FrequencyManagerV2(path=str(path))

    assert "不受支持" in caplog.text
    # 拒绝路径不得静默覆盖：原文件仍是 version 99。
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 99
