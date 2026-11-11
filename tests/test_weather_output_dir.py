# SPDX-License-Identifier: MIT
"""WeatherPanel 产物落点：用户目录 + 另存为 + 历史列表（P2-06 / W71）。

旧行为：解码/后处理产物写进 ``<repo>/mbdsdr_ai/artifacts/`` —— 跑一次
解码就污染源码树（git status 多一堆未跟踪 PNG，清理时还容易误删源码）。
新行为：一律落 ``~/.mbdsdr/weather``，源码树里**一个字节都不写**；用户
可以另存为，也能在历史列表里找回之前的产物。

反证目标：把 ``_out_png_path`` 改回源码树落点，本文件第一条用例必须变红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import weather_panel as wp  # noqa: E402

_SOURCE_ARTIFACTS = os.path.join(_ROOT, "mbdsdr_ai", "artifacts")


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qtapp, tmp_path, monkeypatch):
    out_dir = tmp_path / "weather_out"
    out_dir.mkdir()
    monkeypatch.setattr(wp, "_OUT_DIR", str(out_dir))
    return wp.WeatherPanel(), out_dir


class TestOutputDir:
    def test_out_png_path_is_in_user_dir_not_source_tree(self, panel):
        p, out_dir = panel
        path = p._out_png_path("decode")
        assert os.path.abspath(path).startswith(str(out_dir))
        assert os.path.dirname(path) == str(out_dir)
        # 红线：绝不写回源码树（旧实现落在 <repo>/mbdsdr_ai/artifacts）
        assert not os.path.abspath(path).startswith(_SOURCE_ARTIFACTS)
        assert not os.path.abspath(path).startswith(_ROOT + os.sep)
        assert p._out_png_path("enh") != path, "不同 tag/时间必须可区分"


class TestSaveAs:
    def test_save_success_is_reported(self, panel, tmp_path, monkeypatch):
        from PIL import Image
        p, out_dir = panel
        src = out_dir / "weather_decode_1.png"
        Image.new("RGB", (6, 5), (10, 20, 30)).save(src)
        p._current_png = str(src)
        target = tmp_path / "saved.png"
        monkeypatch.setattr(wp.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(target), "")))
        p._save_current()
        assert target.is_file()
        assert "云图已另存" in p.status_label.text()
        assert target.name in p.status_label.text()

    def test_save_failure_is_reported_not_silent(self, panel, tmp_path,
                                                 monkeypatch):
        from PIL import Image
        p, out_dir = panel
        src = out_dir / "weather_decode_2.png"
        Image.new("RGB", (6, 5), (1, 2, 3)).save(src)
        p._current_png = str(src)
        bad = tmp_path / "no_such_dir" / "x.png"
        monkeypatch.setattr(wp.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(bad), "")))
        p._save_current()
        assert "保存 PNG 失败" in p.status_label.text()
        assert "x.png" in p.status_label.text()


class TestHistory:
    def test_history_lists_newest_first_and_previews(self, panel):
        from PIL import Image
        p, out_dir = panel
        for name in ("weather_a_1.png", "weather_b_2.png",
                     "unrelated.png"):
            Image.new("RGB", (4, 4)).save(out_dir / name)
        p._load_history()
        names = [p.history.item(i).text() for i in range(p.history.count())]
        assert names == ["weather_b_2.png", "weather_a_1.png"]
        assert p.history.isVisibleTo(p)
        p._on_history_clicked(p.history.item(0))
        assert os.path.basename(p._current_png) == "weather_b_2.png"
        assert p.save_btn.isEnabled()

    def test_empty_history_shows_hint_not_blank_box(self, panel):
        p, _out_dir = panel
        p._load_history()
        assert p.history.count() == 0
        assert not p.history.isVisibleTo(p)
        assert p.history_hint.isVisibleTo(p)
