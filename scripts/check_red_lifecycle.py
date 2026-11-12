#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""红项生命周期机检：把「已修」这类状态当作需举证的断言来查。

规则：
  R1 FAIL  出现「已修 / 已修复 / 已落地 / 已解决」但同一行没有 commit hash。
  R2 FAIL  写了 hash，但 `git cat-file -e <sha>^{commit}` 查不到（hash 打错/不是提交）。
  R3 WARN  状态为已修且 hash 有效，但相邻行没有可复跑命令（`ctest -R ...` / `pytest ...`），
          且无法按测试名/槽名/脚本自动映射。
  R3 SKIP  同一情形但**能**自动映射到可执行命令 → 视为已举证（不记 WARN）；
          映射失败 → 记 SKIP 并逐条打印（`SKIP 1（R3 无复跑命令：<file>:<line>，…）`），不许静默。
          映射规则见 `CommandResolver`（测试函数名→`pytest <file>::<name>`、C++ 槽名→`ctest -R`、
          `scripts/*.py`→`python scripts/*.py`；同名多处/无符号时为无法映射）。
  R4 FAIL  标了 `not_verified` / 「未复核」，但没写「为什么没验 / 缺什么才能验」。
  R5 FAIL  同一红项（测试名 / 槽名 / 条目编号 / §节号 / `§7.3.1 A` 组键）在 ≥2 处
          （跨文档或同文档不同节）状态不一致。
  R6 FAIL  写了有效 hash，但该提交的改动文件与本条描述的对象（文件名 / 符号 /
          测试名）无任何交集，或本条根本没描述可比对象——「随便填一个仓库里
          存在的 hash」不能混过 R1。
  R7 FAIL  出现快照标记，但头部不完整（缺「本文为当时状态快照」/「未逐条复核」/
          「不作现状依据」/「共 N 条未复核」），或声明的 N 与实际条数不符。
  R8 FAIL  已转绿条目的**再检义务**：条目必须声明「验证提交 `<sha>` + 文件集合」，
          且集合里的每个文件/目录在 `<sha>` 之后不得再被改动（判据是 git 历史，
          不是行号/mtime）。一旦有后继提交碰过，该条目的有效状态即为「待复核」，
          并点名「哪个文件在哪笔提交之后又变了」。绑定文件被删、绑定写不全、
          「已转绿」却没有绑定，同样判 R8。
          另：状态词含「已转绿」但没有 commit hash → R1，文案固定为
          「已修必须带一次可复跑的验证记录（命令 + 实际输出）」。
  R9 FAIL  已修条目有 hash、有复跑命令，但本节/相邻行内没有「实际输出」
          （passed/failed/AssertionError/→ 等结果行）→ 表头规矩的
          「命令 + 实际输出」后半段也由机检执行。

快照（T 路线，单独成类计数，不许改文档销红）：
  * 头部四要素齐全且 N 与实际条数一致 → 该文件内所有红项状态行降为
    `level="SNAPSHOT"`（不记 FAIL/WARN，也不参与 R1/R2/R4/R6/R5），
    但每条仍逐条打印并计入总账的 SNAPSHOT 数。
  * 头部不完整或 N 不符 → R7 FAIL，且**不**降级，原红项照常判红。
  * 总账行固定打印：
    `R1 实修 X 条 / SNAPSHOT Y 条 / 历史横幅 Z 条`
    其中 X = 已修 + hash 有效 + R6 相交的条数；Z = 历史横幅降级条数。

豁免（显式，不静默）：
  * 默认读 `docs/red-lifecycle-exemptions.md`；只对文件里逐条列明、且机制可验证的路径生效。
  * 目前唯一允许的机制是 `content-hash-ledger`（该文件的 hash 列是行内容 hash，不是提交 hash）。
    声明了该机制但文件里没有 12 位行内容 hash 列 → 豁免无效，照常扫描并报 FAIL。
  * 每次运行打印 `SKIP n（豁免：<文件>，理由见 …）`。

历史快照横幅：
  * 只有 `docs/learn/phase*/` 下的文件允许写「历史快照，不作现状依据」横幅。
  * 带横幅的历史文件，R1/R2 降为 WARN 并计入单独的 HIST-WARN。
  * 现代文档带横幅 → X1 FAIL（横幅只能用于历史目录）。

用法：
  python scripts/check_red_lifecycle.py
  python scripts/check_red_lifecycle.py --json
  python scripts/check_red_lifecycle.py --root <仓库>       # 测试用
  python scripts/check_red_lifecycle.py --exemptions <清单>
  python scripts/check_red_lifecycle.py --exclude <md...>   # 临时排除，仍会打印 SKIP

退出码：0 无 FAIL（WARN 允许）；1 有 FAIL；2 用法/git 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
# 测试可用 --root 指到临时仓库；运行期由 main() 覆盖。
SCAN_ROOT = ROOT

EXEMPTION_FILE = "docs/red-lifecycle-exemptions.md"
BANNER_TEXT = "历史快照，不作现状依据"
HISTORICAL_PATH_RE = re.compile(r"^docs/learn/phase\d+/")
ALLOWED_MECHANISMS = {"content-hash-ledger"}

SNAPSHOT_TITLE = "本文为当时状态快照"
SNAPSHOT_REQUIRED = ("未逐条复核", "不作现状依据")
SNAPSHOT_COUNT_RE = re.compile(r"共\s*(\d+)\s*条未复核")
SNAPSHOT_MARKERS = (SNAPSHOT_TITLE, "未逐条复核", "不作现状依据")

FIXED_RE = re.compile(r"已修复|已修|已落地|已解决")
RECHECK_MARK = "再检绑定"
RECHECK_SHA_RE = re.compile(r"验证提交\s*`([0-9a-f]{7,40})`")
RECHECK_FILES_RE = re.compile(r"文件集合\s*(.+?)\s*$")
LEDGER_DISPLAY = "docs/known-red-register.md"
HEADING_RE = re.compile(r"^(#{1,6})\s")
# 「未复核」只有落在红项语境里才算状态；「未复核 claim」是协作时序，不是红项未验。
RED_CONTEXT_RE = re.compile(r"槽|红项|测试|用例|状态|ctest|pytest|test_")
OPEN_RE = re.compile(r"待修|未修|在途修")
# 纯数字串（如采样率 3200000）不是提交 hash；要求至少含一个 a-f 字母。
HASH_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
CMD_RE = re.compile(
    r"`?(?:python\s+-m\s+)?pytest\b|`?ctest\s+-R|`?python\s+scripts/|`?\./scripts/"
)
OUTPUT_RE = re.compile(
    r"\b\d+\s+(?:passed|failed)\b|\b(?:passed|failed)\b|AssertionError|"
    r"Traceback|exit code|返回码|→"
)
REASON_RE = re.compile(
    r"缺|阻塞|原因|因为|无法|尚未|待|才能|未具备|不可复现|无环境|没有|不满足"
)
ITEM_RE = re.compile(r"`([^`]*(?:::|test_[A-Za-z0-9_]+)[^`]*)`")
SLOT_KEY_RE = re.compile(r"`([A-Z]\d+)`")
SECTION_KEY_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)\s+([A-Z])(?![0-9A-Za-z])")
GROUP_REF_RE = re.compile(r"(?<![0-9A-Za-z])([A-C])\s*的\s*\d+\s*(?:条|槽|个)")
# R6：从条目里抽「改动对象」。只认有辨识度的东西，避免把普通词当符号。
PATH_REF_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*"
    r"\.(?:py|cpp|cc|cxx|h|hpp|js|ts|tsx|md|json|ps1|cmake|txt|yml|yaml)"
)
CXX_SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*::([A-Za-z_~][A-Za-z0-9_]*)")
TEST_NAME_RE = re.compile(r"\b(test_[A-Za-z0-9_]+)\b")
# 反引号里的首标识符也算对象：`kDbViewUpperDefault=-20`、`currentDbCeil()==0`、
# `main_window.cpp:5644` 都能取到 `kDbViewUpperDefault` / `currentDbCeil` / `main_window`。
BACKTICK_IDENT_RE = re.compile(r"`[ \t]*([A-Za-z_][A-Za-z0-9_]{3,})")
TEXT_SUFFIXES = {
    ".py", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".js", ".ts", ".tsx", ".md",
    ".json", ".ps1", ".cmake", ".txt", ".yml", ".yaml",
}
IDENT_STOPWORDS = {
    "not_verified", "open", "fixed", "ctest", "pytest", "python", "commit",
    "hash", "sha", "docs", "tests", "script", "scripts", "true", "false",
    "none", "null", "todo", "skip", "warn", "fail", "pass",
} | ALLOWED_MECHANISMS
QUOTE_PAIRS = (("「", "」"), ("“", "”"), ("‘", "’"), ('"', '"'))
STATUS_LABELS = {
    "fixed": "已修",
    "not_verified": "not_verified",
    "open": "open",
}


@dataclass
class Finding:
    rule: str
    level: str
    path: str
    line: int
    message: str
    text: str
    historical: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "rule": self.rule,
            "level": self.level,
            "historical": self.historical,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "text": self.text,
        }


@dataclass
class Hit:
    path: str
    line: int
    status: str
    key: str = field(default="")


def _strip_quoted(text: str) -> str:
    for left, right in QUOTE_PAIRS:
        text = re.sub(left + r"[^" + right + r"]*" + right, "", text)
    return text


def _item_keys(text: str) -> List[str]:
    keys: List[str] = []
    for raw in ITEM_RE.findall(text):
        token = raw.strip()
        key = token.split("::")[-1].strip() if "::" in token else token.strip()
        key = re.sub(r"\(.*$", "", key).strip()
        if key and (key.startswith("test_") or "::" in raw):
            keys.append(key)
    for slot in SLOT_KEY_RE.findall(text):
        keys.append(slot)
    for section, letter in SECTION_KEY_RE.findall(text):
        keys.append(f"§{section} {letter}")
    return keys


def _iter_lines(path: Path) -> Iterable[Tuple[int, str]]:
    """跳过代码块；返回 (行号, 原文)。"""
    in_fence = False
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        yield number, raw


def _commit_exists(sha: str, cache: Dict[str, bool]) -> bool:
    if sha in cache:
        return cache[sha]
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=str(SCAN_ROOT),
        capture_output=True,
        text=True,
    )
    cache[sha] = proc.returncode == 0
    return cache[sha]


def _detect_statuses(text: str, context: str = "", raw: str = "") -> List[str]:
    found: List[str] = []
    fixed = bool(FIXED_RE.search(text))
    # 「已转绿」只在状态位（标题 / 状态行）算销账声明；正文叙述里的
    # 「该节点已转绿」「不得再写已转绿」不是状态，不能要求它带 hash。
    if not fixed and "已转绿" in text:
        stripped = raw.strip()
        fixed = bool(HEADING_RE.match(stripped)) or bool(
            re.match(r"^[-*]\s*(?:\*\*)?(?:状态|转绿)[:：]", stripped)
        )
    if fixed:
        found.append("fixed")
    if "`not_verified`" in text or (
        "未复核" in text and RED_CONTEXT_RE.search(context or text)
    ):
        found.append("not_verified")
    if OPEN_RE.search(text):
        found.append("open")
    return found


def _banner_line(lines: List[Tuple[int, str]]) -> Optional[int]:
    for number, raw in lines[:20]:
        if BANNER_TEXT in raw:
            return number
    return None


def _section_bodies(lines: List[Tuple[int, str]]) -> Dict[int, str]:
    """标题行号 → 该标题到下一个同级/更高级标题之间的正文。

    状态词常写在 `## Kxx — … 已转绿 <sha>` 的标题上，而验证命令、文件集合
    在正文里；只看标题 ±2 行会把「有举证」误判成「没举证」。
    """
    heads = [
        (number, len(match.group(1)))
        for number, raw in lines
        for match in [HEADING_RE.match(raw.strip())]
        if match
    ]
    bodies: Dict[int, str] = {}
    for index, (number, level) in enumerate(heads):
        end = len(lines) + 1
        for later, later_level in heads[index + 1:]:
            if later_level <= level:
                end = later
                break
        bodies[number] = "\n".join(
            raw for candidate, raw in lines if number < candidate < end
        )
    return bodies


def _parse_recheck_binding(raw: str) -> Tuple[Optional[str], List[str]]:
    """解析 `- **再检绑定**：验证提交 `<sha>`；文件集合 `a`、`b/``。"""
    sha_match = RECHECK_SHA_RE.search(raw)
    files_match = RECHECK_FILES_RE.search(raw)
    if not sha_match or not files_match:
        return None, []
    targets: List[str] = []
    for token in re.split(r"[、,，]", files_match.group(1)):
        cleaned = token.strip().strip("`").strip().strip("。；;")
        if cleaned:
            targets.append(cleaned)
    return sha_match.group(1), targets


def _later_changes(
    sha: str, target: str, cache: Dict[Tuple[str, str], Optional[List[Tuple[str, str, List[str]]]]]
) -> Optional[List[Tuple[str, str, List[str]]]]:
    """`sha..HEAD` 之间碰过 target 的提交：[(短 hash, 标题, 该提交碰到的 target 内文件)]。

    返回 None 表示 git 查询失败（sha 不可解析等），与「没有后继改动」区分开。
    """
    key = (sha, target)
    if key in cache:
        return cache[key]
    proc = subprocess.run(
        ["git", "log", "--name-only", "--format=COMMIT%x09%h%x09%s", f"{sha}..HEAD", "--", target],
        cwd=str(SCAN_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        cache[key] = None
        return None
    commits: List[Tuple[str, str, List[str]]] = []
    current: Optional[Tuple[str, str, List[str]]] = None
    for line in proc.stdout.splitlines():
        if line.startswith("COMMIT\t"):
            parts = line.split("\t", 2)
            while len(parts) < 3:
                parts.append("")
            current = (parts[1], parts[2], [])
            commits.append(current)
        elif line.strip() and current is not None:
            current[2].append(line.strip())
    cache[key] = commits
    return commits


def _recheck_findings(
    display: str,
    number: int,
    raw: str,
    cache: Dict[Tuple[str, str], Optional[List[Tuple[str, str, List[str]]]]],
) -> List[Finding]:
    """R8：把「已转绿」的再检义务变成机检。"""
    sha, targets = _parse_recheck_binding(raw)
    if sha is None or not targets:
        return [
            Finding(
                "R8", "FAIL", display, number,
                "再检绑定无法解析：需要「验证提交 `<sha>`；文件集合 `文件或目录/`、`…`」",
                raw.strip(),
            )
        ]
    findings: List[Finding] = []
    for target in targets:
        relative = target.replace("\\", "/").lstrip("./")
        path = SCAN_ROOT / relative.rstrip("/")
        exists = path.is_dir() if target.endswith("/") else path.is_file()
        if not exists:
            findings.append(
                Finding(
                    "R8", "FAIL", display, number,
                    f"待复核：声称已绿的文件/目录 `{target}` 已不存在，"
                    "删掉的文件不能继续算绿（请重跑验证或更正绑定）",
                    raw.strip(),
                )
            )
            continue
        commits = _later_changes(sha, relative.rstrip("/") or relative, cache)
        if commits is None:
            findings.append(
                Finding(
                    "R8", "FAIL", display, number,
                    f"再检失败：无法读取验证提交 `{sha}` 之后的 git 历史（sha 不可解析？）",
                    raw.strip(),
                )
            )
            continue
        if not commits:
            continue
        details: List[str] = []
        for commit_sha, subject, files in commits[:3]:
            hit_files = [item for item in files if item and item != target] or files
            named = "、".join(f"`{item}`" for item in hit_files[:3]) or f"`{target}`"
            details.append(f"{commit_sha}（{subject}）改了 {named}")
        more = f"；另有 {len(commits) - 3} 笔" if len(commits) > 3 else ""
        findings.append(
            Finding(
                "R8", "FAIL", display, number,
                f"待复核：`{target}` 在验证提交 `{sha}` 之后又被改动"
                f"（共 {len(commits)} 笔：{'；'.join(details)}{more}）；"
                "本条不能继续算绿，需重跑并把验证提交更新到最新一次",
                raw.strip(),
            )
        )
    return findings


def _entry_requires_recheck(
    display: str, lines: List[Tuple[int, str]], bodies: Dict[int, str]
) -> List[Finding]:
    """R8 配套：账本里写「已转绿」的 K 条目必须带再检绑定。"""
    if display != LEDGER_DISPLAY:
        return []
    findings: List[Finding] = []
    for number, raw in lines:
        stripped = raw.strip()
        if not re.match(r"^##\s+K\d+", stripped):
            continue
        claims_fixed = "已转绿" in stripped or "已修" in stripped
        if not claims_fixed:
            continue
        if RECHECK_MARK in bodies.get(number, ""):
            continue
        kid = re.match(r"^##\s+(K\d+)", stripped).group(1)  # type: ignore[union-attr]
        findings.append(
            Finding(
                "R8", "FAIL", display, number,
                f"{kid} 声称已修/已转绿，但没有「再检绑定」（验证提交 + 文件集合），"
                "后继提交再碰同一文件时无法自动退回待复核",
                stripped,
            )
        )
    return findings



class CommandResolver:
    """把文档里的测试名 / C++ 槽名 / 脚本路径映射成可执行复跑命令。

    只在 R3 分支用：相邻行没有显式命令时尝试补一条；补不出来就记 SKIP 并打印原因。
    索引按需构建，结果缓存；不写盘、不执行命令。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._py: Optional[Dict[str, List[str]]] = None
        self._ctest: Optional[Dict[str, str]] = None
        self._symbols: Dict[str, List[str]] = {}
        self._ctest_names: Optional[set] = None

    def _load_py(self) -> Dict[str, List[str]]:
        if self._py is None:
            index: Dict[str, List[str]] = defaultdict(list)
            tests_dir = self.root / "tests"
            if tests_dir.is_dir():
                for path in sorted(tests_dir.rglob("*.py")):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for name in set(re.findall(r"\bdef\s+(test_[A-Za-z0-9_]+)", text)):
                        index[name].append(path.relative_to(self.root).as_posix())
            self._py = {key: sorted(set(value)) for key, value in index.items()}
        return self._py

    def _load_ctest(self) -> Dict[str, str]:
        if self._ctest is None:
            mapping: Dict[str, str] = {}
            for candidate in (
                self.root / "cpp" / "build" / "CTestTestfile.cmake",
                self.root / "cpp" / "CTestTestfile.cmake",
            ):
                if not candidate.exists():
                    continue
                text = candidate.read_text(encoding="utf-8", errors="replace")
                for match in re.finditer(r'add_test\(\s*([^)\s]+)\s+"([^"]+)"', text):
                    exe = Path(match.group(2).replace("\\", "/")).name
                    mapping.setdefault(Path(exe).stem, match.group(1))
            self._ctest = mapping
        return self._ctest

    def _cpp_files(self, symbol: str) -> List[str]:
        if symbol not in self._symbols:
            hits: List[str] = []
            tests_dir = self.root / "cpp" / "tests"
            if tests_dir.is_dir():
                for path in sorted(tests_dir.glob("*.cpp")):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if symbol in text:
                        hits.append(path.stem)
            self._symbols[symbol] = hits
        return self._symbols[symbol]

    def resolve(self, text: str) -> Optional[str]:
        tokens = re.findall(r"`([^`]+)`", text)
        # 1) 显式测试文件：`tests/test_x.py` / `test_x.py` / `test_xxx`
        for token in tokens:
            base = token.split(":")[0].strip()
            name = Path(base).name
            if not name.startswith("test_"):
                continue
            if base.endswith(".py"):
                if base.startswith("tests/") and (self.root / base).exists():
                    return f"pytest {base}"
                stem_candidates = [name]
            else:
                stem_candidates = [name + ".py"]
            tests_dir = self.root / "tests"
            if tests_dir.is_dir():
                for candidate in stem_candidates:
                    hits = sorted(tests_dir.rglob(candidate))
                    if len(hits) == 1:
                        return "pytest " + hits[0].relative_to(self.root).as_posix()
        # 2) Python 测试函数名：唯一出现 → pytest <file>::<name>
        for name in re.findall(r"\b(test_[A-Za-z0-9_]+)\b", text):
            files = self._load_py().get(name, [])
            if len(files) == 1:
                return f"pytest {files[0]}::{name}"
        # 3) C++ 槽名：`display::Foo` 或表格里的裸写法 display::Foo → 唯一测试源文件 → ctest 名
        ctest = self._load_ctest()
        for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_~][A-Za-z0-9_]*)", text):
            symbol = match.group(2)
            files = self._cpp_files(symbol)
            if len(files) == 1:
                name = ctest.get(files[0])
                if name:
                    return f'ctest -R "^{name}$"'
        if self._ctest_names is None:
            self._ctest_names = set(ctest.values())
        # 4) 直接写 ctest 名：`spectrum_display`
        for token in tokens:
            name = token.strip()
            if name in self._ctest_names:
                return f'ctest -R "^{name}$"'
        # 5) 脚本：`scripts/check_x.py` → python scripts/check_x.py
        for token in tokens:
            base = token.split(":")[0].strip()
            if base.startswith("scripts/") and base.endswith(".py") and (self.root / base).exists():
                return f"python {base}"
        return None

    def reason(self, text: str) -> str:
        """映射失败时给出可打印的原因，不许静默。"""
        names = re.findall(r"\b(test_[A-Za-z0-9_]+)\b", text)
        for name in names:
            files = self._load_py().get(name, [])
            if len(files) > 1:
                return f"测试名 `{name}` 出现在 {len(files)} 个文件，无法唯一映射"
        if re.findall(r"`([^`]+)`", text):
            return "有文件名/符号但无法唯一映射到 pytest/ctest"
        return "无可识别的测试名/槽名/脚本"


@dataclass
class ChangeRefs:
    paths: Set[str] = field(default_factory=set)
    symbols: Set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.paths or self.symbols)

    def brief(self, limit: int = 3) -> str:
        items = sorted(self.paths) + sorted(self.symbols)
        head = "、".join(items[:limit])
        return head + ("…" if len(items) > limit else "")


def _brief(items: Iterable[str], limit: int = 3) -> str:
    listing = list(items)
    head = "、".join(listing[:limit]) or "-"
    return head + ("…" if len(listing) > limit else "")


def _change_refs(text: str) -> ChangeRefs:
    """从条目文本里抽 R6 要比对的改动对象：文件路径 + 符号/测试名。"""
    refs = ChangeRefs()
    for match in PATH_REF_RE.finditer(text):
        raw = match.group(0).strip("`（）()、，。:：;；")
        if raw:
            refs.paths.add(raw.replace("\\", "/"))
    for match in CXX_SYMBOL_RE.finditer(text):
        refs.symbols.add(match.group(1))
    for match in TEST_NAME_RE.finditer(text):
        refs.symbols.add(match.group(1))
    for match in BACKTICK_IDENT_RE.finditer(text):
        token = match.group(1)
        if token.lower() in IDENT_STOPWORDS or HASH_RE.fullmatch(token) or token.startswith("test_"):
            # test_ 名字已由 TEST_NAME_RE 收进 symbols，这里不重复。
            continue
        refs.symbols.add(token)
    return refs


class CommitInspector:
    """R6 防伪闸门：hash 必须真的碰到该条声称改动的东西。

    - 改动文件列表来自 `git show --name-only --format= <sha>`（按 sha 缓存）。
    - 文件交集：完整路径或 basename 命中即算相交。
    - 符号/测试名：在该提交的这些改动文件里 `git grep -F` 能找到即算相交
      （命中文件名 stem 也算）。
    - 本条一个可比对象都抽不出来 → 无法比对，同样判红。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._files: Dict[str, List[str]] = {}
        self._grep: Dict[Tuple[str, str], bool] = {}

    def files(self, sha: str) -> List[str]:
        if sha not in self._files:
            proc = subprocess.run(
                ["git", "show", "--no-renames", "--name-only", "--format=", sha],
                cwd=str(self.root),
                capture_output=True,
                text=True,
            )
            self._files[sha] = (
                [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                if proc.returncode == 0
                else []
            )
        return self._files[sha]

    def _contains(self, sha: str, symbol: str, paths: List[str]) -> bool:
        key = (sha, symbol)
        if key not in self._grep:
            text_paths = [
                p for p in paths if Path(p).suffix.lower() in TEXT_SUFFIXES
            ] or paths
            proc = subprocess.run(
                ["git", "grep", "-F", "-q", "-e", symbol, sha, "--", *text_paths],
                cwd=str(self.root),
                capture_output=True,
                text=True,
            )
            self._grep[key] = proc.returncode == 0
        return self._grep[key]

    def match(self, sha: str, refs: ChangeRefs) -> Optional[str]:
        """相交则返回命中的对象，否则 None。"""
        files = self.files(sha)
        if not files:
            return None
        basenames = {Path(f).name for f in files}
        for ref in refs.paths:
            if ref in files or Path(ref).name in basenames:
                return ref
        for symbol in refs.symbols:
            if any(symbol in Path(f).name for f in files):
                return symbol
            if self._contains(sha, symbol, files):
                return symbol
        return None


def _snapshot_header(
    lines: List[Tuple[int, str]],
) -> Tuple[bool, Optional[int], List[str], Set[int]]:
    """识别快照头。返回 (是否出现快照标记, 声明条数, 问题列表, 头部行集合)。"""
    head = lines[:30]
    head_text = "\n".join(raw for _number, raw in head)
    count_match = SNAPSHOT_COUNT_RE.search(head_text)
    # 只有显式标题或「共 N 条未复核」才算快照声明；
    # 历史横幅里的「不作现状依据」不能误触发快照头。
    present = SNAPSHOT_TITLE in head_text or count_match is not None
    if not present:
        return False, None, [], set()
    header_lines = {
        number
        for number, raw in head
        if any(marker in raw for marker in SNAPSHOT_MARKERS)
        or SNAPSHOT_COUNT_RE.search(raw)
    }

    problems: List[str] = []
    if SNAPSHOT_TITLE not in head_text:
        problems.append(f"缺少「{SNAPSHOT_TITLE}」")
    for required in SNAPSHOT_REQUIRED:
        if required not in head_text:
            problems.append(f"缺少「{required}」")
    declared: Optional[int] = None
    if count_match is None:
        problems.append("缺少「共 N 条未复核」计数")
    else:
        declared = int(count_match.group(1))
    return True, declared, problems, header_lines


def scan_file(
    path: Path,
    display: str,
    hash_cache: Dict[str, bool],
    resolver: Optional[CommandResolver] = None,
    inspector: Optional[CommitInspector] = None,
    stats: Optional[Dict[str, int]] = None,
    recheck_cache: Optional[
        Dict[Tuple[str, str], Optional[List[Tuple[str, str, List[str]]]]]
    ] = None,
) -> Tuple[List[Finding], List[Hit]]:
    findings: List[Finding] = []
    hits: List[Hit] = []
    lines = list(_iter_lines(path))
    bodies = _section_bodies(lines)
    recheck_cache = recheck_cache if recheck_cache is not None else {}
    for number, raw in lines:
        if RECHECK_MARK in raw and raw.lstrip().startswith("- **再检绑定**"):
            findings.extend(_recheck_findings(display, number, raw, recheck_cache))
    findings.extend(_entry_requires_recheck(display, lines, bodies))
    historical = bool(HISTORICAL_PATH_RE.match(display))
    banner = _banner_line(lines)
    if banner is not None and not historical:
        findings.append(
            Finding(
                "X1", "FAIL", display, banner,
                "「历史快照，不作现状依据」横幅只能用于 docs/learn/phase*/ 历史目录",
                lines[banner - 1][1].strip() if banner - 1 < len(lines) else "",
            )
        )
    in_731 = False
    group: Optional[str] = None

    present, declared, problems, header_lines = _snapshot_header(lines)
    status_entries: List[Tuple[int, str, str]] = []
    for index, (number, raw) in enumerate(lines):
        if number in header_lines:
            continue
        text = _strip_quoted(raw)
        window_text = _strip_quoted(
            "\n".join(item for _n, item in lines[max(0, index - 2): index + 3])
        )
        for status in _detect_statuses(text, window_text, raw):
            status_entries.append((number, raw, status))

    if present:
        header_line = min(header_lines) if header_lines else 1
        header_text = lines[header_line - 1][1].strip() if header_line - 1 < len(lines) else ""
        reason = ""
        if problems or declared is None:
            reason = "快照头无效：" + "；".join(problems or ["缺少「共 N 条未复核」计数"])
        elif declared != len(status_entries):
            reason = (
                f"快照头声明的未复核条数与实际不符：声明 {declared} 条，"
                f"实际 {len(status_entries)} 条"
            )
        if reason:
            # 头不合法就不认快照，原红项照常判红，避免「写错头就销红」。
            findings.append(Finding("R7", "FAIL", display, header_line, reason, header_text, historical))
        else:
            for number, raw, status in status_entries:
                findings.append(
                    Finding(
                        "SNAPSHOT", "SNAPSHOT", display, number,
                        f"当时状态快照、未逐条复核（{STATUS_LABELS.get(status, status)}）",
                        raw.strip(), historical,
                    )
                )
                if stats is not None:
                    stats["snapshot"] = stats.get("snapshot", 0) + 1
            return findings, []

    for index, (number, raw) in enumerate(lines):
        if number in header_lines:
            continue
        stripped = raw.strip()
        if re.match(r"^####\s+7\.3\.1", stripped):
            in_731, group = True, None
        elif in_731 and re.match(r"^(?:##|###)\s+([A-Z]\d?)\s*\.", stripped):
            # `## A.` / `### A2.` 都是账本里的分组标题，A2 归入 A。
            group = re.match(r"^(?:##|###)\s+([A-Z]\d?)\s*\.", stripped).group(1)[0]  # type: ignore[union-attr]
        elif re.match(r"^#{1,4}\s", stripped):
            in_731, group = False, None

        text = _strip_quoted(raw)
        # 原因/复跑命令允许落在相邻两行内（表格单元格会折行）。
        window = "\n".join(item for _n, item in lines[max(0, index - 2): index + 3])
        window_text = _strip_quoted(window)
        # 状态写在标题上时，举证材料在整节正文里，不能只看标题 ±2 行。
        context_text = _strip_quoted(bodies.get(number, window))
        statuses = _detect_statuses(text, window_text, raw)
        if not statuses:
            continue

        keys = _item_keys(text)
        if in_731 and group:
            keys.append(f"§7.3.1 {group}")
        for letter in GROUP_REF_RE.findall(text):
            keys.append(f"§7.3.1 {letter}")
        if in_731:
            # §7.3.1 的说明常折行：状态词与「B 的 8 个槽」可能落在相邻行。
            for letter in GROUP_REF_RE.findall(window_text):
                keys.append(f"§7.3.1 {letter}")
        keys = list(dict.fromkeys(keys))

        for status in statuses:
            hits.append(Hit(display, number, status))
            for key in keys:
                hits.append(Hit(display, number, status, key))

            if status == "fixed":
                hashes = HASH_RE.findall(text)
                if not hashes:
                    findings.append(
                        Finding(
                            "R1", "WARN" if (banner is not None and historical) else "FAIL",
                            display, number,
                            "已修必须带一次可复跑的验证记录（命令 + 实际输出）："
                            "出现「已转绿/已修/已修复/已落地/已解决」但同一行没有 commit hash",
                            raw.strip(), historical,
                        )
                    )
                    continue
                bad = [h for h in hashes if not _commit_exists(h, hash_cache)]
                for sha in bad:
                    findings.append(
                        Finding(
                            "R2", "WARN" if (banner is not None and historical) else "FAIL",
                            display, number,
                            f"hash `{sha}` 不是可解析的提交（git cat-file -e {sha}^{{commit}} 失败）",
                            raw.strip(), historical,
                        )
                    )
                verified = not bad
                if verified and inspector is not None:
                    refs = _change_refs(raw + "\n" + context_text)
                    hit = None
                    for sha in hashes:
                        hit = inspector.match(sha, refs)
                        if hit:
                            break
                    if hit is None:
                        level = "WARN" if (banner is not None and historical) else "FAIL"
                        if refs:
                            detail = (
                                f"引用对象={refs.brief()}；"
                                f"该提交改动文件={_brief(inspector.files(hashes[0]))}"
                            )
                        else:
                            detail = "本条没有可比的改动对象（无文件名/符号/测试名）"
                        findings.append(
                            Finding(
                                "R6", level, display, number,
                                f"hash {_brief(hashes, 2)} 与本条描述的改动对象无交集：" + detail,
                                raw.strip(), historical,
                            )
                        )
                        verified = False
                if verified and not (banner is not None and historical) and stats is not None:
                    stats["fixed_verified"] = stats.get("fixed_verified", 0) + 1
                command = None
                if not bad and not CMD_RE.search(context_text):
                    if resolver is not None:
                        command = resolver.resolve(text) or resolver.resolve(context_text)
                    if command:
                        # 自动映射到可执行命令：视为已举证，不记 WARN。
                        pass
                    elif resolver is None:
                        findings.append(
                            Finding(
                                "R3", "WARN", display, number,
                                "已修 + hash 有效，但本节/相邻行内没有可复跑命令（ctest -R / pytest）",
                                raw.strip(), historical,
                            )
                        )
                    else:
                        reason = resolver.reason(text + "\n" + context_text)
                        key = keys[0] if keys else ""
                        findings.append(
                            Finding(
                                "R3", "SKIP", display, number,
                                f"无复跑命令且无法自动映射：{reason}"
                                + (f"（键=`{key}`）" if key else ""),
                                raw.strip(), historical,
                            )
                        )
                if (
                    display == LEDGER_DISPLAY
                    and not bad
                    and (CMD_RE.search(context_text) or command)
                    and not OUTPUT_RE.search(context_text)
                ):
                    findings.append(
                        Finding(
                            "R9", "WARN" if (banner is not None and historical) else "FAIL",
                            display, number,
                            "已修记录缺「实际输出」：命令已有，但本节/相邻行内没有 "
                            "passed/failed/AssertionError/→ 等结果行",
                            raw.strip(), historical,
                        )
                    )
            elif status == "not_verified":
                if not REASON_RE.search(context_text):
                    findings.append(
                        Finding(
                            "R4", "FAIL", display, number,
                            "标了 `not_verified`/未复核，但本节/相邻行内没写为什么没验或缺什么才能验",
                            raw.strip(), historical,
                        )
                    )
    return findings, hits


def _cross_doc_conflicts(hits: List[Hit]) -> List[Finding]:
    """同一红项键在两处出现（可跨文档，也可同文档不同节），且状态不一致 → R5。"""
    by_key: Dict[str, List[Hit]] = defaultdict(list)
    for hit in hits:
        if hit.key and hit.status != "open":
            by_key[hit.key].append(hit)
    findings: List[Finding] = []
    for key, items in sorted(by_key.items()):
        locations = {(item.path, item.line) for item in items}
        statuses = {item.status for item in items}
        if len(locations) >= 2 and len(statuses) >= 2:
            files = {item.path for item in items}
            where = "；".join(
                f"{item.path}:{item.line}={STATUS_LABELS.get(item.status, item.status)}"
                for item in items
            )
            findings.append(
                Finding(
                    "R5", "FAIL", sorted(files)[0], items[0].line,
                    f"同一红项 `{key}` 在多处状态不一致：{where}",
                    "",
                )
            )
    return findings


def _load_exemptions() -> Dict[str, str]:
    path = SCAN_ROOT / EXEMPTION_FILE
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for _number, raw in _iter_lines(path):
        match = re.match(r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", raw.strip())
        if match:
            result[os_norm(match.group(1))] = match.group(2)
    return result


def _validate_exemption(path: Path, mechanism: str) -> Tuple[bool, str]:
    if mechanism not in ALLOWED_MECHANISMS:
        return False, f"未知豁免机制 `{mechanism}`"
    text = path.read_text(encoding="utf-8", errors="replace")
    if mechanism == "content-hash-ledger":
        if re.search(r"\|[^\n]*\|\s*[0-9a-f]{12}\s*\|", text):
            return True, ""
        return False, "声明 content-hash-ledger，但文件里没有 12 位行内容 hash 列"
    return False, "机制不可验证"


def _collect_docs(paths: Optional[List[str]]) -> List[Tuple[Path, str]]:
    if paths:
        candidates = [Path(p) for p in paths]
    else:
        candidates = sorted((SCAN_ROOT / "docs").rglob("*.md"))
    result: List[Tuple[Path, str]] = []
    for path in candidates:
        if not path.is_absolute():
            path = (SCAN_ROOT / path).resolve()
        if not path.exists() or path.suffix.lower() != ".md":
            continue
        try:
            display = path.resolve().relative_to(SCAN_ROOT).as_posix()
        except ValueError:
            display = path.as_posix()
        result.append((path, display))
    return result


def os_norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lower()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="红项生命周期机检（已修/not_verified 必须举证）")
    parser.add_argument("--paths", nargs="*", default=None, help="只扫指定 md 文件")
    parser.add_argument("--root", default=None, help="仓库根目录（默认本仓库，测试用）")
    parser.add_argument("--exemptions", default=None, help="豁免清单路径（默认 docs/red-lifecycle-exemptions.md）")
    parser.add_argument("--exclude", nargs="*", default=[], help="临时排除的路径（仍打印 SKIP）")
    parser.add_argument("--max-detail", type=int, default=3, help="每个文件最多打印几条明细")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    global SCAN_ROOT, EXEMPTION_FILE
    SCAN_ROOT = Path(args.root).resolve() if args.root else ROOT
    if args.exemptions:
        EXEMPTION_FILE = args.exemptions

    docs = _collect_docs(args.paths)
    exemptions = _load_exemptions()
    excluded = {os_norm(p) for p in args.exclude}
    hash_cache: Dict[str, bool] = {}
    recheck_cache: Dict[
        Tuple[str, str], Optional[List[Tuple[str, str, List[str]]]]
    ] = {}
    all_findings: List[Finding] = []
    all_hits: List[Hit] = []
    skip_entries: List[Dict[str, object]] = []
    errors: List[str] = []
    resolver = CommandResolver(SCAN_ROOT)
    inspector = CommitInspector(SCAN_ROOT)
    stats: Dict[str, int] = {"fixed_verified": 0, "snapshot": 0}

    for path, display in docs:
        try:
            findings, hits = scan_file(
                path, display, hash_cache, resolver, inspector, stats, recheck_cache
            )
        except OSError as exc:
            errors.append(f"{display}: {exc}")
            continue

        if display in excluded:
            skip_entries.append({"path": display, "count": len(findings), "mechanism": "--exclude"})
            continue

        if display in exemptions:
            mechanism = exemptions[display]
            valid, why = _validate_exemption(path, mechanism)
            if valid:
                skip_entries.append({"path": display, "count": len(findings), "mechanism": mechanism})
                continue
            all_findings.append(
                Finding("X0", "FAIL", display, 0, f"豁免无效：{why}（{EXEMPTION_FILE}）", "")
            )

        all_findings.extend(findings)
        all_hits.extend(hits)

    all_findings.extend(_cross_doc_conflicts(all_hits))

    fails = [f for f in all_findings if f.level == "FAIL"]
    warns = [f for f in all_findings if f.level == "WARN" and not f.historical]
    hist_warns = [f for f in all_findings if f.level == "WARN" and f.historical]
    r3_skips = [f for f in all_findings if f.level == "SKIP"]
    snapshots = [f for f in all_findings if f.level == "SNAPSHOT"]
    snapshot_files = sorted({f.path for f in snapshots})
    skip_total = sum(int(entry["count"]) for entry in skip_entries) + len(r3_skips)
    fixed_verified = stats["fixed_verified"]
    recheck_findings = [f for f in all_findings if f.rule == "R8"]
    recheck_pending = len({(f.path, f.line) for f in recheck_findings})
    ledger = (
        f"R1 实修 {fixed_verified} 条 / SNAPSHOT {len(snapshots)} 条 / "
        f"历史横幅 {len(hist_warns)} 条"
    )

    if args.json:
        print(
            json.dumps(
                {
                    "scanned": len(docs),
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "hist_warn_count": len(hist_warns),
                    "snapshot_count": len(snapshots),
                    "fixed_verified_count": fixed_verified,
                    "hist_banner_count": len(hist_warns),
                    "recheck_pending_count": recheck_pending,
                    "recheck_findings": [f.as_dict() for f in recheck_findings],
                    "ledger": ledger,
                    "skip_count": skip_total,
                    "skipped": skip_entries,
                    "r3_skips": [f.as_dict() for f in r3_skips],
                    "snapshot_files": snapshot_files,
                    "snapshots": [f.as_dict() for f in snapshots],
                    "by_file": _group_counts(fails, warns, hist_warns, snapshots),
                    "findings": [f.as_dict() for f in all_findings],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if fails else 0

    print(
        f"SCAN {len(docs)} 个文档；FAIL {len(fails)}，WARN {len(warns)}，"
        f"HIST-WARN {len(hist_warns)}（历史快照降级），SNAPSHOT {len(snapshots)}，"
        f"SKIP {skip_total}"
    )
    print(f"总账：{ledger}")
    print(
        f"再检：待复核/缺绑定 {recheck_pending} 条"
        "（绑定文件在验证提交后被后继提交碰过，或已修条目没有可自动再检的绑定）"
    )
    if snapshots:
        for path in snapshot_files:
            count = sum(1 for f in snapshots if f.path == path)
            print(f"  SNAPSHOT {count}（{path}，当时状态快照 / 未逐条复核）")
    if skip_total:
        for entry in skip_entries:
            print(
                f"  SKIP {entry['count']}（豁免：{entry['path']}，"
                f"机制={entry['mechanism']}，理由见 {EXEMPTION_FILE}）"
            )
        for finding in r3_skips:
            print(
                f"  SKIP 1（R3 无复跑命令：{finding.path}:{finding.line}，"
                f"{finding.message}）"
            )
    for err in errors:
        print(f"  ERROR {err}")
    print()

    groups = _group_counts(fails, warns, hist_warns, snapshots)
    if groups:
        print("按文件汇总（FAIL / WARN / HIST-WARN / SNAPSHOT）：")
        for row in groups:
            print(
                f"  {row['path']}  FAIL {row['fail']} / WARN {row['warn']} / "
                f"HIST {row['hist']} / SNAP {row['snap']}"
            )
        print()

    by_file: Dict[str, List[Finding]] = defaultdict(list)
    for finding in fails + warns + hist_warns + snapshots:
        by_file[finding.path].append(finding)
    print(f"明细（每文件最多 {args.max_detail} 条）：")
    if not by_file:
        print("  无")
    for path in sorted(by_file):
        print(f"  {path}")
        for finding in by_file[path][: args.max_detail]:
            if finding.level == "SNAPSHOT":
                level = "SNAPSHOT"
            elif finding.historical:
                level = "HIST-WARN"
            else:
                level = "FAIL" if finding.level == "FAIL" else "WARN"
            print(f"    {level} {path}:{finding.line} [{finding.rule}] {finding.message}")
        remaining = len(by_file[path]) - args.max_detail
        if remaining > 0:
            print(f"    …… 另有 {remaining} 条同类，用 --max-detail 展开或 --json 取全量")
    print()
    if fails:
        print("结论：FAIL —— 先把清单交各 owner，不要在扫描时顺带改别人的段落。")
        return 1
    print("结论：PASS（WARN / HIST-WARN 不阻断）")
    return 0


def _group_counts(
    fails: List[Finding],
    warns: List[Finding],
    hist_warns: List[Finding],
    snapshots: Optional[List[Finding]] = None,
) -> List[Dict[str, object]]:
    fail_counts: Dict[str, int] = defaultdict(int)
    warn_counts: Dict[str, int] = defaultdict(int)
    hist_counts: Dict[str, int] = defaultdict(int)
    snap_counts: Dict[str, int] = defaultdict(int)
    for finding in fails:
        fail_counts[finding.path] += 1
    for finding in warns:
        warn_counts[finding.path] += 1
    for finding in hist_warns:
        hist_counts[finding.path] += 1
    for finding in snapshots or []:
        snap_counts[finding.path] += 1
    paths = sorted(
        set(fail_counts) | set(warn_counts) | set(hist_counts) | set(snap_counts)
    )
    return [
        {
            "path": path,
            "fail": fail_counts.get(path, 0),
            "warn": warn_counts.get(path, 0),
            "hist": hist_counts.get(path, 0),
            "snap": snap_counts.get(path, 0),
        }
        for path in paths
    ]


if __name__ == "__main__":
    sys.exit(main())
