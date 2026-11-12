#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""横幅被覆盖机检：HEAD 有「本文件…未验证」横幅、工作区同一文件没有 → 报丢件。

背景：文件级 E4 横幅（见 ``docs/evidence-standard.md``）是整份文档的口径声明。
并行进程按自己的旧快照回写文件时，会把别人刚提交的横幅**静默删掉**——
写得进 HEAD、扫描器也绿，但工作区里横幅已经没了，下一次提交就把丢件固化。

判据（与 ``scripts/audit_evidence.py`` 的 ``FILE_BANNER_RE`` 同一份正则，
不另存副本；改横幅写法只改那一处）：

1. 取 HEAD 里该文件前 80 行：命中 ``FILE_BANNER_RE`` = HEAD 带横幅；
2. 取工作区同一文件前 80 行：未命中 = 工作区没横幅；
3. 两者同时成立 → 报「横幅被覆盖」。文件在工作区被删除也算覆盖。

反证（必须能过）：
* 删掉某文件里唯一一条横幅 → 必须报该文件；
* 从 HEAD 恢复 → 不再报。

用法::

    python scripts/check_banner_coverage.py                # 默认范围（同证据闸门）
    python scripts/check_banner_coverage.py --paths docs/a.md
    python scripts/check_banner_coverage.py --json

退出码：0 = 无覆盖；1 = 有覆盖；2 = 环境错误。不修改任何文件，只报事实。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_evidence as ae  # noqa: E402 - 复用同一份 FILE_BANNER_RE / _files

ROOT = ae.ROOT
#: 横幅只认文件开头：与 audit_evidence.run() 的判据一致。
BANNER_SCAN_LINES = 80


def _has_banner(text: str) -> bool:
    head = "\n".join(text.splitlines()[:BANNER_SCAN_LINES])
    return bool(ae.FILE_BANNER_RE.search(head))


def _head_text(rel: str) -> Optional[str]:
    """HEAD 版本的文件内容；未跟踪/路径不存在返回 None。"""
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else None


def check(paths: Optional[List[str]] = None) -> Dict:
    covered: List[Dict[str, str]] = []
    checked = 0
    skipped_untracked = 0
    for f in ae._files(paths):
        rel = (str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f))
        rel = rel.replace(os.sep, "/")
        head = _head_text(rel)
        if head is None:
            skipped_untracked += 1
            continue
        if not _has_banner(head):
            continue
        checked += 1
        if not f.is_file():
            covered.append({"path": rel, "reason": "工作区缺失（HEAD 带横幅）"})
            continue
        try:
            work = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            covered.append({"path": rel, "reason": f"工作区不可读（{exc.__class__.__name__}）"})
            continue
        if not _has_banner(work):
            covered.append({"path": rel, "reason": "工作区横幅被覆盖（HEAD 带横幅）"})
    return {"checked": checked, "skipped_untracked": skipped_untracked,
            "covered": covered}


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(
        description="横幅被覆盖机检（判据：docs/evidence-standard.md 的文件级 E4 横幅）")
    ap.add_argument("--paths", nargs="*", default=None,
                    help="只查指定文件（默认与证据闸门同范围）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    rep = check(args.paths)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"检查 {rep['checked']} 个 HEAD 带横幅的文件"
              f"（跳过未跟踪 {rep['skipped_untracked']} 个）")
        for item in rep["covered"]:
            print(f"  - {item['path']}：{item['reason']}")
        print("结论：" + ("FAIL（横幅被覆盖，见上方清单）"
                         if rep["covered"] else "PASS（无横幅丢失）"))
    return 1 if rep["covered"] else 0


if __name__ == "__main__":
    sys.exit(main())
