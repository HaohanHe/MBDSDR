# SPDX-License-Identifier: MIT
"""tests/test_decode_audit.py — 结构化解码审计日志（P5 之后的无阻塞工作）。

守三件事：
  1. 开关语义：未设 `MBDSDR_DECODE_AUDIT_DIR` 时零写入；设了才写 JSONL；
  2. 审计只记元数据——报文正文（可能含真实呼号/电话/位置）**不进审计**；
  3. 红线：审计写入失败绝不影响解码结果，只打到 stderr。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import decode_audit  # noqa: E402
from mbdsdr_ai.decode_audit import (  # noqa: E402
    AUDIT_ENV, AUDIT_FILENAME, append_audit, audit_record, maybe_audit,
)
from mbdsdr_ai.decode_router import run_digital_mode  # noqa: E402


@pytest.fixture(autouse=True)
def _no_audit_env(monkeypatch):
    monkeypatch.delenv(AUDIT_ENV, raising=False)


def _read_lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_disabled_writes_nothing(tmp_path):
    assert maybe_audit("decode_digital_mode", {"mode": "ais", "ok": True}) is None
    assert not (tmp_path / AUDIT_FILENAME).exists()


def test_enabled_appends_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path))
    path = maybe_audit("decode_digital_mode", {"mode": "ais", "ok": True})
    assert path == str(tmp_path / AUDIT_FILENAME)
    lines = _read_lines(path)
    assert len(lines) == 1
    assert lines[0]["tool"] == "decode_digital_mode"
    assert lines[0]["mode"] == "ais"
    assert lines[0]["ok"] is True


def test_repeated_calls_append_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path))
    maybe_audit("t", {"mode": "ais"})
    maybe_audit("t", {"mode": "navtex"})
    lines = _read_lines(os.path.join(str(tmp_path), AUDIT_FILENAME))
    assert [r["mode"] for r in lines] == ["ais", "navtex"]


def test_record_has_expected_fields_and_no_message_body():
    result = {
        "mode": "pocsag", "ok": True, "status": "decoded", "error_code": "",
        "evidence_level": "partial", "output_kind": "messages",
        "num_messages": 2,
        "messages": [{"text": "HELLO 1234567"}, {"text": "SECOND"}],
        "error": "",
        "input_file": "",
    }
    rec = audit_record("decode_digital_mode", result)
    assert rec["num_messages"] == 2
    assert rec["messages_recorded"] is False
    assert "messages" not in rec
    assert "HELLO" not in json.dumps(rec, ensure_ascii=False)


def test_record_clips_long_error():
    rec = audit_record("t", {"mode": "x", "error": "E" * 1000})
    assert len(rec["error"]) == decode_audit._MAX_TEXT
    assert rec["error"] == "E" * decode_audit._MAX_TEXT


def test_fingerprint_matches_file(tmp_path):
    import hashlib

    p = tmp_path / "cap.cf32"
    data = bytes(range(32))
    p.write_bytes(data)
    rec = audit_record("t", {"mode": "ais"}, str(p))
    assert rec["input_bytes"] == len(data)
    assert rec["input_sha256_first1mb"] == hashlib.sha256(data).hexdigest()


def test_fingerprint_missing_file_is_honest(tmp_path):
    rec = audit_record("t", {"mode": "ais"}, str(tmp_path / "nope.cf32"))
    assert rec["input_bytes"] == 0
    assert rec["input_sha256_first1mb"] == ""


def test_write_failure_never_breaks_decode(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(AUDIT_ENV, str(blocker))     # 父路径是文件，makedirs 必失败
    out = maybe_audit("decode_digital_mode", {"mode": "ais", "ok": True})
    assert out is None
    assert "decode-audit" in capsys.readouterr().err


def test_router_audits_unknown_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path))
    res = run_digital_mode("whatever.cf32", "not_a_mode")
    assert res["ok"] is False
    assert res["error_code"] == "unknown_mode"
    lines = _read_lines(os.path.join(str(tmp_path), AUDIT_FILENAME))
    assert len(lines) == 1
    assert lines[0]["error_code"] == "unknown_mode"
    assert lines[0]["ok"] is False


def test_router_audits_missing_sample(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path))
    missing = tmp_path / "absent.cf32"
    res = run_digital_mode(str(missing), "ais", sample_rate=192000)
    assert res["error_code"] == "missing_sample"
    rec = _read_lines(os.path.join(str(tmp_path), AUDIT_FILENAME))[0]
    assert rec["error_code"] == "missing_sample"
    assert rec["input_file"] == str(missing)
    assert rec["input_bytes"] == 0


def test_router_result_unaffected_when_audit_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv(AUDIT_ENV, raising=False)
    baseline = run_digital_mode(str(tmp_path / "absent.cf32"), "ais")
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path / "audit"))
    audited = run_digital_mode(str(tmp_path / "absent.cf32"), "ais")
    assert audited == baseline


def test_append_audit_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b"
    path = append_audit(str(nested / AUDIT_FILENAME), {"tool": "t"})
    assert os.path.exists(path)
