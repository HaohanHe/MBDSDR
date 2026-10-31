# SPDX-License-Identifier: MIT
"""tools/protocol_verify_step.py 与 acceptance_lib 协议步组装的离线单测。

红线与 acceptance_run 一致：禁 mock、诚实 SKIP。golden 步无硬件依赖必须
PASS；ota 步缺录制件必须 SKIP（不是 FAIL）；acceptance_lib 对协议步
raw 行的组装不二次解释（status 直给）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.acceptance_lib import _step_from_raw  # noqa: E402

STEP = ROOT / "tools" / "protocol_verify_step.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STEP), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def test_golden_pass():
    r = _run("golden")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout.strip().splitlines()[-1])
    assert doc["id"] == "protocol_golden"
    assert doc["status"] == "PASS"
    assert doc["provenance"] == "synthetic_encoder_generated"


def test_ota_missing_sample_skip_not_fail():
    """缺真实录制件 → SKIP + 原因（诚实空态，绝不假 FAIL 也绝不假 PASS）。"""
    r = _run("ota")
    assert r.returncode == 2
    doc = json.loads(r.stdout.strip().splitlines()[-1])
    assert doc["id"] == "protocol_ota"
    assert doc["status"] == "SKIP"
    assert "reason" in doc


def test_ota_empty_file_skip():
    """空录制件 → SKIP（诚实空态，不伪造样本）。"""
    empty = ROOT / "tools" / ".acceptance_logs" / "test_empty.c64"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")
    try:
        r = _run("ota", "--iq", str(empty))
        assert r.returncode == 2
        doc = json.loads(r.stdout.strip().splitlines()[-1])
        assert doc["status"] == "SKIP"
        assert "空" in doc["reason"]
    finally:
        empty.unlink(missing_ok=True)


def test_acceptance_lib_protocol_branch():
    """acceptance_lib 组装：status 直给不二次解释；SKIP 带原因。"""
    ok = _step_from_raw({"id": "protocol_golden", "status": "PASS",
                         "detail": "全链 PASS", "n_pass": 4,
                         "provenance": "synthetic_encoder_generated"})
    assert ok["status"] == "PASS"
    assert "断言 PASS 4 条" in ok["key_output"]
    assert "synthetic_encoder_generated" in ok["key_output"]
    skip = _step_from_raw({"id": "protocol_ota", "status": "SKIP",
                           "reason": "无录制件"})
    assert skip["status"] == "SKIP"
    assert skip["skip_reason"] == "无录制件"
    bad = _step_from_raw({"id": "protocol_ota", "status": "BOGUS"})
    assert bad["status"] == "FAIL"  # 非法状态不静默吞
