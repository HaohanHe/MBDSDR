#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""明文凭据机检：形状规则 + 熵分档 + argv/日志 AST 规则。

输出纪律（重要）：本脚本只打印「文件:行 规则名」，**绝不打印命中的值**。
扫描器自己的输出就是新的泄露面——不要给它加 ``--show-value`` 之类的开关，
排查时按行号回编辑器看原文。

分档：
  * BLOCK（退出码 1）：已知凭据形状——GitHub / GitLab / Slack / AWS / JWT /
    PEM 私钥块。
  * WARN（默认不拦）：熵告警（≥32 字符且 Shannon ≥ 4.2，只在源码/配置类
    文本上跑、每个文件只报首条，承认会有误报）、CLI 选项接收凭据、argv 把
    凭据类变量带进子进程、日志打印凭据类变量。

豁免：命中行内必须出现 ``pragma: allowlist secret``（任何注释风格都行），
**逐行**标注；不支持目录级 / 文件级豁免——豁免范围一大，检查器就成摆设。

体积口径：形状规则（BLOCK）对**任何体积**的文件都跑（大文件流式逐行读），
熵规则才有 2MB 上限。跳过分两类、分别计数，不合并成一个数：
「大文件仅形状检查（不做熵）」与「二进制/媒体不扫（不做文本扫描）」。

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

#: 熵规则只跑源码 / 配置类文本。构建产物、数据 dump、日志里天然全是哈希与
#: 随机串：2026-11-13 全仓实测 2 382 625 条 WARN，真正的告警会被完全淹掉。
#: 只收紧熵规则，形状规则（BLOCK）仍然对全量文件生效。
_ENTROPY_SUFFIXES = {
    ".py", ".pyw", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".md", ".rst", ".txt", ".c", ".h", ".hpp", ".cpp", ".cc", ".cs",
    ".java", ".kt", ".rb", ".php", ".go", ".rs", ".swift", ".m", ".mm",
    ".sql", ".html", ".css", ".scss", ".xml", ".gradle", ".spec", ".env",
}
_ENTROPY_NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt"}

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
    "build-local": "构建产物",
    "CMakeFiles": "构建产物",
    "_meteor_build": "构建产物",
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
#: 熵规则的体积上限。只限制熵——大文件里熵告警没有意义；形状规则是廉价
#: 正则，**任何体积都跑**（3MB 的 CSV 里写一枚 token 也必须被拦）。
_MAX_ENTROPY_BYTES = 2 * 1024 * 1024
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


def _iter_files(root: Path, stats: "Counter[str]") -> Iterator[Path]:
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
                # 二进制/媒体：不做文本扫描。与「大文件只跑形状」分开计数，
                # 两类跳过不能合成一个数。
                stats["skip_binary"] += 1
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


def _is_subprocess_call(func: ast.AST) -> bool:
    """只认 ``subprocess.run/Popen/...``。

    按方法名匹配会打到 ``registry.call(...)`` 这类同名方法上（全仓实测 2 条
    误报），所以要求接收者链的根是 ``subprocess``。
    """
    if not isinstance(func, ast.Attribute) or func.attr not in _SUBPROCESS_FUNCS:
        return False
    node = func.value
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "subprocess"


def _scan_ast(path: str, text: str, lines: List[str]) -> List[Finding]:
    # 只对 Python 源码做 AST：对 .sh/.txt 调 ast.parse 只会刷一屏 SyntaxWarning，
    # 还白等解析时间。
    if Path(path).suffix.lower() not in {".py", ".pyw", ".pyi"}:
        return []
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

        if _is_subprocess_call(func):
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
    name = Path(path).name
    entropy_ok = Path(path).suffix.lower() in _ENTROPY_SUFFIXES or name in _ENTROPY_NAMES
    hits: List[Finding] = []
    entropy_reported = False
    for lineno, line in enumerate(lines, 1):
        if SPDX in line:
            continue
        shaped = False
        for rule, rx in SHAPE_RULES:
            if rx.search(line):
                hits.append((path, lineno, rule, "block"))
                shaped = True
        if shaped or not entropy_ok or entropy_reported:
            continue
        for match in _CANDIDATE.finditer(line):
            if _shannon(match.group(0)) >= ENTROPY_MIN:
                hits.append((path, lineno, "high-entropy-string", "warn"))
                entropy_reported = True
                break
    hits.extend(_scan_ast(path, text, lines))
    return hits


def _decode(blob: bytes) -> Optional[str]:
    if b"\0" in blob[:8192]:
        return None
    return blob.decode("utf-8", errors="replace")


def _shape_hits(path: str, lines: Iterable[str]) -> List[Finding]:
    """只跑形状规则（BLOCK 档）。大文件走这里：熵不做，形状照跑。"""
    hits: List[Finding] = []
    for lineno, line in enumerate(lines, 1):
        if SPDX in line:
            continue
        for rule, rx in SHAPE_RULES:
            if rx.search(line):
                hits.append((path, lineno, rule, "block"))
    return hits


def _scan_large(path: Path, display: str, stats: "Counter[str]") -> List[Finding]:
    """超过熵上限的文件：流式逐行只跑形状规则，不整块读进内存。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
            if b"\0" in head:
                # 二进制没有「行」的概念，放到与媒体文件同一类里计数。
                stats["skip_binary"] += 1
                return []
            handle.seek(0)
            lines = (raw.decode("utf-8", errors="ignore") for raw in handle)
            hits = _shape_hits(display, lines)
    except OSError:
        return []
    stats["shape_only_large"] += 1
    return hits


def _scan_path(
    path: Path, display: Optional[str] = None, stats: Optional["Counter[str]"] = None
) -> List[Finding]:
    stats = stats if stats is not None else Counter()
    try:
        size = path.stat().st_size
    except OSError:
        return []
    name = display or str(path)
    if size > _MAX_ENTROPY_BYTES:
        return _scan_large(path, name, stats)
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    text = _decode(blob)
    if text is None:
        stats["skip_binary"] += 1
        return []
    return scan_text(name, text)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _scan_staged(stats: "Counter[str]") -> List[Finding]:
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
            stats["skip_binary"] += 1
            continue
        if len(blob) > _MAX_ENTROPY_BYTES:
            # 暂存区里的大文件同样只做形状检查（熵规则有体积上限）。
            stats["shape_only_large"] += 1
            findings.extend(_shape_hits(name, text.splitlines()))
            continue
        findings.extend(scan_text(name, text))
    return findings


def _report(findings: Iterable[Finding], stats: Optional["Counter[str]"] = None) -> int:
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
    if stats is not None:
        print(
            "[secret-scan] 覆盖："
            f"大文件仅形状检查 {stats['shape_only_large']} 个"
            f"（>{_MAX_ENTROPY_BYTES // (1024 * 1024)}MB，不做熵检查）"
            f"；二进制/媒体跳过 {stats['skip_binary']} 个（不做文本扫描）"
        )
    print("[secret-scan] 输出只含文件:行 + 规则名，不含命中的值。")
    return 1 if blocks else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="明文凭据机检（只报位置，不打印值）")
    parser.add_argument("paths", nargs="*", help="只扫指定文件/目录（默认扫扫描根）")
    parser.add_argument("--root", default=os.environ.get("MBDSDR_SECRET_SCAN_ROOT") or r"D:\mbdsdr")
    parser.add_argument("--staged", action="store_true", help="只扫 git 暂存区内容")
    args = parser.parse_args(argv)

    stats: "Counter[str]" = Counter()
    if args.staged:
        return _report(_scan_staged(stats), stats)

    targets = [Path(item) for item in args.paths] if args.paths else [Path(args.root)]
    findings: List[Finding] = []
    for target in targets:
        if target.is_dir():
            for path in _iter_files(target, stats):
                findings.extend(_scan_path(path, stats=stats))
        elif target.exists():
            findings.extend(_scan_path(target, stats=stats))
        else:
            print(f"SKIP {target} — 路径不存在")
    return _report(findings, stats)


if __name__ == "__main__":
    sys.exit(main())
