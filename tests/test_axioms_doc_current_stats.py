# SPDX-License-Identifier: MIT
"""`docs/axioms.md` 只许有一个「当前计数」，且必须钉在某一棵基准树上。

为什么不再跟 HEAD 比：工具扫的是工作树，别人每提交一笔改变扫描面的代码，
「文档 == 当次输出」就变红一次；长期红等于没有红。

判据（本文件）：

1. ``test_corrupted_number_fails``：基准未变、块内数字被改错 → 必须失败；
2. ``test_head_advanced_only_hints``：基准未变、HEAD 前进若干笔 → 必须通过，
   并打印「请重跑 python -m tools.axioms.current_stats」的提示；
3. ``test_stale_beyond_age_cap_fails`` / ``test_stale_beyond_commit_cap_fails``：
   落后超过 24 小时或 200 笔提交（``MAX_STALE_HOURS`` / ``MAX_STALE_COMMITS``，
   先到者）→ 必须红，并报出落后的时间/提交数；
4. ``test_stale_within_cap_only_hints``：落后但未超上限 → 只提示不红；
5. ``test_same_baseline_wrong_tool_output_fails``：基准未变、工具当次输出
   与文档不符（真错）→ 必须失败；
6. ``test_doc_block_is_verifiable``：真实文档块必须能通过三种检查。

反证 1：把 ``verify_doc`` 的「基准 != HEAD 就只提示」分支改回「一律与传入的
工具输出比对」，``test_head_advanced_only_hints`` 必红。
反证 2：把 ``MAX_STALE_HOURS`` / ``MAX_STALE_COMMITS`` 改成极大值，
``test_stale_beyond_age_cap_fails`` 与 ``test_stale_beyond_commit_cap_fails``
必转绿（即测试变红），说明新判据确实靠上限起作用。

运行：python -m pytest tests/test_axioms_doc_current_stats.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.current_stats import (  # noqa: E402
    BEGIN_MARK,
    END_MARK,
    FINGERPRINT_KEY,
    MAX_STALE_COMMITS,
    MAX_STALE_HOURS,
    fingerprint,
    parse_block,
    verify_doc,
    verify_repo,
)

DOC_PATH = os.path.join(ROOT, "docs", "axioms.md")
BASELINE = "a" * 40
HEAD_AHEAD = "b" * 40
#: 固定「现在」，让过期判据可复现（本文件不读墙钟）。
FIXED_NOW = datetime(2026, 11, 14, 13, 0, tzinfo=timezone(timedelta(hours=8)))
FRESH_STAMP = "2026-11-14T12:00:00+08:00"
BODY = ("违规 641 条：应修 67，复核 415，豁免 159。\n"
        "D1 5 / D2 168 / D3 247 / D4 28 / D5 134 / D6 59 / D7 0 / D8 0。")
OTHER_BODY = ("违规 646 条：应修 70，复核 417，豁免 159。\n"
              "D1 7 / D2 171 / D3 247 / D4 28 / D5 134 / D6 59 / D7 0 / D8 0。")


def _doc() -> str:
    with open(DOC_PATH, encoding="utf-8") as fh:
        return fh.read()


def _synthetic_doc(body: str = BODY, baseline: str = BASELINE,
                   generated_at: str = FRESH_STAMP) -> str:
    return (
        f"{BEGIN_MARK}（由 python -m tools.axioms.current_stats 生成；"
        f"基准 {baseline} 生成于 {generated_at}） -->\n"
        f"{body}\n{FINGERPRINT_KEY}{fingerprint(body)}\n{END_MARK}"
    )


def test_only_one_current_block():
    text = _doc()
    assert text.count(BEGIN_MARK) == 1
    assert text.count(END_MARK) == 1


def test_corrupted_number_fails():
    doc = _synthetic_doc()
    # 未改：必须通过
    assert verify_doc(doc, head_sha=BASELINE).ok
    # 基准不变、只把一个数字改错：指纹对不上 → 红
    tampered = doc.replace("违规 641 条", "违规 642 条")
    res = verify_doc(tampered, head_sha=BASELINE)
    assert not res.ok
    assert "不一致" in res.error


def test_head_advanced_only_hints():
    doc = _synthetic_doc()
    # 基准未更新、HEAD 前进，且传入的「工具当次输出」已经变了：
    # 这正是旧口径会误红的情形，现在必须通过并给提示。
    res = verify_doc(doc, head_sha=HEAD_AHEAD, tool_body=OTHER_BODY,
                     stale_commits=5, now=FIXED_NOW)
    assert res.ok, res.error
    assert "重跑" in res.hint
    assert "python -m tools.axioms.current_stats" in res.hint
    assert "不判红" in res.hint


def test_stale_beyond_age_cap_fails():
    """落后超过 24 小时：从提示升级为红，并报出落后时长。"""
    doc = _synthetic_doc(generated_at="2026-11-13T00:00:00+08:00")
    res = verify_doc(doc, head_sha=HEAD_AHEAD, stale_commits=3, now=FIXED_NOW)
    assert not res.ok
    assert "已过期" in res.error
    assert "小时" in res.error  # 报出落后的时间
    assert "python -m tools.axioms.current_stats" in res.error


def test_stale_beyond_commit_cap_fails():
    """落后超过 200 笔提交：从提示升级为红，并报出提交数。"""
    doc = _synthetic_doc()
    res = verify_doc(doc, head_sha=HEAD_AHEAD,
                     stale_commits=MAX_STALE_COMMITS + 50, now=FIXED_NOW)
    assert not res.ok
    assert "已过期" in res.error
    assert str(MAX_STALE_COMMITS + 50) in res.error  # 报出落后的提交数


def test_stale_within_cap_only_hints():
    """落后但两项都未超上限：只提示，不红。"""
    doc = _synthetic_doc(generated_at=(
        FIXED_NOW - timedelta(hours=MAX_STALE_HOURS / 2)).isoformat())
    res = verify_doc(doc, head_sha=HEAD_AHEAD,
                     stale_commits=MAX_STALE_COMMITS // 2, now=FIXED_NOW)
    assert res.ok, res.error
    assert res.hint and "不判红" in res.hint


def test_same_baseline_wrong_tool_output_fails():
    doc = _synthetic_doc()
    res = verify_doc(doc, head_sha=BASELINE, tool_body=OTHER_BODY)
    assert not res.ok
    assert "同一基准" in res.error


def test_doc_block_is_verifiable():
    """真实文档：格式、指纹、基准关系都必须自洽。"""
    text = _doc()
    blk = parse_block(text)
    assert blk is not None, "计数块不是带基准的新格式"
    assert blk.fingerprint == fingerprint(blk.body)
    res = verify_repo(ROOT)
    assert res.ok, res.error
    if res.hint:
        print("\n[提示] " + res.hint)


def test_historical_counts_are_labeled():
    """558/538/272 三组旧计数必须落在「历史快照（非当前）」的上下文里。"""
    lines = _doc().splitlines()
    for idx, line in enumerate(lines):
        if not any(t in line for t in ("违规 558", "538 条", "违规 272")):
            continue
        context = "\n".join(lines[max(0, idx - 2):idx + 1])
        assert "历史快照" in context or "非当前" in context, \
            f"第 {idx + 1} 行的旧计数没有标注历史快照：{line}"
