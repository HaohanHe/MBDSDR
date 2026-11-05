#!/usr/bin/env python3
"""文档编号闸：同一份文档里，两套套件的环编号不许同号不同义。

两套套件各自有 R1–R7，但含义不同::

    R<n> = run_all.py 七环（主口径；summary.json 的 ring 字段原样就是 R<n>）
    A<n> = acceptance_suite.py 设备链视角（R5 是云台、R6 是串口 GNSS，
           run_all 里 R5 是卫星过境、R6 是星象仪）

事故形态：文档写「R5 云台回读」「R6 串口 GNSS」，读者会以为是七环里的 R5/R6。

本闸只做机器能判的部分：
  1. 文档必须有一张 14 行的编号对照表（R1–R7 + A1–A7），名字对得上；
  2. 行内出现 R<n> 却又带 acceptance_suite 专属词（云台/GNSS/串口/…）→ 判红；
     行内出现 A<n> 却又带 run_all 专属词（卫星追踪/过境/发射/…）→ 判红；
     除非同一行已经把该编号的正确名字写出来（显式对照不算歧义）。

用法::

    python scripts/hw_acceptance/check_ring_labels.py
    python scripts/hw_acceptance/check_ring_labels.py --doc docs/hw-acceptance.md
    python scripts/hw_acceptance/check_ring_labels.py --selftest   # 反证：改成一模一样必须红
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 关键词口径（不是完整标题）：文档里的写法允许带修饰语。
RUN_ALL_RINGS = {
    1: "连接与识别",
    2: "频谱与瀑布",
    3: "解调与音频",
    4: "录音与回放",
    5: "卫星",
    6: "星象仪",
    7: "发射",
}
ACCEPTANCE_RINGS = {
    1: "连接与识别",
    2: "频谱与首帧",
    3: "流稳定性",
    4: "解调与音频",
    5: "云台",
    6: "GNSS",
    7: "星象仪渲染",
}

ACCEPTANCE_ONLY = ("云台", "GNSS", "串口", "流稳定性", "星象仪渲染", "频谱与首帧")
RUN_ALL_ONLY = ("卫星追踪", "过境", "发射", "星象仪模式")

_ROW = re.compile(r"\|\s*([RA])([1-7])\s*\|([^|]*)\|")
_R_LABEL = re.compile(r"(?<![A-Za-z])R([1-7])\b")
_A_LABEL = re.compile(r"(?<![A-Za-z])A([1-7])\b")


def _mapping_table(text: str) -> tuple[list[tuple[str, str, str]], int, int]:
    """找「| 编号 | …」开头的对照表；返回 (rows, 起始行号, 结束行号)，行号 1-based。

    只认这一张表，避免把正文里别的以 R<n> 开头的表格（例如复跑对照表）
    误当成编号声明。
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("| 编号 |"):
            rows: list[tuple[str, str, str]] = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                m = _ROW.match(lines[j].strip())
                if m:
                    rows.append((m.group(1), m.group(2), m.group(3).strip()))
                j += 1
            return rows, i + 1, j
    return [], -1, -1


def check_text(text: str) -> list[str]:
    """返回问题列表；空列表 = 通过。"""
    problems: list[str] = []

    # ---- 1. 编号对照表：14 行，缺一行就判红（缺行往往正是「写成一模一样」）
    rows, table_start, table_end = _mapping_table(text)
    if not rows:
        problems.append("找不到「| 编号 |」开头的编号对照表（两套编号必须分开声明）")
    declared: dict[str, str] = {}
    for family, num, name in rows:
        full = f"{family}{num}"
        if full in declared:
            problems.append(f"编号 {full} 在对照表里出现两次（{declared[full]} / {name}）")
        declared[full] = name

    for n in range(1, 8):
        for family, canonical in (("R", RUN_ALL_RINGS), ("A", ACCEPTANCE_RINGS)):
            full = f"{family}{n}"
            if full not in declared:
                problems.append(f"对照表缺 {full}（两套编号必须分开列，不能合并成 R{n}）")
            elif canonical[n] not in declared[full]:
                problems.append(
                    f"{full} 的名字是「{declared[full]}」，与 {family} 的语义「{canonical[n]}」不符"
                    "（疑似同号不同义）")

    # ---- 2. 行内混用：R<n> 带 A 语义 / A<n> 带 R 语义
    for lineno, line in enumerate(text.splitlines(), 1):
        # 对照表自己的行是显式声明（第三列写的就是语义），不参与「行内混用」判定
        if table_start <= lineno <= table_end:
            continue
        for m in _R_LABEL.finditer(line):
            n = int(m.group(1))
            if any(k in line for k in ACCEPTANCE_ONLY) and RUN_ALL_RINGS[n] not in line:
                problems.append(
                    f"第 {lineno} 行：R{n} 与 acceptance_suite 语义同现"
                    f"（应为 A{n}），原文：{line.strip()[:90]}")
        for m in _A_LABEL.finditer(line):
            n = int(m.group(1))
            if any(k in line for k in RUN_ALL_ONLY) and ACCEPTANCE_RINGS[n] not in line:
                problems.append(
                    f"第 {lineno} 行：A{n} 与 run_all 语义同现"
                    f"（应为 R{n}），原文：{line.strip()[:90]}")

    # ---- 3. 「R5 = 云台」这类显式错误赋值
    assign = re.compile(r"([RA])([1-7])\s*(?:=|：|:|是)\s*([^\s，,。；;|]+)")
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in assign.finditer(line):
            family, n, value = m.group(1), int(m.group(2)), m.group(3)
            canonical = (RUN_ALL_RINGS if family == "R" else ACCEPTANCE_RINGS)[n]
            other = (ACCEPTANCE_RINGS if family == "R" else RUN_ALL_RINGS)[n]
            if other in value and canonical not in value:
                problems.append(
                    f"第 {lineno} 行：{family}{n} 被写成「{value}」，同号不同义")
    return problems


def _selftest() -> int:
    """反证：把两套编号写成一模一样，检查必须红。"""
    doc = (ROOT / "docs" / "hw-acceptance.md").read_text(encoding="utf-8")
    if check_text(doc):
        print("[selftest] 基线文档已不通过，先修文档再谈反证：")
        for p in check_text(doc):
            print("  -", p)
        return 1
    mutations = {
        "acceptance 编号写成 R": doc + "\n设备链视角：R5 云台、R6 串口 GNSS。\n",
        "R5 行写成云台": doc + "\n| R5 | 云台回读 | 占位 |\n",
        "A5 行写成卫星过境": doc + "\n| A5 | 卫星追踪与过境 | 占位 |\n",
        "对照表删掉 A 段": "\n".join(
            line for line in doc.splitlines()
            if not re.match(r"\|\s*A[1-7]\s*\|", line)),
    }
    bad = 0
    for name, text in mutations.items():
        problems = check_text(text)
        if problems:
            print(f"[selftest] 红得预期：{name} -> {problems[0]}")
        else:
            print(f"[selftest] 失败：{name} 未被判红（这个闸是假的）")
            bad += 1
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="环编号同号不同义检查")
    ap.add_argument("--doc", default=str(ROOT / "docs" / "hw-acceptance.md"))
    ap.add_argument("--selftest", action="store_true", help="反证：改坏编号必须判红")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    text = Path(args.doc).read_text(encoding="utf-8")
    problems = check_text(text)
    if problems:
        print(f"[ring-labels] {args.doc}：{len(problems)} 个问题")
        for p in problems:
            print("  -", p)
        return 1
    print(f"[ring-labels] OK：{args.doc} 的 R/A 编号无同号不同义")
    return 0


if __name__ == "__main__":
    sys.exit(main())
