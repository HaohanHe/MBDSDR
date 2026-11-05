#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""断言取证棘轮：docs 里的「已修复/已提交」只许少、不许新增无证据的。

口径（与 ``scripts/verify_claims.py`` 同源）：
  * 一条断言要算**可证实**，必须四元组齐全且 git 三步复核通过：
    ``<结论> — <hash> — <path>:<line> — <改动后字面量>``;
  * 存量（``docs/claims-baseline.json`` 里的计数）允许留着，但**按文件只许降**
    —— 新写的断言没 hash 会直接把计数顶上去，这里就红；
  * 存量怎么销：给该条补 hash 与字面量，或删掉这条断言，然后把
    ``docs/claims-baseline.json`` 的计数改小（只能改小）。

为什么不一次性把 100 多条都补 hash：那是一次性劳动，补完还会再漂；
这里只锁增量，存量由各文档 owner 逐篇销。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_claims as vc  # noqa: E402

BASELINE = ROOT / "docs" / "claims-baseline.json"


def _load_baseline() -> dict:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(data.get("docs"), dict), "基线格式错：缺 docs 映射"
    return data


def _measured() -> Counter:
    claims = vc.verify_docs(ROOT)
    return Counter(c.doc for c in claims if c.status != vc.OK)


def test_claims_baseline_exists_and_has_policy():
    data = _load_baseline()
    assert data.get("policy"), "基线必须写明口径"
    assert int(data.get("total_unverified", 0)) > 0


def test_unverified_claims_do_not_increase():
    """按文件棘轮：任何文件的未证实断言不得超过基线；新文件一条都不许有。"""
    base = _load_baseline()
    allowed = base["docs"]
    measured = _measured()
    violations = []
    for doc, count in sorted(measured.items()):
        limit = int(allowed.get(doc, 0))
        if count > limit:
            violations.append(f"{doc}: {count} > 基线 {limit}（新增 {count - limit}）")
    total = sum(measured.values())
    if total > int(base["total_unverified"]):
        violations.append(
            f"总量 {total} > 基线 {base['total_unverified']}"
            f"（新增 {total - int(base['total_unverified'])}）")
    assert not violations, (
        "新增断言必须带四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>`，"
        "或删掉无法取证的断言：\n" + "\n".join(violations))


def test_baseline_is_not_raised():
    """基线文件只能往下改：本次实测总量必须 ≤ 基线，留松弛由 owner 收紧。"""
    data = _load_baseline()
    total = sum(_measured().values())
    assert total <= int(data["total_unverified"])
