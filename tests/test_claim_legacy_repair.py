"""`legacy-token-repair` 的边界：只修「sidecar 上线前签发、明文从没落盘」的 claim。

背景：claim 是一次性 CLI，早期版本只把凭据哈希写进占位文件，明文 token 从没
落过盘。这类 claim 死锁——release 要 token、续租要 token、takeover 要等 TTL，
谁都动不了。修法必须是**有审计的一次性命令**，不能开成「谁都能清别人的 claim」：

  ① 有 token_sha256 + sidecar 缺失 + 签发早于 sidecar 引入版（028f390f），三条缺一不可；
  ② --owner 必须与台账原文一致；--reason 非空；日志写 action=legacy_token_repair；
  ③ 修完不留半截：占位文件完整删除、台账 current 置空，没有「marker 在、token 没了」。
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
#: sidecar 引入版（028f390f）的时间戳；测试用环境变量注入，避免依赖临时仓库里有该提交。
INTRO_TS = 1794503631.0
REL = "a.txt"
OWNER = "A03静默降级线/6ae85f56"


def _key(rel: str) -> str:
    return os.path.normcase(rel.replace("\\", "/")).replace("\\", "/")


def _marker(locks: Path, rel: str = REL) -> Path:
    digest = hashlib.sha1(_key(rel).encode("utf-8")).hexdigest()[:16]
    return locks / "claims" / f"{digest}.claim"


def _sidecar(locks: Path, rel: str = REL) -> Path:
    return _marker(locks, rel).with_suffix(".token")


def _write_claim(
    locks: Path,
    *,
    rel: str = REL,
    owner: str = OWNER,
    acquired: float | None = None,
    ttl_min: float = 180.0,
    token_sha: bool = True,
    sidecar: bool = False,
) -> Path:
    now = time.time()
    acquired = now - 300.0 if acquired is None else acquired
    directory = locks / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": rel,
        "key": _key(rel),
        "owner": owner,
        "acquired_at": acquired,
        "heartbeat_at": acquired,
        "ttl_min": ttl_min,
        "pid": os.getpid(),
    }
    if token_sha:
        payload["token_sha256"] = "a" * 64
    marker = _marker(locks, rel)
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if sidecar:
        _sidecar(locks, rel).write_text("0" * 32 + "\n", encoding="utf-8")
    return marker


def _run(root: Path, locks: Path, *args: str, intro: float = INTRO_TS):
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(root)
    env["MBDSDR_SIDECAR_INTRO_TS"] = str(intro)
    env.pop("MBDSDR_CLAIM_OWNER", None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def _repair(root: Path, locks: Path, *extra: str, owner: str = OWNER, reason: str = "持有者确认已不在改；修复 legacy 凭据"):
    args = ["legacy-token-repair", REL, "--owner", owner, "--reason", reason, *extra]
    return _run(root, locks, *args)


def _log_rows(locks: Path):
    path = locks / "claim_takeovers.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_repair_legacy_claim_succeeds_and_is_audited(tmp_path: Path) -> None:
    """④ 正常用完：占位文件删除、台账 current 置空、日志有一条 legacy_token_repair。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    _write_claim(locks, acquired=INTRO_TS - 1200.0)  # 00:57:30 那批：签发早于 sidecar

    proc = _repair(root, locks, "--by", "台账维护方/me")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "REPAIR OK" in out, out
    assert not _marker(locks).exists(), "占位文件必须完整删除，不能留半截"
    assert not _sidecar(locks).exists()

    rows = _log_rows(locks)
    assert len(rows) == 1, rows
    rec = rows[0]
    assert rec["action"] == "legacy_token_repair"
    assert rec["path"] == REL
    assert rec["old_owner"] == OWNER
    assert rec["repaired_by"] == "台账维护方/me"
    assert rec["reason"].strip()
    assert rec["token_sha256_prefix"] and rec["sidecar_missing"] is True

    registry = json.loads((locks / "claims.json").read_text(encoding="utf-8"))
    entry = registry["claims"][_key(REL)]
    assert entry["current"] is None
    assert entry["history"][-1]["released_at"] is not None
    assert entry["history"][-1]["closed_reason"] == "legacy_token_repair"

    status = _run(root, locks, "status", REL)
    assert "未占用" in status.stdout, status.stdout


def test_repair_rejects_claim_with_sidecar(tmp_path: Path) -> None:
    """反证①：有 sidecar 的正常 claim 不能用这条命令清掉。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    marker = _write_claim(locks, acquired=INTRO_TS - 1200.0, sidecar=True)

    proc = _repair(root, locks)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "REPAIR DENIED" in out and "[FAIL] sidecar 缺失" in out, out
    assert marker.exists(), "被拒时不能动占位文件"
    assert not _log_rows(locks), "被拒时不能写审计"


def test_repair_rejects_claim_issued_after_sidecar_intro(tmp_path: Path) -> None:
    """反证②：sidecar 引入之后签发的 claim，即使 sidecar 缺失也不许用这条命令。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    marker = _write_claim(locks, acquired=INTRO_TS + 600.0)  # 01:23 签发：本该有 sidecar

    proc = _repair(root, locks)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "[FAIL] 签发早于 sidecar 引入版" in out, out
    assert marker.exists()
    assert not _log_rows(locks)


def test_repair_rejects_owner_mismatch(tmp_path: Path) -> None:
    """反证③：--owner 与台账原文不一致 → 拒。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    marker = _write_claim(locks, acquired=INTRO_TS - 1200.0)

    proc = _repair(root, locks, owner="别的线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "[FAIL] --owner 与台账一致" in out, out
    assert marker.exists()


def test_repair_rejects_claim_without_token_hash(tmp_path: Path) -> None:
    """更早的旧格式（连凭据哈希都没有）不走 repair，只能等 TTL 或 takeover。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    marker = _write_claim(locks, acquired=INTRO_TS - 1200.0, token_sha=False)

    proc = _repair(root, locks)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "[FAIL] 有 token_sha256" in out, out
    assert marker.exists()


def test_repair_requires_nonempty_reason(tmp_path: Path) -> None:
    """--reason 是硬要求：参数层缺失退出 2；给空串同样拒。"""
    locks = tmp_path / "locks"
    root = tmp_path / "root"
    root.mkdir()
    marker = _write_claim(locks, acquired=INTRO_TS - 1200.0)

    missing = _run(root, locks, "legacy-token-repair", REL, "--owner", OWNER)
    assert missing.returncode == 2, missing.stdout + missing.stderr

    blank = _repair(root, locks, reason="   ")
    assert blank.returncode == 2, blank.stdout + blank.stderr
    assert "REASON" in blank.stderr.upper() or "理由" in blank.stderr, blank.stderr
    assert marker.exists()
