#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""源码文本 pin 棘轮闸门：基线只许减，新增即红。

口径与 `scripts/audit_source_text_pins.py` 一致（作用域感知，不做跨函数
变量名传染）。基线 `docs/source-pin-ratchet-baseline.json` 建表于
2026-11-06，已按批次收缩到 56 条（原 174：选中卡 10、u02/u03/u07 27、
C 候选+天空读数+余量 38、B 类两簇 47、他线 967ac726 新增 4）：

* 每条必须带 `owner` + `reason`（新增/保留条目必须写明归属与理由）；
* 当前扫描命中集 `-` 基线非空 → 红（新增源码 pin 必须先改成行为断言）；
* 基线条目数必须与 `BUILT_AT` 相等：删条目后不同步下调上限 → 红；
* 删除基线条目（pin 已被改成行为断言）是允许的，删后仍绿。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_source_text_pins as scanner  # noqa: E402

BASELINE = ROOT / "docs" / "source-pin-ratchet-baseline.json"
BUILT_AT = 56
KEY_FIELDS = ("path", "func", "pinned")


def _entries() -> list[dict]:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert data.get("scanner") == "scripts/audit_source_text_pins.py", \
        "基线不是由当前扫描器生成的"
    assert data.get("built_count") == len(data.get("entries") or []), \
        "baseline built_count 与条目数不一致"
    return data["entries"]


def _key(item: dict) -> tuple:
    return tuple(item[field] for field in KEY_FIELDS)


def _current_hits() -> list[dict]:
    hits: list[dict] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        for hit in scanner.scan_file(path):
            if "error" in hit:
                raise AssertionError(f"扫描器解析失败，闸门不能带病放行：{hit}")
            hits.append(hit)
    return hits


def test_baseline_is_well_formed():
    entries = _entries()
    assert entries, "基线为空：闸门没有可比的存量清单"
    for index, entry in enumerate(entries, 1):
        for field in ("path", "func", "pinned", "owner", "reason"):
            assert entry.get(field), (
                f"基线第 {index} 条缺 {field}：新增/保留条目必须带 owner + 理由")
        assert entry["path"].startswith("tests/"), entry["path"]


def test_baseline_count_matches_ceiling():
    entries = _entries()
    assert len(entries) == BUILT_AT, (
        f"基线条数与 BUILT_AT 不一致：entries={len(entries)}，BUILT_AT={BUILT_AT}。"
        " 删条目（pin 已改行为断言）后必须同步下调 BUILT_AT（只减不增）；"
        " 只删条目不改 BUILT_AT 会让闸门失去上限，必须红。")


def test_no_new_source_text_pins():
    baseline = Counter(_key(e) for e in _entries())
    current = Counter(_key(h) for h in _current_hits())
    new = current - baseline
    if new:
        detail = "\n".join(
            f"  {path}::{func}  钉 {pinned!r}  ×{count}"
            for (path, func, pinned), count in sorted(new.items())
        )
        raise AssertionError(
            "新增源码文本 pin（必须改成行为断言，或由 owner 在基线写明理由）：\n"
            + detail)
