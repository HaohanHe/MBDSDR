# SPDX-License-Identifier: MIT
"""``--exclude-owner`` 按 §6.1 行里的线名排除，不再手算行号。

判据（用户指定）：构造一次「他线在我算完范围后又追加行」的场景——旧做法
（先算好行号范围）必须多带，新做法（提交时按线名现算）必须不多带。

反证：把 ``_classify_owner_rows`` 改回「按传入范围切片」而不是按行文本判线名，
``test_row_appended_after_old_range_is_still_excluded`` 会红（新做法只排到
第 11 行，第 12 行漏进本笔）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import safe_commit as sc  # noqa: E402

MY = "进程内工具守卫线/6ae85f56"
OTHER = "UI防呆线/6ae85f56"


def _row(path: str, owner: str) -> str:
    return f"| `{path}` | {owner}（说明） | 2026-11-14 11:00 | 本笔 |"


def _ranges(items) -> set[int]:
    return {item["start"] for item in items}


def test_rows_classified_by_label():
    rows = [(10, _row("a.py", MY)), (11, _row("b.py", OTHER))]
    excluded, unknown = sc._classify_owner_rows(rows, ["UI防呆线"], [MY])

    assert unknown == []
    assert _ranges(excluded) == {11}
    assert excluded[0]["owner"] == OTHER


def test_row_appended_after_old_range_is_still_excluded():
    """他线在我算完范围后又追加一行：旧做法多带，新做法不多带。"""
    before = [(10, _row("a.py", MY)), (11, _row("b.py", OTHER))]
    old_excluded, _ = sc._classify_owner_rows(before, ["UI防呆线"], [MY])
    old_ranges = _ranges(old_excluded)          # 旧做法：此刻算出的行号范围
    assert old_ranges == {11}

    after = before + [(12, _row("c.py", OTHER))]
    new_excluded, unknown = sc._classify_owner_rows(after, ["UI防呆线"], [MY])
    new_ranges = _ranges(new_excluded)

    other_lines = {11, 12}
    assert unknown == []
    assert other_lines - old_ranges == {12}, "构造失败：旧范围本应漏掉追加行"
    assert old_ranges != other_lines, "旧做法必须多带追加的那一行"
    assert new_ranges == other_lines, "新做法必须把追加行一起排掉"


def test_my_own_rows_are_kept():
    rows = [(10, _row("a.py", MY)), (11, _row("b.py", OTHER))]
    excluded, unknown = sc._classify_owner_rows(rows, ["UI防呆线"], [MY])

    assert unknown == []
    assert 10 not in _ranges(excluded)


def test_unknown_owner_is_refused_not_guessed():
    rows = [(10, _row("a.py", MY)), (11, _row("b.py", "不认识的线/abc"))]
    excluded, unknown = sc._classify_owner_rows(rows, ["UI防呆线"], [MY])

    assert excluded == []
    assert len(unknown) == 1 and "不认识的线/abc" in unknown[0]


def test_non_table_added_line_is_refused():
    rows = [(10, "普通说明文字，不是表格行")]
    excluded, unknown = sc._classify_owner_rows(rows, ["UI防呆线"], [MY])

    assert excluded == []
    assert unknown and "不是 §6.1 表格行" in unknown[0]
