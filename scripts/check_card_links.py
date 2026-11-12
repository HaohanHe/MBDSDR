#!/usr/bin/env python3
"""速查卡与长文防漂移：卡片里每个章节直链必须指到真实存在的章节标题。

卡片写法约定：`[§6.9.6](./COORDINATION.md#L1125)`。
本脚本从卡片里抽取 §编号 与 #L行号，断言：
  ① 行号在文档范围内；
  ② 该行确实是 markdown 标题；
  ③ 标题的编号与卡片引用的 §编号一致（如 §6.9.6 ↔ `#### 6.9.6 ...`）；
  ③b 该编号在长文里**只能有一处标题**——同一编号出现两次时，`#L` 指向其中任一处
     都「行号对、编号也对」，但没人能断定它指的是哪一节；这种歧义按失败处理；
  ④ 卡片覆盖 §6.9.5–§6.9.12 与 §6.9.14–§6.9.16 十一节（形态卡不允许漏节）。

用法：python scripts/check_card_links.py [--card <卡片>] [--doc <长文>] [--fix-anchors]
      --fix-anchors 会按长文里标题的**实际行号**重写卡片里的 #L 锚点再检查；
      长文增删段落导致行号整体漂移时用它一键对齐，不需要手抄 13 个数字。
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
REQUIRED = {"6.9.5", "6.9.6", "6.9.7", "6.9.8", "6.9.9", "6.9.10", "6.9.11", "6.9.12",
            "6.9.14", "6.9.15", "6.9.16"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="速查卡锚点防漂移")
    parser.add_argument("--card", default=str(DEFAULT_CARD))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    parser.add_argument("--fix-anchors", action="store_true",
                        help="按长文标题实际行号重写卡片的 #L 锚点后再检查")
    args = parser.parse_args(argv)

    try:
        card_raw = Path(args.card).read_bytes().decode("utf-8")
        doc_raw = Path(args.doc).read_bytes().decode("utf-8")
    except OSError as exc:
        print(f"PARSE FAIL：{exc}")
        return 2

    # 按字节读、按原换行写回：避免修锚点时把整个文件从 LF 改成 CRLF
    card_nl = "\r\n" if "\r\n" in card_raw else "\n"
    card = card_raw.replace("\r\n", "\n")
    doc_lines = doc_raw.replace("\r\n", "\n").splitlines()

    links = LINK_RE.findall(card)
    if not links:
        print("PARSE FAIL：卡片里没有找到 `[§x](./COORDINATION.md#Ln)` 形式的直链")
        return 2

    # 编号 -> 全部标题行号。正常只有一处；出现两处时 #L 锚点就是歧义的
    # （指向哪一处都「编号对得上」），交给下面的 ③b 报出来，不在这里猜。
    heading_at: dict[str, List[int]] = {}
    for i, line in enumerate(doc_lines, 1):
        m = HEADING_RE.match(line.strip())
        if m:
            heading_at.setdefault(m.group(1), []).append(i)

    if args.fix_anchors:
        anchors: dict[str, int] = {
            section: lines[0] for section, lines in heading_at.items() if len(lines) == 1}

        def _rewrite(m: re.Match) -> str:
            section = m.group(1)
            actual = anchors.get(section)
            if actual is None:
                return m.group(0)      # 长文里没这节或编号重复：保留原样，让下面的校验报出来
            return f"[§{section}](./COORDINATION.md#L{actual})"

        fixed = LINK_RE.sub(_rewrite, card)
        if fixed != card:
            Path(args.card).write_bytes(
                fixed.replace("\n", card_nl).encode("utf-8"))
            card = fixed
            links = LINK_RE.findall(card)
            print(f"FIX 已按长文标题实际行号重写 {len(links)} 条锚点")

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
            continue
        dup = heading_at.get(section, [])
        if len(dup) > 1:
            # 「行号对、编号也对」但存在同编号的另一处标题：这条链接证明不了
            # 自己指的是哪一节，按失败处理，而不是静默采信。
            errors.append(
                f"§{section} -> L{line_no}：该编号在长文里有 {len(dup)} 处标题"
                f"（L{', L'.join(str(n) for n in dup)}），锚点无法唯一确定指哪一处")

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
