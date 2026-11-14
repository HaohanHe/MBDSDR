#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`file_claim.py cleanup-stale` 的行为契约。

这个入口存在的理由：过期 claim 以前只能靠人判断「持有者是不是跑了」，
而 `claim` 是一次性 CLI——pid 在 claim 返回后就已经不存在，拿 pid 当死亡
证明等于任何 claim 都能被清掉（2026-11-13 的接管事故）。

所以这里逐条钉住「按证据释放」：

  * 默认只审计，不改占位；
  * 四项证据（租约过期、持有进程、内容与取得时一致、工作区无在途改动）
    必须**全部 PASS** 才能 `--release`；
  * 任何一项 GAP（证不出来）/ FAIL（证出来有风险）只报「疑似失联 + 缺口」，
    退出码 3；
  * 没有「超过 N 分钟一律释放」的开关；
  * 释放写 `claim_cleanup.log`：前一行是证据 + 释放前占位，后一行是结果。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
TARGET = "f.txt"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败：{proc.stderr}")
    return proc


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / TARGET).write_text("baseline\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", TARGET)
    _git(
        repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "init",
    )


def _env(repo: Path, locks: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env.pop("PYTHONPATH", None)
    env.pop("MBDSDR_CLAIM_OWNER", None)
    env.pop("MBDSDR_TAKEOVER_GRACE_MIN", None)
    return env


def run(repo: Path, locks: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(repo),
        env=_env(repo, locks),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _only_marker(locks: Path) -> Path:
    markers = sorted((locks / "claims").glob("*.claim"))
    assert len(markers) == 1, f"应当恰好一条 claim：{markers}"
    return markers[0]


def _read_marker(locks: Path) -> dict:
    return json.loads(_only_marker(locks).read_text(encoding="utf-8"))


def _write_marker(locks: Path, data: dict) -> None:
    _only_marker(locks).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _expire(locks: Path, *, legacy: bool = False) -> dict:
    """把唯一 claim 改成「早已过期」；legacy=True 时抹掉内容基线（模拟旧格式）。"""
    data = _read_marker(locks)
    now = time.time()
    # acquired 保持在最近一次提交之后（旧 claim 的 git 推导要求），心跳往前拨。
    data["acquired_at"] = now
    data["heartbeat_at"] = now - 3600
    data["ttl_min"] = 10
    if legacy:
        data.pop("content_sha256", None)
        data.pop("content_baseline_from", None)
    _write_marker(locks, data)
    return data


def _claim(repo: Path, locks: Path, path: str = TARGET) -> subprocess.CompletedProcess:
    result = run(repo, locks, "claim", path, "--owner", "线A/abc123", "--ttl-min", "10")
    assert result.returncode == 0, result.stderr + result.stdout
    return result


def _log(locks: Path) -> list[dict]:
    path = locks / "claim_cleanup.log"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_claim_records_content_baseline(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)

    data = _read_marker(locks)
    expected = hashlib.sha256((repo / TARGET).read_bytes()).hexdigest()
    assert data["content_sha256"] == expected
    assert data["content_baseline_from"] == "claim"


def test_renew_does_not_refresh_content_baseline(tmp_path: Path) -> None:
    """取得后被改过再续租，基线仍是取得时的那份——否则「改过」会被洗白。"""
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    original = _read_marker(locks)["content_sha256"]

    (repo / TARGET).write_text("holder edited\n", encoding="utf-8")
    renewed = run(
        repo, locks, "claim", TARGET, "--owner", "线A/abc123",
        "--ttl-min", "10", "--reason", "继续改",
    )
    assert renewed.returncode == 0, renewed.stderr + renewed.stdout
    assert _read_marker(locks)["content_sha256"] == original


def test_no_claims_returns_zero(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    result = run(repo, locks, "cleanup-stale")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "扫描 0 条 claim" in result.stdout


def test_live_claim_is_not_a_cleanup_candidate(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)

    result = run(repo, locks, "cleanup-stale")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "租约未过期 1" in result.stdout
    assert _only_marker(locks).exists()

    trend = locks / "claim_cleanup_trend.jsonl"
    last = json.loads(trend.read_text(encoding="utf-8").splitlines()[-1])
    assert last["claims_with_content_hash"] == 1, "新 claim 必须记内容基线"
    assert last["undecidable_ratio"] == 0.0


def test_default_is_dry_run(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    _expire(locks)

    result = run(repo, locks, "cleanup-stale")
    assert result.returncode == 3, result.stderr + result.stdout
    assert "READY" in result.stdout
    assert "【待办·可释放未释放】1" in result.stdout
    assert "未加 --release" in result.stdout
    assert _only_marker(locks).exists(), "只审计不许改占位"
    assert not _log(locks), "只审计不许写清理账"


def test_release_requires_by_and_reason(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    _expire(locks)

    no_by = run(repo, locks, "cleanup-stale", "--release", "--reason", "x")
    assert no_by.returncode == 1, no_by.stderr + no_by.stdout
    no_reason = run(repo, locks, "cleanup-stale", "--release", "--by", "人")
    assert no_reason.returncode == 1, no_reason.stderr + no_reason.stdout
    assert _only_marker(locks).exists()


def test_release_proven_dead_claim_writes_audit(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    data = _expire(locks)

    result = run(
        repo, locks, "cleanup-stale", "--release",
        "--by", "清理人", "--reason", "测试：证据齐全",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "RELEASED" in result.stdout
    assert not list((locks / "claims").glob("*.claim"))
    assert not list((locks / "claims").glob("*.token")), "凭据 sidecar 应一并删除"

    rows = _log(locks)
    before = [r for r in rows if r["phase"] == "before"]
    after = [r for r in rows if r["phase"] == "after"]
    assert len(before) == 1 and len(after) == 1
    assert before[0]["path"] == TARGET
    assert before[0]["owner"] == data["owner"]
    assert before[0]["line"] == "线A"
    assert before[0]["by"] == "清理人"
    assert before[0]["claim_before"]["owner"] == data["owner"]
    assert set(before[0]["evidence"]) == {"lease", "holder", "content", "no_inflight"}
    assert all(item["status"] == "PASS" for item in before[0]["evidence"].values())
    assert after[0]["released"] is True
    assert after[0]["line"] == "线A"
    assert after[0]["evidence"]["content"]["status"] == "PASS"
    assert after[0]["claim_after"] is None


def test_changed_content_blocks_release(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    _expire(locks)
    (repo / TARGET).write_text("changed after claim\n", encoding="utf-8")

    result = run(repo, locks, "cleanup-stale", "--release", "--by", "人", "--reason", "x")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "SUSPECT" in result.stdout
    assert "content(FAIL" in result.stdout
    assert "补法：" in result.stdout
    assert _only_marker(locks).exists()
    assert not _log(locks)


def test_suspect_json_carries_hint_for_batch_report(tmp_path: Path) -> None:
    """batch_close 只读 JSON，所以 SUSPECT 的补法必须落在 rows[].hint 里。"""
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    _expire(locks)
    (repo / TARGET).write_text("changed after claim\n", encoding="utf-8")

    result = run(repo, locks, "cleanup-stale", "--json")
    assert result.returncode == 0, result.stderr + result.stdout
    row = json.loads(result.stdout)["rows"][0]
    assert row["category"] == "suspect"
    assert "内容与基线不符" in row["hint"]


def test_missing_baseline_on_untracked_file_is_a_gap(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    (repo / "u.txt").write_text("untracked\n", encoding="utf-8")
    _claim(repo, locks, "u.txt")
    _expire(locks, legacy=True)

    result = run(repo, locks, "cleanup-stale", "--release", "--by", "人", "--reason", "x")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "content(GAP" in result.stdout
    assert "不在 git 跟踪下" in result.stdout
    assert _only_marker(locks).exists()


def test_legacy_claim_uses_git_inference(tmp_path: Path) -> None:
    """旧 claim 没有内容基线：工作区干净 + 最后提交早于 claim 时可推导为未改。"""
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    _expire(locks, legacy=True)

    result = run(repo, locks, "cleanup-stale", "--release", "--by", "人", "--reason", "x")
    assert result.returncode == 0, result.stderr + result.stdout
    assert not list((locks / "claims").glob("*.claim"))
    evidence = _log(locks)[0]["evidence"]
    assert evidence["content"]["status"] == "PASS"
    assert evidence["content"]["weight"] == "weak"


def test_alive_pid_without_create_time_is_a_gap(tmp_path: Path) -> None:
    """持有进程还在、但记录里没有创建时刻：区分不了原进程与复用 → 不释放。"""
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    data = _expire(locks)
    data["pid"] = os.getpid()  # 测试进程在清理期间一直活着
    data.pop("pid_create_time", None)
    _write_marker(locks, data)

    result = run(repo, locks, "cleanup-stale", "--release", "--by", "人", "--reason", "x")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "holder(GAP" in result.stdout
    assert _only_marker(locks).exists()


def test_post_claim_commit_is_undecidable_not_todo(tmp_path: Path) -> None:
    """claim 之后有提交：内容再也无法回溯到取得时 → UNDECIDABLE，不进待办、不改退出码。"""
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    (repo / TARGET).write_text("committed after claim\n", encoding="utf-8")
    _git(repo, "add", TARGET)
    _git(
        repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "after claim",
    )
    data = _read_marker(locks)
    now = time.time()
    data["acquired_at"] = now - 120  # 让“之后有提交”确定成立
    data["heartbeat_at"] = now - 3600
    data["ttl_min"] = 10
    data.pop("content_sha256", None)
    data.pop("content_baseline_from", None)
    _write_marker(locks, data)

    result = run(repo, locks, "cleanup-stale", "--release", "--by", "人", "--reason", "x")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "UNDECIDABLE 1" in result.stdout
    assert "不会因为时间推移" in result.stdout
    assert "不是「文件清白」的证明" in result.stdout
    assert "post_claim_commit=1" in result.stdout
    assert _only_marker(locks).exists(), "UNDECIDABLE 不许被释放"

    trend = locks / "claim_cleanup_trend.jsonl"
    last = json.loads(trend.read_text(encoding="utf-8").splitlines()[-1])
    assert last["summary"]["undecidable"] == 1
    assert last["undecidable_reasons"] == {"post_claim_commit": 1}
    assert last["undecidable_ratio"] == 1.0
    assert last["claims_with_content_hash"] == 0  # 这条是旧 claim，没有内容基线


def test_undecidable_json_is_machine_readable(tmp_path: Path) -> None:
    repo, locks = tmp_path / "repo", tmp_path / "locks"
    _init_repo(repo)
    _claim(repo, locks)
    (repo / TARGET).write_text("committed after claim\n", encoding="utf-8")
    _git(repo, "add", TARGET)
    _git(
        repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "after claim",
    )
    data = _read_marker(locks)
    now = time.time()
    data["acquired_at"] = now - 120
    data["heartbeat_at"] = now - 3600
    data["ttl_min"] = 10
    data.pop("content_sha256", None)
    data.pop("content_baseline_from", None)
    _write_marker(locks, data)

    result = run(repo, locks, "cleanup-stale", "--json")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["undecidable"]["count"] == 1
    assert payload["undecidable"]["reasons"] == {"post_claim_commit": 1}
    assert payload["undecidable"]["ratio"] == 1.0
    assert payload["rows"][0]["category"] == "undecidable"
    assert payload["rows"][0]["undecidable_reason"] == "post_claim_commit"
