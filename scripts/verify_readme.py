# SPDX-License-Identifier: MIT
"""README 契约守卫：门面写出来的东西必须真的存在、真的能跑。

检查项（每项独立可测，返回问题描述列表，空列表 = 通过）：

* ``links``      markdown 链接目标存在
* ``paths``      反引号里的仓库路径存在；裸文件名必须在仓内唯一（逼人写全路径）
* ``version``    首行版本号 == ``mbdsdr_ai/VERSION``
* ``commands``   README 里所有 ``python3 ...``（bash 代码块 + 反引号）目标存在，
                 且每个 ``--flag`` 真的被目标脚本的 argparse 注册
* ``japanese``   日本語段落不混进简体中文词
* ``structure``  两套桌面端都在；首页不复刻会过期的工具清单表

用法::

    python3 scripts/verify_readme.py            # 人读
    python3 scripts/verify_readme.py --json    # CI / 测试消费

设计红线：只做静态核对，不执行 README 里的任何命令，不联网、不碰硬件。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.S)
PY_CMD_RE = re.compile(r"\bpython3?\s+(-\s*m\s+)?([^\s]+)")
FLAG_RE = re.compile(r"(?<![\w-])--[A-Za-z][A-Za-z0-9-]*")
PATH_EXT = {
    ".py", ".md", ".cpp", ".h", ".hpp", ".yml", ".yaml", ".toml", ".json",
    ".sh", ".bat", ".ps1", ".txt", ".db", ".csv", ".png", ".html", ".spec",
    ".rules",
}
#: 日本語段落里出现即视为「中文混入」的词（都是本项目实际踩过的）。
JA_FORBIDDEN = (
    "向量", "分析仪", "源码", "零硬件", "演示", "仓库", "全仓", "闭环",
    "已验", "已修", "测试通过", "全部实现", "无硬件演示",
)
#: 第三方 CLI 的参数不归本仓库 argparse 管。
SKIP_MODULES = {"pytest", "pip", "python", "python3"}
#: 复刻进 README 就会立刻过期的工具表行。
FROZEN_TOOL_ROW_RE = re.compile(
    r"^\|\s*(tune_frequency|set_mode|set_sample_rate|memory_add|"
    r"start_recording|vna_calibration)\s*\|", re.M)


def read_readme(path: Path | None = None) -> str:
    return (path or README).read_text(encoding="utf-8-sig")


def _iter_repo_files(root: Path) -> Iterable[Path]:
    skip = {".git", "mbdsdr_ai-0.2.0", "__pycache__", "node_modules", "build"}
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.relative_to(root).parts) & skip):
            yield p


# ── 1. links ─────────────────────────────────────────────────────────────
def check_links(text: str, root: Path = ROOT) -> list[str]:
    bad = []
    for target in LINK_RE.findall(text):
        t = target.strip()
        if not t or t.startswith(("http://", "https://", "#", "mailto:")):
            continue
        rel = t.split("#", 1)[0].strip()
        if not rel:
            continue
        if not (root / rel).exists():
            bad.append(f"链接目标不存在: {t}")
    return bad


# ── 2. paths ─────────────────────────────────────────────────────────────
def check_paths(text: str, root: Path = ROOT, _files: list[Path] | None = None) -> list[str]:
    bad = []
    files = _files if _files is not None else None
    # markdown 链接的文本（如 [`NOTICE.md`](NOTICE.md)）是展示文字不是路径引用，
    # 目标由 check_links 校验；先剥掉链接外壳，避免把链接文本误判成裸文件名。
    body = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\2", text)
    for token in BACKTICK_RE.findall(body):
        t = token.strip()
        if any(c in t for c in (" ", "\t", "(", ")", "|", "=", "://", "*", ",")):
            continue
        if t.startswith(("-", "$", "~")):
            continue
        if Path(t).suffix.lower() not in PATH_EXT:
            continue
        if "/" in t or "\\" in t:
            if not (root / t).exists():
                bad.append(f"反引号路径不存在: {t}")
            continue
        # 裸文件名一律要求写全路径：README 是首页，读者不该靠猜去找文件。
        # 实测踩过 llm_client.cpp 写成裸名（真身是 cpp/src/ai/llm_client.cpp）。
        if files is None:
            files = list(_iter_repo_files(root))
        hits = [p for p in files if p.name == t]
        if not hits:
            bad.append(f"反引号文件名在仓内找不到: {t}")
        else:
            where = str(hits[0].relative_to(root)).replace("\\", "/")
            bad.append(f"反引号文件名请写全路径: {t} -> {where}")
    return bad


# ── 3. version ───────────────────────────────────────────────────────────
def check_version(text: str, root: Path = ROOT) -> list[str]:
    first = text.splitlines()[0] if text.splitlines() else ""
    m = re.search(r"v(\d+\.\d+\.\d+)", first)
    canonical = (root / "mbdsdr_ai" / "VERSION").read_text(
        encoding="utf-8-sig").strip()
    if not m:
        return ["README 首行缺版本号（发布清单要求 vX.Y.Z）"]
    if m.group(1) != canonical:
        return [f"README 首行版本 {m.group(1)} ≠ canonical {canonical}"]
    return []


# ── 4. commands ──────────────────────────────────────────────────────────
def _script_flags(path: Path) -> set[str]:
    """静态抽出脚本里 argparse 注册的 --flag（不 import、不执行）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            for f in FLAG_RE.findall(first.value):
                flags.add(f)
    return flags


def _module_to_file(root: Path, module: str) -> Path | None:
    rel = module.replace(".", "/")
    for cand in (root / f"{rel}.py", root / rel / "__main__.py"):
        if cand.exists():
            return cand
    return None


def _python_commands(text: str) -> list[str]:
    """bash 代码块里的命令行 + 反引号里的命令片段，去重保序。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        line = line.split("#", 1)[0].strip()
        m = PY_CMD_RE.search(line)
        if m and line not in seen:
            seen.add(line)
            out.append(line)

    for block in BASH_BLOCK_RE.findall(text):
        for line in block.splitlines():
            add(line)
    for token in BACKTICK_RE.findall(text):
        if re.search(r"\bpython3?\s+", token):
            add(token)
    return out


def check_commands(text: str, root: Path = ROOT) -> list[str]:
    bad = []
    for cmd in _python_commands(text):
        m = PY_CMD_RE.search(cmd)
        if not m:
            continue
        is_module, target = bool(m.group(1)), m.group(2)
        if is_module:
            if target in SKIP_MODULES:
                continue
            script = _module_to_file(root, target)
            if script is None:
                bad.append(f"命令里的模块找不到文件: {target} ({cmd[:70]})")
                continue
        else:
            if target.startswith("-"):
                continue
            script = root / target
            if not script.exists():
                bad.append(f"命令里的脚本不存在: {target}")
                continue
        known = _script_flags(script)
        for flag in FLAG_RE.findall(cmd):
            if flag not in known:
                bad.append(
                    f"{target} 未注册参数 {flag}（README 写了假参数）: {cmd[:70]}")
    return bad


# ── 5. japanese ──────────────────────────────────────────────────────────
def check_japanese(text: str, root: Path = ROOT) -> list[str]:
    blocks = re.findall(r"<details[^>]*>(.*?)</details>", text, re.S)
    ja = [b for b in blocks if "日本語" in b]
    if not ja:
        return ["README 缺日本語摘要块"]
    bad = []
    for word in JA_FORBIDDEN:
        if word in ja[0]:
            bad.append(f"日本語段落混入简体中文词: {word}")
    return bad


# ── 6. structure ─────────────────────────────────────────────────────────
def check_structure(text: str, root: Path = ROOT) -> list[str]:
    bad = []
    for must, why in (
        ("desktop/README.md", "Python/PySide6 桌面端入口"),
        ("cpp/README.md", "原生 C++/Qt 桌面端入口"),
        ("python desktop/main.py", "Python 桌面端启动命令"),
        ("scripts/verify_readme.py", "本守卫的可复跑入口"),
        ("scripts/repro_readme_offline.py", "离线命令的一键复跑入口"),
    ):
        if must not in text:
            bad.append(f"README 缺 {why}: {must}")
    if FROZEN_TOOL_ROW_RE.search(text):
        bad.append("README 复刻了会过期的工具清单表（应指向运行时事实源）")
    return bad


CHECKS: dict[str, Callable[..., list[str]]] = {
    "links": check_links,
    "paths": check_paths,
    "version": check_version,
    "commands": check_commands,
    "japanese": check_japanese,
    "structure": check_structure,
}


def run_checks(text: str | None = None, root: Path = ROOT) -> dict[str, list[str]]:
    body = text if text is not None else read_readme(root / "README.md")
    return {name: fn(body, root) for name, fn in CHECKS.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 README 的链接/路径/命令/版本/语种契约")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供 CI 消费）")
    ap.add_argument("--readme", default="", help="指定 README 路径（默认仓库根 README.md）")
    args = ap.parse_args(argv)
    text = read_readme(Path(args.readme)) if args.readme else None
    result = run_checks(text)
    total = sum(len(v) for v in result.values())
    if args.json:
        print(json.dumps({"ok": total == 0, "total": total, "checks": result},
                         ensure_ascii=False, indent=2))
    else:
        for name, issues in result.items():
            print(f"[{'PASS' if not issues else 'FAIL'}] {name}: "
                  f"{'ok' if not issues else str(len(issues)) + ' issue(s)'}")
            for i in issues:
                print("       -", i)
        print(f"\n{'README_CONTRACT_OK' if total == 0 else 'README_CONTRACT_FAIL'} "
              f"({total} issue(s))")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
