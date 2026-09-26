"""书签管理器确定性测试。

对照 mbdsdr_ai/bookmark_manager.py（上游 gqrx/qtgui/bookmarks.cpp）。
"""
import json
from pathlib import Path

import pytest

from mbdsdr_ai.bookmark_manager import Bookmark, BookmarkManager


@pytest.fixture
def bm(tmp_path):
    return BookmarkManager(config_dir=tmp_path)


def test_add_and_list(bm):
    bm.add(Bookmark(frequency_hz=145_000_000, name="R1", modulation="NFM"))
    bm.add(Bookmark(frequency_hz=98_500_000, name="F1", modulation="WFM"))
    bookmarks = bm.all()
    # 按频率排序
    assert [b.frequency_hz for b in bookmarks] == [98_500_000, 145_000_000]


def test_remove(bm):
    bm.add(Bookmark(frequency_hz=100_000_000, name="X"))
    assert bm.remove(100_000_000) is True
    assert bm.remove(100_000_000) is False
    assert bm.all() == []


def test_update(bm):
    bm.add(Bookmark(frequency_hz=100_000_000, name="X", modulation="FM"))
    assert bm.update(100_000_000, modulation="AM", bandwidth_hz=5000) is True
    b = bm.all()[0]
    assert b.modulation == "AM" and b.bandwidth_hz == 5000
    assert bm.update(999, modulation="XX") is False


def test_in_range_binary_search(bm):
    for f in [88_000_000, 98_000_000, 145_000_000, 430_000_000]:
        bm.add(Bookmark(frequency_hz=f, name=str(f)))
    got = [b.frequency_hz for b in bm.in_range(90_000_000, 200_000_000)]
    assert got == [98_000_000, 145_000_000]


def test_search(bm):
    bm.add(Bookmark(frequency_hz=1, name="Airport VHF", tags=["aviation"]))
    bm.add(Bookmark(frequency_hz=2, name="Maritime", tags=["marine"]))
    assert [b.name for b in bm.search("air")] == ["Airport VHF"]
    assert [b.name for b in bm.search("marine")] == ["Maritime"]
    assert bm.search("") == bm.all()


def test_groups(bm):
    bm.add(Bookmark(frequency_hz=1, name="a", group="airband"))
    bm.add(Bookmark(frequency_hz=2, name="b", group="airband"))
    bm.add(Bookmark(frequency_hz=3, name="c", group="hf"))
    g = bm.groups()
    assert sorted(g.keys()) == ["airband", "hf"]


def test_csv_import_export_roundtrip(bm, tmp_path):
    bm.add(Bookmark(frequency_hz=145_000_000, name="R", modulation="NFM",
                    bandwidth_hz=12500, color="#ff0000"))
    text = bm.export_csv_text()
    assert "Frequency,Name,Modulation,Bandwidth,Color" in text

    bm2 = BookmarkManager(config_dir=tmp_path / "other")
    n = bm2.import_csv_text(text)
    assert n == 1
    b = bm2.all()[0]
    assert b.frequency_hz == 145_000_000
    assert b.modulation == "NFM" and b.color == "#ff0000"


def test_import_gqrx_semicolon_format(bm):
    """兼容 GQRX 原生分号格式 Freq;Name;Mod;BW;Tags。"""
    semi = "# comment\n1000000;BCB;AM;10000;news,local\n"
    n = bm.import_csv_text(semi)
    assert n == 1
    b = bm.all()[0]
    assert b.frequency_hz == 1_000_000 and b.modulation == "AM"
    assert "news" in b.tags and "local" in b.tags


def test_import_bad_lines_skipped(bm):
    text = "Frequency,Name,Mod,BW,Color\nnotanum,hi,FM,100,#000\n12345;ok;AM;10000;\n"
    n = bm.import_csv_text(text)
    # 第一行表头被跳过（Frequency 无法 int 化），第二行 bad freq 跳过，第三行 ok
    assert n == 1


def test_persistence_json(bm, tmp_path):
    bm.add(Bookmark(frequency_hz=7_074_000, name="CW", modulation="CW"))
    path = tmp_path / "bookmarks.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["bookmarks"][0]["name"] == "CW"

    bm2 = BookmarkManager(config_dir=tmp_path)
    assert len(bm2.all()) == 1 and bm2.all()[0].name == "CW"


def test_no_preloaded_stations(bm):
    """红线：初始库为空，不预存任何地区电台。"""
    assert bm.all() == []


def test_ai_suggest_mode(bm):
    s = bm.suggest_mode(7_074_000, 20)
    assert s["suggested_mode"] == "CW"
    s = bm.suggest_mode(145_000_000, 12_500, "weak")
    assert s["suggested_mode"] == "NFM" and s["gain_delta_db"] == 6.0
    s = bm.suggest_mode(98_500_000, 200_000)
    assert s["suggested_mode"] == "WFM"
