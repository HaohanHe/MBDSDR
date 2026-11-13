# SPDX-License-Identifier: MIT
"""审计里的「可疑占位」一节（只报不拦）。

一条写着别人名字、又没有理由的占位，等于**不知道谁占的、也不知道为什么占**，
它比没有占位更糟——别人会以为这里是安全的。本文件钉四件事：

  ① 冒名 / 空理由的活跃 claim 必须入列；
  ② 两个标记都干净的活跃 claim 不入列；
  ③ 过期 claim 不入列（判活与闸共用 ``claim_active``）；
  ④ 旧占位没有 ``owner_mismatch`` 键时显示「未记录」，**不**当成冒名；
     没有 ``reason_missing`` 键时退回读 ``claim_reason``（空串才算空理由）。

并且：审计恒以 0 退出——这一节只报，不拦。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "audit_unclaimed_changes.py"
sys.path.insert(0, str(REPO / "scripts"))
import audit_unclaimed_changes as audit  # noqa: E402


def _marker(directory: Path, name: str, **overrides) -> Path:
    now = time.time()
    payload = {
        "path": name,
        "key": name,
        "owner": "甲线",
        "acquired_at": now,
        "heartbeat_at": now,
        "ttl_min": 10.0,
        "pid": 1,
    }
    payload.update(overrides)
    path = directory / f"{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}.claim"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_mismatched_and_reasonless_claim_is_listed(tmp_path):
    _marker(tmp_path, "scripts/safe_commit.py", issued_by="乙线",
            owner_mismatch=True, reason_missing=True, claim_reason="")
    rows = audit._flagged_claims(time.time(), tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "scripts/safe_commit.py"
    assert row["owner"] == "甲线"
    assert row["issued_by"] == "乙线"
    assert row["owner_mismatch"] is True
    assert row["reason_missing"] is True


def test_clean_claim_is_not_listed(tmp_path):
    _marker(tmp_path, "scripts/a.py", issued_by="甲线",
            owner_mismatch=False, reason_missing=False, claim_reason="K25 断言补证")
    assert audit._flagged_claims(time.time(), tmp_path) == []


def test_expired_claim_is_not_listed(tmp_path):
    old = time.time() - 3600
    _marker(tmp_path, "scripts/b.py", acquired_at=old, heartbeat_at=old,
            owner_mismatch=True, reason_missing=True)
    assert audit._flagged_claims(time.time(), tmp_path) == []


def test_legacy_marker_without_flags_is_not_called_impersonation(tmp_path):
    """旧占位没有 owner_mismatch 键：显示「未记录」，不升级成冒名。"""
    _marker(tmp_path, "scripts/c.py", claim_reason="K25 断言补证")
    assert audit._flagged_claims(time.time(), tmp_path) == []


def test_legacy_marker_with_empty_reason_is_listed_as_reason_missing(tmp_path):
    """旧占位没有 reason_missing 键：退回读 claim_reason，空串算空理由。"""
    _marker(tmp_path, "scripts/d.py", claim_reason="")
    rows = audit._flagged_claims(time.time(), tmp_path)
    assert len(rows) == 1
    assert rows[0]["owner_mismatch"] is None  # 未记录，不是 False
    assert rows[0]["reason_missing"] is True


def test_cli_prints_section_and_exits_zero(tmp_path):
    _marker(tmp_path, "scripts/safe_commit.py", issued_by="乙线",
            owner_mismatch=True, reason_missing=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--markers", str(tmp_path)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "可疑占位（只报不拦）：1 条" in proc.stdout
    assert "scripts/safe_commit.py" in proc.stdout
    assert "issued_by=乙线" in proc.stdout


def test_cli_json_shape_has_both_sections(tmp_path):
    _marker(tmp_path, "scripts/e.py", owner_mismatch=True, reason_missing=False)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--markers", str(tmp_path)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data) == {"unclaimed", "flagged_claims"}
    assert [row["path"] for row in data["flagged_claims"]] == ["scripts/e.py"]
