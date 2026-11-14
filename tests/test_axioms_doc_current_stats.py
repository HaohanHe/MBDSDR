# SPDX-License-Identifier: MIT
"""`docs/axioms.md` 只许有一个「当前计数」，且必须等于工具当次输出。

反证：把文档块里的 ``643`` 改成 ``642``，``test_doc_stats_match_tool`` 必红；
把 538/272 两处历史快照的「历史快照（非当前）」标签删掉，
``test_historical_counts_are_labeled`` 必红。

运行：python -m pytest tests/test_axioms_doc_current_stats.py -v
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.current_stats import BEGIN_MARK, END_MARK, render_block  # noqa: E402

DOC_PATH = os.path.join(ROOT, "docs", "axioms.md")


def _doc() -> str:
    with open(DOC_PATH, encoding="utf-8") as fh:
        return fh.read()


def _current_block(text: str) -> str:
    pattern = re.escape(BEGIN_MARK) + r".*?-->\n(.*?)\n" + re.escape(END_MARK)
    match = re.search(pattern, text, re.S)
    assert match, "docs/axioms.md 缺少 axioms-current-stats 块"
    return match.group(1).strip()


def test_only_one_current_block():
    text = _doc()
    assert text.count(BEGIN_MARK) == 1
    assert text.count(END_MARK) == 1


def test_doc_stats_match_tool():
    assert _current_block(_doc()) == render_block().strip()


def test_historical_counts_are_labeled():
    """558/538/272 三组旧计数必须落在「历史快照（非当前）」的上下文里。"""
    lines = _doc().splitlines()
    for idx, line in enumerate(lines):
        if not any(t in line for t in ("违规 558", "538 条", "违规 272")):
            continue
        context = "\n".join(lines[max(0, idx - 2):idx + 1])
        assert "历史快照" in context or "非当前" in context, \
            f"第 {idx + 1} 行的旧计数没有标注历史快照：{line}"
