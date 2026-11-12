#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""审计：提交信息声称的范围 vs tree 里实际改动的路径（全历史，不是最近 N 笔）。

背景：共享 ``.git/index`` 导致提交归属错乱（``ddcecf76`` 是已知实例——消息是
体检线的文档坐标校正，tree 里却是冗余线的 ``scripts/config_write_guard.py``）。
事后靠人肉核对「这行是谁的」不可持续，所以把「消息声称」与「tree 实际」做成
可复跑的比对。

声称通道（只认可机读的两种，正文里的上下文提及不算声明）：
  P  前缀   —— ``docs(...)`` / ``test(...)`` 声明的类别，实际改动必须含文档类/
              测试类文件（任意层级的 ``docs/``、``*.md``、``test_*.py``、
              ``scripts/repro/`` 都算，``README.md`` 这类根目录文档也算文档）；
  S  scope  —— ``type(scope)`` 的 scope 本身写成路径时（含 ``/`` 或以
              ``.py``/``.md`` 结尾，且顶层目录在已知集合里），实际改动必须落在
              该路径或其目录下；
  D  点名   —— ``files:`` / ``文件：`` 这类显式清单行里的路径，必须在本笔改动里。

**不把正文里反引号包着的路径算成声明**——实测那样会从 33 笔涨到 136 笔，
且大量是「正文讨论到某文件」的上下文（棘轮基线条目清单、行号校正说明）；
放宽到「正文任何位置出现路径」是 957 笔。两条都不可用，噪声会淹没真错配。

判定「内容是否另行落库」（每条错配都给结论）：
  ① 同一 subject 的另一笔——消息复用正是 ddcecf76 的成因，这条能自动对上
     ``80a1b873``；
  ② 声称但没改的路径 → 时间上最近、且触及该路径的别的提交；
  ③ 多出来（消息没声称）的路径 → 是否另有落库；没有就是归属错位。
另标 ``comment_only``：本笔 +/- 行全是注释/docstring 引号行的，属于「改代码文件
里的注释却用 docs 前缀」的归类习惯，不算内容错配。

用法::

    python scripts/audit_commit_scope.py            # 全历史，打印统计 + 逐条
    python scripts/audit_commit_scope.py --quiet    # 只打印统计与计数

退出码：0（只读审计，不判红）。口径变化必须在这里改，不许在别处另抄一份。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH_RE = re.compile(
    r"((?:[\w.-]+/)+[\w.-]+\.(?:py|md|json|ps1|cpp|h|txt|yml|yaml|toml|spec|html|js|ts|css))"
    r"|((?<![\w/.-])[\w-]+\.(?:py|md|json|ps1|cpp|h))"
)
PREFIX_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:")
WINDOW = 400          # 「相距几笔」的搜索窗口
DOCS_EXT = (".md", ".rst", ".txt")
DOCS_NAME = ("readme", "changelog", "notice", "license", "licensing", "authors")
TOP_DIRS = {"docs", "tests", "scripts", "desktop", "mbdsdr_ai", "tools", "cpp", "mobile"}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout


def load_history() -> list[dict]:
    """一次 git log 拿全历史：sha / message / 本笔实际改动路径。"""
    raw = git("-c", "core.quotepath=false", "log", "--all", "--no-merges",
              "--no-renames", "--format=%x1e%H%x1f%B%x1d", "--name-only")
    commits = []
    for chunk in raw.split("\x1e")[1:]:
        head, _, tail = chunk.partition("\x1d")
        sha, _, msg = head.partition("\x1f")
        paths = [p for p in tail.strip().splitlines() if p.strip()]
        subject = msg.strip().splitlines()[0].strip() if msg.strip() else ""
        commits.append({"sha": sha.strip(), "msg": msg, "subject": subject,
                        "paths": sorted(paths)})
    return commits


def is_docs(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return (path.startswith("docs/") or path.endswith(DOCS_EXT)
            or any(name.startswith(n) for n in DOCS_NAME))


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    dirs = path.split("/")[:-1]
    return (name.startswith("test_") or name.endswith(("_test.py", "_test.dart"))
            or "tests" in dirs or "test" in dirs
            or "repro" in dirs)      # scripts/repro 是反证探针，同属测试面


def under(path: str, scope: str) -> bool:
    scope = scope.strip("/")
    return path == scope or path.startswith(scope + "/")


def declared_paths(msg: str, universe: set[str]) -> list[str]:
    """只认显式清单行（``files:`` / ``文件：``）；反引号提及是上下文，不算声明。"""
    blocks = []
    for line in msg.splitlines():
        if re.match(r"^\s*(files?|changed|paths?)\s*:", line, re.I) or \
           re.match(r"^\s*(文件|改动路径|涉及文件)\s*[:：]", line):
            blocks.append(line)
    out = []
    for text in blocks:
        for m in PATH_RE.finditer(text):
            token = (m.group(1) or m.group(2)).strip("/")
            if token in universe:
                out.append(token)
            elif any(p.endswith("/" + token) for p in universe):
                out.append(token)
    return sorted(set(out))


def comment_only(sha: str) -> bool:
    """本笔的 +/- 行是否全是注释行（``#``、空行、纯 docstring 引号行）。

    区分两种「docs 前缀却改了 .py」：只动注释属于归类习惯，动了代码逻辑才是
    消息与内容错配。docstring 正文行识别不了，会被当成代码（宁可误报不放过）。
    """
    patch = git("show", "--format=", "--unified=0", "--no-renames", sha)
    lines = [ln[1:] for ln in patch.splitlines()
             if (ln.startswith("+") or ln.startswith("-"))
             and not ln.startswith(("+++", "---"))]
    if not lines:
        return False
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#") or s in ('"""', "'''") \
                or s.startswith(("//", "/*", "*", 'r"""', "r'''")):
            continue
        if s.endswith(('"""', "'''")) and not s.startswith(("def ", "class ")):
            continue
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提交信息声称范围 vs tree 实际改动的全历史审计")
    ap.add_argument("--quiet", action="store_true", help="只打印统计，不逐条列出")
    args = ap.parse_args(argv)

    commits = load_history()
    universe = {p for c in commits for p in c["paths"]}
    old2new = list(reversed(commits))          # git log 是新→旧
    for i, c in enumerate(old2new):
        c["idx"] = i
    by_subject = defaultdict(list)
    touching = defaultdict(list)
    for c in old2new:
        if c["subject"]:
            by_subject[c["subject"]].append(c)
        for p in c["paths"]:
            touching[p].append(c)

    def landings_for(token: str, exclude_sha: str, idx: int):
        rows = []
        for path, cs in touching.items():
            if path == token or path.endswith("/" + token):
                rows.extend(x for x in cs if x["sha"] != exclude_sha)
        near = [x for x in rows if abs(x["idx"] - idx) <= WINDOW]
        return sorted(near, key=lambda x: abs(x["idx"] - idx))

    rows = []
    for c in old2new:
        hits = []
        m = PREFIX_RE.match(c["subject"])
        typ = (m.group("type") if m else "").lower()
        scope = (m.group("scope") if m else "") or ""
        actual = c["paths"]

        if typ == "docs" and actual and not any(is_docs(p) for p in actual):
            hits.append(("P", "前缀 docs( 声称文档，实际改动无一为文档", [], actual))
        if typ in ("test", "tests") and actual and not any(is_test(p) for p in actual):
            hits.append(("P", "前缀 test( 声称测试，实际改动无一为测试", [], actual))
        if ("/" in scope or scope.endswith((".py", ".md"))) and actual \
                and scope.split("/")[0] in TOP_DIRS:
            if all(not under(p, scope) for p in actual):
                hits.append(("S", f"scope={scope} 是路径，实际改动全在其外", [scope], actual))
        named = declared_paths(c["msg"], universe)
        missing = [t for t in named
                   if not any(p == t or p.endswith("/" + t) for p in actual)]
        if missing:
            hits.append(("D", "显式清单里的路径本笔没改", missing, actual))
        if not hits:
            continue

        verdict = []
        for twin in by_subject.get(c["subject"], []):
            if twin["sha"] != c["sha"]:
                verdict.append(
                    f"同消息另一笔 {twin['sha'][:8]}（相距 {twin['idx'] - c['idx']:+d} 笔）"
                    f"改动 {twin['paths'][:6]}"
                )
        for _kind, _desc, claimed, _act in hits:
            for token in claimed:
                lands = landings_for(token, c["sha"], c["idx"])
                if lands:
                    best = lands[0]
                    verdict.append(
                        f"声称的 {token} 在 {best['sha'][:8]} 落库"
                        f"（{best['subject'][:40]}，相距 {best['idx'] - c['idx']:+d} 笔）"
                    )
                else:
                    verdict.append(f"声称的 {token} 未找到落点 → 疑似丢件")
        for _kind, _desc, _claimed, act in hits:
            for token in act:
                near = [x for x in touching.get(token, [])
                        if x["sha"] != c["sha"] and abs(x["idx"] - c["idx"]) <= WINDOW]
                if near:
                    best = min(near, key=lambda x: abs(x["idx"] - c["idx"]))
                    verdict.append(
                        f"多出的 {token} 另有落库 {best['sha'][:8]}"
                        f"（{best['subject'][:36]}，相距 {best['idx'] - c['idx']:+d} 笔）"
                    )
                else:
                    verdict.append(f"多出的 {token} 只有本笔（无第二落点）")
        rows.append({"sha": c["sha"], "subject": c["subject"], "hits": hits,
                     "verdict": verdict, "actual": actual,
                     "comment_only": comment_only(c["sha"])})

    kinds = defaultdict(int)
    for r in rows:
        for k, *_ in r["hits"]:
            kinds[k] += 1
    real = [r for r in rows if not r["comment_only"]]
    note = [r for r in rows if r["comment_only"]]
    print(f"提交总数 {len(commits)}；错配 {len(rows)} 笔；"
          f"规则 P(前缀)/S(scope)/D(点名) = {kinds['P']}/{kinds['S']}/{kinds['D']}")
    print(f"其中仅改注释行（归类习惯，非内容错配）{len(note)} 笔；"
          f"动了代码/非注释内容 {len(real)} 笔")
    if args.quiet:
        return 0
    print()
    for r in reversed(real):          # 新→旧
        kinds_txt = ",".join(k for k, *_ in r["hits"])
        print(f"{r['sha']}\t[{kinds_txt}]\t{r['subject'][:70]}")
        for k, desc, claimed, act in r["hits"]:
            print(f"  {k}: {desc}；声称={claimed[:3]} 实际={act[:3]}")
        for line in list(dict.fromkeys(r["verdict"]))[:4]:
            print(f"  → {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
