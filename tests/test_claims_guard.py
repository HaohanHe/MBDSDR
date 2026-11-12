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
KNOWN_RED = ROOT / "docs" / "known-red-register.md"
CAP_RE = re.compile(r"<!--\s*waived-total:\s*(\d+)\s*-->")
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")
#: 「已登记红」出口：白名单只减不增，确实不可复核的结论改走 K 编号登记。
#: 标记格式固定为 `<!-- claims-gate-exempt: K20 docs/x.md#<12位指纹> -->`。
EXEMPT_RE = re.compile(
    r"<!--\s*claims-gate-exempt:\s*(K\d+)\s+([\w./-]+)#([0-9a-f]{12})\s*-->")
#: 建表时（2026-11-06）HEAD 的未证实断言数；只许往下减。
BUILT_AT = 142


def _exempt_fps(text: str) -> set[str]:
    """解析「已登记红」出口。K 编号必须非空且在同一文件里有对应小节，否则 fail closed。

    这条出口只负责「已登记」的那几条，不放行「还没登记」的新断言——否则它就是
    白名单 2.0，会把闸门重新变回摆设。
    """
    headings = set(re.findall(r"^#+\s+(K\d+)\b", text, re.M))
    out: set[str] = set()
    for m in EXEMPT_RE.finditer(text):
        k, _doc, fp = m.group(1), m.group(2), m.group(3)
        if k not in headings:
            raise AssertionError(
                f"claims-gate-exempt 指向的 {k} 在 known-red-register 里没有小节标题；"
                "出口必须绑一条正式登记的已知红（K 编号不可为空/不可悬空）")
        out.add(fp)
    return out


def _registered_red_fps() -> set[str]:
    if not KNOWN_RED.exists():
        return set()
    return _exempt_fps(KNOWN_RED.read_text(encoding="utf-8"))


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
    """新增/改写的断言没有四元组 → 红（已登记到 K 编号的除外）。"""
    measured, claims = _measured()
    waived, _ = _waived()
    added = set(measured) - set(waived) - _registered_red_fps()
    if added:
        detail = [f"{c.doc}:{c.line} [{c.status}] {c.reason}\n    {c.text[:120]}"
                  for c in claims if _fp(c.doc, c.text) in added]
        raise AssertionError(
            "docs/ 有新增或改写的断言未带完整四元组（也不在白名单里）：\n  "
            + "\n  ".join(detail)
            + "\n修复：补 <hash> — <path>:<line> — <改动后字面量>，或删除断言；"
              "确实不可复核的，登记到 docs/known-red-register.md 的 K 编号并加"
              " claims-gate-exempt 标记。")


def test_registered_red_exit_requires_k_id():
    """出口必须带非空且真实存在的 K 编号：悬空的 K 直接红，不许当白名单用。"""
    ok = "## K20 — x\n<!-- claims-gate-exempt: K20 docs/a.md#0123456789ab -->\n"
    assert _exempt_fps(ok) == {"0123456789ab"}
    for bad in (
        "## K20 — x\n<!-- claims-gate-exempt: K99 docs/a.md#0123456789ab -->\n",
        "<!-- claims-gate-exempt: K20 docs/a.md#0123456789ab -->\n",
    ):
        try:
            _exempt_fps(bad)
        except AssertionError:
            continue
        raise AssertionError("悬空/无小节的 claims-gate-exempt 没有被拒绝")


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


def test_new_claim_without_hash_turns_gate_red(tmp_path):
    """变异反证：新写一条无四元组断言 → 闸门必红。

    真实仓库里的白名单只减不增，靠的就是这条判据：
    新断言过不了 verify_claims（NO_HASH），指纹又不在白名单，
    于是 `measured - waived` 非空 → `test_no_new_or_modified_unverified_claims` 红。
    同一行补成完整四元组后必须转为「可证实」并放行（证明红来自缺证据，不是措辞）。
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    sample = docs / "mutation-sample.md"
    sample.write_text("# 变异样本\n\n登录超时处理已修复，未附提交与路径。\n", encoding="utf-8")

    claims = vc.verify_docs(ROOT, docs_root=docs)
    bad = [c for c in claims if c.status != vc.OK]
    assert bad, "注入的无 hash 断言没有被 verify_docs 判为未证实"
    assert bad[0].status == vc.NO_HASH, f"期望 {vc.NO_HASH}，实得 {bad[0].status}"

    measured = Counter(_fp(c.doc, c.text) for c in bad)
    waived, _ = _waived()
    assert measured - waived, "新断言未被闸门识别为增量：白名单会漏放"

    sample.write_text(
        "# 变异样本\n\n登录超时处理已修复 — `451073e` — `desktop/module_panel.py:323` — "
        "`round(v * 1e6)`\n",
        encoding="utf-8",
    )
    claims = vc.verify_docs(ROOT, docs_root=docs)
    assert [c.status for c in claims] == [vc.OK], "补全四元组后仍未转为可证实"
