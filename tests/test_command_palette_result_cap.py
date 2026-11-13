# SPDX-License-Identifier: MIT
"""命令面板结果行数上限：行数判据 + 性能反证
=============================================

钉住的契约：
  * 命中数超过 ``command_palette.MAX_RENDERED_RESULTS`` 时，列表只渲染前
    N 行，其余不构造 QListWidgetItem；
  * 截断必须可见：说明区明确写「另有 N 条未显示」，N = 命中数 − 上限；
  * 渲染保留相关度排序，首行仍是 ``_results[0]``（回车路径不变）；
  * ``visible_count()`` 仍报「命中条数」，不因渲染截断而少报。

反证（``test_removing_cap_restores_full_render_and_slowdown``）：把上限
放大到无穷后，列表行数必须回到全部命中、整键耗时必须显著变长。删掉
``_render_results`` 里的切片或让上限失效，本文件立刻红。

样本口径与 ``scripts/repro/2026-11-13-palette-result-cap.py`` 一致：
500 条书签形 + 10000 条卫星形命令（10500 条），同输入空查询。
"""
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

#: 与复跑脚本相同的合成规模：500 书签 + 10000 卫星。
BOOKMARKS = 500
SATELLITES = 10000


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def _fresh_recent(tmp_path, monkeypatch):
    import command_palette as cp
    monkeypatch.setattr(cp, "_recent_path", lambda: tmp_path / "recent.json")
    cp.reset_recent()
    yield
    cp.reset_recent()


def _commands(sat_count=SATELLITES):
    import command_palette as cp
    out = []
    for i in range(BOOKMARKS):
        freq = 145_000_000 + i * 12_500
        out.append(cp.Command(
            title=f"书签 {i:03d}", group="书签",
            description=f"书签：本地中继 {i}（FM，分组 测试）",
            trigger=f"{freq / 1e6:g} MHz · {freq} Hz · FM",
            source=f"bookmark:{freq}:书签 {i:03d}",
            run=lambda: {"ok": True}))
    for i in range(sat_count):
        out.append(cp.Command(
            title=f"SAT-{i:05d}", group="卫星",
            description="卫星跟踪：跳到该卫星并让「卫星」下拉选中它（星历：预推）",
            trigger=f"NORAD {i + 10000} · 预推",
            source=f"sat:{i + 10000}",
            run=lambda: {"ok": True}))
    return out


@pytest.fixture(scope="module")
def big_palette(qapp):
    import command_palette as cp
    dlg = cp.CommandPalette(window=None)
    dlg._commands = _commands()
    dlg._refresh("")
    yield dlg
    dlg.deleteLater()
    qapp.processEvents()


def _command_rows(dlg):
    """列表里的命令行数（组标题的 UserRole 为空）。"""
    from PySide6.QtCore import Qt
    return sum(1 for i in range(dlg.list_widget.count())
               if dlg.list_widget.item(i).data(Qt.UserRole) is not None)


def test_cap_bounds_rendered_rows_and_keeps_hit_count(big_palette):
    import command_palette as cp
    dlg = big_palette
    hits = BOOKMARKS + SATELLITES

    assert dlg.visible_count() == hits, (
        "visible_count 是命中数，不该被渲染上限改口径")
    assert _command_rows(dlg) == cp.MAX_RENDERED_RESULTS, (
        f"渲染行数 {_command_rows(dlg)} != 上限 {cp.MAX_RENDERED_RESULTS}")
    headers = dlg.list_widget.count() - _command_rows(dlg)
    assert 0 < headers <= len(cp.GROUPS), f"组标题数异常: {headers}"
    assert dlg.hidden_count() == hits - cp.MAX_RENDERED_RESULTS


def test_hidden_note_states_exact_remainder(big_palette):
    dlg = big_palette
    hidden = BOOKMARKS + SATELLITES - 200
    text = dlg.reason_label.text()
    assert "未显示" in text, f"截断没告诉用户还有多少条: {text!r}"
    assert f"另有 {hidden} 条未显示" in text.replace("  ", " "), (
        f"未显示条数不对（期望 {hidden}）: {text!r}")


def test_cap_preserves_relevance_order(big_palette):
    """截的是尾部：前 N 行必须仍按 ``_results`` 的顺序出现。"""
    from PySide6.QtCore import Qt
    dlg = big_palette
    rendered = [dlg.list_widget.item(i).data(Qt.UserRole)
                for i in range(dlg.list_widget.count())]
    rendered = [c for c in rendered if c is not None]
    assert [c.source for c in rendered] == \
        [c.source for c in dlg._results[:len(rendered)]]


def test_no_hidden_note_when_hits_fit(qapp):
    """命中数小于上限时不出现「未显示」提示（不制造假截断）。"""
    import command_palette as cp
    dlg = cp.CommandPalette(window=None)
    dlg._commands = _commands()[:50]
    dlg._refresh("")
    assert dlg.visible_count() == 50
    assert dlg.hidden_count() == 0
    assert "未显示" not in dlg.reason_label.text()
    dlg.deleteLater()
    qapp.processEvents()


def _refresh_median_ms(dlg, rounds=3):
    warmup = 1
    samples = []
    for n in range(warmup + rounds):
        t0 = time.perf_counter()
        dlg._refresh("")
        if n >= warmup:
            samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def test_removing_cap_restores_full_render_and_slowdown(big_palette, monkeypatch):
    """反证：上限去掉后必须重新渲染全部命中，且同进程内明显变慢。

    同一进程、同一输入、同一 QListWidget，先量截断态再量全量态，
    把机器负载差异压到最小；判据同时看行数（确定性）和耗时（数量级）。
    """
    import command_palette as cp
    dlg = big_palette

    capped_rows = _command_rows(dlg)
    capped_ms = _refresh_median_ms(dlg)
    assert capped_rows == cp.MAX_RENDERED_RESULTS

    monkeypatch.setattr(cp, "MAX_RENDERED_RESULTS", 10 ** 9)
    dlg._refresh("")
    uncapped_rows = _command_rows(dlg)
    uncapped_ms = _refresh_median_ms(dlg)

    assert uncapped_rows == BOOKMARKS + SATELLITES, (
        f"去掉上限后仍只渲染 {uncapped_rows} 行——上限没有真正生效")
    assert uncapped_ms > capped_ms * 3, (
        f"去掉上限没有明显变慢：截断 {capped_ms:.1f} ms vs "
        f"全量 {uncapped_ms:.1f} ms（{uncapped_rows} 行）")
    assert dlg.hidden_count() == 0
