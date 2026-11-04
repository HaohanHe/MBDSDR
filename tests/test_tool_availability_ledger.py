# SPDX-License-Identifier: MIT
"""P13 台账自洽守卫：docs/tool-availability.md 的分组计数必须与逐行表一致。

背景：这份清单是「再多买一样东西能解锁多少个工具」的唯一依据。只要有人在表里
手改一行、或审计脚本改了分组规则却忘了重跑，计数与行数就会漂移。此测试把
「分组计数 == 表内行数」变成入库门禁，且不依赖真机（纯文本解析）。

复跑：pytest tests/test_tool_availability_ledger.py -q
重新生成台账：python scripts/tool_availability_audit.py
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "tool-availability.md"

GROUP_LABELS = ("① SDR", "② 额外硬件/外部程序", "③ 真样本", "④ 网络/API", "⑤ 人工确认")


def _doc_text() -> str:
    assert DOC.exists(), f"台账不存在：{DOC}（先跑 scripts/tool_availability_audit.py）"
    return DOC.read_text(encoding="utf-8")


def _counts_from_conclusion(text: str):
    m = re.search(
        r"结论：一类 (\d+) / 二类 (\d+) / 三类 (\d+) / 四类 (\d+)", text)
    assert m, "台账缺少「结论：一类 … / 二类 …」行"
    return [int(x) for x in m.groups()]


def _group_table(text: str):
    """解析「## 补齐清单」下的分组计数表。"""
    sec = text.split("## 补齐清单", 1)[1].split("## ", 1)[0]
    out = {}
    for line in sec.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] not in GROUP_LABELS:
            continue
        out[cells[0]] = int(cells[1])
    return out


def _group_name_lines(text: str):
    """解析「各组工具名」里每个分组的工具数（括号里的数字）。"""
    sec = text.split("各组工具名", 1)[1].split("## ", 1)[0]
    out = {}
    for line in sec.splitlines():
        for g in GROUP_LABELS:
            m = re.match(rf"- {re.escape(g)}（(\d+)）", line)
            if m:
                out[g] = int(m.group(1))
    return out


def _main_rows(text: str):
    sec = text.split("## 逐项总表", 1)[1]
    rows = []
    for line in sec.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 行内可能含转义竖线，按首列工具名 + 末列分组取即可
        rows.append((cells[0], cells[2], cells[-1]))
    return rows


def test_group_counts_match_rows():
    text = _doc_text()
    runs, honest, stub, broken = _counts_from_conclusion(text)
    total = int(re.search(r"工具总数：\*\*(\d+)\*\*", text).group(1))
    assert runs + honest + stub + broken == total, "四类计数之和 != 工具总数"

    rows = _main_rows(text)
    assert len(rows) == total, f"逐行表行数 {len(rows)} != 工具总数 {total}"
    honest_rows = [r for r in rows if "二类" in r[1]]
    assert len(honest_rows) == honest, (
        f"逐行表里二类行 {len(honest_rows)} != 结论 {honest}")

    table = _group_table(text)
    name_lines = _group_name_lines(text)
    assert set(table) == set(GROUP_LABELS), "补齐清单缺少分组"
    assert sum(table.values()) == honest, (
        f"分组计数合计 {sum(table.values())} != 二类 {honest}")
    assert name_lines == table, f"各组工具名计数 {name_lines} != 分组表 {table}"

    for g in GROUP_LABELS:
        in_rows = sum(1 for r in honest_rows if r[2] == g)
        assert in_rows == table[g], (
            f"分组「{g}」逐行 {in_rows} 行 != 清单 {table[g]}")
    ungrouped = [r[0] for r in honest_rows if r[2] not in GROUP_LABELS]
    assert not ungrouped, f"二类行未标补齐组：{ungrouped}"


def test_no_fabricated_success_left():
    """success 却自述失败的行必须清零（修复后应为 0）。"""
    text = _doc_text()
    m = re.search(r"## 疑似「成功文案里藏虚构」（(\d+) 项）", text)
    assert m, "台账缺少虚构成功小节"
    n = int(m.group(1))
    sec = text.split("## 疑似", 1)[1].split("## ", 1)[0]
    listed = len(re.findall(r"^- `", sec, flags=re.MULTILINE))
    assert listed == n, f"小节声明 {n} 项，实际列出 {listed} 项"
