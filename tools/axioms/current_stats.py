# SPDX-License-Identifier: MIT
"""`docs/axioms.md` 当前计数的唯一生成器（按基准 sha 钉住）。

文档里只允许有一个「当前计数」块；块内文字由本模块生成，命令：

    python -m tools.axioms.current_stats

为什么按基准钉住
----------------
旧口径是「文档数字 == 工具当次输出」，而工具扫的是工作树：别人每提交一笔
改变扫描面的代码，这个判据就变红一次。长期红等于没有红——大家会学会忽略它。

现在块内同时记下**基准 sha**、**生成时刻**与**校验指纹**（计数正文的
sha256）：判据比对的是「基准生成时的工具输出」，不是浮动 HEAD。HEAD 前进
但基准没更新时只提示重跑，不改退出码；只有同一基准下文档数字与工具输出
不一致（含手改数字）才判红。

生成器仍是单一事实源：采集走 ``survey.collect_all``，豁免走
``model.load_waivers/apply_waivers``，汇总走 ``Report8.summary``，与
``scripts/axioms.py`` 是同一条路径。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.checks import skip_report  # noqa: E402
from tools.axioms.model import (  # noqa: E402
    DEFAULT_WAIVER_FILE,
    Repo,
    apply_waivers,
    load_waivers,
)
from tools.axioms.report8 import Report8  # noqa: E402
from tools.axioms.survey import collect_all  # noqa: E402

BEGIN_MARK = "<!-- axioms-current-stats:begin"
END_MARK = "<!-- axioms-current-stats:end -->"
FINGERPRINT_KEY = "校验指纹 sha256:"
DOC_REL_PATH = os.path.join("docs", "axioms.md")
#: git pathspec 的分隔符是正斜杠；显式归一化，不依赖 Git for Windows 的路径转换。
DOC_POSIX_PATH = DOC_REL_PATH.replace(os.sep, "/")

#: 基准过期上限（双条件、先到者）：超过任一，判据从「提示」升级为红。
#: 24 小时：正常一天之内会重生成一次；再久「当前计数」就跨过了发布/排班
#:   节奏，继续沿用旧数会直接误导对「当前存量」的判断。
#: 200 笔提交：本仓多线并发，一天约几十到上百笔；200 笔≈两天以上的代码面
#:   变化，此时基准那棵树与 HEAD 早已不是同一回事。
MAX_STALE_HOURS = 24.0
MAX_STALE_COMMITS = 200

_MARKER_RE = re.compile(
    re.escape(BEGIN_MARK)
    + r"（由 python -m tools\.axioms\.current_stats 生成；"
    r"基准 ([^\s）]{1,60}) 生成于 ([^）]+)） -->\n(.*?)\n"
    + re.escape(END_MARK),
    re.S,
)


def current_summary(root=None):
    """跑一次全量采集 + 豁免，返回 ``Report8.summary()`` 的字典。"""
    repo = Repo(root or ROOT)
    axioms, dims = collect_all(repo)
    waivers = load_waivers(repo, DEFAULT_WAIVER_FILE)
    for dim in dims:
        apply_waivers(dim.findings, waivers)
    return Report8(axioms, dims, [], skip_report()).summary()


def counts_text(summary=None, root=None) -> str:
    """两行计数正文（不含标记与指纹）。"""
    s = summary if summary is not None else current_summary(root)
    dims = " / ".join(
        f"{dim} {s['by_dimension'][dim]['total']}"
        for dim in sorted(s["by_dimension"])
    )
    return (
        f"违规 {s['total']} 条：应修 {s['must_fix']}，复核 {s['review']}，"
        f"豁免 {s['waived']}。\n{dims}。"
    )


def fingerprint(body: str) -> str:
    """计数正文的 sha256：用来发现「正文被手改、指纹没跟着改」。"""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def render_block(root=None) -> str:
    """生成文档块内的正文 + 指纹行（不含首尾标记）。"""
    body = counts_text(root=root)
    return f"{body}\n{FINGERPRINT_KEY}{fingerprint(body)}"


def render_begin_marker(baseline_sha: str, generated_at: str) -> str:
    return (
        f"{BEGIN_MARK}（由 python -m tools.axioms.current_stats 生成；"
        f"基准 {baseline_sha} 生成于 {generated_at}） -->"
    )


def git_head(root=None) -> str:
    """当前 HEAD sha；取不到返回空串（判据据此退化为「只看指纹」）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root or ROOT,
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def worktree_dirty(root=None) -> bool:
    """工作树是否有未提交的已跟踪改动（不看未跟踪文件）。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root or ROOT, capture_output=True, text=True, check=True)
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return True


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def age_hours(generated_at: str, now: Optional[datetime] = None) -> Optional[float]:
    """基准生成时刻距今多少小时；解析不了返回 None（不据此判红）。"""
    try:
        stamp = datetime.fromisoformat(str(generated_at).strip())
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    ref = now or datetime.now().astimezone()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - stamp).total_seconds() / 3600.0


def commits_since(root=None, baseline_sha: str = "", head: str = "HEAD") -> Optional[int]:
    """基准之后「改了扫描面」的提交笔数。

    只重写计数块文档的提交（如重生成本块的那一笔）不算——它不改变被统计的
    输入，基准因此仍然对得上，不必提示重跑。
    """
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", f"{baseline_sha}..{head}", "--", ".",
             f":(exclude){DOC_POSIX_PATH}"],
            cwd=root or ROOT, capture_output=True, text=True, check=True)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


@dataclass(frozen=True)
class Block:
    baseline_sha: str
    generated_at: str
    body: str
    fingerprint: str


def parse_block(text: str) -> Optional[Block]:
    """解析文档里的计数块；缺块或旧格式（无基准）返回 None。"""
    match = _MARKER_RE.search(text)
    if not match:
        return None
    raw = match.group(3).strip()
    lines = raw.splitlines()
    fp = ""
    if lines and lines[-1].strip().startswith(FINGERPRINT_KEY):
        fp = lines[-1].strip()[len(FINGERPRINT_KEY):].strip()
        body = "\n".join(lines[:-1]).strip()
    else:
        body = raw
    return Block(match.group(1), match.group(2).strip(), body, fp)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    error: str = ""
    hint: str = ""


def verify_doc(text: str, *, head_sha: str,
               tool_body: Optional[str] = None,
               stale_commits: Optional[int] = None,
               now: Optional[datetime] = None) -> VerifyResult:
    """按基准校验文档计数块。

    * 块内数字与块内指纹不符 → 红（手改数字）；
    * 基准 sha != 当前 HEAD：先看是否超过过期上限（24 小时 / 200 笔提交，
      ``MAX_STALE_HOURS`` / ``MAX_STALE_COMMITS``）——超过即红；未超过只提示。
      基准之后只有「重写本计数块」的提交时视为未过期；
    * 基准 sha == 当前 HEAD 且给了 ``tool_body`` → 与工具当次输出逐字比对。
    """
    blk = parse_block(text)
    if blk is None:
        return VerifyResult(
            False,
            error=("docs/axioms.md 缺少带基准的计数块（旧格式或未生成）。"
                   "请重跑 `python -m tools.axioms.current_stats` 更新。"))
    if not blk.fingerprint:
        return VerifyResult(
            False, error="计数块缺校验指纹行，无法判定是否被手改；请重跑生成器。")
    if fingerprint(blk.body) != blk.fingerprint:
        return VerifyResult(
            False,
            error=("文档计数与基准生成时的工具输出不一致（数字被手改？）。"
                   "请重跑 `python -m tools.axioms.current_stats`，不要手改。"))
    if blk.baseline_sha != head_sha:
        age = age_hours(blk.generated_at, now)
        over_age = age is not None and age > MAX_STALE_HOURS
        over_commits = (stale_commits is not None
                        and stale_commits > MAX_STALE_COMMITS)
        if over_age or over_commits:
            parts = []
            if age is not None:
                parts.append(f"距生成 {age:.1f} 小时（上限 {MAX_STALE_HOURS:g} 小时）")
            if stale_commits is not None:
                parts.append(f"落后 {stale_commits} 笔提交（上限 {MAX_STALE_COMMITS} 笔）")
            return VerifyResult(
                False,
                error=(f"计数基准已过期：{'，'.join(parts) or '超过上限'}。"
                       "请重跑 `python -m tools.axioms.current_stats` 并随代码提交"
                       "更新基准。"))
        if stale_commits == 0:
            return VerifyResult(True)
        return VerifyResult(
            True,
            hint=(f"HEAD 已前进（基准 {blk.baseline_sha[:12]} → 当前 "
                  f"{(head_sha or '?')[:12]}）。请重跑 "
                  "`python -m tools.axioms.current_stats` 更新基准；本次不判红。"))
    if tool_body is not None and tool_body.strip() != blk.body:
        return VerifyResult(
            False,
            error=("同一基准下文档计数与工具当次输出不一致：\n"
                   f"文档：\n{blk.body}\n工具：\n{tool_body.strip()}"))
    return VerifyResult(True)


def verify_repo(root=None) -> VerifyResult:
    """按真实仓库状态校验：仅当基准 == HEAD 且工作树干净时才现场重跑工具。

    工作树有未提交改动时，重跑结果不属于基准那棵树，不能拿它判红——只留
    指纹校验与提示。
    """
    root = root or ROOT
    with open(os.path.join(root, DOC_REL_PATH), encoding="utf-8") as fh:
        text = fh.read()
    head = git_head(root)
    blk = parse_block(text)
    tool_body = None
    stale_commits = None
    if blk is not None and head and blk.baseline_sha == head:
        if worktree_dirty(root):
            res = verify_doc(text, head_sha=head)
            return VerifyResult(
                res.ok, res.error,
                res.hint or "工作树有未提交改动，未现场重跑工具（只校验块内指纹）。")
        tool_body = counts_text(root=root)
    elif blk is not None and head:
        stale_commits = commits_since(root, blk.baseline_sha)
    return verify_doc(text, head_sha=head, tool_body=tool_body,
                      stale_commits=stale_commits)


def update_doc(root=None, doc_path=None) -> Block:
    """重跑采集并把计数块（基准 sha、生成时刻、指纹、正文）写回文档。"""
    root = root or ROOT
    doc_path = doc_path or os.path.join(root, DOC_REL_PATH)
    with open(doc_path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    body = counts_text(root=root)
    marker = render_begin_marker(git_head(root) or "unknown", now_stamp())
    block = f"{marker}\n{body}\n{FINGERPRINT_KEY}{fingerprint(body)}\n{END_MARK}"
    new_text, count = _MARKER_RE.subn(lambda _m: block, text, count=1)
    if not count:
        raise SystemExit(f"{doc_path} 找不到 axioms-current-stats 块，拒绝写入")
    with open(doc_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)
    parsed = parse_block(new_text)
    assert parsed is not None
    return parsed


if __name__ == "__main__":
    updated = update_doc()
    print(f"已更新 {DOC_REL_PATH}：基准 {updated.baseline_sha}，"
          f"生成于 {updated.generated_at}。")
    print(updated.body)
