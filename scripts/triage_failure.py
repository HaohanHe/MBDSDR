# SPDX-License-Identifier: MIT
"""
scripts/triage_failure.py
=========================
失败归因助手：判断一条失败是不是「他线在途半成品」造成的假红。

判定式（三条同时成立才算命中，缺一条降级为「不确定」）：

    A. 异常类型落在导入期/构造期集合
       {AttributeError, NameError, ImportError, ModuleNotFoundError,
        SyntaxError, IndentationError}
    B. 出错符号在 ``git show HEAD:<定义文件>`` 里不存在
    C. 该定义文件在工作区是脏的（``git status --porcelain`` 非空）

背景（2026-11-11，本仓实测第 4 例）：设置线的「调用」与「定义」分两次写盘，
``refresh_font_scale_note`` 只存在于工作区未提交的 +23 行，HEAD 0 命中；
守门用例的子进程在该窗口内导入 ``settings_panel`` 拿到半成品，报
``AttributeError``。报错里只有方法名，``tests/`` 下 ``git grep`` 与
``pytest --collect-only`` 都找不到对应 nodeid——这类红长得像产品缺陷，
实际是别人的在途编辑。

用法::

    python scripts/triage_failure.py --stderr child_stderr.txt
    python scripts/triage_failure.py --junitxml .pytest_cache/junit.xml
    python scripts/triage_failure.py --stderr -        # 从 stdin 读

**纪律（硬约束）**：

* 只打印结论行，不产生任何其它输出；
* 恒以退出码 0 结束——它不是门禁，绝不改变测试的红绿；
* 拿不准就输出「不确定」，不猜。

「不确定」的三种真实来源：动态属性注入（``__getattr__``）、符号提取失败、
符号不在 HEAD 但工作区干净（更像已提交代码的接口漂移，不是在途半成品）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

#: 导入期/构造期异常：出现即可能是「模块语法完整但契约不完整的中间态」。
IMPORT_CONSTRUCT_ERRORS = {
    "AttributeError", "NameError", "ImportError", "ModuleNotFoundError",
    "SyntaxError", "IndentationError",
}

_FRAME_RE = re.compile(r'^\s*File "(.+?)", line (\d+)', re.M)
_EXC_RE = re.compile(
    r"^(" + "|".join(sorted(IMPORT_CONSTRUCT_ERRORS)) + r")\s*:\s*(.*)$", re.M)
_ATTR_RE = re.compile(r"'([A-Za-z_]\w*)' object has no attribute '([A-Za-z_]\w*)'")
_NAME_RE = re.compile(r"name '([A-Za-z_]\w*)' is not defined")
_IMPORT_RE = re.compile(r"cannot import name '([A-Za-z_]\w*)'")


@dataclass
class Verdict:
    kind: str                 # "in_flight" | "uncertain"
    path: str
    symbol: str
    reason: str = ""

    def line(self) -> str:
        if self.kind == "in_flight":
            return (f"[triage] 疑似在途半成品：{self.path} 的 {self.symbol} "
                    f"只存在于工作区，HEAD 无定义；先确认写盘者，不要报产品红。")
        return (f"[triage] 不确定：{self.path} 的 {self.symbol} — {self.reason}；"
                f"请人工确认，不要据此判定产品红。")


def _git(repo: Path, *args: str) -> Optional[str]:
    """跑一条 git；不可用/出错返回 None（调用方据此静默）。"""
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _head_text(repo: Path, rel: str) -> Optional[str]:
    return _git(repo, "show", f"HEAD:{rel}")


def _is_dirty(repo: Path, rel: str) -> bool:
    out = _git(repo, "status", "--porcelain", "--", rel)
    return bool(out and out.strip())


def _find_class_file(repo: Path, cls: str) -> Optional[str]:
    """在 HEAD 里找 ``class <cls>`` 的定义文件（优先 desktop/ 与 mbdsdr_ai/）。"""
    out = _git(repo, "grep", "-l", "-E", rf"^\s*class\s+{cls}\b", "HEAD", "--", "*.py")
    if not out:
        return None
    files = [ln.split(":", 1)[1].strip() for ln in out.splitlines() if ":" in ln]
    if not files:
        return None
    for prefix in ("desktop/", "mbdsdr_ai/"):
        for f in files:
            if f.startswith(prefix):
                return f
    return files[0]


def _symbol_in_text(text: Optional[str], symbol: str) -> bool:
    if not text:
        return False
    pat = re.compile(
        rf"^\s*(?:async\s+)?def\s+{re.escape(symbol)}\b"
        rf"|^\s*{re.escape(symbol)}\s*(?::[^=\n]*)?=",
        re.M)
    return bool(pat.search(text))


def _rel(repo: Path, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(repo.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _extract(text: str):
    """返回 (异常名, 符号, 提示文件) 或 (异常名, None, 文件)。"""
    m = _EXC_RE.search(text)
    if not m:
        return None, None, None
    exc, msg = m.group(1), m.group(2)

    frames = _FRAME_RE.findall(text)
    last_file = frames[-1][0] if frames else None

    if exc == "AttributeError":
        a = _ATTR_RE.search(msg)
        return exc, (a.group(2) if a else None), last_file
    if exc == "NameError":
        n = _NAME_RE.search(msg)
        return exc, (n.group(1) if n else None), last_file
    if exc in ("ImportError", "ModuleNotFoundError"):
        n = _IMPORT_RE.search(msg)
        return exc, (n.group(1) if n else None), last_file
    # SyntaxError / IndentationError：没有符号可比对，只能看文件脏不脏
    return exc, None, last_file


def triage_text(text: str, repo: Path) -> List[Verdict]:
    out: List[Verdict] = []
    exc, symbol, hint_file = _extract(text)
    if exc is None:
        return out

    if symbol is None:
        if hint_file is None:
            out.append(Verdict("uncertain", "<unknown>", f"（{exc}）",
                               "异常里定位不到任何文件"))
            return out
        rel = _rel(repo, hint_file)
        if exc in ("SyntaxError", "IndentationError"):
            # 语法错没有符号可比对，只能看文件脏不脏
            if _is_dirty(repo, rel):
                out.append(Verdict("in_flight", rel, f"（{exc}，语法不完整）"))
            else:
                out.append(Verdict("uncertain", rel, f"（{exc}）",
                                   "文件干净，语法错更可能来自已提交代码"))
            return out
        # AttributeError/NameError/ImportError 却没取到符号 → 不猜
        out.append(Verdict("uncertain", rel, f"（{exc}）",
                           "无法从异常文本提取符号，无法与 HEAD 比对"))
        return out

    # 符号的归属文件：类定义文件优先，退回 traceback 最后一帧
    attr = _ATTR_RE.search(_EXC_RE.search(text).group(2))
    rel = None
    if attr:
        cls_file = _find_class_file(repo, attr.group(1))
        if cls_file:
            rel = cls_file
    if rel is None and hint_file:
        rel = _rel(repo, hint_file)
    if rel is None:
        out.append(Verdict("uncertain", "<unknown>", symbol, "无法定位定义文件"))
        return out

    # 动态属性注入：类里自己定义了 __getattr__，该符号本就不该静态存在
    wt = repo / rel
    if wt.is_file():
        try:
            if re.search(r"^\s*def __getattr__\b", wt.read_text(encoding="utf-8"), re.M):
                out.append(Verdict("uncertain", rel, symbol,
                                   "类里定义了 __getattr__（动态注入），不能按缺失判定"))
                return out
        except OSError:
            pass

    head = _head_text(repo, rel)
    if head is None:
        out.append(Verdict("uncertain", rel, symbol, "读不到 HEAD 版本"))
        return out
    if _symbol_in_text(head, symbol):
        return out          # 符号在 HEAD 里存在 → 不发声，交给人看真缺陷
    # 符号现在已写进工作区：半成品窗口已关闭，这条错更可能来自旧状态
    try:
        wt_text = wt.read_text(encoding="utf-8") if wt.is_file() else ""
    except OSError:
        wt_text = ""
    if _symbol_in_text(wt_text, symbol):
        out.append(Verdict("uncertain", rel, symbol,
                           "符号现在已在工作区，半成品窗口已关闭（错误可能来自旧状态）"))
        return out
    if _is_dirty(repo, rel):
        out.append(Verdict("in_flight", rel, symbol))
    else:
        out.append(Verdict("uncertain", rel, symbol,
                           "符号不在 HEAD，但工作区干净——更像已提交代码的接口漂移"))
    return out


def _texts_from_junit(path: Path) -> Iterable[str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    chunks: List[str] = []
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            for node in case.iter(tag):
                chunks.append((node.get("message") or "") + "\n" + (node.text or ""))
    return chunks


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="失败归因：在途半成品 vs 真缺陷")
    ap.add_argument("--junitxml", action="append", default=[])
    ap.add_argument("--stderr", action="append", default=[])
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args(argv)

    repo = Path(args.repo_root)
    chunks: List[str] = []
    for p in args.stderr:
        if p == "-":
            chunks.append(sys.stdin.read())
        else:
            try:
                chunks.append(Path(p).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    for p in args.junitxml:
        chunks.extend(_texts_from_junit(Path(p)))

    seen = set()
    for chunk in chunks:
        for v in triage_text(chunk, repo):
            key = (v.kind, v.path, v.symbol)
            if key in seen:
                continue
            seen.add(key)
            print(v.line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
