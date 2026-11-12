#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""明文凭据机检：形状规则 + 熵分档 + argv/日志 AST 规则。

输出纪律（重要）：本脚本只打印「文件:行 规则名」，**绝不打印命中的值**。
扫描器自己的输出就是新的泄露面——不要给它加 ``--show-value`` 之类的开关，
排查时按行号回编辑器看原文。

分档：
  * BLOCK（退出码 1）：已知凭据形状——GitHub / GitLab / Slack / AWS / JWT /
    PEM 私钥块。
  * WARN（默认不拦）：熵告警（≥32 字符且 Shannon ≥ 4.2，承认会有误报）、
    CLI 选项接收凭据、argv 把凭据类变量带进子进程、日志打印凭据类变量。

豁免：命中行内必须出现 ``pragma: allowlist secret``（任何注释风格都行），
**逐行**标注；不支持目录级 / 文件级豁免——豁免范围一大，检查器就成摆设。

用法::

    python scripts/secret_scan.py                  # 扫 D:\\mbdsdr（跳过目录逐条打印）
    python scripts/secret_scan.py --staged         # 只扫暂存区内容（pre-commit 用）
    python scripts/secret_scan.py --root D:\\some   # 指定扫描根
    python scripts/secret_scan.py path1 path2      # 只扫指定文件

跳过目录写在 ``SKIP_DIRS`` 里并逐条打印原因，不静默少扫。
"""
from __future__ import annotations

import argparse
import ast
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

SPDX = "pragma: allowlist secret"

#: 形状规则：名字只用于报告，不含凭据值。
SHAPE_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("github-pat-classic", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("github-pat-fine-grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab-pat", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

#: 熵候选：不跨空格/引号，避免把整行代码当随机串。
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
ENTROPY_MIN = 4.2

#: argv / 日志规则用到的标识符形状。
_CRED_NAME = re.compile(
    r"(?i)(token|passwd|password|secret|api[_-]?key|access[_-]?key|private[_-]?key)"
)
_CRED_KEY_LITERAL = re.compile(
    r"(?i)^(api[_-]?key|access[_-]?key|private[_-]?key|token|passwd|password|secret)"
    r"[A-Za-z0-9_.-]*$"
)
_CLI_OPTIONS = {
    "--token",
    "--password",
    "--passwd",
    "--api-key",
    "--secret",
    "--access-key",
    "--private-key",
}
_SUBPROCESS_FUNCS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
}
_LOG_FUNCS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}

#: 跳过的目录 → 原因。逐条打印，避免「静默少扫」。
SKIP_DIRS = {
    ".git": "版本库元数据",
    ".tmp": "临时/草稿目录（本轮不扫，需单独巡检）",
    "node_modules": "第三方依赖",
    "__pycache__": "字节码缓存",
    ".venv": "虚拟环境",
    "venv": "虚拟环境",
    ".dart_tool": "Dart 构建缓存",
    ".pytest_cache": "测试缓存",
    "build": "构建产物",
    "dist": "构建产物",
    "reference": "第三方上游源码（gpredict）",
    "deepseek-harness-master": "第三方工具源码副本",
    ".qoder": "生成的仓 wiki（占位文本）",
    ".wt-satpass": "工作树副本",
    "_wt": "工作树副本",
    "_headcheck": "工作树副本",
    "MBDSDR-wt-e7c1": "工作树副本",
}

_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".db",
    ".sqlite",
    ".wav",
    ".mp3",
    ".mp4",
    ".avi",
    ".mkv",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
}
_MAX_BYTES = 2 * 1024 * 1024
_MAX_WARN_PRINT = 200

Finding = Tuple[str, int, str, str]  # (path, line, rule, tier)


def _shannon(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _skip_reason(name: str, full: Path) -> Optional[str]:
    if name in SKIP_DIRS:
        return SKIP_DIRS[name]
    if "output" in full.parts and name.startswith("wt-"):
        return "output 下的工作树副本"
    if "output" in full.parts and name in {"backend-opt", "readme-opt"}:
        return "output 下的工作树副本"
    return None


def _iter_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        kept: List[str] = []
        for name in dirnames:
            full = Path(dirpath) / name
            reason = _skip_reason(name, full)
            if reason:
                print(f"SKIP {full} — {reason}")
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() in _BINARY_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_BYTES:
                    print(f"SKIP {path} — 文件超过 {_MAX_BYTES // (1024 * 1024)}MB")
                    continue
            except OSError:
                continue
            yield path


def _cred_refs(node: ast.AST) -> bool:
    """节点内是否引用了凭据类标识符 / 键名。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and _CRED_NAME.search(sub.id):
            return True
        if isinstance(sub, ast.Attribute) and _CRED_NAME.search(sub.attr):
            return True
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and _CRED_KEY_LITERAL.match(sub.value)
        ):
            return True
    return False


def _scan_ast(path: str, text: str, lines: List[str]) -> List[Finding]:
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return []

    hits: List[Finding] = []

    def allowed(lineno: int) -> bool:
        return 0 < lineno <= len(lines) and SPDX in lines[lineno - 1]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        lineno = getattr(node, "lineno", 0)

        if isinstance(func, ast.Attribute) and func.attr == "add_argument":
            # 选项名是字面量（"--token"），`_cred_refs` 的键名规则不接受前导 -，
            # 所以这里对字面量去横线后再比对选项集合；变量名/键名仍走 _cred_refs。
            options = {
                sub.value.lstrip("-").lower()
                for sub in ast.walk(node)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            }
            cred_option = bool(options & {opt.lstrip("-") for opt in _CLI_OPTIONS})
            if (cred_option or _cred_refs(node)) and not allowed(lineno):
                hits.append((path, lineno, "argv-cli-credential-option", "warn"))
            continue

        if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
            segment = ast.get_source_segment(text, node) or ""
            shaped = any(rx.search(segment) for _, rx in SHAPE_RULES)
            has_cred = _cred_refs(node)
            if (shaped or has_cred) and not allowed(lineno):
                hits.append((path, lineno, "argv-credential-in-command", "warn"))
            continue

        is_log = (isinstance(func, ast.Name) and func.id == "print") or (
            isinstance(func, ast.Attribute) and func.attr in _LOG_FUNCS
        )
        if is_log:
            segment = ast.get_source_segment(text, node) or ""
            if "redact" in segment.lower():
                continue
            if _cred_refs(node) and not allowed(lineno):
                hits.append((path, lineno, "log-credential-value", "warn"))

    return hits


def scan_text(path: str, text: str) -> List[Finding]:
    lines = text.splitlines()
    hits: List[Finding] = []
    for lineno, line in enumerate(lines, 1):
        if SPDX in line:
            continue
        shaped = False
        for rule, rx in SHAPE_RULES:
            if rx.search(line):
                hits.append((path, lineno, rule, "block"))
                shaped = True
        if shaped:
            continue
        for match in _CANDIDATE.finditer(line):
            if _shannon(match.group(0)) >= ENTROPY_MIN:
                hits.append((path, lineno, "high-entropy-string", "warn"))
                break
    hits.extend(_scan_ast(path, text, lines))
    return hits


def _decode(blob: bytes) -> Optional[str]:
    if b"\0" in blob[:8192]:
        return None
    return blob.decode("utf-8", errors="replace")


def _scan_path(path: Path, display: Optional[str] = None) -> List[Finding]:
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    text = _decode(blob)
    if text is None:
        return []
    return scan_text(display or str(path), text)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _scan_staged() -> List[Finding]:
    proc = _git("diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMR")
    if proc.returncode != 0:
        print(f"[secret-scan] 读取暂存区失败：{proc.stderr.strip()}")
        return []
    findings: List[Finding] = []
    for name in [line for line in proc.stdout.splitlines() if line.strip()]:
        blob = subprocess.run(
            ["git", "show", f":{name}"], capture_output=True
        ).stdout
        text = _decode(blob)
        if text is None:
            continue
        findings.extend(scan_text(name, text))
    return findings


def _report(findings: Iterable[Finding]) -> int:
    unique = sorted(set(findings), key=lambda item: (item[0], item[1], item[2]))
    blocks = [f for f in unique if f[3] == "block"]
    warns = [f for f in unique if f[3] == "warn"]

    for path, lineno, rule, _tier in blocks:
        print(f"BLOCK {path}:{lineno} {rule}")
    for path, lineno, rule, _tier in warns[:_MAX_WARN_PRINT]:
        print(f"WARN  {path}:{lineno} {rule}")
    if len(warns) > _MAX_WARN_PRINT:
        print(f"WARN  其余 {len(warns) - _MAX_WARN_PRINT} 条省略（计数含全部）")

    by_rule = Counter(rule for _p, _l, rule, _t in unique)
    print(f"[secret-scan] BLOCK={len(blocks)} WARN={len(warns)}")
    print("[secret-scan] 按规则：" + ("，".join(f"{k}={v}" for k, v in sorted(by_rule.items())) or "无"))
    print("[secret-scan] 输出只含文件:行 + 规则名，不含命中的值。")
    return 1 if blocks else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="明文凭据机检（只报位置，不打印值）")
    parser.add_argument("paths", nargs="*", help="只扫指定文件/目录（默认扫扫描根）")
    parser.add_argument("--root", default=os.environ.get("MBDSDR_SECRET_SCAN_ROOT") or r"D:\mbdsdr")
    parser.add_argument("--staged", action="store_true", help="只扫 git 暂存区内容")
    args = parser.parse_args(argv)

    if args.staged:
        return _report(_scan_staged())

    targets = [Path(item) for item in args.paths] if args.paths else [Path(args.root)]
    findings: List[Finding] = []
    for target in targets:
        if target.is_dir():
            for path in _iter_files(target):
                findings.extend(_scan_path(path))
        elif target.exists():
            findings.extend(_scan_path(target))
        else:
            print(f"SKIP {target} — 路径不存在")
    return _report(findings)


if __name__ == "__main__":
    sys.exit(main())
