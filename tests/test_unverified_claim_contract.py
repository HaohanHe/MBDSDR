#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""「代码已接、真机未验」必须落账到附 U 的契约守卫。

判据来源：`docs/limitations-register.md` §附 U 开头的纪律（提交 9a0a7157）：

    代码注释、提交说明或汇报里写「代码已接、真机未验」，必须同时在本附录
    加一行——写清①哪条路径②验了什么③没验什么④怎么验。缺这一行，那句话不算数。

散文规则没人执行，本文件把它变成机检：

  * 扫描 git 跟踪的 `.py` / `.md`，逐处找出「真机未验」与「代码已接」；
  * 只认简写「真机未验」（不以「证」续接）。「真机未验证」是附 E L43 与
    `docs/doppler-compensation.md` §3.8.1 规定的**运行时文案**（PTT 按下前
    必须显示），不是「有代码、缺真机」的残留声明，不属于本纪律的判据对象；
  * 逐处判定覆盖：附 U「哪条路径」里出现过的代码文件，按其**所在类名**做精确
    匹配（同一文件里别的后端的 U 条目不算）；测试与文档按其**点名的后端**做
    匹配（必须指名道姓，存在任意一条 U 不算覆盖）；标「待立项」的 U.7 记录的是
    还没接线的待办，不能给「代码已接」背书，不计入覆盖；
  * 每条 U 条目还必须写全①哪条路径②验了什么③没验什么④怎么验，缺项即红；
  * 附 U 标题与纪律段、本检查自身是规则原文，显式豁免并写明理由；
  * 模块级注释按所在连续段落（前后空行之间）里的函数名判定——一处注释可能
    同时声明整段工具，必须对上一个登记的真实 handler；一条 U 登记多个 handler
    时（U.8 的 7 个 `_lt_*`）允许在所在函数范围内对上登记名，单符号条目不放宽。

历史缺口（`mcp_server.py` 解码器长尾）已由 U.8 补齐，`_PENDING` 现为空；
新缺口不再给白名单，直接红。`_PENDING` 机制保留给将来「先报后补」的存量缺口：
条目必须仍然存在且仍未覆盖，补上 U 后本检查会红并提示销账（棘轮）。
"""
import ast
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTER = "docs/limitations-register.md"
SELF = "tests/test_unverified_claim_contract.py"

# 「代码已接」与简写「真机未验」；「真机未验证」是运行时文案，见文件头说明。
CLAIM_RE = re.compile(r"代码已接|真机未验(?!证)")
U_HEADING_RE = re.compile(r"^### (U\.\d+)\s*(.*)$")
CELL_KEYS = ("哪条路径", "验了什么", "没验什么", "怎么验")
_PATH_RE = re.compile(r"([\w./]+\.py):([A-Za-z_][\w.]*)")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,}")

# 存量缺口白名单（棘轮）。当前为空：mcp_server 解码器长尾已由 U.8 补齐。
# 将来「先报后补」时在此登记：(文件, 该行锚点, 缺什么)。锚点是字面量，行号会漂；
# 条目消失或变得「已被 U 覆盖」都说明该销账了。
_PENDING = []

# 显式豁免：文件/区段 + 理由（必须能打印出来，不是默默跳过）。
_SELF_EXEMPT = "检查自身的判据文本与规则说明，不是能力声明"
_RULE_TEXT_EXEMPT = "附 U 标题与纪律段是规则原文（本检查要执行的句子），不是能力声明"


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _section_bounds(lines):
    """返回（标题行下标, 首个 `### U.x` 行下标, 段末下标）；纪律段=两者之间。"""
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## 附 U")), None)
    assert start is not None, (
        f"{REGISTER} 里找不到「## 附 U」——纪律落账表没了，先修文档再谈机检")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    first_entry = next((i for i in range(start + 1, end)
                        if lines[i].startswith("### U.")), end)
    return start, first_entry, end


def _aliases(symbol):
    stem = symbol[:-len("Backend")] if symbol.endswith("Backend") else symbol
    return {symbol, stem}


def _u_entries():
    """解析附 U：每条给 文件 / 符号 / 别名 / 缺哪几个必填字段。"""
    lines = _read(REGISTER).splitlines()
    start, first_entry, end = _section_bounds(lines)
    entries, current = [], None
    for i in range(first_entry, end):
        line = lines[i]
        m = U_HEADING_RE.match(line)
        if m:
            current = {"id": m.group(1), "title": m.group(2), "body": [], "at": i + 1}
            entries.append(current)
        elif current is not None:
            current["body"].append(line)
    assert entries, f"{REGISTER} §附 U 里没有任何 `### U.x` 条目"
    # U.0 是无表格的「通用复现骨架」，不承担覆盖；要覆盖就得有「哪条路径」表格。
    claims = [e for e in entries if any(ln.startswith("|") for ln in e["body"])]
    assert claims, f"{REGISTER} §附 U 里没有任何带表格的条目"
    parsed = []
    for e in claims:
        path_line = next((ln for ln in e["body"] if ln.startswith("| 哪条路径")), "")
        matches = list(_PATH_RE.finditer(path_line))
        assert matches, (
            f"附 U {e['id']}（{REGISTER}:{e['at']}）的「哪条路径」解析不到 "
            f"`file.py:符号`，无法按路径对账：{path_line[:80]!r}")
        # 一条 U 可以登记同文件里的多个 handler（如 U.8 的 7 个 _lt_*）。
        symbols = [m.group(2).split(".")[0] for m in matches]
        missing = [k for k in CELL_KEYS
                   if not any(ln.startswith(f"| {k}") for ln in e["body"])]
        parsed.append({
            "id": e["id"], "file": matches[0].group(1), "symbols": symbols,
            "aliases": set().union(*(_aliases(s) for s in symbols)),
            "missing_cells": missing, "at": e["at"],
            # 「待立项」记录的是还没接线的待办，不能给「代码已接」背书。
            "covers": "待立项" not in e["title"],
        })
    return parsed, start, first_entry, end


def _tracked_files():
    proc = subprocess.run(
        ["git", "-C", ROOT, "ls-files", "-z"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"git ls-files 失败，无法确定扫描范围：{proc.stderr[:200]}"
    files = [p for p in proc.stdout.split("\0")
             if p.endswith((".py", ".md")) and os.path.exists(os.path.join(ROOT, p))]
    if SELF not in files:
        files.append(SELF)
    return files


def _enclosing_names(tree, lineno):
    """该行所在的类名（可能多层嵌套），用于「同文件精确匹配」。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                names.append(node.name)
    return names


def _py_context(rel, lines, lineno):
    """命中行的判定上下文：命中行本身；若落在 docstring 内，取整段 docstring。

    不取整个函数体——大函数里顺带出现的别的后端名会把「这里没点名」洗成
    「已覆盖」。模块级注释例外：取前后最近空行之间的整段（段落），这样一处
    注释声明整段工具时能对上一个真实 handler。
    返回 (别名匹配上下文, 行/类级符号, 所在函数或段落内符号)；第三项只对登记了
    多个 handler 的 U 条目开放（例 U.8），单符号条目仍只认行/类级符号。
    """
    src = "\n".join(lines)
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"{rel}:{lineno} 命中判据但文件无法解析（{e}），"
                             "无法判定所在后端，按失败处理") from e
    symbols = set(_enclosing_names(tree, lineno))
    context = lines[lineno - 1]
    enclosing = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.lineno <= lineno
                 <= getattr(node, "end_lineno", node.lineno)]
    if not enclosing:
        lo = lineno - 1
        while lo > 0 and lines[lo - 1].strip():
            lo -= 1
        hi = lineno
        while hi < len(lines) and lines[hi].strip():
            hi += 1
        paragraph = "\n".join(lines[lo:hi])
        scope = symbols | set(re.findall(r"[A-Za-z_]\w*", paragraph))
        return context, scope, scope
    innermost = max(enclosing, key=lambda n: n.lineno)
    scope = set(re.findall(r"[A-Za-z_]\w*",
                           ast.get_source_segment(src, innermost) or ""))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            hi = getattr(first, "end_lineno", first.lineno)
            if first.lineno <= lineno <= hi:
                context = "\n".join(lines[first.lineno - 1:hi])
                break
    symbols |= set(re.findall(r"([A-Za-z_]\w*Backend)\b", context))
    return context, symbols, scope


def _entry_matches(entry, token):
    t = token.lower()
    for alias in entry["aliases"]:
        a = alias.lower()
        if t == a or (len(t) >= 4 and a.startswith(t)) or (len(t) >= 4 and t.startswith(a)):
            return True
    return False


def _is_backend_token(token, entries):
    return token.endswith("Backend") or any(_entry_matches(e, token) for e in entries)


def _verdict(occ, entries):
    """返回 (是否覆盖, 命中的 U id 列表, 缺什么)。"""
    usable = [e for e in entries if e["covers"]]
    same_file = [e for e in usable if e["file"] == occ["rel"]]
    if same_file:
        hit = [e["id"] for e in same_file
               if set(e["symbols"]) & occ["symbols"]
               # 一条 U 登记多个 handler 时（如 U.8 的 7 个 _lt_*），
               # 允许在其所在函数范围内找登记过的名字；单符号条目不放宽。
               or (len(e["symbols"]) > 1
                   and set(e["symbols"]) & occ["scope_symbols"])]
        if hit:
            return True, hit, ""
        return False, [], (
            f"同一文件里的附 U 条目 {[e['id'] for e in same_file]} 对不上所在后端"
            f"（该处符号：{sorted(occ['symbols']) or '无'}）")

    mentioned = {t for t in _TOKEN_RE.findall(occ["context"])
                 if _is_backend_token(t, usable)}
    if not mentioned:
        return False, [], ("无法定位到后端/路径（该处没点名任何附 U 覆盖的后端），"
                           "不能靠「附 U 里存在任意一条」充数")
    hit = {e["id"] for e in usable for t in mentioned if _entry_matches(e, t)}
    uncovered = sorted(t for t in mentioned
                       if not any(_entry_matches(e, t) for e in usable))
    if uncovered:
        return False, sorted(hit), f"点名的后端 {uncovered} 在附 U 里没有对应条目"
    return True, sorted(hit), ""


def _scan(entries):
    lines = _read(REGISTER).splitlines()
    start, first_entry, _ = _section_bounds(lines)
    covered, violations, exemptions = [], [], []
    for rel in _tracked_files():
        text = _read(rel)
        if not CLAIM_RE.search(text):
            continue
        file_lines = text.splitlines()
        for i, line in enumerate(file_lines, 1):
            if not CLAIM_RE.search(line):
                continue
            if rel == SELF:
                exemptions.append((rel, i, _SELF_EXEMPT))
                continue
            if rel == REGISTER and start < i <= first_entry:
                exemptions.append((rel, i, _RULE_TEXT_EXEMPT))
                continue
            if rel.endswith(".py"):
                context, symbols, scope = _py_context(rel, file_lines, i)
            else:
                context, symbols, scope = line, set(), set()
            occ = {"rel": rel, "line": i, "text": line.strip(),
                   "context": context, "symbols": symbols, "scope_symbols": scope}
            ok, hit, why = _verdict(occ, entries)
            if ok:
                covered.append((rel, i, hit))
            else:
                violations.append({**occ, "why": why})
    return covered, violations, exemptions


def _report(rows):
    return "\n".join(
        f"  - {r['rel']}:{r['line']}  {r['text'][:100]}\n    缺：{r['why']}"
        for r in rows)


def test_appendix_u_entries_are_complete():
    """每条 U 都必须写全①哪条路径②验了什么③没验什么④怎么验。"""
    entries, _, _, _ = _u_entries()
    incomplete = [e for e in entries if e["missing_cells"]]
    assert not incomplete, (
        "附 U 条目缺必填字段（纪律要求写清①路径②验了什么③没验什么④怎么验）：\n"
        + "\n".join(f"  - {e['id']}（{REGISTER}:{e['at']}）缺 "
                    f"{'/'.join(e['missing_cells'])}" for e in incomplete))


def test_unverified_claims_have_appendix_u_entry():
    """每处「代码已接、真机未验」都要有按后端/路径对得上的 U 条目。"""
    entries, _, _, _ = _u_entries()
    covered, violations, _ = _scan(entries)
    pending_keys = {(rel, anchor) for rel, anchor, _ in _PENDING}
    unexplained = [v for v in violations
                   if not any(v["rel"] == rel and anchor in v["text"]
                              for rel, anchor in pending_keys)]
    assert not unexplained, (
        f"{len(unexplained)} 处「代码已接、真机未验」没有对应的附 U 条目：\n"
        + _report(unexplained)
        + "\n修法：在 docs/limitations-register.md §附 U 补一条 U.x（①哪条路径"
          "②验了什么③没验什么④怎么验），或把该表述改成不声称未验证；"
          "不要删检查、不要放宽判据。")
    # 覆盖必须是「指名道姓」的：有命中条目的才计入，空命中不算。
    assert all(hit for _, _, hit in covered), covered


def test_existing_gaps_are_tracked_and_still_open():
    """棘轮：`_PENDING` 里的缺口必须仍然存在且仍未覆盖，否则提示销账。"""
    entries, _, _, _ = _u_entries()
    _, violations, _ = _scan(entries)
    problems = []
    for rel, anchor, need in _PENDING:
        hits = [v for v in violations if v["rel"] == rel and anchor in v["text"]]
        if not hits:
            problems.append(
                f"{rel} 里的待补条目（锚点 {anchor!r}）已消失或已被附 U 覆盖——"
                "若已补 U 条目，请从 _PENDING 移除该行（{0}）".format(need))
    assert not problems, "待补清单与现状不一致：\n  - " + "\n  - ".join(problems)


def test_rule_text_and_self_are_explicitly_exempted():
    """豁免必须带理由并真的命中；规则原文搬家后旧豁免不能静默失效。"""
    entries, _, _, _ = _u_entries()
    _, _, exemptions = _scan(entries)
    assert exemptions, "豁免清单一条都没命中——附 U 纪律段/本检查的表述已变，先核对"
    for rel, line, reason in exemptions:
        assert reason.strip(), f"{rel}:{line} 被豁免但没有理由"
