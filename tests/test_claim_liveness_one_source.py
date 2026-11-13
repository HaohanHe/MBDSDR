# SPDX-License-Identifier: MIT
"""判据同源：闸与按需审计的「活跃」判定必须来自 ``claim_window.claim_active``。

背景（2026-11-13）：审计脚本一度自己写 ``now - heartbeat > ttl``，闸写的是
``heartbeat + ttl <= now``——「正好到期」那一秒，审计说还有主、闸说已过期。
两边都是「看起来对」的实现，结论相反，谁都能引一条支持自己。

反证：同一条占位文件设成 55 分钟 / 61 分钟，闸（pre-commit 走的
``active_claims_for_paths``）与审计（``_active_owner``）必须给同一个答案；
60 分钟的边界也必须一致（都判过期）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import file_claim as fc  # noqa: E402
import claim_window as cw  # noqa: E402
import check_claim_coordination as gate  # noqa: E402
import audit_unclaimed_changes as audit  # noqa: E402


def _write_marker(tmp_path: Path, path: str, age_min: float, ttl_min: float = 60.0) -> float:
    """在临时目录放一条占位文件，心跳设为 ``age_min`` 分钟前。"""
    key, _display = fc.canonical_path(path)
    marker = Path(tmp_path) / fc._marker_for(key).name
    now = time.time()
    heartbeat = now - age_min * 60.0
    payload = {
        "path": path,
        "owner": "判据线/6ae85f56",
        "ttl_min": ttl_min,
        "acquired_at": heartbeat,
        "heartbeat_at": heartbeat,
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return now


def test_liveness_function_lives_in_claim_window():
    """两侧都 import 它，且谁都不再自己算 TTL。"""
    assert callable(cw.claim_active)
    for name in ("check_claim_coordination.py", "audit_unclaimed_changes.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "claim_active" in src, f"{name} 没有用 claim_active"
        assert 'payload.get("ttl_min")' not in src, f"{name} 仍在自算 TTL"


def test_55_minutes_both_say_active(tmp_path):
    now = _write_marker(tmp_path, "desktop/x.py", age_min=55)

    gate_hits = gate.active_claims_for_paths(
        ["desktop/x.py"], now=now, marker_dir=tmp_path)
    gate_owner = dict(gate_hits).get("desktop/x.py", "") if gate_hits else ""
    audit_owner = audit._active_owner("desktop/x.py", now, marker_dir=tmp_path)

    assert gate_owner == "判据线/6ae85f56"
    assert audit_owner == gate_owner


def test_61_minutes_both_say_expired(tmp_path):
    now = _write_marker(tmp_path, "desktop/x.py", age_min=61)

    gate_hits = gate.active_claims_for_paths(
        ["desktop/x.py"], now=now, marker_dir=tmp_path)
    audit_owner = audit._active_owner("desktop/x.py", now, marker_dir=tmp_path)

    assert gate_hits == []
    assert audit_owner == ""


def test_exact_boundary_both_say_expired(tmp_path):
    """边界：``heartbeat + ttl == now`` 视为已过期——两侧必须一致。"""
    key, _display = fc.canonical_path("desktop/x.py")
    marker = Path(tmp_path) / fc._marker_for(key).name
    now = 1_800_000_000.0
    payload = {
        "path": "desktop/x.py",
        "owner": "判据线/6ae85f56",
        "ttl_min": 60.0,
        "acquired_at": now - 3600.0,
        "heartbeat_at": now - 3600.0,
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert gate.active_claims_for_paths(
        ["desktop/x.py"], now=now, marker_dir=tmp_path) == []
    assert audit._active_owner("desktop/x.py", now, marker_dir=tmp_path) == ""
