#!/usr/bin/env python3
"""数字级证据闸门：扫 docs/**/*.md 与 experiments/**/README.md 里的测量数字。

判据见 docs/evidence-standard.md（E1 有原始件 / E2 可复跑 / E3 不可复跑 / E4 未验证）。

分类逻辑（散文：同一段 ±2 行窗口；表格：本行 + 表头 + 紧邻表标题行）：
  * 有 ``未验证`` / ``证据不足`` / ``待核实`` 等标记 -> 已显式标未验证；
  * 命中仓内存在的原始件路径（非 .md）-> E1 有原始件；
  * 命中可执行脚本命令（python/pytest/ctest/make/scripts 路径）-> E2 可复跑；
  * 只命中 commit -> 无证据（E3 时点线索，不构成测量证据）；
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
    r"RMS|SNR|Eb/N0|BER|误码|谐振|标定|约|≈"
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

# ---- 五类分诊（只用于“无证据”行；分类不改变闸门退出码）----
# 文件级 E4 横幅：**显式**声明「本文件数字未验证」。只认写在文件开头（前 80 行）
# 的这几种写法，不按目录名猜测——phase*/audit_r2 这类路径外延会变成看不见的
# 全文件豁免。有了它，历史快照只需在文件头写一行，不必逐条贴标记。
FILE_BANNER_RE = re.compile(
    r"(?:本文件|全文|整篇|本页)[^\n]{0,24}(?:未验证|证据不足|E4)"
    r"|证据状态\s*[:：]\s*E4"
)
#: ① 本来就不需要证据：参数/规格/判据、标准或外部引用、可推导常量
BUCKET_SPEC_RE = re.compile(
    r"默认|阈值|上限|下限|范围|配置|设置项|可供|可选|步进|档位|分辨率|量程|枚举|标称|"
    r"规格|单位|换算|定义|公式|λ/4|λg|λ/2|频段|下行|上行|频道|判据|容差|≤|≥"
)
BUCKET_REF_RE = re.compile(
    r"标准|规范|教程|材料给出|协议|文献|论文|参考|引用|ITU|CCSDS|3GPP|IEC|GB/T|"
    r"EN 302|教材|实测口径"
)
BUCKET_DERIVE_RE = re.compile(r"bin 宽|由 ?fs|推导|核算|= ?fs|fs/?N|N ?= ?\d|相干增益")
MEASURE_VERB_RE = re.compile(r"实测|测得|读数|真机(?!红|绿)|现场|当晚|复跑|验证|测量")
# ④ 验收/性能结论：载荷性数字，标“未验证”不够，必须补测留件
BUCKET_ACCEPT_FILE_RE = re.compile(
    r"docs/hw-acceptance\.md|docs/real_hw_acceptance_|dsp-performance-baseline"
)
BUCKET_ACCEPT_LINE_RE = re.compile(r"PASS|通过|达标|验收|总耗时|本机实测|实测")
# ⑤ 应删除或改写：已划线撤回、显式作废、预期值冒充实测
BUCKET_RETRACT_RE = re.compile(
    r"~~|已撤回|作废|应删|不再引用|是预期值|预期值.{0,8}非实测|非实测|已推翻"
)
# ③ 工件存在但没写路径：按文件名去仓内索引里找
ARTIFACT_EXT = (r"(?:json|csv|wav|rgb|sigmf-data|sigmf-meta|npy|npz|bmp|png|iq|"
                r"bin|dat|log|txt|tsv)")
ARTIFACT_TOKEN_RE = re.compile(rf"([A-Za-z0-9_.-]+\.{ARTIFACT_EXT})")
EXP_SCRIPT_RE = re.compile(r"([A-Za-z0-9_.-]+\.py)")
EXPERIMENT_README_RE = re.compile(r"^experiments/[^/]+/README\.md$")


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


def _classify(window: str, line: Optional[str] = None,
              file_unverified: bool = False) -> Tuple[str, str]:
    """返回 (列名, 证据说明) 列名 ∈ 有证据/无证据/已显式标未验证。"""
    if UNVERIFIED_RE.search(window):
        # 表格块的“未验证”只写在表头/别行时，不自动给整表背书：
        # 本行若真带原始件，按 E1 计；否则仍记“已标未验证”，但注明是借用标记。
        if line is not None and not UNVERIFIED_RE.search(line):
            for tok in PATH_RE.findall(line):
                p = _resolve(tok)
                if p is None:
                    continue
                if ".tmp-tool-results" in tok:
                    return "有证据", f"E3 不可复跑（历史产物 {tok}；块级标记不覆盖本行）"
                if p.suffix.lower() != ".md":
                    return "有证据", f"E1 有原始件（{tok}；块级标记不覆盖本行）"
            return "已显式标未验证", "仅窗口内他行标未验证（本行无标注，需核）"
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
    if file_unverified:
        # 文件头横幅强于「仅 commit」：整份文档已声明是历史快照/未验证。
        return "已显式标未验证", "文件级 E4 横幅（本文件数字未验证）"
    if commits:
        return "无证据", f"仅 commit {commits[0]}（E3 时点线索，非测量证据）"

    return "无证据", "无 commit/原始件/复跑命令/未验证标记"


_artifact_index: Optional[Dict[str, str]] = None


def _artifact_names() -> Dict[str, str]:
    """仓内数据工件文件名索引（用于判断“有工件、只是没写路径”）。"""
    global _artifact_index
    if _artifact_index is None:
        idx: Dict[str, str] = {}
        skip_dirs = {".git", "__pycache__", "node_modules", "build", ".venv"}
        for dirpath, dirnames, files in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fn in files:
                idx.setdefault(fn, os.path.join(dirpath, fn))
        _artifact_index = idx
    return _artifact_index


def _has_sibling_data(rel: str) -> bool:
    """experiments/<name>/README.md 同目录是否存在 data/ 采集件。"""
    d = (ROOT / rel).parent / "data"
    if not d.is_dir():
        return False
    return any(p.is_file() and p.suffix.lower() not in {".md", ".py"} for p in d.iterdir())


def _is_table_sep(line: str) -> bool:
    t = line.strip()
    return t.startswith("|") and "-" in t and not any(ch.isalnum() for ch in t)


def _bucket(rel: str, line: str) -> Tuple[str, str]:
    """给“无证据”行一个处置类（①–⑤）。规则可解释、可复跑；分类不放行。"""
    rel = rel.replace("\\", "/")
    # ⑤ 已撤回/划线/预期值：应删除或改写
    if BUCKET_RETRACT_RE.search(line):
        return "⑤", "已划线/撤回/预期值冒充实测"
    # ③ 工件存在但没写路径
    idx = _artifact_names()
    arts = [t for t in ARTIFACT_TOKEN_RE.findall(line) if os.path.basename(t) in idx]
    if arts:
        return "③", f"工件存在未引路径（{arts[0]}）"
    sc = [t for t in EXP_SCRIPT_RE.findall(line)
          if os.path.basename(t).startswith("exp_") and os.path.basename(t) in idx]
    if sc:
        return "③", f"可复跑实验脚本存在未引路径（{sc[0]}）"
    if EXPERIMENT_README_RE.match(rel) and _has_sibling_data(rel):
        return "③", "同目录 data/ 有采集件，未逐条写路径"
    # ① 本来就不需要证据（只对没有测量动作的行生效）
    if not MEASURE_VERB_RE.search(line):
        if BUCKET_SPEC_RE.search(line):
            return "①", "参数/规格/判据定义"
        if BUCKET_REF_RE.search(line):
            return "①", "标准或外部引用"
        if BUCKET_DERIVE_RE.search(line):
            return "①", "可推导常量"
    # ④ 验收/性能结论：必须补测并留原始件
    if BUCKET_ACCEPT_FILE_RE.search(rel) and BUCKET_ACCEPT_LINE_RE.search(line):
        return "④", "验收/性能结论，须补测留件"
    # ② 其余测量断言：降级为未验证
    return "②", "测量断言无工件，降级为未验证"


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
    # 章节号（`## 2.1 signal_quality`）不是测量数字
    head = re.match(r"^\s*#{1,6}\s+(\d+(?:\.\d+)*)\s", line)
    for m in NUM_RE.finditer(clean):
        if head and m.group(1) == head.group(1) and m.start() < 12:
            continue
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


def run(paths: Optional[List[str]] = None, classify: bool = False) -> Dict:
    files = _files(paths)
    rows: List[Dict] = []
    skipped_examples = 0
    file_banners: List[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        prose = list(_prose_lines(text))
        rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
        file_unverified = bool(FILE_BANNER_RE.search("\n".join(text.splitlines()[:80])))
        if file_unverified:
            file_banners.append(rel)
        # 每个非空正文行的“上一非空行”：给表格找紧邻的表标题行用
        prev_nonblank: Dict[int, str] = {}
        last_text: Optional[str] = None
        for no, ln in prose:
            if ln.strip():
                if last_text is not None:
                    prev_nonblank[no] = last_text
                last_text = ln
        for block in _blocks(prose):
            block_text = "\n".join(ln for _, ln in block)
            is_table = block[0][1].strip().startswith("|")
            # 规范文件 §3 的「反例表」引用的是别人写错的原文，不是本仓断言（§4.0）：
            # 只认「docs/evidence-standard.md + 表头首格 == 反例」这一种形态，不做模糊匹配
            if is_table and rel.replace("\\", "/") == "docs/evidence-standard.md" and \
                    block[0][1].strip().strip("|").split("|")[0].strip() == "反例":
                skipped_examples += sum(1 for _, ln in block if not _is_table_sep(ln)) - 1
                continue
            if is_table:
                # 表格按整块判定：表头写“谐振/实测”，整张表都是测量断言
                if not CLAIM_VERB_RE.search(block_text):
                    continue
                # 证据窗口（2026-11-12）：本行 + 表头 + 紧邻表标题行；
                # 其他数据单元格里的路径不给整表背书（evidence-standard §2.6）
                title = prev_nonblank.get(block[0][0], "")
                header = block[0][1]
                base = "\n".join(x for x in (title, header) if x)
                # 表头与 |---|---| 分隔行不是断言（否则“90% 档”这类列名会入账）
                skip = {block[0][0]}
                if len(block) > 1 and _is_table_sep(block[1][1]):
                    skip.add(block[1][0])
                lines = [(no, ln, f"{base}\n{ln}" if base else ln)
                         for no, ln in block
                         if no not in skip and not _is_table_sep(ln)]
            else:
                # 散文按句/行判定，避免一段里一个“验证”把整段参数数字都算断言；
                # 证据窗口是同一段 ±2 行（2026-11-12 对齐规范 §4.1）
                block_map = {no: ln for no, ln in block}
                lines = [(no, ln, "\n".join(block_map[n] for n in range(no - 2, no + 3)
                                            if n in block_map))
                         for no, ln in block
                         if CLAIM_VERB_RE.search(ln)
                         and not (SPEC_LINE_RE.search(ln)
                                  and not MEASURED_LINE_RE.search(ln))]
            for lineno, line, window in lines:
                col, why = _classify(window, line, file_unverified)
                for num in _numbers(line):
                    row = {"file": rel, "line": lineno, "number": num,
                           "column": col, "evidence": why}
                    if classify and col == "无证据":
                        row["bucket"], row["bucket_reason"] = _bucket(rel, line)
                    rows.append(row)

    counts = Counter(r["column"] for r in rows)
    buckets = Counter(r["bucket"] for r in rows if r.get("bucket"))
    files_with_none = sorted({r["file"] for r in rows if r["column"] == "无证据"})
    return {"files": len(files), "claims": len(rows), "counts": dict(counts),
            "buckets": dict(buckets), "rows": rows, "files_with_none": files_with_none,
            "skipped_examples": skipped_examples,
            "file_banners": sorted(file_banners)}


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="数字级证据闸门（判据：docs/evidence-standard.md）")
    ap.add_argument("--paths", nargs="*", default=None, help="只扫指定文件（默认全仓范围）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-list", type=int, default=80, help="每栏最多列多少条明细")
    ap.add_argument("--classify", action="store_true",
                    help="对“无证据”行做五类分诊（①无需证据 ②降级未验证 ③有工件缺路径 "
                         "④必须补测 ⑤删除/改写）；分类不放行，退出码不变")
    ap.add_argument("--classify-samples", type=int, default=5, help="每类列多少条代表")
    args = ap.parse_args(argv)

    t0 = time.time()
    rep = run(args.paths, classify=args.classify)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        c = rep["counts"]
        print(f"扫描 {rep['files']} 个文件，{rep['claims']} 条数字断言"
              f"（{time.time() - t0:.1f}s）")
        if rep.get("skipped_examples"):
            print(f"（规范 §3 反例表引用行 {rep['skipped_examples']} 条不计，见 §4.0）")
        print()
        print("| 有证据 | 无证据 | 已显式标未验证 |")
        print("| --- | --- | --- |")
        print(f"| {c.get('有证据', 0)} | {c.get('无证据', 0)} "
              f"| {c.get('已显式标未验证', 0)} |\n")

        if args.classify:
            b = rep["buckets"]
            print("| ①无需证据 | ②降级未验证 | ③有工件缺路径 | ④必须补测 | ⑤删除/改写 |")
            print("| --- | --- | --- | --- | --- |")
            print(f"| {b.get('①', 0)} | {b.get('②', 0)} | {b.get('③', 0)} "
                  f"| {b.get('④', 0)} | {b.get('⑤', 0)} |\n")
            for bucket in ("①", "②", "③", "④", "⑤"):
                items = [r for r in rep["rows"] if r.get("bucket") == bucket]
                print(f"### {bucket}（{len(items)}）")
                for r in items[:args.classify_samples]:
                    print(f"- `{r['file']}:{r['line']}` {r['number']} — "
                          f"{r['bucket_reason']}")
                print()

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
