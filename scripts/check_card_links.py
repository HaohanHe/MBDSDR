#!/usr/bin/env python3
"""速查卡与长文防漂移：卡片里每个章节直链必须指到真实存在的章节标题。

卡片写法约定：`[§6.9.6](./COORDINATION.md#L1125)`。
本脚本从卡片里抽取 §编号 与 #L行号，断言：
  ① 行号在文档范围内；
  ② 该行确实是 markdown 标题；
  ③ 标题的编号与卡片引用的 §编号一致（如 §6.9.6 ↔ `#### 6.9.6 ...`）；
  ④ 卡片覆盖 §6.9.5–§6.9.9 五节（九形态卡不允许漏节）。

用法：python scripts/check_card_links.py [--card <卡片>] [--doc <长文>]
退出码：0 全过；1 有漂移；2 解析失败。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CARD = ROOT / "docs" / "COMMIT-INCIDENT-CARD.md"
DEFAULT_DOC = ROOT / "docs" / "COORDINATION.md"
LINK_RE = re.compile(r"\[§([0-9]+(?:\.[0-9]+)*)\]\(\./COORDINATION\.md#L([0-9]+)\)")
HEADING_RE = re.compile(r"^#{2,4}\s+([0-9]+(?:\.[0-9]+)*)\s")
REQUIRED = {"6.9.5", "6.9.6", "6.9.7", "6.9.8", "6.9.9"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="速查卡锚点防漂移")
    parser.add_argument("--card", default=str(DEFAULT_CARD))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    args = parser.parse_args(argv)

    try:
        card = Path(args.card).read_text(encoding="utf-8")
        doc_lines = Path(args.doc).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"PARSE FAIL：{exc}")
        return 2

    links = LINK_RE.findall(card)
    if not links:
        print("PARSE FAIL：卡片里没有找到 `[§x](./COORDINATION.md#Ln)` 形式的直链")
        return 2

    errors: List[str] = []
    seen = set()
    for section, line_s in links:
        seen.add(section)
        line_no = int(line_s)
        if line_no < 1 or line_no > len(doc_lines):
            errors.append(f"§{section} -> L{line_no}：超出文档范围（共 {len(doc_lines)} 行）")
            continue
        heading = doc_lines[line_no - 1].strip()
        match = HEADING_RE.match(heading)
        if not match:
            errors.append(f"§{section} -> L{line_no}：该行不是标题：{heading[:60]!r}")
            continue
        if match.group(1) != section:
            errors.append(f"§{section} -> L{line_no}：实际标题编号是 {match.group(1)}：{heading[:60]!r}")

    missing = REQUIRED - seen
    if missing:
        errors.append(f"卡片缺少必需章节直链：{', '.join(sorted(missing))}")

    if errors:
        for item in errors:
            print(f"FAIL {item}")
        print(f"FAIL links={len(links)} errors={len(errors)}")
        return 1

    print(f"PASS links={len(links)} sections={','.join(sorted(seen))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
