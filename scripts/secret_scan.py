#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""明文凭据机检：形状规则 + 熵分档 + argv/日志 AST 规则。

输出纪律（重要）：本脚本只打印「文件:行 规则名」，**绝不打印命中的值**。
扫描器自己的输出就是新的泄露面——不要给它加 ``--show-value`` 之类的开关，
排查时按行号回编辑器看原文。

分档：
  * BLOCK（退出码 1）：已知凭据形状——GitHub / GitLab / Slack / AWS / JWT /
    PEM 私钥块。
  * MUST（必须看，默认不拦）：熵告警里**结构性**可疑的那一小撮——凭据文件
    （`.env` / `.pem|key|...` / `secrets.yaml` 这类整名就是凭据面的文件），或
    高熵串紧邻 `api_key=` / `"token": "` 这类字段名。判据只看位置与字段名，
    **不看分数**（调阈值会把真命中一起调没）。
  * SAMPLE（抽样看，默认不拦）：其余熵告警（普通源码/文档里的长串），每个
    文件只报首条、终端只打 20 条代表，全量分布用 `--entropy-stats`。
  * WARN（默认不拦）：CLI 选项接收凭据、argv 把凭据类变量带进子进程、
    日志打印凭据类变量。

豁免：命中行内必须出现 ``pragma: allowlist secret``（任何注释风格都行），
**逐行**标注；不支持目录级 / 文件级豁免——豁免范围一大，检查器就成摆设。

体积口径：形状规则（BLOCK）对**任何体积**的文件都跑（大文件流式逐行读），
熵规则才有 2MB 上限。跳过分两类、分别计数，不合并成一个数：
「大文件仅形状检查（不做熵）」与「二进制/媒体不扫（不做文本扫描）」。

用法::

    python scripts/secret_scan.py                  # 扫 D:\\mbdsdr（跳过目录逐条打印）
    python scripts/secret_scan.py --staged         # 只扫暂存区内容（pre-commit 用）
    python scripts/secret_scan.py --history        # 扫 git 历史里的 blob + 提交信息（只跑形状规则）
    python scripts/secret_scan.py --artifact DIR   # 扫待发布目录（含二进制），BLOCK>0 退出 1
    python scripts/secret_scan.py --commit-msg F   # 扫提交信息文件（commit-msg 钩子用），BLOCK>0 退出 1
    python scripts/secret_scan.py --entropy-stats  # 熵告警分层分布（只量不改判）
    python scripts/secret_scan.py --root D:\\some   # 指定扫描根
    python scripts/secret_scan.py path1 path2      # 只扫指定文件

历史口径（``--history``）：工作树 0 BLOCK 只能证明「现在没有」，不能证明「从没
提交过」。历史模式对 ``git rev-list --all`` 可达的每个 blob 跑同一组形状规则
（不跑熵：全量历史里熵告警没有信号），报告「命中 blob / 路径 / 提交」三个数，
并逐个提交用 ``git merge-base --is-ancestor <commit> HEAD`` 判断是否仍可从 HEAD
到达（不靠 ``git log`` 的主观印象）。同样只打印路径、行号、规则名、提交号。
非 blob 对象、首 8KB 含 NUL 的二进制 blob、以及 ``--all`` 之外（dangling /
仅 reflog 可达）的对象不在扫描范围内，逐类计数后打印。

提交信息口径（2026-11-13 补的通道）：``git commit -m "ghp_…"`` 不产生 blob，
上面的 blob 扫描**看不见**它；annotated tag 的 message 与 ``git notes`` 同理。
所以 ``--history`` 同时扫三类对象：
  * commit message：``git log --all --reflog <worktree HEAD> --format=%H%x00%B``，
    一条提交一条，行号按消息正文计；
  * annotated tag message：``refs/tags`` 里 ``objecttype=tag`` 的对象，
    ``git cat-file tag <sha>`` 取头部之后的正文（轻量 tag 没有独立 message，只计数）；
  * notes：``refs/notes/*`` 每个 ref 跑 ``git notes list`` 再 ``cat-file -p``。
命中打印 ``HISTORY-MSG <提交号|tag:名称|note:对象>：<行> <规则>``，仍然不打印值。
提交信息写进 commit/tag 对象后，改它必须重写历史——所以这条通道有独立的
写入闸（``scripts/hooks/commit-msg`` → ``--commit-msg``），见 docs/code-guards.md。

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
import threading
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

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

#: 字节流版本：发布产物里有 exe/dll/pyc，凭据字符串可能被嵌在二进制里，
#: 产物门禁因此不按文本扫，直接对字节跑同一组形状规则（同一真值源）。
SHAPE_BYTES: Tuple[Tuple[str, "re.Pattern[bytes]"], ...] = tuple(
    (name, re.compile(pattern.pattern.encode())) for name, pattern in SHAPE_RULES
)

#: 历史扫描的「已知良性」登记：**完整 blob sha → 规则名**。
#: 命中条件严格：sha 完全一致、规则名一致、且该 blob 没有任何提交引用它
#: （不可达）。可达 blob 命中同一规则、或命中另一个 sha，一律照旧报 HISTORY。
#: 登记、打码指纹与解除条件在 docs/code-guards.md（真值源），这里只放判据。
KNOWN_BENIGN_HISTORY: Dict[str, str] = {
    "c20bd082d6382e0d498f029151e10e305a39ef12": "github-pat-classic",
}

#: 熵候选：不跨空格/引号，避免把整行代码当随机串。
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
ENTROPY_MIN = 4.2

#: 熵告警分级（2026-11-13 降噪）：**结构性判据，不动分数阈值**。
#:
#: 「必须看」（must）——三选一：
#:   ① 文件本身就是凭据面：`.env` / `.env.*` / `*.pem|key|pfx|p12|keystore|jks`，
#:      或文件名里带 secret / credential / token / passwd / password；
#:   ② 高熵串**紧邻**凭据字段名（`api_key=`、`"token": "` 这类，字段名与值之间
#:      只允许 `: = " '` 与空白）——这是「值写在配置里」的典型形状；
#:   ③ 其余一律「抽样看」（sample）：普通源码/文档里的长串只记一条，不逐条看。
#: 判据只看位置与字段名，不看分数：调阈值能把真命中一起调没，结构判据不会。
_CRED_NEAR = re.compile(
    r"(?i)(token|passwd|password|secret|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?$"
)
#: 文件名判据只认「整名就是凭据文件」，不做子串匹配——`test_secret_scan.py`、
#: `2026-07-29-...-credentials.i18n.yaml` 这类名字里带 secret/token 的文档与测试
#: 不是凭据面，按子串判会把「必须看」重新灌满。
_SECRET_FILE_RE = re.compile(
    r"(?i)^(\.netrc|\.npmrc|\.pypirc|\.pgpass|\.htpasswd"
    r"|id_(rsa|dsa|ecdsa|ed25519)"
    r"|(secrets?|credentials?|tokens?|passwords?)(\.(json|ya?ml|toml|ini|cfg|txt|env))?)$"
)
_SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"}
_ENV_NAME_RE = re.compile(r"(?i)^\.?env(\..+)?$")

#: 分级统计用的分数区间（只用于「先量」的分层，不参与判定）。
_SCORE_BUCKETS = ((4.2, 4.5), (4.5, 5.0), (5.0, 5.5), (5.5, 6.0))

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
#: 「抽样看」就是抽样：只打一小撮代表，其余按计数省略（全量在 --entropy-stats）。
_MAX_SAMPLE_PRINT = 20

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


def _entropy_tier(path: str, line: str, start: int) -> str:
    """高熵串落在「必须看」还是「抽样看」。只读位置/文件名/字段名，不读分数。"""
    name = Path(path).name
    suffix = Path(path).suffix.lower()
    if (
        _ENV_NAME_RE.match(name)
        or suffix in _SECRET_SUFFIXES
        or _SECRET_FILE_RE.match(name)
    ):
        return "must"
    # 只看候选串前面 48 字符：字段名必须紧邻值（允许 : = 引号 与空白）。
    if _CRED_NEAR.search(line[max(0, start - 48):start]):
        return "must"
    return "sample"


def _entropy_candidates(line: str) -> Iterator[Tuple[int, str, float]]:
    """一行里第一个过阈值的高熵串：(列号, 候选串, 分数)。每行只取第一个。"""
    for match in _CANDIDATE.finditer(line):
        score = _shannon(match.group(0))
        if score >= ENTROPY_MIN:
            yield match.start(), match.group(0), score
            return


def scan_text(path: str, text: str) -> List[Finding]:
    lines = text.splitlines()
    name = Path(path).name
    # 坑：`Path(".env").suffix == ""`，只按 suffix 判会让 `.env` 永远不进熵检查
    # （`.env` 曾写在 _ENTROPY_SUFFIXES 里却从未生效）。凭据面必须显式认名字。
    entropy_ok = (
        Path(path).suffix.lower() in _ENTROPY_SUFFIXES
        or name in _ENTROPY_NAMES
        or bool(_ENV_NAME_RE.match(name))
    )
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
        for start, _value, _score in _entropy_candidates(line):
            hits.append((path, lineno, "high-entropy-string", _entropy_tier(path, line, start)))
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


def _read_exact(stream, size: int) -> bytes:
    chunks: List[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _history_tips() -> List[str]:
    """所有 worktree 的 HEAD。

    链接工作树处于 detached HEAD 时，该提交不在任何 ref 下，``--all`` 看不见
    它（``.git/worktrees/<name>/HEAD`` 不是 ref）。巡检历史时把它补进去。
    """
    proc = _git("worktree", "list", "--porcelain")
    tips: List[str] = []
    for raw in proc.stdout.splitlines():
        if raw.startswith("HEAD "):
            sha = raw[5:].strip()
            if sha and sha not in tips:
                tips.append(sha)
    return tips


def _all_blob_shas() -> List[str]:
    """对象库里的全部 blob（含 --all/reflog 之外的对象），不只是可达的那部分。"""
    proc = _git(
        "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"
    )
    if proc.returncode != 0:
        print(f"[secret-scan] 读取对象库失败：{proc.stderr.strip()}")
        return []
    return [
        fields[0]
        for fields in (line.split() for line in proc.stdout.splitlines())
        if len(fields) == 2 and fields[1] == "blob"
    ]


def _history_blob_paths(tips: List[str]) -> Dict[str, List[str]]:
    """``git rev-list --objects``：blob → 曾用过的路径（可能多个）。"""
    proc = _git(
        "-c", "core.quotepath=false", "rev-list", "--objects", "--all", "--reflog", *tips
    )
    if proc.returncode != 0:
        print(f"[secret-scan] 读取历史对象失败：{proc.stderr.strip()}")
        return {}
    out: Dict[str, List[str]] = {}
    for raw in proc.stdout.splitlines():
        sha, sep, path = raw.partition(" ")
        if not sep or not path.strip():
            continue
        out.setdefault(sha, []).append(path.strip())
    return out


def _history_blob_commits(tips: List[str]) -> Dict[str, List[str]]:
    """``git log --raw`` 的「新增态 blob」→ 引入它的提交。

    一次 git 调用建反向索引，避免对每个命中 blob 再跑一次 ``git log
    --find-object``（命中一多就是几百次进程启动）。
    """
    proc = _git(
        "-c", "core.quotepath=false", "log", "--all", "--reflog", *tips, "--raw",
        "--no-abbrev", "--root", "--format=C %H",
    )
    if proc.returncode != 0:
        print(f"[secret-scan] 读取历史提交失败：{proc.stderr.strip()}")
        return {}
    out: Dict[str, List[str]] = {}
    commit = ""
    for raw in proc.stdout.splitlines():
        if raw.startswith("C "):
            commit = raw[2:].strip()
            continue
        if not raw.startswith(":") or not commit:
            continue
        fields = raw.split("\t")
        meta = fields[0].split()
        if len(fields) < 2 or len(meta) < 5:
            continue
        new_sha = meta[3]
        if set(new_sha) == {"0"}:
            continue
        out.setdefault(new_sha, []).append(commit)
    return out


def _scan_history_blobs(
    shas: List[str], stats: "Counter[str]"
) -> Dict[str, List[Tuple[int, str]]]:
    """流式读 ``git cat-file --batch``：blob → [(行号, 规则)]，只跑形状规则。"""
    hits: Dict[str, List[Tuple[int, str]]] = {}
    if not shas:
        return hits
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    payload = ("\n".join(shas) + "\n").encode("ascii")

    def _feed() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(payload)
            proc.stdin.close()
        except OSError:
            pass

    threading.Thread(target=_feed, daemon=True).start()
    stream = proc.stdout
    assert stream is not None
    while True:
        header = stream.readline()
        if not header:
            break
        fields = header.split()
        if len(fields) < 3:
            # "<sha> missing" 之类：没有内容段，不需要对齐
            stats["history_skip_missing"] += 1
            continue
        sha = fields[0].decode("ascii", errors="replace")
        otype = fields[1]
        size = int(fields[2])
        if otype != b"blob":
            _read_exact(stream, size)
            stream.read(1)
            stats["history_skip_non_blob"] += 1
            continue
        blob = _read_exact(stream, size)
        stream.read(1)
        stats["history_blobs"] += 1
        stats["history_bytes"] += len(blob)
        if b"\0" in blob[:8192]:
            stats["history_skip_binary"] += 1
            continue
        text = blob.decode("utf-8", errors="ignore")
        blob_hits = [
            (lineno, rule) for _p, lineno, rule, _t in _shape_hits("", text.splitlines())
        ]
        if blob_hits:
            hits[sha] = blob_hits
    proc.wait()
    return hits


def _head_blob_map() -> Dict[str, str]:
    proc = _git("-c", "core.quotepath=false", "ls-tree", "-r", "HEAD")
    out: Dict[str, str] = {}
    if proc.returncode != 0:
        return out
    for raw in proc.stdout.splitlines():
        meta, _sep, path = raw.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and fields[1] == "blob":
            out[path] = fields[2]
    return out


def _ancestor_of_head(commit: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], capture_output=True
    )
    return proc.returncode == 0


#: ``git log --format`` 的记录分隔符（``%x1e``）。提交信息里不会出现 NUL/0x1e，
#: 用它切记录比按行切安全（``%B`` 本身是多行）。
_MSG_SEP = "\x1e"


def _shape_line_hits(lines: Iterable[str]) -> List[Tuple[int, str]]:
    """只要 (行号, 规则)，路径由调用方补。``pragma: allowlist secret`` 逐行生效。"""
    return [
        (lineno, rule)
        for _path, lineno, rule, _tier in _shape_hits("", lines)
    ]


def _history_commit_messages(
    tips: List[str], stats: "Counter[str]"
) -> List[Tuple[str, int, str]]:
    """commit message → [(提交号, 行号, 规则)]。

    ``git commit -m "ghp_…"`` 不产生 blob，blob 扫描完全看不见它；这里对
    ``--all`` + reflog + 各 worktree HEAD 覆盖到的提交逐个跑形状规则。
    """
    proc = _git(
        "-c", "core.quotepath=false", "log", "--all", "--reflog", *tips,
        "--format=%x1e%H%x00%B",
    )
    if proc.returncode != 0:
        print(f"[secret-scan] 读取提交信息失败：{proc.stderr.strip()}")
        return []
    hits: List[Tuple[str, int, str]] = []
    seen: set = set()
    for record in proc.stdout.split(_MSG_SEP):
        record = record.lstrip("\r\n")
        if not record:
            continue
        sha, sep, body = record.partition("\x00")
        sha = sha.strip()
        if not sep or not sha or sha in seen:
            continue
        seen.add(sha)
        stats["history_msgs"] += 1
        body_lines = body.splitlines()
        stats["history_msg_lines"] += len(body_lines)
        for lineno, rule in _shape_line_hits(body_lines):
            hits.append((sha, lineno, rule))
    return hits


def _history_tag_messages(stats: "Counter[str]") -> List[Tuple[str, int, str]]:
    """annotated tag 的 message → [(tag 名, 行号, 规则)]。

    轻量 tag 指向 commit、没有独立 message（正文就是被指向提交的 message，
    已在 commit message 里扫过），只计数不重复扫。
    """
    proc = _git(
        "-c", "core.quotepath=false", "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(objecttype)", "refs/tags",
    )
    if proc.returncode != 0:
        print(f"[secret-scan] 读取 tag 失败：{proc.stderr.strip()}")
        return []
    hits: List[Tuple[str, int, str]] = []
    for raw in proc.stdout.splitlines():
        fields = raw.split("\x00")
        if len(fields) != 3:
            continue
        ref, sha, otype = fields
        if otype != "tag":
            stats["history_skip_lightweight_tag"] += 1
            continue
        stats["history_tags"] += 1
        obj = subprocess.run(
            ["git", "cat-file", "tag", sha], capture_output=True
        )
        if obj.returncode != 0:
            stats["history_skip_missing"] += 1
            continue
        _headers, sep, body = obj.stdout.decode(
            "utf-8", errors="replace"
        ).partition("\n\n")
        if not sep:
            continue
        body_lines = body.splitlines()
        stats["history_tag_lines"] += len(body_lines)
        name = ref[len("refs/tags/"):] if ref.startswith("refs/tags/") else ref
        for lineno, rule in _shape_line_hits(body_lines):
            hits.append((name, lineno, rule))
    return hits


def _history_note_messages(stats: "Counter[str]") -> List[Tuple[str, int, str]]:
    """``git notes`` 的正文 → [(被注释对象, 行号, 规则)]。

    ``notes`` 是独立 blob，且默认不在 ``rev-list --objects`` 的路径索引里
    （ref 名前缀 ``refs/notes/`` 下没有 tree），blob 扫描可能命中却报不出路径。
    这里按 ref 逐个 ``notes list`` 再读正文。
    """
    refs = _git("for-each-ref", "--format=%(refname)", "refs/notes")
    hits: List[Tuple[str, int, str]] = []
    for ref in [line.strip() for line in refs.stdout.splitlines() if line.strip()]:
        listing = _git("notes", f"--ref={ref}", "list")
        if listing.returncode != 0:
            print(f"[secret-scan] 读取 notes 失败（{ref}）：{listing.stderr.strip()}")
            continue
        for raw in listing.stdout.splitlines():
            fields = raw.split()
            if len(fields) < 2:
                continue
            note_blob, target = fields[0], fields[1]
            stats["history_notes"] += 1
            obj = subprocess.run(
                ["git", "cat-file", "-p", note_blob], capture_output=True
            )
            if obj.returncode != 0:
                stats["history_skip_missing"] += 1
                continue
            for lineno, rule in _shape_line_hits(
                obj.stdout.decode("utf-8", errors="replace").splitlines()
            ):
                hits.append((target, lineno, rule))
    return hits


def _split_known_benign(
    sha: str, rules: Iterable[str], commits: List[str]
) -> Tuple[List[str], List[str]]:
    """返回 (登记为良性的规则, 仍要报的规则)。

    只有三个条件同时成立才放行：sha 完全一致、规则名一致、**没有任何提交
    引用它**（`commits` 为空 = 不可达）。可达 blob 一律照旧报——这是登记的
    解除条件在代码里的落点。
    """
    registered = KNOWN_BENIGN_HISTORY.get(sha)
    benign: List[str] = []
    still: List[str] = []
    for rule in rules:
        if registered == rule and not commits:
            benign.append(rule)
        else:
            still.append(rule)
    return benign, still


def _history_report(
    stats: "Counter[str]",
    hits: Dict[str, List[Tuple[int, str]]],
    blob_paths: Dict[str, List[str]],
    blob_commits: Dict[str, List[str]],
) -> int:
    head_blobs = _head_blob_map()
    hit_commits: set = set()
    hit_paths: set = set()
    still_at_head = 0
    unattributed = 0
    benign_blobs: List[str] = []
    for sha in sorted(hits):
        paths = sorted(set(blob_paths.get(sha, [])))
        commits = sorted(set(blob_commits.get(sha, [])))
        rules = [rule for _lineno, rule in hits[sha]]
        benign, still = _split_known_benign(sha, rules, commits)
        if benign:
            benign_blobs.append(f"{sha[:12]}:{','.join(sorted(set(benign)))}")
        if not still:
            continue
        if not commits:
            unattributed += 1
        if not paths:
            paths = ["<路径未知>"]
        hit_paths.update(paths)
        hit_commits.update(commits)
        shown = ",".join(c[:12] for c in commits[:5]) or "未知"
        if len(commits) > 5:
            shown += f"+{len(commits) - 5}"
        more = f" paths={len(paths)}" if len(paths) > 1 else ""
        for lineno, rule in hits[sha]:
            if rule not in still:
                continue
            print(
                f"HISTORY {paths[0]}:{lineno} {rule} blob={sha[:12]} "
                f"commits={shown}{more}"
            )
        if any(head_blobs.get(path) == sha for path in paths):
            still_at_head += 1

    from_head = sorted(c for c in hit_commits if _ancestor_of_head(c))
    only_other_refs = len(hit_commits) - len(from_head)

    print(f"[secret-scan] 历史扫描：对象库全部 blob；形状规则 {len(SHAPE_RULES)} 条，不做熵")
    print(
        "[secret-scan] 历史覆盖："
        f"blob 扫描 {stats['history_blobs']} 个 / {stats['history_bytes'] / 1048576:.1f} MiB"
        f"；非 blob 跳过 {stats['history_skip_non_blob']} 个（tree/commit，无文本内容）"
        f"；二进制跳过 {stats['history_skip_binary']} 个（首 8KB 含 NUL）"
        f"；读取失败 {stats['history_skip_missing']} 个"
    )
    print(
        "[secret-scan] 历史命中："
        f"blob={len(hits) - len(benign_blobs)} 个，路径={len(hit_paths)} 个，"
        f"提交={len(hit_commits)} 个"
    )
    if benign_blobs:
        print(
            f"[secret-scan] 已知良性（不拦，登记见 docs/code-guards.md）："
            f"{'；'.join(benign_blobs)}"
        )
    print(
        "[secret-scan] 历史命中提交："
        f"仍可从 HEAD 到达 {len(from_head)} 个；仅存在于其它 ref 或不可达 {only_other_refs} 个"
        + (f"（HEAD 可达：{','.join(c[:12] for c in from_head[:10])}）" if from_head else "")
    )
    print(
        f"[secret-scan] 历史命中 blob：仍以同一 blob 出现在 HEAD 的路径 {still_at_head} 个"
        + (f"；归属索引查不到提交的 {unattributed} 个" if unattributed else "")
    )
    print(
        "[secret-scan] 未扫范围：二进制 blob（首 8KB 含 NUL，不做文本扫描）；"
        "tree 对象（无文本内容，commit message 见下段独立扫描）；"
        "熵规则不跑（全量历史里没有信号）。"
    )
    print("[secret-scan] 输出只含路径:行 + 规则名 + 提交号，不含命中的值。")
    return 0


def _report_history_messages(
    stats: "Counter[str]",
    msg_hits: List[Tuple[str, int, str]],
    tag_hits: List[Tuple[str, int, str]],
    note_hits: List[Tuple[str, int, str]],
) -> None:
    """打印 commit message / tag / notes 的命中。只报位置与规则名。"""
    for sha, lineno, rule in sorted(msg_hits):
        print(f"HISTORY-MSG {sha[:12]}:{lineno} {rule}")
    for name, lineno, rule in sorted(tag_hits):
        print(f"HISTORY-MSG tag:{name}:{lineno} {rule}")
    for target, lineno, rule in sorted(note_hits):
        print(f"HISTORY-MSG note:{target[:12]}:{lineno} {rule}")
    print(
        "[secret-scan] 提交信息扫描："
        f"commit message {stats['history_msgs']} 条 / {stats['history_msg_lines']} 行；"
        f"annotated tag {stats['history_tags']} 个 / {stats['history_tag_lines']} 行"
        f"（轻量 tag 跳过 {stats['history_skip_lightweight_tag']} 个，无独立 message）；"
        f"notes {stats['history_notes']} 条"
    )
    total = len(msg_hits) + len(tag_hits) + len(note_hits)
    print(f"[secret-scan] 提交信息命中：{total} 条")
    if total:
        print(
            "[secret-scan] 提交信息已写进 commit/tag/note 对象，删除字面量必须重写历史；"
            "先轮换凭据，再按 docs/code-guards.md 的裁定处理。"
        )


def _scan_history(stats: "Counter[str]") -> int:
    top = _git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print("[secret-scan] --history 需要 git 仓库；当前目录不在仓库里。")
        return 0
    print(f"[secret-scan] 历史扫描范围：{top.stdout.strip()}（对象库全部 blob）")
    tips = _history_tips()
    print(f"[secret-scan] 归属索引：refs=--all + reflog + {len(tips)} 个 worktree HEAD")
    blob_paths = _history_blob_paths(tips)
    blob_commits = _history_blob_commits(tips)
    hits = _scan_history_blobs(_all_blob_shas(), stats)
    code = _history_report(stats, hits, blob_paths, blob_commits)
    msg_hits = _history_commit_messages(tips, stats)
    tag_hits = _history_tag_messages(stats)
    note_hits = _history_note_messages(stats)
    _report_history_messages(stats, msg_hits, tag_hits, note_hits)
    # 历史模式一直是「只报不拦」（退出码 0）：它是巡检，不是提交闸；
    # 写入闸在 scripts/hooks/commit-msg（--commit-msg）。
    return code


def scan_artifact(root: Path) -> Tuple[List[Tuple[str, str]], "Counter[str]"]:
    """扫一个待发布目录的全部文件（含二进制），返回 [(相对路径, 规则)]。

    与工作树扫描的区别：产物里的 exe/dll/pyc 不能跳过——凭据可能被嵌进去，
    所以直接对字节跑 ``SHAPE_BYTES``；也不认 pragma（产物不是源码）。
    只按 (路径, 规则) 去重，不打印值。
    """
    hits: List[Tuple[str, str]] = []
    seen: set = set()
    stats: "Counter[str]" = Counter()
    root = Path(root)
    if not root.exists():
        stats["artifact_missing"] += 1
        return hits, stats
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        stats["artifact_files"] += 1
        if path.is_symlink():
            stats["artifact_skip_symlink"] += 1
            continue
        try:
            with path.open("rb") as fh:
                carry = b""
                offset = 0
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    buf = carry + chunk
                    base = offset - len(carry)
                    for rule, rx in SHAPE_BYTES:
                        for match in rx.finditer(buf):
                            key = (rule, base + match.start())
                            if key in seen:
                                continue
                            seen.add(key)
                            pair = (path.relative_to(root).as_posix(), rule)
                            if pair not in hits:
                                hits.append(pair)
                    stats["artifact_bytes"] += len(chunk)
                    carry = buf[-128:]
                    offset += len(chunk)
        except OSError:
            stats["artifact_skip_unreadable"] += 1
    return hits, stats


def _report_artifact(
    root: Path, hits: List[Tuple[str, str]], stats: "Counter[str]"
) -> int:
    for rel, rule in hits:
        print(f"BLOCK {rel} {rule}")
    print(f"[secret-scan] 产物扫描：{root}")
    line = (
        "[secret-scan] 覆盖："
        f"文件 {stats['artifact_files']} 个 / {stats['artifact_bytes'] / 1048576:.1f} MiB"
        "（含二进制，按字节跑形状规则）"
    )
    if stats["artifact_skip_symlink"]:
        line += f"；符号链接跳过 {stats['artifact_skip_symlink']} 个"
    if stats["artifact_skip_unreadable"]:
        line += f"；不可读跳过 {stats['artifact_skip_unreadable']} 个"
    print(line)
    print(
        f"[secret-scan] 产物门禁：BLOCK={len(hits)}"
        + ("（发布产物不得包含这些文件：删掉或移出打包目录后复跑）" if hits else "（无凭据形状）")
    )
    print("[secret-scan] 输出只含相对路径 + 规则名，不含命中的值。")
    return 1 if hits else 0


def scan_commit_msg(path: Path) -> Tuple[List[Tuple[int, str]], int]:
    """扫提交信息文件（commit-msg 钩子传进来的那个），返回 ([(行号, 规则)], 行数)。

    只跑形状规则：消息是纯文本，熵规则在这里只会对「一长串 hash/URL」报噪音。
    ``pragma: allowlist secret`` 仍按行生效——说明性文字里要保留整串示例时用它，
    与源码豁免同一纪律（逐行，不做文件级豁免）。
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return _shape_line_hits(lines), len(lines)


def _report_commit_msg(path: Path, hits: List[Tuple[int, str]], lines: int) -> int:
    for lineno, rule in hits:
        print(f"BLOCK {path}:{lineno} {rule}")
    print(f"[secret-scan] commit-msg 扫描：{path}（{lines} 行，只跑形状规则）")
    print(
        f"[secret-scan] 提交信息门禁：BLOCK={len(hits)}"
        + ("（删掉消息里的凭据后重新提交）" if hits else "（无凭据形状）")
    )
    print("[secret-scan] 输出只含行号 + 规则名，不含命中的值。")
    return 1 if hits else 0


def _score_bucket(score: float) -> str:
    for lo, hi in _SCORE_BUCKETS:
        if score < hi:
            return f"{lo:.1f}~{hi:.1f}"
    return ">=6.0"


def _top_dirs(rel: str, depth: int = 2) -> str:
    return "/".join(rel.split("/")[:depth])


def entropy_stats(root: Path, stats: "Counter[str]") -> int:
    """熵告警分层（只量不改判）：文件类型 / 目录 / 分数区间 / 分级。

    口径与 ``scan_text`` 完全一致：**每个文件只取首条**（``entropy_reported``），
    所以这里也给出一条文件级记录，而不是逐行累加——否则统计数和实际告警数对不上。
    """
    by_suffix: "Counter[str]" = Counter()
    by_dir: "Counter[str]" = Counter()
    by_bucket: "Counter[str]" = Counter()
    by_tier: "Counter[str]" = Counter()
    must_rows: List[Tuple[str, int, str, str]] = []
    entropy_files = 0
    for path in _iter_files(root, stats):
        try:
            size = path.stat().st_size
        except OSError:
            stats["skip_unreadable"] += 1
            continue
        if size > _MAX_ENTROPY_BYTES:
            stats["shape_only_large"] += 1
            continue
        try:
            text = _decode(path.read_bytes())
        except OSError:
            stats["skip_unreadable"] += 1
            continue
        if text is None:
            stats["skip_binary"] += 1
            continue
        if not (
            path.suffix.lower() in _ENTROPY_SUFFIXES
            or path.name in _ENTROPY_NAMES
            or _ENV_NAME_RE.match(path.name)
        ):
            continue
        entropy_files += 1
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        hit: Optional[Tuple[int, int, float]] = None
        for lineno, line in enumerate(text.splitlines(), 1):
            if SPDX in line:
                continue
            if any(rx.search(line) for _rule, rx in SHAPE_RULES):
                continue  # 与 scan_text 一致：命中形状的行不参与熵判定
            found = next(_entropy_candidates(line), None)
            if found is not None:
                hit = (lineno, found[0], found[2])
                break
        if hit is None:
            continue
        lineno, start, score = hit
        tier = _entropy_tier(rel, text.splitlines()[lineno - 1], start)
        suffix = path.suffix.lower() or path.name
        by_suffix[suffix] += 1
        by_dir[_top_dirs(rel)] += 1
        by_bucket[_score_bucket(score)] += 1
        by_tier[tier] += 1
        if tier == "must":
            must_rows.append((rel, lineno, suffix, f"{score:.2f}"))

    stats["entropy_files"] = entropy_files

    def _dump(title: str, counter: "Counter[str]", limit: int = 15) -> None:
        print(f"[secret-scan] {title}（共 {sum(counter.values())} 条）")
        for key, count in counter.most_common(limit):
            print(f"    {key:<28} {count}")

    print(f"[secret-scan] 熵告警分层：{root}（每文件每规则一条，共 {sum(by_tier.values())} 条）")
    print(f"[secret-scan] 参与熵检查的文件 {entropy_files} 个")
    _dump("按后缀", by_suffix)
    _dump("按目录（前两层）", by_dir)
    _dump("按分数区间", by_bucket)
    _dump("按分级", by_tier)
    print(
        f"[secret-scan] 分层合计：总={sum(by_tier.values())} "
        f"must={by_tier['must']} sample={by_tier['sample']}"
        "（每文件每规则一条）"
    )
    print(f"[secret-scan] 必须看明细（{len(must_rows)} 条，路径:行 + 类型 + 分数，不含值）")
    for rel, lineno, suffix, score in sorted(must_rows)[:200]:
        print(f"    MUST {rel}:{lineno} type={suffix} score={score}")
    if len(must_rows) > 200:
        print(f"    MUST 其余 {len(must_rows) - 200} 条省略（计数含全部）")
    return 0


def _report(findings: Iterable[Finding], stats: Optional["Counter[str]"] = None) -> int:
    unique = sorted(set(findings), key=lambda item: (item[0], item[1], item[2]))
    blocks = [f for f in unique if f[3] == "block"]
    musts = [f for f in unique if f[3] == "must"]
    samples = [f for f in unique if f[3] == "sample"]
    warns = [f for f in unique if f[3] == "warn"]

    for path, lineno, rule, _tier in blocks:
        print(f"BLOCK {path}:{lineno} {rule}")
    for path, lineno, rule, _tier in musts[:_MAX_WARN_PRINT]:
        print(f"MUST  {path}:{lineno} {rule}")
    if len(musts) > _MAX_WARN_PRINT:
        print(f"MUST  其余 {len(musts) - _MAX_WARN_PRINT} 条省略（计数含全部）")
    for path, lineno, rule, _tier in samples[:_MAX_SAMPLE_PRINT]:
        print(f"SAMPLE {path}:{lineno} {rule}")
    if len(samples) > _MAX_SAMPLE_PRINT:
        print(
            f"SAMPLE 其余 {len(samples) - _MAX_SAMPLE_PRINT} 条省略"
            "（抽样看：代表条数已打，全量分布用 --entropy-stats）"
        )
    for path, lineno, rule, _tier in warns[:_MAX_WARN_PRINT]:
        print(f"WARN  {path}:{lineno} {rule}")
    if len(warns) > _MAX_WARN_PRINT:
        print(f"WARN  其余 {len(warns) - _MAX_WARN_PRINT} 条省略（计数含全部）")

    by_rule = Counter(rule for _p, _l, rule, _t in unique)
    print(
        f"[secret-scan] BLOCK={len(blocks)} MUST={len(musts)} "
        f"SAMPLE={len(samples)} WARN={len(warns)}"
    )
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
    parser.add_argument(
        "--history",
        action="store_true",
        help="扫 git 历史里的 blob + commit/tag/note 信息（只跑形状规则；不拦提交，退出码 0）",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="扫一个待发布目录的全部文件（含二进制），BLOCK>0 退出 1",
    )
    parser.add_argument(
        "--commit-msg",
        type=Path,
        default=None,
        metavar="FILE",
        help="扫提交信息文件（commit-msg 钩子用），BLOCK>0 退出 1",
    )
    parser.add_argument(
        "--entropy-stats",
        action="store_true",
        help="只统计熵告警的分层分布（后缀/目录/分数区间/分级），不改判定、退出码 0",
    )
    args = parser.parse_args(argv)

    stats: "Counter[str]" = Counter()
    if args.entropy_stats:
        roots = [Path(item) for item in args.paths] if args.paths else [Path(args.root)]
        for root in roots:
            entropy_stats(root, stats)
        return 0
    if args.commit_msg is not None:
        msg_hits, msg_lines = scan_commit_msg(args.commit_msg)
        return _report_commit_msg(args.commit_msg, msg_hits, msg_lines)
    if args.artifact is not None:
        hits, artifact_stats = scan_artifact(args.artifact)
        return _report_artifact(args.artifact, hits, artifact_stats)
    if args.history:
        return _scan_history(stats)
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
