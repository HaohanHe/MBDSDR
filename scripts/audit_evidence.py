#!/usr/bin/env python3
"""数字级证据闸门：扫 docs/**/*.md 与 experiments/**/README.md 里的测量数字。

判据见 docs/evidence-standard.md（E1 有原始件 / E2 可复跑 / E3 不可复跑 / E4 未验证）。

分类逻辑（同一段 ±2 行窗口）：
  * 有 ``未验证`` / ``证据不足`` / ``待核实`` 等标记 -> 已显式标未验证；
  * 命中仓内存在的原始件路径（非 .md）-> E1 有原始件；
  * 命中可执行脚本命令（python/pytest/ctest/make/scripts 路径）-> E2 可复跑；
  * 只命中 commit -> E3 不可复跑（时点线索，不构成测量证据，仍计入“无证据”的提示项）；
  * 只命中 ``.tmp-tool-results`` 历史产物 -> E3 不可复跑；
  * 都没有 -> 无证据（红）。

退出码：存在“无证据且未标未验证”即 1，否则 0。

用法：
    python scripts/audit_evidence.py                # 全仓默认范围
    python scripts/audit_evidence.py --paths a.md   # 只查指定文件（反证用）
    python scripts/audit_evidence.py --json         # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

# 带单位的测量数字；纯小数（0.49）只在“测量语境行”才计入断言。
UNIT = (r"(?:%|dBm|dBi|dB|MHz|GHz|kHz|Hz|ms|µs|us|ns|ppm|km|cm|mm|m|"
        r"mV|V|mA|A|mW|W|bps|kbps|Mbps|Gbps|bit|bits|载波|帧|条|倍|次|°|Ω|ohm)")
NUM_RE = re.compile(
    rf"(?<![\w./-])(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)(?:\s*({UNIT}))?"
)

# 只有带“测量/验证”动词的段落才入账；否则 0.25/98% 之类的排版数字会淹没账本。
# 表格按“块”整体判定：表头写了实测/谐振，整张表都算测量断言。
CLAIM_VERB_RE = re.compile(
    r"实测|测量|测得|验证|已验|真机|复跑|通过|证明|复现|回损|驻波|VSWR|"
    r"RMS|SNR|Eb/N0|BER|误码|谐振|标定"
)
# 纯参数/默认值句（没有实测动作）不算测量断言，否则设置清单会被全量误报
SPEC_LINE_RE = re.compile(
    r"默认|阈值|上限|下限|范围|配置|设置项|可供|可选|步进|档位|分辨率|预算"
)
MEASURED_LINE_RE = re.compile(
    r"实测|测得|测量|读数|回损|驻波|VSWR|RMS|SNR|Eb/N0|BER|误码|标定|校准|复跑"
)
# 无单位的纯小数（0.49 / 14.96）只在同句有这些指标名时才算断言
METRIC_NEAR_RE = re.compile(
    r"RMS|SNR|Eb/N0|BER|误码|误差|回损|驻波|VSWR|精度|耗时|时延|时差|频率|"
    r"增益|功率|噪声|载波|系数|相位|时长|速率|温度|电压|电流|比例|占比|口径"
)

# 证据标记
UNVERIFIED_RE = re.compile(
    r"未验证|证据不足|待核实|待真机验证|待真机确认|不可复跑|未实测|"
    r"未做过真机验证|synthetic-only|no-artifact|contradicted|存档不足以支持|"
    r"无原始件|未取证|仅文档自述|按未验证"
)
COMMIT_TOKEN_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
PATH_RE = re.compile(
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:json|csv|rgb|sigmf-data|sigmf-meta|"
    r"log|bmp|wav|bin|dat|npy|npz|png|jpg|jpeg|txt|tsv|md)"
)
CMD_RE = re.compile(
    r"(?:python(?:3)?\s+(?:-m\s+)?[A-Za-z0-9_./-]+|pytest\s+[^\s`]+|"
    r"ctest(?:\s+[^\s`]+)?|make(?:\s+[A-Za-z0-9_-]+)?|"
    r"(?:scripts|tools)/[A-Za-z0-9_./-]+\.(?:py|sh|ps1))"
)
LINE_REF_RE = re.compile(r"\w+\.(?:py|cpp|h|md|json|csv|txt):\d+")
DATE_RE = re.compile(r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}")
URL_RE = re.compile(r"https?://\S+")


def _files(paths: Optional[List[str]]) -> List[Path]:
    if paths:
        return [Path(p) if Path(p).is_absolute() else ROOT / p for p in paths]
    out: List[Path] = []
    out.extend(sorted((ROOT / "docs").rglob("*.md")))
    out.extend(sorted((ROOT / "experiments").rglob("README.md")))
    return [p for p in out if p.is_file()]


def _prose_lines(text: str) -> Iterator[Tuple[int, str]]:
    """跳过围栏代码块，只返回正文行（1-based 行号）。"""
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield i, line


_commit_cache: Dict[str, bool] = {}


def _is_commit(tok: str) -> bool:
    if tok in _commit_cache:
        return _commit_cache[tok]
    ok = False
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{tok}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True, timeout=6,
        )
        ok = r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        ok = False
    _commit_cache[tok] = ok
    return ok


def _resolve(tok: str) -> Optional[Path]:
    tok = tok.strip("`'\"").rstrip(".,;:)]}")
    p = Path(tok)
    cand = p if p.is_absolute() else ROOT / p
    return cand if cand.exists() else None


def _classify(window: str) -> Tuple[str, str]:
    """返回 (列名, 证据说明) 列名 ∈ 有证据/无证据/已显式标未验证。"""
    if UNVERIFIED_RE.search(window):
        return "已显式标未验证", "有未验证/证据不足标记"

    paths = PATH_RE.findall(window)
    for tok in paths:
        p = _resolve(tok)
        if p is None:
            continue
        if ".tmp-tool-results" in tok:
            return "有证据", f"E3 不可复跑（历史产物 {tok}）"
        if p.suffix.lower() != ".md":
            return "有证据", f"E1 有原始件（{tok}）"

    cmds = CMD_RE.findall(window)
    for cmd in cmds:
        # 命令里若带仓内路径，要求路径存在；裸命令（pytest/make）视为可复跑声明
        toks = re.findall(r"[A-Za-z0-9_./-]+\.(?:py|sh|ps1)", cmd)
        if not toks or any(_resolve(t) for t in toks):
            return "有证据", f"E2 可复跑（`{cmd.strip()}`）"

    commits = [t for t in COMMIT_TOKEN_RE.findall(window) if _is_commit(t)]
    if commits:
        return "无证据", f"仅 commit {commits[0]}（E3 时点线索，非测量证据）"

    return "无证据", "无 commit/原始件/复跑命令/未验证标记"


def _blocks(prose: List[Tuple[int, str]]) -> Iterator[List[Tuple[int, str]]]:
    """把正文切成“断言块”：连续表格行算一块，空行/新段落结束一块。"""
    cur: List[Tuple[int, str]] = []
    for lineno, line in prose:
        s = line.strip()
        if not s:
            if cur:
                yield cur
                cur = []
            continue
        if not cur:
            cur = [(lineno, line)]
            continue
        prev = cur[-1][1].strip()
        # 表格连续行；或标题（#）单独成块；其余同段延续
        is_tbl = s.startswith("|") and prev.startswith("|")
        is_head = s.startswith("#") or prev.startswith("#")
        if is_tbl or (not is_head and not prev.startswith("|")):
            cur.append((lineno, line))
        else:
            yield cur
            cur = [(lineno, line)]
    if cur:
        yield cur


INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _numbers(line: str) -> Iterable[str]:
    """只收测量数字：带单位的数，或同句有指标名的纯小数。"""
    clean = URL_RE.sub(" ", line)
    clean = DATE_RE.sub(" ", clean)
    clean = LINE_REF_RE.sub(" ", clean)
    clean = INLINE_CODE_RE.sub(" ", clean)      # 反引号里是代码/路径/哈希，不是断言
    metric_ctx = bool(METRIC_NEAR_RE.search(clean))
    for m in NUM_RE.finditer(clean):
        val, unit = m.group(1), m.group(2)
        if unit:
            if unit == "%" and not metric_ctx:
                continue
            end = m.end()
            # `9A` / `3V` 这类十六进制或代号不是测量值：单位后紧跟字母数字则跳过
            if end < len(clean) and clean[end].isalnum():
                continue
            yield m.group(0).strip()
        elif "." in val and metric_ctx:   # 纯小数，例如 0.49 / 14.96
            yield val


def run(paths: Optional[List[str]] = None) -> Dict:
    files = _files(paths)
    rows: List[Dict] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        prose = list(_prose_lines(text))
        rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
        for block in _blocks(prose):
            block_text = "\n".join(ln for _, ln in block)
            is_table = block[0][1].strip().startswith("|")
            if is_table:
                # 表格按整块判定：表头写“谐振/实测”，整张表都是测量断言
                if not CLAIM_VERB_RE.search(block_text):
                    continue
                lines = [(no, ln, block_text) for no, ln in block]
            else:
                # 散文按句/行判定，避免一段里一个“验证”把整段参数数字都算断言
                lines = [(no, ln, ln) for no, ln in block
                         if CLAIM_VERB_RE.search(ln)
                         and not (SPEC_LINE_RE.search(ln)
                                  and not MEASURED_LINE_RE.search(ln))]
            for lineno, line, window in lines:
                col, why = _classify(window)
                for num in _numbers(line):
                    rows.append({"file": rel, "line": lineno, "number": num,
                                 "column": col, "evidence": why})

    counts = Counter(r["column"] for r in rows)
    files_with_none = sorted({r["file"] for r in rows if r["column"] == "无证据"})
    return {"files": len(files), "claims": len(rows), "counts": dict(counts),
            "rows": rows, "files_with_none": files_with_none}


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="数字级证据闸门（判据：docs/evidence-standard.md）")
    ap.add_argument("--paths", nargs="*", default=None, help="只扫指定文件（默认全仓范围）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-list", type=int, default=80, help="每栏最多列多少条明细")
    args = ap.parse_args(argv)

    t0 = time.time()
    rep = run(args.paths)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        c = rep["counts"]
        print(f"扫描 {rep['files']} 个文件，{rep['claims']} 条数字断言"
              f"（{time.time() - t0:.1f}s）\n")
        print("| 有证据 | 无证据 | 已显式标未验证 |")
        print("| --- | --- | --- |")
        print(f"| {c.get('有证据', 0)} | {c.get('无证据', 0)} "
              f"| {c.get('已显式标未验证', 0)} |\n")

        for col in ("无证据", "有证据", "已显式标未验证"):
            items = [r for r in rep["rows"] if r["column"] == col]
            print(f"## {col}（{len(items)}）")
            if not items:
                print("（无）\n")
                continue
            shown = 0
            for r in items:
                if shown >= args.max_list:
                    print(f"… 其余 {len(items) - shown} 条见 --json")
                    break
                print(f"- `{r['file']}:{r['line']}` {r['number']} — {r['evidence']}")
                shown += 1
            print()

    bad = rep["counts"].get("无证据", 0)
    if not args.json:
        if bad:
            print(f"结论：红 —— {bad} 条数字断言既无证据也未标未验证。")
        else:
            print("结论：绿 —— 所有数字断言都有证据或已显式标未验证。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
