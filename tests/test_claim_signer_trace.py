# SPDX-License-Identifier: MIT
"""冒名占位 / 空理由的留痕（2026-11-13 裁定）。

背景：`scripts/safe_commit.py` 上出现过一条 owner 写「收尸线」、理由为空、
时间 20:41:49–20:42:24 的 claim，不是该线签发的。追到根：`cmd_claim` 既不记
「谁签发的」，也不校验理由非空——**名字可以是别人的，理由可以是空的，而台账
看不出来**。

裁定（用户原话）：空理由**不升级为必填**——会一次打红大量既有调用，而且
「理由是否可信」给不了机器判据。但两类痕迹必须能被看见：

  ① `--owner` 与签发身份一致：不警告、不落 `owner_mismatch`；
  ② 不一致：stderr 有 WARN，占位文件与台账都落 `owner_mismatch=true` + `issued_by`；
  ③ 空理由：落 `reason_missing=true`，**退出码仍是 0**（留痕不是收紧）；
  ④ 正常带理由且一致：两个标记都为 false。

签发身份 = `MBDSDR_CLAIM_OWNER`，缺省读 `git config mbdsdr.owner`；都没有时
为空串——空串**不算冒名**（未声明 ≠ 别人签的）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
SIGNER_ENV = "MBDSDR_CLAIM_OWNER"
TARGET = "a.txt"


def _env(locks: Path, signer: str | None, root: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(root)
    env.pop("PYTHONPATH", None)
    env.pop(SIGNER_ENV, None)  # 不许真实会话的身份漏进测试
    if signer is not None:
        env[SIGNER_ENV] = signer
    return env


def _run(locks: Path, signer: str | None, *args: str, root: Path = REPO):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "claim", TARGET, *args],
        cwd=str(root),
        env=_env(locks, signer, root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60.0,
    )


def _payload(locks: Path) -> dict:
    markers = sorted((locks / "claims").glob("*.claim"))
    assert len(markers) == 1, f"应当恰好一个占位文件：{markers}"
    return json.loads(markers[0].read_text(encoding="utf-8"))


def _history(locks: Path) -> dict:
    data = json.loads((locks / "claims.json").read_text(encoding="utf-8"))
    (entry,) = data["claims"].values()
    return entry["history"][-1]


def test_matching_owner_has_no_mismatch_flag():
    with tempfile.TemporaryDirectory() as raw:
        locks = Path(raw)
        proc = _run(locks, "甲线", "--owner", "甲线", "--ttl-min", "1")
        assert proc.returncode == 0, proc.stderr
        payload = _payload(locks)
        assert payload["issued_by"] == "甲线"
        assert payload["owner_mismatch"] is False
        assert "owner_mismatch" not in (proc.stdout + proc.stderr)


def test_mismatched_owner_warns_and_flags_both_surfaces():
    with tempfile.TemporaryDirectory() as raw:
        locks = Path(raw)
        proc = _run(locks, "乙线", "--owner", "甲线", "--ttl-min", "1")
        assert proc.returncode == 0, proc.stderr
        assert "WARN 冒名痕迹" in proc.stderr
        assert "issued_by=乙线" in proc.stderr
        payload = _payload(locks)
        assert payload["owner_mismatch"] is True
        assert payload["issued_by"] == "乙线"
        assert payload["owner"] == "甲线"
        assert _history(locks)["owner_mismatch"] is True
        assert _history(locks)["issued_by"] == "乙线"


def test_empty_reason_is_flagged_but_still_succeeds():
    with tempfile.TemporaryDirectory() as raw:
        locks = Path(raw)
        proc = _run(locks, "甲线", "--owner", "甲线", "--ttl-min", "1")
        assert proc.returncode == 0, proc.stderr
        payload = _payload(locks)
        assert payload["reason_missing"] is True
        assert payload["claim_reason"] == ""
        assert _history(locks)["reason_missing"] is True


def test_named_reason_and_matching_owner_are_clean():
    with tempfile.TemporaryDirectory() as raw:
        locks = Path(raw)
        proc = _run(
            locks, "甲线", "--owner", "甲线", "--ttl-min", "1",
            "--reason", "K25 断言补证还没跑完",
        )
        assert proc.returncode == 0, proc.stderr
        payload = _payload(locks)
        assert payload["owner_mismatch"] is False
        assert payload["reason_missing"] is False
        history = _history(locks)
        assert history["owner_mismatch"] is False
        assert history["reason_missing"] is False


def test_undeclared_signer_is_not_a_mismatch():
    """未声明签发身份 ≠ 别人签的：不许把「读不到」记成「冒名」。"""
    with tempfile.TemporaryDirectory() as raw:
        locks = Path(raw)
        bare_root = Path(raw) / "bare"  # 非 git 仓库 → git config 取不到
        bare_root.mkdir()
        proc = _run(locks, None, "--owner", "甲线", "--ttl-min", "1", root=bare_root)
        assert proc.returncode == 0, proc.stderr
        payload = _payload(locks)
        assert payload["issued_by"] == ""
        assert payload["owner_mismatch"] is False
        assert "WARN 冒名痕迹" not in proc.stderr
