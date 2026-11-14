#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""§11.3 裸提交前提的机检入口：四类样本各落一处结论，缺行反证必须红。

判据（对应 2026-11-14「四条前提要能机检」）：

  * 全满足、文件集不全、缺行、有删除 四种样本，分别落到 ①/④/③ 的对应结论，
    且互不串门（全满足那笔不进任何违规名单）；
  * 反证：把「有 claim 但没写 claim-line」判成合规，``test_missing_trailer_must_be_red``
    的后半段断言必红；前半段同时证明它不是「无差别全红」；
  * 台账不存在时三项必须报「未审计」，不许显示 0 违规冒充清白；
  * 退出码恒 0——审计只报告，不拦截。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
AUDIT = SCRIPTS / "audit_bare_commits.py"

OWNER = "甲线/aaaa1111"
DOC = "docs/COORDINATION.md"

DOC_TWO_ROWS = """# 协调

### 6.1 当前占用

| 路径 | 占用者 | 说明 |
| --- | --- | --- |
| `a.py` | 甲线 | 基线 |
| `b.py` | 甲线 | 基线 |
"""

DOC_ONE_ROW = """# 协调

### 6.1 当前占用

| 路径 | 占用者 | 说明 |
| --- | --- | --- |
| `a.py` | 甲线 | 基线 |
"""


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                          text=True, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, files: dict[str, str], message: str, at: float) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    env = os.environ.copy()
    stamp = f"@{int(at)} +0000"
    env["GIT_AUTHOR_DATE"] = stamp
    env["GIT_COMMITTER_DATE"] = stamp
    proc = _git(repo, "commit", "-q", "-m", message, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _ledger(locks: Path, paths: list[str], start: float, end: float) -> None:
    locks.mkdir(parents=True, exist_ok=True)
    claims = {
        rel: {
            "path": rel,
            "current": None,
            "history": [{
                "owner": OWNER,
                "acquired_at": start,
                "released_at": end,
                "last_heartbeat_at": start,
            }],
        }
        for rel in paths
    }
    (locks / "claims.json").write_text(
        json.dumps({"version": 1, "claims": claims}, ensure_ascii=False), encoding="utf-8")


def _env(repo: Path, locks: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env.pop("PYTHONPATH", None)
    env.pop("MBDSDR_COORD_DOC", None)
    return env


def _run(repo: Path, locks: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUDIT), *extra], cwd=repo, env=_env(repo, locks),
        capture_output=True, text=True, encoding="utf-8", timeout=180)


def _four_samples(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """全满足 / 文件集不全 / 缺行 / 有删除 各一笔。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    now = time.time()
    start, end = now - 1000.0, now + 1000.0

    base = _commit(repo, {"a.py": "x = 0\n", "b.py": "y = 0\n", DOC: DOC_TWO_ROWS},
                   "chore: base\n\nclaim-line: 甲线", now - 500.0)
    ok = _commit(repo, {"a.py": "x = 1\n"},
                 "feat: all satisfied\n\nclaim-line: 甲线", now - 300.0)
    partial = _commit(repo, {"a.py": "x = 2\n", "c.py": "z = 1\n"},
                      "feat: unclaimed file\n\nclaim-line: 甲线", now - 200.0)
    no_line = _commit(repo, {"a.py": "x = 3\n"}, "feat: forgot trailer", now - 100.0)
    deleted = _commit(repo, {DOC: DOC_ONE_ROW},
                      "docs: drop row\n\nclaim-line: 甲线", now - 50.0)

    _ledger(locks, ["a.py", "b.py", DOC], start, end)
    return repo, locks, {"base": base, "ok": ok, "partial": partial,
                         "no_line": no_line, "deleted": deleted}


def test_four_samples_land_in_their_own_conclusions(tmp_path: Path) -> None:
    repo, locks, sha = _four_samples(tmp_path)
    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    data = json.loads(proc.stdout)
    cover = data["checks"]["claim_coverage"]
    line = data["checks"]["claim_line"]
    append = data["checks"]["append_only"]

    # ① 只有「文件集不全」那笔：c.py 提交时刻没有 claim。
    assert cover["status"] == "checked", data
    assert cover["violating"] == 1, data
    assert [v["sha"] for v in cover["violations"]] == [sha["partial"]], cover
    paths = [p["path"] for p in cover["violations"][0]["paths"]]
    assert paths == ["c.py"], paths

    # ④ 只有「缺行」那笔。
    assert line["post"]["violating"] == 1, line
    assert [v["sha"] for v in line["post"]["violations"]] == [sha["no_line"]], line
    assert line["post"]["violations"][0]["kind"] == "missing_trailer"

    # ③ 只有「有删除」那笔，且点名文件与删掉的行。
    assert append["violating"] == 1, append
    assert [v["sha"] for v in append["violations"]] == [sha["deleted"]], append
    assert append["violations"][0]["path"] == DOC
    assert append["violations"][0]["rows"], append

    # 全满足与基线不得出现在任何违规名单里。
    flagged = ({v["sha"] for v in cover["violations"]}
               | {v["sha"] for v in line["post"]["violations"]}
               | {v["sha"] for v in append["violations"]})
    assert sha["ok"] not in flagged and sha["base"] not in flagged, flagged

    # 文本输出必须把「不覆盖」写在脸上。
    text = _run(repo, locks, "--range", "HEAD", "--rule-since", "0")
    assert text.returncode == 0, text.stdout + text.stderr
    assert text.stdout.count("本项机检不覆盖，需人工复核") >= 3, text.stdout
    assert "不判断某笔是不是裸提交" in text.stdout, text.stdout


def test_missing_trailer_must_be_red(tmp_path: Path) -> None:
    """反证：缺行被判成合规时，后半段断言必红。

    前半段（规则生效后）要求缺行进违规名单——把「有 claim 无行」当合规的实现
    在这里一定失败；后半段（同一笔放到规则生效前）要求它**不**进缺口，证明
    这些红旗来自规则时点，而不是「见谁都红」。
    """
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    now = time.time()

    _commit(repo, {"a.py": "x = 0\n"}, "chore: base\n\nclaim-line: 甲线", now - 500.0)
    pre = _commit(repo, {"a.py": "x = 1\n"}, "feat: before rule, no line", now - 300.0)
    post = _commit(repo, {"a.py": "x = 2\n"}, "feat: after rule, no line", now - 100.0)
    _ledger(locks, ["a.py"], now - 1000.0, now + 1000.0)

    proc = _run(repo, locks, "--range", "HEAD", "--rule-since",
                str(now - 200.0), "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)["checks"]["claim_line"]
    assert [v["sha"] for v in data["post"]["violations"]] == [post], data
    assert data["pre"]["claim_without_trailer"] == 1, data

    merged = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    both = json.loads(merged.stdout)["checks"]["claim_line"]
    assert {v["sha"] for v in both["post"]["violations"]} == {pre, post}, both


def test_missing_ledger_reports_not_checked_not_clean(tmp_path: Path) -> None:
    """台账不存在时不许显示「0 违规」冒充清白：必须报未审计。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"       # 故意不写 claims.json
    _commit(repo, {"a.py": "x = 1\n"}, "feat: no ledger\n\nclaim-line: 甲线",
            time.time() - 10.0)

    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)["checks"]
    assert data["claim_coverage"]["status"] == "not_checked", data
    assert data["claim_line"]["status"] == "not_checked", data
    text = _run(repo, locks, "--range", "HEAD", "--rule-since", "0")
    assert "未审计" in text.stdout, text.stdout
