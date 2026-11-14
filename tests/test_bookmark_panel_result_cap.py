# SPDX-License-Identifier: MIT
"""书签列表结果行数上限：行数判据 + 性能反证
=============================================

书签面板的搜索框每个按键都会触发 ``refresh_list``，渲染成本跟随命中行数
（实测 5000 行 194 ms、20000 行 2608 ms）。钉住的契约：

  * 命中超过 ``bookmark_panel.MAX_RENDERED_BOOKMARKS``(500) 时只建前 500 行；
  * 截断可见：状态栏写「显示前 500 条，另有 N 条未显示」；
  * 分组过滤先于截断（选中「业余」时不该被别的组挤掉）；
  * 搜索命中不足上限时仍显示「N 条书签」，不制造假截断。

反证（``test_removing_cap_restores_full_render_and_slowdown``）：把上限放大到
无穷后，行数必须回到全部命中、同进程耗时必须显著变长。删掉 ``refresh_list``
里的切片，本文件立刻红。

样本：5000 条合成书签（假 manager 只提供 all()/search()），每档预热 1 +
采样 3 取中位；复跑入口 ``scripts/repro/2026-11-14-list-render-baseline.py
--only bookmark``。
"""
import os
import statistics
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

BOOKMARKS = 5000


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _bm(i, group=""):
    return SimpleNamespace(name=f"频道 {i}", frequency_hz=145_000_000 + i * 1000,
                           modulation="FM", bandwidth_hz=12_500, group=group)


@pytest.fixture()
def panel(qapp, tmp_path):
    from bookmark_panel import BookmarkPanel
    dlg = BookmarkPanel(config_dir=str(tmp_path))
    yield dlg
    dlg.deleteLater()
    qapp.processEvents()


def _set_items(panel, items, search=None):
    panel._mgr = SimpleNamespace(
        all=lambda: items,
        search=lambda q: (search(q) if callable(search) else items))
    panel.refresh_list()


def test_cap_bounds_rows_and_reports_remainder(panel):
    import bookmark_panel as bp
    items = [_bm(i) for i in range(BOOKMARKS)]
    _set_items(panel, items)

    assert panel.tree.topLevelItemCount() == bp.MAX_RENDERED_BOOKMARKS
    assert bp.MAX_RENDERED_BOOKMARKS == 500
    text = panel.status_label.text()
    hidden = BOOKMARKS - bp.MAX_RENDERED_BOOKMARKS
    assert f"另有 {hidden} 条未显示" in text, text


def test_group_filter_applies_before_cap(panel):
    import bookmark_panel as bp
    items = ([_bm(i, "ham") for i in range(400)]
             + [_bm(10000 + i, "marine") for i in range(3000)])
    _set_items(panel, items)

    panel._current_group = "ham"
    panel.refresh_list()
    assert panel.tree.topLevelItemCount() == 400
    assert "未显示" not in panel.status_label.text()

    panel._current_group = "marine"
    panel.refresh_list()
    assert panel.tree.topLevelItemCount() == bp.MAX_RENDERED_BOOKMARKS
    assert "另有 2500 条未显示" in panel.status_label.text()


def test_no_hidden_note_when_hits_fit(panel):
    items = [_bm(i) for i in range(40)]
    _set_items(panel, items)
    assert panel.tree.topLevelItemCount() == 40
    assert panel.status_label.text() == "40 条书签"


def _refresh_median_ms(panel, rounds=3):
    samples = []
    for n in range(1 + rounds):
        t0 = time.perf_counter()
        panel.refresh_list()
        if n >= 1:
            samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def test_removing_cap_restores_full_render_and_slowdown(panel, monkeypatch):
    import bookmark_panel as bp
    items = [_bm(i) for i in range(BOOKMARKS)]
    _set_items(panel, items)
    capped_rows = panel.tree.topLevelItemCount()
    capped_ms = _refresh_median_ms(panel)
    assert capped_rows == bp.MAX_RENDERED_BOOKMARKS

    monkeypatch.setattr(bp, "MAX_RENDERED_BOOKMARKS", 10 ** 9)
    panel.refresh_list()
    uncapped_rows = panel.tree.topLevelItemCount()
    uncapped_ms = _refresh_median_ms(panel)

    assert uncapped_rows == BOOKMARKS, (
        f"去掉上限后仍只渲染 {uncapped_rows} 行——上限没有真正生效")
    assert uncapped_ms > capped_ms * 3, (
        f"去掉上限没有明显变慢：截断 {capped_ms:.1f} ms vs "
        f"全量 {uncapped_ms:.1f} ms（{uncapped_rows} 行）")
