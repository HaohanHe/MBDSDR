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
    trend = tmp_path / "trend.jsonl"
    _commit(repo, {"a.py": "x = 1\n"}, "feat: no ledger\n\nclaim-line: 甲线",
            time.time() - 10.0)

    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json",
                "--trend", str(trend))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)["checks"]
    assert data["claim_coverage"]["status"] == "not_checked", data
    assert data["claim_line"]["status"] == "not_checked", data
    text = _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
                "--trend", str(trend))
    assert "未审计" in text.stdout, text.stdout

    # 趋势里也不许把「未审计」记成 0。
    row = json.loads(trend.read_text(encoding="utf-8").splitlines()[0])
    assert row["violations"]["claim_coverage"] is None, row
    assert row["violations"]["claim_line"] is None, row


# ---------------------------------------------------------------------------
# 趋势：键 = 区间 + 判据版本
# ---------------------------------------------------------------------------


def _rows(trend: Path) -> list[dict]:
    return [json.loads(line) for line in
            trend.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_same_range_twice_keeps_one_row(tmp_path: Path) -> None:
    """判据：同一区间跑两次 → 只留一行（把去重键去掉，本测试必红）。"""
    repo, locks, sha = _four_samples(tmp_path)
    trend = tmp_path / "trend.jsonl"

    for _ in range(2):
        proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
                    "--trend", str(trend))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    rows = _rows(trend)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["criteria"].startswith("v3:"), row
    assert row["commits"] == 5, row
    assert row["range"]["from"]["sha"] == sha["base"], row
    assert row["range"]["to"]["sha"] == sha["deleted"], row
    assert row["violations"] == {"claim_coverage": 1, "append_only": 1,
                                 "claim_line": 1}, row
    assert row["denominators"] == {"claim_coverage": 5, "append_only": 2,
                                   "claim_line": 5}, row
    assert row["lines"]["distinct"] == 1, row


def test_new_criteria_version_starts_another_row(tmp_path: Path) -> None:
    """判据：同区间换判据版本 → 另起一行，且两行可区分。"""
    repo, locks, _ = _four_samples(tmp_path)
    trend = tmp_path / "trend.jsonl"

    for version in ("v1-probe-a", "v1-probe-b", "v1-probe-a"):
        proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
                    "--trend", str(trend), "--criteria-version", version)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    rows = _rows(trend)
    assert [r["criteria"] for r in rows] == ["v1-probe-a", "v1-probe-b"], rows
    assert rows[0]["range_key"] == rows[1]["range_key"], rows


def test_trend_report_groups_by_version_with_denominator(tmp_path: Path) -> None:
    """趋势文件要能读：按判据版本分组，比率带分母，并写明下降≠变好的限制。"""
    repo, locks, _ = _four_samples(tmp_path)
    trend = tmp_path / "trend.jsonl"
    for version in ("v1-probe-a", "v1-probe-b"):
        _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
             "--trend", str(trend), "--criteria-version", version)

    proc = _run(repo, locks, "--trend-report", "--trend", str(trend))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "判据版本 v1-probe-a" in out and "判据版本 v1-probe-b" in out, out
    assert "① 1/5" in out and "③ 1/2" in out and "④ 1/5" in out, out
    assert "比率必须带分母" in out, out
    assert "不等于实践变好" in out, out


# ---------------------------------------------------------------------------
# ①后半：提交时刻他线正持有同一文件（提示，不是违规）
# ---------------------------------------------------------------------------


def _foreign_samples(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """四笔：他线持同文件（主数）/ 持不同文件 / 无人持有 / 他线持登记面（窄锁）。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    now = time.time()
    start, end = now - 1000.0, now + 1000.0
    base = _commit(repo, {"seed.py": "s = 0\n", DOC: DOC_TWO_ROWS},
                   "chore: base\n\nclaim-line: 甲线", now - 500.0)
    same = _commit(repo, {"a.py": "x = 1\n"},
                   "feat: on someone's file\n\nclaim-line: 甲线", now - 300.0)
    other = _commit(repo, {"c.py": "c = 1\n"},
                    "feat: other file\n\nclaim-line: 甲线", now - 200.0)
    free = _commit(repo, {"d.py": "d = 1\n"},
                   "feat: nobody holds it\n\nclaim-line: 甲线", now - 100.0)
    reg = _commit(repo, {DOC: DOC_TWO_ROWS + "| `e.py` | 乙线 | 新行 |\n"},
                  "docs: add row\n\nclaim-line: 甲线", now - 50.0)

    # 登记面从 base 之后才被乙线持有，免得基线提交本身也算一次窄锁命中。
    _ledger_multi(locks, {"seed.py": OWNER, "c.py": OWNER, "d.py": OWNER,
                          "a.py": "乙线/bbbb2222",
                          DOC: ("乙线/bbbb2222", now - 400.0, end)},
                  start, end)
    return repo, locks, {"base": base, "same": same, "other": other,
                         "free": free, "reg": reg}


def _registry_only(tmp_path: Path) -> tuple[Path, Path, str]:
    """只有登记面窄锁命中：主数必须报 0，而不是把窄锁算成信号。"""
    repo = _repo(tmp_path)
    locks = tmp_path / "locks"
    now = time.time()
    _commit(repo, {"seed.py": "s = 0\n", DOC: DOC_TWO_ROWS},
            "chore: base\n\nclaim-line: 甲线", now - 500.0)
    reg = _commit(repo, {DOC: DOC_TWO_ROWS + "| `e.py` | 乙线 | 新行 |\n"},
                  "docs: add row\n\nclaim-line: 甲线", now - 300.0)
    _ledger_multi(locks, {"seed.py": OWNER,
                          DOC: ("乙线/bbbb2222", now - 400.0, now + 1000.0)},
                  now - 1000.0, now + 1000.0)
    return repo, locks, reg


def test_foreign_hold_three_samples(tmp_path: Path) -> None:
    """判据：同文件 → 疑似；不同文件 / 无人持有 → 不报；登记面另立一类。"""
    repo, locks, sha = _foreign_samples(tmp_path)
    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    checks = json.loads(proc.stdout)["checks"]

    fh = checks["foreign_hold"]
    assert fh["status"] == "checked", fh
    assert fh["checked"] == 5 and fh["skipped_no_trailer"] == 0, fh
    # 主数只含非登记面：a.py 那笔；登记面那笔进 registry，不进 count。
    assert fh["count"] == 1, fh
    assert [v["sha"] for v in fh["suspected"]] == [sha["same"]], fh
    hit = fh["suspected"][0]["hits"][0]
    assert hit["path"] == "a.py" and hit["line"] == "乙线", hit
    assert hit["held_min"] > 0 and hit["left_min"] > 0, hit
    assert fh["registry_count"] == 1, fh
    assert [v["sha"] for v in fh["registry"]] == [sha["reg"]], fh
    assert fh["registry"][0]["hits"][0]["path"] == DOC, fh

    # ① 仍只按自己的判据：同一笔因「没有被自己的 claim 覆盖」进 ①，
    # 另三笔不进——把他线持有并进 ① 会让这里多出条目，测试因此变红。
    cov = checks["claim_coverage"]
    assert cov["violating"] == 1, cov
    assert [v["sha"] for v in cov["violations"]] == [sha["same"]], cov
    assert [p["path"] for p in cov["violations"][0]["paths"]] == ["a.py"], cov

    flagged = {v["sha"] for v in fh["suspected"]}
    assert not (flagged & {sha["other"], sha["free"], sha["reg"]}), flagged

    text = _run(repo, locks, "--range", "HEAD", "--rule-since", "0")
    assert "提示，非违规" in text.stdout, text.stdout
    assert "命中不等于被带入" in text.stdout, text.stdout
    assert "本项机检不覆盖，需人工复核" in text.stdout, text.stdout
    assert "主数（非登记面文件）：疑似 1 笔" in text.stdout, text.stdout
    assert "登记面窄锁（已知形态，只做趋势）：1 笔" in text.stdout, text.stdout


def test_registry_hold_is_not_the_main_number(tmp_path: Path) -> None:
    """反证：把登记面窄锁算进主数，本测试必红（主数是 0 就该报 0）。

    真实区间的 8 笔疑似 100% 是登记面窄锁；若它们仍计入主数，这项提示的主数
    就永远是「文档活动量」，不是信号。
    """
    repo, locks, reg = _registry_only(tmp_path)
    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0", "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fh = json.loads(proc.stdout)["checks"]["foreign_hold"]
    assert fh["count"] == 0, fh
    assert fh["suspected"] == [], fh
    assert fh["registry_count"] == 1, fh
    assert [v["sha"] for v in fh["registry"]] == [reg], fh
    assert fh["checked"] == 2, fh

    text = _run(repo, locks, "--range", "HEAD", "--rule-since", "0")
    assert "主数（非登记面文件）：疑似 0 笔" in text.stdout, text.stdout
    assert "这项在当前实践下从未命中" in text.stdout, text.stdout


def test_foreign_hold_has_its_own_trend_denominator(tmp_path: Path) -> None:
    """趋势里单列分母 + 拆分登记面，不混进 ① 的比率；判据版本升到 v3。"""
    repo, locks, _ = _foreign_samples(tmp_path)
    trend = tmp_path / "trend.jsonl"
    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
                "--trend", str(trend))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    row = _rows(trend)[0]
    assert row["criteria"].startswith("v3:"), row
    assert row["suspected_foreign_hold"] == {
        "count": 1, "checked": 5, "skipped_no_trailer": 0, "rate": 0.2,
        "registry_count": 1, "registry_rate": 0.2, "registry_doc": DOC}, row
    # ① 的分母/分子不受提示项影响。
    assert row["violations"]["claim_coverage"] == 1, row
    assert row["denominators"]["claim_coverage"] == 5, row
    assert row["rates"]["claim_coverage"] == 0.2, row

    report = _run(repo, locks, "--trend-report", "--trend", str(trend))
    assert "他线在途主数 1/5" in report.stdout, report.stdout
    assert "登记面窄锁，已知形态、只做趋势：1/5" in report.stdout, report.stdout
    assert "命中不等于被带入" in report.stdout, report.stdout
    # 本段主数不为 0 → 不该出现「从未命中」的结论。
    assert "从未命中" not in report.stdout, report.stdout


def test_trend_says_never_hit_when_main_is_zero(tmp_path: Path) -> None:
    """主数长期是 0 时必须明说，别让它读起来像一直在发现问题。"""
    repo, locks, _ = _registry_only(tmp_path)
    trend = tmp_path / "trend.jsonl"
    proc = _run(repo, locks, "--range", "HEAD", "--rule-since", "0",
                "--trend", str(trend))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    row = _rows(trend)[0]
    assert row["suspected_foreign_hold"]["count"] == 0, row

    report = _run(repo, locks, "--trend-report", "--trend", str(trend))
    assert "他线在途主数（非登记面，提示）0/2" in report.stdout, report.stdout
    assert "登记面窄锁（已知形态）1/2" in report.stdout, report.stdout
    assert "这项在当前实践下从未命中" in report.stdout, report.stdout


def _ledger_multi(locks: Path, owner_of: dict[str, object],
                  start: float, end: float) -> None:
    """每路径一个 owner，或 (owner, 起, 止) 三元组（用来让持有晚于基线提交）。"""
    locks.mkdir(parents=True, exist_ok=True)
    claims = {}
    for rel, spec in owner_of.items():
        owner, win_start, win_end = (
            (spec, start, end) if isinstance(spec, str) else spec)
        claims[rel] = {"path": rel, "current": None,
                       "history": [{"owner": owner, "acquired_at": win_start,
                                    "released_at": win_end,
                                    "last_heartbeat_at": win_start}]}
    (locks / "claims.json").write_text(
        json.dumps({"version": 1, "claims": claims}, ensure_ascii=False),
        encoding="utf-8")

