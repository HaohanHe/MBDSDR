#!/usr/bin/env python3
"""速查卡与长文防漂移：卡片里每个章节直链必须按**章节号**落到真实存在的标题。

卡片写法约定：`[§6.9.6](./COORDINATION.md#6.9.6)`。
锚点里只放章节号，**不放行号**：行号由本脚本解析长文标题现算，长文增删段落
不再需要重钉。2026-11-13 之前锚点钉死 `#L行号`，长文每编辑一次就整批指错，
重数一遍只能撑到下一次编辑——所以改成按章节号解析。

判据：
  ① 锚点必须是章节号；`#L123` 这类写死行号的形态直接判失败（不让旧机制回流）；
  ② 锚点编号与链接文字里的 §编号一致；
  ③ 长文里确实有该编号的标题（`## 6.9` / `#### 6.10.2` …），行号由解析得出；
  ③b 该编号在长文里**只能有一处标题**——同一编号出现两次时无法断定指哪一节，
     按失败处理；
  ④ 卡片覆盖 §6.9.5–§6.9.12 与 §6.9.14–§6.9.16 十一节（形态卡不允许漏节）。

用法：python scripts/check_card_links.py [--card <卡片>] [--doc <长文>] [--fix-anchors]
      --fix-anchors 把卡片里遗留的 `#L行号` 锚点改写成 `#章节号`（章节号取链接文字
      里的 §编号），不猜不存在的章号，改完仍走同一套校验。
退出码：0 全过；1 有漂移；2 解析失败。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CARD = ROOT / "docs" / "COMMIT-INCIDENT-CARD.md"
DEFAULT_DOC = ROOT / "docs" / "COORDINATION.md"
LINK_RE = re.compile(r"\[§([0-9]+(?:\.[0-9]+)*)\]\(\./COORDINATION\.md#([^)\s]*)\)")
SECTION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
LINE_ANCHOR_RE = re.compile(r"^L[0-9]+$")
HEADING_RE = re.compile(r"^#{2,4}\s+([0-9]+(?:\.[0-9]+)*)\s")
REQUIRED = {"6.9.5", "6.9.6", "6.9.7", "6.9.8", "6.9.9", "6.9.10", "6.9.11", "6.9.12",
            "6.9.14", "6.9.15", "6.9.16"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="速查卡锚点防漂移（按章节号解析）")
    parser.add_argument("--card", default=str(DEFAULT_CARD))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    parser.add_argument("--fix-anchors", action="store_true",
                        help="把卡片里遗留的 #L 行号锚点改写成 #章节号后再检查")
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
        print("PARSE FAIL：卡片里没有找到 `[§x](./COORDINATION.md#章节号)` 形式的直链")
        return 2

    # 编号 -> 全部标题行号。正常只有一处；出现两处时锚点就是歧义的
    # （指向哪一处都「编号对得上」），交给下面的 ③b 报出来，不在这里猜。
    heading_at: dict[str, List[int]] = {}
    for i, line in enumerate(doc_lines, 1):
        m = HEADING_RE.match(line.strip())
        if m:
            heading_at.setdefault(m.group(1), []).append(i)

    if args.fix_anchors:
        def _rewrite(m: re.Match) -> str:
            section, anchor = m.group(1), m.group(2)
            if anchor == section:
                return m.group(0)
            if LINE_ANCHOR_RE.match(anchor):
                # 旧机制遗留的写死行号：章节号以链接文字为准，行号不再保留
                return f"[§{section}](./COORDINATION.md#{section})"
            # 其余不符形态（章节号写错等）不做猜测，留给校验报出来
            return m.group(0)

        fixed = LINK_RE.sub(_rewrite, card)
        if fixed != card:
            Path(args.card).write_bytes(
                fixed.replace("\n", card_nl).encode("utf-8"))
            card = fixed
            links = LINK_RE.findall(card)
            print(f"FIX 已把 {len(links)} 条锚点改为按章节号定位")

    errors: List[str] = []
    resolved: List[Tuple[str, int]] = []
    seen = set()
    for section, anchor in links:
        seen.add(section)
        if LINE_ANCHOR_RE.match(anchor):
            errors.append(
                f"§{section} -> #{anchor}：锚点写死了行号；必须按章节号写成 #{section}，"
                f"行号由检查器现算")
            continue
        if not SECTION_RE.match(anchor):
            errors.append(f"§{section} -> #{anchor}：锚点不是章节号")
            continue
        if anchor != section:
            errors.append(
                f"§{section} -> #{anchor}：链接文字与锚点的章节号不一致")
            continue
        hits = heading_at.get(section, [])
        if not hits:
            errors.append(f"§{section} -> #{anchor}：长文里没有这个编号的标题")
            continue
        if len(hits) > 1:
            # 「编号对得上」但存在同编号的另一处标题：这条链接证明不了
            # 自己指的是哪一节，按失败处理，而不是静默采信。
            errors.append(
                f"§{section} -> #{anchor}：该编号在长文里有 {len(hits)} 处标题"
                f"（L{', L'.join(str(n) for n in hits)}），锚点无法唯一确定指哪一处")
            continue
        resolved.append((section, hits[0]))

    missing = REQUIRED - seen
    if missing:
        errors.append(f"卡片缺少必需章节直链：{', '.join(sorted(missing))}")

    if errors:
        for item in errors:
            print(f"FAIL {item}")
        print(f"FAIL links={len(links)} errors={len(errors)}")
        return 1

    # 行号是解析出来的，不是卡片里维护的常量：打印出来便于人工核对
    for section, line_no in resolved:
        print(f"  §{section} -> L{line_no}")
    print(f"PASS links={len(links)} errors=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
