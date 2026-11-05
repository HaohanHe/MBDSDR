# SPDX-License-Identifier: MIT
"""文档断言闸门：存量白名单只减不增，增量必须带四元组。

口径（与 `scripts/verify_claims.py` 一致）：

    <结论> — <commit hash> — <path>:<line> — <改动后唯一字面量>

* 白名单 `docs/claim-waivers.md` 只登记**建表时 HEAD 已提交**的未证实断言；
  每条附指纹与「为什么暂不补证」。
* `docs/` 下新增/改写过的断言，指纹不在白名单 → 红（补齐四元组后才许提交）。
* 白名单行失效（断言已补证或已删）→ 红（必须销账，只减不增）。
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_claims as vc  # noqa: E402

WAIVERS = ROOT / "docs" / "claim-waivers.md"
CAP_RE = re.compile(r"<!--\s*waived-total:\s*(\d+)\s*-->")
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")
#: 建表时（2026-11-06）HEAD 的未证实断言数；只许往下减。
BUILT_AT = 142


def _fp(doc: str, text: str) -> str:
    return hashlib.sha256(f"{doc}\x00{text}".encode("utf-8")).hexdigest()[:12]


def _measured() -> tuple[Counter, list]:
    claims = vc.verify_docs(ROOT)
    bad = [c for c in claims if c.status != vc.OK]
    return Counter(_fp(c.doc, c.text) for c in bad), bad


def _waived() -> tuple[Counter, list]:
    rows = []
    for line in WAIVERS.read_text(encoding="utf-8").splitlines():
        if not ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append((cells[1], cells[2]))
    return Counter(f for _, f in rows), rows


def test_waiver_file_is_declared():
    assert WAIVERS.exists(), "docs/claim-waivers.md 缺失：闸门没有存量白名单"
    assert CAP_RE.search(WAIVERS.read_text(encoding="utf-8")), "缺 waived-total 声明"


def test_waiver_count_matches_cap_and_never_increases():
    cap = int(CAP_RE.search(WAIVERS.read_text(encoding="utf-8")).group(1))
    waived, _ = _waived()
    assert len(waived) == cap, (
        f"白名单行数 {len(waived)} != waived-total {cap}：删行必须同步调小计数")
    assert len(waived) <= BUILT_AT, (
        f"白名单只减不增：{len(waived)} 已超过建表时 {BUILT_AT} 条")


def test_no_new_or_modified_unverified_claims():
    """新增/改写的断言没有四元组 → 红。"""
    measured, claims = _measured()
    waived, _ = _waived()
    added = measured - waived
    if added:
        detail = [f"{c.doc}:{c.line} [{c.status}] {c.reason}\n    {c.text[:120]}"
                  for c in claims if _fp(c.doc, c.text) in added]
        raise AssertionError(
            "docs/ 有新增或改写的断言未带完整四元组（也不在白名单里）：\n  "
            + "\n  ".join(detail)
            + "\n修复：补 <hash> — <path>:<line> — <改动后字面量>，或删除断言。")


def test_waivers_are_not_stale():
    """白名单里的断言已补证/已删 → 红：必须销账，体现「只减」。"""
    measured, _ = _measured()
    waived, rows = _waived()
    gone = waived - measured
    if gone:
        detail = [f"{doc} 指纹 {f}" for doc, f in rows if f in gone]
        raise AssertionError(
            "白名单条目已失效（断言已补证或已删除），请删行并调小 waived-total：\n  "
            + "\n  ".join(detail))
