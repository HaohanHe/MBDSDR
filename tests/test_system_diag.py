# SPDX-License-Identifier: MIT
"""scripts/system_diag.py 的聚合契约测试（假结果，不碰硬件）。

钉住的是聚合层的判定口径：只看两侧退出码、GNSS 天线 warning 不算失败、
总体退出码 0/1/2，以及 --json 的字段。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "system_diag.py")


def _load():
    spec = importlib.util.spec_from_file_location("system_diag", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stage(name, ok=True, detail="ok"):
    return {"name": name, "ok": ok, "detail": detail, "elapsed_ms": 1.0}


def _sdr(verdict="ok", exit_code=0):
    return {"device": "RTL-SDR [rtlsdr:SN]", "verdict": verdict,
            "exit_code": exit_code, "stages": [_stage("enumerate")]}


def _gnss(verdict="ok", exit_code=0, antenna_status="unknown",
          warning=False):
    return {
        "port": "COM4", "baud": 9600, "verdict": verdict,
        "exit_code": exit_code, "stages": [_stage("open")],
        "fix": {}, "antenna": {"status": antenna_status, "text": None,
                               "warning": warning},
    }


def test_ok_and_ok_exit_0(capsys):
    m = _load()
    rc = m.main([], sdr_runner=lambda _a: _sdr(),
                gnss_runner=lambda _a: _gnss())
    assert rc == m.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "样本正常" in out and "定位正常" in out
    assert "两侧都通过" in out


def test_ok_sdr_with_failed_gnss_is_degraded(capsys):
    m = _load()
    rc = m.main([], sdr_runner=lambda _a: _sdr(),
                gnss_runner=lambda _a: _gnss("no_fix", 4))
    assert rc == m.EXIT_DEGRADED == 2
    out = capsys.readouterr().out
    assert "部分失败" in out
    assert "[GNSS]" in out  # 只为失败一侧给建议


def test_both_failed_exit_1(capsys):
    m = _load()
    rc = m.main([], sdr_runner=lambda _a: _sdr("no_samples", 4),
                gnss_runner=lambda _a: _gnss("open_failed", 2))
    assert rc == m.EXIT_FAILED == 1
    out = capsys.readouterr().out
    assert "两侧都失败" in out
    assert "[SDR]" in out and "[GNSS]" in out


def test_antenna_warning_does_not_fail():
    m = _load()
    result = m.run_diagnostics(
        sdr_runner=lambda _a: _sdr(),
        gnss_runner=lambda _a: _gnss(antenna_status="open", warning=True))
    assert result.verdict == "ok"
    assert result.exit_code == 0
    assert result.summary == "SDR：样本正常；GNSS：定位正常（天线开路）"


def test_sdr_failure_summary_text():
    m = _load()
    result = m.run_diagnostics(
        sdr_runner=lambda _a: _sdr("no_samples", 4),
        gnss_runner=lambda _a: _gnss(antenna_status="open", warning=True))
    assert result.verdict == "degraded"
    assert result.summary == ("SDR：无样本（硬件数据通路）；"
                              "GNSS：定位正常（天线开路）")


def test_json_fields(capsys):
    m = _load()
    rc = m.main(["--json"], sdr_runner=lambda _a: _sdr("no_samples", 4),
                gnss_runner=lambda _a: _gnss(antenna_status="open", warning=True))
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"sdr", "gnss", "verdict", "exit_code", "summary"}
    assert payload["verdict"] == "degraded"
    assert payload["exit_code"] == 2
    for side in ("sdr", "gnss"):
        assert "verdict" in payload[side]
        assert "exit_code" in payload[side]
        assert isinstance(payload[side]["stages"], list)
    assert payload["gnss"]["antenna"]["status"] == "open"


def test_runner_exception_counts_as_failure():
    m = _load()

    def _boom(_args):
        raise RuntimeError("模拟崩溃")

    result = m.run_diagnostics(
        sdr_runner=_boom, gnss_runner=lambda _a: _gnss())
    assert result.sdr["verdict"] == "runner_error"
    assert "模拟崩溃" in result.sdr["error"]
    assert result.verdict == "degraded"
    assert result.exit_code == 2
