#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""提交范围检查：列出某提交改了哪些文件，并提示「该文件在提交时刻是否被多线持有」。

能做到的（真检查）：
  * 从 `git show --name-status` 列出该提交改动的全部文件；
  * 从 `file_claim.py` 的认领台账 `claims.json` 里查每个文件的持有历史：
    若在**提交那一刻**（提交者本地时间，`%ct`）有 ≥2 个不同 owner 的 claim
    处于活跃期（acquired_at ≤ t ≤ released_at），标 `MULTI`；
  * 若只有一个 owner 活跃 → `ONE`；台账里没有记录 → `NONE`；
  * 附「整文件替换」提示：`--numstat` 的新增行数等于该文件新版本总行数时，
    说明这次提交把文件内容整段换掉了（正是 `git commit -- <path>` 的事故形态）。

做不到的（明确说做不到，不造假）：
  * **无法从 `git show` 判断某个 hunk 属于哪条线**。Git 只记录统一 diff，
    不记录「这一行是谁写的」；任何声称能从提交内容反推 hunk 归属的检测都是
    假的。
  * 台账只在 `file_claim.py` 落地之后才开始积累。安装之前的提交（包括
    2026-11-05 的三次事故）一律 `NO_RECORD`，本工具不会假装知道。
  * 提交时刻可能早于台账记录，也可能提交后有人续租——只能按时间区间判断。

用法：
  python scripts/check_commit_scope.py HEAD
  python scripts/check_commit_scope.py <hash> [--json]
  python scripts/check_commit_scope.py --staged [--json]
  python scripts/check_commit_scope.py --post-commit HEAD --declared-index <私有 index>

  `--staged` 供 pre-commit 钩子调用：读取 `git diff --cached` 的路径，检查这些
  路径当前被几个**仍在 TTL 内**的 owner 持有；≥2 个 owner 即拒绝提交（退出码 1）。

  `--post-commit` 供 post-commit 钩子/人工复核调用：逐 path 比较
  `git rev-parse HEAD:<path>` 与声明私有 index 里的 `:<path>` blob。
  不等 → 说明本次提交按**工作区**取内容，绕过了私有 index（形态⑩：
  `git commit -- <paths>` 的 pathspec 覆盖 index），打印两侧 blob 并拒绝（退出码 1）。
  未声明私有 index 时明确 SKIP（不假报）。

  `--staged --expect <路径...>`（或环境变量 `MBDSDR_COMMIT_PATHS`，换行/`;` 分隔）
  再加一道：**提交的文件集合必须全部落在声明清单内，多一个就拒**，并把多出来的
  路径逐条打印。来源清单没声明时不启用这一条（保持旧行为，不假报）。

  `--staged --declared-index <路径>`（或环境变量 `MBDSDR_DECLARED_INDEX`）
  再加一道：会打印**实际读到的 index 路径**；若声明了私有 index 而实际读到的是
  共享 `.git/index`，直接拒并打印 DECLARED / ACTUAL 两个路径。这一条专治
  「PowerShell `$env:GIT_INDEX_FILE` 没落到子进程」（事故 `16f075f3`：
  计划只提交 10 个文件，闸门读共享 index，一次夹带 26 个别线文件）。

  暂存里的删除条目（`D`）必须逐条声明为**意图删除**（`--intent-delete <路径>`，
  可重复；或环境变量 `MBDSDR_INTENT_DELETIONS`，换行 / `;` 分隔；`--expect`
  声明清单里出现的删除条目同样算已声明）。未声明的删除条目一律拒并打印
  `DELETE <path>`——fail-closed：全仓带删除的提交占比极低，声明成本可接受；
  而漏掉这一条，陈旧私有 index 的幻影删除就会直接进库（`35db7a13` 等 4 起）。
  `safe_commit.py` 会把 `--files` 里「HEAD 有、新索引没有」的路径自动导出为
  意图删除集，正常删文件不需要手写声明。

  `--staged` 还有一道**内容一致性**闸：每个非删除的暂存条目，index 里的 blob
  必须与工作树当前内容一致；只要 `git diff --quiet -- <path>` 报 1 就拒，
  并打印 `DIRTY <path>`。这一条不看 owner、不看声明清单——**没有 claim 的
  他人暂存文件也不能放行**。事故 `bc0c75e1`：从共享 index 提交了 4 份别人
  文档的旧 blob，工作树是新内容，行号重定位被整体回退，而当时闸门判「允许提交」。

  2026-11-12 裁定：**闸门管的是「共享 index 里的旧快照」，不是「暂存≠工作树」
  这个形态**，二者只在共享 index 路径上等价。因此：
  * 共享 index（未设置 `GIT_INDEX_FILE`，或它指向 `.git/index`）：DIRTY 一律
    拒绝，无任何豁免；
  * 私有 index（`GIT_INDEX_FILE` 指向别的文件）：允许差异，但每个有差异的
    文件必须有**逐文件显式声明**的排除 hunk（环境变量 `MBDSDR_EXCLUDE_HUNKS`，
    JSON：`[{"path","start","end","owner"}]`，由 `safe_commit.py --exclude-hunk`
    写入）。声明为空又有差异 → 拒（无法区分「排除了什么」与「自己的改动没
    add 全」）。不读通配、不认全局开关。
  放行时打印 `DIRTY(allowed) <path> — <范围>（<owner>）`，并把声明写进
  `MBDSDR_HOOK_LOG`（若设置）与 JSON 输出。

退出码：0 无 MULTI；1 发现 MULTI 或有未解释差异（需人工确认）；2 用法/git 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1])
DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)
REGISTRY_PATH = LOCKS_DIR / "claims.json"
EXCLUDE_ENV = "MBDSDR_EXCLUDE_HUNKS"
INTENT_ENV = "MBDSDR_INTENT_DELETIONS"


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def _canonical(path: str) -> str:
    return os.path.normcase(path.replace("\\", "/")).replace("\\", "/").lstrip("./")


def _load_registry() -> Dict[str, Any]:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"claims": {}}
    except (OSError, json.JSONDecodeError):
        print(f"WARN 台账不可读：{REGISTRY_PATH}", file=sys.stderr)
        return {"claims": {}}


def _owners_at(entry: Dict[str, Any], moment: float) -> List[str]:
    owners: List[str] = []
    for item in entry.get("history", []):
        start = item.get("acquired_at")
        end = item.get("released_at")
        if not isinstance(start, (int, float)):
            continue
        if start <= moment and (end is None or (isinstance(end, (int, float)) and end >= moment)):
            owner = str(item.get("owner", "?"))
            if owner not in owners:
                owners.append(owner)
    return owners


def _file_lines(sha: str, path: str) -> Optional[int]:
    try:
        text = _git("show", f"{sha}:{path}")
    except SystemExit:
        return None
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _numstat(sha: str) -> Dict[str, tuple]:
    result: Dict[str, tuple] = {}
    for line in _git("show", "--format=", "--numstat", "--no-renames", sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            result[path] = (int(added), int(deleted))
        except ValueError:
            result[path] = (-1, -1)  # 二进制
    return result


def check(ref: str, as_json: bool) -> int:
    sha = _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    meta = _git("show", "-s", "--format=%an%x00%ad%x00%ct%x00%s", "--date=iso", sha).strip()
    author, when, ctime, subject = meta.split("\x00", 3)
    moment = float(ctime)

    statuses: List[Dict[str, Any]] = []
    for line in _git("show", "--format=", "--name-status", "--no-renames", sha).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[-1].strip()
        statuses.append({"status": status, "path": path})

    numstat = _numstat(sha)
    registry = _load_registry().get("claims", {})

    rows: List[Dict[str, Any]] = []
    multi = 0
    for item in statuses:
        path = item["path"]
        entry = registry.get(_canonical(path), {})
        owners = _owners_at(entry, moment) if entry else []
        if len(owners) >= 2:
            verdict = "MULTI"
            multi += 1
        elif owners:
            verdict = "ONE"
        else:
            verdict = "NONE"

        added, _deleted = numstat.get(path, (-1, -1))
        total_new = _file_lines(sha, path) if added >= 0 else None
        whole_rewrite = bool(total_new and added >= total_new)

        rows.append(
            {
                "path": path,
                "status": item["status"],
                "verdict": verdict,
                "owners_at_commit": owners,
                "added": added,
                "whole_rewrite": whole_rewrite,
                "claim_history": len(entry.get("history", [])) if entry else 0,
            }
        )

    if as_json:
        print(
            json.dumps(
                {
                    "commit": sha,
                    "author": author,
                    "date": when,
                    "subject": subject,
                    "multi_claim": multi,
                    "files": rows,
                    "limitations": [
                        "无法从 git show 判断 hunk 归属（git 不记录逐行的作者）",
                        "台账只在 file_claim.py 落地后积累；更早提交一律 NO_RECORD",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if multi else 0

    print(f"COMMIT {sha}")
    print(f"  {when}  {author}  {subject}")
    print(f"  改动文件 {len(rows)} 个；提交时刻多线持有 {multi} 个")
    print()
    print(f"  {'判定':<7}{'状态':<5}{'文件':<46}{'提交时持有者':<24}备注")
    for row in rows:
        owners = ", ".join(row["owners_at_commit"]) if row["owners_at_commit"] else "-"
        note = "整文件替换" if row["whole_rewrite"] else ""
        print(f"  {row['verdict']:<7}{row['status']:<5}{row['path']:<46}{owners:<24}{note}")
    print()
    if multi:
        print("结论：存在 MULTI —— 提交时刻该文件被多条线同时持有，极可能带走了他线在途改动。")
        print("      请人工核对提交内容，不要以「提交成功了」当作归属正确。")
    else:
        print("结论：未发现 MULTI。注意 NONE 不等于「干净」，只等于「无台账记录」。")
    print()
    print("限制（明说）：")
    print("  1. git 不记录 hunk 作者，任何「从提交反推某 hunk 属于哪条线」都做不到，本工具不做。")
    print(f"  2. 台账 {REGISTRY_PATH} 从机制落地起积累；更早的提交无法判定，不伪造结论。")
    return 1 if multi else 0


def _revparse_optional(spec: str, index_path: Optional[str] = None) -> Optional[str]:
    """解析 rev（如 `HEAD:path` / `:path`），不存在时返回 None（不抛）。"""
    env = dict(os.environ)
    if index_path:
        env["GIT_INDEX_FILE"] = index_path
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", spec],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def check_post_commit(
    ref: str,
    as_json: bool,
    declared_index_arg: Optional[str] = None,
) -> int:
    """提交后核对：本次提交的每个 path 的 blob 是否等于私有 index 里同一 path 的 blob。

    这是形态⑩（私有 index + `git commit -- <paths>`，pathspec 按工作区取内容、
    绕过 index）的机械闸。相等 → 提交用的就是私有 index 的内容；不等 → 这个 path
    被工作区覆盖了，本次提交带进了不属于私有 index 的改动。
    """
    sha = _git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    declared = (
        (declared_index_arg or os.environ.get("MBDSDR_DECLARED_INDEX", "")).strip()
    )
    if not declared:
        print("SKIP 未声明私有 index（--declared-index / MBDSDR_DECLARED_INDEX），无法逐 path 比对。")
        return 0
    index_path = _index_abs(declared)
    if not os.path.exists(index_path):
        print(f"ERROR 声明的私有 index 不存在：{index_path}")
        return 2

    paths: List[tuple] = []
    for line in _git("show", "--format=", "--name-status", "--no-renames", sha).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        paths.append((parts[0].strip(), parts[-1].strip()))

    rows: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    for status, path in paths:
        head_blob = _revparse_optional(f"{sha}:{path}")
        index_blob = _revparse_optional(f":{path}", index_path=index_path)
        same = head_blob == index_blob
        row = {
            "path": path,
            "status": status,
            "head_blob": head_blob,
            "index_blob": index_blob,
            "match": same,
        }
        rows.append(row)
        if not same:
            mismatches.append(row)

    if as_json:
        print(
            json.dumps(
                {
                    "commit": sha,
                    "declared_index": index_path,
                    "paths": rows,
                    "mismatch": len(mismatches),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if mismatches else 0

    print(f"COMMIT {sha}")
    print(f"INDEX 声明 {index_path}")
    print(f"比对 {len(rows)} 个 path")
    for row in rows:
        head = row["head_blob"] or "（无）"
        idx = row["index_blob"] or "（无）"
        mark = "OK  " if row["match"] else "FAIL"
        print(f"  {mark} {row['status']:<2} {row['path']}")
        print(f"       HEAD  {head}")
        print(f"       INDEX {idx}")
    print()
    if mismatches:
        print(
            f"结论：{len(mismatches)} 个 path 的 HEAD blob 与私有 index 不一致 —— "
            "本次提交按工作区内容取了这些文件（pathspec 覆盖 index，形态⑩）。"
        )
        print("处置：不 reset；由该行归属线补正；在 docs/COMMIT-INCIDENT-CARD.md ⑩ 登记。")
        return 1
    print("结论：全部 path 与私有 index 一致。")
    return 0



def _active_claim(entry: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
    """返回仍在 TTL 内的 current claim；已过期或已释放返回 None。"""
    cur = entry.get("current")
    if not isinstance(cur, dict):
        return None
    try:
        heartbeat = float(cur.get("heartbeat_at") or cur.get("acquired_at") or 0)
        ttl_s = float(cur.get("ttl_min") or 0) * 60.0
    except (TypeError, ValueError):
        return None
    if ttl_s <= 0 or now - heartbeat > ttl_s:
        return None
    return cur


def _declared_paths(cli_expect: Optional[List[str]]) -> Optional[List[str]]:
    """本次提交声明要带的路径清单。

    来源优先级：`--expect` 命令行 > 环境变量 `MBDSDR_COMMIT_PATHS`
    （换行 / `;` 分隔）。两者都没有 → 返回 None，表示「未声明」，
    此时不做「多一个就拒」的检查（保持旧行为，不假报）。
    """
    raw: List[str] = []
    if cli_expect:
        raw = list(cli_expect)
    else:
        env_val = os.environ.get("MBDSDR_COMMIT_PATHS", "")
        if env_val.strip():
            raw = [x for x in env_val.replace(";", "\n").splitlines()]
    if not raw:
        return None
    return [_canonical(x.strip()) for x in raw if x.strip()]


def _intent_deletions(cli_intent: Optional[List[str]]) -> Optional[List[str]]:
    """本次提交显式声明的**意图删除**路径集。

    来源优先级：`--intent-delete`（可重复）> 环境变量
    `MBDSDR_INTENT_DELETIONS`（换行 / `;` 分隔）。两者都没有 → 返回 None
    （未声明），此时暂存里任何删除条目都按「未解释」拒绝（fail-closed）。
    """
    raw: List[str] = list(cli_intent or [])
    if not raw:
        env_val = os.environ.get(INTENT_ENV, "")
        if env_val.strip():
            raw = [x for x in env_val.replace(";", "\n").splitlines()]
    if not raw:
        return None
    return [_canonical(x.strip()) for x in raw if x.strip()]


def _index_abs(path: str) -> str:
    """把 index 路径归一成绝对路径（只用于显示与比较，不改大小写）。"""
    raw = (path or "").strip()
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = str(ROOT / raw)
    return os.path.abspath(raw)


def _normalize_index_path(path: str) -> str:
    """比较用的归一：Windows 上大小写与分隔符不敏感。"""
    return os.path.normcase(_index_abs(path))


def _worktree_dirty(paths: List[str]) -> List[str]:
    """index 内容与工作树不一致的路径（`git diff --quiet` 退出码 1）。

    事故 `bc0c75e1`：共享 index 里留着 4 份别人文档的**旧 blob**，工作树是
    新内容；提交按 index 走，于是重定位后的新行号被旧快照整体回退。当时
    闸门只看 owner 与声明清单，判「允许提交」。这里逐 path 比对，不看 owner。
    """
    dirty: List[str] = []
    for path in paths:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "--", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 1:
            dirty.append(path)
        elif proc.returncode != 0:
            raise SystemExit(
                f"git diff --quiet -- {path} 失败：{proc.stderr.strip()}"
            )
    return dirty


def _private_index() -> Optional[str]:
    """当前是否走私有 index；是则返回其绝对路径，共享 index 返回 None。

    判据只有一条：`GIT_INDEX_FILE` 已设置且**不是**共享 `.git/index`。
    shell 里把变量指回共享 index 不算私有 index，仍按 DIRTY 全拒处理。
    """
    raw = (os.environ.get("GIT_INDEX_FILE") or "").strip()
    if not raw:
        return None
    common = _git("rev-parse", "--git-common-dir").strip() or ".git"
    common_dir = Path(common)
    if not common_dir.is_absolute():
        common_dir = (ROOT / common_dir).resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (ROOT / candidate).resolve()
    if os.path.normcase(str(candidate)) == os.path.normcase(str(common_dir / "index")):
        return None
    return str(candidate)


def _excluded_hunks() -> Dict[str, List[Dict[str, Any]]]:
    """解析 `MBDSDR_EXCLUDE_HUNKS`：私有 index 提交里显式排除的 hunk。

    形态：`[{"path": "...", "start": 12, "end": 20, "owner": "..."}]`。
    任何解析失败都当「未声明」（宁可拒，不假报放行）。
    """
    raw = (os.environ.get(EXCLUDE_ENV) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"WARN {EXCLUDE_ENV} 不是合法 JSON，按未声明处理", file=sys.stderr)
        return {}
    if not isinstance(data, list):
        print(f"WARN {EXCLUDE_ENV} 不是列表，按未声明处理", file=sys.stderr)
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        path = _canonical(str(item.get("path") or ""))
        try:
            start = int(item["start"])
            end = int(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not path or start <= 0 or end < start:
            continue
        out.setdefault(path, []).append(
            {"start": start, "end": end, "owner": str(item.get("owner") or "").strip()}
        )
    return out


def _log_dirty_allowed(paths: List[str], exclusions: Dict[str, List[Dict[str, Any]]]) -> None:
    """把 DIRTY(allowed) 的排除声明写进钩子日志（只写日志，不改退出码）。"""
    log = (os.environ.get("MBDSDR_HOOK_LOG") or "").strip()
    if not log:
        return
    record = {
        "type": "DIRTY_ALLOWED",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "paths": paths,
        "exclude_hunks": {path: exclusions[_canonical(path)] for path in paths},
    }
    try:
        log_path = Path(log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"WARN 无法写 DIRTY_ALLOWED 日志：{exc}", file=sys.stderr)


def check_staged(
    as_json: bool,
    expect: Optional[List[str]] = None,
    declared_index_arg: Optional[str] = None,
    intent_arg: Optional[List[str]] = None,
) -> int:
    """检查暂存区：①跨线（≥2 个活跃 owner）拒；②超出声明清单的文件拒；
    ③声明的私有 index 与实际读到的 index 不一致拒；
    ④出现「未声明为意图删除」的删除条目时拒（陈旧私有 index 的幻影删除）；
    ⑤暂存 blob 与工作树不一致时拒（共享 index 里的旧快照会被一起提交）；
    私有 index 例外：必须在 `MBDSDR_EXCLUDE_HUNKS` 里逐文件声明排除了哪些
    hunk，声明为空又存在差异一律拒（2026-11-12 裁定，见模块 docstring）。

    第 ② 条是「私有 index 文件名撞车 / 复用旧 index」这类事故的机械闸：
    提交的文件集合必须**全部落在声明清单内**，多一个就拒并打印多出来的路径。
    第 ③ 条是「PowerShell 环境变量没落到子进程」的机械闸：`$env:GIT_INDEX_FILE`
    没生效时，闸门读到的是 `.git/index`；声明了私有 index 就必须拒，
    并打出两个路径对比。
    第 ④ 条是「陈旧私有 index 制造幻影删除」的机械闸：私有 index 若基于旧 HEAD
    `read-tree`，旧 HEAD 之后入库的文件在它看来就是删除（`D`），会被计入暂存。
    删除条目必须逐条落进意图删除集（`--intent-delete` /
    `MBDSDR_INTENT_DELETIONS`，或 `--expect` 清单），否则拒——**未声明时也拒**
    （2026-11-12 起 fail-closed；此前未声明清单时只告警，`35db7a13` 正是从
    这个缺口把守卫测试删掉的）。
    第 ⑤ 条是「旧 blob 被提交」的机械闸（事故 `bc0c75e1`）：共享 index 里留着
    别人文档的旧快照、工作树是新内容时，提交会把旧内容写回库并整体回退重定位。
    这一条逐 path 比对 index 与工作树，**无 claim 的文件同样适用**。
    """
    import time

    actual_index = _git("rev-parse", "--git-path", "index").strip()
    declared_index = (
        declared_index_arg
        or os.environ.get("MBDSDR_DECLARED_INDEX", "")
    ).strip()
    index_mismatch = bool(declared_index) and (
        _normalize_index_path(actual_index) != _normalize_index_path(declared_index)
    )

    now = time.time()
    items: List[tuple] = []
    for line in _git("diff", "--cached", "--name-status", "--no-renames").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        path = parts[-1].strip()
        if path:
            items.append((status, path))
    paths = [path for _status, path in items]
    status_of = {path: status for status, path in items}
    deletions = [path for status, path in items if status.startswith("D")]
    registry = _load_registry().get("claims", {})

    rows: List[Dict[str, Any]] = []
    owners: List[str] = []
    for path in paths:
        entry = registry.get(_canonical(path), {})
        cur = _active_claim(entry, now) if entry else None
        if cur is None:
            rows.append(
                {"path": path, "status": status_of[path], "owner": None, "remaining_min": None}
            )
            continue
        owner = str(cur.get("owner", "?"))
        try:
            heartbeat = float(cur.get("heartbeat_at") or cur.get("acquired_at") or now)
            ttl_s = float(cur.get("ttl_min") or 0) * 60.0
        except (TypeError, ValueError):
            heartbeat, ttl_s = now, 0.0
        remaining = max(0.0, (heartbeat + ttl_s - now) / 60.0)
        if owner not in owners:
            owners.append(owner)
        rows.append(
            {
                "path": path,
                "status": status_of[path],
                "owner": owner,
                "remaining_min": round(remaining, 1),
            }
        )

    declared = _declared_paths(expect)
    intent = _intent_deletions(intent_arg)
    allowed_deletions = set(declared or []) | set(intent or [])
    extras: List[str] = []
    if declared is not None:
        allowed = set(declared)
        extras = [p for p in paths if _canonical(p) not in allowed]
    unexplained_deletions = [
        p for p in deletions if _canonical(p) not in allowed_deletions
    ]

    dirty = _worktree_dirty([p for p in paths if not status_of[p].startswith("D")])
    private_index = _private_index()
    exclusions = _excluded_hunks() if private_index else {}
    allowed_dirty = [p for p in dirty if exclusions.get(_canonical(p))]
    blocked_dirty = [p for p in dirty if p not in allowed_dirty]

    multi = len(owners) >= 2
    if as_json:
        print(
            json.dumps(
                {
                    "staged": paths,
                    "deletions": deletions,
                    "intent_deletions": intent,
                    "unexplained_deletions": unexplained_deletions,
                    "owners": owners,
                    "multi_owner": multi,
                    "declared": declared,
                    "extras": extras,
                    "worktree_dirty": dirty,
                    "private_index": _index_abs(private_index) if private_index else None,
                    "dirty_allowed": allowed_dirty,
                    "dirty_blocked": blocked_dirty,
                    "exclude_hunks": exclusions,
                    "actual_index": _index_abs(actual_index),
                    "declared_index": _index_abs(declared_index),
                    "index_mismatch": index_mismatch,
                    "files": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return (
            1
            if (
                multi
                or extras
                or index_mismatch
                or unexplained_deletions
                or blocked_dirty
            )
            else 0
        )

    print(f"INDEX 实际读取 {_index_abs(actual_index)}")
    if declared_index:
        print(f"INDEX 声明     {_index_abs(declared_index)}")
    print()
    print(f"STAGED {len(paths)} 个文件")
    for row in rows:
        if row["owner"]:
            print(
                f"  {row['status']:<2} {row['path']:<44} owner={row['owner']}"
                f" 剩余 TTL {row['remaining_min']}min"
            )
        else:
            print(f"  {row['status']:<2} {row['path']:<44} owner=-（无活跃 claim）")
    print()
    if declared is not None:
        print(f"声明清单 {len(declared)} 个：{', '.join(declared)}")
        print()

    failed = False
    if index_mismatch:
        print("拒绝：声明了私有 index，但闸门实际读到的是另一个 index（多为此类事故）：")
        print(f"  DECLARED {_index_abs(declared_index)}")
        print(f"  ACTUAL   {_index_abs(actual_index)}")
        print("  原因：PowerShell 里 `$env:GIT_INDEX_FILE` 没落到子进程（子进程读到共享 index），")
        print("        于是闸门检查的是别人的暂存区，提交会按共享 index 走。")
        print("  改法：私有 index 一律显式传参，禁止依赖 shell 里 export 的变量——")
        print("        `GIT_INDEX_FILE=<path> git commit ...` 或 subprocess.run(env={...})。")
        failed = True
    if unexplained_deletions:
        print(
            f"拒绝：暂存区有 {len(unexplained_deletions)} 条删除，但没有声明为意图删除"
            "（fail-closed：删除必须逐条声明，幻影删除正是这样进库的）："
        )
        for path in unexplained_deletions:
            print(f"  DELETE  {path}")
        print("  原因一（真删）：你确实要删这些文件，只是没有声明。")
        print("  原因二（幻影删除）：私有 index 基于旧 HEAD `read-tree`——旧 HEAD 之后")
        print("        入库的文件在这份索引里表现为删除（D），会被一起计入本次提交。")
        print("  真删的过法（任选其一，都在同一次提交里）：")
        print("    python scripts/check_commit_scope.py --staged --intent-delete <path>"
              " [--intent-delete <path> ...]")
        print('    $env:MBDSDR_INTENT_DELETIONS = "<path>;<path>"   # 换行或 ; 分隔')
        print("    python scripts/safe_commit.py --files ... --message-file ...")
        print("      （safe_commit 会把「HEAD 有、新索引没有」的路径自动导出为意图删除集）")
        print("  幻影删除的修法：不要复用旧索引文件——`git read-tree HEAD` 基于当前 HEAD")
        print("        重建私有 index 后重新 add；上面这些文件就不该出现在暂存里。")
        failed = True
    if allowed_dirty:
        print(
            f"DIRTY(allowed)：{len(allowed_dirty)} 个文件在私有 index 里与工作树不一致，"
            "已逐文件声明排除 hunk："
        )
        for path in allowed_dirty:
            ranges = exclusions[_canonical(path)]
            desc = ", ".join(
                f"{r['start']}-{r['end']}" + (f"（{r['owner']}）" if r["owner"] else "")
                for r in ranges
            )
            print(f"  DIRTY(allowed) {path} — 排除 hunk {desc}")
        print("  说明：共享 index 不认此形态；私有 index 只认逐文件显式声明。")
        _log_dirty_allowed(allowed_dirty, exclusions)
    if blocked_dirty:
        if private_index:
            print(
                f"拒绝：私有 index 里 {len(blocked_dirty)} 个文件与工作树不一致，"
                "但没有声明排除 hunk："
            )
            for path in blocked_dirty:
                print(f"  DIRTY   {path}")
            print("  原因：私有 index 允许「排除他人 hunk」，但必须逐文件显式声明，")
            print("        否则无法区分「排除了什么」与「自己的改动没 add 全」。")
            print("  修法：`safe_commit.py --exclude-hunk <path>:<start-end>` 重提交；")
            print("        或 `git add -- <path>` 把工作树内容全部暂存。")
        else:
            print(
                f"拒绝：{len(blocked_dirty)} 个暂存文件的 index 内容与工作树不一致"
                "（会提交旧 blob）："
            )
            for path in blocked_dirty:
                print(f"  DIRTY   {path}")
            print("  原因：index 里留的是旧 blob、工作树是新内容（事故 bc0c75e1：")
            print("        从共享 index 提交了 4 份他人文档的旧快照，行号重定位整体回退）。")
            print("        共享 index 不认排除声明，暂存内容必须与工作树一致。")
            print("  修法：确认工作树内容无误后 `git add -- <path>` 重新写入 index；")
            print("        若这些路径不该进本次提交，`git restore --staged -- <path>` 撤出。")
        failed = True
    if extras:
        print(f"拒绝：暂存区有 {len(extras)} 个文件不在声明清单内（多一个就拒）：")
        for path in extras:
            print(f"  EXTRA   {path}")
        print("  这不是「提交成功」——要么从本次提交里剔除这些路径，")
        print("  要么把它们补进声明清单（确认确实归你）。")
        failed = True
    if multi:
        print(f"发现 MULTI：暂存区涉及 {len(owners)} 个仍在 TTL 内的 owner：{', '.join(owners)}")
        print("  拒绝提交。只提交你自己的路径：git commit --only -- <你的文件...>")
        failed = True
    if failed:
        return 1

    if owners:
        print(f"仅 1 个活跃 owner：{owners[0]}，允许提交。")
    else:
        print("暂存区没有活跃 claim，允许提交（NONE 不等于归属正确，请自行确认）。")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="提交范围检查（claim 台账版）")
    parser.add_argument("ref", nargs="?", help="提交 hash / HEAD / 分支（--staged 时可省略）")
    parser.add_argument("--staged", action="store_true", help="检查暂存区（pre-commit 用）")
    parser.add_argument(
        "--expect",
        nargs="*",
        default=None,
        help="本次提交的声明路径清单；暂存区出现清单外的文件即拒绝（多一个就拒）",
    )
    parser.add_argument(
        "--intent-delete",
        action="append",
        default=[],
        metavar="PATH",
        help="本次提交显式声明要删除的路径（可重复）；暂存里的删除条目不在其中即拒绝"
        "（也可用环境变量 MBDSDR_INTENT_DELETIONS，换行 / ; 分隔）",
    )
    parser.add_argument(
        "--declared-index",
        default=None,
        help="操作者声明的私有 index 路径；与实际读到的 index 不一致即拒绝"
        "（也可用环境变量 MBDSDR_DECLARED_INDEX）",
    )
    parser.add_argument(
        "--post-commit",
        nargs="?",
        const="HEAD",
        default=None,
        metavar="REF",
        help="提交后核对：逐 path 比对 HEAD:<path> 与声明私有 index 的 :<path> blob，"
        "不等即拒（形态⑩：pathspec 覆盖 index）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)
    try:
        if args.staged:
            return check_staged(
                args.json,
                expect=args.expect,
                declared_index_arg=args.declared_index,
                intent_arg=args.intent_delete,
            )
        if args.post_commit:
            return check_post_commit(
                args.post_commit, args.json, declared_index_arg=args.declared_index
            )
        if not args.ref:
            parser.error("需要 ref，或使用 --staged")
        return check(args.ref, args.json)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
