# SPDX-License-Identifier: MIT
"""`claim_takeovers.log` 的写方/读方 schema 契约（机检）。

2026-11-13 真实事故：`file_claim.py legacy-token-repair` 写审计行时不带
`new_owner`，而闸门要求**每一行**都有 `path` + `new_owner` → 那一行合法审计把
整本台账判成不可信 → 全新克隆上每一次提交都被拒。补丁只是把
`legacy_token_repair` 加进白名单，下一个往日志里写新 action 的人还会踩同一脚。

本文件把「闸门能被自己的修复工具废掉」变成契约：

  ① 已知 action（`orphan_takeover` / `audit_replay` / `legacy_token_repair`）：
     写方产出的行必须被读方接受（前两者用**真实 CLI** 产出，不手搓）；
  ② 未知 action：只忽略这一行、给可读原因，**台账整体仍可用**（其余行照常解析）；
  ③ 未知 action 不得成为接管凭证（别人仍持有 → 仍拒，且报真实持有者）；
  ④ 白名单冻结：`NON_TAKEOVER_ACTIONS` 少一个已知 action 就红，并点名是哪个。

判定标准：按失败关闭的闸门，必须保证「正常操作永远不触发失败关闭」。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOOK_SRC = SCRIPTS / "hooks" / "pre-commit"
FILE_CLAIM = SCRIPTS / "file_claim.py"
GATE_SRC = SCRIPTS / "claim_gate.py"

sys.path.insert(0, str(SCRIPTS))
import claim_gate  # noqa: E402  （读方）
import file_claim  # noqa: E402  （写方，同一模块对象；只打补丁不改代码）

TARGET = "a.txt"
#: sidecar 引入版（028f390f）的时间戳；与 test_claim_legacy_repair 同一口径。
INTRO_TS = 1794503631.0

STUB = "#!/usr/bin/env python\nimport sys\nsys.exit(0)\n"


def _git(repo, env, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(repo), env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=check,
    )


def _key(rel):
    return os.path.normcase(rel.replace("\\", "/")).replace("\\", "/").lstrip("./")


def _marker(locks, rel=TARGET):
    digest = hashlib.sha1(_key(rel).encode("utf-8")).hexdigest()[:16]
    return locks / "claims" / f"{digest}.claim"


def _env(tmp_path, repo, locks):
    env = os.environ.copy()
    env.pop("MBDSDR_CLAIM_OWNER", None)
    env.pop("PYTHONPATH", None)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(tmp_path / "hook_ok")
    env["MBDSDR_SCOPE_SCRIPT"] = str(tmp_path / "scope.py")
    env["MBDSDR_SECRET_SCAN"] = str(tmp_path / "scanner.py")
    env["MBDSDR_CLAIM_GATE"] = str(GATE_SRC)
    env["MBDSDR_SIDECAR_INTRO_TS"] = str(int(INTRO_TS))
    return env


@pytest.fixture
def gate_repo(tmp_path):
    """临时 git 仓 + 真实 pre-commit（范围/凭据两段用替身，只测 claim 契约）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    locks = tmp_path / "locks"
    (tmp_path / "scope.py").write_text(STUB, encoding="utf-8")
    (tmp_path / "scanner.py").write_text(STUB, encoding="utf-8")
    env = _env(tmp_path, repo, locks)
    (repo / TARGET).write_text("base\n", encoding="utf-8")
    _git(repo, env, "init", "-q")
    _git(repo, env, "config", "user.email", "LedgerContract@example.invalid")
    _git(repo, env, "config", "user.name", "ledger-contract")
    _git(repo, env, "config", "commit.gpgsign", "false")
    _git(repo, env, "add", TARGET)
    _git(repo, env, "commit", "-q", "-m", "base")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_SRC, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    return repo, env, locks


@pytest.fixture
def reader_log(tmp_path, monkeypatch):
    """把读方（claim_gate.fc）的 TAKEOVER_LOG 指向临时文件，返回该路径。"""
    path = tmp_path / "locks" / "claim_takeovers.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(file_claim, "TAKEOVER_LOG", path)
    return path


def _run_claim(repo, env, *args):
    return subprocess.run(
        [sys.executable, str(FILE_CLAIM), *args], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def _log_rows(locks):
    path = locks / "claim_takeovers.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_row(path, row):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _commit(repo, env):
    return _git(repo, env, "commit", "-q", "-m", "probe", check=False)


def _stage(repo, env, text):
    (repo / TARGET).write_text(text, encoding="utf-8")
    _git(repo, env, "add", TARGET)


# ---------------------------------------------------------------------------
# ④ 白名单冻结（反证：从 NON_TAKEOVER_ACTIONS 移走任一已知 action 必须红并点名）
# ---------------------------------------------------------------------------

def test_non_takeover_actions_are_frozen_per_action():
    """逐个 action 断言，失败时报出**是哪一个**被移走/新增。"""
    assert claim_gate.TAKEOVER_ACTIONS == {"orphan_takeover"}, (
        f"TAKEOVER_ACTIONS 只能有 orphan_takeover，实际 {set(claim_gate.TAKEOVER_ACTIONS)}")
    for action in ("audit_replay", "legacy_token_repair"):
        assert action in claim_gate.NON_TAKEOVER_ACTIONS, (
            f"{action} 被从 NON_TAKEOVER_ACTIONS 移除了：它是已知写方动作，"
            "移出后要么被当成接管凭证（等于给审计行发锁），要么让整本台账判死")
    assert claim_gate.NON_TAKEOVER_ACTIONS == {"audit_replay", "legacy_token_repair"}, (
        f"NON_TAKEOVER_ACTIONS 出现未登记成员："
        f"{set(claim_gate.NON_TAKEOVER_ACTIONS) - {'audit_replay', 'legacy_token_repair'}}")
    assert claim_gate.KNOWN_ACTIONS == (
        claim_gate.TAKEOVER_ACTIONS | claim_gate.NON_TAKEOVER_ACTIONS
    ), "KNOWN_ACTIONS 必须恰好 = 接管凭证 ∪ 非接管行；新增写方 action 请两边同步登记"


def test_every_known_action_has_a_writer_to_reader_round_trip():
    """已知 action 必须逐一有「写方产出 → 读方接受」用例；新增一个就红。"""
    covered = {"orphan_takeover", "audit_replay", "legacy_token_repair"}
    missing = set(claim_gate.KNOWN_ACTIONS) - covered
    assert not missing, (
        f"KNOWN_ACTIONS 新增了 {sorted(missing)} 但没有对应的 writer→reader 用例："
        "请在本文件补一条（能跑真实 CLI 的优先跑真实 CLI）")
    assert covered - set(claim_gate.KNOWN_ACTIONS) == set()


# ---------------------------------------------------------------------------
# ① 已知 action：写方产出的行必须被读方接受
# ---------------------------------------------------------------------------

def test_orphan_takeover_written_by_cli_is_accepted_by_reader(gate_repo, reader_log):
    """真实 `file_claim.py takeover` 产出的行 → 读方接受，且能当接管凭证。"""
    repo, env, locks = gate_repo
    claimed = _run_claim(repo, env, "claim", TARGET, "--owner", "线A", "--ttl-min", "1")
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr

    now = time.time()
    marker = _marker(locks)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload.pop("pid_create_time", None)
    payload.update({"acquired_at": now - 180 * 60, "heartbeat_at": now - 120 * 60, "ttl_min": 1})
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    taken = _run_claim(
        repo, env, "takeover", TARGET, "--owner", "线B",
        "--reason", "原 claim 心跳停在 120min 前，TTL 1min；工作区无改动",
    )
    assert taken.returncode == 0, taken.stdout + taken.stderr

    rows = _log_rows(locks)
    assert [r["action"] for r in rows] == ["orphan_takeover"], rows

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert warnings == [], f"写方产出的合法行被读方抱怨：{warnings}"
    assert len(accepted) == 1 and accepted[0]["new_owner"] == "线B", accepted

    takeovers = claim_gate._takeover_rows({}, accepted)
    assert len(takeovers) == 1, takeovers
    assert takeovers[0]["new_owner"] == "线B" and takeovers[0]["path"] == _key(TARGET)


def test_legacy_token_repair_row_is_accepted_without_new_owner(gate_repo, reader_log):
    """事故主角：`legacy_token_repair` 行没有 `new_owner`，读方必须接受且不判死台账。"""
    repo, env, locks = gate_repo
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    acquired = INTRO_TS - 1200.0
    _marker(locks).write_text(
        json.dumps(
            {
                "path": TARGET,
                "key": _key(TARGET),
                "owner": "A线/原持有",
                "acquired_at": acquired,
                "heartbeat_at": acquired,
                "ttl_min": 180.0,
                "pid": os.getpid(),
                "token_sha256": "a" * 64,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    repaired = _run_claim(
        repo, env, "legacy-token-repair", TARGET, "--owner", "A线/原持有",
        "--reason", "持有者确认已不在改；修复 legacy 凭据", "--by", "台账维护方/me",
    )
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr

    rows = _log_rows(locks)
    assert [r["action"] for r in rows] == ["legacy_token_repair"], rows
    assert "new_owner" not in rows[0], "事故形态就是这一行没有 new_owner"

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert warnings == [], f"合法审计行被读方抱怨（这正是全仓只读的成因）：{warnings}"
    assert len(accepted) == 1, accepted
    # 记账行不是接管凭证：不得凭空给任何 owner 发锁。
    assert claim_gate._takeover_rows({}, accepted) == []


def test_audit_replay_row_shape_is_accepted(reader_log):
    """`audit_replay` 由人工按文档追加（无 CLI 写方），字段形状取自真实日志。"""
    row = {
        "action": "audit_replay",
        "logged_at": "2026-11-13 09:12:00",
        "epoch": 1762996320.0,
        "path": TARGET,
        "old_owner": "线A",
        "new_owner": "线B",
        "verdict": "不合规",
        "violations": ["接管时心跳未过期"],
        "replay_of_epoch": 1762995000.0,
        "replay_of_logged_at": "2026-11-13 08:50:00",
        "basis": "docs/COORDINATION.md §6.1.4",
        "loss": "无",
        "ownership": "归还线A",
        "criteria_new": {"expired": False, "no_inflight": True},
    }
    _append_row(reader_log, row)

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert warnings == [], warnings
    assert len(accepted) == 1 and accepted[0]["action"] == "audit_replay", accepted
    assert claim_gate._takeover_rows({}, accepted) == [], "审计行不得成为接管凭证"


# ---------------------------------------------------------------------------
# ② 未知 action：只忽略该行，台账整体仍可用
# ---------------------------------------------------------------------------

def test_unknown_action_row_is_skipped_with_readable_reason(reader_log):
    """「只有 path、没有 new_owner、action=新名字」→ 不抛异常、不判死台账。"""
    _append_row(reader_log, {"action": "brand_new_audit", "path": TARGET})

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert accepted == [], accepted
    assert len(warnings) == 1, warnings
    text = warnings[0]
    assert "brand_new_audit" in text, text
    assert "第 1 行" in text and "不认识" in text, text
    assert "已忽略这一行" in text, text


def test_unknown_action_does_not_destroy_neighbour_rows(reader_log):
    """同一份日志里，未知行只影响自己；合法 orphan_takeover 行照常被解析。"""
    _append_row(reader_log, {"action": "brand_new_audit", "path": "other.txt"})
    _append_row(
        reader_log,
        {
            "action": "orphan_takeover",
            "logged_at": "2026-11-13 10:00:00",
            "epoch": 1762999200.0,
            "path": TARGET,
            "old_owner": "线A",
            "new_owner": "线B",
            "reason": "心跳过期 + 无在途",
        },
    )

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert len(accepted) == 1 and accepted[0]["action"] == "orphan_takeover", accepted
    assert len(warnings) == 1 and "brand_new_audit" in warnings[0], warnings
    takeovers = claim_gate._takeover_rows({}, accepted)
    assert len(takeovers) == 1 and takeovers[0]["new_owner"] == "线B", takeovers


def test_known_takeover_row_missing_owner_is_skipped_not_fatal(reader_log):
    """已知 action 但写方漏字段：只丢这一行的凭证效力，不判死台账。"""
    _append_row(
        reader_log,
        {"action": "orphan_takeover", "path": TARGET, "epoch": 1762999200.0, "reason": "x"},
    )

    warnings = []
    accepted = claim_gate._load_takeover_log(warnings)
    assert accepted == [], accepted
    assert len(warnings) == 1 and "new_owner" in warnings[0], warnings


# ---------------------------------------------------------------------------
# ③ 端到端：未知 action 不得成为接管凭证，但也不得让仓库变只读
# ---------------------------------------------------------------------------

def test_unknown_action_row_does_not_block_clean_commit(gate_repo):
    """事故原形的反向验证：日志里有未知行时，无关提交必须能过。"""
    repo, env, locks = gate_repo
    locks.mkdir(parents=True, exist_ok=True)
    _append_row(locks / "claim_takeovers.log", {"action": "brand_new_audit", "path": TARGET})
    _stage(repo, env, "v2\n")

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "brand_new_audit" in out and "不认识" in out, out
    assert "失败关闭" not in out, out


def test_unknown_action_does_not_excuse_foreign_claim(gate_repo):
    """未知 action 行里写了 new_owner=我，也不能解开别人仍持有的 claim。"""
    repo, env, locks = gate_repo
    now = time.time()
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    _marker(locks).write_text(
        json.dumps(
            {
                "path": TARGET,
                "key": _key(TARGET),
                "owner": "A线/真持有",
                "acquired_at": now - 60.0,
                "heartbeat_at": now - 60.0,
                "ttl_min": 180.0,
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _append_row(
        locks / "claim_takeovers.log",
        {"action": "brand_new_audit", "path": TARGET, "epoch": now, "new_owner": "我这条线"},
    )
    env = dict(env, MBDSDR_CLAIM_OWNER="我这条线")
    _stage(repo, env, "v2\n")

    proc = _commit(repo, env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "持有者：A线/真持有" in out, out
    assert "brand_new_audit" in out and "不认识" in out, out
