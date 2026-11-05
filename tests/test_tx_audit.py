# SPDX-License-Identifier: MIT
"""发射门落盘审计反证（批次 C-一）。

反证三条：
1. 签发一次 → 审计文件多一条 ``armed``（且只记 token 前 8 位，搜不到全串）；
2. 模型自报 ``arm_manual`` → ``refused`` + ``gate_arm_manual_untrusted``；
3. 无效 token → ``refused``（``gate_unknown_token``）。

另验证过期落 ``expired``、允许落 ``allowed``、按大小轮转、以及审计写失败时
fail-closed（不签发 / 不放行，状态可见）。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tx_guard import (  # noqa: E402
    GATE_AUDIT_FAILED,
    GATE_EXPIRED,
    GATE_MANUAL_UNTRUSTED,
    GATE_UNKNOWN_TOKEN,
    TxAuditError,
    action_digest,
    get_tx_gate,
    issue_confirmation,
    reset_tx_audit,
    reset_tx_gate,
    tx_audit_status,
)


@pytest.fixture(autouse=True)
def _audit_log(tmp_path):
    log = reset_tx_audit(str(tmp_path / "tx_audit.jsonl"))
    reset_tx_gate()
    yield log
    reset_tx_audit()
    reset_tx_gate()


def _read_records(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _raw(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _make_broken_path(tmp_path):
    """构造一个"父路径是文件"的非法审计路径（makedirs 必失败）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    return str(blocker / "tx_audit.jsonl")


# ── 反证 1：签发落 armed ────────────────────────────────────────────────
def test_issue_confirmation_writes_armed(_audit_log):
    args = {"freq_hz": 435_000_000.0, "confirm_token": "gate-param-must-not-leak"}
    tok = issue_confirmation("sdr_transmit_iq", args, ttl_s=30, source="test")

    recs = _read_records(_audit_log.path)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["result"] == "armed"
    assert rec["tool"] == "sdr_transmit_iq"
    assert rec["token_prefix"] == tok[:8]
    assert len(rec["token_prefix"]) == 8
    assert rec["ttl_s"] == 30.0
    assert rec["source"] == "test"
    assert rec["ts_local"] and rec["ts_utc"]
    # 门参数不进参数摘要
    assert "confirm_token" not in rec["args_summary"]
    assert "gate-param-must-not-leak" not in rec["args_summary"]
    # 完整 token 绝不落盘
    raw = _raw(_audit_log.path)
    assert tok not in raw
    assert tok[:8] in raw


# ── 反证 2：模型自报 arm_manual → refused + 原因码 ──────────────────────
def test_model_arm_manual_is_refused_with_reason_code(_audit_log):
    d = get_tx_gate().authorize(
        arm_manual=True,
        tool="code_modify_file",
        args_summary='{"file_path":"mbdsdr_ai/agent.py"}',
        source="test",
    )
    assert d.allowed is False
    assert d.code == GATE_MANUAL_UNTRUSTED

    rec = _read_records(_audit_log.path)[-1]
    assert rec["result"] == "refused"
    assert rec["code"] == GATE_MANUAL_UNTRUSTED
    assert rec["tool"] == "code_modify_file"
    assert rec["source"] == "test"
    assert rec["token_prefix"] == ""


# ── 反证 3：无效 token → refused ────────────────────────────────────────
def test_invalid_token_is_refused(_audit_log):
    bogus = "deadbeef" + "0" * 24
    d = get_tx_gate().authorize(token=bogus, tool="sdr_transmit_cw",
                                source="test")
    assert d.allowed is False
    assert d.code == GATE_UNKNOWN_TOKEN

    rec = _read_records(_audit_log.path)[-1]
    assert rec["result"] == "refused"
    assert rec["code"] == GATE_UNKNOWN_TOKEN
    assert rec["token_prefix"] == bogus[:8]
    # 提交的无效 token 也只留前 8 位
    assert bogus not in _raw(_audit_log.path)


# ── 放行 / 过期 ─────────────────────────────────────────────────────────
def test_allowed_record_keeps_only_prefix(_audit_log):
    args = {"freq_hz": 435_000_000.0}
    tok = issue_confirmation("sdr_transmit_iq", args, ttl_s=30, source="test")
    d = get_tx_gate().authorize(
        token=tok, action=action_digest("sdr_transmit_iq", args),
        tool="sdr_transmit_iq", args_summary='{"freq_hz":435000000.0}',
        source="test")
    assert d.allowed is True

    rec = _read_records(_audit_log.path)[-1]
    assert rec["result"] == "allowed"
    assert rec["code"] == "ok"
    assert rec["token_prefix"] == tok[:8]
    assert tok not in _raw(_audit_log.path)


def test_expired_recorded_as_expired(_audit_log):
    gate = get_tx_gate()
    tok = gate.arm(ttl_s=0.01, now=0.0, tool="sdr_transmit_iq", source="test")
    d = gate.authorize(token=tok, now=1.0, tool="sdr_transmit_iq",
                       source="test")
    assert d.allowed is False
    assert d.code == GATE_EXPIRED

    rec = _read_records(_audit_log.path)[-1]
    assert rec["result"] == "expired"
    assert rec["code"] == GATE_EXPIRED
    assert rec["token_prefix"] == tok[:8]


# ── 轮转（单文件上限 × 保留份数）────────────────────────────────────────
def test_rotation_by_size(tmp_path):
    log = reset_tx_audit(str(tmp_path / "rot.jsonl"),
                         max_bytes=4096, backups=2)
    reset_tx_gate()
    for i in range(60):
        get_tx_gate().arm(ttl_s=10, tool=f"tool_{i}", source="test")
    assert os.path.exists(log.path)
    assert os.path.exists(log.path + ".1"), "超过大小上限后必须轮转"
    assert os.path.getsize(log.path) < 4096 + 2048
    # 轮转上限：只保留 backups 份历史
    assert not os.path.exists(log.path + ".3")

    # 全部历史里都搜不到完整 token：逐条记录只可能含前 8 位。
    for p in [log.path, log.path + ".1", log.path + ".2"]:
        for rec in _read_records(p):
            assert len(rec["token_prefix"]) <= 8


# ── fail-closed：审计写失败不许静默放行 ──────────────────────────────────
def test_audit_failure_blocks_arm(tmp_path):
    reset_tx_audit(_make_broken_path(tmp_path))
    reset_tx_gate()
    with pytest.raises(TxAuditError):
        get_tx_gate().arm(ttl_s=10, source="test")

    snap = get_tx_gate().snapshot()
    assert snap["state"] == "disarmed", "审计写失败时门不得被武装"
    status = tx_audit_status()
    assert status["fail_closed"] is True
    assert status["last_error"], "写失败必须可见"
    assert status["write_failures"] >= 1


def test_audit_failure_blocks_allowed(tmp_path):
    good = reset_tx_audit(str(tmp_path / "ok.jsonl"))
    gate = reset_tx_gate()
    tok = gate.arm(ttl_s=60, source="test")
    assert _read_records(good.path)[-1]["result"] == "armed"

    reset_tx_audit(_make_broken_path(tmp_path))
    d = gate.authorize(token=tok, tool="sdr_transmit_iq", source="test")
    assert d.allowed is False
    assert d.code == GATE_AUDIT_FAILED
    assert "审计写入失败" in d.reason
    assert gate.snapshot()["audit"]["last_error"]


def test_source_defaults_are_classified(tmp_path, monkeypatch):
    monkeypatch.delenv("MBDSDR_TX_AUDIT_PATH", raising=False)
    log = reset_tx_audit(str(tmp_path / "src.jsonl"))
    get_tx_gate().arm(ttl_s=10)
    rec = _read_records(log.path)[-1]
    assert rec["source"] == "test"
