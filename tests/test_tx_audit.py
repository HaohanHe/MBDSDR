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
import logging
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import mbdsdr_ai.tx_guard as tx_guard_mod  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402
from mbdsdr_ai.tx_guard import (  # noqa: E402
    GATE_AUDIT_FAILED,
    GATE_EXPIRED,
    GATE_MANUAL_UNTRUSTED,
    GATE_UNKNOWN_TOKEN,
    TxAuditError,
    TxCapabilities,
    TxSession,
    action_digest,
    default_audit_path,
    get_tx_gate,
    issue_confirmation,
    record_tx_result,
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


# ── 写不进：环境变量指到不可写路径，签发失败且可见 ─────────────────────
def test_unwritable_env_path_blocks_arm_and_status_visible(
        tmp_path, monkeypatch):
    bad = _make_broken_path(tmp_path)
    monkeypatch.setenv("MBDSDR_TX_AUDIT_PATH", bad)
    log = reset_tx_audit()          # 不传 path，按环境变量解析
    reset_tx_gate()
    assert log.path == bad

    with pytest.raises(TxAuditError) as ei:
        get_tx_gate().arm(ttl_s=10, source="test")
    assert "blocker" in str(ei.value), "失败原因必须指向不可写路径"

    snap = get_tx_gate().snapshot()
    assert snap["state"] == "disarmed", "审计不可写时不得武装"
    assert snap["audit"]["fail_closed"] is True
    assert snap["audit"]["last_error"]
    assert snap["audit"]["path"] == bad
    assert snap["audit"]["write_failures"] >= 1


def test_unwritable_env_path_refuses_allowed_not_silent_allow(
        tmp_path, monkeypatch):
    """门已用正常路径武装后，审计变不可写：放行必须降级为拒绝，不许静默发波。"""
    good = reset_tx_audit(str(tmp_path / "ok.jsonl"))
    gate = reset_tx_gate()
    tok = gate.arm(ttl_s=60, source="test")
    assert _read_records(good.path)[-1]["result"] == "armed"

    bad = _make_broken_path(tmp_path)
    reset_tx_audit(bad)
    d = gate.authorize(token=tok, tool="sdr_transmit_iq", source="test")
    assert d.allowed is False
    assert d.code == "audit_write_failed"
    assert "审计写入失败" in d.reason

    snap = gate.snapshot()
    assert snap["audit"]["path"] == bad
    assert snap["audit"]["last_error"]
    # 没有产生任何 allowed 记录（旧路径里只有 armed）
    assert [r["result"] for r in _read_records(good.path)] == ["armed"]


def test_audit_failure_logs_are_throttled(tmp_path, monkeypatch, caplog):
    """连续失败 21 次只打 1 条 ERROR：发射循环不会把日志刷爆，但计数仍如实累加。"""
    bad = _make_broken_path(tmp_path)
    reset_tx_audit(bad)
    reset_tx_gate()

    with caplog.at_level(logging.ERROR, logger="mbdsdr_ai.tx_guard"):
        with pytest.raises(TxAuditError):
            get_tx_gate().arm(ttl_s=10, source="test")
        for _ in range(20):
            with pytest.raises(TxAuditError):
                get_tx_gate().arm(ttl_s=10, source="test")

    errors = [r for r in caplog.records
              if "发射审计写入失败" in r.getMessage()]
    assert len(errors) == 1, f"限流失效，实际 {len(errors)} 条"
    assert tx_audit_status()["write_failures"] == 21, "计数不能因限流丢失"


# ── 落地策略：默认路径不在 C 盘 ─────────────────────────────────────────
def _clear_audit_env(monkeypatch):
    for name in ("MBDSDR_TX_AUDIT_PATH", "MBDSDR_TX_AUDIT_MAX_MB",
                 "MBDSDR_TX_AUDIT_KEEP_FILES",
                 "MBDSDR_TX_AUDIT_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)


def test_default_path_is_not_c_drive(tmp_path, monkeypatch):
    """默认审计路径必须落在非 C 盘（本机 D:\\），且不写 C 盘用户目录。"""
    _clear_audit_env(monkeypatch)
    monkeypatch.setattr(tx_guard_mod, "_load_ai_config", lambda: None)

    path = default_audit_path()
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    assert drive != "C:", f"默认审计路径不得在 C 盘：{path}"
    home = os.path.abspath(os.path.expanduser("~")).replace("/", "\\").lower()
    assert not os.path.abspath(path).replace("/", "\\").lower().startswith(home)
    if os.path.exists("D:\\"):
        assert path.replace("/", "\\").lower().startswith("d:\\mbdsdr\\logs")


def test_config_path_and_limits_are_used(tmp_path, monkeypatch):
    """设置项（config.json）可改路径/单文件上限/保留份数/保留天数。"""
    _clear_audit_env(monkeypatch)
    target = str(tmp_path / "cfg" / "tx_audit.jsonl")

    class _Cfg:
        tx_audit_path = target
        tx_audit_max_mb = 3
        tx_audit_keep_files = 4
        tx_audit_retention_days = 30

    monkeypatch.setattr(tx_guard_mod, "_load_ai_config", lambda: _Cfg())
    log = reset_tx_audit()
    assert log.path == target
    assert log.max_bytes == 3 * 1024 * 1024
    assert log.backups == 3 and log.keep_files == 4
    assert log.retention_days == 30


def test_config_defaults_are_declared():
    """配置数据类自带审计策略默认值（设置面板据此渲染）。"""
    cfg = AgentConfig()
    assert cfg.tx_audit_path == ""
    assert cfg.tx_audit_max_mb == 50
    assert cfg.tx_audit_keep_files == 10
    assert cfg.tx_audit_retention_days == 365


# ── 轮转：超限删最旧，最新一条完整保留 ────────────────────────────────
def test_rotation_deletes_oldest_and_keeps_newest(tmp_path):
    log = reset_tx_audit(str(tmp_path / "rot2.jsonl"),
                         max_bytes=4096, backups=2, retention_days=3650)
    reset_tx_gate()
    for i in range(60):
        get_tx_gate().arm(ttl_s=10, tool=f"tool-{i}", source="test")

    for suffix in ("", ".1", ".2"):
        assert os.path.exists(log.path + suffix), f"缺少轮转文件 {suffix}"
    assert not os.path.exists(log.path + ".3"), "超过保留份数必须删最旧"

    newest = _read_records(log.path)[-1]
    assert newest["result"] == "armed"
    assert newest["tool"] == "tool-59", "最新一条必须完整保留"

    all_tools = []
    for suffix in ("", ".1", ".2"):
        all_tools += [r["tool"] for r in _read_records(log.path + suffix)]
    assert "tool-0" not in all_tools, "最旧记录应被删除（不是停止写入）"


# ── 密钥打码：文件里绝不出现原始 API key / 凭证 ────────────────────────
def test_secrets_are_redacted_in_audit_file(_audit_log):
    api_key = "sk-proj-1234567890abcdef"
    bearer = "sk-live-ABCDEFGHIJKLMNOP"
    ok = record_tx_result(
        tool="sdr_transmit_iq", freq_hz=435_000_000.0,
        args_summary=json.dumps({"freq_hz": 435_000_000.0,
                                 "api_key": api_key,
                                 "Authorization": "Bearer " + bearer}),
        ok=False, code="tx_write_failed",
        reason=f"鉴权失败 api_key={api_key} Authorization: Bearer {bearer}",
        source="test")
    assert ok

    raw = _raw(_audit_log.path)
    assert api_key not in raw, "原始 API key 不得落盘"
    assert bearer not in raw, "原始 Bearer 凭证不得落盘"
    assert "***" in raw

    rec = _read_records(_audit_log.path)[-1]
    assert rec["result"] == "tx_failed"
    assert rec["freq_hz"] == 435_000_000.0
    summary = json.loads(rec["args_summary"])
    assert summary["api_key"] == "***"
    assert summary["Authorization"] == "***"
    assert api_key not in rec["reason"] and bearer not in rec["reason"]


def test_args_summary_redacts_secret_named_keys():
    """即便调用方忘记剔除，键名疑似密钥的参数值也不会进摘要。"""
    from mbdsdr_ai.tx_guard import _args_summary
    text = _args_summary({"freq_hz": 435_000_000.0,
                          "api_key": "sk-abcdef123456",
                          "password": "hunter2"})
    assert "sk-abcdef123456" not in text
    assert "hunter2" not in text
    assert "***" in text


# ── 发射结果记录：成功/失败都落一条，且写失败不阻断 ────────────────────
class _FakeTxBackend:
    def __init__(self, write_ok=True):
        self.write_ok = write_ok
        self.freq = None

    def supports_tx(self):
        return True

    def set_frequency(self, hz):
        self.freq = hz

    def device_alive(self):
        return True

    def is_underrun(self):
        return False

    def stop_tx(self):
        pass

    def write_tx(self, block):
        from mbdsdr_ai.tx_guard import tx_authorized
        return self.write_ok and tx_authorized()


_CAPS = TxCapabilities(
    known=True, freq_range_hz=(1_000_000.0, 6_000_000_000.0),
    bandwidth_range_hz=(2_000_000.0, 20_000_000.0),
    gain_range_db=(0.0, 47.0), max_duration_s=600.0,
    duplex="half", source="测试参数表")


def _tx_session_records(_audit_log, backend):
    tok = get_tx_gate().arm(ttl_s=60, tool="sdr_transmit_iq", source="test")
    rep = TxSession(backend).transmit(
        np.ones(12, dtype=np.complex64), freq_hz=435_000_000.0,
        tx_token=tok, caps=_CAPS, chunk_samples=4)
    return rep, _read_records(_audit_log.path)[-1]


def test_tx_session_records_ok_result(_audit_log):
    rep, rec = _tx_session_records(_audit_log, _FakeTxBackend())
    assert rep["state"] == "completed"
    assert rec["result"] == "tx_ok"
    assert rec["tool"] == "TxSession.transmit"
    assert rec["freq_hz"] == 435_000_000.0
    assert rec["code"] == "ok"
    assert rec["ts_local"] and rec["ts_utc"]
    assert len(rec["token_prefix"]) == 8


def test_tx_session_records_failed_result(_audit_log):
    rep, rec = _tx_session_records(_audit_log, _FakeTxBackend(write_ok=False))
    assert rep["state"] == "failed"
    assert rec["result"] == "tx_failed"
    assert rec["code"] == "tx_write_failed"
    assert "write_tx 返回失败" in rec["reason"]


def test_result_record_failure_is_visible_not_raise(tmp_path):
    """发射后结果记录写不进：不抛异常，只在状态里如实计数。"""
    reset_tx_audit(_make_broken_path(tmp_path))
    assert record_tx_result(tool="TxSession.transmit", ok=True) is False
    assert tx_audit_status()["write_failures"] >= 1
    assert tx_audit_status()["last_error"]
