# SPDX-License-Identifier: MIT
"""
tests/test_satdump_live_envelope.py
====================================
验证 SatDump 封装修复（2026-11-02 材料审计发现）:

  1. live 命令走上游选项式（--source/--samplerate/--frequency/--gain），
     不再是错位的位置参数;
  2. ToolResult.success 严格跟随内层执行结果（rc!=0 -> success=False），
     不再永远 success=True;
  3. process 同样映射。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.satdump_integration as si  # noqa: E402
import mbdsdr_ai.sdr_tools as st  # noqa: E402


class _RC:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = "ok"
        self.stderr = ""


def _patch_run(monkeypatch, rc):
    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return _RC(rc)

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    monkeypatch.setattr(si, "find_satdump", lambda: "/usr/bin/satdump")
    return calls


def test_live_command_upstream_options(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch, 0)
    st._satdump_result({"satellite": "noaa19_apt", "frequency_hz": 137.1e6,
                        "output_dir": str(tmp_path)}, st._satdump_live)
    cmd = calls[0]
    assert cmd[1] == "live"
    assert cmd[2] == "noaa19_apt"
    assert cmd[3] == str(tmp_path)
    for opt in ("--source", "--samplerate", "--frequency", "--gain"):
        assert opt in cmd, f"缺上游选项 {opt}: {cmd}"
    assert "--source" in cmd and "rtlsdr" in cmd
    assert "137100000" in cmd


def test_live_success_follows_returncode(monkeypatch, tmp_path):
    _patch_run(monkeypatch, 1)
    res = st._satdump_result({"satellite": "noaa19_apt",
                              "frequency_hz": 137.1e6,
                              "output_dir": str(tmp_path)}, st._satdump_live)
    assert res.success is False, "rc!=0 时信封不得报成功"


def test_live_success_true_on_zero(monkeypatch, tmp_path):
    _patch_run(monkeypatch, 0)
    res = st._satdump_result({"satellite": "noaa19_apt",
                              "frequency_hz": 137.1e6,
                              "output_dir": str(tmp_path)}, st._satdump_live)
    assert res.success is True


def test_process_success_follows_returncode(monkeypatch, tmp_path):
    _patch_run(monkeypatch, 2)
    res = st._satdump_result({"input_file": str(tmp_path / "x.cu8"),
                              "satellite": "noaa19_apt",
                              "output_dir": str(tmp_path)}, st._satdump_process)
    assert res.success is False


def test_missing_satdump_is_failure_not_success(tmp_path):
    res = st._satdump_result({"satellite": "noaa19_apt",
                              "frequency_hz": 137.1e6,
                              "output_dir": str(tmp_path)}, st._satdump_live)
    # 本机 find_satdump 若真装了则跳过此断言环境差异
    if si.find_satdump():
        import pytest
        pytest.skip("本机装有 SatDump")
    assert res.success is False
